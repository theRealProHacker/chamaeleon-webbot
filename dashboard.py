"""Dashboard HTTP layer and the in-memory cache in front of it.

Shape of the thing:

- The DETERMINISTIC axes (chat counts, user messages, day/hour/weekday buckets,
  the 7x24 heatmap, the segment split) are computed live from the raw chats by
  ``month_aggregate``. They are cheap and repeatable, so they are recomputed on
  the 5-minute refresh rather than stored.
- The PAID axes (answer quality, causes, countries) come from one LLM run per
  month and are read out of ``month_stats``. They are never computed in a
  request — see ``quality_job`` for why that matters on a single worker.
- If ``month_stats`` is not there yet (the DDL is a manual step), the dashboard
  still works: deterministic axes live, paid axes reported as
  ``status: missing``. Fail open, always.

Every aggregate is carried as a ``[allgemein, meinchamaeleon, agentur]`` vector,
so any tag selection in the UI is a sum instead of a re-run.

All datetimes here are German local time. Row timestamps arrive from Postgres in
UTC and are converted once, at the parse boundary, in
``month_aggregate.parse_row_timestamp``. Do not call ``isoparse`` on a row
timestamp anywhere — the hour and weekday buckets would silently become UTC ones.
"""

import hmac
import os
import re
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Iterable, NotRequired, Optional, TypedDict

from flask import request, jsonify, send_from_directory

import chat_quality
import month_aggregate
import month_stats
import quality_job
import rate_limit
import travel_index
from chat_segments import SEGMENTS, Segment
from db_logging import ChatHistory, Message, _message_bounds, supabase, DEBUG
from month_aggregate import (
    CUTOFF_ENABLED,
    DAY_NAME,
    GERMAN_MONTHS,
    MonthAggregate,
    MonthKey,
    QualityAggregate,
    aggregate_month,
    median_duration,
    avg_messages,
    avg_user_messages,
    chats_visible,
    current_month_start,
    month_state,
    is_new_format,
    is_user,
    month_bounds,
    month_key,
    month_label,
    parse_row_timestamp,
    weekday_occurrences,
    weekday_vectors,
    select,
    tz,
)

# Columns the dashboard reads. `session_id` is excluded on purpose — see the
# authentication note further down: it is the Kunden-Modus bearer token.
CHAT_COLUMNS = "id, messages, timestamp"

# A month parameter comes off the URL and is used to build date bounds and a
# cache key. Validate its shape before either.
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class OldMessage(TypedDict):
    role: NotRequired[str]
    content: str
    data: NotRequired[Any]
    type: NotRequired[str]


type AnyMessage = OldMessage | Message


# Old and new rows are told apart by the messages themselves: only new ones
# carry a per-message `timestamp`. That used to be done via `session_id is
# None`, which meant selecting the auth token just to branch on it.
class OldChatRow(TypedDict):
    id: str  # uuid primary key
    messages: list[OldMessage]
    timestamp: str  # ISO-8601 string from Postgres


class ChatRow(TypedDict):
    id: str  # uuid primary key
    messages: ChatHistory
    timestamp: str  # ISO-8601 string from Postgres


type AnyChatRow = OldChatRow | ChatRow


# ──────────────────────────────────────────
# Dashboard API payload
# ──────────────────────────────────────────


class ChatDetail(TypedDict):
    id: str | None
    chat_timestamp: str | None
    segment: str
    started_at: NotRequired[str]
    ended_at: NotRequired[str]
    duration_seconds: NotRequired[float]
    user_message_count: int
    messages: list[AnyMessage]


class WeekdayCount(TypedDict):
    weekday: str
    short_label: str
    count: int
    # How often this weekday occurred in the period, and count/occurrences.
    # `average` is None when the weekday has not happened yet (the running
    # month, early on) — that is "no data", and must not be drawn as a zero.
    occurrences: int
    average: Optional[float]


class HourlyCount(TypedDict):
    hour: int
    label: str
    count: int


class DailyCount(TypedDict):
    date: str
    label: str  # "14 August" — tooltips
    short_label: str  # "14" — axis ticks
    count: int


