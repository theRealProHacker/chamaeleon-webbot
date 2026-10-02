"""AI evaluation for the MeinChamäleon customer-mode Q&A (Kunden-Modus).

Mirrors test_agentur_faq.py: pose one of the official "Mein Chamäleon" support
questions with Kunden-Modus active (a kunden_id plus an endpoint carrying a
VRRVORGANG booking number), let Leon answer via the live model, and assert the
reply actually contains the correct MeinChamäleon deep link — not merely the
name of the section in quotes. The whole point of these cases is that the bot
HANDS OVER a clickable URL wherever one applies.

NEVER part of the default suite — every case is a live Gemini call. Run manually:

    RUN_MEINCHAMAELEON_EVAL=1 pytest tests/test_meinchamaeleon_faq.py -v

Needs GEMINI_API_KEY (.env). LLM output varies; a lone failure is worth one
re-run before believing it.

The links come straight from the Kunden-Modus prompt block
(agent_base.format_system_prompt, is_kunde=True): the four trip links carry the
current page's VRRVORGANG booking number, the static links (Übersicht, Meine
Daten) do not. What each case actually pins down is the MAPPING — which section
a given question resolves to — because that mapping is the behaviour under test:

  Flugplan, Visumausfüllhilfe, Reiseunterlagen, Rail&Fly-Codes,
  Rechnung/Zahlungslink         -> Reiseunterlagen  (#unterlagen)
  Passdaten                     -> Gäste            (#gaeste)
  Unterkünfte & Reiseverlauf    -> Reiseunterlagen  (#unterlagen; einen
                                   #reiseverlauf-Bereich hat die Reiseseite nicht)
  E-Mail / Login prüfen         -> Meine Daten       (/MeinChamaeleon/Daten)
  Clubstufe                     -> Übersicht         (/MeinChamaeleon)
  Gutschein einlösen            -> mailto:erlebnisberatung@chamaeleon-reisen.de

Seit die Detailansicht des buchungen_tool die Dokumente der Buchung mit Links
listet (Design unterlagen-tripurl-leon2, 2026-09-27), SOLL Leon Dokumentfragen
über das Tool beantworten — und dabei den #unterlagen-Link trotzdem mitgeben.
Deshalb hat die erfundene kunden_id hier eine erfundene Buchung (Fixture
``_erfundene_buchung``): mit „unbekannt“ als Tool-Antwort prüfte der Fall nur
noch die Störungsmeldung, nicht mehr die Zuordnung.

Koffergröße (QA 11) has no applicable MeinChamäleon URL — it points at the
airline's own rules — so it gets its own content-only case at the bottom.
"""

import os
import re

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import kundendaten
from agent import call

RUN = os.getenv("RUN_MEINCHAMAELEON_EVAL") == "1"

pytestmark = pytest.mark.skipif(
    not RUN, reason="live MeinChamäleon FAQ eval - set RUN_MEINCHAMAELEON_EVAL=1 to run"
)


def _is_regex(pattern: str) -> bool:
    return bool(re.search(r"[\[\](){}.*+?^$|\\]", pattern))


def keyword_matches(keyword: str, text: str) -> bool:
    """Case-insensitive match; regex-looking keywords are treated as regex."""
    if _is_regex(keyword):
        try:
            return bool(re.search(keyword, text, re.IGNORECASE))
        except re.error:
            return keyword.lower() in text.lower()
    return keyword.lower() in text.lower()


L = re.escape  # link/URL keywords: match the substring literally

# Kunden-Modus context. The endpoint carries a VRRVORGANG so the four trip links
# get prefilled with the booking number (see format_system_prompt); the fake
# kunden_id only flips the mode on and binds the flights tool by closure — it is
# not a real customer, so a stray flights-tool call comes back "unbekannt".
BN = "9988776655"
ENDPOINT = f"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG={BN}"
KUNDEN_ID = "TEST_KUNDE_EVAL"

# The exact URLs the prompt block hands the model for this booking number.
_TRIP = f"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG={BN}"
UNTERLAGEN = _TRIP + "#unterlagen"
GAESTE = _TRIP + "#gaeste"
DATEN = "https://www.chamaeleon-reisen.de/MeinChamaeleon/Daten"
GUTSCHEIN_MAIL = "mailto:erlebnisberatung@chamaeleon-reisen.de"
# /MeinChamaeleon NOT followed by a deeper path — i.e. the "Übersicht" link, not
# /MeinChamaeleon/Daten or /MeinChamaeleon/Reise. Regex so keyword_matches runs
# it as a pattern; a trailing slash is tolerated.
UEBERSICHT = r"chamaeleon-reisen\.de/MeinChamaeleon/?(?![-\w])"

