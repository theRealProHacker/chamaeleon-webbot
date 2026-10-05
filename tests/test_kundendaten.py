"""Tests for Kunden-Modus (kundendaten.py + kunden_modus prompt block).

Pure logic is tested directly; TourOne calls are monkeypatched — no live
requests here. ``app`` wird nur dort importiert, wo /kunde/auth selbst geprüft
wird, und dann INNERHALB der Testfunktion (Muster wie test_general.py): der
Import zieht die halbe Anwendung nach sich und geht die übrigen Tests nichts an.
"""

import threading
import time

import gevent
import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import kundendaten as kd
import travel_index

# --- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hop2_folgt_kd_patch(monkeypatch):
    """Hop 2 (``kd._buchung_roh``) ruft ``travel_index._tourone_get`` spät
    gebunden auf, Hop 1 den in kd importierten Namen. Die Tests hier patchen
    ``kd._tourone_get`` für BEIDE Hops — also leitet travel_index zur Laufzeit
    dorthin weiter, und jeder Patch auf kd greift auch für Hop 2."""
    monkeypatch.setattr(
        travel_index, "_tourone_get", lambda *a, **k: kd._tourone_get(*a, **k)
    )

ZUKUNFT_VON = "2099-01-01 00:00:00"
ZUKUNFT_BIS = "2099-01-15 00:00:00"
VERGANGEN_VON = "2020-01-01 00:00:00"
VERGANGEN_BIS = "2020-01-15 00:00:00"

FLUG = {
    "id": 4711,
    "pnrFileKey": "GEHEIMPNR",
    "vonCo3Code": "FRA",
    "nachCo3Code": "WDH",
    "flugnr": "4Y123",
    "airline": "4Y",
    "status": "OK",
    "abflug": "2099-01-01 10:20:00",
    "ankunft": "2099-01-01 18:30:00",
    "rang": 1,
    "sitzplatz": "12A",
}


def eingebettete_buchung(vorgang="126001", von=ZUKUNFT_VON, bis=ZUKUNFT_BIS):
    return {"vorgang": vorgang, "vonDat": von, "bisDat": bis, "reiseCode": "NAWDH"}


def adresse_mit(buchungen):
    return {"kundennummer": 999999999, "buchungen": buchungen}


def volle_buchung(status="OK", flugdaten=None, titel="Namibia-Reise", vorgang="126001"):
    """Ein /get/buchung-Objekt mit Zahlstand + bewusst auszuschließenden Feldern."""
    return {
        "vorgang": vorgang,
        "status": status,
        "beschreibungen": [{"titel": titel}],
        "persAdult": 2,
        "persChild": 0,
        "persBaby": 0,
        "personen": 2,
        "preis": 8198.0,
        "anzahlungBetrag": 1640.0,
        "anzahlungDat": "2026-03-15 00:00:00",
        "restBetrag": 6558.0,
        "schlussZahlungDat": "2026-07-01 00:00:00",
        "eingangBetrag": 1640.0,
        # muss draußen bleiben (Whitelist):
        "provision": 4242.0,
        "adrNotfallKontakt": "NOTFALLPERSON",
        "flugdaten": [FLUG] if flugdaten is None else flugdaten,
    }


def fake_tourone(monkeypatch, handlers):
    """Patch kd._tourone_get; ``handlers`` maps path → result | Exception |
    callable(params). Records every call for structural assertions."""
    calls = []

    def fake(path, params, timeout=20):
        calls.append({"path": path, "params": dict(params), "timeout": timeout})
        result = handlers[path]
        if callable(result) and not isinstance(result, Exception):
            result = result(params)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(kd, "_tourone_get", fake)
    # Ein neuer Fake heißt: ab hier gilt eine andere TourOne-Antwort. Der
    # Buchungs-Cache (10 min, Schlüssel nur die Kundennummer) überlebte sonst
    # den vorigen Test und der Fake käme gar nicht zum Zug. Die Cache-Tests
    # weiter unten leeren zusätzlich selbst, wo sie es genau festnageln.
    kd._buchungen_roh.cache_clear()
    return calls


# --- parse_kunden_id ----------------------------------------------------------


def test_parse_akzeptiert_string_und_int():
    assert kd.parse_kunden_id("999999999") == "999999999"
    assert kd.parse_kunden_id("  42abc_X-1  ") == "42abc_X-1"
    assert kd.parse_kunden_id(999999999) == "999999999"


def test_parse_verwirft_andere_typen():
    # bool ist int-Subklasse: JSON true darf nicht zu "True" werden.
    assert kd.parse_kunden_id(True) == ""
    assert kd.parse_kunden_id(False) == ""
    assert kd.parse_kunden_id(None) == ""
    assert kd.parse_kunden_id(1.5) == ""
    assert kd.parse_kunden_id(["1"]) == ""
    assert kd.parse_kunden_id({"id": "1"}) == ""


def test_parse_allowlist():
    assert kd.parse_kunden_id("abc/def") == ""
    assert kd.parse_kunden_id("1?x=2") == ""
    assert kd.parse_kunden_id("a" * 33) == ""
    assert kd.parse_kunden_id("a" * 32) == "a" * 32
    assert kd.parse_kunden_id("") == ""
    assert kd.parse_kunden_id("   ") == ""


# --- fetch_buchungen_text: Fehl-/Leerfälle ------------------------------------


def test_unbekannte_id_eigener_text(monkeypatch):
    # Kontrakt: unbekannte ID → [] mit HTTP 200, nie Fehlerstatus.
    fake_tourone(monkeypatch, {"/get/adresse": []})
    assert kd.fetch_buchungen_text("000000001") == kd.UNBEKANNT_TEXT


def test_api_fehler_hop1(monkeypatch):
    fake_tourone(monkeypatch, {"/get/adresse": RuntimeError("boom")})
    assert kd.fetch_buchungen_text("999999999") == kd.FEHLER_TEXT


def test_keine_buchungen(monkeypatch):
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([])})
    assert kd.fetch_buchungen_text("999999999") == kd.KEINE_BUCHUNGEN_TEXT


