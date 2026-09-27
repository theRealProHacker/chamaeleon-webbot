import re
import time

import gevent
import mistune
from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from agent_base import (
    GEMINI_API_KEY,
    _vrrvorgang_from_url,
    all_sites,
    country_faq_tool_base,
    country_faq_tool_description,
    detect_recommendation_links,
    format_system_prompt,
    laender_faqs,
    reise_info_tools_description,
    reiseinfo_tool_base,
    berater_tool_base,
    berater_tool_description,
    termine_tool_base,
    termine_tool_description,
    visa_tool_base,
    visa_tool_description,
    website_tool_description,
    website_tool_multi,
)
from agenturdaten import make_buchungen_agentur_tool
import kundendaten
from kundendaten import make_buchungen_tool

# Initialize the model
model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", google_api_key=GEMINI_API_KEY, temperature=0.1
)


@tool(description=visa_tool_description)
def visa_tool(country: str) -> str:
    """LangChain tool wrapper for the visa tool."""
    return visa_tool_base(country)


@tool(description=website_tool_description)
def chamaeleon_website_tool(
    url_paths: list[str], abschnitt: str = "", orte: list[str] | None = None
) -> str:
    """LangChain tool wrapper for the base website tool."""
    return website_tool_multi(url_paths, abschnitt, orte)


@tool(description=country_faq_tool_description)
def country_faq_tool(country: str) -> str:
    """LangChain tool wrapper for the country FAQ tool."""
    return country_faq_tool_base(country)


@tool(description=berater_tool_description)
def erlebnisberater_tool(url_path: str) -> str:
    """LangChain tool wrapper for the berater tool."""
    return berater_tool_base(url_path)


@tool(description=termine_tool_description)
def termine_tool(
    url_path: str,
    jahr: int | None = None,
    monat: int | None = None,
    nur_freie: bool = False,
) -> str:
    """LangChain tool wrapper for the termine tool."""
    return termine_tool_base(url_path, jahr, monat, nur_freie)


def make_reiseinfo_tool(
    seiten_vorgang: str = "", kunden_id: str = "", agentur_id: str = ""
):
    """Build the per-request Reiseinfo tool, bound to this requester by closure.

    Which booking is meant comes from the request, not from the model: the open
    trip page (VRRVORGANG) or, failing that, the requester's next trip. The model
    may still name another booking, but only one that belongs to this customer
    or agency — the number is checked against their own bookings, never trusted.

    It never has to look a number up either: that second call is exactly where it
    used to narrate „ich schaue gleich nach“ and end the turn instead of
    answering.
    """

    # Bewusst lose getypt: ein Wert, den die Validierung ablehnt (gemessen
    # 2026-09-27: quelle="dokumente", abschnitt=None), kommt beim Modell als
    # Tool-Fehler an, und es sagt dem Gast „da ist ein Fehler aufgetreten“.
    # Normalisiert wird in reiseinfo_tool_base.
    @tool(description=reise_info_tools_description)
    def reiseinfo_tool(
        vorgangsnummer: str | None = "",
        quelle: str | None = "auto",
        abschnitt: str | None = "",
        tag: int | None = 0,
    ) -> str:
        """LangChain tool wrapper for the Reiseinfo tool."""
        return reiseinfo_tool_base(
            vorgangsnummer or "", seiten_vorgang, kunden_id, agentur_id,
            quelle=quelle or "auto", abschnitt=abschnitt or "", tag=tag or 0,
        )

    return reiseinfo_tool


def convert_messages_to_langchain(messages: list) -> list:
    """Convert generic message format to LangChain message objects."""
    chat_history = []
    for msg in messages:
        if msg["role"] == "user":
            chat_history.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            chat_history.append(AIMessage(content=msg["content"]))
    return chat_history


# Single '*' between two word characters is the German Genderstern (e.g.
# "Berater*innen"), not Markdown emphasis. Escape it so mistune doesn't render
# the text between two such stars as italics, but only outside HTML tags so
# that '*' inside e.g. an href stays untouched.
#
# Als HTML-Entity, nicht als "\*" (2026-09-25): schreibt Gemini selbst HTML
# ("<p>…</p>"), reicht mistune den Block unveraendert durch, und der Backslash
# stand sichtbar im Chat ("Mitarbeiter\*innen"). &#42; zeigt der Browser in
# beiden Faellen als Stern. Ein Backslash, den das Modell schon selbst
# davorgesetzt hat, faellt dabei mit weg.
_genderstern_pattern = re.compile(r"(?<=\w)\\?\*(?=\w)")
_html_tag_pattern = re.compile(r"(<[^>]*>)")


