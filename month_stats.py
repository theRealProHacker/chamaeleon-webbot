"""Supabase persistence for the monthly dashboard aggregates.

One row per month in ``month_stats``, latest write wins. The DDL lives in
``sql/month_stats.sql`` and is applied BY HAND in the Supabase SQL editor — the
API client cannot run DDL.

Every call fails open. A missing table, a Supabase outage or a malformed row
must never take the dashboard down: the deterministic axes are computed live
anyway, and the LLM axes then report ``status: missing``, which the frontend
renders as a state rather than as a zero. Failing open matters more here than
elsewhere because the manual DDL step is the likeliest place for this to stall
— the identical step for ``sitemap_versions`` has been open since 2026-07-06.

The table is a CACHE, not a record: the raw chats stay, so anything in here can
be recomputed and a bad row can simply be dropped.
"""

import time
from datetime import datetime, timezone
from typing import Any

from db_logging import supabase

TABLE = "month_stats"


class RowCountMismatch(RuntimeError):
    """A select returned fewer rows than the server counted."""


def assert_complete(rows: list[Any], count: int | None, what: str) -> None:
    """Refuse to persist an aggregate built from a silently truncated select.

    PostgREST caps a select at ``db-max-rows`` when configured and says nothing
    about it — the response is simply short. Computing a month from a truncated
    read and writing it as the month's aggregate turns a transient limit into a
    permanently wrong stored number, which is exactly the failure the cache is
    not allowed to have.

    Whether the limit is actually set for this project is disputed: a live probe
    found none, the owner's figure was 20.000. The assertion costs one extra
    counted request and settles the question at the moment it would matter, so
    it is worth having under either answer.
    """
    if count is None:
        return
    if len(rows) != count:
        raise RowCountMismatch(
            f"{what}: selected {len(rows)} rows but the server counts {count}. "
            "Refusing to persist an aggregate from a truncated read."
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_month(month: str) -> dict | None:
    """The stored row for one month, or None (missing table included)."""
    try:
        rows = (
            supabase.table(TABLE).select("*").eq("month", month).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception as e:
        print(f"[month-stats] load failed (table missing? see sql/month_stats.sql): {e}")
        return None


def load_all() -> dict[str, dict]:
    """Every stored month, keyed by month. Empty dict on any failure."""
    try:
        rows = supabase.table(TABLE).select("*").execute().data or []
        return {row["month"]: row for row in rows}
    except Exception as e:
        print(f"[month-stats] load_all failed: {e}")
        return {}


def save_counts(month: str, counts: dict, version: int) -> bool:
    """Upsert the deterministic half. Never touches the paid half."""
    return _upsert(
        month,
        {
            "counts": counts,
            "counts_computed_at": _now(),
            "counts_version": version,
        },
    )


def save_quality(
    month: str,
    quality: dict,
    taxonomy: list[dict],
    version: int,
    taxonomy_version: int,
    run_id: str,
    status: str,
) -> bool:
    """Upsert the paid half plus the taxonomy it was classified against.

    The taxonomy travels with the month on purpose: a month must always be read
    with the taxonomy it was RUN with. Reading July's numbers through August's
    taxonomy compares two different measuring devices.
    """
    return _upsert(
        month,
        {
            "quality": quality,
            "quality_computed_at": _now(),
            "quality_version": version,
            "quality_status": status,
            "run_id": run_id,
            "taxonomy": taxonomy,
            "taxonomy_version": taxonomy_version,
        },
    )


def _upsert(month: str, fields: dict) -> bool:
    row = {"month": month, "computed_at": _now(), **fields}
    try:
        supabase.table(TABLE).upsert(row, on_conflict="month").execute()
        return True
    except Exception as e:
        print(f"[month-stats] save failed for {month}: {e}")
        return False


def drop_month(month: str) -> bool:
    """Delete one cached month so it gets recomputed. Safe by construction."""
    try:
        supabase.table(TABLE).delete().eq("month", month).execute()
        return True
    except Exception as e:
        print(f"[month-stats] drop failed for {month}: {e}")
        return False


def latest_taxonomy() -> tuple[list[dict], int]:
    """The newest stored taxonomy and its version, or ([], 0).

    This is what a new month is classified against (A4): month N uses month
    N−1's taxonomy plus an explicit ``neu`` bucket, so cause IDs stay comparable
    across months.
    """
    try:
        rows = (
            supabase.table(TABLE)
            .select("taxonomy, taxonomy_version")
            .not_.is_("taxonomy", "null")
            .order("taxonomy_version", desc=True)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception as e:
        print(f"[month-stats] taxonomy load failed: {e}")
        return [], 0
    if not rows:
        return [], 0
    return rows[0].get("taxonomy") or [], rows[0].get("taxonomy_version") or 0
