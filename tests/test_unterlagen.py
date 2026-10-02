"""Tests für unterlagen.py: Dokumente-Block, Download-Allowlist, Text-Cache,
Threadpool, Gliederung und die synthetischen Fixture-PDFs.

Kein Netz: ``unterlagen.requests.get`` wird überall gepatcht. Die PDFs unter
tests/fixtures/unterlagen/ erzeugt scripts/unterlagen_fixtures.py — alle
Personen- und Buchungsdaten darin sind erfunden (siehe die Fixture-Checks am
Ende).
"""

import glob
import json
import os
import re
import threading
from types import SimpleNamespace

import pytest
import requests
from pypdf import PdfReader

import common as _  # noqa: F401  (adds repo root to sys.path)

import unterlagen as ul

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "unterlagen")
LINK = "https://unterlagen.chamaeleon-reisen.de/?datei=abc123"
HEUTE = "2026-10-01"
DECKEL = 60_000


def fixture_bytes(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def fixture_text(name: str) -> str:
    return "\n".join(s.extract_text() for s in PdfReader(os.path.join(FIXTURES, name)).pages)


@pytest.fixture(autouse=True)
def frische_caches():
    ul.text.cache_clear()
    ul._gemeldete_hosts.clear()
    ul._gemeldete_gliederung.clear()
    yield
    ul.text.cache_clear()


def fake_get(monkeypatch, antwort):
    """Patcht requests.get; ``antwort`` ist bytes, eine Exception oder ein
    Statuscode. Liefert die Liste der Aufrufe."""
    aufrufe = []

    def get(url, **kwargs):
        aufrufe.append({"url": url, **kwargs})
        if isinstance(antwort, Exception):
            raise antwort
        status, daten = (antwort, b"") if isinstance(antwort, int) else (200, antwort)
        return SimpleNamespace(
            status_code=status,
            iter_content=lambda groesse: [daten[i : i + groesse] for i in range(0, len(daten), groesse)],
            close=lambda: None,
        )

    monkeypatch.setattr(ul.requests, "get", get)
    return aufrufe


def eintrag(id_, name, link=LINK, beschreibung=None):
    return {"id": str(id_), "name": name, "beschreibung": beschreibung, "link": link}


def buchung(unterlagen, status="OK", von="2026-10-15 00:00:00", bis="2026-10-28 00:00:00", tripurl=None):
    return {"status": status, "vonDat": von, "bisDat": bis, "unterlagen": unterlagen, "tripurl": tripurl}


# --- T1: dokumente_zeilen ------------------------------------------------------


def test_dokumente_feste_reihenfolge_und_gleichnamige_nummeriert():
    liste = [
        eintrag(30, "AGB.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=agb"),
        eintrag(20, "Anschreiben.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=a2"),
        eintrag(12, "Rechnung.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=r"),
        eintrag(10, "Anschreiben.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=a1"),
        eintrag(15, "Aktueller Flugplan.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=af"),
        eintrag(11, "Flugplan.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=f"),
        eintrag(9, "Reisebestätigung.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=b"),
        eintrag(40, "Reiseunterlagen.pdf", link="https://unterlagen.chamaeleon-reisen.de/?datei=u"),
    ]
    zeilen = ul.dokumente_zeilen(buchung(liste), HEUTE)
    assert zeilen == [
        "- Dokumente:",
        "  - [Reiseunterlagen](https://unterlagen.chamaeleon-reisen.de/?datei=u)",
        "  - [Reisebestätigung](https://unterlagen.chamaeleon-reisen.de/?datei=b)",
        "  - [Flugplan](https://unterlagen.chamaeleon-reisen.de/?datei=f)",
        "  - [Aktueller Flugplan](https://unterlagen.chamaeleon-reisen.de/?datei=af)",
        "  - [Rechnung](https://unterlagen.chamaeleon-reisen.de/?datei=r)",
        "  - [AGB](https://unterlagen.chamaeleon-reisen.de/?datei=agb)",
        # aufsteigende id = ältere Version zuerst
        "  - [Anschreiben (1)](https://unterlagen.chamaeleon-reisen.de/?datei=a1)",
        "  - [Anschreiben (2)](https://unterlagen.chamaeleon-reisen.de/?datei=a2)",
    ]


def test_dokumente_gleichnamige_mit_beschreibung():
    liste = [eintrag(2, "Anschreiben.pdf", beschreibung="Schlussunterlagen"), eintrag(1, "Anschreiben.pdf")]
    zeilen = ul.dokumente_zeilen(buchung(liste), HEUTE)
    assert f"  - [Anschreiben (1)]({LINK})" in zeilen
    assert f"  - [Anschreiben (Schlussunterlagen)]({LINK})" in zeilen


def test_dokumente_teilnehmerdaten_ohne_direktlink():
    zeilen = ul.dokumente_zeilen(buchung([eintrag(5, "Teilnehmerdaten.pdf")]), HEUTE)
    assert not any(LINK in z for z in zeilen)
    assert any(z.startswith("  - Teilnehmerdaten (nur in MeinChamäleon") for z in zeilen)


def test_dokumente_eintraege_ohne_link_oder_name():
    liste = [
        {"id": "1", "name": "Rechnung.pdf", "link": None},
        {"id": "2", "name": None, "link": LINK},  # ohne Namen: nicht prüfbar, nicht gezeigt
        {"id": "3"},  # weder Name noch Link: nichts zu listen
        "kaputt",
        eintrag(4, "Flugplan.pdf", link="http://unterlagen.chamaeleon-reisen.de/x"),
        eintrag(6, "Reiseunterlagen.pdf", link="https://x.example/a (b)"),
    ]
    zeilen = ul.dokumente_zeilen(buchung(liste), HEUTE)
    assert zeilen[:5] == [
        "- Dokumente:",
        "  - Reiseunterlagen (ohne Link)",
        "  - Flugplan (ohne Link)",
        "  - Rechnung (ohne Link)",
    ]
    assert len(zeilen) == 4


def test_dokumente_ohne_ulas_hinweis_mit_tagen_bis_abreise():
    zeilen = ul.dokumente_zeilen(buchung([eintrag(1, "Rechnung.pdf")]), HEUTE)
    assert zeilen[-1] == (
        "- Schlussunterlagen (Reiseunterlagen) noch nicht bereitgestellt; "
        "Reisebeginn 15.10.2026, in 14 Tagen (keine Frist nennen: direkt bei "
        "der Erlebnisberater*in melden)"
    )
    morgen = ul.dokumente_zeilen(buchung([], von="2026-10-02 00:00:00"), HEUTE)
    assert "Reisebeginn 02.10.2026, morgen (keine Frist" in morgen[-1]
    spaeter = ul.dokumente_zeilen(buchung([], von="2026-11-15 00:00:00", bis="2026-11-28 00:00:00"), HEUTE)
    assert spaeter[-1].endswith("(in der Regel kommen sie etwa zwei Wochen vor Abreise)")


def test_dokumente_leere_liste_nur_hinweiszeile():
    assert ul.dokumente_zeilen(buchung([]), HEUTE) == [
        "- Schlussunterlagen (Reiseunterlagen) noch nicht bereitgestellt; "
        "Reisebeginn 15.10.2026, in 14 Tagen (keine Frist nennen: direkt bei "
        "der Erlebnisberater*in melden)"
    ]


def test_dokumente_vergangene_reise_ohne_ulas_kein_hinweis():
    """Alte Buchungen ohne ``unterlagen``: „noch nicht bereitgestellt“ wäre falsch."""
    vorbei = buchung([], von="2025-05-01 00:00:00", bis="2025-05-15 00:00:00")
    assert ul.dokumente_zeilen(vorbei, HEUTE) == []


def test_dokumente_mit_ulas_kein_hinweis():
    zeilen = ul.dokumente_zeilen(buchung([eintrag(1, "Reiseunterlagen.pdf")]), HEUTE)
    assert not any("Schlussunterlagen" in z for z in zeilen)


def test_tripurl_nur_bis_reiseende():
    url = "https://travel-details.eu/de?tid=AAAA-BBBB-CCCC"
    kommend = ul.dokumente_zeilen(buchung([], tripurl=url), HEUTE)
    assert kommend[-1] == f"- Aktuelle Einreise-, Visa- und Impfbestimmungen für diese Reise: {url}"
    letzter_tag = ul.dokumente_zeilen(buchung([], bis=f"{HEUTE} 00:00:00", tripurl=url), HEUTE)
    assert url in letzter_tag[-1]
    vergangen = ul.dokumente_zeilen(
        buchung([eintrag(1, "Rechnung.pdf")], von="2026-09-01 00:00:00", bis="2026-09-15 00:00:00", tripurl=url),
        HEUTE,
    )
    assert not any(url in z for z in vergangen)
    assert f"  - [Rechnung]({LINK})" in vergangen  # Dokumente bleiben nach der Reise


def test_storniert_nichts():
    liste = [eintrag(1, "Rechnung.pdf")]
    assert ul.dokumente_zeilen(buchung(liste, status="XX", tripurl="https://travel-details.eu/x"), HEUTE) == []
    assert ul.dokumente_zeilen(buchung(liste, status="AN"), HEUTE) != []


def test_ohne_ulas_heute_laufend_oder_ohne_datum():
    heute = ul.dokumente_zeilen(buchung([], von=f"{HEUTE} 00:00:00"), HEUTE)
    assert "Reisebeginn 01.10.2026, heute (keine Frist" in heute[-1]
    # Reise läuft schon bzw. vonDat unlesbar: keine Tage, kein Rat, nur der Stand.
    nur_stand = "- Schlussunterlagen (Reiseunterlagen) noch nicht bereitgestellt"
    assert ul.dokumente_zeilen(buchung([], von="2026-09-25 00:00:00"), HEUTE) == [nur_stand]
    assert ul.dokumente_zeilen(buchung([], von=None), HEUTE) == [nur_stand]


# --- Auswahl: quelle_auto, Slugs ---------------------------------------------


def test_quelle_auto_ulas_vor_best_hoechste_id():
    liste = [eintrag(5, "Reisebestätigung.pdf"), eintrag(7, "Reiseunterlagen.pdf"), eintrag(9, "Reiseunterlagen.pdf")]
    assert ul.quelle_auto(liste)["id"] == "9"
    assert ul.quelle_auto(liste[:1])["id"] == "5"
    assert ul.quelle_auto([eintrag(1, "Rechnung.pdf")]) is None
    assert ul.quelle_auto(None) is None


def test_slugs_hoechste_id_ohne_ulas_best_und_teilnehmerdaten():
    liste = [
        eintrag(3, "Visum Ausfüllhilfen.pdf"),
        eintrag(8, "Visum Ausfüllhilfen.pdf"),
        eintrag(4, "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf"),
        eintrag(5, "Aktueller Flugplan.pdf"),
        eintrag(6, "Visa-Dokumente.pdf"),
        eintrag(7, "Reiseunterlagen.pdf"),
        eintrag(9, "Reisebestätigung.pdf"),
        eintrag(10, "Teilnehmerdaten.pdf"),
        eintrag(11, "Anschreiben.pdf"),
        eintrag(12, "Wichtige Reisedokumente.pdf"),
        eintrag(13, "AGB.pdf"),
        eintrag(14, "Versicherungsangebot Hanse Merkur.pdf"),
    ]
    slugs = ul.dokumente_nach_slug(liste)
    assert list(slugs) == [
        "aktueller-flugplan",
        "visum-ausfuellhilfen",
        "visa-dokumente",
        "einreisebestimmungen",
        "wichtige-reisedokumente",
        "anschreiben",
    ]
    assert slugs["visum-ausfuellhilfen"]["id"] == "8"


# --- T2: Download mit Allowlist ----------------------------------------------


def test_fremder_host_wird_nicht_geladen_und_einmal_geloggt(monkeypatch, capsys):
    aufrufe = fake_get(monkeypatch, b"%PDF")
    assert ul._laden("https://evil.example/x.pdf") is None
    assert ul._laden("https://evil.example/y.pdf") is None
    assert ul._laden("http://unterlagen.chamaeleon-reisen.de/x") is None  # kein https
    assert ul._laden("https://unterlagen.chamaeleon-reisen.de.evil.example/x") is None
    assert aufrufe == []
    log = capsys.readouterr().out
    assert log.count("'evil.example'") == 1


def test_download_timeout_und_ohne_redirects(monkeypatch):
    aufrufe = fake_get(monkeypatch, b"%PDF-daten")
    assert ul._laden(LINK) == b"%PDF-daten"
    assert aufrufe == [{"url": LINK, "timeout": 8, "allow_redirects": False, "stream": True}]


def test_download_ohne_pdf_oder_zu_gross_wirft(monkeypatch):
    # Eine Fehlerseite mit HTTP 200 ist ein Ausfall (nicht gecacht), kein
    # „nicht lesbares PDF“ für 24 h.
    fake_get(monkeypatch, b"<html>Wartung</html>")
    with pytest.raises(ValueError):
        ul._laden(LINK)
    monkeypatch.setattr(ul, "MAX_BYTES", 10)
    fake_get(monkeypatch, b"%PDF-" + b"x" * 20)
    with pytest.raises(ValueError):
        ul._laden(LINK)


@pytest.mark.parametrize("antwort", [500, 302, requests.Timeout("zu langsam")])
def test_download_fehler_wirft(monkeypatch, antwort):
    fake_get(monkeypatch, antwort)
    with pytest.raises(requests.RequestException):
        ul._laden(LINK)


def test_ausfall_gerade_nicht_abrufbar_mit_link_und_nicht_gecacht(monkeypatch):
    aufrufe = fake_get(monkeypatch, requests.Timeout("zu langsam"))
    text, hinweis = ul.lesen(eintrag(1, "Rechnung.pdf"))
    assert text is None
    assert "gerade nicht abrufbar" in hinweis and LINK in hinweis
    assert "nicht vorhanden" not in hinweis
    ul.lesen(eintrag(1, "Rechnung.pdf"))
    assert len(aufrufe) == 2  # Ausfall nicht gecacht


def test_fremder_host_nur_verlinkt(monkeypatch):
    fake_get(monkeypatch, b"%PDF")
    text, hinweis = ul.lesen(eintrag(1, "Rechnung.pdf", link="https://evil.example/r.pdf"))
    assert text is None and "https://evil.example/r.pdf" in hinweis


# --- T3: text(), Cache, Threadpool, Fehlerpfade ------------------------------


def test_text_extrahiert_und_cacht_je_id(monkeypatch):
    aufrufe = fake_get(monkeypatch, fixture_bytes("rechnung.pdf"))
    erster = ul.text("1", LINK)
    assert "Deine Rechnung" in erster
    assert ul.text("1", LINK) == erster
    assert len(aufrufe) == 1
    ul.text("2", LINK)  # neue id = neue Version = neuer Abruf
    assert len(aufrufe) == 2


def test_weniger_als_200_zeichen_nicht_lesbar(monkeypatch):
    fake_get(monkeypatch, fixture_bytes("fast_leer.pdf"))
    assert ul.text("1", LINK) == ""
    text, hinweis = ul.lesen(eintrag(1, "Rechnung.pdf"))
    assert text is None and "nicht lesbar" in hinweis and LINK in hinweis


def test_kaputtes_pdf_nicht_lesbar(monkeypatch):
    fake_get(monkeypatch, b"%PDF-1.4 kaputt")
    assert ul.text("1", LINK) == ""


def test_threadpool_nur_unter_gevent_patch(monkeypatch):
    haupt = threading.get_ident()
    assert ul._im_threadpool(threading.get_ident) == haupt  # ohne Patch direkt
    monkeypatch.setattr(ul.monkey, "is_module_patched", lambda name: True)
    assert ul._im_threadpool(threading.get_ident) != haupt  # echter OS-Thread
    fake_get(monkeypatch, fixture_bytes("rechnung.pdf"))
    assert "Deine Rechnung" in ul.text("1", LINK)


def test_teilnehmerdaten_und_eintraege_ohne_link_nie_geladen(monkeypatch):
    aufrufe = fake_get(monkeypatch, fixture_bytes("rechnung.pdf"))
    text, hinweis = ul.lesen(eintrag(1, "Teilnehmerdaten.pdf"))
    assert text is None and hinweis == ul.TEILNEHMERDATEN_TEXT and LINK not in hinweis
    assert ul.dokument(eintrag(1, "Teilnehmerdaten.pdf")) == ul.TEILNEHMERDATEN_TEXT
    assert ul.lesen({"id": "2", "name": "Rechnung.pdf"})[0] is None
    assert ul.lesen({"id": "3", "link": LINK})[0] is None
    assert aufrufe == []


# --- T4: Gliederung ------------------------------------------------------------


def test_gliederung_neues_template():
    gl = ul.gliedern(fixture_text("reiseunterlagen_neu.pdf"))
    tage = gl["tage"]
    assert len(tage) == 13
    assert (tage[0]["wochentag"], tage[0]["datum"], tage[0]["titel"]) == ("Di", "06.10.2026", "Abflug nach Namibia")
    # Anschlussprogramm: gleicher Datumstag, eigene Tage
    assert tage[7]["programm"] == "" and tage[8]["programm"] == "KAPSTADT"
    assert tage[7]["datum"] == tage[8]["datum"] == "13.10.2026"
    abschnitte = gl["abschnitte"]
    assert set(abschnitte) == {
        "highlights",
        "reiseverlauf",
        "leistungen",
        "hinweise",
        "wichtige-reisehinweise",
        "checkliste",
        "fluege",
        "reiseinformationen",
    }
    assert len(abschnitte["leistungen"]) == 2  # Reise + Anschlussprogramm
    # Umbruch-Toleranz
    assert abschnitte["fluege"][1]["titel"] == "INFORMATIONEN INLANDS- UND REGIONALFLÜGE"
    assert abschnitte["fluege"][1]["text"].startswith("Gepäckbestimmungen für den Regionalflug")
    # Programmname landet nicht im vorigen Abschnitt
    assert "KAPSTADT" not in abschnitte["hinweise"][-1]["text"]
    # Unterüberschriften
    assert abschnitte["reiseinformationen"][0]["unter"] == ["Fahrzeuge", "Geld und Kreditkarten", "Strom"]
    assert "Abholung am Zielflughafen" in abschnitte["wichtige-reisehinweise"][0]["unter"]
    assert "Notfalltelefonnummer" in abschnitte["wichtige-reisehinweise"][0]["unter"]


def test_gliederung_laesst_deckblatt_und_generisches_weg():
    gl = ul.gliedern(fixture_text("reiseunterlagen_neu.pdf"))
    alles = json.dumps(gl, ensure_ascii=False)
    for draussen in (
        "Musterfrau",  # Deckblatt
        "Vorgang 900001",
        "Reisen in kleinen Gruppen",  # ALLGEMEINE REISEINFORMATIONEN
        "drei Stunden vor dem Abflug",  # ICH BIN DANN MAL WEG...
        "100 ml",  # EU-HANDGEPÄCKREGELUNG.
        "Rückmeldung",  # WIE HAT ES DIR GEFALLEN?
    ):
        assert draussen not in alles, draussen


def test_gliederung_altes_template():
    gl = ul.gliedern(fixture_text("reisebestaetigung_alt.pdf"))
    tage = gl["tage"]
    assert len(tage) == 10
    assert all(t["wochentag"] == "" for t in tage)
    assert tage[1]["titel"] == "Ankunft in Alice Springs"
    assert "Hotel Wüstenrose" in tage[1]["text"]
    ri = gl["abschnitte"]["reiseinformationen"]
    assert ri[0]["titel"] == "REISEINFORMATIONEN ROTE ERDE"
    assert ri[0]["unter"] == ["Fahrzeuge", "Nebenkosten vor Ort"]
    assert "wichtige-reisehinweise" not in gl["abschnitte"]
    assert "reisemedizinisch" not in json.dumps(gl, ensure_ascii=False)  # BERATUNG


def test_unbekanntes_template_nicht_gegliedert(monkeypatch, capsys):
    assert ul.gliedern("Irgendein Text\n01.02.2026 Ein Datum\nohne Überschriften") is None
    fake_get(monkeypatch, fixture_bytes("visum_ausfuellhilfen.pdf"))
    # Ohne Abschnitt: None, damit die Textbausteine antworten; mit Abschnitt
    # der Hinweis mit Link.
    assert ul.reiseunterlagen(eintrag(77, "Reiseunterlagen.pdf"), deckel=DECKEL) is None
    antwort = ul.reiseunterlagen(eintrag(77, "Reiseunterlagen.pdf"), "leistungen", deckel=DECKEL)
    assert antwort == ul.NICHT_GEGLIEDERT_TEXT.format(name="Reiseunterlagen", link=LINK)
    assert capsys.readouterr().out.count("Dokument 77") == 1


def test_uebersicht_mit_wichtigen_reisehinweisen(monkeypatch):
    fake_get(monkeypatch, fixture_bytes("reiseunterlagen_neu.pdf"))
    antwort = ul.reiseunterlagen(
        eintrag(1, "Reiseunterlagen.pdf"), deckel=DECKEL, slugs=("rechnung", "visum-ausfuellhilfen")
    )
    assert antwort.startswith(f"Quelle: Reiseunterlagen ({LINK})")
    # 13 Einträge, aber 12 Kalendertage: der 13.10. steht zweimal (Anschlussprogramm)
    assert "- reiseverlauf: 12 Tage, 06.10.2026 bis 17.10.2026" in antwort
    assert "Reiseleitung: Herr Jonas Fiktiv" in antwort
    assert "dokument:rechnung, dokument:visum-ausfuellhilfen" in antwort
    assert "- reiseinformationen: Reiseinformationen (Fahrzeuge, Geld und Kreditkarten, Strom)" in antwort
    # WICHTIGE REISEHINWEISE komplett: Abholung, Partnerkontakt, Notfallnummer
    assert "Beispiel Safaris Namibia" in antwort and "+49-(0)170-0000000" in antwort
    assert "Dünenblick Lodge" not in antwort  # Tage nur auf Nachfrage


def test_reiseverlauf_ein_tag_alle_tage_und_deckel():
    gl = ul.gliedern(fixture_text("reiseunterlagen_neu.pdf"))
    tag3 = ul.abschnitt_text(gl, "reiseverlauf", 3, deckel=DECKEL)
    assert tag3.startswith("Tag 3 · Do 08.10.2026 · In die Namib")
    assert "Dünenblick Lodge" in tag3 and "Swakopmund" not in tag3
    # tag=N ist der N-te Kalendertag ab dem ersten Tag, nicht der N-te Eintrag:
    # am 13.10. enden Hauptreise und beginnt das Anschlussprogramm, Tag 8 hat
    # also beide Einträge, und Tag 9 ist der 14.10.
    tag8 = ul.abschnitt_text(gl, "reiseverlauf", 8, deckel=DECKEL)
    assert tag8.startswith("Tag 8 · Di 13.10.2026 · Rückfahrt nach Windhoek und Weiterflug")
    assert "Tag 8 · Di 13.10.2026 · Flug nach Kapstadt (Anschlussprogramm KAPSTADT)" in tag8
    tag9 = ul.abschnitt_text(gl, "reiseverlauf", 9, deckel=DECKEL)
    assert tag9.startswith("Tag 9 · Mi 14.10.2026 · Tafelberg und Kap der Guten Hoffnung")
    assert "Tag 12 · Sa 17.10.2026 · Wieder zu Hause" in ul.abschnitt_text(gl, "reiseverlauf", 12, deckel=DECKEL)
    assert "die Reise hat 12 Tage" in ul.abschnitt_text(gl, "reiseverlauf", 13, deckel=DECKEL)
    alle = ul.abschnitt_text(gl, "reiseverlauf", 0, deckel=DECKEL)
    assert "Dünenblick Lodge" in alle and "Hotel Hafenlicht" in alle
    # über dem Deckel: Inhaltsverzeichnis der Tage, nie mitten im Tag gekürzt
    toc = ul.abschnitt_text(gl, "reiseverlauf", 0, deckel=500)
    assert "Dünenblick Lodge" not in toc
    assert "- Tag 3 · Do 08.10.2026 · In die Namib" in toc
    assert "- Tag 12 · Sa 17.10.2026 · Wieder zu Hause (Anschlussprogramm KAPSTADT)" in toc


def test_unterkuenfte_ist_der_reiseverlauf():
    """Gemessen 2026-10-02: Gemini fragt nach „unterkuenfte“; die Hotels
    stehen je Tag im Reiseverlauf, „gibt es nicht“ wäre falsch."""
    gl = ul.gliedern(fixture_text("reiseunterlagen_neu.pdf"))
    for name in ("unterkuenfte", "Unterkünfte", " hotels "):
        alle = ul.abschnitt_text(gl, name, 0, deckel=DECKEL)
        assert "Dünenblick Lodge" in alle and "Hotel Hafenlicht" in alle, name


def test_tagnummer_ohne_lesbares_datum_zaehlt_eintraege():
    # Datum, das es nicht gibt, oder Sprung zurück: dann lieber die alte
    # Zählung nach Einträgen als eine falsche Kalenderrechnung.
    def t(datum, titel):
        return {"wochentag": "", "datum": datum, "titel": titel, "text": "", "programm": ""}
    gl = {"abschnitte": {}, "tage": [t("30.02.2026", "A"), t("01.03.2026", "B")]}
    assert ul.abschnitt_text(gl, "reiseverlauf", 2, deckel=DECKEL).startswith("Tag 2 · 01.03.2026 · B")
    gl = {"abschnitte": {}, "tage": [t("05.03.2026", "A"), t("01.03.2026", "B")]}
    assert ul.abschnitt_text(gl, "reiseverlauf", 2, deckel=DECKEL).startswith("Tag 2 · 01.03.2026 · B")


def test_abschnitt_text_andere_und_unbekannte():
    gl = ul.gliedern(fixture_text("reisebestaetigung_alt.pdf"))
    leistungen = ul.abschnitt_text(gl, "leistungen", deckel=DECKEL)
    assert leistungen.startswith("LEISTUNGEN BEI CHAMÄLEON\n- Linienflug")
    fehlt = ul.abschnitt_text(gl, "wichtige-reisehinweise", deckel=DECKEL)
    assert "gibt es in diesem Dokument nicht" in fehlt
    assert "Vorhanden: reiseverlauf, leistungen, hinweise, checkliste, reiseinformationen, fluege." in fehlt
    ohne_tage = {"abschnitte": {}, "tage": []}
    assert ul.abschnitt_text(ohne_tage, "reiseverlauf", deckel=DECKEL) == "Dieses Dokument enthält keinen Tagesablauf."


def test_einreisebestimmungen_ein_block_je_nationalitaet(monkeypatch):
    fake_get(monkeypatch, fixture_bytes("einreisebestimmungen.pdf"))
    antwort = ul.dokument(eintrag(1, "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf"))
    assert "Stand bei Buchung, 03.02.2026" in antwort
    assert antwort.count("Für Reisende mit Staatsangehörigkeit DE:") == 1
    assert antwort.count("Für Reisende mit Staatsangehörigkeit AT:") == 1
    de, at = antwort.split("Für Reisende mit Staatsangehörigkeit AT:")
    assert de.count("Zielland: Namibia") == 1 and de.count("Zielland: Südafrika") == 1
    assert "Österreichische Staatsangehörige benötigen ein Visum." in at
    assert "Deutsche" not in at
    assert "innerhalb von 6 Stunden" in de  # Vortext der klebenden Nummer bleibt
    for draussen in ("Musterfrau", "Beispielmann", "Probst", "Geburtsdatum", "01.01.1970", "Beispielweg", "Trip-URL"):
        assert draussen not in antwort, draussen


def test_einreisebestimmungen_ohne_personenzeile_nicht_gegliedert():
    assert ul.einreise_bloecke("Einreisebestimmungen\nZielland: Namibia\nVisum nötig.") is None


def test_einreise_ohne_nationalitaet_und_stand_aus_buchungsdatum():
    roh = (
        "Buchung vom 03.02.2026\n"
        "1. Frau Musterfrau, Erika\nGeburtsdatum: 01.01.1970\n"
        "Zielland: Namibia\nVisum nötig.\n"
    )
    antwort = ul.einreise_bloecke(roh)
    assert "(Stand bei Buchung, 03.02.2026)" in antwort
    assert "Für Reisende mit Staatsangehörigkeit ohne Angabe (1. Reisende*r):" in antwort
    assert "Musterfrau" not in antwort and "01.01.1970" not in antwort


def test_dokument_einreise_ohne_personenzeile_nicht_gegliedert(monkeypatch):
    monkeypatch.setattr(ul, "text", lambda dok_id, link: "Zielland: Namibia\n" + "Visum nötig. " * 30)
    antwort = ul.dokument(eintrag(1, "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf"))
    assert antwort == ul.NICHT_GEGLIEDERT_TEXT.format(
        name="Einreisebestimmungen Zeitpunkt der Reiseanmeldung", link=LINK
    )


def test_vorwaermen_nur_hauptdokument_offener_buchungen(monkeypatch):
    geholt = []
    monkeypatch.setattr(ul, "text", lambda dok_id, link: geholt.append((dok_id, link)))
    liste = [eintrag(1, "Rechnung.pdf"), eintrag(2, "Reiseunterlagen.pdf", link=LINK + "u")]
    ul.vorwaermen(None)
    ul.vorwaermen(buchung(liste, status="XX"))
    ul.vorwaermen(buchung(liste[:1]))  # weder ULAS noch BEST
    ul.vorwaermen(buchung([{"id": "3", "name": "Reiseunterlagen.pdf", "link": None}]))
    assert geholt == []
    ul.vorwaermen(buchung(liste))
    assert geholt == [("2", LINK + "u")]


def test_dokument_klein_ganz(monkeypatch):
    fake_get(monkeypatch, fixture_bytes("visum_ausfuellhilfen.pdf"))
    antwort = ul.dokument(eintrag(1, "Visum Ausfüllhilfen.pdf"))
    assert antwort.startswith(f"Inhalt von „Visum Ausfüllhilfen“ ({LINK}):")
    assert "(Stand 01.08.2026)" in antwort
    assert "Gästehaus Kameldorn, Windhoek" in antwort


# --- T5: Fixtures enthalten keine echten Daten -------------------------------

ERFUNDENE_NAMEN = {
    "Musterfrau", "Beispielmann", "Probst", "Platzhalter", "Fiktiv", "Erfunden",
    "Erika", "Theo", "Mia", "Paula", "Jonas", "Lena",
}
FIXTURE_PDFS = sorted(glob.glob(os.path.join(FIXTURES, "*.pdf")))


def test_fixture_pdfs_vorhanden():
    assert {os.path.basename(p) for p in FIXTURE_PDFS} >= {
        "reiseunterlagen_neu.pdf",
        "reisebestaetigung_alt.pdf",
        "visum_ausfuellhilfen.pdf",
        "rechnung.pdf",
        "einreisebestimmungen.pdf",
        "fast_leer.pdf",
    }


@pytest.mark.parametrize("pfad", FIXTURE_PDFS, ids=os.path.basename)
def test_fixture_nur_erfundene_personen_und_nummern(pfad):
    text = fixture_text(os.path.basename(pfad))
    for nummer in re.findall(r"(?:Vorgang|Vorgangsnr\.|Rechnung|Kunde|Kundennummer) (\d{5,})", text):
        assert nummer.startswith(("9000", "8000")), nummer
    for nachname in re.findall(r"\b(?:Herr|Frau)\s+([A-ZÄÖÜ][a-zäöüß]+)", text):
        assert nachname in ERFUNDENE_NAMEN, nachname
    for mail in re.findall(r"[\w.+-]+@[\w.-]+", text):
        assert mail.endswith(".invalid"), mail
    for telefon in re.findall(r"\+\d[\d ()/-]{6,}\d", text):
        assert re.sub(r"\D", "", telefon).endswith("000000"), telefon


# Echte Buchungen, aus denen die Struktur abgeschaut wurde, liegen nur im
# Scratchpad außerhalb des Repos. Die Liste der verbotenen Strings wird dort zur
# Laufzeit gebaut und nie eingecheckt; ohne Scratchpad wird übersprungen.
QUELLE = os.environ.get(
    "UNTERLAGEN_QUELLE",
    "/tmp/claude-1000/-home-rharvey-Dokumente-Programmieren-cham-chamaeleon-webbot/"
    "0b4800a0-55c3-4651-88aa-817c2747859d/scratchpad/pdfs",
)
_FELDER = ("vorgang", "adrName", "adrVorname", "adrStrasse", "adrKundenNr", "adrEmail", "adrTel", "adrHandy", "expName")


def _verbotene_strings(quelle: str) -> set:
    verboten = set()
    for pfad in glob.glob(os.path.join(quelle, "b_*.json")):
        with open(pfad, encoding="utf-8") as f:
            b = json.load(f)
        for feld in _FELDER:
            wert = str(b.get(feld) or "").strip()
            if len(wert) >= 4:
                verboten.add(wert)
    for pfad in glob.glob(os.path.join(quelle, "*Rechnung*.txt")) + glob.glob(os.path.join(quelle, "*Einreise*.txt")):
        with open(pfad, encoding="utf-8", errors="replace") as f:
            for nachname in re.findall(r"\b(?:Herr|Frau)\s+([A-ZÄÖÜ][\wäöüß-]+),", f.read()):
                verboten.add(nachname)
    return verboten


def test_fixtures_ohne_namen_und_nummern_der_quellbuchungen():
    if not os.path.isdir(QUELLE):
        pytest.skip("Quellbuchungen nicht vorhanden (liegen bewusst außerhalb des Repos)")
    verboten = _verbotene_strings(QUELLE)
    assert verboten, "keine verbotenen Strings gefunden — Quelle falsch?"
    for pfad in FIXTURE_PDFS:
        text = fixture_text(os.path.basename(pfad))
        treffer = sorted(v for v in verboten if v in text)
        # Nur die Anzahl ausgeben: die Treffer selbst wären echte Daten im Log.
        assert not treffer, f"{os.path.basename(pfad)}: {len(treffer)} echte Strings"


# --- Review-Befunde 2026-09-28 -----------------------------------------------


@pytest.mark.parametrize("name", ["Teilnehmerdaten.pdf", "Teilnehmer-Daten.pdf", "Liste Teilnehmer.pdf"])
def test_teilnehmerdaten_auch_unter_anderem_namen_nie_verlinkt(name):
    zeilen = ul.dokumente_zeilen(buchung([eintrag(1, name)]), HEUTE)
    assert not any(LINK in z for z in zeilen)
    assert ul.lesen(eintrag(1, name)) == (None, ul.TEILNEHMERDATEN_TEXT)


def test_unicode_ziffer_als_id_reisst_die_liste_nicht():
    zeilen = ul.dokumente_zeilen(buchung([eintrag("²", "Rechnung.pdf"), eintrag(3, "Flugplan.pdf")]), HEUTE)
    assert zeilen[1:3] == [f"  - [Flugplan]({LINK})", f"  - [Rechnung]({LINK})"]


def test_links_im_modelltext_nur_sicher(monkeypatch):
    fake_get(monkeypatch, fixture_bytes("rechnung.pdf"))
    unsicher = eintrag(1, "Rechnung.pdf", link="https://evil.example/a (b)")
    assert "evil" not in ul.lesen(unsicher)[1]
    buchung_ = {"tripurl": "https://travel-details.eu/x y", "bisDat": "2099-01-01"}
    assert ul.aktuelle_tripurl(buchung_, HEUTE) == ""
    assert ul.aktuelle_tripurl({**buchung_, "tripurl": "https://t.eu/ok"}, HEUTE) == "https://t.eu/ok"


def test_dokument_ohne_anschrift_des_gasts(monkeypatch):
    fake_get(monkeypatch, fixture_bytes("rechnung.pdf"))
    antwort = ul.dokument(eintrag(1, "Rechnung.pdf"))
    assert "Beispielweg" not in antwort and "Musterstadt" not in antwort
    assert "\nVorgang 900001\n" in antwort and "Deine Rechnung" in antwort


EINREISE_UMBRUCH = """Einreisebestimmungen
1. Frau Musterfrau, Erika
Geburtsdatum: 01.01.1970,
Staatsangehörigkeit: DE
Zielland: Namibia
Deutsche Staatsangehörige benötigen ein Visum.
2. Herr Probst, Max
Geburtsdatum: 02.02.1972,
Staatsangehörigkeit: CH
Zielland: Namibia
Schweizer Staatsangehörige benötigen kein Visum.
"""


def test_einreise_nationalitaet_nach_umbruch_nicht_zusammengelegt():
    antwort = ul.einreise_bloecke(EINREISE_UMBRUCH)
    assert "Staatsangehörigkeit DE:" in antwort and "Staatsangehörigkeit CH:" in antwort
    assert "Schweizer Staatsangehörige benötigen kein Visum." in antwort
    assert "Musterfrau" not in antwort and "Probst" not in antwort and "1970" not in antwort


def test_einreise_nicht_erkannte_person_nur_link():
    # Die zweite Kopfzeile ist umgebrochen: nicht als Grenze erkannt, ihre
    # Zeilen stünden im DE-Block — dann lieber gar nicht gliedern.
    kaputt = EINREISE_UMBRUCH.replace("2. Herr Probst, Max\n", "2. Herr Probst,\nMax\n\n")
    assert ul.einreise_bloecke(kaputt) is None


def test_nationalitaet_nicht_aus_dem_naechsten_block():
    # Erste Person ohne Nationalität und ohne Bestimmungen: die der zweiten
    # darf nicht in ihren Block rutschen (sonst fielen deren Regeln weg).
    text = EINREISE_UMBRUCH.replace(
        "Geburtsdatum: 01.01.1970,\nStaatsangehörigkeit: DE\nZielland: Namibia\n"
        "Deutsche Staatsangehörige benötigen ein Visum.\n",
        "Geburtsdatum: 01.01.1970\n",
    )
    antwort = ul.einreise_bloecke(text)
    assert "Staatsangehörigkeit CH:" in antwort
    assert "Schweizer Staatsangehörige benötigen kein Visum." in antwort


def test_umgebrochener_titel_wird_verbunden():
    # Gemessen: „REISEINFORMATIONEN MACHU“ / „PICCHU“ — die erste Zeile passt
    # schon allein, die Fortsetzung gehört trotzdem in den Titel.
    assert ul._hauptueberschrift("REISEINFORMATIONEN MACHU", "PICCHU")[1] == "REISEINFORMATIONEN MACHU PICCHU"
