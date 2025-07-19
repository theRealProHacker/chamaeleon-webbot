#!/usr/bin/env python3
"""
Test script for the streaming chatbot functionality
"""

import sys
import os

# Add the bot directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_lang import call_stream

def test_streaming():
    """Test the streaming functionality"""
    messages = [
        {"role": "user", "content": "Hallo! Kannst du mir etwas über Namibia erzählen?"}
    ]
    
    print("Testing streaming functionality...")
    print("=" * 50)
    
    try:
        for event in call_stream(messages, "/", "", ""):
            print(f"Event: {event['type']}")
            if event['type'] == 'status':
                print(f"Status: {event['data']}")
            elif event['type'] == 'response':
                print(f"Response: {event['data']['reply'][:100]}...")
                if event['data'].get('recommendations'):
                    print(f"Recommendations: {event['data']['recommendations']}")
            elif event['type'] == 'error':
                print(f"Error: {event['data']}")
            print("-" * 30)
    except Exception as e:
        print(f"Error during streaming: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_streaming()
