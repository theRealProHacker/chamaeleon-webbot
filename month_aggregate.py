"""Deterministic month aggregates as pure functions — no DB, no LLM, no Flask.

Everything the dashboard can count without paying for a model lives here: chat
counts, user-message counts, the day/hour/weekday buckets and the 7x24 heatmap,
each carried as a ``[allgemein, meinchamaeleon, agentur]`` vector so any tag
selection in the UI is a sum rather than a re-run (the EUREKA premise).

Two rules this module exists to hold:

**Time is Europe/Berlin, and that is a bug fix, not a convention** (P8).
``chats.timestamp`` arrives from Postgres in UTC. Converting at the parse
boundary — and only there — is what keeps the hour and weekday buckets honest;
read straight, they sit 1-2 hours off, and chats around midnight land in the
wrong day, weekday, and occasionally the wrong month. Since these numbers get
persisted into ``month_stats``, a wrong bucket would outlive the run that made
it.

**A row that fails to parse still happened** (T02). The old code incremented
``total_chats`` before the try block and skipped the time buckets on a broken
``messages`` column, so the totals and the bucket sums disagreed with each
other in the live data. Here a row contributes its timestamp buckets
unconditionally and only its user-message count is skipped, which is the one
number a broken column actually makes unknowable.
"""

from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence, TypedDict
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from chat_segments import (
    SEGMENTS,
    Segment,
    add,
    empty_vector,
    segment_of_chat,
    vector_total,
)

SCHEMA_VERSION = 1

tz = ZoneInfo("Europe/Berlin")

type MonthKey = str  # "2026-07"

DAY_NAME = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]

