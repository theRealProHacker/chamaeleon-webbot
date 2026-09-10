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
import month_summary
import pii_scan

# One lock per month, plus a lock guarding the dict itself. A month is either
# running or it is not; concurrent requests collapse onto the same run.
_locks_guard = threading.Lock()
_running: dict[str, dict[str, Any]] = {}
# Dasselbe fuer den Kurzreport: ein Aufruf je Monat, nie zwei zugleich.
_summarizing: set[str] = set()

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


def enqueue(month: str, fetch_rows_for) -> dict[str, Any]:
    """Start a run for ``month`` in the background, or report the one in flight.

    ``fetch_rows_for`` is ``month -> rows`` — injected so this module never
    reaches into the dashboard's cache and can be driven by the scheduler and
    by an admin request alike. It takes the month as an argument because the
    Kurzreport at the end of the run also needs the PREVIOUS month's rows.
    """
    with _locks_guard:
        if month in _running:
            return {"status": "running", "started_at": _running[month]["started_at"]}
        _running[month] = {"started_at": time.time(), "batches_done": 0}

    thread = threading.Thread(
        target=_run, args=(month, fetch_rows_for), name=f"quality-{month}", daemon=True
    )
    thread.start()
    return {"status": "started"}


def _run(month: str, fetch_rows_for) -> None:
    try:
        rows = fetch_rows_for(month)
        taxonomy, taxonomy_version = month_stats.latest_taxonomy()
        topics = month_stats.latest_topics()

        # Der Stichprobe wird einmal gezogen und von beiden Achsen benutzt: sie
        # ist deterministisch, also ist derselbe Schnitt durch den Monat auch
        # derselbe fuer Ursachen und Themen.
        sample_prepared: list[dict] | None = None

        def _sample() -> list[dict]:
            nonlocal sample_prepared
            if sample_prepared is None:
                sample = _stratified_sample(rows, TAXONOMY_SAMPLE_SIZE)
                sample_prepared = [
                    c for row in sample if (c := chat_quality.prepare_chat(row))
                ]
            return sample_prepared

        if not taxonomy:
            # First run ever: derive the taxonomy from a sample of THIS month.
            # Every later month inherits it, so cause IDs stay comparable.
            taxonomy, _ = chat_quality.build_taxonomy(_sample())
            taxonomy_version = 1
            print(f"[quality-job] {month}: derived taxonomy v1 with {len(taxonomy)} causes")

        if not topics:
            # Same, one month later in the project's life: every month written
            # before the topic axis existed has no list to inherit.
            topics, _ = chat_quality.build_topic_taxonomy(_sample())
            print(f"[quality-job] {month}: derived topic list with {len(topics)} topics")

        result = chat_quality.classify_month(
            rows,
            chat_quality.active_causes(taxonomy),
            month,
            taxonomy_version=taxonomy_version,
            topics=chat_quality.active_topics(topics),
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

        saved = month_stats.save_quality(
            month,
            quality,
            taxonomy,
            version=month_aggregate.SCHEMA_VERSION,
            taxonomy_version=taxonomy_version,
            run_id=result["run_id"],
            status=result["status"],
            topics=topics,
        )
        # Der Kurzreport haengt am Lauf, nicht am Aufruf (A5): einmal beim
        # Monatsabschluss, ueber das eben geschriebene Aggregat. Nur fuer
        # vollstaendige Laeufe — ein Text ueber eine halbe Klassifikation
        # liest sich wie einer ueber den ganzen Monat.
        if saved and result["status"] == "ok":
            _summarize(month, rows, fetch_rows_for)
    except Exception as e:  # noqa: BLE001 — a failed run must not kill the worker
        print(f"[quality-job] {month}: run failed: {e}")
    finally:
        with _locks_guard:
            _running.pop(month, None)


# ──────────────────────────────────────────
# KI-Kurzreport
# ──────────────────────────────────────────


def summary_running(month: str) -> bool:
    with _locks_guard:
        return month in _summarizing


def enqueue_summary(month: str, fetch_rows_for) -> dict[str, Any]:
    """Den Kurzreport eines bereits ausgewerteten Monats (neu) erzeugen.

    Fuer Monate, die vor dem Kurzreport ausgewertet wurden, und fuer eine neue
    Prompt-Version. Ohne fertige Auswertung gibt es nichts zusammenzufassen —
    das wird hier abgewiesen, nicht im Thread, damit der Aufrufer es erfaehrt.
    """
    if is_running(month):
        return {"status": "running"}
    row = month_stats.load_month(month)
    if not row or not row.get("quality") or (row.get("quality_status") or "ok") != "ok":
        return {"status": "missing"}
    with _locks_guard:
        if month in _summarizing:
            return {"status": "running"}
        _summarizing.add(month)

    def _job():
        try:
            _summarize(month, fetch_rows_for(month), fetch_rows_for, row)
        except Exception as e:  # noqa: BLE001
            print(f"[quality-job] {month}: Kurzreport fehlgeschlagen: {e}")
        finally:
            with _locks_guard:
                _summarizing.discard(month)

    threading.Thread(target=_job, name=f"summary-{month}", daemon=True).start()
    return {"status": "started"}


def _summarize(month: str, rows, fetch_rows_for, row: dict | None = None) -> None:
    """Ein Gemini-Aufruf ueber das gespeicherte Aggregat, dann ein Upsert.

    Schlaegt der Aufruf fehl, wird nichts geschrieben: der Monat behaelt seinen
    vorherigen Kurzreport, falls einer da ist (A5). Der Vormonat wird nur
    verglichen, wenn er gegen dieselbe Taxonomie gelaufen ist — dieselbe Regel
    wie in den Delta-Spalten des Reports.
    """
    with _locks_guard:
        _summarizing.add(month)
    try:
        row = row or month_stats.load_month(month)
        quality = (row or {}).get("quality")
        if not quality:
            return
        version = quality.get("taxonomy_version", 0)
        causes, topics = month_stats.split_taxonomy((row or {}).get("taxonomy"))
        taxonomy = chat_quality.mark_referral(causes, version)

        previous = (
            month_aggregate.month_bounds(month)[0] - timedelta(days=1)
        ).strftime("%Y-%m")
        previous_row = month_stats.load_month(previous) or {}
        previous_quality = previous_row.get("quality")
        if not previous_quality or previous_quality.get("taxonomy_version") != version:
            previous_quality = None
        try:
            previous_rows = fetch_rows_for(previous)
        except Exception as e:  # noqa: BLE001 — ohne Vormonat steht der Text trotzdem
            print(f"[quality-job] {month}: Vormonat {previous} nicht lesbar: {e}")
            previous_rows = []

        data = month_summary.build_input(
            month,
            month_aggregate.aggregate_month(rows, month) if rows else None,
            quality,
            taxonomy,
            topics,
            month_aggregate.aggregate_month(previous_rows, previous) if previous_rows else None,
            previous_quality,
        )
        summary = month_summary.generate(data)
        if month_stats.save_summary(month, summary):
            print(f"[quality-job] {month}: Kurzreport gespeichert")
    except Exception as e:  # noqa: BLE001
        print(f"[quality-job] {month}: Kurzreport fehlgeschlagen: {e}")
    finally:
        with _locks_guard:
            _summarizing.discard(month)


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
        # Ausgewertet, aber ohne Kurzreport — Monate von vor dem Kurzreport,
        # oder ein Gemini-Aufruf, der beim Lauf fehlschlug. Einmal nachholen.
        row = month_stats.load_month(previous) or {}
        if not row.get("summary") and not summary_running(previous):
            print(f"[quality-job] {previous} hat keinen Kurzreport — erzeuge ihn")
            enqueue_summary(previous, fetch_rows_for)
        return
    print(f"[quality-job] {previous} ist abgeschlossen und hat keinen Report — starte Lauf")
    enqueue(previous, fetch_rows_for)