def test_leere_auswahl_hat_eigenen_text(monkeypatch):
    # Nur vergangene vorhanden, aber "kommende" gewünscht → keine Auswahl.
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit(
            [eingebettete_buchung(von=VERGANGEN_VON, bis=VERGANGEN_BIS)]
        )},
    )
    text = kd.fetch_buchungen_text("999999999", auswahl="kommende")
    assert "keine Buchung" in text


# --- grobe Liste (details=false) ---------------------------------------------


def test_overview_holt_den_status_je_buchung(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung()]),
         "/get/buchung": {"status": "OK"}},
    )
    text = kd.fetch_buchungen_text("999999999", details=False)
    assert "Buchungsnummer 126001" in text
    # Zukunfts-Datum → Marker; die erste künftige Reise heißt „nächste Reise",
    # weitere künftige bleiben „kommend".
    assert "nächste Reise" in text
    # Hop 1 kennt keinen Status — ein Hop 2 je Buchung, nicht mehr.
    assert [c["path"] for c in calls] == ["/get/adresse", "/get/buchung"]


def _storno_vor_lebender(monkeypatch):
    """Gesehen 2026-09-30: die näheste kommende Buchung ist storniert, die
    lebende liegt dahinter — Leon nannte die stornierte „nächste Reise“."""
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([
            eingebettete_buchung("STORNO", von="2099-03-16 00:00:00", bis="2099-03-31 00:00:00"),
            eingebettete_buchung("LEBT", von="2099-10-12 00:00:00", bis="2099-10-22 00:00:00"),
        ]),
         "/get/buchung": lambda p: {
             "vorgang": p["vorgangsNummer"],
             "status": "XX" if p["vorgangsNummer"] == "STORNO" else "OP",
         }},
    )


def test_stornierte_buchung_ist_in_der_liste_nicht_die_naechste(monkeypatch):
    _storno_vor_lebender(monkeypatch)
    text = kd.fetch_buchungen_text("999999999", auswahl="kommende")
    storno = next(z for z in text.splitlines() if "STORNO" in z)
    lebt = next(z for z in text.splitlines() if "LEBT" in z)
    assert "storniert" in storno and "nächste Reise" not in storno
    assert "nächste Reise" in lebt


def test_anzahl_ueberspringt_stornierte(monkeypatch):
    _storno_vor_lebender(monkeypatch)
    for details in (False, True):
        text = kd.fetch_buchungen_text("999999999", "kommende", 1, details)
        assert "LEBT" in text and "STORNO" not in text


def test_gescheiterter_status_gilt_nicht_als_storniert(monkeypatch):
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung()]),
         "/get/buchung": RuntimeError("boom")},
    )
    text = kd.fetch_buchungen_text("999999999", details=False)
    assert "nächste Reise" in text and "storniert" not in text


def _titel_map(monkeypatch, mapping):
    """Reise-Index vortäuschen, ohne den (minutenlangen) Build anzustoßen."""
    monkeypatch.setattr(kd, "get_titel_for_code", lambda code: mapping.get(code, ""))


def test_overview_zeigt_echten_titel_statt_reisecode(monkeypatch):
    """Hop 1 liefert leeres beschreibungen — der Code allein ist für den Kunden
    unlesbar (`COSAN_NEU`). Der Reise-Index löst ihn zum Katalogtitel auf."""
    _titel_map(monkeypatch, {"NAWDH": "Wüstenhauch"})
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung()])})
    text = kd.fetch_buchungen_text("999999999", details=False)
    assert '„Wüstenhauch"' in text
    assert "NAWDH" not in text


def test_overview_faellt_auf_den_code_zurueck_wenn_der_index_ihn_nicht_kennt(monkeypatch):
    """Ein Miss bleibt ein Miss. Suffix-Codes teilen meist den Basistitel, aber
    nicht immer (NAFAM_DRR vs. NAFAM sind verschiedene Reisen) — ein selbstsicher
    falscher Reisename in einer Buchung ist schlimmer als ein roher Code."""
    _titel_map(monkeypatch, {})
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung()])})
    text = kd.fetch_buchungen_text("999999999", details=False)
    assert '„NAWDH"' in text


def test_overview_titel_kostet_keinen_zusaetzlichen_request(monkeypatch):
    """Der Lookup ist ein In-Memory-Peek; die grobe Liste bleibt bei einem Hop."""
    _titel_map(monkeypatch, {"NAWDH": "Wüstenhauch"})
    calls = fake_tourone(
        monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung()])}
    )
    kd.fetch_buchungen_text("999999999", details=False)
    assert [c["path"] for c in calls].count("/get/adresse") == 1
    assert len(calls) == 2  # Hop 1 + der Status-Hop-2, kein Titel-Request


def test_detail_zieht_hop2_titel_dem_index_vor(monkeypatch):
    """Hop 2 ist autoritativ: dort steht der Titel der Buchung selbst, während der
    Index den Katalogstand zeigt. Bei Abweichung gewinnt die Buchung."""
    _titel_map(monkeypatch, {"NAWDH": "Katalog-Titel"})
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(titel="Titel der Buchung"),
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "Titel der Buchung" in text
    assert "Katalog-Titel" not in text


def test_detail_nutzt_den_index_wenn_hop2_keinen_titel_hat(monkeypatch):
    """Notnagel-Kette: beschreibungen leer → Index → Code."""
    _titel_map(monkeypatch, {"NAWDH": "Katalog-Titel"})
    ohne_titel = volle_buchung()
    ohne_titel["beschreibungen"] = []
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": ohne_titel,
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "Katalog-Titel" in text
    assert "NAWDH" not in text


def test_auswahl_trennt_kommende_und_vergangene(monkeypatch):
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([
            eingebettete_buchung("P", von=VERGANGEN_VON, bis=VERGANGEN_BIS),
            eingebettete_buchung("F", von=ZUKUNFT_VON, bis=ZUKUNFT_BIS),
        ])},
    )
    komm = kd.fetch_buchungen_text("999999999", auswahl="kommende")
    assert "Buchungsnummer F" in komm and "Buchungsnummer P" not in komm
    verg = kd.fetch_buchungen_text("999999999", auswahl="vergangene")
    assert "Buchungsnummer P" in verg and "Buchungsnummer F" not in verg


