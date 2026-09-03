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

import calendar
import os
from datetime import date, datetime, timedelta
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

# Aufbewahrungsfrist. Ein Monat gilt als `alt`, sobald sein ERSTER Tag so lange
# zurückliegt — nicht sein letzter. Für September heißt das: der Monat kippt am
# 90. Tag nach dem 1. September, unabhängig davon, wie lange er selbst lief.
RETENTION_DAYS = 90

# Upper bounds in seconds for the duration histogram; the last bucket is open.
# Fine at the bottom because that is where the mass is — the median sits around
# four seconds, so a first bucket of 0-5 s would put half the month in one bar
# and leave the median to interpolation.
DURATION_EDGES = [2, 3, 5, 8, 15, 30, 60, 120, 300, 600, 1800, 3600, None]

# Stufe 1 der Löschung: Rohtranskripte eines alten Monats werden im Dashboard
# NICHT MEHR ANGEZEIGT. Gelöscht wird dabei nichts — die Frist ist damit
# ausdrücklich noch nicht erfüllt, das bleibt Stufe 2 (echtes Löschen in der DB).
#
# Standardmäßig AUS, und das ist keine Vorsicht, sondern die vereinbarte
# Reihenfolge: erst die Reports der alten Monate rechnen und prüfen, dann
# verbergen. Andernfalls verlieren zehn Monate ihre Chats, ohne dass an ihrer
# Stelle etwas steht. Einschalten über CUTOFF_ENABLED=true.
CUTOFF_ENABLED = os.environ.get("CUTOFF_ENABLED", "false").lower() == "true"

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


type MonthState = Literal["laufend", "abgeschlossen", "alt"]


def month_state(key: MonthKey, now: datetime | None = None) -> MonthState:
    """Where a month stands: still filling, finished, or past the retention line.

    - ``laufend``: the running month. Its numbers still change.
    - ``abgeschlossen``: finished, and its raw chats are still shown.
    - ``alt``: the first day is at least RETENTION_DAYS ago. Raw chats are no
      longer shown (once the cutoff is switched on); what remains is the report.

    Deliberately keyed on the FIRST day, as specified: a month becomes old as a
    whole, not day by day, so a month never sits half visible.
    """
    now = now or datetime.now(tz)
    if key == month_key(current_month_start(now)):
        return "laufend"
    start, _ = month_bounds(key)
    if start <= now - timedelta(days=RETENTION_DAYS):
        return "alt"
    return "abgeschlossen"


def weekday_occurrences(key: MonthKey, now: datetime | None = None) -> list[int]:
    """How often each weekday (Mon=0) actually occurs in this month.

    A month holds four or five of each weekday, so a raw weekday bar is up to
    25% taller for no reason but the calendar. Dividing by this turns the bars
    into "chats on an average Monday", which is the number the reader thinks
    they are already looking at.

    The RUNNING month counts only COMPLETED days — up to yesterday. Two
    separate distortions are removed by that one cut:

    - Weekdays that have not happened yet would otherwise be divided by an
      occurrence that never came. They return 0 here, and the callers render
      that as "no data" rather than as zero chats.
    - TODAY is a part-day. Plotted as a full occurrence it makes the current
      weekday look dead every morning: at 09:00 a third of a Thursday would
      sit next to four whole Wednesdays under the same word, "average".

    The matching cut on the chat counts is `weekday_vectors`; the two must
    always be used together or the average divides today's chats by yesterday.
    """
    now = now or datetime.now(tz)
    year, month = int(key[:4]), int(key[5:7])
    last = calendar.monthrange(year, month)[1]
    if key == month_key(current_month_start(now)):
        last = min(last, now.day - 1)
    counts = [0] * 7
    for day in range(1, last + 1):
        counts[date(year, month, day).weekday()] += 1
    return counts


def weekday_vectors(
    agg: "MonthAggregate", key: MonthKey, now: datetime | None = None
) -> list[list[int]]:
    """The weekday buckets of `agg`, counting only completed days.

    The counterpart to `weekday_occurrences`. For the running month today's
    chats are taken back out of today's weekday, so numerator and denominator
    describe the same set of days.

    Done by subtracting the daily bucket rather than by re-reading the rows:
    `daily` already holds exactly today's chats per segment, so this is exact
    and costs no second fetch.
    """
    now = now or datetime.now(tz)
    vectors = [list(vector) for vector in agg["weekday"]]
    if key != month_key(current_month_start(now)):
        return vectors
    today = agg["daily"].get(str(now.day))
    if today:
        for index, value in enumerate(today):
            vectors[now.weekday()][index] -= value
    return vectors