class MonthlySummary(TypedDict):
    key: MonthKey
    label: str  # "August 2026" — tooltips and titles
    short_label: str  # "Aug 26" — axis ticks
    count: int


class MonthDetail(TypedDict):
    month: str
    label: str
    state: str  # laufend | abgeschlossen | alt
    chats_hidden: bool
    comparison: Optional[dict[str, Any]]
    total_chats: int
    segment_counts: dict[str, int]
    avg_user_messages_per_chat: float
    daily_counts: list[DailyCount]
    hourly_counts: list[HourlyCount]
    weekday_counts: list[WeekdayCount]
    heatmap: list[list[int]]
    quality: dict[str, Any]
    chats: Optional[list[ChatDetail]]


class DashboardPayload(TypedDict):
    total_chats: int
    segment_counts: dict[str, int]
    avg_user_messages_per_chat: float
    hourly_counts: list[HourlyCount]
    weekday_counts: list[WeekdayCount]
    heatmap: list[list[int]]
    monthly_summary: list[MonthlySummary]
    current_month: MonthKey


# ──────────────────────────────────────────
# Reading the query string
# ──────────────────────────────────────────


def selected_segments() -> list[Segment] | None:
    """Tag selection from ``?segment=…``. Empty or absent means all.

    An empty selection deliberately means "all", not "none": that is the tag
    bar's resting state, and rendering zeros there would read as "no data"
    rather than "no filter".
    """
    raw = request.args.getlist("segment") if request else []
    chosen = [s for s in raw if s in SEGMENTS]
    return chosen or None


def build_hourly_count(hour: int, count: int) -> HourlyCount:
    return {"hour": hour, "label": f"{hour:02d}:00", "count": count}


def build_weekday_count(weekday: int, count: int, occurrences: int) -> WeekdayCount:
    return {
        "weekday": DAY_NAME[weekday],
        "short_label": DAY_NAME[weekday][:2],
        "count": count,
        "occurrences": occurrences,
        "average": count / occurrences if occurrences else None,
    }


def build_daily_count(day: int, key: MonthKey, count: int) -> DailyCount:
    month = int(key[5:7])
    return {
        "date": f"{key}-{day:02d}",
        "label": f"{day}. {GERMAN_MONTHS[month]}",
        "short_label": str(day),
        "count": count,
    }


def short_month_label(key: MonthKey) -> str:
    return f"{GERMAN_MONTHS[int(key[5:7])][:3]} {key[2:4]}"


def segment_counts(vector: list[int]) -> dict[str, int]:
    return {segment: vector[i] for i, segment in enumerate(SEGMENTS)}


# ──────────────────────────────────────────
# Fetching
# ──────────────────────────────────────────


def fetch_month_chats(key: MonthKey) -> tuple[list[AnyChatRow], int | None]:
    """One month of raw chats, plus the server-side row count.

    The count comes back from the same request (``count="exact"``) and exists so
    a silently truncated read cannot be written into ``month_stats`` as a
    month's aggregate — see ``month_stats.assert_complete``.
    """
    month_start, next_month_start = month_bounds(key)
    response = (
        supabase.table("chats")
        .select(CHAT_COLUMNS, count="exact")
        .gte("timestamp", month_start.isoformat())
        .lt("timestamp", next_month_start.isoformat())
        .order("timestamp", desc=True)
        .execute()
    )
    return response.data, getattr(response, "count", None)  # type: ignore


# One unpaged read of the whole table is 14k rows and 16 MB of jsonb, and it
# already came back as a Postgres `statement timeout` in production — the first
# request after a deploy then 500s. Paging keeps every single query small and
# bounded, at the cost of a dozen round trips on a path that runs once per
# process.
FETCH_PAGE = 2000