def test_alle_stellt_die_naechste_reise_voran_nicht_die_entfernteste(monkeypatch):
    """Der 2026-07-30-Fehler: „alle" sortierte nach vonDat ABSTEIGEND, also stand
    bei zwei künftigen Reisen die weiter entfernte oben. Weil „alle" der Standard
    ist, lieferte anzahl=1 damit die letzte statt der nächsten Reise — der Bot
    nannte San Agustín (17.08.) statt Gobi (08.08.) als nächste."""
    gobi = eingebettete_buchung("GOBI", von="2099-08-08 00:00:00", bis="2099-08-22 00:00:00")
    kolumbien = eingebettete_buchung("KOL", von="2099-08-17 00:00:00", bis="2099-09-01 00:00:00")
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([kolumbien, gobi])})
    text = kd.fetch_buchungen_text("999999999", auswahl="alle", anzahl=1)
    assert "Buchungsnummer GOBI" in text
    assert "Buchungsnummer KOL" not in text


def test_alle_listet_kommende_vor_vergangenen(monkeypatch):
    """Reihenfolge insgesamt: kommende näheste voran, dann vergangene neueste voran."""
    buchungen = [
        eingebettete_buchung("ALT", von=VERGANGEN_VON, bis=VERGANGEN_BIS),
        eingebettete_buchung("SPAET", von="2099-08-17 00:00:00", bis="2099-09-01 00:00:00"),
        eingebettete_buchung("NEU_ALT", von="2021-01-01 00:00:00", bis="2021-01-15 00:00:00"),
        eingebettete_buchung("FRUEH", von="2099-08-08 00:00:00", bis="2099-08-22 00:00:00"),
    ]
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit(buchungen)})
    text = kd.fetch_buchungen_text("999999999", auswahl="alle")
    positionen = [text.index(f"Buchungsnummer {v}") for v in ("FRUEH", "SPAET", "NEU_ALT", "ALT")]
    assert positionen == sorted(positionen)


def test_naechste_reise_ist_explizit_markiert(monkeypatch):
    """Die Sortierung allein hat das Modell schon falsch gelesen, also steht es
    jetzt als Wort in der Zeile."""
    gobi = eingebettete_buchung("GOBI", von="2099-08-08 00:00:00", bis="2099-08-22 00:00:00")
    kolumbien = eingebettete_buchung("KOL", von="2099-08-17 00:00:00", bis="2099-09-01 00:00:00")
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([kolumbien, gobi])})
    text = kd.fetch_buchungen_text("999999999", auswahl="alle")
    gobi_zeile = next(z for z in text.splitlines() if "GOBI" in z)
    kol_zeile = next(z for z in text.splitlines() if "KOL" in z)
    assert "nächste Reise" in gobi_zeile
    assert "nächste Reise" not in kol_zeile


def test_laufende_reise_ist_nicht_die_naechste(monkeypatch):
    """Eine laufende Reise sortiert wegen ihres vonDat vorne, ist aber nicht die
    nächste — sonst hätte der Bot „nächste Reise" für etwas Begonnenes gesagt."""
    laeuft = {
        "vorgang": "LAUFT",
        "vonDat": VERGANGEN_VON,
        "bisDat": "2099-01-15 00:00:00",
        "reiseCode": "NAWDH",
    }
    kommend = eingebettete_buchung("KOMMT", von="2099-08-08 00:00:00", bis="2099-08-22 00:00:00")
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([laeuft, kommend])})
    text = kd.fetch_buchungen_text("999999999", auswahl="alle")
    lauft_zeile = next(z for z in text.splitlines() if "LAUFT" in z)
    kommt_zeile = next(z for z in text.splitlines() if "KOMMT" in z)
    assert "läuft gerade" in lauft_zeile and "nächste Reise" not in lauft_zeile
    assert "nächste Reise" in kommt_zeile


def test_ohne_kommende_reise_keine_markierung(monkeypatch):
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit(
            [eingebettete_buchung("P", von=VERGANGEN_VON, bis=VERGANGEN_BIS)]
        )},
    )
    text = kd.fetch_buchungen_text("999999999", auswahl="alle")
    assert "nächste Reise" not in text


def test_anzahl_nimmt_die_neuesten_vergangenen(monkeypatch):
    buchungen = [
        eingebettete_buchung(
            str(i), von=f"202{i}-01-01 00:00:00", bis=f"202{i}-01-15 00:00:00"
        )
        for i in range(4)  # 2020..2023, alle vergangen
    ]
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit(buchungen)})
    text = kd.fetch_buchungen_text("999999999", auswahl="vergangene", anzahl=2)
    # neueste zuerst → 2023 (vorgang "3") und 2022 ("2")
    assert "Buchungsnummer 3" in text and "Buchungsnummer 2" in text
    assert "Buchungsnummer 1" not in text and "Buchungsnummer 0" not in text


# --- Detailansicht (details=true) --------------------------------------------


