"""AI evaluation for the Agenturbereich knowledge base (faqs/agentur.md).

Pose a real question with the agentur KB injected (is_agentur=True), let Leon
answer via the live model, and assert what the reviewer feedback demands —
both what the answer must contain and what it must never contain.

NEVER part of the default suite — every case is a live Gemini call. Run manually:

    RUN_AGENTUR_EVAL=1 pytest tests/test_agentur_faq.py -v

Needs GEMINI_API_KEY (.env). LLM output varies; a lone failure is worth one
re-run before believing it.

Gemessen 2026-09-18, drei volle Laeufe: unter den ~30 Aufrufen am Stueck
kippt Gemini reproduzierbar in die leere Antwort (finish_reason='STOP',
tool_calls=0, output_tokens=0). Die Retry-Kette in agent.py versucht es
dreimal und gibt dann "Entschuldige, da ist mir gerade keine Antwort
gelungen." aus. Es trifft wechselnde Faelle, am haeufigsten den teuersten
(zwei Zuege plus Tool-Aufruf): in der Suite 0 von 3, einzeln laufend 6 von 6.
Ein roter Fall heisst hier also zuerst "nochmal einzeln laufen lassen", und
erst wenn er dann auch faellt, ist die Wissensbasis schuld.

Die Faelle kommen aus zwei Feedbackrunden:

  Runde 1 ("Chatbot Leon fuer den Agenturbereich - Feedback.docx", 31
  Kommentare): die Link- und Wortlautkorrekturen. Stehen unten ohne
  darf_nicht-Liste und pruefen weiter, dass die Wissensbasis liefert, was
  damals gefordert war.

  Runde 2 ("Aktualisierungen 28.08.", 2026-09-18): das Kontakt-Routing. Leon
  schickte Flug-, Buchungsstatus- und Ansprechpartnerfragen an den Vertrieb.
  Leitfrage des Owners: geht es um die Reise selbst, dann Erlebnisberatung;
  geht es um das Drumherum, dann Vertrieb. Diese Faelle tragen eine
  darf_nicht-Liste, denn bei ihnen ist die falsche Adresse der Fehler.

Die Faelle laufen MIT page_content, weil die Produktion es immer mitschickt
(app.py:102) — ohne es fehlt der ganze Seiteninhalt-Block aus dem Prompt und
die Evals messen einen anderen Prompt als den ausgelieferten.
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


# --- Telefonnummern: auf Ziffern pruefen, nie auf Schreibweisen -------------
#
# Die Website schreibt dieselbe Durchwahl in drei Formen (gemessen ueber 25
# Reiseseiten: "+49 30 347996-901", "+49 30-347996-903", "030347996905"), und
# das Modell formatiert sie noch einmal um. Wer auf "347 996 290" prueft, sieht
# einen Fehler gruen, weil er als "347996-290" dastand.

_NUMMER = re.compile(r"\d[\d\s\-/()]{6,}\d")

VERTRIEB = "347996290"
EMPFANG = "3479960"
_STAMM = "347996"


def telefonnummern(text: str) -> set[str]:
    """Alle Telefonnummern im Text, normalisiert auf die reine Rufnummer.

    Laender- und Ortsvorwahl fallen weg: jede Nummer in dieser Wissensbasis ist
    eine Berliner Chamaeleon-Nummer, und das Modell schreibt sie mal mit "+49
    30", mal mit "030", mal ganz ohne. Verglichen wird, was uebrig bleibt.
    """
    gefunden = set()
    for treffer in _NUMMER.finditer(text):
        ziffern = re.sub(r"\D", "", treffer.group())
        ziffern = ziffern.removeprefix("0049").removeprefix("49").lstrip("0")
        gefunden.add(ziffern.removeprefix("30"))
    return gefunden


def nennt_vertrieb(text: str) -> bool:
    """Vertrieb genannt — als Nummer in beliebiger Schreibweise oder als Mail."""
    return VERTRIEB in telefonnummern(text) or "agentur@chamaeleon-reisen.de" in text


def nennt_empfang(text: str) -> bool:
    return EMPFANG in telefonnummern(text)


def nennt_durchwahl(text: str) -> bool:
    """Eine Erlebnisberater-Durchwahl: Stammnummer plus Nebenstelle.

    Nicht der Vertrieb (…290) und nicht der Empfang (…0). Der Personenname
    taugt nicht als Pruefung — Personal wechselt, die Nebenstellenlogik nicht.
    """
    for nummer in telefonnummern(text):
        if nummer.startswith(_STAMM) and nummer not in (VERTRIEB, EMPFANG):
            return True
    return False


# Repraesentativer Seiteninhalt, wie ihn das Widget im Agenturbereich
# mitschickt. Kurz gehalten: es geht darum, dass der Block ueberhaupt im
# Prompt steht, nicht um seinen Inhalt.
AGENTUR_SEITE = """# Buchungen & Dokumente

