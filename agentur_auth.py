"""Agentur-Modus auth: verify the agt.chamaeleon-reisen.de login server-side.

Same shape as :mod:`kunden_auth`, same trust boundary, different session key and
a different identity kind. The widget on an agt page reads its own ``PHPSESSID``
via ``document.cookie`` and forwards it in the ``/agentur/auth`` body; the server
replays it against ``ss.php``, reads ``SESSION_AGTNR`` and binds that
Agenturnummer to the chat ``session_id``. Thereafter ``session_id`` is the bearer
token and ``/chat/stream`` derives the Agenturnummer from the binding, never from
the request body.

    POST /agentur/auth {session_id, phpsessid}      → agentur_auth.authenticate()
      │
      ├─ begin_auth(session_id)        ← ALWAYS first: a failed re-auth must
      │                                  never leave the previous agency bound.
      ├─ verify(phpsessid, ua) ──► ss.php ──► SESSION_AGTNR
      │       └─ bad shape / network / non-200 / no key → None (fail closed)
      └─ commit_auth(session_id, agentur_id, generation)
              └─ writes ONLY if no newer auth (or unbind) intervened.

⚠ UNVERIFIED TRANSPORT. ``docs/kunden-auth-spec.md`` M3/M4 confirmed for `www`
only, on 2026-07-29. Nobody has checked whether the **agt** ``PHPSESSID`` is
readable from ``document.cookie`` — if it is ``HttpOnly``, this module is dead
code and the answer is the signed-token transport in spec §7. What IS confirmed
(2026-07-31) is that ``ss.php`` answers 200 anonymously and that a browser-viewed
logged-in agt session carries ``SESSION_AGTNR``. Owner chose to build ahead of
the check; see plan Risk 1 / task T1.

⚠ The ss.php dump for an agency session is MORE dangerous than a customer one:
it carries the password hash, salt, IBAN, USt-IdNr and a plaintext password in
``SESSION_AGTBEZ``. Exactly one field is read here. The body is never logged,
never stored, never returned — and no real dump may ever be committed as a test
fixture.
"""

import re

import requests

import session_binding
from kunden_auth import DEFAULT_USER_AGENT, TIMEOUT, _PHPSESSID_RE

# Eigene Origin→ss.php-Tabelle, NICHT kunden_auth.SS_URLS.
#
# Das PHPSESSID-Cookie wird host-only gesetzt (`path=/`, kein `Domain`), also ist
# ein www-Token für agts ss.php bedeutungslos und umgekehrt. Ein Replay gegen den
# falschen Host schlägt nicht laut fehl — er liefert 200 mit einer LEEREN Session,
# also „nicht eingeloggt", und die Agentur bekäme dauerhaft `authenticated:false`
# ohne jeden Hinweis auf die Ursache.
#
# Die agt-Hosts gehören bewusst NICHT in kunden_auth.SS_URLS: dort ist ihr Fehlen
# selbst eine Kontrolle (Kunden- und Agentur-Modus schließen sich aus, app.py
# erzwingt `kunden_id = ""` auf Agentur-Requests). Beide Tabellen bleiben getrennt.
#
# ss.php ist auf beiden Hosts vorhanden — verifiziert 2026-07-30: 200, anonym
# `Array ( )`. Alle Werte https: der Token ist passwortgleich.
SS_URLS_AGENTUR = {
    "https://agt.chamaeleon-reisen.de": "https://agt.chamaeleon-reisen.de/ss.php",
    "https://agt.chamdev.tourone.de": "https://agt.chamdev.tourone.de/ss.php",
}

# Kein Fallback auf Produktion wie im Kundenpfad: ein unbekannter Origin hat hier
# keine plausible ss.php, und gegen den falschen Host zu replayen heißt, einen
# passwortgleichen Token an einen Host zu schicken, der ihn nicht ausgestellt hat.
# Unbekannt → "" → fail closed, ohne Request.
def ss_url_for_origin(origin) -> str:
    """agt-ss.php für diesen Origin, sonst "" (kein Request)."""
    if not isinstance(origin, str):
        return ""
    return SS_URLS_AGENTUR.get(origin.strip().rstrip("/"), "")

# Binding lifetime, deliberately its own constant rather than a reuse of
# kunden_auth.BINDING_TTL (D-A4). An agency counter is a shared workstation with
# staff turnover through the day; a customer's own browser is not. The two have
# no reason to move together.
AGENTUR_BINDING_TTL = 12 * 60 * 60

# print_r line, e.g. "    [SESSION_AGTNR] => 12345".
#
# The bracket anchoring is load-bearing, MORE than on the customer path: the same
# session carries SESSION_AGTALTAGTNR, SESSION_AGTNEUAGTNR, SESSION_AGTCRSTOMAAGTNR
# and SESSION_AGTCRSMERLINAGTNR — four other keys *ending* in AGTNR. A substring
# match binds the wrong agency (the "old" or "new" number after a migration).
# Match the full bracketed key, never a suffix.
#
# Indent is EXACTLY four spaces, not \s*: print_r indents the outer array's own
# keys by four and everything deeper by more, so `^\s*` would also match a key
# nested inside another value.
#
# ⚠ RESIDUAL, NOT CLOSED BY THIS REGEX — same as kunden_auth, see
# docs/kunden-auth-spec.md §8. print_r emits session values raw and unquoted, so
# a value containing a newline is indistinguishable from a new key.
_AGTNR_RE = re.compile(
    r"^ {4}\[SESSION_AGTNR\][ \t]*=>[ \t]*(.+?)[ \t]*$", re.MULTILINE
)

