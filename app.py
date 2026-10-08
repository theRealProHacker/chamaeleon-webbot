import json
import os
import threading
import time
import traceback
from functools import cache

import requests
from flask import Flask, Response, abort, request, send_from_directory
from flask_cors import CORS

import agent
from agent import call_stream
from agent_base import _vrrvorgang_from_url, markdownify_page_html
import kundendaten
from kundendaten import filter_new_tool_calls
import agentur_auth
import kunden_auth
import chat_segments
import dashboard
import quality_job
import rate_limit
import faq_sync
import sitemap_sync
import travel_index
import unterlagen
from db_logging import DEBUG, Message, log_messages, log_queue
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
    # Normalised BEFORE it reaches the model or the database: the widget has
    # sent both absolute urls and bare paths, and the dashboard groups on this
    # value. Normalising at the boundary means the path parsing downstream only
    # ever has to handle one shape (and only for rows written before this).
    # Die Buchungsnummer der geöffneten MeinChamäleon-Reise steht nur in der
    # Query, und die wirft normalize_url weg (2026-09-29: ohne sie baute Leon
    # /MeinChamaeleon/Reise#unterlagen ohne Nummer). Der Agent bekommt sie
    # zurück, Log und Dashboard weiter nur den Pfad.
    seiten_vorgang = _vrrvorgang_from_url(endpoint)
    endpoint = chat_segments.normalize_url(endpoint) or "/"
    agent_endpoint = (
        f"{endpoint}?VRRVORGANG={seiten_vorgang}" if seiten_vorgang else endpoint
    )
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
    # Kunden-Modus: die kunden_id kommt NICHT mehr aus dem Body (das war
    # client-asserted und spoofbar), sondern aus der serverseitig verifizierten
    # Bindung, die POST /kunde/auth via ss.php angelegt hat. session_id ist der
    # Auth-Token. Ein im Body mitgeschickter kunden_id-Wert wird ignoriert. Bei
    # Agentur-Requests gewinnt der Agentur-Modus (Modi bleiben exklusiv).
    if not messages:
        return abort(400, "No messages provided")

    if not session_id or not isinstance(session_id, str):
        return abort(400, "No session_id provided")

    # Erst NACH der Typprüfung: resolve() schlägt sonst mit TypeError (500) auf
    # einem unhashbaren session_id aus dem Body auf (JSON-Objekt/-Array).
    kunden_id = "" if is_agentur else (kunden_auth.resolve(session_id) or "")
    # Die Agenturnummer kommt — wie die kunden_id — NUR aus der serverseitig
    # verifizierten Bindung, nie aus dem Body. is_agentur allein ist bloß ein
    # Header-Spiegel und beweist gar nichts; er entscheidet nur über den
    # Prompt-Modus. Erst diese Bindung schaltet die Buchungsdaten frei.
    agentur_id = (agentur_auth.resolve(session_id) or "") if is_agentur else ""

    # The segment the dashboard counts this chat under. Derived here, from the
    # server-verified bindings, rather than in the dashboard from the
    # client-sent url — see chat_segments.segment_from_context.
    segment = chat_segments.segment_from_context(endpoint, kunden_id, agentur_id)

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
                agent_endpoint,
                kundenberater_name,
                kundenberater_telefon,
                is_agentur,
                page_content,
                kunden_id,
                agentur_id,
                session_id=session_id,
            ):
                # "Tool gefeuert" beobachtbar machen (stdout, nicht Supabase):
                # nur Toolname + session_id, nie Argumente oder Kundendaten.
                # Nur bei DEBUG=true — in prod würde das die Logs zumüllen.
                if DEBUG and event.get("type") == "tool_call":
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
                            "segment": segment,
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
                                        "segment": segment,
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