def fetch_all_chats() -> tuple[list[AnyChatRow], int | None]:
    rows: list[AnyChatRow] = []
    total: int | None = None
    start = 0
    while True:
        response = (
            supabase.table("chats")
            .select(CHAT_COLUMNS, count="exact")
            .order("timestamp", desc=False)
            .range(start, start + FETCH_PAGE - 1)
            .execute()
        )
        page = response.data or []
        if total is None:
            total = getattr(response, "count", None)
        rows += page
        if len(page) < FETCH_PAGE:
            break
        start += FETCH_PAGE
        # A count the server did give us is the honest stop condition; without
        # one the short page above is. Never loop unbounded against a table.
        if total is not None and len(rows) >= total:
            break
    return rows, total


# ──────────────────────────────────────────
# In-memory cache
# ──────────────────────────────────────────


# The cache is a plain dict plus module functions, not a class: nothing ever
# reassigns its sub-dicts, only mutates them, so an alias cannot silently detach
# from the live state the way `self._aggregates = {}` would.
#
# Past months are static once computed; only the current one expires. Nothing in
# here is authoritative — it is all recomputable from `chats` — so a process
# restart costs time, never correctness.

EXPIRY_SECONDS = 5 * 60

# Transcripts are heavy (17 MB for the full table) and only a handful of months
# are ever open at once. Bounded so a long-lived worker cannot grow without limit.
MAX_CACHED_TRANSCRIPT_MONTHS = 3


def new_month_cache() -> dict:
    return {
        "aggregates": {},
        "chats": {},
        "row_counts": {},
        "current_month": month_key(current_month_start()),
        "last_fetched": 0.0,
    }


def cache_expired(cache: dict) -> bool:
    return time.time() - cache["last_fetched"] > EXPIRY_SECONDS


def cache_months(cache: dict) -> list[MonthKey]:
    return sorted(cache["aggregates"])


def load_all(cache: dict) -> None:
    """Compute every month from the full table. Called on demand, not at import."""
    rows, _count = fetch_all_chats()
    if DEBUG:
        print(f"Fetched {len(rows)} chat rows from Supabase for cache initialization")

    by_month: dict[MonthKey, list[AnyChatRow]] = {}
    for row in rows:
        by_month.setdefault(month_key(parse_row_timestamp(row["timestamp"])), []).append(row)

    cache["aggregates"] = {
        key: aggregate_month(month_rows, key) for key, month_rows in by_month.items()
    }
    cache["row_counts"] = {key: len(month_rows) for key, month_rows in by_month.items()}
    cache["aggregates"].setdefault(
        cache["current_month"], month_aggregate.empty_aggregate(cache["current_month"])
    )
    cache["last_fetched"] = time.time()


def ensure_loaded(cache: dict) -> None:
    if not cache["aggregates"]:
        load_all(cache)


def warm_cache() -> None:
    """Fill the module cache off the request path. Called once at boot."""
    started = time.time()
    ensure_loaded(month_cache)
    print(
        f"[dashboard] cache warm: {len(month_cache['aggregates'])} months, "
        f"{sum(month_cache['row_counts'].values())} chats, {time.time() - started:.1f}s"
    )


def refresh_current_month(cache: dict, include_chats: bool = False) -> None:
    key = cache["current_month"]
    rows, _count = fetch_month_chats(key)
    cache["aggregates"][key] = aggregate_month(rows, key)
    cache["row_counts"][key] = len(rows)
    if include_chats:
        store_chats(cache, key, analyse_chats(rows))
    cache["last_fetched"] = time.time()


def rollover(cache: dict) -> None:
    """Finalize the old current month and start tracking the new one."""
    new_month = month_key(current_month_start())
    if new_month == cache["current_month"]:
        return
    ensure_loaded(cache)
    refresh_current_month(cache)
    cache["current_month"] = new_month
    cache["aggregates"].setdefault(new_month, month_aggregate.empty_aggregate(new_month))
    refresh_current_month(cache)


def cached_aggregate(cache: dict, key: MonthKey) -> MonthAggregate | None:
    ensure_loaded(cache)
    if key == cache["current_month"] and cache_expired(cache):
        refresh_current_month(cache)
    return cache["aggregates"].get(key)


def all_time_aggregate(cache: dict) -> MonthAggregate:
    ensure_loaded(cache)
    if cache_expired(cache):
        refresh_current_month(cache)
    return month_aggregate.combine(cache["aggregates"].values())


