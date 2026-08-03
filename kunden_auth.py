"""Kunden-Modus auth: verify the MeinChamäleon login server-side.

The chat widget can no longer assert *who* the customer is. The widget reads its
own ``PHPSESSID`` via ``document.cookie`` on the chamaeleon page (same-origin to
the page) and forwards the value in the ``/kunde/auth`` body; the server replays
it against ``ss.php``, reads ``SESSION_ADRKUNDENNR`` and binds it to the chat
``session_id``. Thereafter ``session_id`` is the bearer token (owner decision
2026-07-28 — see ``docs/kunden-auth-plan.md``). ``/chat/stream`` derives the
``kunden_id`` from the binding, never from the request body, so a spoofed
``kunden_id`` no longer reaches TourOne.

Our API is a DIFFERENT ORIGIN from chamaeleon-reisen.de, so the browser never
attaches the session cookie to our requests on its own — the value has to be
forwarded explicitly. v1 read ``request.cookies`` and was therefore inert.

The forwarded ``PHPSESSID`` is a password-grade credential: ``ss.php`` dumps the
whole session — password hash, salt and full PII — to anyone holding it. It is
used for exactly one call, never logged, never stored, never returned. This
module reads exactly ``SESSION_ADRKUNDENNR`` and nothing else, and never logs
the response body, not even on error.

    POST /kunde/auth {session_id, phpsessid}          → kunden_auth.authenticate()
      │
      ├─ begin_auth(session_id)        ← ALWAYS first: a failed re-auth must
      │                                  never leave the previous customer bound.
      │                                  Returns a generation token.
      ├─ verify(phpsessid, ua) ──► ss.php ──► SESSION_ADRKUNDENNR
      │       └─ bad shape / network / non-200 / no key → None (fail closed)
      └─ commit_auth(session_id, kunden_id, generation)
              └─ writes ONLY if no newer auth (or unbind) intervened, so a slow
                 call cannot resurrect an identity a later one already cleared

``bind()`` is the unconditional primitive and is NOT what the route uses.
"""

import json
import re
from urllib.parse import parse_qs

import requests

import session_binding
from kundendaten import parse_kunden_id

# MeinChamäleon session-introspection endpoint. With the customer's session
# cookies it returns their session (incl. SESSION_ADRKUNDENNR); without → empty.
SS_URL = "https://www.chamaeleon-reisen.de/ss.php"

# Which ss.php to replay the token against, keyed by the browser's Origin.
#
# A PHP session lives in ONE host's session store, and the cookie cannot cross
# registrable domains at all — a PHPSESSID from leon.chamdev.tourone.de is
# meaningless to chamaeleon-reisen.de's ss.php. Verifying every origin against
# production therefore fails closed for anyone testing on a dev host, which is
# exactly what happened on 2026-07-30 (spec §8).
#
# The Origin header is CLIENT-CONTROLLED, so it may only ever be a KEY into this
# table, never the URL itself — otherwise an attacker points verification at an
# ss.php they control and mints any Kundennummer they like. Unknown or absent →
# production. All values are https: the token is password-grade, so an http
# mapping would put it on the wire in cleartext (an http:// origin thus falls
# back to production and fails closed, which is the intended outcome).
#
# The agentur hosts are deliberately absent: Agentur- and Kunden-Modus are
# mutually exclusive (``app.py`` forces ``kunden_id = ""`` on agentur requests),
# so a binding made from those origins could never be read. They live in
# ``agentur_auth.SS_URLS_AGENTUR`` instead — the agt PHPSESSID is a DIFFERENT
# session (the cookie is host-only), so the two tables must not be merged.
#
# Trade-off accepted with this table (owner, 2026-07-30): a login on a dev host
# becomes a valid auth path for the production chat, because the dev widget talks
# to the production backend. Keep in sync with the CORS origins in ``app.py``.
SS_URLS = {
    "https://www.chamaeleon-reisen.de": SS_URL,
    # Bare domain 301s to www; mapped straight to the target because the replay
    # runs with allow_redirects=False and would otherwise fail on the 301.
    "https://chamaeleon-reisen.de": SS_URL,
    "https://leon.chamdev.tourone.de": "https://leon.chamdev.tourone.de/ss.php",
    "https://chamdev.tourone.de": "https://chamdev.tourone.de/ss.php",
}