_DOK = "https://unterlagen.chamaeleon-reisen.de/eval/"


@pytest.fixture(autouse=True)
def _erfundene_buchung(monkeypatch):
    """KUNDEN_ID hat genau die Buchung BN: kommend, mit Dokumenten wie echt
    (Rechnung, Flugplan, Ausfüllhilfe, Reiseunterlagen). Andere IDs (der echte
    Testkunde 999999999) laufen unverändert gegen TourOne."""
    hop1, hop2 = kundendaten._buchungen_roh, kundendaten._buchung_roh
    eingebettet = {"vorgang": BN, "vonDat": "2099-05-01 00:00:00",
                   "bisDat": "2099-05-15 00:00:00", "reiseCode": "NAWDH"}
    buchung = dict(
        eingebettet, status="OK", beschreibungen=[{"titel": "Namibia-Reise"}],
        persAdult=2, flugdaten=[], tripurl="https://travel-details.eu/de?tid=EVAL-EVAL-EVAL",
        unterlagen=[
            {"id": str(i), "name": name, "beschreibung": "", "link": _DOK + f"{i}.pdf"}
            for i, name in enumerate(
                ["Rechnung.pdf", "Reisebestätigung.pdf", "Flugplan.pdf",
                 "Visum Ausfüllhilfen.pdf", "Reiseunterlagen.pdf",
                 "Teilnehmerdaten.pdf"], start=1)
        ],
    )
    monkeypatch.setattr(kundendaten, "_buchungen_roh",
                        lambda kid: [eingebettet] if kid == KUNDEN_ID else hop1(kid))
    monkeypatch.setattr(kundendaten, "_buchung_roh",
                        lambda v: buchung if v == BN else hop2(v))
    monkeypatch.setattr(kundendaten, "buchungsstatus",
                        lambda v: "OK" if v == BN else hop2(v).get("status", ""))


def _dok_oder_bereich(nr: int) -> str:
    """Seit der Dokumentliste ist der direkte PDF-Link das Soll (Design: nie NUR
    der Bereich); der #unterlagen-Link bleibt als gleichwertige Antwort."""
    return f"(?:{L(UNTERLAGEN)}|{L(_DOK + f'{nr}.pdf')})"


# (id, question, [required url keywords]) — the answer must surface these links.
URL_CASES = [
    ("flugplan",
     "Wo finde ich den Flugplan?",
     [_dok_oder_bereich(3)]),
    ("visumausfuellhilfe",
     "Wo finde ich die Visumausfüllhilfe?",
     [_dok_oder_bereich(4)]),
    ("login-email",
     "Meine E-Mail-Adresse ist hinterlegt, aber ich kann mich nicht einloggen.",
     [L(DATEN)]),
    ("passdaten",
     "Wo kann ich meine Passdaten einpflegen?",
     [L(GAESTE)]),
    ("reiseunterlagen",
     "Wo finde ich unsere Reiseunterlagen?",
     [_dok_oder_bereich(5)]),
    ("rail-and-fly",
     "Wie buche ich Rail&Fly und wo finde ich die Codes?",
     [_dok_oder_bereich(5)]),
    ("clubstufe",
     "Wo sehe ich meine Clubstufe?",
     [UEBERSICHT]),
    ("gutschein",
     "Wo finde ich meinen Gutschein?",
     [L(GUTSCHEIN_MAIL)]),
    ("zahlungslink",
     "Wo finde ich den Zahlungslink für die Kreditkarte?",
     [_dok_oder_bereich(1)]),
    ("reiseverlauf",
     "Wo finde ich die Unterkünfte und den Reiseverlauf?",
     [L(UNTERLAGEN)]),
]


def _params(cases):
    return [pytest.param(q, kw, id=cid) for cid, q, kw in cases]


@pytest.mark.parametrize("question,keywords", _params(URL_CASES))
def test_meinchamaeleon_provides_url(question, keywords):
    """Ask Leon in Kunden-Modus; assert the correct MeinChamäleon link appears."""
    reply = call(
        [{"role": "user", "content": question}], ENDPOINT, kunden_id=KUNDEN_ID
    )
    missing = [k for k in keywords if not keyword_matches(k, reply)]
    assert not missing, f"missing {missing}\n--- reply ---\n{reply}"


