import asyncio
import json
import os
import re
import time
import traceback
from functools import cache
from pprint import pprint

import requests
from flask import Flask, Response, abort, jsonify, request, stream_template
from flask_cors import CORS

from agent import call_stream
from db_logging import Message, log_messages, logging_old
from recommendations import make_recommendation_previews_async

app = Flask(__name__)

# Configure CORS to allow requests from specific domains
CORS(app, origins=[
    "https://chamdev.tourone.de",
    "https://chamaeleon-reisen.de",
    "https://www.chamaeleon-reisen.de",
    "https://agt.chamaeleon-reisen.de",
    "https://agt.chamdev.tourone.de",
    "https://leon.chamdev.tourone.de",
    # Allow HTTP for development
    "http://localhost",
    "http://127.0.0.1",
    "http://chamdev.tourone.de",
    "http://chamaeleon-reisen.de",
    "http://www.chamaeleon-reisen.de",
    "http://agt.chamaeleon-reisen.de",
    "http://agt.chamdev.tourone.de",
    "http://leon.chamdev.tourone.de",
])


# --- Streaming Chatbot API Endpoint ---
@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    data = request.get_json()
    session_id = data.get("session_id")
    messages: list[Message] = data.get("messages", [])
    endpoint = data.get("current_url", "/")
    kundenberater_name = data.get("kundenberater_name", "")
    kundenberater_telefon = data.get("kundenberater_telefon", "")

    if not messages:
        abort(400, "No messages provided")
    
    if not session_id:
        abort(400, "No session_id provided")

    messages = messages[:]
    logging_messages = messages[-1:]

    assert len(logging_messages) == 1 and logging_messages[0]["role"] == "user"

    logging_messages[0]["timestamp"] = time.time()
    
    def generate():
        try:
            for event in call_stream(
                messages, endpoint, kundenberater_name, kundenberater_telefon
            ):
                # Handle recommendation previews for final response
                if event.get("type") == "response":
                    recommendations = event["data"]["recommendations"]

                    # Send the response first without previews
                    event_json = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_json}\n\n"
                    # log assistant message
                    logging_messages.append({
                        "role": "assistant",
                        "content": event["data"]["reply"],
                        "url": endpoint,
                        "timestamp": time.time()
                    })

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
                                preview_json = json.dumps(
                                    preview_event, ensure_ascii=False
                                )
                                yield f"data: {preview_json}\n\n"
                                logging_messages.append({
                                    "role": "recommendation_previews",
                                    "content": previews,
                                    "url": endpoint,
                                    "timestamp": time.time()
                                }) # type: ignore
                        except Exception as e:
                            print(f"Error generating recommendation previews: {e}")
                    
                # elif event["type"]=="status":
                #     event_json = json.dumps(event, ensure_ascii=False)
                #     print("Status message: ", event_json)
                    # yield f"data: {event_json}\n\n"

            log_messages(session_id, logging_messages)
            logging_old(logging_messages)

        except Exception as e:
            print(f"Error in streaming: {e}")
            traceback.print_exc()
            error_event = {"type": "error", "data": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable buffering for nginx
        },
    )


# --- End Streaming Chatbot API Endpoint ---


# --- Proxy Setup ---
BASE_URL = "https://www.chamaeleon-reisen.de"

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
