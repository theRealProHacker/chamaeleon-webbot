"""Tests for Kunden-Modus auth (kunden_auth.py).

ss.php is monkeypatched — no live requests. Deliberately never imports ``app``
(importing it triggers live Supabase reads). Focus is the security-critical
behaviour: extract exactly the Kundennummer, never the password hash; fail
closed on anything unexpected; a failed re-auth never leaves the previous
customer bound; the binding is a bearer token with a TTL.

ALL sample data here is FABRICATED. A real ss.php dump carries a live password
hash and salt — it must never enter this repo (see docs/kunden-auth-plan.md).
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import time

import kunden_auth as ka

# --- fixtures ----------------------------------------------------------------

# FABRICATED ss.php dump. Shape copied from the real endpoint, values invented:
# the fields we need plus the ones that must NEVER be picked up (internal
# address id, password hash, salt).
DUMP_EINGELOGGT = """<pre>Array
(
    [LIC_RECHTE] => 1
    [SESSION_ADRID] => 100001
    [SESSION_ADRKUNDENNR] => 999999999
    [SESSION_ADRTYPE] => K
    [SESSION_ADRNAME] => Musterfrau
    [SESSION_ADREMAIL] => erika.musterfrau@example.invalid
    [SESSION_ADRPASSWORT] => 1111111111111111111111111111111111111111
    [SESSION_ADRSALT] => 2222222222222222222222222222222222222222
)
</pre>"""

# Unknown or fresh PHPSESSID. Verified live 2026-07-29: ss.php answers a
# cookie-less request with exactly this, 21 bytes. This is the fail-closed case —
# a forged or expired token produces no SESSION_ADRKUNDENNR and binds nothing.
DUMP_ANONYM = """<pre>Array
(
)
</pre>"""

# An anonymous session that has actually BROWSED the site. Also verified live
# 2026-07-29: 175 keys / 4843 bytes, all of them server-set (CMS licence flags,
# static SQL constants) — no user-supplied input reached it under four probe
# vectors, and no SESSION_ADR* keys while logged out. FABRICATED here, but the
# shape is faithful, including the nested sub-array: real dumps do nest, which is
# what made the old `^\\s*` indent anchor unsafe.
DUMP_ANONYM_BROWSING = """<pre>Array
(
    [SQLWHERELISTREISEN] => REIKATID not in (0,0,0)
    [LIC_RECHTE] => 1
    [LIC_MERKZETTEL] => 1
    [SOME_GROUP] => Array
        (
            [NESTED_KEY] => 0
        )
    [COOKIE_OK] => 1
)
</pre>"""

# Valid-shaped PHP session id (fabricated).
SID = "abcdef0123456789abcdef0123"


class _FakeResp:
    def __init__(self, body: str, status: int = 200):
        self.content = body.encode("ISO-8859-1")
        self.status_code = status


def _patch_get(monkeypatch, resp=None, exc=None, spy=None):
    def fake_get(url, cookies=None, headers=None, timeout=None, allow_redirects=None):
        if spy is not None:
            spy.update(
                url=url,
                cookies=cookies,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(ka.requests, "get", fake_get)


# --- extraction --------------------------------------------------------------


def test_extract_picks_kundennr_not_id_or_hash():
    kid = ka.extract_kundennr(DUMP_EINGELOGGT)
    assert kid == "999999999"
    # never the internal address id or the password hash/salt
    assert kid != "100001"
    assert "1111111111" not in kid
    assert "2222222222" not in kid


def test_extract_empty_dump_is_blank():
    assert ka.extract_kundennr(DUMP_ANONYM) == ""


def test_extract_blank_for_a_populated_anonymous_session():
    """A browsing anonymous session is full of keys — none of them ours.

    The empty-dump case above is only what an UNKNOWN token returns; a session
    that has browsed carries ~175 server-set keys. Fail-closed has to hold for
    both, and this one also exercises the nested sub-array.
    """
    assert ka.extract_kundennr(DUMP_ANONYM_BROWSING) == ""


def test_extract_garbage_is_blank():
    assert ka.extract_kundennr("nonsense without the key") == ""


def test_extract_rejects_ambiguous_dump():
    """Two SESSION_ADRKUNDENNR lines = shadowing attempt, not a coin flip.

    print_r emits session values raw, so a field serialised before the real key
    (or anything echoed above the <pre>) could smuggle in a second one and bind
    us to another customer's Kundennummer.
    """
    shadowed = DUMP_EINGELOGGT.replace(
        "    [LIC_RECHTE] => 1",
        "    [LIC_RECHTE] => 1\n    [SESSION_ADRKUNDENNR] => 111111",
    )
    assert ka.extract_kundennr(shadowed) == ""


# --- verify (fail closed) ----------------------------------------------------


def test_verify_returns_kundennr_when_logged_in(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_EINGELOGGT))
    assert ka.verify_meinchamaeleon_session(SID) == "999999999"


def test_verify_forwards_token_as_cookie_with_browser_ua(monkeypatch):
    """The token rides as a PHPSESSID cookie; the UA must not say python-requests."""
    spy = {}
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_EINGELOGGT), spy=spy)
    ka.verify_meinchamaeleon_session(SID)
    assert spy["cookies"] == {"PHPSESSID": SID}
    assert "Mozilla" in spy["headers"]["User-Agent"]
    # Never follow redirects: a dict-passed cookie gets domain="", which matches
    # EVERY host, so a 3xx would hand this password-grade token to the redirect
    # target (cleartext on an http hop) and let it feed extract_kundennr.
    assert spy["allow_redirects"] is False

    # A forwarded customer UA wins (needed if the PHP session is UA-bound).
    ka.verify_meinchamaeleon_session(SID, "CustomerBrowser/1.0")
    assert spy["headers"]["User-Agent"] == "CustomerBrowser/1.0"


def test_verify_rejects_malformed_token_without_calling_ss(monkeypatch):
    """Bad token shapes never reach an outbound Cookie header."""
    called = {"n": 0}

    def fake_get(*a, **kw):
        called["n"] += 1
        return _FakeResp(DUMP_EINGELOGGT)

    monkeypatch.setattr(ka.requests, "get", fake_get)

    for bad in ("", "short", "bad\r\nInjected: header", "has spaces here!!", None, 123):
        assert ka.verify_meinchamaeleon_session(bad) is None
    assert called["n"] == 0


def test_verify_none_when_anonymous(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_ANONYM))
    assert ka.verify_meinchamaeleon_session(SID) is None


def test_verify_none_on_non_200(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_EINGELOGGT, status=500))
    assert ka.verify_meinchamaeleon_session(SID) is None


def test_verify_none_on_exception(monkeypatch):
    _patch_get(monkeypatch, exc=RuntimeError("boom"))
    assert ka.verify_meinchamaeleon_session(SID) is None


# --- binding (session_id as bearer token) ------------------------------------


def test_bind_and_resolve_roundtrip():
    ka.bind("sess-abc", "999999999")
    assert ka.resolve("sess-abc") == "999999999"


def test_resolve_unknown_session_is_none():
    assert ka.resolve("never-bound-xyz") is None


def test_resolve_empty_session_is_none():
    assert ka.resolve("") is None


def test_bind_ignores_empty_inputs():
    ka.bind("", "999999999")
    ka.bind("sess-empty-kid", "")
    assert ka.resolve("sess-empty-kid") is None


def test_resolve_drops_expired_binding():
    # Write an already-expired entry directly, then resolve must evict + return None.
    ka._bindings["sess-old"] = ("999999999", time.time() - 1)
    assert ka.resolve("sess-old") is None
    assert "sess-old" not in ka._bindings


def test_unbind_is_idempotent():
    ka.bind("sess-unbind", "999999999")
    ka.unbind("sess-unbind")
    ka.unbind("sess-unbind")  # again: must not raise
    ka.unbind("")
    assert ka.resolve("sess-unbind") is None


def test_failed_reauth_does_not_keep_previous_customer(monkeypatch):
    """Regression: shared browser — a failed re-auth must fail CLOSED.

    Customer A authenticates, logs out of the website, and person B opens the
    chat in the same browser within the 12h window, so the widget re-auths with
    the SAME stored session_id. Verification correctly fails; the binding must
    be gone, not left pointing at A.

    ponytail: mirrors the three lines /kunde/auth runs, in order, rather than
    importing app (that triggers live Supabase reads). Catches a broken unbind();
    would not catch someone deleting the unbind() CALL from app.py.
    """
    ka.bind("shared-browser", "999999999")
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_ANONYM))

    ka.unbind("shared-browser")
    kunden_id = ka.verify_meinchamaeleon_session(SID)
    if kunden_id:
        ka.bind("shared-browser", kunden_id)

    assert ka.resolve("shared-browser") is None


def test_extract_ignores_a_nested_key():
    """`^\\s*` matched any indent, so a key nested inside another value counted.

    Needs no attacker at all — a session carrying a sub-array with this key was
    enough to bind Kunden-Modus to it. Top-level print_r keys are indented by
    exactly four spaces.
    """
    nested = """<pre>Array
