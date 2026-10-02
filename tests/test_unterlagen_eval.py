"""Verhaltens-Eval: Leon mit den Dokumenten einer Buchung (Design
unterlagen-tripurl-leon2, T12).

Acht Fälle aus dem Design, jeder ein Live-Aufruf von Gemini im Kunden-Modus.
Die Buchung ist erfunden (``/get/buchung`` gemockt), die PDFs sind die
synthetischen Fixtures aus tests/fixtures/unterlagen/ — kein echter Kunde, kein
Download. Der Testkunde 999999999 taugt dafür nicht (storniert, drei Dokumente,
altes Template, keine TripURL).

NIE Teil der Standard-Suite. Manuell:

    RUN_UNTERLAGEN_EVAL=1 pytest tests/test_unterlagen_eval.py -q -s

Geprüft wird Verhalten (was in der Antwort steht), nie der Prompt. Ein einzelner
roter Fall ist erst einmal einzeln zu wiederholen (siehe test_agentur_faq.py
zur leeren Gemini-Antwort in langen Läufen).
"""

import datetime
import os
import re

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import kundendaten
import unterlagen
from agent import call

RUN = os.getenv("RUN_UNTERLAGEN_EVAL") == "1"

pytestmark = pytest.mark.skipif(
    not RUN, reason="live Unterlagen-Eval - RUN_UNTERLAGEN_EVAL=1 setzen"
)

KUNDEN_ID = "TEST_KUNDE_UNTERLAGEN"
BN = "9900001"
ENDPOINT = f"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG={BN}"
_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "unterlagen")
_HOST = "https://unterlagen.chamaeleon-reisen.de/eval/"
TRIPURL = "https://travel-details.eu/de?tid=EVAL-TEST-0000"

REISEUNTERLAGEN = {"id": "15", "name": "Reiseunterlagen.pdf", "link": _HOST + "reiseunterlagen_neu.pdf"}
REISEBESTAETIGUNG = {"id": "11", "name": "Reisebestätigung.pdf", "link": _HOST + "reisebestaetigung_alt.pdf"}
AUSFUELLHILFE = {"id": "13", "name": "Visum Ausfüllhilfen.pdf", "link": _HOST + "visum_ausfuellhilfen.pdf"}
EINREISE = {"id": "12", "name": "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf",
            "link": _HOST + "einreisebestimmungen.pdf"}
RECHNUNG = {"id": "10", "name": "Rechnung.pdf", "link": _HOST + "rechnung.pdf"}
TEILNEHMER = {"id": "14", "name": "Teilnehmerdaten.pdf", "link": _HOST + "teilnehmerdaten.pdf"}

ALLE = [RECHNUNG, REISEBESTAETIGUNG, EINREISE, AUSFUELLHILFE, TEILNEHMER, REISEUNTERLAGEN]
OHNE_ULAS = [RECHNUNG, REISEBESTAETIGUNG, EINREISE, AUSFUELLHILFE, TEILNEHMER]


def _datum(tage: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=tage)).strftime("%Y-%m-%d 00:00:00")


@pytest.fixture
def buchung(monkeypatch):
    """``buchung(unterlagen, abreise_in_tagen)`` richtet die erfundene Buchung ein
    und gibt die Liste der geladenen Links zurück."""
    geladen: list[str] = []

    def einrichten(eintraege, abreise_in_tagen=40):
        eingebettet = {"vorgang": BN, "vonDat": _datum(abreise_in_tagen),
                       "bisDat": _datum(abreise_in_tagen + 12), "reiseCode": "NAWDH"}
        roh = dict(
            eingebettet, status="OK", beschreibungen=[{"titel": "Namibia – Zauber der Weite"}],
            persAdult=2, flugdaten=[], tripurl=TRIPURL,
            unterlagen=[dict(e, beschreibung="") for e in eintraege],
        )
        monkeypatch.setattr(kundendaten, "_buchungen_roh", lambda kid: [eingebettet])
        monkeypatch.setattr(kundendaten, "_buchung_roh", lambda v: roh)
        monkeypatch.setattr(kundendaten, "buchungsstatus", lambda v: "OK")

        def laden(link):
            geladen.append(link)
            with open(os.path.join(_FIXTURES, link.rsplit("/", 1)[1]), "rb") as f:
                return f.read()

        monkeypatch.setattr(unterlagen, "_laden", laden)
        return geladen

    return einrichten


def frage(text: str) -> str:
    return call([{"role": "user", "content": text}], ENDPOINT, kunden_id=KUNDEN_ID)


def test_ausfuellhilfe_vor_visum_de(buchung):
    buchung(ALLE)
    reply = frage("Wie beantrage ich das Visum für Namibia?")
    assert AUSFUELLHILFE["link"] in reply, reply
    if "visum.de" in reply:
        assert reply.index(AUSFUELLHILFE["link"]) < reply.index("visum.de"), reply


def test_14_tage_ohne_unterlagen_keine_frist(buchung):
    buchung(OHNE_ULAS, abreise_in_tagen=10)
    reply = frage("Wann bekomme ich meine Reiseunterlagen?")
    assert not re.search(r"(zwei|2)\s*Wochen", reply, re.I), reply
    assert re.search(r"Erlebnisberat|tel:|\+49", reply), reply


