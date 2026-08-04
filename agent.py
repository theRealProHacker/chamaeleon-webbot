import re
import time

import mistune
from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from agent_base import (
    GEMINI_API_KEY,
    chamaeleon_website_tool_base,
    country_faq_tool_base,
    country_faq_tool_description,
    detect_recommendation_links,
    format_system_prompt,
    laender_faqs,
    termine_tool_base,
    termine_tool_description,
    visa_tool_base,
    visa_tool_description,
    website_tool_description,
)
from agenturdaten import make_buchungen_agentur_tool
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
def chamaeleon_website_tool(url_path: str) -> str:
    """LangChain tool wrapper for the base website tool."""
    return chamaeleon_website_tool_base(url_path)


@tool(description=country_faq_tool_description)
def country_faq_tool(country: str) -> str:
    """LangChain tool wrapper for the country FAQ tool."""
    return country_faq_tool_base(country)


@tool(description=termine_tool_description)
def termine_tool(
    url_path: str,
    jahr: int | None = None,
    monat: int | None = None,
    nur_freie: bool = False,
) -> str:
    """LangChain tool wrapper for the termine tool."""
    return termine_tool_base(url_path, jahr, monat, nur_freie)


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
_genderstern_pattern = re.compile(r"(?<=\w)\*(?=\w)")
_html_tag_pattern = re.compile(r"(<[^>]*>)")


def escape_genderstern(text: str) -> str:
    """Escape Genderstern asterisks outside HTML tags before Markdown rendering."""
    parts = _html_tag_pattern.split(text)
    for i in range(0, len(parts), 2):  # even indices are text outside tags
        parts[i] = _genderstern_pattern.sub(r"\\*", parts[i])
    return "".join(parts)


# Ein sauber beendeter Modellzug meldet STOP — auch der, der ein Tool aufruft.
# Alles andere ist ein Vorfall: MALFORMED_FUNCTION_CALL (Gemini wollte ein Tool
# aufrufen und hat ungültiges JSON erzeugt), SAFETY/RECITATION/
# PROHIBITED_CONTENT (Antwort verworfen) oder MAX_TOKENS (Budget aufgebraucht).
# "stop"/"end_turn" stehen vorsorglich drin, falls hier je ein anderer Anbieter
# gebunden wird; Gemini liefert ausschließlich Großschreibung.
_NORMALE_FINISH_REASONS = {"STOP", "stop", "end_turn"}

_MAX_VERSUCHE = 3

# Deckel für die Vorfall-Zeilen eines Versuchs. Ein Modell, das in einer
# MALFORMED_FUNCTION_CALL-Schleife hängt, erzeugt bis zum Rekursionslimit von
# create_react_agent (25) ein gutes Dutzend auffälliger Nachrichten; ohne Deckel
# wären das Dutzende Zeilen aus einer einzigen Anfrage.
_MAX_VORFALL_ZEILEN = 5

# Ein neuer Versuch startet nur, solange der Turn insgesamt darunter liegt. Das
# Widget bricht nach 30 s ab und bekommt bis zum finalen response-Event kein
# einziges Byte, die 30 s sind also eine harte Frist für den ganzen Turn. Der
# gemessene Leer-Bug ist schnell (Median 0,74 s), ein langsamer Lauf ist ein
# anderer Fehler — den zu wiederholen hieße, den Abbruch zu provozieren.
_RETRY_ZEITBUDGET_S = 6.0

