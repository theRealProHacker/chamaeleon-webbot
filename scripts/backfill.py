"""Nachfaltung: die Kreuztabelle Qualität × Ursache in month_stats nachtragen.

Juli und August 2026 sind bezahlt und klassifiziert. Was in der Zeile fehlt, ist
die ZELLE: `qualitaet` und `ursachen` sind zwei Randverteilungen derselben
Tabelle, und die Verweis-Regel (Punkt 2 der Kundenliste) muss wissen, wie sich
die Verweis-Ursache auf `ausgewichen` und `abgebrochen` verteilt. Die Verdicts
selbst hat der Lauf verworfen; sie liegen nur noch in `data/quality_runs/`.

Kein Modellaufruf. Die Kreuztabelle wird aus den gespeicherten Verdicts
gefaltet, gegen die Randverteilungen der Tabelle geprüft und nur dann
geschrieben — und geschrieben wird die BESTEHENDE Zeile plus ein Feld, nicht
eine neu gerechnete. Damit ist SC4 nicht nachgewiesen, sondern strukturell wahr:
was nicht angefasst wird, kann nicht abweichen.

    python scripts/backfill.py --check           # nur prüfen, nichts schreiben
    python scripts/backfill.py                   # Juli und August schreiben
    python scripts/backfill.py --dry-key 2026-07 # Probelauf auf 2026-07-test
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard
import month_aggregate
import month_stats
import pii_scan

MONTHS = ["2026-07", "2026-08"]
RUN_DIR = "data/quality_runs"
RUN_SUFFIX = "t3-p3"

# Die sieben Felder, die `save_quality` schreibt. `quality_computed_at` ist
# ausgenommen und das ist keine Nachlässigkeit: der Upsert setzt es bei jedem
# Schreiben neu, einen Pfad, der einzelne Felder anfasst, gibt es nicht.
CHECKED_FIELDS = (
    "quality",
    "quality_version",
    "quality_status",
    "run_id",
    "taxonomy",
    "taxonomy_version",
)


def load_verdicts(month: str) -> list[dict] | None:
    """Die Verdicts des t3-Laufs. NICHT data/assignment-*.json — das ist v1.

    Der v1-Lauf (t1) steht mit anderen Zahlen in data/ und ist überholt: u01
    liegt dort bei 209 statt bei 372. Wer ihn hier einliest, schreibt eine
    Kreuztabelle, die zu den Randverteilungen der Tabelle nicht passt — was der
    Abgleich unten fängt, aber teurer als ein richtiger Dateiname.
    """
    path = os.path.join(RUN_DIR, f"{month}-{RUN_SUFFIX}.jsonl")
    if not os.path.exists(path):
        print(f"[nachfaltung] {month}: {path} fehlt — das Verzeichnis ist gitignored")
        return None
    verdicts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                verdicts.append(json.loads(line))
    return verdicts


def cross_table(month: str, verdicts: list[dict], rows: list) -> dict:
    """Nur die Kreuztabelle. Alles andere am Aggregat wird verworfen."""
    folded = month_aggregate.aggregate_quality(rows, verdicts, month)
    return folded["qualitaet_ursache"]


def check_marginals(cross: dict, stored: dict) -> list[str]:
    """Die Zelle muss die Randverteilungen der Zeile ergeben, sonst passt sie nicht."""
    problems: list[str] = []

    by_cause: dict[str, list[int]] = {}
    for causes in cross.values():
        for cause, vector in causes.items():
            target = by_cause.setdefault(cause, month_aggregate.empty_vector())
            for i, value in enumerate(vector):
                target[i] += value

    stored_causes = {
        cause: vector
        for cause, vector in (stored.get("ursachen") or {}).items()
        if cause != "neu"
    }
    folded_named = {c: v for c, v in by_cause.items() if c != "neu"}
    if folded_named != stored_causes:
        only_folded = set(folded_named) - set(stored_causes)
        only_stored = set(stored_causes) - set(folded_named)
        differing = {
            c for c in set(folded_named) & set(stored_causes)
            if folded_named[c] != stored_causes[c]
        }
        problems.append(
            "Ursachen-Randverteilung weicht ab: "
            f"nur gefaltet {sorted(only_folded)}, nur gespeichert {sorted(only_stored)}, "
            f"verschieden {sorted(differing)}"
        )

    quality = stored.get("qualitaet") or {}
    for name, causes in cross.items():
        if name not in quality:
            problems.append(f"Qualitätsklasse '{name}' steht nicht in der Zeile")
            continue
        for i in range(len(month_aggregate.SEGMENTS)):
            cell = sum(vector[i] for vector in causes.values())
            if cell > quality[name][i]:
                problems.append(
                    f"'{name}' Segment {i}: Kreuztabelle {cell} > Randverteilung "
                    f"{quality[name][i]}"
                )
    return problems


def diff_untouched(before: dict, after: dict) -> list[str]:
    """Byte-genauer Vergleich aller geprüften Felder vor und nach dem Schreiben."""
    problems = []
    for field in CHECKED_FIELDS:
        left, right = before.get(field), after.get(field)
        if field == "quality":
            # Genau ein Feld darf hinzugekommen sein.
            left = {k: v for k, v in (left or {}).items() if k != "qualitaet_ursache"}
            right = {k: v for k, v in (right or {}).items() if k != "qualitaet_ursache"}
        if json.dumps(left, sort_keys=True, ensure_ascii=False) != json.dumps(
            right, sort_keys=True, ensure_ascii=False
        ):
            problems.append(f"{field} hat sich geändert")
    return problems


def main(months: list[str], check_only: bool, dry_key: str | None) -> int:
    failures = 0
    for month in months:
        row = month_stats.load_month(month)
        stored = (row or {}).get("quality")
        if not stored:
            print(f"[nachfaltung] {month}: keine Zeile in month_stats")
            failures += 1
            continue
        if "qualitaet_ursache" in stored and not check_only:
            print(f"[nachfaltung] {month}: Kreuztabelle steht schon, nichts zu tun")
            continue

        verdicts = load_verdicts(month)
        if verdicts is None:
            failures += 1
            continue

        rows, count = dashboard.fetch_month_chats(month)
        try:
            month_stats.assert_complete(rows, count, f"chats {month}")
        except month_stats.RowCountMismatch as exc:
            print(f"[nachfaltung] {month}: {exc}")
            failures += 1
            continue

        cross = cross_table(month, verdicts, rows)
        problems = check_marginals(cross, stored)

        cells = sum(len(c) for c in cross.values())
        print(
            f"[nachfaltung] {month}: {len(verdicts)} Verdicts, {len(rows)} Chats, "
            f"{len(cross)} Klassen / {cells} Zellen"
        )
        if problems:
            for problem in problems:
                print(f"              PROBLEM: {problem}")
            failures += 1
            continue

        # Die neue Zeile ist die alte plus ein Feld. Nichts wird neu gerechnet.
        written = dict(stored)
        written["qualitaet_ursache"] = cross

        # Der Scan laeuft ueber die NEUE Zeile, sein Ergebnis wird aber nicht
        # zurueckgeschrieben: die Kreuztabelle traegt nur Ursachen-IDs und
        # Zahlen, kein Freitext, also bleibt der gespeicherte Befund gueltig.
        # Ihn zu ueberschreiben waere ein siebtes geaendertes Feld fuer nichts.
        scan = pii_scan.scan(written, rows)
        if not scan["clean"]:
            print(
                f"              PROBLEM: PII-Scan: "
                f"{len(scan['session_id_hits'])} tokenförmig, "
                f"{len(scan['booking_number_hits'])} Buchungsnummern, "
                f"{len(scan['verbatim_hits'])} wörtlich"
            )
            failures += 1
            continue

        if check_only:
            print("              geprüft, nichts geschrieben (--check)")
            continue

        causes, topics = month_stats.split_taxonomy((row or {}).get("taxonomy"))
        target = f"{month}-test" if dry_key == month else month
        ok = month_stats.save_quality(
            target,
            written,
            causes,
            version=row.get("quality_version") or month_aggregate.SCHEMA_VERSION,
            taxonomy_version=row.get("taxonomy_version") or 0,
            run_id=row.get("run_id") or "",
            status=row.get("quality_status") or stored.get("status", "ok"),
            topics=topics,
        )
        if not ok:
            print(f"              SCHREIBEN FEHLGESCHLAGEN für {target}")
            failures += 1
            continue

        back = month_stats.load_month(target) or {}
        untouched = diff_untouched(row, back)
        if untouched:
            for problem in untouched:
                print(f"              PROBLEM: {problem}")
            failures += 1
            continue
        stored_cross = (back.get("quality") or {}).get("qualitaet_ursache") or {}
        if stored_cross != cross:
            print("              PROBLEM: Kreuztabelle kam anders zurück")
            failures += 1
            continue
        print(
            f"              {target} geschrieben, zurückgelesen, "
            f"sechs Felder unverändert"
        )
        if target != month:
            month_stats.drop_month(target)
            print(f"              Probelauf-Zeile {target} wieder entfernt")

    return failures


if __name__ == "__main__":
    argv = sys.argv[1:]
    dry = None
    args = []
    skip = False
    for index, arg in enumerate(argv):
        if skip:
            skip = False
            continue
        if arg == "--dry-key" and index + 1 < len(argv):
            dry = argv[index + 1]
            skip = True
        elif not arg.startswith("--"):
            args.append(arg)
    sys.exit(main(args or ([dry] if dry else MONTHS), "--check" in sys.argv, dry))
