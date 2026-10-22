"""Verhaltens-Eval: Einreise und Visum aus Passolution (Design
passolution-einreise, §7 Fälle 1–6 und 5a).

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
KUNDEN_ID = "TEST_KUNDE_EINREISE"
BN = "9900002"
KUNDEN_ENDPOINT = f"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG={BN}"
TRIPURL = "https://travel-details.eu/de?tid=TEST-TEST-0001"

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
    monkeypatch.setattr(passolution, "_reise_laender", lambda code: ["NA"], raising=False)
    monkeypatch.setattr(
        passolution, "laender_der_seite",
        lambda pfad: ["BW", "NA", "ZW"] if "Caprivi" in pfad else (["CO"] if "Kolumbien/" in pfad else []),
        raising=False,
    )
    return abrufe


def _datum(tage: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=tage)).strftime("%Y-%m-%d 00:00:00")


def buchung_einrichten(monkeypatch, eintraege: list, tripurl: str = "") -> None:
    """Erfundene, eigene Namibia-Buchung in 40 Tagen; PDFs aus den Fixtures."""
    eingebettet = {"vorgang": BN, "vonDat": _datum(40), "bisDat": _datum(52), "reiseCode": "NAWDH"}
    roh = dict(
        eingebettet, status="OK", beschreibungen=[{"titel": "Namibia – Zauber der Weite"}],
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
