"""Session → verified identity bindings, shared by Kunden- and Agentur-Modus.

A store is a plain dict built by :func:`new_store`; every function here takes it
as its first argument. No class, deliberately: nothing ever *reassigns*
``store["bindings"]``, only mutates it, so a module that keeps

    _bindings = _store["bindings"]

as an alias can never silently detach from the live dict. With a class holding
the dict as an attribute, one ``self._bindings = {}`` anywhere would leave the
alias pointing at a dead object and the module would read stale state forever.

The generation/in-flight machinery is the interesting part, and the reason this
is shared rather than copy-pasted per identity kind. "Single worker" means one
PROCESS, not one request at a time: the Dockerfile runs gunicorn -k gevent with
--worker-connections 1000, so up to 1000 greenlets interleave inside that worker
and the ss.php call yields. The lock therefore guards individual dict operations
but NOT the unbind → verify → bind sequence:

  t=0.0  A (logged in) opens the chat  → begin, then ss.php stalls 3s
  t=0.5  B (logged out) opens the chat → begin, ss.php fast, no commit
  t=3.0  A's call resumes              → commit(session → A)   ← lands LAST
         B is holding a session that resolves to A's identity.

Same shared browser, same stored session_id, no attacker — just latency
ordering. So an auth claims a generation up front and may only write its result
if nothing superseded it. Newest auth wins, always.

This store never logs. Every log line lives in the calling module, because only
it knows which identity kind ("kunden_auth" / "agentur_auth") is being talked
about, and a shared line would either lie or need a label passed through six
call sites.
"""

import threading
import time


def new_store(ttl) -> dict:
    """A fresh binding store.

    ``ttl`` is seconds, either a number or a zero-argument callable. A callable
    is read **at bind time**, so a module constant stays the single source of
    truth even if it is reassigned after the store is built — snapshotting it
    here would silently freeze the value taken at import.
    """
    return {
        "ttl": ttl,
        # session_id -> (identity, expiry_epoch). In-memory, single-worker deploy
        # (see rate_limit.py); cleared on restart → fail closed, widget re-auths.
        "bindings": {},
        # session_id -> generation of the auth currently in flight for it.
        # Entries are removed the moment an auth settles, so this never grows.
        "inflight": {},
        "seq": 0,
        "lock": threading.Lock(),
    }


def _expiry(store: dict) -> float:
    ttl = store["ttl"]
    return time.time() + (ttl() if callable(ttl) else ttl)


def unbind(store: dict, session_id: str) -> None:
    """Drop any binding for this session and cancel any auth in flight for it.

    The widget keeps the same session_id in localStorage for hours, so without
    this a failed re-auth would silently leave the previous identity bound: on a
    shared browser (household, office, agency counter) person B would open the
    chat, verification would correctly fail because nobody is logged in, and B
    would still be served person A's data — the exact IDOR this closes.

    Dropping the in-flight entry is what makes that hold under concurrency: a
    slower auth still waiting on ss.php can no longer commit afterwards, because
    its generation is gone. Used by the 429 path in rate_limit.py, which has to
    clear the binding without running the view.
    """
    if not session_id:
        return
    with store["lock"]:
        store["bindings"].pop(session_id, None)
        store["inflight"].pop(session_id, None)


def begin(store: dict, session_id: str) -> int:
    """Clear the binding and claim a generation for the auth about to run.

    Every auth starts here, so every failure path below it ends anonymous rather
    than as whoever used the browser before. The returned token must be handed to
    :func:`commit`; that is what stops a slow auth from resurrecting an identity
    a later one already cleared.
    """
    if not session_id:
        return 0
    with store["lock"]:
        store["bindings"].pop(session_id, None)
        store["seq"] += 1
        store["inflight"][session_id] = store["seq"]
        return store["seq"]


def commit(store: dict, session_id: str, identity: str, generation: int) -> bool:
    """Write this auth's result, unless a newer auth superseded it.

    Returns True if this call still owned the session. False means another auth
    for the same session_id started (or an unbind ran) while we were waiting on
    ss.php — its outcome is authoritative and ours is discarded.
    """
    if not session_id:
        return False
    with store["lock"]:
        if store["inflight"].get(session_id) != generation:
            return False
        del store["inflight"][session_id]
        if identity:
            store["bindings"][session_id] = (identity, _expiry(store))
        return True


def bind(store: dict, session_id: str, identity: str) -> None:
    """Bind a verified identity to this session (no-op on empty inputs).

    Unconditional low-level primitive. An auth route must NOT use this — it goes
    through begin/commit so a superseded auth cannot overwrite a newer one.
    """
    if not session_id or not identity:
        return
    with store["lock"]:
        store["bindings"][session_id] = (identity, _expiry(store))


def resolve(store: dict, session_id: str) -> str | None:
    """Verified identity for this session, or ``None`` (never bound / expired)."""
    if not session_id:
        return None
    with store["lock"]:
        entry = store["bindings"].get(session_id)
        if entry is None:
            return None
        identity, expiry = entry
        if time.time() >= expiry:
            del store["bindings"][session_id]
            return None
        return identity
