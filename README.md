# Chamaeleon Reisen Chatbot Proxy

Flask-based proxy server that injects a Gemini AI-powered chatbot into `chamaeleon-reisen.de`.

## Quick Start

1. Get a Gemini API key from [Google AI Studio](https://makersuite.google.com/)
2. Create `.env` file: `GEMINI_API_KEY=your-api-key-here`
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python app.py`
5. Open: `http://localhost:5000`

## Files

- `app.py` - Main Flask application
- `chatbot.html` - Chatbot UI
- `requirements.txt` - Dependencies
- `.env` - API key configuration