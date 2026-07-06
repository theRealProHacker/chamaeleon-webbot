"""Rate limiting for the chatbot endpoint, built on flask-limiter.

Keyed by client IP (the real IP via X-Forwarded-For behind the Railway proxy,
thanks to ProxyFix). Local/loopback requests are exempt. When a client exceeds
the limit the rejection is returned as an HTTP 200 SSE ``error`` event so the
chat widget renders its error message, and the rejected turn is still logged
for the audit trail.

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

MESSAGE_LIMIT = "15 per hour"

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
    """Render the rejection as an HTTP 200 SSE ``error`` event (so the chat
    widget shows its error message) and log the rejected turn."""
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
        # Rate limiting ausgesetzt (2026-07-06): enabled=True setzen, um das
        # 15/h-Limit wieder scharf zu schalten.
        enabled=False,
    )
    app.register_error_handler(RateLimitExceeded, _on_rate_limit)
    return limiter