def test_detail_zeigt_zahlstand_und_haelt_whitelist(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(),
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    # Gewollt drin:
    assert "Namibia-Reise" in text
    assert "8.198,00 €" in text  # Gesamtpreis (deutsche Notation)
    assert "6.558,00 €" in text  # offener Betrag
    assert "fällig 01.07.2026" in text
    assert "2 Erwachsene" in text
    assert "126001" in text  # Buchungsnummer
    assert "4Y123" in text and "FRA" in text and "WDH" in text
    # Whitelist: PII / PNR / Provision / interne dürfen NIE erscheinen.
    for verboten in ("GEHEIMPNR", "12A", "4.242", "NOTFALLPERSON", "999999999", "Provision"):
        assert verboten not in text
    # Strukturell: nur GET-Pfade, überall das enge Chat-Timeout.
    assert all(c["path"].startswith("/get/") for c in calls)
    assert all(c["timeout"] == kd.TIMEOUT for c in calls)


UNTERLAGEN = [
    {"id": "11", "name": "Rechnung.pdf", "beschreibung": "", "link": "https://unterlagen.chamaeleon-reisen.de/r.pdf"},
    {"id": "12", "name": "Teilnehmerdaten.pdf", "beschreibung": "", "link": "https://unterlagen.chamaeleon-reisen.de/tn.pdf"},
    {"id": "13", "name": "Reiseunterlagen.pdf", "beschreibung": "", "link": "https://unterlagen.chamaeleon-reisen.de/ulas.pdf"},
]
TRIPURL = "https://travel-details.eu/de?tid=TEST-TEST-TEST"


def test_detail_zeigt_dokumente_und_tripurl(monkeypatch):
    buchung = volle_buchung()
    buchung.update(unterlagen=UNTERLAGEN, tripurl=TRIPURL, bisDat=ZUKUNFT_BIS)
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung()]), "/get/buchung": buchung},
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "[Reiseunterlagen](https://unterlagen.chamaeleon-reisen.de/ulas.pdf)" in text
    assert "[Rechnung](https://unterlagen.chamaeleon-reisen.de/r.pdf)" in text
    assert TRIPURL in text
    # Teilnehmerdaten: genannt, aber nie verlinkt (Passnummern im Chat-Log).
    assert "Teilnehmerdaten" in text and "tn.pdf" not in text
    assert "noch nicht bereitgestellt" not in text


def test_detail_storniert_ohne_dokumente(monkeypatch):
    buchung = volle_buchung(status="XX")
    buchung.update(unterlagen=UNTERLAGEN, tripurl=TRIPURL)
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung()]), "/get/buchung": buchung},
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "unterlagen.chamaeleon-reisen.de" not in text and TRIPURL not in text


def test_detail_vergangen_dokumente_ja_tripurl_nein(monkeypatch):
    buchung = volle_buchung()
    buchung.update(unterlagen=UNTERLAGEN, tripurl=TRIPURL, vonDat=VERGANGEN_VON, bisDat=VERGANGEN_BIS)
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung(von=VERGANGEN_VON, bis=VERGANGEN_BIS)]),
            "/get/buchung": buchung,
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "r.pdf" in text and TRIPURL not in text


def test_detail_ohne_schlussunterlagen_nennt_den_stand(monkeypatch):
    buchung = volle_buchung()
    buchung.update(unterlagen=UNTERLAGEN[:1], bisDat=ZUKUNFT_BIS, vonDat=ZUKUNFT_VON)
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung()]), "/get/buchung": buchung},
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "noch nicht bereitgestellt" in text and "01.01.2099" in text


def test_detail_stornierte_buchung_ohne_zahlstand(monkeypatch):
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(status="XX"),
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "storniert" in text
    assert "8.198,00 €" not in text  # kein Zahlstand bei storniert


@pytest.mark.parametrize(
    "status,klartext",
    [("AN", "angefragt"), ("OP", "Option"), ("RQ", "auf Anfrage")],
)
def test_unbestaetigte_buchung_ist_nicht_storniert(monkeypatch, status, klartext):
    """AN/OP/RQ sind lebende Buchungen — gemessen 143 von 1200 (2026-09-09).

    Der frühere ``status != "OK"``-Test meldete sie als „storniert" und
    unterschlug Zahlstand und Flüge. Gerade frisch gebuchte Reisen stehen so.
    """
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(status=status),
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "storniert" not in text
    assert klartext in text
    assert "8.198,00 €" in text  # Zahlstand bleibt


def test_unbekannter_status_gilt_als_gebucht(monkeypatch):
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(status="ZZ"),
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "- Status: gebucht" in text
    assert "storniert" not in text


def test_detail_ohne_flugdaten(monkeypatch):
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(flugdaten=[]),
        },
    )
    text = kd.fetch_buchungen_text("999999999", details=True)
    assert "noch nicht eingebucht" in text
    assert "8.198,00 €" in text  # Zahlstand trotzdem vorhanden


def test_detail_holt_jede_buchung_ohne_deckel(monkeypatch):
    """Owner-Entscheidung 2026-07-30: der Bot muss ALLE Buchungen voll einsehen.

    Vorher deckelte MAX_DETAIL=5, und weil anzahl nur von vorne schneidet und es
    keinen Offset gibt, waren ältere Buchungen im Detail unerreichbar — nicht nur
    pro Aufruf, sondern grundsätzlich.
    """
    calls = fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit(
                [eingebettete_buchung(str(i)) for i in range(10)]
            ),
            "/get/buchung": lambda params: volle_buchung(
                vorgang=params["vorgangsNummer"]
            ),
        },
    )
    text = kd.fetch_buchungen_text("999999999", auswahl="alle", details=True)
    assert len(calls) == 1 + 10  # Hop 1 + je Buchung ein Hop 2
    for i in range(10):
        assert f"Buchungsnummer: {i}" in text
    assert "weitere" not in text


def test_detail_behaelt_die_reihenfolge_trotz_nebenlaeufigkeit(monkeypatch):
    """pool.map liefert in Eingangsreihenfolge — die Sortierung darf nicht davon
    abhängen, welcher Request zuerst zurückkommt."""
    buchungen = [
        eingebettete_buchung("FRUEH", von="2099-08-08 00:00:00", bis="2099-08-22 00:00:00"),
        eingebettete_buchung("SPAET", von="2099-08-17 00:00:00", bis="2099-09-01 00:00:00"),
        eingebettete_buchung("ALT", von=VERGANGEN_VON, bis=VERGANGEN_BIS),
    ]
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit(buchungen),
            "/get/buchung": lambda params: volle_buchung(
                vorgang=params["vorgangsNummer"]
            ),
        },
    )
    text = kd.fetch_buchungen_text("999999999", auswahl="alle", details=True)
    positionen = [text.index(f"Buchungsnummer: {v}") for v in ("FRUEH", "SPAET", "ALT")]
    assert positionen == sorted(positionen)


