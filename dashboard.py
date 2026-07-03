"""
Following idea:
- On startup, load all chats from the DB and analyze them, storing results in an in-memory cache grouped by month.
- When the dashboard requests data for a month, serve from cache if available and not expired;

Only current month can expire, past months are static once loaded. When current month expires, only re-fetch current month to update it, not everything.

When current month changes, fetch old current month to finalize it, and start tracking new current month.

All datetimes in local timezone, i.e. German local time.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo
from types import NoneType
from dateutil.parser import isoparse
from typing import Any, Literal, NotRequired, Optional, TypedDict

from flask import request, jsonify, send_from_directory

from db_logging import ChatHistory, Message, _message_bounds, supabase, DEBUG

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


class OldMessage(TypedDict):
    role: NotRequired[str]
    content: str
    data: NotRequired[Any]
    type: NotRequired[str]


type AnyMessage = OldMessage | Message

# ──────────────────────────────────────────
# Supabase DB row  (what .select("*") returns)
# ──────────────────────────────────────────


class OldChatRow(TypedDict):
    id: str  # uuid primary key
    messages: list[OldMessage]
    timestamp: str  # ISO-8601 string from Postgres
    session_id: NoneType


class ChatRow(TypedDict):
    id: str  # uuid primary key
    messages: ChatHistory
    timestamp: str  # ISO-8601 string from Postgres
    session_id: str


type AnyChatRow = OldChatRow | ChatRow


# ──────────────────────────────────────────
# Dashboard API payload
# ──────────────────────────────────────────

MonthKey = str  # "2025-09"

type Day = int  # 1–31
# type Hour = Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
type Hour = int  # 0–23
# type Weekday = Literal[0, 1, 2, 3, 4, 5, 6]
type Weekday = int  # 0–6, where 0=Monday, 6=Sunday


# class FrontendMessage(TypedDict):
#     role: str
#     content: str | list
#     url: NotRequired[str]
#     timestamp: NotRequired[str]


class ChatDetail(TypedDict):
    id: str | None
    chat_timestamp: str | None
    # ISO strings for JSON serialization
    started_at: NotRequired[str]
    ended_at: NotRequired[str]
    duration_seconds: NotRequired[float]
    user_message_count: int
    messages: list[AnyMessage]


class WeekdayCount(TypedDict):
    weekday: str  # "Monday"
    short_label: str  # "Mon"
    count: int


class HourlyCount(TypedDict):
    hour: int  # 0–23
    label: str  # "09:00"
    count: int


class DailyCount(TypedDict):
    date: str  # "2025-09-14"
    label: str  # "14 Sep"
    count: int


class MonthlySummary(TypedDict):
    key: MonthKey
    label: str
    count: int


class MonthDetail(TypedDict):
    month: str
    label: str
    total_chats: int
    avg_user_messages_per_chat: float
    daily_counts: list[DailyCount]
    hourly_counts: list[HourlyCount]
    weekday_counts: list[WeekdayCount]
    chats: Optional[list[ChatDetail]]


class DashboardPayload(TypedDict):
    # general
    total_chats: int
    avg_user_messages_per_chat: float
    hourly_counts: list[HourlyCount]
    weekday_counts: list[WeekdayCount]
    monthly_summary: list[MonthlySummary]
    current_month: MonthKey


# ──────────────────────────────────────────
# In-memory cache
# ──────────────────────────────────────────

tz = ZoneInfo("Europe/Berlin")


def month_key(dt: datetime) -> MonthKey:
    return dt.strftime("%Y-%m")


def build_hourly_count(hour: int, count: int) -> HourlyCount:
    return {"hour": hour, "label": f"{hour:02d}:00", "count": count}


def build_weekday_count(weekday: int, count: int) -> WeekdayCount:
    return {
        "weekday": DAY_NAME[weekday],
        "short_label": DAY_NAME[weekday][:3],
        "count": count,
    }


def build_daily_count(day: int, month_key: MonthKey, count: int) -> DailyCount:
    month = int(month_key[5:7])
    return {
        "date": f"{month_key}-{day:02d}",
        "label": f"{day} {GERMAN_MONTHS[month]}",
        "count": count,
    }


def is_user(message: AnyMessage) -> bool:
    return "role" in message and message["role"] == "user"


class MonthCache:
    START_MONTH = datetime(2025, 9, 1)
    EXPIRY_SECONDS = 5 * 60  # 5 minutes
    _cache: dict[MonthKey, MonthDetail]
    current_month: MonthKey
    last_fetched_current_month: float

    total_chats: int
    total_user_messages: int
    hourly_counts: list[int]
    weekday_counts: list[int]

    def __init__(self):
        self._cache = {}
        self.current_month = month_key(self.current_month_start())
        self.last_fetched_current_month = 0.0
        self.total_chats = 0
        self.total_user_messages = 0
        self.hourly_counts = [0] * 24
        self.weekday_counts = [0] * 7

    @property
    def avg_user_messages_per_chat(self) -> float:
        return (
            self.total_user_messages / self.total_chats if self.total_chats > 0 else 0.0
        )

    @property
    def expired(self) -> bool:
        now = time.time()
        return now - self.last_fetched_current_month > self.EXPIRY_SECONDS

    def load_all(self):
        # fetch everything
        # TODO: long term: stream this if it gets too big, or do it in batches by month
        rows: list[AnyChatRow] = supabase.table("chats").select("*").execute().data  # type: ignore

        if DEBUG:
            print(
                f"Fetched {len(rows)} chat rows from Supabase for cache initialization"
            )

        # setup month grouping
        month_rows: dict[
            MonthKey,
            list[AnyChatRow],
        ] = {}

        # group rows by month
        for row in rows:
            timestamp = isoparse(row["timestamp"])
            _month_key = month_key(timestamp)

            month_rows.setdefault(_month_key, []).append(row)

        # setup counting
        self.total_chats = 0
        self.total_user_messages = 0
        # count together mounths
        for _month_key, _rows in month_rows.items():
            _total_count, _user_message_count, hourly_counts, _, weekday_counts = (
                self.compute_month(_rows, _month_key)
            )

            # update counts
            self.total_chats += _total_count
            self.total_user_messages += _user_message_count
            for hour, count in hourly_counts.items():
                self.hourly_counts[hour] += count
            for weekday, count in weekday_counts.items():
                self.weekday_counts[weekday] += count

        self.last_fetched_current_month = time.time()

    def compute_month(
        self, rows: list[AnyChatRow], _month_key: MonthKey
    ) -> tuple[int, int, dict[Hour, int], dict[Day, int], dict[Weekday, int]]:
        (
            total_count,
            user_message_count,
            hourly_count,
            daily_count,
            weekday_count,
        ) = (0, 0, {h: 0 for h in range(24)}, {}, {wd: 0 for wd in range(7)})

        for row in rows:
            timestamp = isoparse(row["timestamp"])
            day, hour, weekday = timestamp.day, timestamp.hour, timestamp.weekday()
            total_count += 1
            try:
                user_message_count += sum(1 for m in row["messages"] if is_user(m))
            except (KeyError, TypeError):
                print(row["messages"])
                continue
            hourly_count[hour] += 1
            daily_count[day] = daily_count.get(day, 0) + 1
            weekday_count[weekday] += 1

        for day in range(1, max(daily_count.keys(), default=0) + 1):
            daily_count.setdefault(day, 0)

        self._cache[_month_key] = {
            "month": _month_key,
            "label": f"{GERMAN_MONTHS[int(_month_key[5:7])]} {_month_key[:4]}",
            "total_chats": total_count,
            "avg_user_messages_per_chat": (
                user_message_count / total_count if total_count > 0 else 0.0
            ),
            "daily_counts": [
                build_daily_count(day, _month_key, count)
                for day, count in sorted(daily_count.items())
            ],
            "hourly_counts": [
                build_hourly_count(hour, count)
                for hour, count in sorted(hourly_count.items())
            ],
            "weekday_counts": [
                build_weekday_count(weekday, count)
                for weekday, count in sorted(weekday_count.items())
            ],
            "chats": None,
        }

        return (
            total_count,
            user_message_count,
            hourly_count,
            daily_count,
            weekday_count,
        )

    def update_current_month(
        self, include_chats: bool = False
    ) -> None | list[ChatDetail]:
        """
        If you call this, first check if it is actually expired.
        """
        # assert self.current_month in self._cache, "Current month not in cache, cannot update"
        if self.current_month not in self._cache:
            return self.add_month(self.current_month)
        cache_entry = self._cache[self.current_month]
        now = time.time()
        # subtract old current month counts from totals before re-fetching
        self.total_chats -= cache_entry["total_chats"]
        self.total_user_messages -= round(
            cache_entry["avg_user_messages_per_chat"] * cache_entry["total_chats"]
        )
        for hourly, count in enumerate(cache_entry["hourly_counts"]):
            self.hourly_counts[hourly] -= count["count"]
        for weekday, count in enumerate(cache_entry["weekday_counts"]):
            self.weekday_counts[weekday] -= count["count"]

        # re-fetch current month data from DB
        rows = fetch_month_chats(self.current_month)
        # analyze and update cache for current month
        (
            total_count,
            user_message_count,
            hourly_count,
            _,
            weekday_count,
        ) = self.compute_month(rows, self.current_month)
        if include_chats:
            chats = self._cache[self.current_month]["chats"] = analyse_chats(rows)

        # update total counts and averages
        self.total_chats += total_count
        self.total_user_messages += user_message_count
        for hour, count in sorted(hourly_count.items()):
            self.hourly_counts[hour] += count
        for weekday, count in sorted(weekday_count.items()):
            self.weekday_counts[weekday] += count

        self.last_fetched_current_month = now

        if include_chats:
            return chats

    def current_month_rollover(self):
        """
        Checks if current month has changed, and if so, finalizes old month and starts tracking new month.
        """
        new_current_month = month_key(self.current_month_start())
        if new_current_month == self.current_month:
            return

        # finalize old month
        self.update_current_month(include_chats=False)
        # start tracking new month
        self.add_month(new_current_month)

    def add_month(self, new_current_month: MonthKey):
        self.current_month = new_current_month
        self._cache.setdefault(
            self.current_month,
            {
                "month": self.current_month,
                "label": f"{GERMAN_MONTHS[int(self.current_month[5:7])]} {self.current_month[:4]}",
                "total_chats": 0,
                "avg_user_messages_per_chat": 0.0,
                "daily_counts": [],
                "hourly_counts": [],
                "weekday_counts": [],
                "chats": None,
            },
        )
        return self.update_current_month(include_chats=False)

    @staticmethod
    def current_month_start() -> datetime:
        now = datetime.now()
        return datetime(now.year, now.month, 1)


def fetch_month_chats(month_key: MonthKey) -> list[AnyChatRow]:
    month_start = datetime.strptime(month_key + "-01", "%Y-%m-%d").replace(tzinfo=tz)
    next_month_start = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows: list[AnyChatRow] = (
        supabase.table("chats")
        .select("*")
        .gte("timestamp", month_start.isoformat())
        .lt("timestamp", next_month_start.isoformat())
        .order("timestamp", desc=True)
        .execute()
        .data  # type: ignore
    )
    return rows


def analyse_chats(rows: list[AnyChatRow]) -> list[ChatDetail]:
    details: list[ChatDetail] = []
    for row in rows:
        # read values from row
        db_id = row["id"]
        messages = row["messages"]
        if not messages:
            continue
        timestamp = row["timestamp"]
        session_id = row["session_id"]
        # number of user messages
        user_message_count = sum(1 for m in messages if is_user(m))
        detail: ChatDetail = {
            "id": db_id,
            "chat_timestamp": timestamp,
            "user_message_count": user_message_count,
            "messages": messages,  # type: ignore
            # "html": False,
        }
        details.append(detail)
        if session_id is None:
            continue
        # Only new chats after here
        _row: ChatRow = row  # type: ignore
        messages = _row["messages"]
        detail["id"] = session_id
        # detail["html"] = True
        start_ts, end_ts = _message_bounds(messages)
        detail["started_at"] = datetime.fromtimestamp(start_ts).isoformat()
        detail["ended_at"] = datetime.fromtimestamp(end_ts).isoformat()
        detail["duration_seconds"] = end_ts - start_ts

    return details


month_cache = MonthCache()

# Authentication for dashboard routes
API_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
API_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "change-me")


def check_auth(username: str | None, password: str | None) -> bool:
    return username == API_USERNAME and password == API_PASSWORD


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


# Routes
@auth_required
def dashboard_data():
    """
    General dashboard data endpoint. Returns aggregated stats for all months
    """

    month_cache.current_month_rollover()

    if month_cache.expired:
        month_cache.update_current_month(include_chats=True)

    payload: DashboardPayload = {
        "total_chats": month_cache.total_chats,
        "avg_user_messages_per_chat": month_cache.avg_user_messages_per_chat,
        "hourly_counts": [
            build_hourly_count(hour, count)
            for hour, count in enumerate(month_cache.hourly_counts)
        ],
        "weekday_counts": [
            {
                "weekday": DAY_NAME[weekday],
                "short_label": DAY_NAME[weekday][:3],
                "count": count,
            }
            for weekday, count in enumerate(month_cache.weekday_counts)
        ],
        "monthly_summary": [
            {
                "key": month,
                "label": cache_entry["label"],
                "count": cache_entry["total_chats"],
            }
            for month, cache_entry in sorted(month_cache._cache.items())
        ],
        "current_month": month_cache.current_month,
    }

    return jsonify(payload)


def _dashboard_month(_month_key: MonthKey) -> MonthDetail:
    """
    Endpoint for fetching detailed data for a specific month, including individual chats.
    """
    if _month_key == month_cache.current_month and month_cache.expired:
        month_cache.update_current_month(include_chats=True)
    elif not month_cache._cache[_month_key]["chats"]:
        rows = fetch_month_chats(_month_key)
        month_cache._cache[_month_key]["chats"] = analyse_chats(rows)
    return month_cache._cache[_month_key]


@auth_required
def dashboard_month(month: MonthKey):
    month_cache.current_month_rollover()  # check if we need to rollover to new month
    if not month:
        return jsonify({"error": "Missing 'month' query parameter"}), 400
    elif month not in month_cache._cache:
        return jsonify({"error": f"Month '{month}' not found in cache"}), 404

    return jsonify(_dashboard_month(month))


@auth_required
def dashboard_index():
    return send_from_directory("static/dashboard", "index.html")


@auth_required
def admin_index():
    # Hidden admin page: no link from the dashboard, same Basic-Auth gate.
    return send_from_directory("static/admin", "index.html")


# Load cache on startup
month_cache.load_all()

# Define dashboard routes
routes = [
    ("/api/dashboard", dashboard_data),
    ("/api/dashboard/<string:month>", dashboard_month),
    ("/dashboard", dashboard_index),
    ("/dashboard/", dashboard_index),
    ("/admin", admin_index),
    ("/admin/", admin_index),
]