def escape_genderstern(text: str) -> str:
    """Escape Genderstern asterisks outside HTML tags before Markdown rendering."""
    parts = _html_tag_pattern.split(text)
    for i in range(0, len(parts), 2):  # even indices are text outside tags
        parts[i] = _genderstern_pattern.sub("&#42;", parts[i])
    return "".join(parts)


# Gemini schreibt Attribute gelegentlich JSON-escaped: <a href=\"…\">
# (gemessen 2026-09-25, 1 von 5 Laeufen im Koffer-Fall). Das ist kein gueltiges
# HTML, mistune maskiert den Tag, und der Kunde sieht "<a href=…>" als Text statt
# des Links. Repariert wird nur innerhalb von Tags.
def entferne_escapte_anfuehrungszeichen(text: str) -> str:
    """``\\"`` und ``\\'`` in HTML-Tags zu schlichten Anführungszeichen."""
    teile = _html_tag_pattern.split(text)
    for i in range(1, len(teile), 2):  # ungerade Indizes sind Tags
        teile[i] = teile[i].replace('\\"', '"').replace("\\'", "'")
    return "".join(teile)


# Der Prompt sagt "Verwende einfach die relativen URLs, z.B. "/Impressum"" — mal
# setzt Gemini daraus einen Link, mal steht der Pfad nackt im Satz
# ("… findest du unter /Afrika/Uganda/Gorilla."), und dann war er nicht
# anklickbar (Owner, 2026-09-25). Verlinkt wird nur ein Pfad, den es auf der
# Website gibt (all_sites), nur ausserhalb von Tags und von bestehenden <a>,
# und ohne das Satzzeichen dahinter: der Pfad-Zeichensatz kennt keinen Punkt.
# Geprueft wird gegen all_sites selbst, nicht gegen eine Kopie: der naechtliche
# Sitemap-Sync tauscht die Liste in place aus.
_nackter_pfad = re.compile(
    r"(?<![\w/\\.:=\"'#-])(/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)(#[A-Za-z0-9_-]+)?(?![\w/])"
)


def verlinke_nackte_pfade(html: str) -> str:
    """Bekannte Website-Pfade im Fliesstext als Link setzen; alles andere bleibt."""
    teile = _html_tag_pattern.split(html)
    in_link = 0
    for i, teil in enumerate(teile):
        if i % 2:  # Tag
            if re.match(r"<a[\s>]", teil, re.I):
                in_link += 1
            elif re.match(r"</a\s*>", teil, re.I):
                in_link = max(0, in_link - 1)
            continue
        if in_link:
            continue

        def _link(m: re.Match) -> str:
            pfad, anker = m.group(1), m.group(2) or ""
            if pfad == "/" or pfad not in all_sites:
                return m.group(0)
            return f'<a href="{pfad}{anker}" target="_blank">{pfad}{anker}</a>'

        teile[i] = _nackter_pfad.sub(_link, teil)
    return "".join(teile)


_NORMALE_FINISH_REASONS = {"STOP", "stop", "end_turn"}

# Gemini kuendigt gelegentlich an, nachzusehen, und beendet dann den Zug ohne
# Tool-Aufruf: "Super, Namibia ist eine fantastische Wahl! Ich schaue mal,
# welche Reisen für euch passen." (gemessen 2026-09-25, trotz Prompt-Regel 1
# von 5 Laeufen; schon 2026-08-10 beim Reiseinfo-Tool). Der Kunde wartet dann
# auf etwas, das nie kommt. Erkannt wird nur die Ankuendigung OHNE Tool-Aufruf
# im selben Zug; dann gibt es genau einen Anstoss.
_ANKUENDIGUNG = re.compile(
    r"\b(?:ich schaue|schaue ich|ich sehe (?:mal |kurz |gleich )?nach"
    r"|sehe ich (?:mal |kurz |gleich )?nach|ich prüfe|prüfe ich|ich suche"
    r"|suche ich|einen (?:kleinen |kurzen )?moment)\b",
    re.IGNORECASE,
)