def cached_chats(cache: dict, key: MonthKey) -> list[ChatDetail]:
    cached = cache["chats"].get(key)
    if cached is not None and not (key == cache["current_month"] and cache_expired(cache)):
        return cached
    rows, _count = fetch_month_chats(key)
    details = analyse_chats(rows)
    store_chats(cache, key, details)
    if key == cache["current_month"]:
        cache["aggregates"][key] = aggregate_month(rows, key)
        cache["row_counts"][key] = len(rows)
        cache["last_fetched"] = time.time()
    return details


def store_chats(cache: dict, key: MonthKey, details: list[ChatDetail]) -> None:
    chats = cache["chats"]
    chats[key] = details
    while len(chats) > MAX_CACHED_TRANSCRIPT_MONTHS:
        for oldest in sorted(chats):
            if oldest != key and oldest != cache["current_month"]:
                del chats[oldest]
                break
        else:
            break


def analyse_chats(rows: Iterable[AnyChatRow]) -> list[ChatDetail]:
    """Transcripts for the drill-down view."""
    from chat_segments import segment_of_chat

    details: list[ChatDetail] = []
    for row in rows:
        messages = row["messages"]
        if not messages:
            continue
        detail: ChatDetail = {
            "id": row["id"],
            "chat_timestamp": row["timestamp"],
            "segment": segment_of_chat(messages if isinstance(messages, list) else []),
            "user_message_count": sum(1 for m in messages if is_user(m)) if isinstance(messages, list) else 0,
            "messages": messages,  # type: ignore
        }
        details.append(detail)
        # Old rows have no per-message timestamps and therefore no bounds.
        if not is_new_format(messages):
            continue
        start_ts, end_ts = _message_bounds(messages)  # type: ignore
        # Unix timestamps are absolute; fromtimestamp without a tz would render
        # them in whatever the container clock is, which is UTC on Railway.
        detail["started_at"] = datetime.fromtimestamp(start_ts, tz).isoformat()
        detail["ended_at"] = datetime.fromtimestamp(end_ts, tz).isoformat()
        detail["duration_seconds"] = end_ts - start_ts

    return details


month_cache = new_month_cache()


# ──────────────────────────────────────────
# Quality (read side only — runs happen in quality_job)
# ──────────────────────────────────────────


def previous_month(key: MonthKey) -> MonthKey:
    start, _ = month_bounds(key)
    return month_key(start - timedelta(days=1))


def previous_cause_counts(
    key: MonthKey, taxonomy_version: int, segments: Iterable[Segment] | None
) -> dict[str, int]:
    """Last month's count per cause, for the delta column.

    Read on the server, not assembled in the browser out of whatever months the
    user happened to click on — that made the delta column show a dash almost
    always, which quietly removed the one thing SC6 is measured on.

    Returns nothing across a taxonomy change: two versions are two different
    measuring devices, and a delta between them is a number with no meaning.
    """
    row = month_stats.load_month(previous_month(key))
    stored = (row or {}).get("quality")
    if not stored or stored.get("taxonomy_version") != taxonomy_version:
        return {}
    return {
        cause_id: select(vector, segments)
        for cause_id, vector in (stored.get("ursachen") or {}).items()
    }


