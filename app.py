from functools import cache
import os
from pprint import pprint
from bs4 import BeautifulSoup
from flask import Flask, request, Response, jsonify, abort, stream_template
from flask_cors import CORS
import requests
import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from agent_base import all_sites, find_trip_site, get_chamaeleon_website_html, BASE_URL
from agent import call
from agent_lang import call_stream

app = Flask(__name__)

# Configure CORS to allow requests from specific domains
CORS(app, origins=[
    "https://chamdev.tourone.de",
    "https://chamaeleon-reisen.de",
    "https://www.chamaeleon-reisen.de",
    # Allow HTTP for development
    "http://chamdev.tourone.de",  
    "http://chamaeleon-reisen.de",
    "http://www.chamaeleon-reisen.de"
])

LOGGING_URL = os.environ.get("LOGGING_URL", "http://localhost:5000/log")

def make_recommendation_preview(recommendation: str):
    """
    This is where we gather the preview information that is necessary for the preview.
    The frontend is still responsible for displaying the preview with nice HTML.

    The information we need is the trip title and the head image URL.
    """

    try:
        site = find_trip_site(recommendation)
    except ValueError:
        print(f"Warning: No site found for recommendation '{recommendation}'")
        return None  # No site found for the recommendation

    try:
        html = get_chamaeleon_website_html(site)
        soup = BeautifulSoup(html, "html.parser")

        title_text = soup.find("title").get_text(strip=True).split("-")[0].strip()
        if len(title_text.split()) > 5:
            title_text = recommendation.split("/")[-1].replace("-ALL", "")
        image_url = soup.find("meta", property="og:image")["content"]

        return {"url": BASE_URL + site, "title": title_text, "image": image_url}
    except Exception as e:
        print(f"Error creating preview for {recommendation}: {e}")
        return None


def make_recommendation_previews_async(recommendations):
    """
    Create recommendation previews in parallel using ThreadPoolExecutor
    """
    if not recommendations:
        return []

    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all preview generation tasks
        future_to_rec = {
            executor.submit(make_recommendation_preview, rec): rec
            for rec in recommendations
        }

        previews = []
        for future in future_to_rec:
            try:
                preview = future.result(timeout=5)  # 5 second timeout per preview
                if preview:
                    previews.append(preview)
            except Exception as e:
                rec = future_to_rec[future]
                print(f"Error creating preview for {rec}: {e}")

        return previews


# --- Chatbot API Endpoint ---
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])
    endpoint = data.get("current_url", "/")
    kundenberater_name = data.get("kundenberater_name", "")
    kundenberater_telefon = data.get("kundenberater_telefon", "")

    if not messages:
        abort(400, "No messages provided")

    try:
        response = call(messages, endpoint, kundenberater_name, kundenberater_telefon)
    except Exception as e:
        raise e

    if response.get("recommendations"):
        recommendations = response["recommendations"]

        # Generate previews asynchronously
        try:
            response["recommendation_previews"] = make_recommendation_previews_async(
                recommendations
            )
        except Exception as e:
            print(f"Error generating recommendation previews: {e}")
            response["recommendation_previews"] = []

    return jsonify(response)


# --- End Chatbot API Endpoint ---