def ss_url_for_origin(origin) -> str:
    """Pick the ss.php to verify against. Unknown origin → production."""
    if not isinstance(origin, str):
        return SS_URL
    return SS_URLS.get(origin.strip().rstrip("/"), SS_URL)

# ss.php mid-chat must not hang the auth call.
TIMEOUT = 8

# Hard cap on how much of a /kunde/auth body we will read. See read_capped_body.
AUTH_BODY_MAX_BYTES = 8 * 1024

# A datacenter IP announcing "python-requests" is a trivial filter target, so the
# replay looks like a browser. Overridden with the customer's own UA when one is
# forwarded — that is also what makes the replay work if the PHP session turns
# out to be UA-bound (condition C2 — verification steps in docs/kunden-auth-spec.md).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# PHP session ids are alphanumeric (plus , and - at 6 bits/char). Validate before
# the value reaches a Cookie header: this is a trust boundary, and a token with
# CR/LF in it has no business getting near an outbound request.
#
# \A...\Z, NOT ^...$: Python's $ also matches immediately before a trailing
# newline, so "abcdef0123456789\n" passed the old anchor and requests then built
# the header verbatim. It failed closed only by accident (http.client rejects an
# illegal header value, and the bare except below turned that into None). The
# guarantee this regex is documented to give has to be real, not incidental.
_PHPSESSID_RE = re.compile(r"\A[A-Za-z0-9,-]{16,128}\Z")

# What a Kundennummer may look like, checked SEPARATELY from parse_kunden_id.
# parse_kunden_id is the allowlist for a client-sent string — it exists to kill
# path/query injection, so it permits anything in [A-Za-z0-9_-]{1,32}. That is
# far too loose for a value we are about to treat as a verified identity: a
# print_r dump whose key holds a nested array renders as the literal "Array",
# and a non-customer session can carry "0". Both would pass the allowlist and
# bind Kunden-Modus to a nonsense id, which then goes to TourOne with our bearer
# token. Real Kundennummern are digits.
_KUNDENNR_RE_VALUE = re.compile(r"\A[0-9]{4,12}\Z")

# Binding lifetime. session_id is the bearer token; the TTL bounds two things:
# how long a LIFTED token stays usable and how long a logged-out customer stays
# authed in an already-open chat.
#
# "Lifted", not "guessed" — the distinction is measured, not assumed. The widget
# mints session_id with Math.random(), which looks alarming for a credential, so
# it was quantified (2026-07-29, full working in docs/kunden-auth-plan.md):
# V8 emits k·2^-52, the token is floor(k·36^9/2^52), the most probable token has
# 45 preimages → H∞ = 46.508 bits, plus 25.36 bits of Date.now() over the 12h
# window ≈ 71.87 bits. A 100k-IP botnet at the 200/h/IP cap (rate_limit.
# MESSAGE_LIMIT — raised from 100 with Agentur-Modus, so the figure halves)
# needs ~2.5e8 YEARS to hit any one of 100 bound sessions. Guessing is not a
# threat and the rate limit is not what stops it. Do NOT spend effort hardening
# the generator; switching to crypto.getRandomValues() is one line of hygiene,
# not a fix.
#
# The real exposure is where the token is STORED and COPIED: it is also the
# Supabase log key (a DB/dashboard reader can lift it — accepted MVP risk), it
# lives in localStorage on a page running third-party tags, and dashboard.py
# serves it. That is what the TTL is bounding.
#
# 12h is the same NUMBER as the message-session window
# (db_logging.SESSION_MESSAGE_EXPIRY_SECONDS) but not the same CLOCK, and the
# difference is visible to customers: this TTL is absolute from the moment of
# commit_auth and is never refreshed by use, whereas db_logging's window is
# rolling (now - last_active). So a customer who authenticates at 08:00 and is
# still chatting at 20:05 silently drops out of Kunden-Modus mid-conversation
# while db_logging still considers the session live — the exact "reads as a
# broken bot" symptom that shortening the TTL was rejected for. Making it roll on
# resolve() would fix that, but it also extends how long a lifted token stays
# usable, so it is a deliberate open question, not an oversight.
#
# Accepted, deliberately (eng review 2026-07-28): logging out of the website does
# NOT end Kunden-Modus in an already-open chat tab, for up to 12h. Shortening the
# TTL was rejected because it downgrades a customer mid-conversation, which reads
# as a broken bot.
#
# CAVEAT — the fail-closed guarantee is CLIENT-TRIGGERED. The server never
# re-verifies on its own; unbind() runs only inside /kunde/auth. So "a fresh chat
# open fails closed" holds ONLY IF the widget calls /kunde/auth unconditionally on
# every open, INCLUDING when it finds no PHPSESSID. If the widget short-circuits
# ("no cookie, don't bother") or the call never lands (offline, adblock, JS error,
# navigation), the prior binding survives and the next person on that browser
# inherits it. The widget is owner-side and not in this repo, so nothing here
# enforces it — it is a contract, not a guarantee. Spelled out as a hard
# requirement in docs/kunden-auth-spec.md §5(a).
BINDING_TTL = 12 * 60 * 60

