"""Re-run July and August with the topic axis (Punkt 3).

Both months already have a report, but they were classified before `thema_id`
existed. This re-runs them against the SAME cause taxonomy — never a re-derived
one, that mistake cost a full restart once — plus a topic list derived once and
shared by both, so the two months stay comparable to each other.

The taxonomy version is bumped. Two reasons, and the second is the load-bearing
one:

  - a run that answers a new question is a different measuring device, and the
    version is how the dashboard knows not to compare across one
  - `classify_month` resumes from a checkpoint keyed by (month, version). At the
    old version it would rejoin the OLD run's checkpoint, whose verdicts carry
    no `thema_id` at all — every chat would come back as topic `neu` with a
    green log, which is exactly the silent-failure shape this project already
    hit once.

    python scripts/topic_backfill.py --check    # zeigt den Plan, ruft nichts auf
    python scripts/topic_backfill.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_quality
import dashboard
import month_aggregate
import month_stats
import pii_scan
import quality_job

MONTHS = ["2026-07", "2026-08"]
TOPIC_PATH = "data/topic-taxonomy.json"
SAMPLE_PER_MONTH = 150


def load_or_build_topics(rows_by_month: dict[str, list]) -> list[dict]:
    """One topic list for both months, derived once and kept on disk.

    On disk rather than derived per run: a list that changes because the sample
    changed is a list nobody can compare across months, and a killed run must
    not silently take the list down with it.
    """
    if os.path.exists(TOPIC_PATH):
        with open(TOPIC_PATH, encoding="utf-8") as f:
            topics = json.load(f)["themen"]
        print(f"[topics] reusing {len(topics)} topics from {TOPIC_PATH}")
        return topics

    sample = []
    for month in MONTHS:
        picked = quality_job._stratified_sample(rows_by_month[month], SAMPLE_PER_MONTH)
        sample += [c for row in picked if (c := chat_quality.prepare_chat(row))]
    print(f"[topics] deriving from {len(sample)} chats across {len(MONTHS)} months")
    # Breiter als der erste Versuch. Mit 12 Themen aus 150 Chats musste jedes
    # Thema ueber 13 % des Monats abdecken, und ein Viertel aller Gespraeche
    # fiel durch: Unterkunft und Zimmer, Verpflegung, Versicherungen,
    # Verfuegbarkeit, Kontakt zu Mitarbeitern, das Herzensmensch-Programm — alles
    # wiederkehrend, keines gross genug fuer die alte Schwelle.
    topics, _ = chat_quality.build_topic_taxonomy(sample, min_topics=16, max_topics=22)

    os.makedirs(os.path.dirname(TOPIC_PATH), exist_ok=True)
    with open(TOPIC_PATH, "w", encoding="utf-8") as f:
        json.dump({"themen": topics}, f, ensure_ascii=False, indent=2)
    print(f"[topics] wrote {len(topics)} topics to {TOPIC_PATH}")
    return topics


def main() -> int:
    check_only = "--check" in sys.argv

    causes, old_version = month_stats.latest_taxonomy()
    if not causes:
        print("[topic-backfill] no stored cause taxonomy — refusing to derive one here")
        return 1
    new_version = old_version + 1
    print(f"[topic-backfill] {len(causes)} causes, taxonomy v{old_version} -> v{new_version}")

    rows_by_month = {}
    for month in MONTHS:
        rows, count = dashboard.fetch_month_chats(month)
        month_stats.assert_complete(rows, count, f"{month} chats")
        rows_by_month[month] = rows
        print(f"[topic-backfill] {month}: {len(rows)} chats")

    topics = load_or_build_topics(rows_by_month)
    for topic in topics:
        print(f"    {topic['thema_id']}  {topic['label']}")

    if check_only:
        print("\n[topic-backfill] --check: nothing called, nothing written")
        return 0

    for month in MONTHS:
        rows = rows_by_month[month]
        result = chat_quality.classify_month(
            rows,
            chat_quality.active_causes(causes),
            month,
            taxonomy_version=new_version,
            topics=chat_quality.active_topics(topics),
        )
        quality = month_aggregate.aggregate_quality(
            rows,
            result["verdicts"],
            month,
            run_id=result["run_id"],
            model=result["model"],
            prompt_version=result["prompt_version"],
            taxonomy_version=new_version,
            status=result["status"],
            unmapped=len(result["unmapped"]),
        )

        # Dieselbe Sperre wie im Nachtlauf: der Payload wird nur geschrieben,
        # wenn er die PII-Pruefung besteht. Die Begruendung, month_stats
        # unbefristet zu halten, ist seine Anonymitaet — ein Treffer hier heisst,
        # dass diese Begruendung fuer diesen Monat nicht stimmt.
        scan = pii_scan.scan(quality, rows)
        quality["pii_scan"] = scan
        if not scan["clean"]:
            print(f"[topic-backfill] {month}: PII scan failed — NOT persisting")
            return 1

        topic_total = sum(sum(v) for v in quality["themen"].values())
        classified = sum(quality["classified_chats"])
        print(
            f"[topic-backfill] {month}: {classified} klassifiziert, "
            f"Themen decken {topic_total} — {'deckungsgleich' if topic_total == classified else 'ABWEICHUNG'}"
        )

        ok = month_stats.save_quality(
            month,
            quality,
            causes,
            version=month_aggregate.SCHEMA_VERSION,
            taxonomy_version=new_version,
            run_id=result["run_id"],
            status=result["status"],
            topics=topics,
        )
        print(f"[topic-backfill] {month}: saved={ok}, status={result['status']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
