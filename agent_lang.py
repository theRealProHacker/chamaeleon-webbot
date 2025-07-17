from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from langgraph.prebuilt import create_react_agent
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
    make_recommend_human_support_base,
    format_system_prompt
)

# Initialize the model
model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=GEMINI_API_KEY)

# Alternative OpenAI model
# model = ChatOpenAI(
#     model_name="gpt-4.1-2025-04-14",
#     temperature=0.3,
#     openai_api_key=OPENAI_API_KEY
# )

@tool(description=visa_tool_description)
def visa_tool(country: str) -> str:
    """LangChain tool wrapper for the visa tool."""
    return visa_tool_base(country)

@tool(description=website_tool_description)
def chamaeleon_website_tool(url_path: str) -> str:
    """LangChain tool wrapper for the base website tool."""
    return chamaeleon_website_tool_base(url_path)

@tool(description=country_faq_tool_description)
def country_faq_tool(continent: str, country: str) -> str:
    """LangChain tool wrapper for the country FAQ tool."""
    return country_faq_tool_base(continent, country)

def make_recommend_trip(container: set[str]):
    """Create a LangChain tool for trip recommendations."""
    base_func = make_recommend_trip_base(container)

    @tool(description="Schlage eine oder mehrere Reise vor. Beispielsweise recommend_trip('Nofretete') oder recommend_trip(['/Nofretete-ALL', '/Botswana-Namibia/Okavango']). ")
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
            visa_tool,
            chamaeleon_website_tool,
            country_faq_tool,
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
    # reply = process_links_in_reply(reply)

    # Extract recommendations
    recommendations.update(detect_recommendation_links(reply))

    reply = mistune.markdown(reply, escape=False)  # Convert markdown to HTML if needed

    # Debug output
    result = {'reply': reply, 'recommendations': list(recommendations)}
    print(result)
    
    return result