def quality_for(key: MonthKey, segments: Iterable[Segment] | None) -> dict[str, Any]:
    """The paid half of a month, shaped for the frontend. Never triggers a run."""
    if quality_job.is_running(key):
        return {
            "status": "running",
            "ursachen": [],
            "laender": [],
            "qualitaet": {},
            "chat_ids_by_cause": {},
        }

    row = month_stats.load_month(key)
    stored: QualityAggregate | None = (row or {}).get("quality")
    if not stored:
        return {
            "status": "missing",
            "ursachen": [],
            "themen": [],
            "laender": [],
            "qualitaet": {},
            "chat_ids_by_cause": {},
        }

    # The FAQ gap marker is computed on READ, not stored: `faqs/` changes
    # independently of the classification run, and a cause that got a snippet
    # last week should stop being marked without repaying for the month.
    causes, topics = month_stats.split_taxonomy((row or {}).get("taxonomy"))
    taxonomy = chat_quality.mark_faq_gaps(causes)
    return {
        "status": stored.get("status", "ok"),
        "computed_at": (row or {}).get("quality_computed_at"),
        "taxonomy_version": stored.get("taxonomy_version", 0),
        "classified_chats": select(stored.get("classified_chats", [0, 0, 0]), segments),
        "unmapped_chats": stored.get("unmapped_chats", 0),
        "qualitaet": {
            quality: select(vector, segments)
            for quality, vector in (stored.get("qualitaet") or {}).items()
        },
        "ursachen": month_aggregate.top_causes(stored, taxonomy, segments),
        "vormonat": previous_cause_counts(
            key, stored.get("taxonomy_version", 0), segments
        ),
        "chat_ids_by_cause": stored.get("chat_ids_by_cause") or {},
        "neu": select((stored.get("ursachen") or {}).get("neu", [0, 0, 0]), segments),
        "themen": month_aggregate.top_topics(stored, topics, segments),
        "themen_neu": select(
            (stored.get("themen") or {}).get("neu", [0, 0, 0]), segments
        ),
        "laender": month_aggregate.top_countries(stored, segments),
        # Die Zahl der Gespraeche, in denen kein Reiseland vorkam. Sie faellt
        # aus der Zehnerliste heraus (sie sortiert immer ans Ende), gehoert aber
        # in den Report: ohne sie liest sich die Laenderspalte, als beschriebe
        # sie alle Gespraeche.
        "laender_ohne": select(
            (stored.get("laender") or {}).get(month_aggregate.NO_COUNTRY, [0, 0, 0]),
            segments,
        ),
    }


def previous_period_comparison(key: MonthKey, segments) -> dict[str, Any] | None:
    """The same slice of the previous month, for the running month only.

    Whole-month against part-month is the comparison that misleads: on the 3rd,
    "92 gegen 1.734" reads as a collapse. So the previous month is cut at the
    same day of the month and only that part is compared.

    What it cannot fix: 1.-3. September and 1.-3. August are different weekdays,
    and this axis is weekday-sensitive. Close enough to steer by, not a
    like-for-like figure.
    """
    now = datetime.now(tz)
    day = now.day
    previous = previous_month(key)
    rows, count = fetch_month_chats(previous)
    partial = month_aggregate.aggregate_month(rows, previous, max_day=day)

    current = cached_aggregate(month_cache, key)
    if current is None:
        return None

    return {
        "month": previous,
        "label": month_label(previous),
        "through_day": day,
        "chats": select(partial["total_chats"], segments),
        "avg_user_messages": avg_user_messages(partial, segments),
        "avg_messages": avg_messages(partial, segments),
        "median_duration_seconds": median_duration(partial, segments),
        # Same figures for the running month, so the frontend does no arithmetic.
        "current": {
            "chats": select(current["total_chats"], segments),
            "avg_user_messages": avg_user_messages(current, segments),
            "avg_messages": avg_messages(current, segments),
            "median_duration_seconds": median_duration(current, segments),
        },
    }