# --- Kunden-Modus auth ---
def _vorwaermen_buchungen(session_id: str) -> None:
    """Die Buchungen des gerade angemeldeten Kunden im Hintergrund vorholen.

    Das Widget ruft /kunde/auth, bevor der Kunde tippt. Wer diese Sekunde
    nutzt, spart sie der ersten Nachricht: der Prompt-Bau löst dort die gemeinte
    Reise auf (agent.reise_fuer_links) und läse sonst kalt aus TourOne —
    gemessen 2026-09-20 für Testkunde 999999999: Median 286 ms, kalt 768 ms,
    Ausreißer 1.268 ms.

    Muster wie ``_startup_warm``: Daemon-Thread, die Route wartet nicht. Die
    Kundennummer kennt die Route nicht (authenticate gibt nur authenticated und
    session_id zurück), der Thread holt sie über dieselbe Bindung wie
    /chat/stream.

    Annahme: das wirkt nur, solange WEB_CONCURRENCY=1 — der Cache liegt im
    Prozess (gleiche Annahme wie rate_limit und session_binding). Mit mehr
    Workern landet die erste Nachricht eventuell bei einem anderen Prozess und
    zahlt den Abruf selbst; mehr passiert nicht.

    Fehler bleiben hier: ein misslungenes Vorwärmen darf die Anmeldung nicht
    berühren. Geloggt wird nur der Typ, nie die Kundennummer (gleicher Grund wie
    in kundendaten.vorgangsnummern).
    """

    def hole():
        try:
            kunden_id = kunden_auth.resolve(session_id) or ""
            if kunden_id:
                # Absichtlich die private Funktion: gewärmt werden sollen genau
                # die Cache-Einträge, die der Prompt-Bau später liest — Hop 1
                # (Buchungen) UND Hop 2 (Status der gemeinten Reise). Nur Hop 1
                # zu wärmen liess die erste Nachricht die Statusprüfung kalt
                # zahlen, im 1-s-Budget von reise_fuer_links (Review 2026-09-25).
                vorgang, _ = agent._naechste_reise(kunden_id)
                # Weiter bis zum PDF-Text der Reise: Hop 2 liegt nach der
                # Statusprüfung schon im Cache, fehlt also nur Download und
                # Extraktion — sonst zahlt die erste Inhaltsfrage bis ~9 s.
                if vorgang:
                    unterlagen.vorwaermen(kundendaten._buchung_roh(vorgang))
        except Exception as e:
            print(f"[app] kunden warm failed: {type(e).__name__}")

    threading.Thread(target=hole, name="kunden-warm", daemon=True).start()


@app.route("/kunde/auth", methods=["POST"], endpoint=rate_limit.AUTH_ENDPOINT)
@limiter.limit(rate_limit.MESSAGE_LIMIT, exempt_when=rate_limit.is_loopback)
def kunde_auth():
    """Verify the MeinChamäleon login once and bind it to the chat session_id.

    Wir sind eine ANDERE ORIGIN als chamaeleon-reisen.de — der Browser schickt
    das Session-Cookie also nicht von selbst mit (daran war v1 wirkungslos). Das
    Widget liest sein eigenes PHPSESSID per document.cookie auf der Chamäleon-
    Seite und schickt den Wert im Body; der Server spielt ihn gegen ss.php ein,
    liest die autoritative Kundennummer und bindet sie an die session_id. Danach
    ist session_id der Auth-Token (siehe /chat/stream).

    Fail closed — und zwar erst löschen, dann prüfen: ein fehlgeschlagener
    Re-Auth darf NIE die Bindung des vorherigen Kunden stehen lassen (geteilter
    Browser, gleiche gespeicherte session_id). Rate-limited wie /chat/stream,
    ss.php ist ein externes Orakel.
    """
    # NICHT request.get_json(): das liefert bei jedem Nicht-JSON-Content-Type
    # None, und ein fetch() ohne expliziten Header schickt text/plain. Ginge
    # dabei die session_id verloren, würden wir mit 400 aussteigen BEVOR die
    # Bindung gelöscht ist — der vorherige Kunde bliebe gebunden. Ein Header darf
    # keine Sicherheitsfunktion haben. read_capped_body deckelt das Ganze, damit
    # daraus kein Speicher-DoS wird (siehe dort).
    data = kunden_auth.coerce_json_body(None, kunden_auth.read_capped_body(request))
    # Die gesamte Reihenfolge (löschen → prüfen → nur committen, wenn nichts
    # dazwischenkam) liegt bewusst in kunden_auth.authenticate: sie IST die
    # Sicherheitseigenschaft dieses Endpunkts und ist dort testbar, ohne app zu
    # importieren (das löst Live-Supabase-Reads aus).
    # Origin entscheidet, gegen WELCHES ss.php der Token eingespielt wird: eine
    # PHP-Session existiert nur im Store ihres eigenen Hosts, ein PHPSESSID vom
    # Dev-Host ist für Produktion bedeutungslos (kunden_auth.SS_URLS). Der Header
    # ist client-kontrolliert und deshalb dort nur ein Tabellen-Key, nie die URL.
    authenticated, session_id = kunden_auth.authenticate(
        data,
        request.headers.get("User-Agent", ""),
        request.headers.get("Origin", ""),
    )
    if session_id is None:
        # Ohne brauchbare session_id gibt es auch nichts zu lösen.
        return abort(400, "No session_id provided")
    if authenticated:
        _vorwaermen_buchungen(session_id)
    return {"authenticated": authenticated}


