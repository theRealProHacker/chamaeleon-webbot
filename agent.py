import re
import time

import mistune
from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from agent_base import (
    GEMINI_API_KEY,
    _vrrvorgang_from_url,
    chamaeleon_website_tool_base,
    country_faq_tool_base,
    country_faq_tool_description,
    detect_recommendation_links,
    format_system_prompt,
    laender_faqs,
    reise_info_tools_description,
    reiseinfo_tool_base,
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

    @tool(description=reise_info_tools_description)
    def reiseinfo_tool(vorgangsnummer: str = "") -> str:
        """LangChain tool wrapper for the Reiseinfo tool."""
        return reiseinfo_tool_base(
            vorgangsnummer, seiten_vorgang, kunden_id, agentur_id
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
_genderstern_pattern = re.compile(r"(?<=\w)\*(?=\w)")
_html_tag_pattern = re.compile(r"(<[^>]*>)")


def escape_genderstern(text: str) -> str:
    """Escape Genderstern asterisks outside HTML tags before Markdown rendering."""
    parts = _html_tag_pattern.split(text)
    for i in range(0, len(parts), 2):  # even indices are text outside tags
        parts[i] = _genderstern_pattern.sub(r"\\*", parts[i])
    return "".join(parts)


_NORMALE_FINISH_REASONS = {"STOP", "stop", "end_turn"}

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

    tools = [
        visa_tool,
        chamaeleon_website_tool,
        country_faq_tool,
        termine_tool,
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
