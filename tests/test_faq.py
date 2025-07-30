from contextlib import redirect_stdout
import time
import json
import os
import re

import common as _

from agent_base import general_faq_data
from agent_lang import call

# --- Keywords for "Pass" Condition ---
# Each question maps to a list of keywords. ALL keywords must be present (case-insensitive)
# in the AI agent's response for the test to pass.
# Keywords can be either:
# - Plain text strings (matched as case-insensitive substrings)
# - Regular expressions (automatically detected by presence of regex special characters)

AMP = r"(?:&amp;|&)"

EXPECTED_KEYWORDS = {
    "Wie kann ich meine Reise bezahlen?": ["überweisung", "kreditkarte", "mastercard", "visa"],
    "Wo finde ich den Zahlungslink für die Kreditkartenzahlung?": ["rechnung", "mail", "zugesandt"],
    "Wie hoch ist die Anzahlung?": ["20%", "reisepreises", "restzahlung", "4 wochen", "reiseantritt"],
    "Wann erhalte ich meine Reiseunterlagen?": ["zwei wochen", "reisebeginn", "flugplan", "reisedetails", f"rail{AMP}fly-gutscheincodes"],
    "Welche Versicherungen bieten Sie an?": [
        "hansemerkur", "reiseversicherung", "premiumschutz", "basisschutz",
        "rücktrittsversicherung", "urlaubsgarantie"
        # Add more specific types if critical, e.g., "reise-krankenversicherung"
    ],
    "Versicherungsangebot": ["https://www.chamaeleon-reisen.de/daten/pdfs/hansemerkur_versicherung.pdf"],
    "Versicherungsbedingungen": [re.escape("https://m.hmrv.de/documents/168711/897094/vb-rks+2021+%28t-d%29.pdf/01f6f36a-f275-41ee-8ad9-6944207b6fcd")],
    "Ab wann kann ich meine Rail & Fly Tickets einbuchen?": ["10 wochen", "anreise", "digital", "reiseunterlagen"],
    "Kann ich mit dem Rail&Fly 1 Tag früher anreisen?": ["ja", "datum", "anpassen"],
    "Kann ich mit dem Rail&Fly 1 Tag später abreisen?": ["ja", "datum", "anpassen"],
    "Brauche ich ein Visum/Impfungen für meine Reise?": ["reiseanmeldung", "mein chamäleon", "einreisebestimmungen", "visainformationen", "länderinfos", "reiseziel"],
    "Wie ist der Altersdurchschnitt auf unseren Reisen?": ["50-60 jahre"],
    "Wo finde ich die Provisionsabrechnung?": ["agenturbereich", "hochgeladen", "https://agt.chamaeleon-reisen.de/agentur/buchungen"],
    "Können Kinder mitreisen?": ["ab ((zwölf)|12) jahren", "geeignet"],
    "Erhalte ich die Reiseunterlagen auch noch per Post?": ["digitaler form", "bestätigungsunterlagen", "post"],
    "Mein gewünschter Termin ist online nicht mehr sichtbar": ["ausgebucht", "geschlossen", "mail", "erlebnisberatung@chamaeleon-reisen.de"],
    "Ich möchte Sitzplätze reservieren, wie kann ich das tun?": ["mail", "erlebnisberatung@chamaeleon-reisen.de", "vorgangsnummer"],
    "Wann erhalte ich meine Flugtickets?": ["flugtickets .* nicht mehr", "flugplan", "reiseunterlagen"],
    "Wo finde ich meine Flugzeiten?": ["rechnung", "unterlagenlink", "mein chamäleon"],
    "Wo finde ich meine gebuchten Sitzplätze?": ["rechnung", "unterlagenlink", "mein chamäleon", "neben den flugzeiten", "gebucht sind"],
    "Wie löse ich einen Reisegutschein ein?": ["mail", "gutscheinnummer", "vorgang", "rechnen"],
    "Wie hoch ist die maximale Teilnehmerzahl auf den Reisen?": ["(12)|(zwölf)"],
    "Ich muss meine Reise stornieren wie mache ich das?": ["e-mail", "vorgangsnummer", "erlebnisberatung@chamaeleon-reisen.de"],
}

assert set(EXPECTED_KEYWORDS.keys()) == set(general_faq_data.keys())

PROGRESS_FILE = "test_progress.json"
RESULTS_FILE = "test_results_keywords.json"


def load_progress():
    """Load previous test progress if it exists."""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    
    # Initialize progress tracking for all questions
    return {
        question: {
            "passes": 0,
            "total_attempts": 0,
            "completed": False,
            "last_attempt_passed": False
        } for question in EXPECTED_KEYWORDS.keys()
    }


def save_progress(progress):
    """Save current test progress."""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=4, ensure_ascii=False)


def save_results(results):
    """Save detailed test results."""
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)


def is_regex_pattern(pattern):
    """Check if a string is intended to be a regular expression pattern."""
    # Consider it a regex if it contains regex special characters
    regex_chars = r'[\[\](){}.*+?^$|\\]'
    return bool(re.search(regex_chars, pattern))


def keyword_matches(keyword, text):
    """Check if keyword matches in text, supporting both plain text and regex."""
    if is_regex_pattern(keyword):
        try:
            # Use regex matching (case-insensitive)
            return bool(re.search(keyword, text, re.IGNORECASE))
        except re.error:
            # If regex is invalid, fall back to plain text matching
            return keyword.lower() in text.lower()
    else:
        # Plain text matching (case-insensitive)
        return keyword.lower() in text.lower()


