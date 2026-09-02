"""Segment assignment for chats — deterministic, no LLM, no I/O.

A2/P7 of the dashboard design: every chat belongs to exactly one of three
segments, derived from the ``url`` the assistant messages carry.

    /Agentur…        -> agentur
    /MeinChamaeleon… -> meinchamaeleon
    everything else  -> allgemein

There is deliberately no fourth bucket. A chat without any assistant ``url``
counts as ``allgemein`` (P1: "Es gibt kein Unbekannt. Unbekannt ist die
öffentliche Webseite") — the widget ran on the public site only until mid-May
2026, so an unattributed chat really is a public-site chat. The consequence
worth keeping in mind: because the cases are complete and non-overlapping, the
sum invariant ``allgemein + meinchamaeleon + agentur == total`` holds for every
countable quantity (SC1).

Chat segment is the MOST FREQUENT segment over the chat's assistant urls, ties
broken by the first url (P7). "First url wins" was the original rule; measured
over four months it differs on 53 chats (0.79 %) and moves 23 net, symmetric in
both directions — noise, but the better rule is free.

Kept free of `dashboard` and `db_logging` imports on purpose so the aggregate
layer can be exercised without a database.
"""

from collections import Counter
from typing import Any, Iterable, Literal
from urllib.parse import urlsplit

type Segment = Literal["allgemein", "meinchamaeleon", "agentur"]

SEGMENTS: tuple[Segment, ...] = ("allgemein", "meinchamaeleon", "agentur")

# Index into the [allgemein, meinchamaeleon, agentur] count vectors that every
# aggregate carries (the EUREKA premise: additive aggregates, so any tag
# selection in the UI is a sum instead of a re-run).
SEGMENT_INDEX: dict[Segment, int] = {s: i for i, s in enumerate(SEGMENTS)}

DEFAULT_SEGMENT: Segment = "allgemein"

# Path prefixes, compared case-insensitively. Order matters only in that the
# first match wins; the two prefixes cannot both match.
_PREFIXES: tuple[tuple[str, Segment], ...] = (
    ("/agentur", "agentur"),
    ("/meinchamaeleon", "meinchamaeleon"),
)


def normalize_url(raw: Any) -> str:
    """Path of a url, without host, query, fragment or trailing slash.

    CASE IS PRESERVED. This value is not only compared — it is handed to the
    model as the current page and looked up in ``travel_index`` and
    ``agent_base.all_sites``, both of which key on the real, mixed-case path
    (``/Afrika/Namibia``). Lowercasing here would quietly turn every trip page
    into an unknown one. The case-insensitive part belongs to segment matching
    alone, and lives in ``segment_of_url``.

    Accepts both absolute urls and bare paths — the widget has sent both. A
    non-string (or empty) value yields "", which callers read as "no url".
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""
    parts = urlsplit(raw.strip())
    path = parts.path if (parts.scheme or parts.netloc) else raw.split("#")[0].split("?")[0]
    path = path.strip().rstrip("/")
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def segment_of_url(raw: Any) -> Segment:
    """Segment for a single url. Anything unrecognised is ``allgemein`` (P1)."""
    path = normalize_url(raw).lower()
    for prefix, segment in _PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return segment
    return DEFAULT_SEGMENT


def segment_from_context(url: Any, kunden_id: str = "", agentur_id: str = "") -> Segment:
    """Segment decided server-side, at the moment the message is written.

    ``current_url`` comes out of the request body and is client-controlled — a
    handful of curl requests could visibly tip the Agentur bucket, which is the
    smallest of the three. The two bindings cannot be asserted by a client:
    ``agentur_id`` and ``kunden_id`` only exist after a server-side session
    check, so where one is present it decides. The path is the fallback, and
    stays the only signal for rows written before this existed.

    Agentur wins over Kunde because the two modes are exclusive and the request
    handler resolves them in that order.
    """
    if agentur_id:
        return "agentur"
    if kunden_id:
        return "meinchamaeleon"
    return segment_of_url(url)


def assistant_urls(messages: Iterable[Any]) -> list[str]:
    """The ``url`` values of a chat's assistant messages, in order.

    Only assistant messages carry a url (see db_logging.Message), which is why
    P7 is formulated over them: a rule "by user message" would not be runnable.
    Rows with broken ``messages`` are common enough in the live table that this
    tolerates non-dict elements rather than raising.
    """
    urls: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        url = message.get("url")
        if isinstance(url, str) and url.strip():
            urls.append(url)
    return urls


def message_segments(messages: Iterable[Any]) -> list[Segment]:
    """Per-assistant-message segment, in order.

    Prefers the ``segment`` written by the server at message time; falls back to
    parsing the url, which is what every row from before that change has.
    """
    segments: list[Segment] = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        stored = message.get("segment")
        if stored in SEGMENTS:
            segments.append(stored)  # type: ignore[arg-type]
            continue
        url = message.get("url")
        if isinstance(url, str) and url.strip():
            segments.append(segment_of_url(url))
    return segments


def segment_of_chat(messages: Iterable[Any]) -> Segment:
    """Most frequent segment over the chat's assistant messages; ties -> first."""
    segments = message_segments(messages)
    if not segments:
        return DEFAULT_SEGMENT
    counts = Counter(segments)
    best = max(counts.values())
    tied = {segment for segment, count in counts.items() if count == best}
    if len(tied) == 1:
        return tied.pop()
    # Tie: the first url decides.
    for segment in segments:
        if segment in tied:
            return segment
    return DEFAULT_SEGMENT


def empty_vector() -> list[int]:
    """A fresh [allgemein, meinchamaeleon, agentur] counter."""
    return [0, 0, 0]


def add(vector: list[int], segment: Segment, amount: int = 1) -> list[int]:
    """Add to one component in place and return the vector (for chaining)."""
    vector[SEGMENT_INDEX[segment]] += amount
    return vector


def vector_total(vector: list[int | None]) -> int:
    """Sum of a count vector, treating ``None`` ("not measured") as 0.

    ``None`` appears for retired causes (A4): "not measured any more" is not
    "did not occur", so the components stay nullable and only the display layer
    collapses them.
    """
    return sum(component or 0 for component in vector)
