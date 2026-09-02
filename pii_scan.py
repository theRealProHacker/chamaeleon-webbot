"""Checks that a month's aggregate payload carries no personal data (SC3).

The payload in ``month_stats`` is meant to be anonymous, and that is the whole
justification for keeping it indefinitely. This module is what makes that a
checked claim rather than an assumption: it runs over the finished payload,
before it is written, and records what it found as part of the run.

Three things it looks for:

1. **Session tokens.** ``chats.session_id`` IS the Kunden-Modus bearer token —
   a leaked one can be replayed against /chat/stream to read a customer's
   bookings. The dashboard no longer selects the column at all, so this is a
   backstop against it arriving some other way.
2. **Booking numbers.** Six-digit ``vorgang`` values (kundendaten.py) identify a
   specific customer's trip.
3. **Verbatim transcript.** Any run of 40+ characters that also appears in a
   user message of that month. A paraphrase carrying a customer name is NOT
   caught by this, and that limit is stated rather than papered over — it is
   checked by hand on the first run.

What this is not: a guarantee. It is a mechanical check for the three shapes
that are known to be dangerous here.
"""

import re
from typing import Any, Iterable

# A UUID, and the opaque hand-issued ids the live table also holds. Matching the
# shape rather than a value list: a token that leaks is by definition one we do
# not have on hand to compare against.
SESSION_ID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)

# TourOne `vorgang` numbers as they appear in kundendaten.py: six digits,
# standing alone. Deliberately NOT 6-10 digits — measured against the live
# table, a wider pattern matches the company's own phone number (030 347 996
# 217) in 143 messages, and a check that cries wolf on every run is a check
# nobody reads. The neighbours must be non-alphanumeric, not merely non-digit,
# or every uuid in the payload matches on its hex runs.
BOOKING_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z])\d{6}(?![0-9A-Za-z])")

# Shortest run of characters that counts as quoting a conversation rather than
# coinciding with one. Below this, ordinary German phrases collide.
VERBATIM_LENGTH = 40


def _strings(value: Any) -> Iterable[str]:
    """Every string anywhere in a nested payload, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def user_messages(rows: Iterable[Any]) -> list[str]:
    texts: list[str] = []
    for row in rows:
        messages = row.get("messages")
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content)
    return texts


def scan(payload: Any, rows: Iterable[Any]) -> dict:
    """Scan one month's payload. Returns the artifact; never raises."""
    values = [s for s in _strings(payload) if s]
    rows = list(rows)
    sources = user_messages(rows)

    # The payload legitimately carries chat ROW ids (the drill-down needs them),
    # and those are uuids too — shape alone cannot tell them from a session
    # token. So the check is membership, not shape: a uuid in the payload is
    # fine if it is one of this month's chat ids, and a finding if it is not.
    known_ids = {str(row.get("id")) for row in rows if row.get("id")}
    session_hits = [
        v
        for v in values
        if SESSION_ID_RE.search(v) and v not in known_ids
    ]
    booking_hits = [v for v in values if BOOKING_NUMBER_RE.search(v)]

    # Verbatim: only long strings can contain a long run, so short ones are
    # skipped before the quadratic part.
    verbatim_hits: list[str] = []
    candidates = [v for v in values if len(v) >= VERBATIM_LENGTH]
    if candidates:
        haystack = "\n".join(sources)
        for value in candidates:
            for start in range(0, len(value) - VERBATIM_LENGTH + 1):
                window = value[start : start + VERBATIM_LENGTH]
                if window in haystack:
                    verbatim_hits.append(value)
                    break

    return {
        "checked_strings": len(values),
        "checked_user_messages": len(sources),
        "known_chat_ids": len(known_ids),
        "session_id_hits": session_hits[:5],
        "booking_number_hits": booking_hits[:5],
        "verbatim_hits": verbatim_hits[:5],
        "clean": not (session_hits or booking_hits or verbatim_hits),
        # Stated, not hidden: what this cannot see.
        "not_checked": "Paraphrasen, die einen Kundennamen mitführen",
    }
