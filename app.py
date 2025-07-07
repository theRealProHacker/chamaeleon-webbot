from functools import cache
from flask import Flask, request, Response, jsonify, abort
import requests
import re

from agent import call

app = Flask(__name__)


# --- Chatbot API Endpoint ---
@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    messages = data.get('messages', [])
    endpoint = data.get('current_url', 'N/A')
    
    if not messages:
        abort(400, 'No messages provided')

    try:
       response = call(messages, endpoint)
    except Exception as e:
        print(f"Error calling agent: {str(e)}")
        abort(500)
    
    return jsonify(response)
# --- End Chatbot API Endpoint ---


# --- Proxy Setup ---
BASE_URL = "https://www.chamaeleon-reisen.de"

# Load chatbot widget once on startup
try:
    with open("chatbot.html", "r", encoding="utf-8") as f:
        chatbot_html = f.read()
except FileNotFoundError:
    chatbot_html = "<!-- chatbot.html not found -->"
# --- End Proxy Setup ---


# --- Proxy Route ---
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
@cache
def proxy(path):
    target_url = f"{BASE_URL}/{path}"
    if request.query_string:
        target_url += '?' + request.query_string.decode('utf-8')

    headers = {key: value for key, value in request.headers if key.lower() != 'host'}
    headers['Host'] = 'www.chamaeleon-reisen.de'

    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
        )

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for name, value in resp.raw.headers.items() if name.lower() not in excluded_headers]

        content_type = resp.headers.get('Content-Type', '')

        # Only inject if content is HTML
        if 'text/html' in content_type and "." not in path:
            # Decode content from ISO-8859-1 to Python Unicode string
            content = resp.content.decode('ISO-8859-1')

            # Replace any existing charset meta tag with UTF-8
            content = re.sub(r'<meta\s+charset=["\\]?[^"\\\'>]*["\\]?>', '<meta charset="UTF-8">', content, flags=re.IGNORECASE)
            content = re.sub(r'<meta\s+http-equiv=["\\]?Content-Type["\\]?\s+content=["\\]?text/html;\s*charset=["\\]?[^"\\\'>]*["\\]?>', '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">', content, flags=re.IGNORECASE)

            # If no charset meta tag exists, add one in the head
            if '<head>' in content.lower() and '<meta charset' not in content.lower():
                content = content.replace('<head>', '<head><meta charset="UTF-8">')
            elif '<head>' not in content.lower() and '<meta charset' not in content.lower():
                # Fallback if no head tag, add at the beginning of html
                if '<html' in content.lower():
                    content = content.replace('<html', '<html lang="de"><head><meta charset="UTF-8"></head>', 1)
                else:
                    content = '<!DOCTYPE html>\n<html lang="de">\n<head>\n<meta charset="UTF-8">\n</head>\n<body>' + content + '</body>\n</html>'

            # Inject chatbot before </body>
            if '</body>' in content.lower():
                content = content.replace('</body>', chatbot_html + '</body>')

            # Ensure the Content-Type header for the response is UTF-8
            final_content_type = 'text/html; charset=UTF-8'
            updated_response_headers = []
            content_type_found = False
            for name, value in response_headers:
                if name.lower() == 'content-type':
                    updated_response_headers.append(('Content-Type', final_content_type))
                    content_type_found = True
                else:
                    updated_response_headers.append((name, value))
            if not content_type_found:
                updated_response_headers.append(('Content-Type', final_content_type))

            # Flask's Response will encode the string to UTF-8 by default
            return Response(content, resp.status_code, updated_response_headers)

        return Response(resp.content, resp.status_code, response_headers)

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return f"Request failed: {e}", 502

if __name__ == "__main__":
    app.run(debug=True, port=5000)