def chats_visible(key: MonthKey, now: datetime | None = None) -> bool:
    """Whether the raw transcripts of a month may still be served."""
    if not CUTOFF_ENABLED:
        return True
    return month_state(key, now) != "alt"


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
    # all messages, not just the user's — "Länge eines Gesprächs"
    messages: list[int]
    # Duration as a HISTOGRAM, not a sum. Measured over August 2026: median 4 s,
    # mean 1.054 s, longest 98 hours — a handful of chats left open for days
    # make the mean say "half an hour" about conversations that typically last
    # seconds. A histogram gives a median instead, and unlike a median it is
    # additive, so a tag selection is still a sum (the same reason every other
    # aggregate here is a count vector).
    # duration_buckets[i] is a segment vector for the range DURATION_EDGES[i].
    duration_buckets: list[list[int]]
    duration_samples: list[int]
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
        "messages": empty_vector(),
        "duration_buckets": [empty_vector() for _ in DURATION_EDGES],
        "duration_samples": empty_vector(),
        "unreadable_rows": 0,
    }


def chat_duration(messages: list) -> float | None:
    """Seconds from the first to the last timestamped message, or None.

    Old rows (before 2026-05-22) carry no per-message timestamps and therefore
    have no duration at all — None, not 0. Counting them as zero-length would
    drag the average down by exactly the share of old data in the month.
    """
    stamps = [
        m["timestamp"]
        for m in messages
        if isinstance(m, dict) and isinstance(m.get("timestamp"), (int, float))
    ]
    if len(stamps) < 2:
        return None
    return max(stamps) - min(stamps)


def duration_bucket(seconds: float) -> int:
    for index, edge in enumerate(DURATION_EDGES):
        if edge is None or seconds < edge:
            return index
    return len(DURATION_EDGES) - 1


def aggregate_month(
    rows: Iterable[Any], key: MonthKey, max_day: int | None = None
) -> MonthAggregate:
    """Aggregate raw chat rows of one month. Pure: same rows, same result.

    ``max_day`` cuts the month off after that day of the month. It exists for
    the running month's comparison: 1.-3. September against 1.-3. August is the
    only comparison that means anything while a month is still filling up.
    """
    agg = empty_aggregate(key)

    for row in rows:
        timestamp = parse_row_timestamp(row["timestamp"])
        if max_day is not None and timestamp.day > max_day:
            continue
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
        add(agg["messages"], segment, len(messages))
        duration = chat_duration(messages)
        if duration is not None:
            add(agg["duration_buckets"][duration_bucket(duration)], segment)
            add(agg["duration_samples"], segment)

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


def avg_messages(agg: MonthAggregate, segments: Iterable[Segment] | None = None) -> float:
    """Messages per chat, both sides — the "length" of a conversation."""
    chats = select(agg["total_chats"], segments)
    if not chats:
        return 0.0
    return select(agg["messages"], segments) / chats


def median_duration(
    agg: MonthAggregate, segments: Iterable[Segment] | None = None
) -> float | None:
    """Median chat duration in seconds, interpolated inside its bucket.

    A median, not a mean, and the reason is in the data rather than in taste:
    over August the median is 4 seconds while the mean is 1.054, because a few
    conversations stayed open for days. The mean describes those few; the median
    describes the month.

    Counted over the chats that HAVE a duration. Rows from before 2026-05-22
    carry no per-message timestamps, so they have none — treating them as zero
    would make the figure a function of how much old data a month holds.
    """
    counts = [select(bucket, segments) for bucket in agg["duration_buckets"]]
    total = sum(counts)
    if not total:
        return None

    target = total / 2
    seen = 0
    for index, count in enumerate(counts):
        if seen + count < target:
            seen += count
            continue
        low = 0 if index == 0 else DURATION_EDGES[index - 1]
        high = DURATION_EDGES[index]
        if high is None:
            # Open-ended top bucket: report its lower edge rather than invent a
            # number for it.
            return float(low)
        if not count:
            return float(low)
        return low + (high - low) * ((target - seen) / count)
    return None


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
            combined["messages"][i] += agg["messages"][i]
            combined["duration_samples"][i] += agg["duration_samples"][i]
            for bucket in range(len(DURATION_EDGES)):
                combined["duration_buckets"][bucket][i] += agg["duration_buckets"][bucket][i]
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
