"""Backfill month_stats from an Assignment run (A11 Schritt 3).

The Assignment already paid Gemini for July and August and wrote the result to
data/. This puts those numbers into the table instead of letting the nightly job
buy the same answers again.

Nothing here calls a model. It re-reads the raw chats, checks the stored
aggregate against them, scans the payload for personal data, and only then
writes. A backfill that cannot be checked against source is just a second copy
of an assumption.

    python scripts/backfill.py [month …]      # default: 2026-07 2026-08
    python scripts/backfill.py --check        # verify what is stored, write nothing
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_segments
import dashboard
import month_aggregate
import month_stats
import pii_scan

MONTHS = ["2026-07", "2026-08"]
DATA_DIR = "data"


def load_assignment(month: str) -> tuple[dict, list[dict]] | None:
    path = os.path.join(DATA_DIR, f"assignment-{month}.json")
    taxonomy_path = os.path.join(DATA_DIR, "assignment-taxonomy.json")
    if not os.path.exists(path):
        print(f"[backfill] {month}: no {path}, skipping")
        return None
    with open(path, encoding="utf-8") as f:
        quality = json.load(f)["aggregate"]
    with open(taxonomy_path, encoding="utf-8") as f:
        taxonomy = json.load(f)["taxonomy"]
    return quality, taxonomy


def verify(month: str, quality: dict, rows: list) -> list[str]:
    """Cross-check the stored aggregate against the raw chats. Returns problems."""
    problems: list[str] = []
    counts = month_aggregate.aggregate_month(rows, month)

    # The segment split of the classified chats must match the split the raw
    # rows produce. It is computed by the same deterministic rule on both sides,
    # so a mismatch means the run saw a different set of chats than exists now.
    classified = quality["classified_chats"]
    if sum(classified) != len(rows):
        problems.append(
            f"klassifiziert {sum(classified)} von {len(rows)} Chats "
            "(unmapped bleibt sichtbar, aber das Aggregat deckt nicht den Monat)"
        )
    if classified != counts["total_chats"] and sum(classified) == len(rows):
        problems.append(
            f"Segment-Split weicht ab: gespeichert {classified}, "
            f"aus den Rohdaten {counts['total_chats']}"
        )

    # Every chat id in the drill-down index must be a chat of this month.
    known = {str(row.get("id")) for row in rows}
    unknown = {
        cid
        for ids in (quality.get("chat_ids_by_cause") or {}).values()
        for cid in ids
        if cid not in known
    }
    if unknown:
        problems.append(f"{len(unknown)} Chat-IDs im Drill-down gehören nicht zu {month}")

    # The cause counts have to add up to the conversations that were not
    # answered well. This is the number the frontend states, so if it is wrong
    # the page contradicts itself.
    bad = sum(
        quality["qualitaet"].get(key, [0, 0, 0])[i]
        for key in ("ausgewichen", "abgebrochen", "falsch")
        for i in range(3)
    )
    named = sum(sum(v) for k, v in quality["ursachen"].items() if k != "neu")
    neu = sum(quality["ursachen"].get("neu", [0, 0, 0]))
    if named + neu != bad:
        problems.append(
            f"Ursachen addieren sich nicht: {named} benannt + {neu} neu != {bad} schlechte Antworten"
        )

    return problems


def main(months: list[str], check_only: bool = False) -> int:
    failures = 0
    for month in months:
        loaded = load_assignment(month)
        if not loaded:
            continue
        quality, taxonomy = loaded

        rows, count = dashboard.fetch_month_chats(month)
        try:
            month_stats.assert_complete(rows, count, f"chats {month}")
        except month_stats.RowCountMismatch as exc:
            print(f"[backfill] {month}: {exc}")
            failures += 1
            continue

        problems = verify(month, quality, rows)
        scan = pii_scan.scan(quality, rows)
        quality["pii_scan"] = scan
        if not scan["clean"]:
            problems.append(
                f"PII-Scan: {len(scan['session_id_hits'])} tokenförmig, "
                f"{len(scan['booking_number_hits'])} Buchungsnummern, "
                f"{len(scan['verbatim_hits'])} wörtlich"
            )

        counts = month_aggregate.aggregate_month(rows, month)
        heatmap_total = sum(sum(sum(c) for c in row) for row in counts["heatmap"])
        print(
            f"[backfill] {month}: {len(rows)} Chats (Server zählt {count}), "
            f"Segmente {counts['total_chats']}, Heatmap-Summe {heatmap_total}, "
            f"Status {quality['status']}"
        )

        if problems:
            for problem in problems:
                print(f"           PROBLEM: {problem}")
            failures += 1
            continue

        if check_only:
            print("           geprüft, nichts geschrieben (--check)")
            continue

        ok = month_stats.save_counts(month, counts, month_aggregate.SCHEMA_VERSION)
        ok = (
            month_stats.save_quality(
                month,
                quality,
                taxonomy,
                version=month_aggregate.SCHEMA_VERSION,
                taxonomy_version=quality.get("taxonomy_version", 1),
                run_id=quality.get("run_id", ""),
                status=quality.get("status", "ok"),
            )
            and ok
        )
        if not ok:
            print(f"           SCHREIBEN FEHLGESCHLAGEN für {month}")
            failures += 1
            continue

        # Read it back rather than trusting the write.
        stored = month_stats.load_month(month)
        back = (stored or {}).get("quality") or {}
        if sum(back.get("classified_chats", [0, 0, 0])) != sum(quality["classified_chats"]):
            print(f"           RÜCKLESEN weicht ab für {month}")
            failures += 1
            continue
        print(
            f"           geschrieben und zurückgelesen: "
            f"{len(back.get('ursachen') or {})} Ursachen, "
            f"{len(back.get('laender') or {})} Länder, "
            f"Taxonomie v{stored.get('taxonomy_version')}"
        )

    return failures


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args or MONTHS, check_only="--check" in sys.argv))
