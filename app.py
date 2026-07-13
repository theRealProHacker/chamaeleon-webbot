import json
import os
import threading
import time
import traceback
from functools import cache

import requests
from flask import Flask, Response, abort, request, send_from_directory
from flask_cors import CORS

from agent import call_stream
from agent_base import markdownify_page_html
from kundendaten import filter_new_tool_calls, parse_kunden_id
import dashboard
import rate_limit
import sitemap_sync
import travel_index
from db_logging import Message, log_messages, log_queue
from recommendations import make_recommendation_previews_async

app = Flask(__name__)

# Configure CORS to allow requests from specific domains
CORS(
    app,
    origins=[
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
    ],
)


# Rate limiting (flask-limiter): keyed by client IP, loopback (dev) exempt.
limiter = rate_limit.init_app(app)


# Requests from the Reisebüro subdomains get the Agenturbereich knowledge base
# injected into the system prompt. The widget posts cross-origin, so the
# browser sends the Origin header; Referer and current_url are fallbacks.
#
# This is content-selection, NOT auth: all three signals are client-controlled
# and spoofable with curl, so faqs/agentur.md must only ever contain generic
# Reisebüro-Infos — never per-agency, bank, or contract data. If that is ever
# needed, gate on a server-verified agency login instead of headers.
# Matching is deliberately loose substring: a false positive is harmless, a
# missed agt request is worse. These hosts also appear in the CORS origins
# list above — keep both in sync.
AGENTUR_HOSTS = ("agt.chamaeleon-reisen.de", "agt.chamdev.tourone.de")


def is_agentur_request(endpoint: str) -> bool:
    candidates = (
        request.headers.get("Origin", ""),
        request.headers.get("Referer", ""),
        endpoint,
    )
    return any(host in value for value in candidates for host in AGENTUR_HOSTS)


# --- Streaming Chatbot API Endpoint ---
@app.route("/chat/stream", methods=["POST"])
@limiter.limit(rate_limit.MESSAGE_LIMIT, exempt_when=rate_limit.is_loopback)
def chat_stream():
    data = request.get_json()
    session_id = data.get("session_id")
    messages: list[Message] = data.get("messages", [])
    endpoint = data.get("current_url", "/")
    if not isinstance(endpoint, str):
        endpoint = "/"
    kundenberater_name = data.get("kundenberater_name", "")
    kundenberater_telefon = data.get("kundenberater_telefon", "")
    # Must be read here: the request context is gone inside the generator.
    is_agentur = is_agentur_request(endpoint)
    # Agentur pages are behind a login and unreachable for the server-side
    # website tool, so the widget scrapes and sends the page HTML instead.
    # Only honored on agentur requests; markdownify_page_html caps the
    # client-controlled input and never raises.
    page_content = ""
    if is_agentur:
        page_content = markdownify_page_html(data.get("page_html", ""))
    # Kunden-Modus: die Widget-gesendete kunden_id des eingeloggten
    # MeinChamäleon-Kunden. Client-asserted und unverifiziert (akzeptiertes
    # MVP-Risiko, siehe TODOS.md); parse_kunden_id normalisiert Typen und
    # filtert per Allowlist. Bei Agentur-Requests gewinnt der Agentur-Modus.
    kunden_id = "" if is_agentur else parse_kunden_id(data.get("kunden_id"))

    if not messages:
        return abort(400, "No messages provided")

    if not session_id:
        return abort(400, "No session_id provided")

    messages = messages[:]
    logging_messages = messages[-1:]
    # Set timestamp of user message
    # logging_messages[0]["timestamp"] = time.time()

    assert len(logging_messages) == 1 and logging_messages[0]["role"] == "user"

    logging_messages[0]["timestamp"] = time.time()

    def generate():
        # Dedup für tool_call-Events: stream_mode="values" liefert historische
        # Calls mit jedem Event erneut (siehe kundendaten.filter_new_tool_calls).
        seen_tool_call_ids: set[str] = set()
        try:
            for event in call_stream(
                messages,
                endpoint,
                kundenberater_name,
                kundenberater_telefon,
                is_agentur,
                page_content,
                kunden_id,
            ):
                # "Tool gefeuert" beobachtbar machen (stdout, nicht Supabase):
                # nur Toolname + session_id, nie Argumente oder Kundendaten.
                if event.get("type") == "tool_call":
                    for tc in filter_new_tool_calls(
                        [event["data"]], seen_tool_call_ids
                    ):
                        print(
                            f"[tool_call] session={session_id} "
                            f"tool={tc.get('name')} is_kunde={bool(kunden_id)}"
                        )

                # Handle recommendation previews for final response
                if event.get("type") == "response":
                    recommendations = event["data"]["recommendations"]

                    # Send the response first without previews
                    event_json = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_json}\n\n"
                    # log assistant message
                    logging_messages.append(
                        {
                            "role": "assistant",
                            "content": event["data"]["reply"],
                            "url": endpoint,
                            "timestamp": time.time(),
                        }
                    )

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
                                logging_messages.append(
                                    {
                                        "role": "recommendation_previews",
                                        "content": previews,
                                        "url": endpoint,
                                        "timestamp": time.time(),
                                    }
                                )  # type: ignore
                        except Exception as e:
                            print(f"Error generating recommendation previews: {e}")

                # elif event["type"]=="status":
                #     event_json = json.dumps(event, ensure_ascii=False)
                #     print("Status message: ", event_json)
                # yield f"data: {event_json}\n\n"

            log_queue.put(lambda: log_messages(session_id, logging_messages))

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


# --- Dashboard routes ---

for route, view_func, *rest in dashboard.routes:
    methods = rest[0] if rest else ["GET"]
    app.add_url_rule(route, view_func=view_func, methods=methods)

# --- End Dashboard routes ---


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

            content = content.replace(
                "https://chamaeleon-webbot-production.up.railway.app", ""
            )

            # Flask's Response will encode the string to UTF-8 by default
            return Response(content, resp.status_code)

        return Response(resp.content, resp.status_code, response_headers)

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return f"Request failed: {e}", 502


# Start the daily in-memory sitemap sync (02:00 Europe/Berlin) only in a real
# server process: the container has $PORT (gunicorn on Railway), or the Werkzeug
# reloader child (dev). A plain `import app` (tests, scripts) does not start it.
if os.environ.get("PORT") or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    # Restore the newest persisted sitemap (incl. /admin curation) BEFORE the
    # sync and the travel-index build, so both start from the curated URLs.
    sitemap_sync.restore_from_db()
    sitemap_sync.start_scheduler()
    travel_index.start_scheduler()

    # Boot warm-up in a background thread (boot itself must not block):
    # run the sitemap sync immediately — a fresh deploy should know today's
    # pages, not wait for the 02:00 job — and only THEN build the travel
    # index, so it derives against the just-synced sitemap instead of racing
    # it. Both steps fail open; the daily schedulers repeat them anyway.
    def _startup_warm():
        try:
            sitemap_sync.sync()
        except Exception as e:
            print(f"[app] startup sitemap sync failed: {e}")
        travel_index.rebuild()

    threading.Thread(target=_startup_warm, name="startup-warm", daemon=True).start()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
