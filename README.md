# Chamaeleon Reisen Chatbot Proxy

This project is a Flask-based proxy server that injects a Gemini AI-powered chatbot into `chamaeleon-reisen.de`.

## Features

- **Proxy Server**: Forwards requests to `chamaeleon-reisen.de`.
- **Chatbot Injection**: Injects a chatbot into the HTML of the proxied pages.
- **Gemini AI Integration**: The chatbot is powered by Google's Gemini AI.
- **Live Content**: The proxied content is always up-to-date with the live site.

## Design-Schema

Das Design folgt diesen Spezifikationen:

- Fonts: Generis Simple (Std) and Caveat
- Standard font size: 17px
- Color scheme:
  - Primary: #FFCC00 (Yellow)
  - Secondary: #C7C9CC (Gray)
  - Adventure Trios: #01AAC9 (Cerulean)
  - Genießer: #5A9E33 (Apple)
  - Individuell/Selbstfahrer: #8E1D36 (Claret)
  - Erlebnis: #F59C00 (Gamboge)
  - Vor/Nachprogramme: #FFE580 (Marigold)

## Einrichtung und Installation

1. Stellen Sie sicher, dass Python installiert ist (3.6 bis 3.12 empfohlen)
   - Hinweis: Es gibt bekannte Kompatibilitätsprobleme mit Python 3.13 und Flask 2.0.1

2. Holen Sie sich einen Gemini API-Schlüssel vom Google AI Studio (https://makersuite.google.com/)

3. Aktualisieren Sie die `.env`-Datei mit Ihrem Gemini API-Schlüssel:
   ```
   GEMINI_API_KEY=your-api-key-here
   ```

4. Installieren Sie die erforderlichen Abhängigkeiten:
   ```
   pip install -r requirements.txt
   ```
   
   Wenn Sie auf Importfehler mit Werkzeug stoßen, versuchen Sie, spezifische kompatible Versionen zu installieren:
   ```
   pip install flask==2.3.3 werkzeug==2.3.7 python-dotenv==1.0.0 google-generativeai==0.3.1
   ```

5. Starten Sie die Flask-Anwendung:
   ```
   python app.py
   ```

6. Öffnen Sie Ihren Browser und navigieren Sie zu:
   ```
   http://localhost:5000
   ```

## Project Structure

- `app.py` - The main Flask application, including the proxy and chatbot logic.
- `chatbot.html` - The HTML, CSS, and JavaScript for the chatbot.
- `requirements.txt` - Python dependencies.
- `.env` - Environment variable file for storing the Gemini API key.

## How It Works

The Flask application acts as a reverse proxy. When a user accesses the Flask server, it forwards the request to `chamaeleon-reisen.de`, retrieves the content, and then injects the chatbot's HTML, CSS, and JavaScript before sending it back to the user. The chatbot itself communicates with the Flask backend to get responses from the Gemini AI.

## API Endpoint

- `/chat` - A POST endpoint that accepts a JSON object with a "message" field and returns an AI-generated response.

## Customization

You can customize the chatbot by editing the `chatbot.html` file. This includes changing the appearance (CSS), behavior (JavaScript), and initial HTML structure.