def test_grobe_liste_ohne_deckel(monkeypatch):
    """Auch die Übersicht kürzt nicht mehr — OVERVIEW_CAP hätte einem Vielbucher
    seine ältesten Reisen unbehebbar verschwiegen."""
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit(
            [eingebettete_buchung(str(i)) for i in range(40)]
        )},
    )
    text = kd.fetch_buchungen_text("999999999", auswahl="alle", details=False)
    for i in range(40):
        assert f"Buchungsnummer {i}" in text
    assert "weitere" not in text


def test_detail_teilerfolg_zeigt_verfuegbare(monkeypatch):
    # An der Buchungsnummer festgemacht, nicht an einem Zähler: Hop 2 läuft
    # nebenläufig, ein "der erste Call" wäre nicht mehr deterministisch (und ein
    # geteilter Zähler ohne Lock wäre obendrein ein Datenrennen im Test selbst).
    def buchung_handler(params):
        if params["vorgangsNummer"] == "126001":
            raise RuntimeError("timeout")
        return volle_buchung()

    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit(
                [eingebettete_buchung("126001"), eingebettete_buchung("126002")]
            ),
            "/get/buchung": buchung_handler,
        },
    )
    text = kd.fetch_buchungen_text("999999999", auswahl="alle", details=True)
    assert "8.198,00 €" in text  # die zweite Buchung liefert → Detail gewinnt
    # Die Lücke muss benannt werden, sonst liest sich die Liste als vollständig.
    assert "keine Details laden" in text


# --- make_buchungen_tool ------------------------------------------------------


def test_tool_hat_selektor_params_aber_keine_id(monkeypatch):
    gesehen = []
    fake_tourone(
        monkeypatch,
        {"/get/adresse": lambda params: gesehen.append(params) or []},
    )
    tool = kd.make_buchungen_tool("999999999")
    # Selektor-Parameter ja, kunden_id nein — das Modell wählt nie WESSEN Daten.
    assert set(tool.args) == {"auswahl", "anzahl", "details"}
    result = tool.invoke({})
    assert result == kd.UNBEKANNT_TEXT
    assert gesehen[0]["kundennummer"] == "999999999"


# --- filter_new_tool_calls ----------------------------------------------------


def test_dedup_tool_calls():
    seen = set()
    erste = kd.filter_new_tool_calls([{"id": "a", "name": "x"}], seen)
    assert [tc["id"] for tc in erste] == ["a"]
    # stream_mode="values" liefert historische Calls erneut — gefiltert.
    zweite = kd.filter_new_tool_calls(
        [{"id": "a", "name": "x"}, {"id": "b", "name": "y"}], seen
    )
    assert [tc["id"] for tc in zweite] == ["b"]


def test_dedup_ohne_id_passiert_durch():
    seen = set()
    assert len(kd.filter_new_tool_calls([{"name": "x"}, {"name": "x"}], seen)) == 2
    assert seen == set()


# --- Buchungs-Cache (_buchungen_roh) -----------------------------------------
#
# Hintergrund: der Prompt-Bau löst seit Änderung E die gemeinte Reise selbst auf
# und liefe sonst je Kunden-Nachricht in einen TourOne-Abruf. Gemessen
# 2026-09-20 (8 Abrufe, Testkunde 999999999): Median 286 ms, kalt 768 ms,
# Ausreißer 1.268 ms.


def test_cache_spart_den_zweiten_abruf(monkeypatch):
    calls = fake_tourone(
        monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung()])}
    )
    erste = kd._buchungen_roh("999999999")
    zweite = kd._buchungen_roh("999999999")
    assert len(calls) == 1
    assert erste == zweite


def test_cache_trennt_die_kunden(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {"/get/adresse": lambda params: adresse_mit(
            [eingebettete_buchung(params["kundennummer"])]
        )},
    )
    assert kd._buchungen_roh("111111111")[0]["vorgang"] == "111111111"
    assert kd._buchungen_roh("222222222")[0]["vorgang"] == "222222222"
    assert len(calls) == 2


def test_ausfall_wird_nicht_gecacht(monkeypatch):
    """Sonst hielte ein einzelner Timeout den Kunden 10 Minuten in der
    Störungsmeldung: ttl_cache merkt sich Rückgabewerte, auch None, aber keine
    Exceptions — deshalb wirft die gecachte Funktion."""
    zustand = {"kaputt": True}

    def wechselhaft(params):
        if zustand["kaputt"]:
            raise RuntimeError("timeout")
        return adresse_mit([eingebettete_buchung()])

    calls = fake_tourone(monkeypatch, {"/get/adresse": wechselhaft})
    with pytest.raises(RuntimeError):
        kd._buchungen_roh("999999999")
    zustand["kaputt"] = False
    assert kd._buchungen_roh("999999999")  # zweiter Anlauf, nicht der Cache
    assert len(calls) == 2


def test_vorgangsnummern_meldet_den_ausfall_und_erholt_sich(monkeypatch):
    """Derselbe Fall eine Ebene höher: None (Störung) darf nicht kleben."""
    zustand = {"kaputt": True}

    def wechselhaft(params):
        if zustand["kaputt"]:
            raise RuntimeError("timeout")
        return adresse_mit([eingebettete_buchung("126001")])

    fake_tourone(monkeypatch, {"/get/adresse": wechselhaft})
    assert kd.vorgangsnummern("999999999") is None
    zustand["kaputt"] = False
    assert kd.vorgangsnummern("999999999") == ["126001"]


def test_unbekannte_id_wird_gecacht_und_bleibt_unterscheidbar(monkeypatch):
    """None heißt „Kundennummer unbekannt", [] heißt „bekannt, ohne Buchung".

    Der Unterschied entscheidet, was der Kunde liest (UNBEKANNT_TEXT vs.
    KEINE_BUCHUNGEN_TEXT) — er darf beim Umzug in den Cache nicht verloren
    gehen. Ein stabiles „kenne ich nicht" ist kein Ausfall und wird gecacht.
    """
    calls = fake_tourone(monkeypatch, {"/get/adresse": []})
    assert kd._buchungen_roh("999999999") is None
    assert kd._buchungen_roh("999999999") is None
    assert len(calls) == 1
    assert kd.fetch_buchungen_text("999999999") == kd.UNBEKANNT_TEXT

    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([])})
    assert kd._buchungen_roh("999999999") == []
    assert kd.fetch_buchungen_text("999999999") == kd.KEINE_BUCHUNGEN_TEXT