# --- End Kunden-Modus auth ---


# --- Agentur-Modus auth ---
#
# ⚠ Die View heißt agentur_auth_ROUTE, nicht agentur_auth. `def agentur_auth():`
# auf Modulebene würde das globale Binding überschreiben, das `import
# agentur_auth` oben angelegt hat — jeder spätere agentur_auth.resolve(...) in
# chat_stream wirft dann AttributeError → 500 auf JEDEM Agentur-Chat.
#
# Der endpoint-Name kommt aus rate_limit.AGENTUR_AUTH_ENDPOINT (wie /kunde/auth
# aus AUTH_ENDPOINT), damit Route und rate_limit.AUTH_ENDPOINTS nicht
# auseinanderlaufen können: fiele diese Route aus der Map, liefe ihr 429
# fail-OPEN.
#
# Was das Shadowing abfängt, ist seit dem Review test_general.py — die beiden
# chat_stream-Tests importieren app und posten echt gegen /chat/stream, also
# würde ein überschriebenes agentur_auth dort als 500 auffallen.
@app.route(
    "/agentur/auth", methods=["POST"], endpoint=rate_limit.AGENTUR_AUTH_ENDPOINT
)
@limiter.limit(rate_limit.MESSAGE_LIMIT, exempt_when=rate_limit.is_loopback)
def agentur_auth_route():
    """Verify the agt-Login once and bind die Agenturnummer an die session_id.

    Spiegelt /kunde/auth exakt: erst löschen, dann prüfen, nur committen wenn
    nichts dazwischenkam. Ein Agentur-Counter ist ein GETEILTER Arbeitsplatz —
    ein fehlgeschlagener Re-Auth darf dort nie die vorherige Agentur gebunden
    lassen, sonst sieht der nächste Reiseprofi fremde Buchungen, Provisionen und
    Endkunden-PII.
    """
    data = kunden_auth.coerce_json_body(None, kunden_auth.read_capped_body(request))
    authenticated, session_id = agentur_auth.authenticate(
        data,
        request.headers.get("User-Agent", ""),
        request.headers.get("Origin", ""),
    )
    if session_id is None:
        return abort(400, "No session_id provided")
    return {"authenticated": authenticated}


# --- End Agentur-Modus auth ---


# --- Dashboard routes ---

# Each dashboard route carries its own limit: `default_limits` is empty, so a
# route registered without one is not rate limited at all.
_limited_views: dict = {}
for route, view_func, methods, limit in dashboard.routes:
    # Wrap each view ONCE: the decorator returns a new object every call, and
    # Flask refuses two different functions under one endpoint name — which is
    # what /dashboard and /dashboard/ sharing a view would otherwise produce.
    limited = _limited_views.setdefault(
        view_func, limiter.limit(limit, exempt_when=rate_limit.is_loopback)(view_func)
    )
    app.add_url_rule(route, view_func=limited, methods=methods)

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
    # FAQs aus Supabase statt des committeten Snapshots; Fehler: Snapshot bleibt.
    faq_sync.load()
    sitemap_sync.start_scheduler().add_job(
        faq_sync.sync,
        "cron",
        hour=2,
        minute=5,
        id="faq-sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    travel_index.start_scheduler()
    # 04:00, after the sitemap sync (02:00) and the travel-index rebuild
    # (03:00): the country prior reads that index.
    quality_job.start_scheduler(lambda month: dashboard.fetch_month_chats(month)[0])

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
        # Den Monats-Cache gleich mitfuellen. Sonst zahlt der erste Besucher
        # nach jedem Deploy den vollen Tabellenlauf im Request — und genau der
        # ist schon einmal in den Postgres-Statement-Timeout gelaufen, also war
        # es nicht nur langsam, sondern ein 500er.
        try:
            dashboard.warm_cache()
        except Exception as e:
            print(f"[app] startup dashboard warm failed: {e}")

    threading.Thread(target=_startup_warm, name="startup-warm", daemon=True).start()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
