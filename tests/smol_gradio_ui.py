from smolagents import GradioUI, CodeAgent

from agent_base import format_system_prompt
from agent_smol import model, ChamaeleonWebsiteTool, RecommendTripTool, VisaTool

endpoint = "/"

system_prompt = format_system_prompt(endpoint)

# Initialize recommendation containers
recommendations = set[str]()
human_support_requests = []

# Create tools
tools = [
    ChamaeleonWebsiteTool(),
    RecommendTripTool(recommendations),
    VisaTool(),
]

# Create agent
agent = CodeAgent(
    tools=tools,
    model=model,
    additional_authorized_imports=["markdownify", "requests", "bs4"],
)

GradioUI(agent).launch()
