"""Runs the paid classification OFF the request path.

Non-negotiable, and the reason this module exists rather than a function call
in ``dashboard.py``: this service runs on ONE gunicorn worker that also proxies
the entire website, with ``GUNICORN_TIMEOUT=120``. A classification run takes
minutes and costs money. Doing it inline would mean the whole site hangs behind
it, and every dashboard refresh would start another paid run.

So the HTTP side only ever ENQUEUES and reads status. It never waits, never
triggers a run implicitly, and a second request for a month already running is a
no-op rather than a second run.
"""

import threading
import time
from datetime import timedelta
from typing import Any

import chat_quality
import month_aggregate
import month_stats
import pii_scan

# One lock per month, plus a lock guarding the dict itself. A month is either
# running or it is not; concurrent requests collapse onto the same run.
_locks_guard = threading.Lock()
_running: dict[str, dict[str, Any]] = {}

# How many chats the taxonomy is derived from. Stratified over segments and
# months by the caller — a chronological seed would not know MeinChamäleon or
# Agentur, which only went live in July and late July/August 2026.
TAXONOMY_SAMPLE_SIZE = 150


def status(month: str) -> dict[str, Any]:
    """What the API reports for a month: running, or what is stored."""
    with _locks_guard:
        active = _running.get(month)
    if active:
        return {
            "status": "running",
            "started_at": active["started_at"],
            "batches_done": active.get("batches_done", 0),
        }
    row = month_stats.load_month(month)
    if not row or not row.get("quality"):
        return {"status": "missing"}
    return {
        "status": row.get("quality_status") or "ok",
        "computed_at": row.get("quality_computed_at"),
        "run_id": row.get("run_id"),
    }


def is_running(month: str) -> bool:
    with _locks_guard:
        return month in _running


def enqueue(month: str, fetch_rows) -> dict[str, Any]:
    """Start a run for ``month`` in the background, or report the one in flight.

    ``fetch_rows`` is a zero-argument callable returning the month's raw rows —
    injected so this module never reaches into the dashboard's cache and can be
    driven by the scheduler and by an admin request alike.
    """
    with _locks_guard:
        if month in _running:
            return {"status": "running", "started_at": _running[month]["started_at"]}
        _running[month] = {"started_at": time.time(), "batches_done": 0}

    thread = threading.Thread(
        target=_run, args=(month, fetch_rows), name=f"quality-{month}", daemon=True
    )
    thread.start()
    return {"status": "started"}


def _run(month: str, fetch_rows) -> None:
    try:
        rows = fetch_rows()
        taxonomy, taxonomy_version = month_stats.latest_taxonomy()

        if not taxonomy:
            # First run ever: derive the taxonomy from a sample of THIS month.
            # Every later month inherits it, so cause IDs stay comparable.
            sample = _stratified_sample(rows, TAXONOMY_SAMPLE_SIZE)
            prepared = [c for row in sample if (c := chat_quality.prepare_chat(row))]
            taxonomy, _ = chat_quality.build_taxonomy(prepared)
            taxonomy_version = 1
            print(f"[quality-job] {month}: derived taxonomy v1 with {len(taxonomy)} causes")

        result = chat_quality.classify_month(
            rows,
            chat_quality.active_causes(taxonomy),
            month,
            taxonomy_version=taxonomy_version,
        )
        quality = month_aggregate.aggregate_quality(
            rows,
            result["verdicts"],
            month,
            run_id=result["run_id"],
            model=result["model"],
            prompt_version=result["prompt_version"],
            taxonomy_version=taxonomy_version,
            status=result["status"],
            unmapped=len(result["unmapped"]),
        )
        # Check the payload for personal data BEFORE it is written. The whole
        # justification for keeping `month_stats` indefinitely is that it is
        # anonymous; a finding here means that claim is false for this month,
        # and the row does not get written.
        scan = pii_scan.scan(quality, rows)
        quality["pii_scan"] = scan
        if not scan["clean"]:
            print(
                f"[quality-job] {month}: PII scan found "
                f"{len(scan['session_id_hits'])} token-shaped, "
                f"{len(scan['booking_number_hits'])} booking-number and "
                f"{len(scan['verbatim_hits'])} verbatim hits — NOT persisting"
            )
            return

        month_stats.save_quality(
            month,
            quality,
            taxonomy,
            version=month_aggregate.SCHEMA_VERSION,
            taxonomy_version=taxonomy_version,
            run_id=result["run_id"],
            status=result["status"],
        )
    except Exception as e:  # noqa: BLE001 — a failed run must not kill the worker
        print(f"[quality-job] {month}: run failed: {e}")
    finally:
        with _locks_guard:
            _running.pop(month, None)


def _stratified_sample(rows: list[Any], size: int) -> list[Any]:
    """Sample evenly across the three segments, deterministically.

    Deterministic (sorted by id, round-robin over segments) rather than random:
    a taxonomy that changes because the sample changed is a taxonomy nobody can
    reason about across runs.
    """
    import chat_segments

    buckets: dict[str, list[Any]] = {s: [] for s in chat_segments.SEGMENTS}
    for row in sorted(rows, key=lambda r: str(r.get("id") or "")):
        messages = row.get("messages")
        segment = chat_segments.segment_of_chat(
            messages if isinstance(messages, list) else []
        )
        buckets[segment].append(row)

    sample: list[Any] = []
    index = 0
    while len(sample) < size and any(len(b) > index for b in buckets.values()):
        for segment in chat_segments.SEGMENTS:
            if len(sample) >= size:
                break
            if len(buckets[segment]) > index:
                sample.append(buckets[segment][index])
        index += 1
    return sample


# ──────────────────────────────────────────
# Daily schedule
# ──────────────────────────────────────────

# 04:00 Europe/Berlin. Deliberately after the other two daily jobs — sitemap
# sync runs at 02:00 and the travel-index rebuild at 03:00, and the country
# prior reads that index. Starting before it would mean classifying against
# yesterday's index for no reason.
RUN_HOUR = 4


def start_scheduler(fetch_rows_for) -> None:
    """Daily check: report the previous month once, as soon as it is closed.

    ``fetch_rows_for`` is ``month -> rows``. Injected rather than imported so
    this module keeps knowing nothing about the dashboard's cache.

    A closed month is computed ONCE — its chats cannot change, so a second run
    would pay again for the same answer.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone="Europe/Berlin")
    scheduler.add_job(
        lambda: run_due_months(fetch_rows_for),
        CronTrigger(hour=RUN_HOUR, minute=0),
        id="quality-monthly",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[quality-job] scheduler started, daily at {RUN_HOUR:02d}:00 Europe/Berlin")


def run_due_months(fetch_rows_for) -> None:
    """Report a month as soon as it is CLOSED, not when it hits the cutoff.

    The retention rule hides a month's raw chats 90 days after its first day.
    Computing the report at that moment would put the run and the disappearance
    of its source data on the same day: one failed run and the month has neither
    chats nor report, and nobody finds out until they look. Running at month
    close leaves roughly three months of slack to notice and retry.

    The running month is never reported — its numbers still change, and a
    partial report invites being read as final.
    """
    import month_aggregate

    now = month_aggregate.current_month_start()
    previous = (now - timedelta(days=1)).strftime("%Y-%m")

    if is_running(previous):
        return
    if status(previous).get("status") in ("ok", "partial"):
        return
    print(f"[quality-job] {previous} ist abgeschlossen und hat keinen Report — starte Lauf")
    enqueue(previous, lambda m=previous: fetch_rows_for(m))
