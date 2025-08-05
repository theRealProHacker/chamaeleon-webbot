from smolagents import CodeAgent, LiteLLMModel
from smolagents.tools import Tool
import mistune

from agent_base import (
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    visa_tool_base,
    visa_tool_description,
    chamaeleon_website_tool_base,
    detect_recommendation_links,
    website_tool_description,
    country_faq_tool_base,
    country_faq_tool_description,
    make_recommend_trip_base,
    format_system_prompt,
)

# Initialize the model for smolagents using Gemini via LiteLLMModel
model = LiteLLMModel(
    model_id="gemini/gemini-2.5-flash",
    api_key=GEMINI_API_KEY,
)


# Create smolagents tools by wrapping base functions
class VisaTool(Tool):
    name = "visa_tool"
    description = visa_tool_description
    inputs = {
        "country": {
            "type": "string",
            "description": "Das Land für das Visa-Informationen benötigt werden",
        }
    }
    output_type = "string"

    def forward(self, country: str) -> str:
        """Smolagents tool wrapper for the visa tool."""
        return visa_tool_base(country)


class ChamaeleonWebsiteTool(Tool):
    name = "chamaeleon_website_tool"
    description = website_tool_description
    inputs = {
        "url_path": {
            "type": "string",
            "description": "Der Pfad zur gewünschten Seite (z.B. '/Vision', '/Afrika/Namibia')",
        }
    }
    output_type = "str"

    def forward(self, url_path: str) -> str:
        """Smolagents tool wrapper for the base website tool."""
        return chamaeleon_website_tool_base(url_path)


class CountryFaqTool(Tool):
    name = "country_faq_tool"
    description = country_faq_tool_description
    inputs = {
        "country": {
            "type": "string",
            "description": "Das Land für das FAQ-Informationen benötigt werden",
        }
    }
    output_type = "string"

    def forward(self, country: str) -> str:
        """Smolagents tool wrapper for the country FAQ tool."""
        return country_faq_tool_base(country)


class RecommendTripTool(Tool):
    name = "recommend_trip"
    description = "Schlage eine oder mehrere Reise vor. Beispielsweise recommend_trip('Nofretete') oder recommend_trip(['/Nofretete-ALL', '/Botswana-Namibia/Okavango']). "
    inputs = {
        "trip_id": {
            "type": "string",
            "description": "ID der zu empfehlenden Reise oder Liste von IDs",
        }
    }
    output_type = "string"

    def __init__(self, container: set[str]):
        super().__init__()
        self.container = container
        self.base_func = make_recommend_trip_base(container)

    def forward(self, trip_id: str) -> str:
        """Smolagents tool wrapper for trip recommendations."""
        try:
            self.base_func(trip_id)
            return f"Reise '{trip_id}' wurde zur Empfehlungsliste hinzugefügt."
        except Exception as e:
            return f"Fehler beim Hinzufügen der Reise: {str(e)}"


def convert_messages_to_smolagents(messages: list) -> str:
    """Convert generic message format to a conversation string for smolagents."""
    conversation = ""
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            conversation += f"User: {content}\n"
        elif role == "assistant":
            conversation += f"Assistant: {content}\n"
    return conversation.strip()


def call(
    messages: list,
    endpoint: str,
    kundenberater_name: str = "",
    kundenberater_telefon: str = "",
) -> dict:
    """
    Main function to process messages and generate responses using smolagents.CodeAgent.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        endpoint: Current website endpoint the user is on
        kundenberater_name: Name of the customer advisor for this trip/page
        kundenberater_telefon: Phone number of the customer advisor for this trip/page

    Returns:
        dict: Contains 'reply' and 'recommendations' keys
    """
    # Format system prompt with current time and endpoint
    system_prompt = format_system_prompt(
        endpoint, kundenberater_name, kundenberater_telefon
    )

    # Initialize recommendation containers
    recommendations = set[str]()

    # Create tools
    tools = [
        VisaTool(),
        ChamaeleonWebsiteTool(),
        CountryFaqTool(),
        RecommendTripTool(recommendations),
    ]

    # Create agent
    agent = CodeAgent(
        tools=tools,
        model=model,
        additional_authorized_imports=["markdownify", "requests", "bs4"],
    )

    # Convert messages to conversation format
    conversation_history = convert_messages_to_smolagents(messages)

    # Get the latest user message
    latest_user_message = ""
    for msg in reversed(messages):
        if msg["role"] == "user":
            latest_user_message = msg["content"]
            break

    try:
        # Run the agent with the latest user message
        # Include conversation history in the message if there is any
        if conversation_history:
            full_message = f"Conversation history:\n{conversation_history}\n\nCurrent question: {latest_user_message}"
        else:
            full_message = latest_user_message

        response = agent.run(full_message)

        # Extract reply from response
        if hasattr(response, "content"):
            reply = response.content
        elif isinstance(response, str):
            reply = response
        else:
            reply = str(response)

    except Exception as e:
        # Fallback response in case of error
        reply = f"Entschuldigung, es gab einen technischen Fehler. Bitte kontaktiere unseren Kundenservice unter der Telefonnummer für weitere Hilfe. Fehlerdetails: {str(e)}"
        print(f"Error in smolagents call: {e}")

    # Process links in reply
    # reply = process_links_in_reply(reply)

    # Extract recommendations
    recommendations.update(detect_recommendation_links(reply))

    reply = mistune.markdown(reply, escape=False)  # Convert markdown to HTML if needed

    # Debug output
    result = {"reply": reply, "recommendations": list(recommendations)}
    print(result)

    return result
