from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from langgraph.prebuilt import create_react_agent

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

# Initialize the model
model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=GEMINI_API_KEY)

# Alternative OpenAI model
# model = ChatOpenAI(
#     model_name="gpt-4.1-2025-04-14",
#     temperature=0.3,
#     openai_api_key=OPENAI_API_KEY
# )

# Create LangChain tools by decorating base functions
@tool(description=website_tool_description)
def chamaeleon_website_tool(url_path: str) -> dict:
    """LangChain tool wrapper for the base website tool."""
    return chamaeleon_website_tool_base(url_path)

def make_recommend_trip(container: set[str]):
    """Create a LangChain tool for trip recommendations."""
    base_func = make_recommend_trip_base(container)
    
    @tool(description="Schlage eine oder mehrere Reise vor (z.B. recommend_trip('Nofretete')). ")
    def recommend_trip(trip_id: str|list[str]):
        return base_func(trip_id)
    
    return recommend_trip

def make_recommend_human_support(container: list[str]):
    """Create a LangChain tool for human support recommendations."""
    base_func = make_recommend_human_support_base(container)
    
    @tool(description="Empfehle den menschlichen Kundenberater anzurufen. ")
    def recommend_human_support():
        return base_func()
    
    return recommend_human_support

def convert_messages_to_langchain(messages: list) -> list:
    """Convert generic message format to LangChain message objects."""
    chat_history = []
    for msg in messages:
        if msg['role'] == 'user':
            chat_history.append(HumanMessage(content=msg['content']))
        elif msg['role'] == 'assistant':
            chat_history.append(AIMessage(content=msg['content']))
    return chat_history

def call(messages: list, endpoint: str) -> dict:
    """
    Main function to process messages and generate responses using LangChain/LangGraph.
    
    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        endpoint: Current website endpoint the user is on
        
    Returns:
        dict: Contains 'reply' and 'recommendations' keys
    """
    # Format system prompt with current time and endpoint
    system_prompt = format_system_prompt(endpoint)
    
    # Convert messages to LangChain format
    chat_history = [SystemMessage(content=system_prompt)] + convert_messages_to_langchain(messages)
    
    # Initialize recommendation containers
    recommendations = set[str]()
    
    # Create agent with tools
    agent_executor = create_react_agent(
        model,
        tools=[
            chamaeleon_website_tool,
            make_recommend_trip(recommendations)
        ],
    )
    
    # Get response from agent
    response = agent_executor.invoke({"messages": chat_history})

    # Debug output
    for message in response["messages"]:
        message.pretty_print()

    # Extract reply from response
    reply = response["messages"][-1].content

    # Process links in reply
    reply = process_links_in_reply(reply)

    # Debug output
    result = {'reply': reply, 'recommendations': list(recommendations)}
    print(result)
    
    return result