Hier findest du deine Buchungen, Optionen und Reisedokumente.

- Buchungsuebersicht
- Provisionsabrechnungen
- Reiseunterlagen
"""


def _frage(text, *, agentur_id=""):
    """Eine Frage an Leon, so wie die Produktion sie stellt."""
    return call(
        [{"role": "user", "content": text}],
        "/Agentur/Buchungen",
        is_agentur=True,
        page_content=AGENTUR_SEITE,
        agentur_id=agentur_id,
    )


# (id, frage, muss, darf_nicht)
DONE = [
    # --- Runde 1: Links und Wortlaut -------------------------------------
    ("kataloge-partner", "Über welche Partner werden die Kataloge der Herzen je Land versendet?",
     ["infox", "schöngrundner", "flühmann",
      L("touristikwelt.infox.de"), L("schoengrundner.at"), L("mailinghouse.ch")], []),
    ("provisionsabrechnung", "Wo finde ich meine Provisionsabrechnung?",
     [L("agt.chamaeleon-reisen.de/Agentur/Buchungen")], []),
    ("lightbox-video", "Wie kann ich die Lightbox aufbauen?",
     [L("owncloud.chamaeleon-reisen.de/index.php/s/yjRIK3970KSTNfe")], []),
    ("paxlounge-video", "Wie übertrage ich ein Angebot von der Website in die Paxlounge?",
     [L("youtube.com/watch?v=9MK1fcVyXIQ")], []),
    ("bosys-video", "Wie kann ich ein Angebot zu BOSYS UI.Office übernehmen?",
     [L("youtube.com/watch?v=VDauXaw1A0U")], []),
    ("registrierung", "Wie kann ich mich als neue Agentur registrieren?",
     [L("agt.chamaeleon-reisen.de/Agentur/AG-Neuanmeldung")], []),
    ("facebook-gruppe", "Gibt es eine Facebook-Gruppe für Reiseprofis?",
     [L("facebook.com/groups/chamaeleon.insider")], []),
    ("expi-50-termine", "Wo finde ich Reisetermine mit 50 % Expi-Ermäßigung?",
     [L("agt.chamaeleon-reisen.de/Agentur/Expi-Reisen")], []),
    ("kurzfristige-abreisen", "Wo finde ich Termine für kurzfristige Abreisen?",
     [L("chamaeleon-reisen.de/Kurzfristige-Abreisen")], []),
    ("just4you", "Welche Reisen kann ich als Just4You buchen?",
     [L("agt.chamaeleon-reisen.de/Agentur/Just4You")], []),
    ("vertriebsteam-mailto", "Wie erreiche ich das Vertriebsteam?",
     [L("mailto:agentur@chamaeleon-reisen.de")], []),
    ("verkaufsunterstuetzung-link", "Wo finde ich Material zur Verkaufsunterstützung?",
     [L("agt.chamaeleon-reisen.de/Agentur/Verkaufsunterstuetzung")], []),  # C1
    ("logo-downloadbereich", "Wo finde ich das Chamäleon-Logo?",
     [L("agt.chamaeleon-reisen.de/Agentur/Downloads")], []),  # C5/C4/C6
    ("livestream-aufzeichnung", "Kann man den LiveStream später noch anschauen?",
     [L("agt.chamaeleon-reisen.de/Agentur/LiveStream")], []),  # C19
    ("paxlounge-wording", "Wie übertrage ich ein Angebot in die Paxlounge?",
     ["video-tutorial"], []),  # C16/C17
    ("bildpaket-allgemein", "Wo finde ich Bilder zu einer bestimmten Reise?",
     ["allgemein"], []),  # C3
    ("social-media-links", "Wo finde ich Chamäleon auf Instagram und Facebook?",
     [L("instagram.com/chamaeleon"), L("facebook.com/Chamaeleon")], []),  # C29
    ("website-einbindung", "Wie kann ich Chamäleon-Reisen auf meiner Website einbinden?",
     ["Partnerlink", "JSON", L("agt.chamaeleon-reisen.de/Agentur/Verkaufsunterstuetzung")], []),  # C23
    # --- Runde 2: Kontakt-Routing (28.08.) --------------------------------
    # Echte Vertriebsthemen muessen weiter beim Vertrieb landen. Ohne diese
    # beiden Faelle misst die Suite nur eine Richtung, und eine Regel, die
    # ALLES zur Erlebnisberatung schickt, waere gruen.
    ("kundenabend", "Kann ich einen Kundenabend mit Chamäleon machen?",
     [L("mailto:agentur@chamaeleon-reisen.de")], []),  # C24
    ("just4you-provision", "Wie viel Provision bekomme ich bei Just4You?",
     [L("agt.chamaeleon-reisen.de/Agentur/Just4You")],
     [r"\d+\s*%", r"\d+\s*(€|EUR|Euro)"]),
    ("sonderreise-kontakt", "Wen kontaktiere ich zu einer Sonderreise?",
     [L("just4you@chamaeleon-reisen.de")], []),
    ("archivierte-unterlagen", "Wie komme ich an archivierte Unterlagen?",
     [L("buchhaltung@chamaeleon-reisen.de")], []),
]


def _params(cases):
    return [pytest.param(q, muss, nicht, id=cid) for cid, q, muss, nicht in cases]


@pytest.mark.parametrize("question,muss,darf_nicht", _params(DONE))
def test_agentur_answer(question, muss, darf_nicht):
    """Ask Leon with the agentur KB injected; assert both directions."""
    reply = _frage(question)
    fehlend = [k for k in muss if not keyword_matches(k, reply)]
    verboten = [k for k in darf_nicht if keyword_matches(k, reply)]
    assert not fehlend and not verboten, (
        f"fehlt {fehlend} / verboten {verboten}\n--- reply ---\n{reply}"
    )


# --- Routing: wer wird genannt --------------------------------------------


def test_provision_bleibt_beim_vertrieb():
    """Drumherum an einer konkreten Buchung gehoert trotzdem dem Vertrieb.

    Der Gegenfall zur neuen Regel: eine Provisionsfrage haengt an einer
    Buchung, ist aber ein Konditionsthema. Ginge sie zur Erlebnisberatung,
    haetten wir die Beschwerde nur umgedreht.
    """
    reply = _frage("Warum ist die Provision auf meiner Buchung niedriger als erwartet?")
    assert nennt_vertrieb(reply), f"Vertrieb nicht genannt:\n--- reply ---\n{reply}"


def test_fluganfrage_fragt_nach_der_reise():
    """Ohne bekannte Reise darf keine Nummer fallen — erst recht nicht die 290."""
    reply = _frage("Ich habe eine Rückfrage zu meiner Fluganfrage – an wen wende ich mich?")
    assert not nennt_vertrieb(reply), f"Vertrieb genannt:\n--- reply ---\n{reply}"


def test_fluganfrage_mit_reise_nennt_die_durchwahl():
    """Das Kernversprechen: Leon NENNT die Durchwahl, statt auf die Seite zu zeigen.

    Auf "Erlebnisberat" zu pruefen taugt nicht — das bestand die alte Antwort
    schon ("wende dich bitte an die Erlebnisberatung").
    """
    reply = call(
        [
            {"role": "user", "content": "Ich habe eine Rückfrage zu meiner Fluganfrage – an wen wende ich mich?"},
            {"role": "assistant", "content": "Um welche Reise geht es denn?"},
            {"role": "user", "content": "Um den Kaukasus."},
        ],
        "/Agentur/Buchungen",
        is_agentur=True,
        page_content=AGENTUR_SEITE,
    )
    assert nennt_durchwahl(reply), f"keine Durchwahl genannt:\n--- reply ---\n{reply}"
    assert not nennt_vertrieb(reply), f"Vertrieb genannt:\n--- reply ---\n{reply}"


def test_termine_nennt_die_durchwahl():
    """Beschluss 1A: §6 sagt nicht mehr "schau in der gelben Box nach"."""
    reply = call(
        [
            {"role": "user", "content": "Wann erscheinen die neuen Termine?"},
            {"role": "assistant", "content": "Um welche Reise geht es denn?"},
            {"role": "user", "content": "Um den Kaukasus."},
        ],
        "/Agentur/Buchungen",
        is_agentur=True,
        page_content=AGENTUR_SEITE,
    )
    assert nennt_durchwahl(reply), f"keine Durchwahl genannt:\n--- reply ---\n{reply}"


def test_ansprechpartner_fragt_zurueck():
    """Mehrdeutig, also Rueckfrage.

    Den Vertrieb fuer den allgemeinen Fall daneben zu nennen ist richtig und
    genau der beschlossene Weg (Reise → Erlebnisberatung, sonst Vertrieb).
    Falsch waere nur, eine Durchwahl zu nennen: die kann Leon nicht kennen,
    solange die Reise nicht feststeht.
    """
    reply = _frage("Wer ist mein Ansprechpartner?")
    assert "?" in reply, f"keine Rückfrage:\n--- reply ---\n{reply}"
    assert "reise" in reply.lower(), f"fragt nicht nach der Reise:\n--- reply ---\n{reply}"
    assert not nennt_durchwahl(reply), f"Durchwahl geraten:\n--- reply ---\n{reply}"


def test_gast_bucht_selbst_geht_nicht_zum_vertrieb():
    """§9 war der direkteste Fehlrouter der Datei."""
    reply = _frage("Was passiert, wenn mein Gast selbst eine Buchung anlegt?")
    assert not nennt_vertrieb(reply), f"Vertrieb genannt:\n--- reply ---\n{reply}"


def test_unbekannte_reise_erfindet_keine_durchwahl():
    """Der Halluzinationspfad: Reise genannt, aber es gibt sie nicht.

    Der Basis-Prompt fordert an anderer Stelle sogar auf, einen
    fehlgeschlagenen Abruf "geschickt zu umspielen" (agent_base.py:563) —
    genau hier darf das nicht in eine erfundene Nebenstelle kippen.
    """
    reply = call(
        [
            {"role": "user", "content": "Ich habe eine Fluganfrage zur Reise Traumland 3000 – an wen wende ich mich?"},
        ],
        "/Agentur/Buchungen",
        is_agentur=True,
        page_content=AGENTUR_SEITE,
    )
    assert not nennt_durchwahl(reply), f"Durchwahl erfunden:\n--- reply ---\n{reply}"


@pytest.mark.skipif(
    not os.getenv("AGENTUR_TEST_NUMMER"),
    reason="braucht eine echte Agenturnummer: AGENTUR_TEST_NUMMER=... setzen",
)
def test_buchungsstatus_mit_verifizierter_agentur():
    """Die Kollision, die sonst unsichtbar bleibt.

    Bei verifizierter Agentur sagt agent_base.py:824-828 "nutze
    buchungen_agentur_tool, sobald nach konkreten Buchungen, Reisenden,
    Terminen, Preisen oder der Provision zu einer Buchung gefragt wird" — die
    neue KB-Regel sagt fuer Buchungsstatus "frag nach der Reise und verweise
    an die Erlebnisberatung". Zwei Anweisungen, ein Fall. Hier muss das Tool
    gewinnen: die Agentur hat ihre eigenen Daten, dafuer braucht sie niemanden
    anzurufen.
    """
    reply = _frage(
        "Wie ist der Stand meiner Buchungen?",
        agentur_id=os.environ["AGENTUR_TEST_NUMMER"],
    )
    assert not nennt_vertrieb(reply), f"Vertrieb genannt:\n--- reply ---\n{reply}"


# --- Regression: Deko ist nie kostenfrei ----------------------------------

_FREE = re.compile(r"kostenfrei|kostenlos|gratis", re.IGNORECASE)


def _claims_free(text: str) -> bool:
    """True if the text asserts something is free WITHOUT negating it.

    Allows the correct "sind nicht kostenfrei" / "keine kostenlose ..." wording
    but flags a bare "... sind kostenfrei" — the exact Katharina Port bug.
    """
    for m in _FREE.finditer(text):
        window = text[max(0, m.start() - 20):m.start()].lower()
        if "nicht" in window or "kein" in window:
            continue
        return True
    return False


@pytest.mark.parametrize("question", [
    "wie teuer sind stoffbanner?",
    "wie teuer sind die leuchtdisplays?",
], ids=["stoffbanner", "leuchtdisplays"])
def test_agentur_deko_price_points_to_page(question):
    """Regression: Stoffbanner/Leuchtdisplays must never be advertised as free."""
    reply = _frage(question)
    assert not _claims_free(reply), f"Leon advertised the deko as free:\n--- reply ---\n{reply}"
    assert "verkaufsunterst" in reply.lower(), f"no page hint:\n--- reply ---\n{reply}"