GERMAN_MONTHS: list[str] = [
    "",
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


def parse_row_timestamp(raw: str) -> datetime:
    """Parse a Postgres row timestamp into German local time.

    Do not call ``isoparse`` on a row timestamp anywhere else — see the module
    docstring. This is the single conversion boundary.
    """
    return isoparse(raw).astimezone(tz)


def month_key(dt: datetime) -> MonthKey:
    return dt.strftime("%Y-%m")


def month_label(key: MonthKey) -> str:
    """Human label for a month key. "" is the all-months aggregate, not a month."""
    if not key:
        return "Alle Monate"
    return f"{GERMAN_MONTHS[int(key[5:7])]} {key[:4]}"


def month_bounds(key: MonthKey) -> tuple[datetime, datetime]:
    """[start, next_start) of a month in German local time."""
    start = datetime.strptime(key + "-01", "%Y-%m-%d").replace(tzinfo=tz)
    next_start = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_start


def current_month_start(now: datetime | None = None) -> datetime:
    """First instant of the current month, German local.

    Aware on purpose: "the current month" is a German-local question, and the
    process clock on Railway is UTC — a naive ``now()`` rolled the month over an
    hour early, and twice a year on the wrong day entirely.
    """
    now = now or datetime.now(tz)
    return datetime(now.year, now.month, 1, tzinfo=tz)


def is_user(message: Any) -> bool:
    return isinstance(message, dict) and message.get("role") == "user"


def is_new_format(messages: Any) -> bool:
    """Whether a row uses the post-2026-05-22 message format.

    New rows carry a per-message ``timestamp``; old ones do not. This used to be
    decided by ``session_id is None``, which meant selecting the Kunden-Modus
    bearer token just to branch on it. Verified over all 14.299 live rows: the
    two conditions agree everywhere, with a clean format switch on 2026-05-22.

    The ``isinstance`` guard is not decoration: on a bare string last element
    ``"timestamp" in messages[-1]`` would silently become a substring test, and
    on a number it raises TypeError. Zero occurrences today, but the surrounding
    code already catches ``(KeyError, TypeError)`` for broken ``messages``, so
    the row class is known to exist.
    """
    if not isinstance(messages, list) or not messages:
        return False
    return isinstance(messages[-1], dict) and "timestamp" in messages[-1]


class MonthAggregate(TypedDict):
    """Additive, segment-split counts for one month. JSON-serialisable."""

    schema_version: int
    month: MonthKey
    label: str
    # [allgemein, meinchamaeleon, agentur]
    total_chats: list[int]
    user_messages: list[int]
    # day-of-month (1-31) -> vector; hour (0-23) -> vector; weekday (0-6) -> vector
    daily: dict[str, list[int]]
    hourly: list[list[int]]
    weekday: list[list[int]]
    # heatmap[weekday][hour] -> vector
    heatmap: list[list[list[int]]]
    # rows whose `messages` column could not be read; their time buckets still count
    unreadable_rows: int


def empty_aggregate(key: MonthKey) -> MonthAggregate:
    return {
        "schema_version": SCHEMA_VERSION,
        "month": key,
        "label": month_label(key),
        "total_chats": empty_vector(),
        "user_messages": empty_vector(),
        "daily": {},
        "hourly": [empty_vector() for _ in range(24)],
        "weekday": [empty_vector() for _ in range(7)],
        "heatmap": [[empty_vector() for _ in range(24)] for _ in range(7)],
        "unreadable_rows": 0,
    }


def aggregate_month(rows: Iterable[Any], key: MonthKey) -> MonthAggregate:
    """Aggregate raw chat rows of one month. Pure: same rows, same result."""
    agg = empty_aggregate(key)

    for row in rows:
        timestamp = parse_row_timestamp(row["timestamp"])
        messages = row.get("messages")
        segment: Segment = segment_of_chat(messages if isinstance(messages, list) else [])

        # Time buckets first and unconditionally: a row that cannot be read
        # still happened at a point in time (T02).
        add(agg["total_chats"], segment)
        day = str(timestamp.day)
        add(agg["daily"].setdefault(day, empty_vector()), segment)
        add(agg["hourly"][timestamp.hour], segment)
        add(agg["weekday"][timestamp.weekday()], segment)
        add(agg["heatmap"][timestamp.weekday()][timestamp.hour], segment)

        # `messages` is jsonb and the live table holds rows where it is not a
        # list at all. Type-check rather than try/except: iterating a string
        # yields characters, so a `sum(... if is_user(m))` over it returns 0
        # without raising — the row would be counted as "no user messages"
        # instead of "unknown".
        if not isinstance(messages, list):
            agg["unreadable_rows"] += 1
            continue
        add(agg["user_messages"], segment, sum(1 for m in messages if is_user(m)))

    # Fill the gaps so the frontend gets a continuous month, not a sparse dict.
    highest = max((int(d) for d in agg["daily"]), default=0)
    for day in range(1, highest + 1):
        agg["daily"].setdefault(str(day), empty_vector())

    return agg


# ──────────────────────────────────────────
# Reading an aggregate under a tag selection
# ──────────────────────────────────────────


def select(vector: list[int] | list[int | None], segments: Iterable[Segment] | None) -> int:
    """Sum a count vector over the selected segments; ``None`` means all.

    An empty selection also means all — that is the tag bar's "nothing pressed"
    state, and showing zero there would read as "no data" rather than "no
    filter".
    """
    chosen = list(segments) if segments else list(SEGMENTS)
    if not chosen:
        return vector_total(list(vector))
    total = 0
    for segment in chosen:
        component = vector[SEGMENTS.index(segment)]
        total += component or 0
    return total


def avg_user_messages(agg: MonthAggregate, segments: Iterable[Segment] | None = None) -> float:
    """Weighted average, rebuilt from the bucket sums.

    Explicitly NOT an average of averages: SC1's sum invariant covers countable
    quantities only, and averaging averages over a tag selection would weight a
    100-chat segment like a 3-chat one.
    """
    chats = select(agg["total_chats"], segments)
    if not chats:
        return 0.0
    return select(agg["user_messages"], segments) / chats


def combine(aggregates: Iterable[MonthAggregate]) -> MonthAggregate:
    """Fold several months into one all-time aggregate (daily buckets dropped).

    Daily counts are meaningless across months — day 3 of July and day 3 of
    August are not the same bucket — so they are left empty rather than summed
    into something nobody should read.
    """
    combined = empty_aggregate("")
    combined["label"] = "Alle Monate"
    for agg in aggregates:
        for i in range(len(SEGMENTS)):
            combined["total_chats"][i] += agg["total_chats"][i]
            combined["user_messages"][i] += agg["user_messages"][i]
            for hour in range(24):
                combined["hourly"][hour][i] += agg["hourly"][hour][i]
            for weekday in range(7):
                combined["weekday"][weekday][i] += agg["weekday"][weekday][i]
                for hour in range(24):
                    combined["heatmap"][weekday][hour][i] += agg["heatmap"][weekday][hour][i]
        combined["unreadable_rows"] += agg["unreadable_rows"]
    return combined


# ──────────────────────────────────────────
# Folding LLM verdicts into segment-split counts
# ──────────────────────────────────────────

NO_COUNTRY = "ohne Land"


class QualityAggregate(TypedDict):
    """Counts from one classification run, split by segment. JSON-serialisable."""

    schema_version: int
    month: MonthKey
    run_id: str
    model: str
    prompt_version: int
    taxonomy_version: int
    status: str  # ok | partial | missing
    # quality -> vector; cause id (or "neu") -> vector; country (or "ohne Land") -> vector
    qualitaet: dict[str, list[int]]
    ursachen: dict[str, list[int]]
    laender: dict[str, list[int]]
    classified_chats: list[int]
    unmapped_chats: int
    # cause id -> the chats behind it, so a row in the cause table can be
    # opened. Ordinary row uuids, not session tokens.
    chat_ids_by_cause: dict[str, list[str]]


def aggregate_quality(
    rows: Iterable[Any],
    verdicts: Iterable[Any],
    month: MonthKey,
    run_id: str = "",
    model: str = "",
    prompt_version: int = 0,
    taxonomy_version: int = 0,
    status: str = "ok",
    unmapped: int = 0,
) -> QualityAggregate:
    """Fold per-chat verdicts into ``[allgemein, meinchamaeleon, agentur]`` vectors.

    The segment comes from the raw row, never from the model — it is
    deterministic (A2) and must stay that way even when the quality axis beside
    it is not.

    A chat counts for EACH of its countries, so ``sum(laender) >= chats``. That
    is the one place SC1's sum invariant deliberately does not hold, and it has
    to be visible in the report rather than looking like an arithmetic bug.
    """
    segment_of: dict[str, Segment] = {}
    for row in rows:
        messages = row.get("messages")
        segment_of[str(row.get("id") or "")] = segment_of_chat(
            messages if isinstance(messages, list) else []
        )

    agg: QualityAggregate = {
        "schema_version": SCHEMA_VERSION,
        "month": month,
        "run_id": run_id,
        "model": model,
        "prompt_version": prompt_version,
        "taxonomy_version": taxonomy_version,
        "status": status,
        "qualitaet": {},
        "ursachen": {},
        "laender": {},
        "classified_chats": empty_vector(),
        "unmapped_chats": unmapped,
        "chat_ids_by_cause": {},
    }

    for verdict in verdicts:
        chat_id = str(verdict.get("chat_db_id") or "")
        segment = segment_of.get(chat_id, "allgemein")
        add(agg["classified_chats"], segment)

        quality = verdict.get("qualitaet") or "beantwortet"
        add(agg["qualitaet"].setdefault(quality, empty_vector()), segment)

        cause = verdict.get("ursache_id") or ""
        if cause:
            add(agg["ursachen"].setdefault(cause, empty_vector()), segment)
            if chat_id:
                agg["chat_ids_by_cause"].setdefault(cause, []).append(chat_id)

        countries = verdict.get("laender") or []
        if not countries:
            countries = [NO_COUNTRY]
        for country in countries:
            add(agg["laender"].setdefault(country, empty_vector()), segment)

    return agg


def missing_quality(month: MonthKey) -> QualityAggregate:
    """The fail-open shape: deterministic axes live, LLM axes explicitly absent.

    Used whenever ``month_stats`` has no row yet — the table is created by hand
    in the Supabase SQL editor and that step has historically been the one that
    stalls (``sitemap_versions`` has been waiting since 2026-07-06). ``missing``
    is a state the frontend renders; it is never a zero, which would read as
    "measured, found nothing".
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "month": month,
        "run_id": "",
        "model": "",
        "prompt_version": 0,
        "taxonomy_version": 0,
        "status": "missing",
        "qualitaet": {},
        "ursachen": {},
        "laender": {},
        "classified_chats": empty_vector(),
        "unmapped_chats": 0,
        "chat_ids_by_cause": {},
    }


def top_causes(
    agg: QualityAggregate,
    taxonomy: Sequence[Any],
    segments: Iterable[Segment] | None = None,
    limit: int = 15,
) -> list[dict]:
    """Causes ranked by how often they explain a bad answer, under a tag selection.

    ``neu`` is never ranked among them — it is a residual, not a cause, and the
    frontend shows it as a footnote so a big residual reads as "the taxonomy is
    out of date" rather than as the top problem.
    """
    labels = {c["ursache_id"]: c for c in taxonomy}
    ranked: list[dict] = []
    for cause_id, vector in agg["ursachen"].items():
        if cause_id == "neu":
            continue
        meta = labels.get(cause_id, {})
        ranked.append(
            {
                "ursache_id": cause_id,
                "label": meta.get("label", cause_id),
                "definition": meta.get("definition", ""),
                "retired": bool(meta.get("retired")),
                "count": None if meta.get("retired") else select(vector, segments),
                "vector": vector,
            }
        )
    ranked.sort(key=lambda c: (c["count"] is None, -(c["count"] or 0)))
    return ranked[:limit]


def top_countries(
    agg: QualityAggregate,
    segments: Iterable[Segment] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Countries as a RANKING, not as exact numbers.

    The country axis is the only non-deterministic one in v1: two runs over the
    same month can differ. How many ranks may be shown at all follows from the
    variance measured by running one month twice — in the long tail of 73
    countries, ranks 8 and 9 swap on a handful of chats.
    """
    ranked = [
        {"land": country, "count": select(vector, segments), "vector": vector}
        for country, vector in agg["laender"].items()
    ]
    ranked.sort(key=lambda c: (c["land"] == NO_COUNTRY, -c["count"]))
    return ranked[:limit]
