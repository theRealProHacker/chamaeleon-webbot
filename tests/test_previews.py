#!/usr/bin/env python3
"""
Test script for the enhanced streaming chatbot functionality
"""

import sys
import os
import time

# Add the bot directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import make_recommendation_previews_async

def test_async_previews():
    """Test the asynchronous recommendation preview generation"""
    print("Testing async recommendation preview generation...")
    print("=" * 50)
    
    # Test with some sample recommendations
    recommendations = ['/Afrika/Namibia', '/Afrika/Botswana', '/Europa/Italien']
    
    start_time = time.time()
    
    try:
        previews = make_recommendation_previews_async(recommendations)
        end_time = time.time()
        
        print(f"Generated {len(previews)} previews in {end_time - start_time:.2f} seconds")
        
        for preview in previews:
            print(f"- {preview['title']}: {preview['url']}")
            
    except Exception as e:
        print(f"Error during async preview generation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_async_previews()