(
    [CART] => Array
        (
            [SESSION_ADRKUNDENNR] => 400123
        )
    [COOKIE_OK] => 1
)
</pre>"""
    assert ka.extract_kundennr(nested) == ""


def test_extract_ignores_an_unindented_injected_key():
    """A logged-OUT attacker with one hostile value in their own session.

    print_r emits values raw, so a newline inside one looks like a new key. With
    no real key present the exactly-one-match rule does not trip. Anchoring the
    indent does not close this class (an injection can include four spaces too —
    see the module comment and spec §8), but it does close the unindented form.
    """
    hostile = """<pre>Array
(
    [LASTSEARCH] => Kenia
[SESSION_ADRKUNDENNR] => 999999999
)
</pre>"""
    assert ka.extract_kundennr(hostile) == ""


def test_authenticate_runs_the_sequence_in_the_safe_order(monkeypatch):
    """The route's security properties ARE this ordering, so test it directly.

    A mutation run showed the view could clear the binding *after* ss.php, or
    call bind() instead of commit_auth, with every test still green — nothing
    exercised app.py. kunden_auth.authenticate now owns the order.
    """
    ka.bind("order-check", "999999999")
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_ANONYM))

    # Anonymous re-auth: the previous customer must be gone, not merely not-added.
    body = {"session_id": "order-check", "phpsessid": SID}
    authenticated, session_id = ka.authenticate(body)
    assert authenticated is False
    assert session_id == "order-check"
    assert ka.resolve("order-check") is None

    # Logged in: binds.
    _patch_get(monkeypatch, resp=_FakeResp(DUMP_EINGELOGGT))
    authenticated, _ = ka.authenticate(body)
    assert authenticated is True
    assert ka.resolve("order-check") == "999999999"

    # A missing/malformed phpsessid is the documented way to log OUT, and it must
    # still clear the binding rather than leave the last customer in place.
    authenticated, _ = ka.authenticate({"session_id": "order-check"})
    assert authenticated is False
    assert ka.resolve("order-check") is None

    # No usable session_id -> caller 400s, and nothing was touched.
    for body in ({}, {"session_id": ""}, {"session_id": 123}, {"session_id": None}):
        assert ka.authenticate(body) == (False, None)


def test_binding_is_cleared_before_ss_php_is_called(monkeypatch):
    """The clear must happen BEFORE verification, not merely before the commit.

    Checking only the end state is not enough — a mutation that moves
    ``begin_auth`` after ``verify`` passes every outcome assertion, because the
    binding still ends up correct. What it breaks is the ~8s window *during* the
    ss.php call: the previous customer stays resolvable the whole time, so any
    /chat/stream in that window is served their bookings. So assert the ordering
    where it is observable — from inside verification.
    """
    seen = {}

    def spy_verify(phpsessid, user_agent=""):
        seen["binding_during_verify"] = ka.resolve("clear-first")
        return "999999999"

    monkeypatch.setattr(ka, "verify_meinchamaeleon_session", spy_verify)
    ka.bind("clear-first", "111111")

    ka.authenticate({"session_id": "clear-first", "phpsessid": SID})

    assert seen["binding_during_verify"] is None, (
        "the previous customer was still bound while ss.php was being called"
    )
    assert ka.resolve("clear-first") == "999999999"


def test_authenticate_settles_the_inflight_entry_even_if_verify_raises(monkeypatch):
    """`_inflight` claims it never accumulates — make that structural."""

    def boom(*a, **kw):
        raise RuntimeError("greenlet died")

    monkeypatch.setattr(ka, "verify_meinchamaeleon_session", boom)
    try:
        ka.authenticate({"session_id": "inflight-leak"})
    except RuntimeError:
        pass
    assert "inflight-leak" not in ka._inflight


def test_read_capped_body_refuses_an_oversized_body():
    """Unbounded get_data() peaked at ~3x the body size (80 MB -> 242 MB).

    One gevent worker with 1000 connections also proxies the whole website, so
    concurrent large POSTs here would OOM the process and take the site down.
    """

    class _Req:
        def __init__(self, payload: bytes, declare_length=True):
            self.content_length = len(payload) if declare_length else None
            self._payload = payload

        @property
        def stream(self):
            import io

            return io.BytesIO(self._payload)

    small = b'{"session_id": "s"}'
    assert ka.read_capped_body(_Req(small)) == small.decode()
    # declared oversize -> not read at all
    assert ka.read_capped_body(_Req(b"x" * (ka.AUTH_BODY_MAX_BYTES + 1))) == ""
    # chunked (no content_length) -> bounded by the read itself
    big = _Req(b"x" * (ka.AUTH_BODY_MAX_BYTES + 1), declare_length=False)
    assert ka.read_capped_body(big) == ""


def test_coerce_json_body_recovers_form_encodings():
    """sendBeacon/URLSearchParams post form-encoded; losing session_id there
    would 400 before the unbind and leave the previous customer bound."""
    assert ka.coerce_json_body(None, "session_id=S1&phpsessid=abc") == {
        "session_id": "S1",
        "phpsessid": "abc",
    }


def test_superseded_auth_cannot_resurrect_the_previous_customer():
    """Regression: the unbind → verify → bind sequence is not atomic.

    gunicorn runs -k gevent with 1000 worker-connections, so the ss.php call
    yields and two /kunde/auth calls for the SAME stored session_id interleave:

      A (logged in)  begins, ss.php stalls
      B (logged out) begins and finishes  → correctly no binding
      A resumes and writes its bind       → B now resolves to A

    B was told authenticated=False and would still have been served A's Buchungen
    and Zahlstand. The generation token makes the newest auth win instead.
    """
    sid = "shared-browser-race"
    gen_a = ka.begin_auth(sid)
    gen_b = ka.begin_auth(sid)

    assert ka.commit_auth(sid, "", gen_b) is True  # B: anonymous, nothing bound
    assert ka.commit_auth(sid, "111111", gen_a) is False  # A: too late, discarded
    assert ka.resolve(sid) is None


def test_unbind_cancels_an_auth_in_flight():
    """A 429 (or any unbind) must also void an auth still waiting on ss.php."""
    sid = "nat-victim-race"
    gen = ka.begin_auth(sid)
    ka.unbind(sid)  # rate_limit._on_rate_limit path — the view never runs
    assert ka.commit_auth(sid, "999999999", gen) is False
    assert ka.resolve(sid) is None


def test_begin_auth_clears_the_binding_immediately():
    """Not only on commit: the clear has to happen before ss.php is called."""
    ka.bind("sess-begin", "999999999")
    ka.begin_auth("sess-begin")
    assert ka.resolve("sess-begin") is None


def test_extract_rejects_values_that_are_not_kundennummern():
    """parse_kunden_id's allowlist is not an identity check.

    A nested print_r value renders as the literal "Array"; a logged-in
    non-customer session can carry "0". Both clear [A-Za-z0-9_-]{1,32} and would
    otherwise be bound and sent to TourOne with our bearer token.
    """
    for bad in ("Array", "0", "abc", "K"):
        dump = DUMP_EINGELOGGT.replace("999999999", bad)
        assert ka.extract_kundennr(dump) == "", bad
    # the real shape still works
    assert ka.extract_kundennr(DUMP_EINGELOGGT) == "999999999"


def test_phpsessid_regex_rejects_a_trailing_newline(monkeypatch):
    """Python's `$` matches before a trailing \\n — the anchor must be \\Z.

    Otherwise the token reaches requests verbatim and the documented "no CR/LF
    gets near an outbound request" guarantee is only held by accident, further
    down, by http.client refusing the header.
    """
    called = {"n": 0}

    def fake_get(*a, **kw):
        called["n"] += 1
        return _FakeResp(DUMP_EINGELOGGT)

    monkeypatch.setattr(ka.requests, "get", fake_get)

    assert ka.verify_meinchamaeleon_session(SID + "\n") is None
    assert called["n"] == 0


def test_coerce_json_body_recovers_a_text_plain_body():
    """fetch() without an explicit Content-Type sends text/plain.

    Flask's get_json then returns None. If that lost the session_id, /kunde/auth
    would 400 before clearing the binding and leave the previous customer bound —
    a Content-Type header must never be a security control.
    """
    assert ka.coerce_json_body({"session_id": "s"}, "") == {"session_id": "s"}
    assert ka.coerce_json_body(None, '{"session_id": "s"}') == {"session_id": "s"}
    # top-level non-objects would make .get() raise an unauthenticated 500
    for hostile in ("[1,2]", '"hello"', "123", "not json at all", ""):
        assert ka.coerce_json_body(None, hostile) == {}


def test_rate_limited_auth_still_clears_the_binding(monkeypatch):
    """Regression: a 429 on /kunde/auth must NOT fail open.

    flask-limiter rejects in before_request, so the view body — and with it the
    unbind() call — never runs. Without the handler-side unbind, anyone sharing
    the victim's egress IP (office/hotel NAT) could burn the hourly budget on
    purpose and guarantee every re-auth from that IP keeps the previous customer
    bound. Verified against the real rate_limit module, no app import.
    """
    import json

    from flask import Flask
    from flask_limiter import Limiter, RateLimitExceeded
    from flask_limiter.util import get_remote_address

    import rate_limit

    app = Flask(__name__)
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
        enabled=True,
    )
    app.register_error_handler(RateLimitExceeded, rate_limit._on_rate_limit)

    @app.route("/kunde/auth", methods=["POST"])
    @limiter.limit("1 per hour")
    def kunde_auth():  # noqa: D401 — endpoint name must match AUTH_ENDPOINT
        return {"authenticated": False}

    assert kunde_auth.__name__ == rate_limit.AUTH_ENDPOINT

    ka.bind("nat-victim", "123456")
    client = app.test_client()
    client.post("/kunde/auth", json={"session_id": "burn"})  # uses up the budget
    resp = client.post("/kunde/auth", json={"session_id": "nat-victim"})

    assert resp.status_code == 429
    assert resp.mimetype == "application/json"
    assert json.loads(resp.data) == {"authenticated": False}
    # the whole point: the binding is gone even though the view never ran
    assert ka.resolve("nat-victim") is None

    # ...and it must still hold when the widget posts JSON as text/plain (the
    # usual way to dodge the CORS preflight). get_json returns None there, so
    # without coerce_json_body the 429 handler loses the session_id and the
    # rejection silently fails OPEN — the exact hole this handler exists to close.
    ka.bind("nat-victim-textplain", "123456")
    resp = client.post(
        "/kunde/auth",
        data=json.dumps({"session_id": "nat-victim-textplain"}),
        content_type="text/plain;charset=UTF-8",
    )
    assert resp.status_code == 429
    assert ka.resolve("nat-victim-textplain") is None
