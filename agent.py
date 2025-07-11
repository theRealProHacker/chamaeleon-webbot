
# Main agent interface - delegates to specific implementations
# By default, uses the LangChain implementation

from agent_lang import call

# Future implementations can be imported here, e.g.:
# from agent_openai import call as call_openai
# from agent_custom import call as call_custom

# Main call function - currently uses LangChain implementation
# This can be easily switched to use different implementations later