def month_detail(key: MonthKey, include_chats: bool = True) -> MonthDetail | None:
    agg = cached_aggregate(month_cache, key)
    if agg is None:
        return None
    segments = selected_segments()
    state = month_state(key)

    # Stufe 1 der Löschung: ab hier werden die Rohtranskripte nicht mehr
    # ausgeliefert — nicht nur ausgeblendet, sondern gar nicht erst geholt.
    # Ein Endpunkt, der die Daten noch sendet und sich auf das Frontend
    # verlässt, hat sie nicht verborgen.
    hidden = not chats_visible(key)
    chats = None if (hidden or not include_chats) else cached_chats(month_cache, key)
    if chats is not None and segments:
        chats = [c for c in chats if c["segment"] in segments]

    comparison = (
        previous_period_comparison(key, segments) if state == "laufend" else None
    )
    occurrences = weekday_occurrences(key)
    weekdays = weekday_vectors(agg, key)

    return {
        "month": key,
        "label": agg["label"],
        "state": state,
        "chats_hidden": hidden,
        "comparison": comparison,
        "total_chats": select(agg["total_chats"], segments),
        "segment_counts": segment_counts(agg["total_chats"]),
        "avg_user_messages_per_chat": avg_user_messages(agg, segments),
        "avg_messages_per_chat": avg_messages(agg, segments),
        "median_duration_seconds": median_duration(agg, segments),
        "daily_counts": [
            build_daily_count(int(day), key, select(vector, segments))
            for day, vector in sorted(agg["daily"].items(), key=lambda kv: int(kv[0]))
        ],
        "hourly_counts": [
            build_hourly_count(hour, select(vector, segments))
            for hour, vector in enumerate(agg["hourly"])
        ],
        "weekday_counts": [
            build_weekday_count(weekday, select(vector, segments), occurrences[weekday])
            for weekday, vector in enumerate(weekdays)
        ],
        "heatmap": [
            [select(agg["heatmap"][weekday][hour], segments) for hour in range(24)]
            for weekday in range(7)
        ],
        "quality": quality_for(key, segments),
        "chats": chats,
    }


# ──────────────────────────────────────────
# Auth
# ──────────────────────────────────────────
#
# DASHBOARD_PASSWORD has NO default and startup fails without it. There used to
# be a `"change-me"` fallback, which was survivable while the dashboard only
# exposed chat statistics.
#
# It used to be far worse than that: the dashboard handed out `session_id`
# values, and since Kunden-Modus auth (kunden_auth) `session_id` IS the bearer
# token for a customer's Buchungen and Zahlstand — a forgotten env var meant
# anyone on the internet could read live tokens and replay them against
# /chat/stream. That is fixed: the token is no longer selected, stored or
# served (see CHAT_COLUMNS).
#
# The password stays mandatory anyway, because what remains is still the full
# text of customer conversations. Failing loudly at boot beats failing open in
# production.
API_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
API_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

if not API_PASSWORD:
    raise RuntimeError(
        "DASHBOARD_PASSWORD is not set. The dashboard exposes full customer "
        "chat transcripts — refusing to start without a password. Set "
        "DASHBOARD_PASSWORD in the environment (.env locally, service variables "
        "on Railway)."
    )


def check_auth(username: str | None, password: str | None) -> bool:
    # compare_digest: constant-time, so a wrong password cannot be recovered by
    # timing the comparison.
    #
    # UTF-8 bytes, NOT str: compare_digest raises TypeError on str arguments
    # containing non-ASCII ("comparing strings with non-ASCII characters is not
    # supported"), where the old == simply returned False. Two consequences if
    # left as str, both live: any request with one umlaut in the Basic-Auth
    # header becomes an unauthenticated 500 instead of a 401, and a
    # DASHBOARD_PASSWORD containing "ä"/"€" locks the dashboard out completely —
    # every attempt 500s, including the correct one. The boot check above only
    # catches an EMPTY password, so it would not have caught that.
    #
    # Both comparisons run before the `and` so a wrong username does not skip the
    # password comparison; short-circuiting there would leak which half failed.
    user_ok = hmac.compare_digest(
        (username or "").encode("utf-8"), API_USERNAME.encode("utf-8")
    )
    password_ok = hmac.compare_digest(
        (password or "").encode("utf-8"), API_PASSWORD.encode("utf-8")
    )
    return user_ok and password_ok


def auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return (
                jsonify({"error": "Authentication required"}),
                401,
                {"WWW-Authenticate": 'Basic realm="Dashboard", charset="UTF-8"'},
            )
        return view(*args, **kwargs)

    return wrapped


# ──────────────────────────────────────────
# Routes
# ──────────────────────────────────────────


