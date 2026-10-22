"""Verhaltens-Eval: Einreise und Visum aus Passolution (Design
passolution-einreise, §7 Fälle 1–6 und 5a; 7–9: was Chamäleon selbst zum
Visum organisiert, steht neben den Passolution-Regeln).

Jeder Fall ist ein Live-Aufruf von Gemini. Passolution ist gemockt: die
Antworten sind echte Bodies aus tests/fixtures/passolution/ (nur Länderregeln),
die TripURL ist synthetisch. So kostet die Eval kein Kontingent und bleibt
reproduzierbar. Die Buchungen sind erfunden wie in test_unterlagen_eval.py.

NIE Teil der Standard-Suite. Manuell:

    RUN_EINREISE_EVAL=1 pytest tests/test_einreise_eval.py -q -s

Die Mocks setzen ``raising=False``: so läuft dieselbe Datei auch gegen einen
Stand ohne passolution-Abruf (A/B gegen main).

Owner 2026-10-22: Leon darf nachfragen, wenn etwas wirklich fraglich ist. Eine
Rückfrage nach der Staatsangehörigkeit ist auf der Website richtig (Fall 1); in
5 und 5a steht sie in der Buchung, dort ist jede solche Frage ein Fehler.
"""

import datetime
import json
import os
import re

import pytest
from cachetools.func import ttl_cache

import common as _  # noqa: F401  (adds repo root to sys.path)

import kundendaten
import passolution
import unterlagen
from agent import call

RUN = os.getenv("RUN_EINREISE_EVAL") == "1"

pytestmark = pytest.mark.skipif(
    not RUN, reason="live Einreise-Eval - RUN_EINREISE_EVAL=1 setzen"
)

_HIER = os.path.dirname(os.path.abspath(__file__))
_PASSOLUTION = os.path.join(_HIER, "fixtures", "passolution")
_UNTERLAGEN = os.path.join(_HIER, "fixtures", "unterlagen")
_HOST = "https://unterlagen.chamaeleon-reisen.de/eval/"

KOLUMBIEN = "/Amerika/Kolumbien/San-Agustin"
CAPRIVI = "/Afrika/Botswana-Namibia/Caprivi"
# Live-Reiseseiten (Website-Tool ungemockt): Petra führt „Gruppenvisum (ab 5
# Gäste)“ in den Leistungen, Nofretete die Hilfe des Teams am Flughafen Kairo
# in den Reisedetails (gemessen 2026-10-22).
PETRA = "/Asien/Jordanien/Petra"
NOFRETETE = "/Afrika/Aegypten/Nofretete"
KUNDEN_ID = "TEST_KUNDE_EINREISE"
BN = "9900002"
KUNDEN_ENDPOINT = f"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG={BN}"
TRIPURL = "https://travel-details.eu/de?tid=TEST-TEST-0001"
# Erfundene Reiseunterlagen einer Petra-Buchung als Text (kein echter Kunde).
REISEUNTERLAGEN_JOPET = {"id": "15", "name": "Reiseunterlagen.pdf", "link": _HOST + "reiseunterlagen_jopet.txt"}

AUSFUELLHILFE = {"id": "13", "name": "Visum Ausfüllhilfen.pdf", "link": _HOST + "visum_ausfuellhilfen.pdf"}
EINREISE = {"id": "12", "name": "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf",
            "link": _HOST + "einreisebestimmungen.pdf"}

# Eine Bitte um die Staatsangehörigkeit: eine Frage („Welche … hast du?“) oder
# eine Aufforderung („nenne mir bitte deine Staatsangehörigkeit“). „Für
# Staatsangehörige CH gilt …“ zählt nicht.
NACH_NATIONALITAET = re.compile(
    r"(welche|nenne|nennst|sag|verrat|teil|benötige ich|brauche ich)[^.!?<]{0,60}(Staatsangehörigkeit|Nationalität)"
    r"|(Staatsangehörigkeit|Nationalität)[^.!?<]*\?",
    re.IGNORECASE,
)
STAND = re.compile(r"\d\d\.\d\d\.\d{4}|Stand", re.IGNORECASE)

_RECORDS = []
for _name in ("all_text_co_de.json", "all_text_bw_na_zw_dach.json",
              "all_text_sz_za_ch.json", "all_text_ca_eg_jo_de.json"):
    with open(os.path.join(_PASSOLUTION, _name), encoding="utf-8") as _f:
        _RECORDS += json.load(_f)["records"]
with open(os.path.join(_PASSOLUTION, "travel_details_synthetisch.json"), encoding="utf-8") as _f:
    TRIPURL_DATEN = json.load(_f)["data"]