_ANSTOSS = (
    "(Hinweis an dich: Du hast angekündigt nachzusehen, aber kein Tool aufgerufen. "
    "Ruf jetzt das passende Tool auf und antworte direkt mit dem Ergebnis, ohne Ankündigung.)"
)


# Nur der letzte Satz zaehlt, und nur ohne Frage und ohne Bedingung: "Nenn mir
# deine Buchungsnummer, dann prüfe ich das" und "Hast du einen Moment Zeit?"
# sind gute Rueckfragen, "Ich suche dir gern etwas raus: Da passen …" hat schon
# das Ergebnis (Review 2026-09-26). Die ersetzte ein Anstoss sonst.
_BEDINGUNG = re.compile(r"\b(?:dann|sobald|wenn|falls)\b", re.IGNORECASE)


def kuendigt_nur_an(reply: str) -> bool:
    """Endet die Antwort mit der Ankündigung, nachzusehen? Nur sinnvoll, wenn kein Tool lief."""
    text = re.sub(r"<[^>]+>", " ", reply).strip()
    if "?" in text:
        return False
    saetze = [s for s in re.split(r"(?<=[.!…:])\s+", text) if s.strip()]
    letzter = saetze[-1] if saetze else ""
    return bool(_ANKUENDIGUNG.search(letzter)) and not _BEDINGUNG.search(letzter)

_MAX_VERSUCHE = 3

_MAX_VORFALL_ZEILEN = 5

_RETRY_ZEITBUDGET_S = 6.0

EMPTY_ANSWER_FALLBACK = (
    "Entschuldige, da ist mir gerade keine Antwort gelungen. "
    "Stell mir die Frage gerne noch einmal."
)


def auffaelliger_finish_reason(message) -> str:
    """Return ``message``'s finish_reason if it is anything but a normal stop.

    Empty string when the message carries no finish_reason at all (System-,
    Human- und Tool-Nachrichten) or when the step ended normally.
    """
    metadata = getattr(message, "response_metadata", None) or {}
    grund = metadata.get("finish_reason") or ""
    if not isinstance(grund, str):
        grund = str(grund)
    return "" if grund in _NORMALE_FINISH_REASONS else grund