# What an Agenturnummer may look like. This is a URL-safety check ONLY: the value
# is about to be interpolated into a query parameter on an authenticated TourOne
# call, so it must not carry path or query characters.
#
# It is deliberately NOT an existence check and deliberately has NO length floor
# (owner, 2026-08-02). The value comes from a session we already trust — checking
# it against /get/agentur?agenturNr= would validate an already-trusted number,
# add a fail-closed lockout path when that endpoint 500s (it does, on non-numeric
# input), and do nothing at all about the real threat, which is print_r forgery.
# The leading-zeros rejection is there because "0"/"00" is what a non-agency
# session renders when the key exists but is unset.
_AGTNR_RE_VALUE = re.compile(r"\A(?!0+\Z)[0-9]{1,12}\Z")

_store = session_binding.new_store(lambda: AGENTUR_BINDING_TTL)

# Live aliases onto the store's own dicts — see kunden_auth for why this is safe
# (nothing ever reassigns them; they are mutated in place).
_bindings: dict[str, tuple[str, float]] = _store["bindings"]
_inflight: dict[str, int] = _store["inflight"]


def extract_agenturnr(body: str) -> str:
    """Pull SESSION_AGTNR out of an ss.php print_r dump; "" if absent.

    Reads ONLY this field. The dump also carries the password hash, salt, IBAN,
    USt-IdNr and a plaintext password — none of it is returned, retained or
    logged.
    """
    # findall, not search: print_r emits session values raw (not HTML-escaped),
    # so a field serialised BEFORE this key that itself contains
    # "[SESSION_AGTNR] => …" would shadow the real value and bind us to another
    # agency. Ambiguity is a failure, not a coin flip: demand exactly one match.
    matches = _AGTNR_RE.findall(body)
    if len(matches) != 1:
        if len(matches) > 1:
            print("[agentur_auth] ss.php dump had multiple SESSION_AGTNR — rejected")
        return ""
    value = matches[0].strip()
    if not _AGTNR_RE_VALUE.match(value):
        return ""
    return value


def verify_agentur_session(
    phpsessid, user_agent: str = "", origin: str = ""
) -> str | None:
    """Return the logged-in agency's Agenturnummer, or ``None``. Never raises.

    Fails closed on every problem: wrong token shape, network, non-200,
    unparsable, key absent, value not URL-safe. Neither the token nor the
    response body is ever logged.
    """
    if not isinstance(phpsessid, str) or not _PHPSESSID_RE.match(phpsessid):
        return None
    ss_url = ss_url_for_origin(origin)
    if not ss_url:
        print(f"[agentur_auth] kein agt-ss.php für Origin {origin!r} — abgelehnt")
        return None
    try:
        resp = requests.get(
            ss_url,
            cookies={"PHPSESSID": phpsessid},
            headers={"User-Agent": user_agent or DEFAULT_USER_AGENT},
            timeout=TIMEOUT,
            # NEVER follow redirects — a 3xx would hand this password-grade token
            # to the redirect target, because requests' cookiejar_from_dict sets
            # domain="" and a blank-domain cookie matches every host. See the
            # same guard in kunden_auth for the full reasoning.
            allow_redirects=False,
        )
    except Exception as e:
        print(f"[agentur_auth] {ss_url} request failed: {type(e).__name__}")
        return None
    if resp.status_code != 200:
        print(f"[agentur_auth] {ss_url} returned status {resp.status_code}")
        return None
    body = resp.content.decode("ISO-8859-1", errors="replace")
    return extract_agenturnr(body) or None


def unbind(session_id: str) -> None:
    """Drop any binding for this session and cancel any auth in flight for it.

    An agency counter is a shared workstation: without this, a failed re-auth
    would leave the previous agency bound and the next person would be served
    another agency's bookings, commission and end-customer PII.
    """
    session_binding.unbind(_store, session_id)


def begin_auth(session_id: str) -> int:
    """Clear the binding and claim a generation for the auth about to run."""
    return session_binding.begin(_store, session_id)


def commit_auth(session_id: str, agentur_id: str, generation: int) -> bool:
    """Write this auth's result, unless a newer auth superseded it."""
    return session_binding.commit(_store, session_id, agentur_id, generation)


def bind(session_id: str, agentur_id: str) -> None:
    """Bind a verified Agenturnummer to this session (no-op on empty inputs).

    Unconditional low-level primitive. ``/agentur/auth`` must NOT use this — it
    goes through begin_auth/commit_auth so a superseded auth cannot overwrite a
    newer one.
    """
    session_binding.bind(_store, session_id, agentur_id)


def resolve(session_id: str) -> str | None:
    """Verified Agenturnummer for this session, or ``None``."""
    return session_binding.resolve(_store, session_id)


def authenticate(
    body: dict, user_agent: str = "", origin: str = ""
) -> tuple[bool, str | None]:
    """The whole /agentur/auth sequence, in the one order that is safe.

    Returns ``(authenticated, session_id)``; ``session_id`` is None when the body
    carried no usable one, which is the caller's cue to 400.

    This lives here rather than in the view for the same reason as on the
    customer path: every security property of this endpoint is a property of the
    ORDER these calls happen in, and nothing tests the view (`import app` pulls
    in live Supabase reads). Keeping the order here makes it testable.
    """
    session_id = body.get("session_id")
    if not session_id or not isinstance(session_id, str):
        return False, None

    generation = begin_auth(session_id)
    agentur_id = ""
    try:
        agentur_id = (
            verify_agentur_session(body.get("phpsessid", ""), user_agent, origin) or ""
        )
    finally:
        committed = commit_auth(session_id, agentur_id, generation)

    if not committed:
        print("[agentur_auth] superseded by a newer auth for the same session")
    authenticated = bool(agentur_id) and committed
    # Never the Agenturnummer, never the token.
    print(f"[agentur_auth] authenticated={authenticated}")
    return authenticated, session_id