# --- Streaming Chatbot API Endpoint ---
@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    data = request.get_json()
    messages: list[dict] = data.get("messages", [])
    endpoint = data.get("current_url", "/")
    kundenberater_name = data.get("kundenberater_name", "")
    kundenberater_telefon = data.get("kundenberater_telefon", "")

    if not messages:
        abort(400, "No messages provided")

    messages = messages[:-1]
    logging_messages = messages[1:]
    
    def generate():
        try:
            for event in call_stream(
                messages, endpoint, kundenberater_name, kundenberater_telefon
            ):
                # Handle recommendation previews for final response
                if event.get("type") == "response":
                    recommendations = event["data"]["recommendations"]

                    # Send the response first without previews
                    logging_messages.append({
                        "role": "assistant",
                        "content": event["data"]["reply"]
                    })
                    event_json = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_json}\n\n"

                    # Generate previews asynchronously and send them separately
                    if recommendations:
                        try:
                            previews = make_recommendation_previews_async(
                                recommendations
                            )
                            if previews:
                                preview_event = {
                                    "type": "recommendation_previews",
                                    "data": {"recommendation_previews": previews},
                                }
                                logging_messages.append(preview_event)
                                preview_json = json.dumps(
                                    preview_event, ensure_ascii=False
                                )
                                yield f"data: {preview_json}\n\n"
                        except Exception as e:
                            print(f"Error generating recommendation previews: {e}")
                elif event["type"]=="status":
                    event_json = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_json}\n\n"

            response = requests.post(LOGGING_URL, data=json.dumps(logging_messages))
            if response.status_code != 200:
                print(f"Error logging messages: {response.text}")

        except Exception as e:
            print(f"Error in streaming: {e}")
            import traceback

            traceback.print_exc()
            error_event = {"type": "error", "data": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# --- End Streaming Chatbot API Endpoint ---


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
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@cache
def proxy(path):
    target_url = f"{BASE_URL}/{path}"
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8")

    headers = {key: value for key, value in request.headers if key.lower() != "host"}
    headers["Host"] = "www.chamaeleon-reisen.de"

    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
        )

        excluded_headers = [
            "content-encoding",
            "content-length",
            "transfer-encoding",
            "connection",
        ]
        response_headers = [
            (name, value)
            for name, value in resp.raw.headers.items()
            if name.lower() not in excluded_headers
        ]

        content_type = resp.headers.get("Content-Type", "")

        # Only inject if content is HTML
        if "text/html" in content_type and "." not in path:
            # Decode content from ISO-8859-1 to Python Unicode string
            content = resp.content.decode("ISO-8859-1")

            content = content.replace("https://chamaeleon-webbot-production.up.railway.app", "")

            # # Replace any existing charset meta tag with UTF-8
            # content = re.sub(
            #     r'<meta\s+charset=["\\]?[^"\\\'>]*["\\]?>',
            #     '<meta charset="UTF-8">',
            #     content,
            #     flags=re.IGNORECASE,
            # )
            # content = re.sub(
            #     r'<meta\s+http-equiv=["\\]?Content-Type["\\]?\s+content=["\\]?text/html;\s*charset=["\\]?[^"\\\'>]*["\\]?>',
            #     '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">',
            #     content,
            #     flags=re.IGNORECASE,
            # )

            # # If no charset meta tag exists, add one in the head
            # if "<head>" in content.lower() and "<meta charset" not in content.lower():
            #     content = content.replace("<head>", '<head><meta charset="UTF-8">')
            # elif (
            #     "<head>" not in content.lower()
            #     and "<meta charset" not in content.lower()
            # ):
            #     # Fallback if no head tag, add at the beginning of html
            #     if "<html" in content.lower():
            #         content = content.replace(
            #             "<html",
            #             '<html lang="de"><head><meta charset="UTF-8"></head>',
            #             1,
            #         )
            #     else:
            #         content = (
            #             '<!DOCTYPE html>\n<html lang="de">\n<head>\n<meta charset="UTF-8">\n</head>\n<body>'
            #             + content
            #             + "</body>\n</html>"
            #         )

            # # Inject chatbot before </body>
            # if "</body>" in content.lower():
            #     content = content.replace("</body>", chatbot_html + "</body>")

            # # Ensure the Content-Type header for the response is UTF-8
            # final_content_type = "text/html; charset=UTF-8"
            # updated_response_headers = []
            # content_type_found = False
            # for name, value in response_headers:
            #     if name.lower() == "content-type":
            #         updated_response_headers.append(
            #             ("Content-Type", final_content_type)
            #         )
            #         content_type_found = True
            #     else:
            #         updated_response_headers.append((name, value))
            # if not content_type_found:
            #     updated_response_headers.append(("Content-Type", final_content_type))

            # Flask's Response will encode the string to UTF-8 by default
            return Response(content, resp.status_code, 
                            # updated_response_headers
            )

        return Response(resp.content, resp.status_code, response_headers)

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return f"Request failed: {e}", 502


if __name__ == "__main__":
    app.run(debug=True, port=5000)