# print_r line, e.g. "    [SESSION_ADRKUNDENNR] => 123456". SESSION_ADRID is the
# internal address id, NOT the Kundennummer — match the exact key.
#
# Indent is EXACTLY four spaces, not \s*. print_r indents the outer array's own
# keys by four and everything deeper by more, so `^\s*` also matched a key nested
# inside another value — no attacker needed, just a session that happens to carry
# a sub-array with this key:
#
#     [CART] => Array
#         (
#             [SESSION_ADRKUNDENNR] => 400123   ← was accepted as top-level
#         )
#
# ⚠ RESIDUAL, NOT CLOSED BY THIS REGEX — see docs/kunden-auth-spec.md §8.
# print_r emits session values raw and unquoted, so a value containing a newline
# is indistinguishable from a new key. An attacker who can get
# "\n    [SESSION_ADRKUNDENNR] => <victim>\n" into any $_SESSION value the site
# persists from user input, while logged OUT (so no real key exists to trip the
# exactly-one-match check), binds as that victim. No regex over print_r can fix
# that; it needs ss.php to stop dumping raw session state, or the signed-token
# transport in spec §7.
_KUNDENNR_RE = re.compile(
    r"^ {4}\[SESSION_ADRKUNDENNR\][ \t]*=>[ \t]*(.+?)[ \t]*$", re.MULTILINE
)

# The binding state, and the generation machinery that makes it safe under
# concurrent auths, live in session_binding — Agentur-Modus needs exactly the
# same thing with a different TTL and a different identity kind. The rationale
# for every rule enforced there (why unbind must run first, why a generation is
# claimed up front, why the newest auth wins) is in that module's docstring.
#
# TTL is passed as a callable so BINDING_TTL above stays the single source of
# truth: the store reads it at bind time rather than snapshotting it at import.
_store = session_binding.new_store(lambda: BINDING_TTL)

# Live aliases onto the store's own dicts, kept because the tests and rate_limit
# read them directly. Safe ONLY because nothing ever reassigns store["bindings"]
# or store["inflight"] — they are mutated in place, never swapped. That is the
# whole reason session_binding is functions over a dict and not a class.
_bindings: dict[str, tuple[str, float]] = _store["bindings"]
_inflight: dict[str, int] = _store["inflight"]


def extract_kundennr(body: str) -> str:
    """Pull SESSION_ADRKUNDENNR out of an ss.php print_r dump; "" if absent.

    Reads ONLY this field. The dump also carries the password hash, salt and
    full PII — none of it is returned, retained or logged. The value is run
    through the same allowlist as the old client-sent id (``parse_kunden_id``),
    which also kills path/query injection into the authenticated TourOne call.
    """
    # findall, not search: print_r emits session values raw (not HTML-escaped),
    # so a field serialised BEFORE this key — or anything echoed above the <pre>
    # — that itself contains "[SESSION_ADRKUNDENNR] => …" would shadow the real
    # value and bind us to someone else's Kundennummer. Ambiguity is a failure,
    # not a coin flip: demand exactly one match.
    matches = _KUNDENNR_RE.findall(body)
    if len(matches) != 1:
        if len(matches) > 1:
            print("[kunden_auth] ss.php dump had multiple SESSION_ADRKUNDENNR — rejected")
        return ""
    kunden_id = parse_kunden_id(matches[0])
    # Second gate, deliberately stricter than the allowlist: the allowlist only
    # proves the string is safe to put in a URL, not that it identifies a
    # customer. "Array" (nested print_r value) and "0" (logged in but not a
    # customer — agency or guest session) both clear it, and either would bind
    # Kunden-Modus and then be sent to TourOne with our bearer token.
    if not _KUNDENNR_RE_VALUE.match(kunden_id):
        if kunden_id:
            print("[kunden_auth] SESSION_ADRKUNDENNR is not a Kundennummer — rejected")
        return ""
    return kunden_id