@auth_required
def DASHBOARD_data():
    """Aggregated stats across all months."""
    rollover(month_cache)
    all_time = all_time_aggregate(month_cache)
    segments = selected_segments()

    # Occurrences add across months exactly the way the chat counts do, so the
    # all-months average stays the honest "chats on an average Monday" and does
    # not need the individual months to be equally long.
    all_time_occurrences = [0] * 7
    for cached_key in cache_months(month_cache):
        for weekday, occurrence in enumerate(weekday_occurrences(cached_key)):
            all_time_occurrences[weekday] += occurrence

    # Der laufende Monat steuert nur abgeschlossene Tage bei — sonst teilte der
    # Gesamtschnitt heutige Chats durch einen Tag, den er nicht mitgezaehlt hat.
    all_time_weekdays = [list(vector) for vector in all_time["weekday"]]
    running = month_cache["current_month"]
    running_agg = month_cache["aggregates"].get(running)
    if running_agg:
        for weekday, vector in enumerate(weekday_vectors(running_agg, running)):
            for index, value in enumerate(vector):
                all_time_weekdays[weekday][index] -= (
                    running_agg["weekday"][weekday][index] - value
                )

    payload: DashboardPayload = {
        "total_chats": select(all_time["total_chats"], segments),
        "segment_counts": segment_counts(all_time["total_chats"]),
        "avg_user_messages_per_chat": avg_user_messages(all_time, segments),
        "hourly_counts": [
            build_hourly_count(hour, select(vector, segments))
            for hour, vector in enumerate(all_time["hourly"])
        ],
        "weekday_counts": [
            build_weekday_count(
                weekday, select(vector, segments), all_time_occurrences[weekday]
            )
            for weekday, vector in enumerate(all_time_weekdays)
        ],
        "heatmap": [
            [select(all_time["heatmap"][weekday][hour], segments) for hour in range(24)]
            for weekday in range(7)
        ],
        "monthly_summary": [
            {
                "key": key,
                "label": month_label(key),
                "short_label": short_month_label(key),
                "count": select(month_cache["aggregates"][key]["total_chats"], segments),
            }
            for key in cache_months(month_cache)
        ],
        "current_month": month_cache["current_month"],
    }

    return jsonify(payload)


@auth_required
def DASHBOARD_month(month: MonthKey):
    """Everything about a month EXCEPT the transcripts.

    Split off on purpose: with them the June payload is 3.0 MB and every number
    on the page waits behind 1.738 conversations that the reader has not asked
    to see yet. Without them it is ~20 KB. The transcripts come from
    ``/api/dashboard/<month>/chats`` when the list is actually wanted.
    """
    rollover(month_cache)
    if not month or not MONTH_RE.match(month):
        return jsonify({"error": "Invalid 'month' parameter, expected YYYY-MM"}), 400
    detail = month_detail(month, include_chats=False)
    if detail is None:
        return jsonify({"error": f"Month '{month}' not found"}), 404
    return jsonify(detail)


@auth_required
def DASHBOARD_month_chats(month: MonthKey):
    """The raw transcripts of one month, or nothing if they are past retention.

    The retention check lives HERE too, not only in `month_detail`: a second
    endpoint that serves the same rows without asking would undo stage 1 of the
    deletion rule completely.
    """
    rollover(month_cache)
    if not month or not MONTH_RE.match(month):
        return jsonify({"error": "Invalid 'month' parameter, expected YYYY-MM"}), 400
    if cached_aggregate(month_cache, month) is None:
        return jsonify({"error": f"Month '{month}' not found"}), 404
    if not chats_visible(month):
        return jsonify({"month": month, "chats_hidden": True, "chats": None})

    segments = selected_segments()
    chats = cached_chats(month_cache, month)
    if segments:
        chats = [c for c in chats if c["segment"] in segments]
    return jsonify({"month": month, "chats_hidden": False, "chats": chats})


@auth_required
def DASHBOARD_quality_run(month: MonthKey):
    """Enqueue a classification run. Returns immediately, always.

    This never computes anything in the request: one worker proxies the whole
    site, and a run takes minutes and costs money. A month already running
    collapses onto the run in flight instead of starting a second one.
    """
    if not MONTH_RE.match(month or ""):
        return jsonify({"error": "Invalid 'month' parameter, expected YYYY-MM"}), 400
    result = quality_job.enqueue(month, lambda: fetch_month_chats(month)[0])
    return jsonify(result)


