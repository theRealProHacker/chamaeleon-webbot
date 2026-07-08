"""AI evaluation for the Agenturbereich knowledge base (faqs/agentur.md).

Mirrors the old keyword-based FAQ eval: pose a real question with the agentur
KB injected (is_agentur=True), let Leon answer via the live model, and assert
the answer contains the keywords / links the reviewer feedback demands. Plain
keywords match case-insensitively; regex-looking keywords (URLs are re.escape'd)
match as regex.

NEVER part of the default suite — every case is a live Gemini call. Run manually:

    RUN_AGENTUR_EVAL=1 pytest tests/test_agentur_faq.py -v

Needs GEMINI_API_KEY (.env). LLM output varies; a lone failure is worth one
re-run before believing it.

The cases encode the triage of the 31 review comments in
"Chatbot Leon für den Agenturbereich - Feedback.docx", judged against the
CURRENT faqs/agentur.md:

  DONE    — the KB now carries what the feedback asked for; the case must PASS
            (the eval checks Leon actually surfaces it). Covers every
            answer-checkable comment: the link/wording fixes (C1, C3, C4/C5/C6,
            C9, C11/C12, C16/C17, C19, C29) and the content-owner texts for
            C13, C23, C24.
  COMPLEX — C18 works via the website tool (no KB change) and C25 is applied
            per the doc; see the note by COMPLEX below. Nothing outstanding.

Source-only feedback (KB wording/formatting, no answer-keyword to check):
C0 'die helfen dir' → 'Sie'; C15 'nicht' fett (already **nicht** in the KB);
C21 Just4You wording; C26 remove 'und Vertrieb'; C30 shorten Rückvergütung;
phone format '030 …' vs '+49 30 …'.
"""

import os
import re

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

from agent import call

RUN = os.getenv("RUN_AGENTUR_EVAL") == "1"

pytestmark = pytest.mark.skipif(
    not RUN, reason="live agentur FAQ eval - set RUN_AGENTUR_EVAL=1 to run"
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

# (id, question, [keywords]) — feedback the current KB already satisfies.
DONE = [
    ("kataloge-partner", "Über welche Partner werden die Kataloge der Herzen je Land versendet?",
     ["infox", "schöngrundner", "flühmann",
      L("touristikwelt.infox.de"), L("schoengrundner.at"), L("mailinghouse.ch")]),
    ("provisionsabrechnung", "Wo finde ich meine Provisionsabrechnung?",
     [L("agt.chamaeleon-reisen.de/Agentur/Buchungen")]),
    ("lightbox-video", "Wie kann ich die Lightbox aufbauen?",
     [L("owncloud.chamaeleon-reisen.de/index.php/s/yjRIK3970KSTNfe")]),
    ("paxlounge-video", "Wie übertrage ich ein Angebot von der Website in die Paxlounge?",
     [L("youtube.com/watch?v=9MK1fcVyXIQ")]),
    ("bosys-video", "Wie kann ich ein Angebot zu BOSYS UI.Office übernehmen?",
     [L("youtube.com/watch?v=VDauXaw1A0U")]),
    ("registrierung", "Wie kann ich mich als neue Agentur registrieren?",
     [L("agt.chamaeleon-reisen.de/Agentur/AG-Neuanmeldung")]),
    ("facebook-gruppe", "Gibt es eine Facebook-Gruppe für Reiseprofis?",
     [L("facebook.com/groups/chamaeleon.insider")]),
    ("expi-50-termine", "Wo finde ich Reisetermine mit 50 % Expi-Ermäßigung?",
     [L("agt.chamaeleon-reisen.de/Agentur/Expi-Reisen")]),
    ("kurzfristige-abreisen", "Wo finde ich Termine für kurzfristige Abreisen?",
     [L("chamaeleon-reisen.de/Kurzfristige-Abreisen")]),
    ("just4you", "Welche Reisen kann ich als Just4You buchen?",
     [L("agt.chamaeleon-reisen.de/Agentur/Just4You")]),
    ("vertriebsteam-mailto", "Wie erreiche ich das Vertriebsteam?",
     [L("mailto:agentur@chamaeleon-reisen.de")]),
    # --- promoted from SIMPLE once the fix landed in agentur.md ---
    ("verkaufsunterstuetzung-link", "Wo finde ich Material zur Verkaufsunterstützung?",
     [L("agt.chamaeleon-reisen.de/Agentur/Verkaufsunterstuetzung")]),  # C1
    ("logo-downloadbereich", "Wo finde ich das Chamäleon-Logo?",
     [L("agt.chamaeleon-reisen.de/Agentur/Downloads")]),  # C5/C4/C6
    ("livestream-aufzeichnung", "Kann man den LiveStream später noch anschauen?",
     [L("agt.chamaeleon-reisen.de/Agentur/LiveStream")]),  # C19
    ("paxlounge-wording", "Wie übertrage ich ein Angebot in die Paxlounge?",
     ["video-tutorial"]),  # C16/C17: 'Erklärungsvideo' → 'Video-Tutorial'
    ("bildpaket-allgemein", "Wo finde ich Bilder zu einer bestimmten Reise?",
     ["allgemein"]),  # C3: ein allgemeines Bild-Paket, nicht pro Reise
    ("social-media-links", "Wo finde ich Chamäleon auf Instagram und Facebook?",
     [L("instagram.com/chamaeleon"), L("facebook.com/Chamaeleon")]),  # C29
    # --- resolved COMPLEX items (text supplied by the content owner) ---
    ("website-einbindung", "Wie kann ich Chamäleon-Reisen auf meiner Website einbinden?",
     ["Partnerlink", "JSON", L("agt.chamaeleon-reisen.de/Agentur/Verkaufsunterstuetzung")]),  # C23
    ("kundenabend", "Kann ich einen Kundenabend mit Chamäleon machen?",
     [L("mailto:agentur@chamaeleon-reisen.de")]),  # C24
]

# Former COMPLEX items, now resolved:
#   C13/C23/C24 — content owner supplied the wording; applied to agentur.md and
#                 covered by the paxlounge / website-einbindung / kundenabend
#                 DONE cases above.
#   C18 — confirmed working, no KB change: the website tool fetches the trip
#         page on www and returns its termine + Erlebnisberater.
#   C25 — NatureBottles reworded per the doc (drop leading "Nein",
#         "unseren gemeinsamen Gästen"); confirm against the screenshot.
COMPLEX = []


def _params(cases):
    return [pytest.param(q, kw, id=cid) for cid, q, kw in cases]


@pytest.mark.parametrize("question,keywords", _params(DONE))
def test_agentur_answer(question, keywords):
    """Ask Leon with the agentur KB injected; assert the feedback keywords appear."""
    reply = call([{"role": "user", "content": question}], "/Agentur", is_agentur=True)
    missing = [k for k in keywords if not keyword_matches(k, reply)]
    assert not missing, f"missing {missing}\n--- reply ---\n{reply}"