def verify_meinchamaeleon_session(
    phpsessid, user_agent: str = "", origin: str = ""
) -> str | None:
    """Return the logged-in customer's Kundennummer, or ``None``. Never raises.

    ``phpsessid`` is the value the widget read from ``document.cookie`` on the
    chamaeleon page and forwarded in the request body — NOT something the
    browser sent us (different origin). Not logged in → empty session dump → no
    SESSION_ADRKUNDENNR → ``None``. Any problem (wrong token shape, network,
    non-200, unparsable) → ``None``, fail closed. Neither the token nor the
    response body is ever logged: the body carries the password hash, salt and
    full PII.

    ``origin`` selects which ss.php the token is replayed against, because a PHP
    session only exists in one host's store (see :data:`SS_URLS`). It is only a
    table key, never a URL — unknown origins verify against production.
    """
    if not isinstance(phpsessid, str) or not _PHPSESSID_RE.match(phpsessid):
        return None
    ss_url = ss_url_for_origin(origin)
    try:
        resp = requests.get(
            ss_url,
            cookies={"PHPSESSID": phpsessid},
            headers={"User-Agent": user_agent or DEFAULT_USER_AGENT},
            timeout=TIMEOUT,
            # NEVER follow redirects. requests builds the jar via
            # cookiejar_from_dict, which sets domain="" — a blank-domain cookie
            # matches EVERY host, so a 3xx out of ss.php (WAF interstitial,
            # maintenance redirect, http:// canonicalisation) would hand this
            # password-grade token to the redirect target, in cleartext on an
            # http hop. It would also make resp.content come from that target,
            # letting it feed extract_kundennr. Anything but a direct 200 fails.
            allow_redirects=False,
        )
    except Exception as e:
        # The URL is safe to log (it is one of our own constants); the token and
        # the body never are.
        print(f"[kunden_auth] {ss_url} request failed: {type(e).__name__}")
        return None
    if resp.status_code != 200:
        print(f"[kunden_auth] {ss_url} returned status {resp.status_code}")
        return None
    body = resp.content.decode("ISO-8859-1", errors="replace")
    return extract_kundennr(body) or None


def unbind(session_id: str) -> None:
    """Drop any binding for this session and cancel any auth in flight for it.

    The widget keeps the same session_id in localStorage for up to 12h, so
    without this a failed re-auth would silently leave the previous customer
    bound: on a shared browser (household, office, hotel) person B would open the
    chat, verification would correctly fail because nobody is logged in, and B
    would still be served person A's bookings and Zahlstand — the exact IDOR this
    module closes.

    Dropping the in-flight entry is what makes that hold under concurrency: a
    slower auth that is still waiting on ss.php can no longer commit its result
    afterwards, because its generation is gone. Used by the 429 path in
    rate_limit.py, which has to clear the binding without running the view.
    """
    session_binding.unbind(_store, session_id)


def begin_auth(session_id: str) -> int:
    """Clear the binding and claim a generation for the auth about to run.

    Every /kunde/auth starts here, so every failure path below it ends anonymous
    rather than as whoever used the browser before. The returned token must be
    handed to :func:`commit_auth`; that is what stops a slow auth from resurrecting
    an identity a later one already cleared (see ``_inflight``).
    """
    return session_binding.begin(_store, session_id)


def commit_auth(session_id: str, kunden_id: str, generation: int) -> bool:
    """Write this auth's result, unless a newer auth superseded it.

    Returns True if this call still owned the session. False means another
    /kunde/auth for the same session_id started (or an unbind ran) while we were
    waiting on ss.php — its outcome is authoritative and ours is discarded.
    """
    return session_binding.commit(_store, session_id, kunden_id, generation)