def passolution_gemockt(monkeypatch, ausfall: bool = False) -> list:
    """``_abrufen`` aus den Fixtures (oder 503); gibt die Abrufe zurück.

    Mit ttl_cache wie das Original, weil ``einreise_text`` in dessen Cache
    nachsieht. Unbekannte Kombinationen werfen wie ein leerer Abruf.
    """
    abrufe: list = []

    @ttl_cache(maxsize=64, ttl=600)
    def abrufen(laender: tuple, nats: tuple) -> list:
        abrufe.append((laender, nats))
        if ausfall:
            raise RuntimeError("503")
        treffer = [r for r in _RECORDS if r["destination"] in laender and r["nationality"] in nats]
        if not treffer:
            raise ValueError("keine records")
        return treffer

    monkeypatch.setattr(passolution, "_abrufen", abrufen, raising=False)
    monkeypatch.setattr(passolution, "_tripurl_daten", lambda tid: None, raising=False)
    monkeypatch.setattr(
        passolution, "_reise_laender", lambda code: ["JO"] if code == "JOPET" else ["NA"], raising=False
    )
    seiten = {"Caprivi": ["BW", "NA", "ZW"], "Kolumbien/": ["CO"], "Jordanien/": ["JO"], "Aegypten/": ["EG"]}
    monkeypatch.setattr(
        passolution, "laender_der_seite",
        lambda pfad: next((codes for teil, codes in seiten.items() if teil in pfad), []),
        raising=False,
    )
    return abrufe


def _datum(tage: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=tage)).strftime("%Y-%m-%d 00:00:00")


def buchung_einrichten(
    monkeypatch, eintraege: list, tripurl: str = "",
    reise_code: str = "NAWDH", titel: str = "Namibia – Zauber der Weite",
) -> None:
    """Erfundene, eigene Buchung in 40 Tagen (Standard Namibia); PDFs aus den Fixtures."""
    eingebettet = {"vorgang": BN, "vonDat": _datum(40), "bisDat": _datum(52), "reiseCode": reise_code}
    roh = dict(
        eingebettet, status="OK", beschreibungen=[{"titel": titel}],
        persAdult=2, flugdaten=[], tripurl=tripurl,
        unterlagen=[dict(e, beschreibung="") for e in eintraege],
    )
    monkeypatch.setattr(kundendaten, "_buchungen_roh", lambda kid: [eingebettet])
    monkeypatch.setattr(kundendaten, "_buchung_roh", lambda v: roh)
    monkeypatch.setattr(kundendaten, "buchungsstatus", lambda v: "OK")

    def laden(link):
        with open(os.path.join(_UNTERLAGEN, link.rsplit("/", 1)[1]), "rb") as f:
            return f.read()

    monkeypatch.setattr(unterlagen, "_laden", laden)
    # Text-Fixtures (.txt) statt PDF: an pypdf vorbei, direkt als Text.
    original = unterlagen.text

    def text(dok_id, link):
        if link.endswith(".txt"):
            with open(os.path.join(_UNTERLAGEN, link.rsplit("/", 1)[1]), encoding="utf-8") as f:
                return f.read()
        return original(dok_id, link)

    monkeypatch.setattr(unterlagen, "text", text)


def website(verlauf: list, endpoint: str) -> str:
    return call(verlauf, endpoint)


def _frage(text: str) -> list:
    return [{"role": "user", "content": text}]


def test_1_website_fragt_nach_der_staatsangehoerigkeit(monkeypatch):
    passolution_gemockt(monkeypatch)
    reply = website(_frage("Brauche ich ein Visum?"), KOLUMBIEN)
    assert NACH_NATIONALITAET.search(reply), reply
    assert "visum.de" not in reply, reply


def test_2_nach_der_antwort_regeln_mit_stand(monkeypatch):
    abrufe = passolution_gemockt(monkeypatch)
    verlauf = _frage("Brauche ich ein Visum?") + [
        {"role": "assistant", "content": "<p>Gern schaue ich das für dich nach! Welche Staatsangehörigkeit hast du?</p>"},
        {"role": "user", "content": "deutsch"},
    ]
    reply = website(verlauf, KOLUMBIEN)
    assert re.search(r"kein(en)? Visum|90 Tage", reply, re.I), reply
    assert STAND.search(reply), reply
    assert "visum.de" not in reply, reply
    if abrufe:  # nur mit Passolution-Tool
        assert all("CO" in laender for laender, _ in abrufe), abrufe


def test_3_mehrlaenderreise_nennt_simbabwe(monkeypatch):
    passolution_gemockt(monkeypatch)
    reply = website(_frage("Welche Einreisebestimmungen gelten? Wir sind Deutsche."), CAPRIVI)
    assert re.search(r"Simbabwe|Zimbabwe|\bZW\b", reply), reply
    assert "visum.de" not in reply, reply


