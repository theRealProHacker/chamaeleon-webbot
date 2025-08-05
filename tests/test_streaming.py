"""
Test script for the streaming chatbot functionality
"""

import common as _

import sys
import os

from agent_lang import call_stream


def test_streaming(message: str = ""):
    """Test the streaming functionality"""
    messages = [
        {
            "role": "user",
            "content": message or "Hallo! Kannst du mir etwas über Namibia erzählen?",
        }
    ]

    print("=" * 50)

    try:
        for event in call_stream(messages, "/", "", ""):
            print(f"Event: {event['type']}")
            if event["type"] == "status":
                print(f"Status: {event['data']}")
            # elif event['type'] == 'response':
            #     print(f"Response: {event['data']['reply'][:100]}...")
            #     if event['data'].get('recommendations'):
            #         print(f"Recommendations: {event['data']['recommendations']}")
            elif event["type"] == "error":
                print(f"Error: {event['data']}")
            print("-" * 30)
    except Exception as e:
        print(f"Error during streaming: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_message = sys.argv[1]
        test_streaming(test_message)
    else:
        test_streaming()