_LEERE_ANTWORT_FALLBACK = (
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
    )

    # Convert messages to LangChain format
    chat_history = [
        SystemMessage(content=system_prompt)
    ] + convert_messages_to_langchain(messages)

    # Initialize recommendation containers
    recommendations = set[str]()

    # Create agent with tools. Ohne kunden_id bleibt die Tool-Liste identisch
    # zu heute (Sicherheitsinvariante); das Flug-Tool existiert nur für den
    # eingeloggten Kunden und ist per Closure an genau seine ID gebunden.
    tools = [
        visa_tool,
        chamaeleon_website_tool,
        country_faq_tool,
        termine_tool,
    ]
    if kunden_id:
        tools.append(make_buchungen_tool(kunden_id))
    # Analog für die Agentur: das Tool existiert nur bei verifizierter Bindung
    # und ist per Closure an genau diese Agenturnummer gebunden. is_agentur
    # allein reicht nicht — das ist nur ein Header-Spiegel.
    if agentur_id:
        tools.append(make_buchungen_agentur_tool(agentur_id))
    agent_executor = create_react_agent(model, tools=tools)
    # Nur die NAMEN der gebundenen Tools — sie erklären den Verdacht (im
    # Kunden-/Agentur-Modus ist ein Tool mehr gebunden), enthalten aber keine
    # Kundendaten.
    gebundene_tools = [getattr(t, "name", str(t)) for t in tools]

    try:
        # Gemini liefert gelegentlich eine leere Antwort: gemessen 26 von 1343
        # Assistant-Turns (1,9 %, 12 von 600 Gesprächen). Ungeprüft rendert das
        # Widget daraus eine leere Blase, speichert sie als Verlauf und schickt
        # sie beim nächsten Mal als History mit — das Modell hält die alte Frage
        # dann für unbeantwortet und beantwortet SIE statt der neuen, die
        # Antworten laufen also um eine Frage versetzt weiter.
        #
        # Ein weiterer Versuch lohnt nur für GENAU die gemessene Signatur: ein
        # schneller Einzelzug, der sauber mit STOP endet und trotzdem nichts
        # sagt (Median 0,74 s). Zwei Wächter grenzen das ab, beide unten an der
        # Schleife:
        #   * auffälliger finish_reason → nicht wiederholen. SAFETY, RECITATION,
        #     PROHIBITED_CONTENT und MAX_TOKENS kommen bei identischem Input
        #     identisch zurück (temperature=0.1); ein zweiter Lauf kostet nur
        #     Tokens und, weil der Graph komplett neu läuft, jeden Tool-Abruf
        #     ein weiteres Mal.
        #   * Zeitbudget → nicht wiederholen, wenn der Turn schon länger läuft.
        #     Echte Antworten brauchen bis zu 18 s; drei solche Läufe rissen den
        #     30-Sekunden-Abbruch des Widgets, und der Kunde sähe statt der
        #     leeren Blase gar nichts mehr.
        # Eine gute Antwort kostet weiterhin genau einen Modelllauf.
        start = time.monotonic()
        for versuch in range(1, _MAX_VERSUCHE + 1):
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
                                    if tc.get("name", "") in gebundene_tools
                                    else "<unbekannt>"
                                    for tc in (
                                        getattr(message, "tool_calls", None) or []
                                    )
                                ]
                                print(
                                    f"[agent] auffälliger finish_reason={grund!r} "
                                    f"versuch={versuch}/{_MAX_VERSUCHE} "
                                    f"tool_calls={namen} "
                                    f"tools_gebunden={gebundene_tools} "
                                    f"nachrichten={len(event['messages'])} "
                                    f"usage={getattr(message, 'usage_metadata', None)}"
                                )

                        # Check for tool calls in AI messages
                        if hasattr(message, "tool_calls") and message.tool_calls:
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

            if reply.strip():
                break

            # WARUM die Antwort leer war, steht in den Metadaten der Nachricht —
            # und war bisher weg, sobald der Turn durch war. finish_reason trennt
            # die Fälle sauber: MALFORMED_FUNCTION_CALL (Gemini wollte ein Tool
            # aufrufen und hat ungültiges JSON erzeugt — passt zum Kunden-Modus,
            # wo ein Tool mehr gebunden ist), SAFETY/RECITATION/PROHIBITED_CONTENT
            # (Antwort verworfen, ohne Text) oder MAX_TOKENS (Denk-Tokens haben
            # das Budget aufgebraucht). Feuert nur im Fehlerfall, also ~26 Zeilen
            # auf 600 Gespräche.
            grund_letzte = auffaelliger_finish_reason(letzte)
            verstrichen = time.monotonic() - start
            print(
                f"[agent] leere Modellantwort, Versuch {versuch}/{_MAX_VERSUCHE} "
                f"finish_reason={(getattr(letzte, 'response_metadata', None) or {}).get('finish_reason')!r} "
                f"tool_calls={len(getattr(letzte, 'tool_calls', None) or [])} "
                f"nach={verstrichen:.1f}s "
                f"usage={getattr(letzte, 'usage_metadata', None)}"
            )

            # Die beiden Wächter aus dem Kommentar oben: ein deterministischer
            # Abbruchgrund kommt identisch zurück, und ein langer Turn hat kein
            # Budget mehr für einen weiteren Lauf.
            if grund_letzte or verstrichen > _RETRY_ZEITBUDGET_S:
                break

        if not reply.strip():
            # Lieber ein ehrlicher Satz als eine leere Blase: der Kunde sieht,
            # dass etwas schiefging, und die Antwort landet nicht als leerer
            # Turn im Verlauf, der die nächste Frage verschieben würde.
            reply = _LEERE_ANTWORT_FALLBACK

        # Extract recommendations
        recommendations.update(detect_recommendation_links(reply))

        # Genderstern (z.B. "Berater*innen") nicht als Markdown-Kursiv rendern
        reply = escape_genderstern(reply)

        reply = mistune.markdown(
            reply, escape=False
        )  # Convert markdown to HTML if needed

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
