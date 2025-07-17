from smolagents import CodeAgent, LiteLLMModel
from smolagents.tools import Tool

from agent_base import (
    GEMINI_API_KEY, 
    OPENAI_API_KEY,
    chamaeleon_website_tool_base,
    website_tool_description,
    make_recommend_trip_base,
    make_recommend_human_support_base,
    format_system_prompt,
    process_links_in_reply
)

# Initialize the model for smolagents using Gemini via LiteLLMModel
model = LiteLLMModel(
    model_id="gemini/gemini-pro",
    api_key=GEMINI_API_KEY,
)

# Create smolagents tools by wrapping base functions
class ChamaeleonWebsiteTool(Tool):
    name = "chamaeleon_website_tool"
    description = website_tool_description
    inputs = {"url_path": {"type": "string", "description": "Der Pfad zur gewünschten Seite (z.B. '/Vision', '/Afrika/Namibia')"}}
    output_type = "object"

    def forward(self, url_path: str) -> dict:
        """Smolagents tool wrapper for the base website tool."""
        return chamaeleon_website_tool_base(url_path)

class RecommendTripTool(Tool):
    name = "recommend_trip"
    description = "Schlage eine oder mehrere Reise vor (z.B. recommend_trip('Nofretete'))"
    inputs = {"trip_id": {"type": "string", "description": "ID der zu empfehlenden Reise"}}
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

class RecommendHumanSupportTool(Tool):
    name = "recommend_human_support"
    description = "Empfehle den menschlichen Kundenberater anzurufen"
    inputs = {}
    output_type = "string"
    
    def __init__(self, container: list[str]):
        super().__init__()
        self.container = container
        self.base_func = make_recommend_human_support_base(container)

    def forward(self) -> str:
        """Smolagents tool wrapper for human support recommendations."""
        try:
            self.base_func()
            return "Menschlicher Kundenberater wurde empfohlen."
        except Exception as e:
            return f"Fehler beim Empfehlen des Kundenberaters: {str(e)}"

def convert_messages_to_smolagents(messages: list) -> str:
    """Convert generic message format to a conversation string for smolagents."""
    conversation = ""
    for msg in messages:
        role = msg['role']
        content = msg['content']
        if role == 'user':
            conversation += f"User: {content}\n"
        elif role == 'assistant':
            conversation += f"Assistant: {content}\n"
    return conversation.strip()

def call(messages: list, endpoint: str, kundenberater_name: str = "", kundenberater_telefon: str = "") -> dict:
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
    system_prompt = format_system_prompt(endpoint, kundenberater_name, kundenberater_telefon)
    
    # Initialize recommendation containers
    recommendations = set[str]()
    human_support_requests = []
    
    # Create tools
    tools = [
        ChamaeleonWebsiteTool(),
        RecommendTripTool(recommendations),
        RecommendHumanSupportTool(human_support_requests)
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
        if msg['role'] == 'user':
            latest_user_message = msg['content']
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
        if hasattr(response, 'content'):
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
    reply = process_links_in_reply(reply)
    
    # Debug output
    result = {'reply': reply, 'recommendations': list(recommendations)}
    print(result)
    
    return result