def authenticate(
    body: dict, user_agent: str = "", origin: str = ""
) -> tuple[bool, str | None]:
    """The whole /kunde/auth sequence, in the one order that is safe.

    Returns ``(authenticated, session_id)``; ``session_id`` is None when the body
    carried no usable one, which is the caller's cue to 400 — and the only case
    where nothing had to be cleared first.

    ``origin`` is the request's ``Origin`` header, used only to pick the ss.php
    host (:data:`SS_URLS`). It comes from the request rather than the body so the
    widget cannot choose it independently of where the page actually runs.

    This lives here rather than in the view on purpose. Every security property
    of this endpoint is a property of the ORDER these calls happen in, and a
    mutation test showed the view could be rewritten to clear the binding after
    ss.php, or to call bind() directly, with the whole suite still passing —
    because `import app` pulls in live Supabase reads, so nothing tests the view.
    Keeping the order here makes it testable without that.

    The ``finally`` is what makes the "``_inflight`` never accumulates" claim
    structural rather than aspirational: an in-flight generation is settled even
    if verification blows up.
    """
    session_id = body.get("session_id")
    if not session_id or not isinstance(session_id, str):
        return False, None

    # Always first: every failure path below ends anonymous, not as whoever used
    # this browser before.
    generation = begin_auth(session_id)
    kunden_id = ""
    try:
        kunden_id = (
            verify_meinchamaeleon_session(
                body.get("phpsessid", ""), user_agent, origin
            )
            or ""
        )
    finally:
        committed = commit_auth(session_id, kunden_id, generation)

    if not committed:
        print("[kunden_auth] superseded by a newer auth for the same session")
    authenticated = bool(kunden_id) and committed
    # Only prod visibility for Kunden-Modus: ungated, but once per chat open
    # rather than per message. Never the Kundennummer, never the token.
    print(f"[kunden_auth] authenticated={authenticated}")
    return authenticated, session_id


def bind(session_id: str, kunden_id: str) -> None:
    """Bind a verified kunden_id to this session (no-op on empty inputs).

    Unconditional low-level primitive. ``/kunde/auth`` must NOT use this — it
    goes through begin_auth/commit_auth so a superseded auth cannot overwrite a
    newer one.
    """
    session_binding.bind(_store, session_id, kunden_id)


def read_capped_body(req, max_bytes: int = AUTH_BODY_MAX_BYTES) -> str:
    """At most ``max_bytes`` of the raw request body, as text. Never raises.

    Bounded on purpose. Reading the body unconditionally is what lets a
    Content-Type header stop being a security control (see coerce_json_body) —
    but ``request.get_data()`` buffers the WHOLE body and decoding it peaks near
    3x its size (measured: an 80 MB body → 242 MB). ``MAX_CONTENT_LENGTH`` is
    unset, gunicorn runs one gevent worker with 1000 connections, and that worker
    also proxies the entire website, so a handful of concurrent large POSTs to
    this route would OOM the process and take the site down with it.

    A legitimate body here is ``{"session_id": …, "phpsessid": …}`` — about 150
    bytes. The cap is fifty times that.
    """
    try:
        length = req.content_length
        if length is not None and length > max_bytes:
            return ""
        # Chunked bodies report no length, so bound the read itself and reject
        # anything that fills it rather than truncating into a half-parse.
        chunk = req.stream.read(max_bytes + 1)
        if len(chunk) > max_bytes:
            return ""
        return chunk.decode("utf-8", errors="replace")
    except Exception:
        return ""


def coerce_json_body(parsed, raw_text: str) -> dict:
    """Best-effort dict from a request body. Never raises.

    ``request.get_json(silent=True)`` returns None for ANY body whose media type
    is not JSON, and a bare ``fetch(url, {method:'POST', body: ...})`` sends
    ``text/plain`` — the standard way a frontend avoids a CORS preflight. If we
    let that lose the session_id, ``/kunde/auth`` 400s *before* clearing the
    binding, and the previous customer stays bound on a shared browser. That
    turns a Content-Type header into a security control, which it must not be.

    It also normalises a top-level JSON array/string/number, which ``.get()``
    would otherwise turn into an unauthenticated 500 — again without unbinding.

    Form encodings are covered too: ``navigator.sendBeacon(url, formData)`` and a
    ``URLSearchParams`` body are realistic ways for a widget to send this, and
    both would otherwise be dropped for exactly the reason above. Multipart is
    not parsed — implausible for a two-field body, and it would need the
    unbounded parser this module exists to avoid.
    """
    if isinstance(parsed, dict):
        return parsed
    raw_text = raw_text or ""
    try:
        recovered = json.loads(raw_text)
        if isinstance(recovered, dict):
            return recovered
    except Exception:
        pass
    if "=" in raw_text:
        try:
            return {k: v[0] for k, v in parse_qs(raw_text, keep_blank_values=True).items()}
        except Exception:
            return {}
    return {}


def resolve(session_id: str) -> str | None:
    """Verified kunden_id for this session, or ``None`` (never bound / expired)."""
    return session_binding.resolve(_store, session_id)
