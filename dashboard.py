import html.entities
import os
import re
import time
from calendar import day_name
from datetime import datetime, timedelta, timezone
from dateutil.parser import isoparse
from typing import Any, Optional

from flask import request, jsonify

from db_logging import ChatHistory, Message, supabase

chat_cache: dict[str, tuple[str, ChatHistory, float]] = {}
CACHE_START_MONTH = datetime(2025, 9, 1, tzinfo=timezone.utc)
CACHE_EXPIRY_SECONDS = 5 * 60
month_cache: dict[str, dict[str, Any]] = {}


def is_real_msg(msg: Message):
    return "role" in msg and "content" in msg


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = isoparse(text)
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def month_key_from_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def get_current_month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def iterate_month_keys(start: datetime, end: datetime) -> list[str]:
    keys: list[str] = []
    pointer = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    limit = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    while pointer <= limit:
        keys.append(month_key_from_datetime(pointer))
        if pointer.month == 12:
            pointer = datetime(pointer.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            pointer = datetime(pointer.year, pointer.month + 1, 1, tzinfo=timezone.utc)
    return keys


def get_tracked_month_keys() -> list[str]:
    current_start = get_current_month_start()
    start = CACHE_START_MONTH
    if current_start < start:
        return [month_key_from_datetime(current_start)]
    return iterate_month_keys(start, current_start)


def prune_month_cache(valid_keys: list[str]) -> None:
    for key in list(month_cache.keys()):
        if key not in valid_keys:
            month_cache.pop(key, None)


def analyze_chat(chat: dict[str, Any]) -> dict[str, Any]:
    messages = chat.get("messages") or []

    parsed_messages: list[dict[str, Any]] = []
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None

    for message in messages:
        msg_ts = parse_iso_datetime(message.get("timestamp"))
        if msg_ts is not None:
            if first_ts is None or msg_ts < first_ts:
                first_ts = msg_ts
            if last_ts is None or msg_ts > last_ts:
                last_ts = msg_ts
        parsed_messages.append(
            {
                "role": message.get("role"),
                "content": message.get("content"),
                "timestamp": msg_ts,
            }
        )

    chat_ts = parse_iso_datetime(chat.get("timestamp") or chat.get("chat_timestamp"))
    if chat_ts is None:
        chat_ts = first_ts or last_ts

    has_duration = first_ts is not None and last_ts is not None and first_ts < last_ts
    duration_seconds = (last_ts - first_ts).total_seconds() if has_duration else 0.0

    return {
        "id": chat.get("id"),
        "chat_timestamp": chat_ts,
        "start_ts": first_ts,
        "end_ts": last_ts,
        "duration_seconds": duration_seconds,
        "has_duration": has_duration,
        "user_message_count": sum(1 for msg in messages if msg.get("role") == "user"),
        "messages": parsed_messages,
    }


def format_month_label(month_key: str) -> str:
    try:
        dt = datetime.strptime(month_key, "%Y-%m")
        return dt.strftime("%B %Y")
    except ValueError:
        return month_key


def get_month_bounds(month_key: str) -> tuple[datetime, datetime]:
    dt = datetime.strptime(month_key, "%Y-%m")
    start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def build_monthly_summary(
    analyses_by_month: dict[str, list[dict[str, Any]]], month_keys: list[str]
) -> list[dict[str, Any]]:
    return [
        {
            "key": month,
            "label": format_month_label(month),
            "count": len(analyses_by_month.get(month, [])),
        }
        for month in month_keys
    ]


def compute_totals(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    total_chats = len(analyses)
    total_user_messages = sum(
        analysis.get("user_message_count", 0) for analysis in analyses
    )
    avg_user_messages = total_user_messages / total_chats if total_chats else 0.0
    return {
        "total_chats": total_chats,
        "avg_user_messages_per_chat": avg_user_messages,
    }


def build_weekday_counts(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[int, int] = {idx: 0 for idx in range(7)}
    for analysis in analyses:
        timestamp = (
            analysis.get("chat_timestamp")
            or analysis.get("start_ts")
            or analysis.get("end_ts")
        )
        if isinstance(timestamp, datetime):
            counts[timestamp.weekday()] += 1

    return [
        {
            "weekday": day_name[idx],
            "short_label": day_name[idx][:3],
            "count": counts[idx],
        }
        for idx in range(7)
    ]


def build_daily_counts(
    analyses: list[dict[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    pointer = start
    buckets: dict[str, dict[str, Any]] = {}
    while pointer < end:
        key = pointer.strftime("%Y-%m-%d")
        buckets[key] = {
            "date": key,
            "label": pointer.strftime("%d %b"),
            "count": 0,
        }
        pointer += timedelta(days=1)

    for analysis in analyses:
        ts = analysis.get("chat_timestamp") or analysis.get("start_ts")
        if not isinstance(ts, datetime):
            continue
        if ts < start or ts >= end:
            continue
        key = ts.strftime("%Y-%m-%d")
        if key in buckets:
            buckets[key]["count"] += 1

    return list(buckets.values())


def build_hourly_counts(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = {hour: 0 for hour in range(24)}
    for analysis in analyses:
        timestamp = (
            analysis.get("chat_timestamp")
            or analysis.get("start_ts")
            or analysis.get("end_ts")
        )
        if isinstance(timestamp, datetime):
            hour = timestamp.hour
            counts[hour] += 1

    return [
        {
            "hour": hour,
            "label": f"{hour:02d}:00",
            "count": counts[hour],
        }
        for hour in range(24)
    ]


def iso_or_none(value: Optional[datetime]) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def chat_detail_sort_key(item: dict[str, Any]) -> datetime:
    for candidate in (
        item.get("ended_at"),
        item.get("chat_timestamp"),
        item.get("started_at"),
    ):
        parsed = parse_iso_datetime(candidate)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def build_month_detail(
    month_analyses: list[dict[str, Any]], month_key: str
) -> dict[str, Any]:
    start, end = get_month_bounds(month_key)
    metrics = compute_totals(month_analyses)
    daily_counts = build_daily_counts(month_analyses, start, end)
    hourly_counts = build_hourly_counts(month_analyses)
    weekday_counts = build_weekday_counts(month_analyses)

    chat_details = []
    for analysis in month_analyses:
        messages_payload = [
            {
                "role": message.get("role"),
                "content": message.get("content"),
                "timestamp": iso_or_none(message.get("timestamp")),
            }
            for message in analysis.get("messages", [])
        ]
        chat_details.append(
            {
                "id": analysis.get("id"),
                "chat_timestamp": iso_or_none(analysis.get("chat_timestamp")),
                "started_at": iso_or_none(analysis.get("start_ts")),
                "ended_at": iso_or_none(analysis.get("end_ts")),
                "duration_seconds": analysis.get("duration_seconds", 0.0),
                "user_message_count": analysis.get("user_message_count", 0),
                "messages": messages_payload,
            }
        )

    chat_details.sort(key=chat_detail_sort_key, reverse=True)

    return {
        "month": month_key,
        "label": format_month_label(month_key),
        "metrics": metrics,
        "daily_counts": daily_counts,
        "hourly_counts": hourly_counts,
        "weekday_counts": weekday_counts,
        "chats": chat_details,
    }


def fetch_chats_for_month(month_key: str) -> list[dict[str, Any]]:
    start, end = get_month_bounds(month_key)
    response = (
        supabase.table("chats")
        .select("*")
        .gte("timestamp", start.isoformat())
        .lt("timestamp", end.isoformat())
        .execute()
    )
    data = response.data
    if not data:
        return []
    return list(data)


def load_month_analyses(
    month_key: str, *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    record = month_cache.get(month_key)
    if record and not force_refresh:
        is_current_month = month_key == month_key_from_datetime(
            get_current_month_start()
        )
        if not is_current_month:
            return record["analyses"]
        if time.time() - record.get("fetched_at", 0) < CACHE_EXPIRY_SECONDS:
            return record["analyses"]

    chats = fetch_chats_for_month(month_key)
    analyses = [analyze_chat(chat) for chat in chats]
    analyses.sort(
        key=lambda item: item.get("chat_timestamp")
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    month_cache[month_key] = {
        "analyses": analyses,
        "fetched_at": time.time(),
    }
    return analyses


def build_dashboard_payload(
    month_key: Optional[str], refresh_current: bool
) -> dict[str, Any]:
    tracked_keys = get_tracked_month_keys()
    prune_month_cache(tracked_keys)
    current_month_key = month_key_from_datetime(get_current_month_start())

    analyses_by_month: dict[str, list[dict[str, Any]]] = {}
    for key in tracked_keys:
        force = refresh_current and key == current_month_key
        analyses_by_month[key] = load_month_analyses(key, force_refresh=force)

    chart_keys = tracked_keys[-12:]
    monthly_summary = build_monthly_summary(analyses_by_month, chart_keys)
    all_analyses = [
        analysis for key in tracked_keys for analysis in analyses_by_month.get(key, [])
    ]
    totals = compute_totals(all_analyses)
    weekday_counts = build_weekday_counts(all_analyses)
    hourly_counts = build_hourly_counts(all_analyses)

    selected_key = month_key or None
    if selected_key == "current":
        selected_key = current_month_key
    if selected_key not in tracked_keys:
        selected_key = None

    selected_month_payload = None
    if selected_key:
        selected_month_payload = build_month_detail(
            analyses_by_month.get(selected_key, []), selected_key
        )

    return {
        "monthly_summary": monthly_summary,
        "totals": totals,
        "selected_month": selected_month_payload,
        "current_month": current_month_key,
        "tracked_months": tracked_keys,
        "weekday_counts": weekday_counts,
        "hourly_counts": hourly_counts,
    }


def dashboard_data():
    month_key = request.args.get("month")
    refresh_current = request.args.get("refreshCurrent", "").lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    payload = build_dashboard_payload(month_key, refresh_current)
    return jsonify(payload)


def gen_key(chat_history: ChatHistory):
    # filter for messages
    return ";".join(
        (msg["role"] + ": " + msg["content"])
        for msg in chat_history
        if is_real_msg(msg)
    )


html_tag_pattern = re.compile(r"<.*?>")


def clean_html_tags(text: str) -> str:
    for key, val in reversed(html.entities.html5.items()):
        # if "quot" in key.lower():
        #     text = text.replace("&quot;", '"')
        #     continue
        text = text.replace("&" + key, val)
    return html_tag_pattern.sub("", text)


def clean_chat_history(chat_history: ChatHistory) -> ChatHistory:
    return [
        {"role": msg["role"], "content": clean_html_tags(msg["content"])}
        if is_real_msg(msg)
        else msg
        for msg in chat_history
    ]


def make_key_chat_history(chat_history: ChatHistory) -> ChatHistory:
    key_chat_history = chat_history[:]
    while True:
        msg = key_chat_history.pop()
        if is_real_msg(msg) and msg["role"] == "user":
            break
    return key_chat_history


routes = [
    ("/api/dashboard", dashboard_data),
]