def test_ohne_ulas_kein_falsches_da(buchung):
    buchung(OHNE_ULAS, abreise_in_tagen=45)
    reply = frage("Sind meine Reiseunterlagen schon da?")
    assert re.search(r"noch nicht", reply, re.I), reply
    assert REISEUNTERLAGEN["link"] not in reply


def test_hotel_an_tag_3(buchung):
    buchung(ALLE)
    reply = frage("In welcher Unterkunft übernachten wir an Tag 3?")
    assert "Dünenblick" in reply, reply


def test_kontakt_vor_ort(buchung):
    buchung(ALLE)
    reply = frage("Wen kann ich vor Ort im Notfall erreichen?")
    assert re.search(r"\+264|0170-0000000|\+49-\(0\)170", reply), reply


def test_passnummer_nur_als_bereichslink(buchung):
    geladen = buchung(ALLE)
    reply = frage("Welche Passnummer hat meine Mitreisende in den Teilnehmerdaten?")
    assert TEILNEHMER["link"] not in geladen
    assert TEILNEHMER["link"] not in reply
    assert "#unterlagen" in reply or "#gaeste" in reply, reply


def test_nicht_im_dokument_wird_nicht_geraten(buchung):
    buchung(ALLE)
    reply = frage("Hat die Dünenblick Lodge WLAN auf den Zimmern?")
    assert re.search(r"Erlebnisberat", reply), reply
    assert not re.search(r"\b(ja|gibt es)\b[^.]*WLAN", reply, re.I), reply


def test_einreise_mit_stand_datum(buchung):
    """Einreisebestimmungen der Buchung sind 6 bis 12 Monate alt: zitiert Leon
    daraus, dann mit Stand-Datum, und immer mit einer aktuellen Quelle."""
    buchung(ALLE)
    reply = frage("Welche Einreisebestimmungen gelten laut meinen Unterlagen für Namibia?")
    assert TRIPURL in reply or "visum.de" in reply, reply
    if re.search(r"Reiseanmeldung|bei Buchung|laut (deinen|den) Unterlagen", reply, re.I):
        assert "03.02.2026" in reply, reply


def test_alle_unterkuenfte_aus_dem_reiseverlauf(buchung):
    """Chat 02.10.2026: „nicht in den Reiseunterlagen aufgeführt“, obwohl jeder
    Tag sein Hotel nennt."""
    buchung(ALLE)
    reply = frage(
        "Stelle mir eine komplette Übersicht aller Unterkünfte entlang des "
        "Reiseverlaufs mit sämtlichen Kontaktdaten"
    )
    assert "Dünenblick" in reply and "Seebrise" in reply, reply
    assert not re.search(r"nicht (in den Reiseunterlagen )?aufgeführt", reply), reply


def test_vorausbuchung_ablauf_aus_der_reiseseite(buchung, monkeypatch):
    """Chat 02.10.2026, Buchung 228121: nur eine Vormerkung. Die Textbausteine
    hat Gemini als „deinen Reiseverlauf“ ausgegeben. Erwartet: der geplante
    Ablauf von der echten Reiseseite (Live-Abruf der Website)."""
    import travel_index

    buchung([{"id": "20", "name": "Vormerkung.pdf", "link": _HOST + "vormerkung.pdf"}])
    monkeypatch.setattr(
        travel_index, "get_url_for_code", lambda code: "/Amerika/Mexiko-Guatemala-Belize/Palenque"
    )
    reply = frage("Ablauf der reise")
    assert re.search(r"Palenque|Cancún|Cancun|Tulum|Mérida|Merida|Tikal", reply), reply
    assert not re.search(r"Checkliste hilft", reply), reply


def test_reiseverlauf_verlinkt_das_pdf(buchung):
    """03.10.2026: „hier ist dein Reiseverlauf: Reiseverlauf“ mit einem Link
    auf einen MeinChamäleon-Bereich, den es nicht gibt. Erwartet: der Link
    auf das PDF der Reiseunterlagen."""
    buchung(ALLE)
    reply = frage("Zeig mir meinen Reiseverlauf")
    assert REISEUNTERLAGEN["link"] in reply, reply
    assert "#reiseverlauf" not in reply, reply


def test_wie_fliege_ich_verlinkt_das_pdf(buchung):
    """03.10.2026: „findest du in deinen Reiseunterlagen unter
    Reiseunterlagen“, nur als Text. Erwartet: Fluginfos aus den Unterlagen
    und ihr PDF als Link; die Reiseunterlagen nie nur beim Namen."""
    buchung(ALLE)
    reply = frage("wie fliege ich?")
    assert REISEUNTERLAGEN["link"] in reply, reply
    ohne_links = re.sub(r"<a [^>]*>.*?</a>", "", reply, flags=re.S)
    assert "Reiseunterlagen" not in ohne_links, reply


def test_gepaeck_auf_der_startseite_aus_den_unterlagen(buchung):
    """03.10.2026: auf der Startseite ging „Wie viel Gepäck darf ich
    mitnehmen?“ 3 von 4 Mal an die Koffer-FAQ, auf der Reiseseite nie.
    Eingeloggt mit offener Reise meint die Frage diese Reise."""
    buchung(ALLE)
    reply = call(
        [{"role": "user", "content": "Wie viel Gepäck darf ich mitnehmen?"}],
        "https://www.chamaeleon-reisen.de/", kunden_id=KUNDEN_ID,
    )
    assert "23 kg" in reply, reply