def text_aus_content(content) -> str:
    """Den Text aus ``content`` ziehen, egal ob String oder Blockliste.

    LangChain typisiert ``AIMessage.content`` als ``str | list[str | dict]``;
    Gemini liefert die Liste, sobald Thinking- oder Multimodal-Blöcke im Spiel
    sind. Ohne diese Normalisierung gilt eine vollständig richtige Antwort als
    leer, wird dreimal wiederholt und am Ende durch den Entschuldigungssatz
    ersetzt — der Kunde bekäme ein Scheitern gemeldet, während die Antwort
    danebenliegt.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        teile = []
        for block in content:
            if isinstance(block, str):
                teile.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                teile.append(block["text"])
        return "".join(teile)
    return ""


# Gesamtwartezeit für den Buchungsabruf beim Prompt-Bau. Der requests-timeout
# aus kundendaten.TIMEOUT (8 s) gilt je Socket-Schritt, nicht für die
# Gesamtdauer — genau deshalb steht hier eine zweite, harte Schranke.
REISE_TIMEOUT_S = 1.0


def reise_fuer_links(endpoint: str, kunden_id: str) -> tuple[str, str]:
    """Welche Reise die MeinChamäleon-Links meinen: ``(vorgang, label)``.

    Rangfolge:
      1. formal gültiges VRRVORGANG in der URL → diese Nummer, OHNE API-Abruf.
         Das ist der Stand von heute und funktioniert auch bei TourOne-Ausfall;
         wer auf seiner Reiseseite steht, meint diese Reise.
      2. sonst die nächste offene Reise aus den (gecachten) Buchungen.
      3. sonst leer → im Prompt bleiben nur die Übersichts-Links.

    Der Abruf darf den Chat nie aufhalten: höchstens ``REISE_TIMEOUT_S``, dann
    geht es ohne Reise-Links weiter. Beim Login hat /kunde/auth den Cache in der
    Regel längst gefüllt (siehe app.py), der Normalfall kostet also nichts.
    """
    if not kunden_id:
        return "", ""
    seiten_vorgang = _vrrvorgang_from_url(endpoint)
    if seiten_vorgang:
        return seiten_vorgang, ""

    # Eigener Greenlet statt Timeout um den Aufruf (Review 2026-09-24): der
    # Chat wartet hoechstens REISE_TIMEOUT_S, der Abruf laeuft aber zu Ende und
    # fuellt die Caches. Mit einem Timeout um den Aufruf wuerde er abgebrochen,
    # nichts landete im Cache, und bei einem TourOne ueber 1 s zahlte JEDE
    # Nachricht die volle Sekunde aufs Neue.
    auflosung = gevent.spawn(_naechste_reise, kunden_id)
    try:
        return auflosung.get(timeout=REISE_TIMEOUT_S)
    except gevent.Timeout:
        return "", ""


def _naechste_reise(kunden_id: str) -> tuple[str, str]:
    """Hop 1 (gecacht), dann die erste offene, nicht stornierte Reise.

    Scheitert ein Abruf — auch die Statuspruefung —, bleiben nur die
    Uebersichts-Links, nie ein Link auf eine womoeglich tote Buchung. Gefangen
    wird HIER und nicht beim Warten: die Exception eines Greenlets schreibt
    gevent sonst samt Traceback ins Log, und die requests-Exception traegt die
    volle Request-URL und damit die Kundennummer.
    """
    try:
        buchungen = kundendaten._buchungen_roh(kunden_id)
        if not buchungen:
            return "", ""
        return kundendaten.naechste_offene_reise(
            buchungen,
            kundendaten.heute_berlin(),
            storniert=lambda v: kundendaten.ist_storniert(kundendaten.buchungsstatus(v)),
        )
    except Exception as e:
        print(
            f"[agent] buchungen für die Reise-Links nicht abrufbar: "
            f"{type(e).__name__}"
        )
        return "", ""


def call_stream(
    messages: list,
    endpoint: str,
    kundenberater_name: str = "",
    kundenberater_telefon: str = "",
    is_agentur: bool = False,
    page_content: str = "",
    kunden_id: str = "",
    agentur_id: str = "",
):
    """
    Streaming version of the call function that yields events during processing.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        endpoint: Current website endpoint the user is on
        kundenberater_name: Name of the customer advisor for this trip/page
        kundenberater_telefon: Phone number of the customer advisor for this trip/page
        is_agentur: Whether the request comes from the Reisebüro/agency area
        page_content: Widget-scraped content of the current (agentur) page,
            already markdownified and capped by markdownify_page_html
        kunden_id: Validated ID of the logged-in MeinChamäleon customer
            (already through parse_kunden_id); "" outside Kunden-Modus
        agentur_id: Agenturnummer from the server-side verified binding;
            "" unless the agency is authenticated. is_agentur alone is only a
            header mirror and never unlocks booking data.

    Yields:
        dict: Events with 'type' and 'data' keys
    """
    # Detect countries
    detected_countries: list[str] = []
    for country in laender_faqs:
        if any(country in msg["content"] for msg in messages):
            detected_countries.append(country)

    # Welche Reise die MeinChamäleon-Links meinen, wird HIER entschieden, nicht
    # im Prompt-Bau und nicht vom Modell: format_system_prompt bekommt zwei
    # fertige Strings und bleibt damit eine reine Textfunktion ohne Netz.
    reise_vorgang, reise_label = reise_fuer_links(endpoint, kunden_id)

    # Format system prompt with current time and endpoint
    system_prompt = format_system_prompt(
        endpoint,
        detected_countries,
        kundenberater_name,
        kundenberater_telefon,
        is_agentur,
        page_content,
        is_kunde=bool(kunden_id),
        has_agentur_daten=bool(agentur_id),
        reise_vorgang=reise_vorgang,
        reise_label=reise_label,
    )

    # Convert messages to LangChain format
    chat_history = [
        SystemMessage(content=system_prompt)
    ] + convert_messages_to_langchain(messages)

    # Initialize recommendation containers
    recommendations = set[str]()

    tools = [
        visa_tool,
        chamaeleon_website_tool,
        country_faq_tool,
        termine_tool,
        erlebnisberater_tool,
    ]
    if kunden_id:
        tools.append(make_buchungen_tool(kunden_id))
    if agentur_id:
        tools.append(make_buchungen_agentur_tool(agentur_id))
    # Die Reiseinfos hängen an einer Buchung. Ohne Kunden- oder Agenturbindung
    # gibt es keine — und ein anonymer Besucher könnte damit nur raten, zu
    # welchem Land eine fremde Buchungsnummer gehört.
    if kunden_id or agentur_id:
        tools.append(
            make_reiseinfo_tool(
                seiten_vorgang=_vrrvorgang_from_url(endpoint) if kunden_id else "",
                kunden_id=kunden_id,
                agentur_id=agentur_id,
            )
        )
    agent_executor = create_react_agent(model, tools=tools)
    
    tool_names = [getattr(t, "name", str(t)) for t in tools]

    try:
        start = time.monotonic()
        angestossen = False
        for versuch in range(1, _MAX_VERSUCHE + 1):
            tool_aufgerufen = False
            # Nur das LETZTE Event wird gebraucht (die Endantwort). Eine Liste
            # aller Events hielte bei stream_mode="values" jeden Zwischenstand
            # inklusive der vollen Tool-Ergebnisse bis zum Turn-Ende am Leben.
            letztes_event = None
            # Ein kaputter Tool-Aufruf ist auch dann das Signal, das wir suchen,
            # wenn der Graph sich danach fängt: das Modell korrigiert sich im
            # nächsten Schritt, der Kunde bekommt eine gute Antwort — und im
            # Leer-Pfad unten sähen wir davon nie etwas. Deshalb wird JEDE
            # Nachricht geprüft, nicht nur die letzte. stream_mode="values"
            # liefert bei jedem Event die GESAMTE Historie erneut (siehe
            # kundendaten.filter_new_tool_calls), deshalb der Set: sonst stünde
            # ein Vorfall einmal pro Folge-Event im Log. Der Set lebt pro
            # Versuch, damit ein zweiter Lauf seine eigenen Vorfälle meldet.
            gemeldete_vorfaelle: set = set()
            for event in agent_executor.stream(
                {"messages": chat_history}, stream_mode="values"
            ):
                letztes_event = event

                # Check if there are new messages with tool calls
                if "messages" in event:
                    for message in event["messages"]:
                        # Auffälliger finish_reason — nur Metadaten ins Log:
                        # Toolnamen, Zähler, Token-Verbrauch. Nie Nachrichten-
                        # text, nie Tool-Argumente, nie eine Kundennummer.
                        grund = auffaelliger_finish_reason(message)
                        if grund and len(gemeldete_vorfaelle) < _MAX_VORFALL_ZEILEN:
                            schluessel = getattr(message, "id", None) or id(message)
                            if schluessel not in gemeldete_vorfaelle:
                                gemeldete_vorfaelle.add(schluessel)
                                # Der Toolname kommt roh aus der Modellantwort und
                                # ist NICHT gegen die deklarierten Tools geprüft
                                # (langchain_google_genai übernimmt ihn ungefiltert).
                                # Also nur durchlassen, was der Server selbst
                                # gebunden hat — sonst schreibt das Modell in
                                # unser Log.
                                namen = [
                                    tc.get("name", "")
                                    if tc.get("name", "") in tool_names
                                    else "<unbekannt>"
                                    for tc in (
                                        getattr(message, "tool_calls", None) or []
                                    )
                                ]
                                print(
                                    f"[agent] auffälliger finish_reason={grund!r} "
                                    f"versuch={versuch}/{_MAX_VERSUCHE} "
                                    f"tool_calls={namen} "
                                    f"tools_gebunden={tool_names} "
                                    f"nachrichten={len(event['messages'])} "
                                    f"usage={getattr(message, 'usage_metadata', None)}"
                                )

                        # Check for tool calls in AI messages
                        if hasattr(message, "tool_calls") and message.tool_calls:
                            tool_aufgerufen = True
                            for tool_call in message.tool_calls:
                                yield {
                                    "type": "tool_call",
                                    "data": {
                                        "name": tool_call["name"],
                                        "args": tool_call["args"],
                                        "id": tool_call.get("id", ""),
                                    },
                                }

                        # Check for tool responses
                        if hasattr(message, "content") and isinstance(
                            message.content, list
                        ):
                            for content_item in message.content:
                                if (
                                    isinstance(content_item, dict)
                                    and content_item.get("type") == "tool_result"
                                ):
                                    yield {
                                        "type": "tool_response",
                                        "data": {
                                            "tool_call_id": content_item.get(
                                                "tool_call_id", ""
                                            ),
                                            "content": content_item.get("content", ""),
                                        },
                                    }

            # Get the final response and extract the reply
            letzte = (
                letztes_event["messages"][-1]
                if letztes_event and letztes_event.get("messages")
                else None
            )
            reply = text_aus_content(getattr(letzte, "content", ""))

            if (
                reply.strip()
                and not tool_aufgerufen
                and not angestossen
                and versuch < _MAX_VERSUCHE
                and kuendigt_nur_an(reply)
                and time.monotonic() - start <= _RETRY_ZEITBUDGET_S
            ):
                print(f"[agent] Ankündigung ohne Tool-Aufruf, Anstoß (versuch={versuch})")
                chat_history = chat_history + [
                    AIMessage(content=reply),
                    HumanMessage(content=_ANSTOSS),
                ]
                angestossen = True
                continue

            if reply.strip():
                break

            grund_letzte = auffaelliger_finish_reason(letzte)
            verstrichen = time.monotonic() - start
            print(
                f"[agent] leere Modellantwort, Versuch {versuch}/{_MAX_VERSUCHE} "
                f"finish_reason={(getattr(letzte, 'response_metadata', None) or {}).get('finish_reason')!r} "
                f"tool_calls={len(getattr(letzte, 'tool_calls', None) or [])} "
                f"nach={verstrichen:.1f}s "
                f"usage={getattr(letzte, 'usage_metadata', None)}"
            )

            if grund_letzte or verstrichen > _RETRY_ZEITBUDGET_S:
                break

        # TODO: move this to the frontend
        if not reply.strip(): 
            reply = EMPTY_ANSWER_FALLBACK

        # Extract recommendations
        recommendations.update(detect_recommendation_links(reply))

        reply = entferne_escapte_anfuehrungszeichen(reply)

        # Genderstern (z.B. "Berater*innen") nicht als Markdown-Kursiv rendern
        reply = escape_genderstern(reply)

        reply = mistune.markdown(
            reply, escape=False
        )  # Convert markdown to HTML if needed

        reply = verlinke_nackte_pfade(reply)

        # Yield final response
        result = {"reply": reply, "recommendations": list(recommendations)}

        yield {"type": "response", "data": result}

    except Exception as e:
        print(f"Error in agent processing: {e}")
        yield {"type": "error", "data": str(e), "error": e}


def call(
    messages: list,
    endpoint: str,
    kundenberater_name: str = "",
    kundenberater_telefon: str = "",
    is_agentur: bool = False,
    page_content: str = "",
    kunden_id: str = "",
    agentur_id: str = "",
) -> str:
    """
    Main function to process messages and generate responses using LangChain/LangGraph.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        endpoint: Current website endpoint the user is on
        kundenberater_name: Name of the customer advisor for this trip/page
        kundenberater_telefon: Phone number of the customer advisor for this trip/page
        is_agentur: Whether the request comes from the Reisebüro/agency area
        page_content: Widget-scraped content of the current (agentur) page,
            already markdownified and capped by markdownify_page_html
        kunden_id: Validated ID of the logged-in MeinChamäleon customer
            (already through parse_kunden_id); "" outside Kunden-Modus
        agentur_id: Agenturnummer from the server-side verified binding;
            "" unless the agency is authenticated

    Returns:
        str: The reply rendered as HTML
    """
    for event in call_stream(
        messages,
        endpoint,
        kundenberater_name,
        kundenberater_telefon,
        is_agentur,
        page_content,
        kunden_id,
        agentur_id,
    ):
        if event["type"] == "response":
            return event["data"]["reply"]
        elif event["type"] == "error":
            raise event["error"]

    raise RuntimeError("No response received from the agent.")