def test_4_namibia_visum_erforderlich(monkeypatch):
    passolution_gemockt(monkeypatch)
    reply = website(_frage("Brauche ich als Deutscher ein Visum für Namibia?"), "/Afrika/Namibia")
    assert re.search(r"\bja\b|erforderlich|benötig|brauchst", reply, re.I), reply
    assert not re.search(r"\bkein(en)? Visum\b", reply, re.I), reply
    assert "visum.de" not in reply, reply


def test_5a_kunde_mit_tripurl_ohne_rueckfrage(monkeypatch):
    passolution_gemockt(monkeypatch)
    monkeypatch.setattr(passolution, "_tripurl_daten", lambda tid: TRIPURL_DATEN, raising=False)
    buchung_einrichten(monkeypatch, [EINREISE], tripurl=TRIPURL)
    reply = call(_frage("Brauche ich ein Visum für meine Reise?"), KUNDEN_ENDPOINT, kunden_id=KUNDEN_ID)
    assert not NACH_NATIONALITAET.search(reply), reply
    assert re.search(r"Schweiz|\bCH\b", reply), reply
    assert "visum.de" not in reply, reply


def test_5_kunde_namibia_mit_einreise_pdf_ohne_rueckfrage(monkeypatch):
    passolution_gemockt(monkeypatch)
    buchung_einrichten(monkeypatch, [EINREISE, AUSFUELLHILFE])
    reply = call(_frage("Brauche ich ein Visum für Namibia?"), KUNDEN_ENDPOINT, kunden_id=KUNDEN_ID)
    assert not NACH_NATIONALITAET.search(reply), reply
    assert AUSFUELLHILFE["link"] in reply, reply
    assert "visum.de" not in reply, reply


def test_6_ausfall_verweist_auf_reiseseite(monkeypatch):
    passolution_gemockt(monkeypatch, ausfall=True)
    reply = website(_frage("Brauche ich als Deutscher ein Visum?"), KOLUMBIEN)
    assert re.search(r"San-Agustin|Reiseseite|Erlebnisberat", reply), reply
    assert "visum.de" not in reply, reply
    assert not re.search(r"Fehler", reply), reply


# Ein Antrag als Hauptweg: „beantrage … e-Visum“, „über das Portal … beantragen“.
# Passolution nennt E-Visum und Visum bei Ankunft; das darf dastehen, aber
# nicht vor dem Gruppenvisum als das, was der Gast tun soll.
ANTRAG = re.compile(r"beantrag|\be-?visum|eservices", re.IGNORECASE)


def _gruppenvisum_zuerst(reply: str) -> None:
    gruppe = re.search(r"Gruppenvisum", reply, re.IGNORECASE)
    assert gruppe, reply
    # „ab 5 Gäste“, „sofern mindestens 5 Gäste teilnehmen“, „ab fünf Reisenden“.
    assert re.search(r"\b(5|fünf)\s+(Gäst|Gaest|Teilnehm|Reisend|Person)", reply, re.IGNORECASE), reply
    antrag = ANTRAG.search(reply)
    assert not antrag or antrag.start() > gruppe.start(), reply


def test_7_petra_gruppenvisum_neben_passolution(monkeypatch):
    passolution_gemockt(monkeypatch)
    reply = website(_frage("Brauche ich ein Visum für Jordanien? Wir sind Deutsche."), PETRA)
    _gruppenvisum_zuerst(reply)
    assert not NACH_NATIONALITAET.search(reply), reply
    assert "visum.de" not in reply, reply


def test_8_nofretete_hilfe_in_kairo_und_regeln(monkeypatch):
    passolution_gemockt(monkeypatch)
    reply = website(_frage("Brauche ich ein Visum für Ägypten? Wir sind Deutsche."), NOFRETETE)
    # Die Hilfe vor Ort, nicht nur Passolutions „Visum bei Ankunft“.
    assert re.search(r"Partneragentur|Mitarbeiter|Team|behilflich|in Empfang|empfängt|hilft", reply, re.I), reply
    assert re.search(r"Passolution", reply), reply
    assert not NACH_NATIONALITAET.search(reply), reply
    assert "visum.de" not in reply, reply


def test_9_kunde_jopet_gruppenvisum_aus_den_unterlagen(monkeypatch):
    passolution_gemockt(monkeypatch)
    monkeypatch.setattr(
        passolution, "_tripurl_daten",
        lambda tid: {"destinations": ["JO"], "nationalities": ["DE"]}, raising=False,
    )
    buchung_einrichten(
        monkeypatch, [REISEUNTERLAGEN_JOPET], tripurl=TRIPURL,
        reise_code="JOPET", titel="Jordanien – Petra",
    )
    reply = call(_frage("Brauche ich ein Visum für Jordanien?"), KUNDEN_ENDPOINT, kunden_id=KUNDEN_ID)
    _gruppenvisum_zuerst(reply)
    assert not NACH_NATIONALITAET.search(reply), reply
    assert "visum.de" not in reply, reply