@auth_required
def DASHBOARD_quality_status(month: MonthKey):
    if not MONTH_RE.match(month or ""):
        return jsonify({"error": "Invalid 'month' parameter, expected YYYY-MM"}), 400
    return jsonify(quality_job.status(month))


@auth_required
def DASHBOARD_index():
    return send_from_directory("static/dashboard", "index.html")


@auth_required
def DASHBOARD_report(month: MonthKey):
    """The report of one month as its own page.

    Deliberately a page and not a tab: this is what remains of a month once its
    transcripts are gone, so it has to be linkable, printable, and readable by
    someone who did not click their way through the dashboard to get here.
    """
    if not month or not MONTH_RE.match(month):
        return jsonify({"error": "Invalid 'month' parameter, expected YYYY-MM"}), 400
    return send_from_directory("static/dashboard", "report.html")


@auth_required
def admin_index():
    # Hidden admin page: no link from the dashboard, same Basic-Auth gate.
    return send_from_directory("static/admin", "index.html")


@auth_required
def admin_sitemap_get():
    """Current in-memory sitemap text + persisted version history."""
    import agent_base
    import sitemap_store

    return jsonify(
        {
            "text": agent_base.sitemap,
            "paths": len(agent_base.all_sites),
            "trip_paths": len(agent_base.trip_sites),
            "versions": sitemap_store.recent_versions(),
        }
    )


@auth_required
def admin_sitemap_post():
    """Apply + persist a hand-curated sitemap text (validated, fail-safe)."""
    import sitemap_sync

    data = request.get_json(silent=True) or {}
    result = sitemap_sync.apply_human_edit(data.get("text") or "")
    return jsonify(result), (400 if "error" in result else 200)


@auth_required
def reindex_travels():
    """Trigger a TourOne travel-index rebuild off the request thread.

    Rebuilding fetches all travels and can take seconds; running it inline would
    block the catch-all proxy (the whole site) on the single worker, so it runs
    in a background thread and this returns immediately with the last summary.
    """
    travel_index.rebuild_async()
    return jsonify({"status": "started", "last": travel_index.last_summary()})


# (path, view, methods, limit). The limit is applied in app.py, where the
# Limiter lives. Every route here was previously unthrottled: `default_limits`
# is empty, so a route without its own decorator has no limit at all — see the
# note in rate_limit.init_app.
routes = [
    ("/api/dashboard", DASHBOARD_data, ["GET"], rate_limit.DASHBOARD_LIMIT),
    ("/api/dashboard/<string:month>", DASHBOARD_month, ["GET"], rate_limit.DASHBOARD_LIMIT),
    (
        "/api/dashboard/<string:month>/chats",
        DASHBOARD_month_chats,
        ["GET"],
        rate_limit.DASHBOARD_LIMIT,
    ),
    ("/api/dashboard/<string:month>/quality", DASHBOARD_quality_status, ["GET"], rate_limit.DASHBOARD_LIMIT),
    ("/api/dashboard/<string:month>/quality", DASHBOARD_quality_run, ["POST"], rate_limit.ADMIN_LIMIT),
    ("/dashboard", DASHBOARD_index, ["GET"], rate_limit.DASHBOARD_LIMIT),
    ("/dashboard/", DASHBOARD_index, ["GET"], rate_limit.DASHBOARD_LIMIT),
    (
        "/dashboard/report/<string:month>",
        DASHBOARD_report,
        ["GET"],
        rate_limit.DASHBOARD_LIMIT,
    ),
    ("/admin", admin_index, ["GET"], rate_limit.DASHBOARD_LIMIT),
    ("/admin/", admin_index, ["GET"], rate_limit.DASHBOARD_LIMIT),
    ("/admin/reindex", reindex_travels, ["POST"], rate_limit.ADMIN_LIMIT),
    ("/admin/sitemap", admin_sitemap_get, ["GET"], rate_limit.DASHBOARD_LIMIT),
    ("/admin/sitemap", admin_sitemap_post, ["POST"], rate_limit.ADMIN_LIMIT),
]
