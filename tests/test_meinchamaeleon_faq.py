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
  Unterkünfte & Reiseverlauf    -> Reiseverlauf     (#reiseverlauf)
  E-Mail / Login prüfen         -> Meine Daten       (/MeinChamaeleon/Daten)
  Clubstufe                     -> Übersicht         (/MeinChamaeleon)
  Gutschein einlösen            -> mailto:erlebnisberatung@chamaeleon-reisen.de

If the model answers a document-location question by calling the flights tool
instead of linking the right section, that is a real miss these cases catch: the
fake test kunden_id then yields the "unbekannt" text and the URL assertion fails.

Koffergröße (QA 11) has no applicable MeinChamäleon URL — it points at the
airline's own rules — so it gets its own content-only case at the bottom.
"""

import os
import re

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

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
REISEVERLAUF = _TRIP + "#reiseverlauf"
DATEN = "https://www.chamaeleon-reisen.de/MeinChamaeleon/Daten"
GUTSCHEIN_MAIL = "mailto:erlebnisberatung@chamaeleon-reisen.de"
# /MeinChamaeleon NOT followed by a deeper path — i.e. the "Übersicht" link, not
# /MeinChamaeleon/Daten or /MeinChamaeleon/Reise. Regex so keyword_matches runs
# it as a pattern; a trailing slash is tolerated.
UEBERSICHT = r"chamaeleon-reisen\.de/MeinChamaeleon/?(?![-\w])"

# (id, question, [required url keywords]) — the answer must surface these links.
URL_CASES = [
    ("flugplan",
     "Wo finde ich den Flugplan?",
     [L(UNTERLAGEN)]),
    ("visumausfuellhilfe",
     "Wo finde ich die Visumausfüllhilfe?",
     [L(UNTERLAGEN)]),
    ("login-email",
     "Meine E-Mail-Adresse ist hinterlegt, aber ich kann mich nicht einloggen.",
     [L(DATEN)]),
    ("passdaten",
     "Wo kann ich meine Passdaten einpflegen?",
     [L(GAESTE)]),
    ("reiseunterlagen",
     "Wo finde ich unsere Reiseunterlagen?",
     [L(UNTERLAGEN)]),
    ("rail-and-fly",
     "Wie buche ich Rail&Fly und wo finde ich die Codes?",
     [L(UNTERLAGEN)]),
    ("clubstufe",
     "Wo sehe ich meine Clubstufe?",
     [UEBERSICHT]),
    ("gutschein",
     "Wo finde ich meinen Gutschein?",
     [L(GUTSCHEIN_MAIL)]),
    ("zahlungslink",
     "Wo finde ich den Zahlungslink für die Kreditkarte?",
     [L(UNTERLAGEN)]),
    ("reiseverlauf",
     "Wo finde ich die Unterkünfte und den Reiseverlauf?",
     [L(REISEVERLAUF)]),
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