def test_cache_laeuft_nach_der_ttl_ab(monkeypatch):
    """Nach 10 Minuten wird neu geholt — sonst sähe ein Kunde nach seiner
    Zahlung noch den alten Zahlstand (deshalb 10 min und nicht 2 h).

    Die Zeit wird vorgespult (``TTLCache.expire``), nicht verschlafen.
    """
    calls = fake_tourone(
        monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung()])}
    )
    cache = kd._buchungen_roh.cache
    assert (cache.ttl, cache.maxsize) == (600, 512)
    kd._buchungen_roh("999999999")
    kd._buchungen_roh("999999999")
    assert len(calls) == 1
    cache.expire(cache.timer() + cache.ttl + 1)  # zehn Minuten später
    kd._buchungen_roh("999999999")
    assert len(calls) == 2


def test_cache_haelt_nur_die_buchungen(monkeypatch):
    """Review D19: Name, Anschrift und Kontaktdaten liegen nie im Cache."""
    fake_tourone(
        monkeypatch,
        {"/get/adresse": {
            "kundennummer": 999999999,
            "name": "Testperson",
            "strasse": "Teststraße 1",
            "email": "test@example.org",
            "buchungen": [eingebettete_buchung()],
        }},
    )
    kd._buchungen_roh("999999999")
    gecacht = repr(list(kd._buchungen_roh.cache.values()))
    for feld in ("Testperson", "Teststraße", "example.org"):
        assert feld not in gecacht


# --- Hop-2-Cache (_buchung_roh) -----------------------------------------------
#
# Vier Aufrufer holen dieselbe Buchung: Detail-Block, Status (Reise-Links),
# Reisecode für reiseinfo_tool und der Agenturpfad. Einer zahlt, die anderen
# lesen den Cache.


def test_hop2_ein_abruf_je_vorgang(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": {**volle_buchung(), "reiseCode": "NAWDH"},
        },
    )
    assert "8.198,00 €" in kd.fetch_buchungen_text("999999999", details=True)
    assert kd.buchungsstatus("126001") == "OK"
    assert agent_base._reise_code_for_vorgang("126001") == "NAWDH"
    hop2 = [c for c in calls if c["path"] == "/get/buchung"]
    assert len(hop2) == 1
    assert hop2[0]["timeout"] == kd.TIMEOUT


def test_hop2_ausfall_wird_nicht_gecacht(monkeypatch):
    """Wie bei Hop 1: ein einzelner Timeout darf die Buchung nicht 10 Minuten
    unsichtbar machen."""
    zustand = {"kaputt": True}

    def wechselhaft(params):
        if zustand["kaputt"]:
            raise RuntimeError("timeout")
        return volle_buchung()

    calls = fake_tourone(monkeypatch, {"/get/buchung": wechselhaft})
    with pytest.raises(RuntimeError):
        kd._buchung_roh("126001")
    assert kd._hop2_alle([eingebettete_buchung()]) == [None]  # Aufrufer fängt
    zustand["kaputt"] = False
    assert kd._hop2_alle([eingebettete_buchung()]) == [volle_buchung()]
    assert kd.buchungsstatus("126001") == "OK"  # aus dem Cache, kein vierter Abruf
    assert len(calls) == 3


# --- naechste_offene_reise (Review D10) --------------------------------------

HEUTE = "2026-09-23"


def _buchung(vorgang, von, bis, code="NAWDH"):
    return {"vorgang": vorgang, "vonDat": von, "bisDat": bis, "reiseCode": code}


def test_nur_vergangene_reisen_ergeben_keine(monkeypatch):
    """Der Grund für die eigene Funktion: reiseinfo_vorgang gäbe hier die
    Vorjahresreise zurück, und ein Link auf „deine Reise" zeigte darauf."""
    _titel_map(monkeypatch, {})
    buchungen = [_buchung("ALT", VERGANGEN_VON, VERGANGEN_BIS)]
    assert kd.naechste_offene_reise(buchungen, HEUTE) == ("", "")


def test_eine_kommende_reise_mit_ziel_und_datum(monkeypatch):
    _titel_map(monkeypatch, {"NAWDH": "Wüstenhauch"})
    buchungen = [
        _buchung("ALT", VERGANGEN_VON, VERGANGEN_BIS),
        _buchung("126001", "2027-03-04 00:00:00", "2027-03-18 00:00:00"),
    ]
    vorgang, label = kd.naechste_offene_reise(buchungen, HEUTE)
    assert vorgang == "126001"
    assert "Wüstenhauch" in label and "04.03.2027" in label


def test_zwei_kommende_reisen_die_naeheste_gewinnt(monkeypatch):
    _titel_map(monkeypatch, {})
    buchungen = [
        _buchung("SPAET", "2027-11-01 00:00:00", "2027-11-15 00:00:00"),
        _buchung("FRUEH", "2027-03-04 00:00:00", "2027-03-18 00:00:00"),
    ]
    vorgang, label = kd.naechste_offene_reise(buchungen, HEUTE)
    assert vorgang == "FRUEH"
    assert "04.03.2027" in label


def test_laufende_reise_zaehlt_als_offen(monkeypatch):
    """Wer gerade unterwegs ist, meint diese Reise — nicht die übernächste."""
    _titel_map(monkeypatch, {})
    buchungen = [
        _buchung("LAEUFT", "2026-09-20 00:00:00", "2026-10-04 00:00:00"),
        _buchung("SPAETER", "2027-03-04 00:00:00", "2027-03-18 00:00:00"),
    ]
    assert kd.naechste_offene_reise(buchungen, HEUTE)[0] == "LAEUFT"


