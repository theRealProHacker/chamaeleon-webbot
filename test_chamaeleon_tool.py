#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test-Script für das Chamäleon Website Tool
"""

from agent import chamaeleon_website_tool

def test_vision_page():
    """Testet das Tool mit der Vision-Seite"""
    print("Testing Chamäleon Website Tool with /Vision page...")
    print("=" * 50)
    
    result = chamaeleon_website_tool('/Vision')
    
    if result.get('status') == 'success':
        print(f"📄 Titel: {result['title']}")
        print("\n📝 MAIN CONTENT (Auszug):")
        print("-" * 30)
        print(result['main_content'][:500] + "..." if len(result['main_content']) > 500 else result['main_content'])
    else:
        print(f"❌ Fehler: {result.get('error')}")
    
    print("\n" + "=" * 50)

if __name__ == "__main__":
    test_vision_page()