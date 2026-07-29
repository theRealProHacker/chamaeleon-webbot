"""Tests for Kunden-Modus (kundendaten.py + kunden_modus prompt block).

Pure logic is tested directly; TourOne calls are monkeypatched — no live
requests here. Deliberately never imports ``app`` (importing it triggers
live Supabase reads).
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import kundendaten as kd

# --- fixtures ----------------------------------------------------------------

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


def test_overview_ohne_hop2(monkeypatch):
    calls = fake_tourone(
        monkeypatch, {"/get/adresse": adresse_mit([eingebettete_buchung()])}
    )
    text = kd.fetch_buchungen_text("999999999", details=False)
    assert "Buchungsnummer 126001" in text
    assert "kommend" in text  # Zukunfts-Datum → Marker
    assert len(calls) == 1  # grobe Liste macht keinen /get/buchung-Call


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
    assert len(calls) == 1


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


def test_detail_cap_begrenzt_hop2(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit(
                [eingebettete_buchung(str(i)) for i in range(10)]
            ),
            "/get/buchung": volle_buchung(),
        },
    )
    kd.fetch_buchungen_text("999999999", auswahl="alle", details=True)
    assert len(calls) == 1 + kd.MAX_DETAIL


def test_detail_teilerfolg_zeigt_verfuegbare(monkeypatch):
    zustand = {"n": 0}

    def buchung_handler(params):
        zustand["n"] += 1
        if zustand["n"] == 1:
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
    assert "8.198,00 €" in text  # der zweite Call liefert → Detail gewinnt


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