def test_label_nimmt_den_titel_aus_der_buchung(monkeypatch):
    _titel_map(monkeypatch, {})
    buchungen = [
        {
            "vorgang": "126001",
            "vonDat": "2027-03-04 00:00:00",
            "bisDat": "2027-03-18 00:00:00",
            "reiseCode": "NAWDH",
            "beschreibungen": [{"titel": "Sossusvlei"}],
        }
    ]
    assert kd.naechste_offene_reise(buchungen, HEUTE)[1] == "Sossusvlei, 04.03.2027"


def test_ohne_buchungen_keine_reise():
    assert kd.naechste_offene_reise([], HEUTE) == ("", "")


# --- Auflösung im Aufrufer (agent.reise_fuer_links, Review D3/D4/D11) --------

UEBERSICHT_URL = "https://www.chamaeleon-reisen.de/MeinChamaeleon"
URL_NUMMER = "9988776655"
REISE_URL = (
    f"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG={URL_NUMMER}"
)


def test_url_nummer_haengt_nicht_an_der_api(monkeypatch):
    """Pflicht-Regression (D3): trägt die Seite eine Nummer, ist die Frage
    beantwortet — die Nummer kommt aus der URL, nie aus der API. Nur das Label
    kommt aus der gecachten groben Liste (ohne es überliest Leon die
    Dokumentliste, 05.10.2026); eine fremde Nummer bekommt keins."""
    import agent

    fake_tourone(
        monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung("126001")])}
    )
    assert agent.reise_fuer_links(REISE_URL, "999999999") == (URL_NUMMER, "")


def test_url_nummer_ueberlebt_den_tourone_ausfall(monkeypatch):
    """Pflicht-Regression (D3): die vier Links, die es heute gibt, müssen den
    Ausfall überstehen — sie hingen nie an der API."""
    import agent

    fake_tourone(monkeypatch, {"/get/adresse": RuntimeError("boom")})
    vorgang, label = agent.reise_fuer_links(REISE_URL, "999999999")
    assert vorgang == URL_NUMMER
    block = agent_base._trip_links_block(vorgang, label)
    for anker in ("#reisedaten", "#gaeste", "#unterlagen"):
        assert f"?VRRVORGANG={URL_NUMMER}{anker})" in block


def test_ohne_url_nummer_gewinnt_die_naechste_offene_reise(monkeypatch):
    import agent

    _titel_map(monkeypatch, {})
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([
            _buchung("ALT", VERGANGEN_VON, VERGANGEN_BIS),
            _buchung("126001", ZUKUNFT_VON, ZUKUNFT_BIS),
        ]),
         "/get/buchung": {"status": "OK"}},
    )
    vorgang, label = agent.reise_fuer_links(UEBERSICHT_URL, "999999999")
    assert vorgang == "126001"
    assert "01.01.2099" in label  # Ziel und Datum stehen im Prompt-Kopf
    assert label in agent_base._trip_links_block(vorgang, label)


def test_ohne_offene_reise_nur_uebersichts_links(monkeypatch):
    import agent

    _titel_map(monkeypatch, {})
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([_buchung("ALT", VERGANGEN_VON, VERGANGEN_BIS)])},
    )
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999") == ("", "")
    assert agent_base._trip_links_block("", "") == ""


def test_unsichere_buchungsnummer_ergibt_keine_links():
    """Die Nummer landet in einem Markdown-Link im Prompt. Alles ausser
    ``[A-Za-z0-9_-]{1,64}`` zaehlt als "keine Nummer" (_VRRVORGANG_SAFE)."""
    assert agent_base._trip_links_block("AB)](javascript:x)", "Ziel") == ""
    assert agent_base._trip_links_block("A" * 65, "") == ""


def test_ausfall_ohne_url_nummer_laesst_den_chat_laufen(monkeypatch):
    import agent

    fake_tourone(monkeypatch, {"/get/adresse": RuntimeError("boom")})
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999") == ("", "")


def test_ohne_kunden_id_kein_abruf(monkeypatch):
    """Öffentlicher Chat: keine Bindung, keine Buchungen, kein Request."""
    import agent

    calls = fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([])})
    assert agent.reise_fuer_links(UEBERSICHT_URL, "") == ("", "")
    assert calls == []


def test_vorwaermen_und_prompt_pfad_teilen_den_cache_eintrag(monkeypatch):
    """Schlüssel-Gleichheit (D11): /kunde/auth wärmt, der Prompt-Bau liest —
    zusammen genau EIN Abruf. Mit einem Timeout-Argument im Schlüssel träfe das
    Vorwärmen einen anderen Eintrag und wäre wirkungslos."""
    import agent

    _titel_map(monkeypatch, {})
    calls = fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([_buchung("126001", ZUKUNFT_VON, ZUKUNFT_BIS)]),
         "/get/buchung": {"status": "OK"}},
    )
    kd._buchungen_roh("999999999")  # das tut der Daemon-Thread in /kunde/auth
    vorgang, _ = agent.reise_fuer_links(UEBERSICHT_URL, "999999999")
    assert vorgang == "126001"
    assert [c["path"] for c in calls].count("/get/adresse") == 1


def test_stornierte_reise_ist_nie_die_naechste(monkeypatch):
    """Review 2026-09-24: Hop 1 kennt keinen Status. Wer A storniert und B
    gebucht hat, bekommt die Links zu B — nie zur toten Buchung A."""
    import agent

    _titel_map(monkeypatch, {})
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([
            _buchung("STORNO", ZUKUNFT_VON, ZUKUNFT_BIS),
            _buchung("LEBT", "2099-06-01", "2099-06-14"),
        ]),
         "/get/buchung": lambda p: {"status": "XX" if p["vorgangsNummer"] == "STORNO" else "OK"}},
    )
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999")[0] == "LEBT"


def test_scheiternde_statuspruefung_gibt_keine_reise_links(monkeypatch):
    """Lieber nur die Uebersichts-Links als ein Link auf eine womoeglich
    stornierte Buchung."""
    import agent

    _titel_map(monkeypatch, {})
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([_buchung("126001", ZUKUNFT_VON, ZUKUNFT_BIS)]),
         "/get/buchung": RuntimeError("boom")},
    )
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999") == ("", "")