def test_koffergroesse_verweist_auf_die_fluggesellschaft():
    """QA 11: no MeinChamäleon page answers Koffergröße — the reply must point at
    the airline's own rules and must NOT invent a trip deep link for it."""
    reply = call(
        [{"role": "user", "content": "Wie groß darf mein Koffer sein?"}],
        ENDPOINT,
        kunden_id=KUNDEN_ID,
    )
    assert keyword_matches("Fluggesellschaft", reply) or keyword_matches(
        "Airline", reply
    ), f"expected a pointer to the airline's rules\n--- reply ---\n{reply}"
    assert f"VRRVORGANG={BN}#" not in reply, (
        f"fabricated a MeinChamäleon deep link where none applies\n"
        f"--- reply ---\n{reply}"
    )


# --- Punkt 14: der Unterlagen-Link, am href geprueft ----------------------
#
# Befund (Owner, 2026-09-20): Leon gibt teilweise nur /MeinChamaeleon aus statt
# des ganzen Links zu den Reiseunterlagen. Zwei Prompt-Regeln ziehen
# gegeneinander ("verwende einfach die relativen URLs" gegen "verwende
# ausschliesslich die hier genannten Links"), und die vier Reise-Links gibt es
# heute nur, wenn die aktuelle URL einen VRRVORGANG traegt — auf der
# Uebersichtsseite hat Leon also gar keinen Unterlagen-Link.
#
# Geprueft wird am href und nicht am Prompt: ob der Link als Text danebensteht,
# hilft dem Kunden nicht, und genau das war der Fehler. Beide Faelle brauchen
# weder Login noch echte Buchung — mit VRRVORGANG reicht eine ausgedachte
# Nummer in der Endpoint-URL, ohne laeuft der Fall als Testkunde 999999999
# (wie in tests/test_kundendaten.py), dessen Buchungen das Tool liefert.
#
# Der Testkunde hat genau eine Buchung: 2025, vergangen, storniert (gemessen
# 2026-09-24). Es gibt also keine Reise, deren Unterlagen Leon verlinken
# koennte — die Uebersicht ist dort die richtige Antwort. Frueher war dieser
# Fall "gruen", weil Leon sich eine eigene VRRVORGANG-URL baute; genau das
# prueft er jetzt als Fehler.

TESTKUNDE = "999999999"
UEBERSICHT_ENDPOINT = "https://www.chamaeleon-reisen.de/MeinChamaeleon"

_HREF = re.compile(r'href="([^"]+)"')


def meinchamaeleon_hrefs(reply: str) -> list[str]:
    """Die MeinChamäleon-Links der Antwort, so wie der Kunde sie anklickt."""
    return [ziel for ziel in _HREF.findall(reply) if "MeinChamaeleon" in ziel]


def test_reiseunterlagen_link_ist_vollstaendig():
    """Punkt 14: der Link zu den Reiseunterlagen, nie der nackte Übersichtslink."""
    reply = call(
        [{"role": "user", "content": "Wo finde ich meine Reiseunterlagen?"}],
        ENDPOINT,
        kunden_id=KUNDEN_ID,
    )
    links = meinchamaeleon_hrefs(reply)
    assert links, f"gar kein MeinChamäleon-Link\n--- reply ---\n{reply}"
    assert any("#unterlagen" in ziel for ziel in links), (
        f"kein Link auf die Reiseunterlagen, nur {links}\n--- reply ---\n{reply}"
    )


def test_ohne_offene_reise_nur_die_uebersicht():
    """Punkt 14, Kehrseite: ohne offene Reise gibt es keinen Unterlagen-Link.

    Der Testkunde hat nur eine vergangene, stornierte Buchung. Leon muss auf
    die Übersicht verweisen und darf keine VRRVORGANG-URL selbst bauen.
    """
    reply = call(
        [{"role": "user", "content": "Wo finde ich meine Reiseunterlagen?"}],
        UEBERSICHT_ENDPOINT,
        kunden_id=TESTKUNDE,
    )
    links = meinchamaeleon_hrefs(reply)
    assert any(ziel.rstrip("/").endswith("/MeinChamaeleon") for ziel in links), (
        f"kein Link auf die Übersicht, nur {links}\n--- reply ---\n{reply}"
    )
    assert "VRRVORGANG" not in reply, (
        f"selbst gebaute Reise-URL\n--- reply ---\n{reply}"
    )