def test_single_question(question, keywords):
    """Test a single question and return the result."""
    print(f"\nTesting Question: '{question}'")
    
    with open(os.devnull, "w") as f, redirect_stdout(f):
        ai_response = call([{
            "role": "user",
            "content": question
        }], "/")
    
    missing_keywords = []
    for keyword in keywords:
        if not keyword_matches(keyword, ai_response):
            missing_keywords.append(keyword)
    
    passed = not missing_keywords
    
    if passed:
        print(f"  Result: \u2705 PASS")
    else:
        print(f"  Result: \u274C FAIL")
        print(f"    Expected Keywords: {keywords}")
        print(f"    Missing Keywords : {missing_keywords}")
        print(f"    Received Response: '{ai_response}'")
    
    return {
        "question": question,
        "expected_keywords": keywords,
        "ai_response": ai_response,
        "passed": passed,
        "missing_keywords": missing_keywords,
        "timestamp": time.time()
    }


def run_tests():
    """
    Runs tests against the AI agent with progress tracking and multi-pass testing.
    - Saves intermediary results to resume from where left off
    - Tests each question until it passes 3 times without failing
    - Stops testing questions that have achieved 3 consecutive passes
    """
    progress = load_progress()
    all_results = []
    
    # Load existing results if available
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                all_results = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            all_results = []
    
    print(f"--- Starting Multi-Pass AI Agent Test Run ---")
    print(f"Total Questions: {len(EXPECTED_KEYWORDS)}")
    
    # Show current progress
    completed_questions = sum(1 for p in progress.values() if p["completed"])
    print(f"Already Completed: {completed_questions}")
    print(f"Remaining: {len(EXPECTED_KEYWORDS) - completed_questions}")
    print("-" * 60)
    
    # Phase 1: Test all questions until each passes at least once
    print("\n=== PHASE 1: Testing until all questions pass at least once ===")
    
    questions_to_test = [q for q, p in progress.items() if not p["last_attempt_passed"]]
    
    while questions_to_test:
        for question in questions_to_test[:]:  # Create a copy to modify during iteration
            if progress[question]["completed"]:
                questions_to_test.remove(question)
                continue
                
            keywords = EXPECTED_KEYWORDS[question]
            result = test_single_question(question, keywords)
            all_results.append(result)
            
            # Update progress
            progress[question]["total_attempts"] += 1
            progress[question]["last_attempt_passed"] = result["passed"]
            
            if result["passed"]:
                progress[question]["passes"] += 1
                questions_to_test.remove(question)
                print(f"  ✓ Question passed! Moving to Phase 2 for this question.")
            else:
                print(f"  ✗ Question failed. Will retry in next round.")
            
            # Save progress after each test
            save_progress(progress)
            save_results(all_results)
            
            print("-" * 60)
            time.sleep(10)
        
        if questions_to_test:
            print(f"\nRetrying {len(questions_to_test)} remaining questions...")
    
    print("\n🎉 PHASE 1 COMPLETE: All questions have passed at least once!")
    
    # Phase 2: Test questions until they pass 3 times consecutively
    print("\n=== PHASE 2: Testing for consistency (3 consecutive passes) ===")
    
    questions_needing_consistency = [
        q for q, p in progress.items() 
        if not p["completed"] and p["passes"] < 3
    ]
    
    while questions_needing_consistency:
        for question in questions_needing_consistency[:]:
            if progress[question]["completed"]:
                questions_needing_consistency.remove(question)
                continue
            
            keywords = EXPECTED_KEYWORDS[question]
            current_passes = progress[question]["passes"]
            
            print(f"\nTesting for consistency ({current_passes}/3 passes): '{question}'")
            
            result = test_single_question(question, keywords)
            all_results.append(result)
            
            # Update progress
            progress[question]["total_attempts"] += 1
            
            if result["passed"]:
                progress[question]["passes"] += 1
                progress[question]["last_attempt_passed"] = True
                
                if progress[question]["passes"] >= 3:
                    progress[question]["completed"] = True
                    questions_needing_consistency.remove(question)
                    print(f"  🎯 Question COMPLETED! (3 consecutive passes achieved)")
                else:
                    print(f"  ✓ Pass {progress[question]['passes']}/3")
            else:
                # Reset passes on failure
                progress[question]["passes"] = 0
                progress[question]["last_attempt_passed"] = False
                print(f"  ✗ Failed! Resetting pass counter to 0/3")
            
            # Save progress after each test
            save_progress(progress)
            save_results(all_results)
            
            print("-" * 60)
            time.sleep(10)
    
    # Final summary
    print("\n" + "="*60)
    print("🏆 ALL TESTS COMPLETE!")
    print("="*60)
    
    total_questions = len(EXPECTED_KEYWORDS)
    completed_questions = sum(1 for p in progress.values() if p["completed"])
    total_attempts = sum(p["total_attempts"] for p in progress.values())
    
    print(f"Total Questions: {total_questions}")
    print(f"Successfully Completed: {completed_questions}")
    print(f"Total Test Attempts: {total_attempts}")
    
    print(f"\nDetailed results saved to '{RESULTS_FILE}'")
    print(f"Progress tracking saved to '{PROGRESS_FILE}'")
    
    # Show summary by question
    print(f"\n--- Question Summary ---")
    for question, prog in progress.items():
        status = "✅ COMPLETED" if prog["completed"] else f"🔄 {prog['passes']}/3 passes"
        print(f"{status} - {prog['total_attempts']} attempts: '{question[:50]}...'")


if __name__ == "__main__":
    run_tests()