def test_abruf_laeuft_nach_dem_timeout_zu_ende(monkeypatch):
    """Review 2026-09-24: der Chat wartet nicht, aber der Cache fuellt sich —
    die naechste Nachricht bekommt die Links ohne neuen Abruf."""
    import agent

    _titel_map(monkeypatch, {})

    def langsam(path, params, timeout=20):
        gevent.sleep(0.2)
        if path == "/get/buchung":
            return {"status": "OK"}
        return adresse_mit([_buchung("126001", ZUKUNFT_VON, ZUKUNFT_BIS)])

    kd._buchungen_roh.cache_clear()
    monkeypatch.setattr(kd, "_tourone_get", langsam)
    monkeypatch.setattr(agent, "REISE_TIMEOUT_S", 0.05)
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999") == ("", "")
    gevent.sleep(0.6)  # der Hintergrund-Abruf laeuft zu Ende
    monkeypatch.setattr(kd, "_tourone_get", lambda *a, **k: 1 / 0)
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999")[0] == "126001"


def test_haengendes_tourone_blockiert_den_chat_nicht(monkeypatch):
    """Review D11: der requests-timeout gilt je Socket-Schritt, nicht für die
    Gesamtdauer — deshalb die harte Schranke außen.

    Der Fake gibt über ``gevent.sleep`` ab, wie es ein hängender Socket unter
    ``gunicorn -k gevent`` (monkey-patched) auch tut.
    """
    import agent

    def haengt(path, params, timeout=20):
        gevent.sleep(5)
        return adresse_mit([eingebettete_buchung()])

    kd._buchungen_roh.cache_clear()
    monkeypatch.setattr(kd, "_tourone_get", haengt)
    # Kurze Schranke, damit der Test nicht an der Maschinenlast haengt: der
    # Fake schlaeft 5 s, die Marge bleibt also gross.
    monkeypatch.setattr(agent, "REISE_TIMEOUT_S", 0.05)
    start = time.monotonic()
    assert agent.reise_fuer_links(UEBERSICHT_URL, "999999999") == ("", "")
    assert time.monotonic() - start < 1.0


# --- Vorwärmen beim Login (app.py /kunde/auth) -------------------------------


def _auth_client(monkeypatch, resolve):
    """Ein Testclient, dessen /kunde/auth-Anmeldung immer gelingt."""
    import app as app_modul

    monkeypatch.setattr(
        app_modul.kunden_auth, "authenticate", lambda *args: (True, "sid")
    )
    monkeypatch.setattr(app_modul.kunden_auth, "resolve", resolve)
    return app_modul.app.test_client()


def test_auth_wartet_nicht_auf_das_vorwaermen(monkeypatch):
    """Die Route antwortet sofort; der Abruf läuft im Daemon-Thread weiter."""
    gestartet, freigeben = threading.Event(), threading.Event()

    def langsam(kunden_id):
        gestartet.set()
        freigeben.wait(5)
        return []

    monkeypatch.setattr(kd, "_buchungen_roh", langsam)
    client = _auth_client(monkeypatch, lambda sid: "999999999")
    start = time.monotonic()
    antwort = client.post("/kunde/auth", json={"session_id": "sid"})
    dauer = time.monotonic() - start
    try:
        assert antwort.get_json() == {"authenticated": True}
        assert dauer < 1.0
        assert gestartet.wait(2)  # das Vorwärmen läuft wirklich
    finally:
        freigeben.set()


def test_fehler_beim_vorwaermen_aendert_die_auth_antwort_nicht(monkeypatch, capsys):
    """Und die Kundennummer darf dabei nicht ins Log — die requests-Exception
    trägt die volle Request-URL (gleicher Grund wie in vorgangsnummern)."""

    def kaputt(session_id):
        raise RuntimeError(
            "404 for url: https://api.tourone.de/get/adresse?kundennummer=999999999"
        )

    client = _auth_client(monkeypatch, kaputt)
    antwort = client.post("/kunde/auth", json={"session_id": "sid"})
    assert antwort.get_json() == {"authenticated": True}

    ausgabe = ""
    for _ in range(200):  # der Thread loggt gleich, aber nicht synchron
        ausgabe += capsys.readouterr().out
        if "kunden warm failed" in ausgabe:
            break
        time.sleep(0.01)
    assert "kunden warm failed: RuntimeError" in ausgabe
    assert "999999999" not in ausgabe


def test_gescheiterte_anmeldung_waermt_nicht(monkeypatch):
    """Ohne Bindung gibt es keine Kundennummer — und nichts vorzuwärmen."""
    import app as app_modul

    gerufen = []
    monkeypatch.setattr(
        app_modul.kunden_auth, "authenticate", lambda *args: (False, "sid")
    )
    monkeypatch.setattr(
        app_modul.kunden_auth, "resolve", lambda sid: gerufen.append(sid)
    )
    antwort = app_modul.app.test_client().post(
        "/kunde/auth", json={"session_id": "sid"}
    )
    assert antwort.get_json() == {"authenticated": False}
    time.sleep(0.05)
    assert gerufen == []


def test_vorwaermen_holt_den_pdf_text_der_naechsten_reise(monkeypatch):
    """T11: Hop 1 → Hop 2 → Reiseunterlagen-Text, alles im Login-Thread."""
    import unterlagen

    geholt = threading.Event()
    fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung()]),
         "/get/buchung": dict(volle_buchung(), unterlagen=UNTERLAGEN)},
    )

    def text(dok_id, link):
        assert link.endswith("ulas.pdf")
        geholt.set()
        return "x" * 300

    monkeypatch.setattr(unterlagen, "text", text)
    client = _auth_client(monkeypatch, lambda sid: "999999999")
    assert client.post("/kunde/auth", json={"session_id": "sid"}).get_json() == {
        "authenticated": True
    }
    assert geholt.wait(2)
