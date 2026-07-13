import re

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
    visa_tool_base,
    visa_tool_description,
    website_tool_description,
)
from kundendaten import make_fluege_tool

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


def call_stream(
    messages: list,
    endpoint: str,
    kundenberater_name: str = "",
    kundenberater_telefon: str = "",
    is_agentur: bool = False,
    page_content: str = "",
    kunden_id: str = "",
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
    ]
    if kunden_id:
        tools.append(make_fluege_tool(kunden_id))
    agent_executor = create_react_agent(model, tools=tools)

    try:
        # Stream the agent execution
        events = []
        for event in agent_executor.stream(
            {"messages": chat_history}, stream_mode="values"
        ):
            events.append(event)

            # Check if there are new messages with tool calls
            if "messages" in event:
                messages = event["messages"]
                for message in messages:
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

        # Get the final response
        response = events[-1]

        # Extract reply from response
        reply = response["messages"][-1].content

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
    ):
        if event["type"] == "response":
            return event["data"]["reply"]
        elif event["type"] == "error":
            raise event["error"]

    raise RuntimeError("No response received from the agent.")
