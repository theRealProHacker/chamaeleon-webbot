"""Rate limiting for the chatbot endpoint, built on flask-limiter.

Keyed by client IP (the real IP via X-Forwarded-For behind the Railway proxy,
thanks to ProxyFix). Local/loopback requests are exempt.

The rejection is rendered per endpoint, because flask-limiter rejects in
``before_request`` and the view body never runs (see ``_on_rate_limit``):

- ``/chat/stream`` → HTTP 200 with an SSE ``error`` event, so the chat widget
  renders its error message; the rejected turn is still logged for the audit
  trail.
- ``/kunde/auth`` → JSON ``{"authenticated": false}`` with status 429, **and the
  Kunden-Modus binding is cleared**. Skipping that unbind would make the
  endpoint fail open, which is exploitable from a shared egress IP.

In-memory storage is correct only with a single worker process
(WEB_CONCURRENCY=1, as deployed); with more workers the counters would not be
shared.

Usage from app.py::

    import rate_limit
    limiter = rate_limit.init_app(app)

    @app.route("/chat/stream", methods=["POST"])
    @limiter.limit(rate_limit.MESSAGE_LIMIT, exempt_when=rate_limit.is_loopback)
    def chat_stream():
        ...
"""

import json
import time

from flask import Response, request
from flask_limiter import Limiter, RateLimitExceeded
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from db_logging import log_messages, log_queue

MESSAGE_LIMIT = "200 per hour"

# Flask endpoint names of the auth routes, mapped to the module that owns each
# one's bindings. A 429 on either must still clear that binding — see
# _on_rate_limit. Was a bare string compared with ==; Agentur-Modus added a
# second auth route with exactly the same requirement.
#
# The agentur VIEW is named agentur_auth_route, but its ENDPOINT is
# "agentur_auth" — see app.py. Keys here are endpoints.
AUTH_ENDPOINTS = {
    "kunde_auth": "kunden_auth",
    "agentur_auth": "agentur_auth",
}

# Back-compat alias: the Kunden-Modus endpoint name is still referenced as a
# single value where a route decorator needs one.
AUTH_ENDPOINT = "kunde_auth"


def _unbind_rate_limited_session(endpoint: str) -> None:
    """Drop the binding for a rate-limited auth request on ``endpoint``.

    Imported lazily: the auth modules are only needed on this path, and a
    module-level import would be a needless cycle risk.
    """
    module_name = AUTH_ENDPOINTS.get(endpoint)
    if not module_name:
        return
    try:
        import importlib

        auth = importlib.import_module(module_name)

        # Same reason as the view: get_json returns None for a text/plain body,
        # and losing the session_id here means the 429 silently fails open —
        # which is the exact hole this handler exists to close. Capped, because
        # this is the REJECTION path: it must stay cheaper than the work it is
        # refusing, or the limiter becomes the amplifier.
        #
        # coerce_json_body/read_capped_body live in kunden_auth and are shared;
        # agentur_auth has no reason to duplicate them.
        import kunden_auth

        data = kunden_auth.coerce_json_body(
            None, kunden_auth.read_capped_body(request)
        )
        session_id = data.get("session_id")
        if isinstance(session_id, str):
            auth.unbind(session_id)
    except Exception as exc:  # never let this break the rejection response
        print(f"Error unbinding rate-limited auth request: {exc}")

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def is_loopback() -> bool:
    """True for local requests (dev), which are exempt from rate limiting.

    Behind the Railway proxy remote_addr is the real client IP (never
    loopback), so production traffic is never exempt.
    """
    return request.remote_addr in ("127.0.0.1", "::1")


def _log_rejected_turn() -> None:
    """Persist the rejected user turn so the cap leaves an audit trail."""
    try:
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id")
        messages = data.get("messages") or []
        if session_id and messages:
            rejected = dict(messages[-1])
            rejected["timestamp"] = time.time()
            log_queue.put(lambda: log_messages(session_id, [rejected]))
    except Exception as exc:  # never let audit logging break the response
        print(f"Error logging rate-limited request: {exc}")


def _on_rate_limit(_exc: RateLimitExceeded) -> Response:
    """Render the rejection for whichever endpoint was throttled.

    flask-limiter rejects in ``before_request``, so the view body NEVER runs on a
    429. For the auth routes that is a security problem, not just a UX one: the
    route's first act is ``unbind(session_id)``, and skipping it leaves the
    previous customer (or agency) bound. Someone sharing the victim's egress IP
    (office, hotel, café NAT, an agency's own office NAT) could then burn the
    hourly budget on purpose to guarantee every re-auth from that IP fails OPEN.
    So the binding is cleared here instead, before the rejection is rendered.
    """
    if request.endpoint in AUTH_ENDPOINTS:
        _unbind_rate_limited_session(request.endpoint)
        # JSON, not SSE: the widget parses this response as JSON, and a 200
        # text/event-stream body would read as "not logged in" or throw.
        return Response(
            json.dumps({"authenticated": False}),
            status=429,
            mimetype="application/json",
        )

    _log_rejected_turn()

    def generate_limited():
        payload = {"type": "error", "data": "rate_limited"}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return Response(
        generate_limited(),
        status=200,
        mimetype="text/event-stream",
        headers=_SSE_HEADERS,
    )


def init_app(app) -> Limiter:
    """Wire rate limiting into ``app`` and return the Limiter to decorate routes."""
    # Trust one proxy hop (Railway) so remote_addr is the real client IP.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
        # Global 200/h für ALLE Routen (Owner-Entscheidung 2026-08-02, hebt die
        # Kunden-Routen mit an). Grund für die Anhebung von 100 auf 200: ein
        # Agentur-Counter sitzt hinter EINER Büro-NAT — mehrere Reiseprofis
        # teilen sich dort eine IP, und der Limiter zählt pro IP. 100/h war für
        # einen einzelnen Kunden großzügig, für ein Vertriebsteam nicht.
        # Weiterhin großzügig genug, dass echte Gespräche nie anstoßen (das
        # 15/h-Limit war das False-Positive-Problem), und dennoch eine Bremse
        # gegen kunden_id-Enumeration und generellen Missbrauch.
        # Nach dem Deploy die Logs auf rate_limited-Events beobachten.
        enabled=True,
    )
    app.register_error_handler(RateLimitExceeded, _on_rate_limit)
    return limiter
