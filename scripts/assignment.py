"""The Assignment (A11 step 1): classify July and August 2026 and show the result.

Runs BEFORE any UI work, on purpose. If the classification is not usable, v1 has
no content, and finding that out after building three screens is the expensive
order to find it out in.

What it produces, all under data/:
  - assignment-<month>.json — the run's verdicts and the aggregate
  - assignment-taxonomy.json — the cause taxonomy both months were run against
  - assignment-report.md — the raw cause list, the country ranking, and the
    30 country assignments to check by hand
  - assignment-variance.json — July classified a second time, so the country
    axis's run-to-run variance is a measured number rather than a worry

Two months, not one: a single month cannot test whether causes carry over,
which is the whole point of the stable-ID taxonomy (A4).

    python scripts/assignment.py [month …]
"""

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_quality
import dashboard
import month_aggregate
import quality_job

MONTHS = ["2026-07", "2026-08"]
OUT_DIR = "data"
TAXONOMY_SAMPLE = 150
HAND_CHECK_SAMPLE = 30


def write(name: str, payload) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(payload, str):
            f.write(payload)
        else:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main(months: list[str]) -> None:
    rows_by_month = {}
    for month in months:
        rows, count = dashboard.fetch_month_chats(month)
        print(f"[assignment] {month}: {len(rows)} rows (server count {count})")
        rows_by_month[month] = rows

    # REUSE a taxonomy that already exists, never re-derive it. A second
    # derivation produces different labels under the same ids, and because the
    # checkpoint file is keyed by (month, taxonomy_version), a re-run would then
    # resume verdicts made against the OLD taxonomy and continue with the new
    # one — one month measured with two different instruments. Delete the file
    # deliberately to start over; do not let a re-run do it by accident.
    taxonomy_path = os.path.join(OUT_DIR, "assignment-taxonomy.json")
    if os.path.exists(taxonomy_path):
        with open(taxonomy_path, encoding="utf-8") as f:
            taxonomy = json.load(f)["taxonomy"]
        print(f"[assignment] reusing stored taxonomy ({len(taxonomy)} causes)")
    else:
        # Stratified over segments AND over both months — a July-only seed does
        # not know the Agentur segment, which only went live at the end of July.
        seed_rows = []
        per_month = TAXONOMY_SAMPLE // len(months)
        for month in months:
            seed_rows += quality_job._stratified_sample(rows_by_month[month], per_month)
        seed = [c for row in seed_rows if (c := chat_quality.prepare_chat(row))]
        print(f"[assignment] deriving taxonomy from {len(seed)} chats")
        taxonomy, taxonomy_hash = chat_quality.build_taxonomy(seed)
        write("assignment-taxonomy.json", {"taxonomy": taxonomy, "prompt_hash": taxonomy_hash})
    for cause in taxonomy:
        print(f"  {cause['ursache_id']}  {cause['label']}")

    results = {}
    aggregates = {}
    for month in months:
        result = chat_quality.classify_month(
            rows_by_month[month], taxonomy, month, taxonomy_version=1
        )
        agg = month_aggregate.aggregate_quality(
            rows_by_month[month],
            result["verdicts"],
            month,
            run_id=result["run_id"],
            model=result["model"],
            prompt_version=result["prompt_version"],
            taxonomy_version=1,
            status=result["status"],
            unmapped=len(result["unmapped"]),
        )
        results[month] = result
        aggregates[month] = agg
        write(f"assignment-{month}.json", {"result": result, "aggregate": agg})

    # Variance: the same month a second time, under a different run id so it
    # does not resume the first run's checkpoint. Costs well under a dollar and
    # is the only number that says how many country ranks may be shown at all.
    # Skipped on a re-run of a single month — the number is already measured and
    # a second measurement of it is just another bill.
    first = months[0]
    variance_path = os.path.join(OUT_DIR, "assignment-variance.json")
    if os.path.exists(variance_path):
        with open(variance_path, encoding="utf-8") as f:
            variance = json.load(f)
        print("[assignment] reusing stored variance measurement")
    else:
        variance_result = chat_quality.classify_month(
            rows_by_month[first], taxonomy, first, taxonomy_version=99, resume=False
        )
        variance = compare_runs(results[first], variance_result)
        write("assignment-variance.json", variance)

    # The report always covers both months: a re-run of one of them reads the
    # other back from its stored file rather than leaving it out.
    for month in MONTHS:
        if month in results:
            continue
        stored_path = os.path.join(OUT_DIR, f"assignment-{month}.json")
        if not os.path.exists(stored_path):
            continue
        with open(stored_path, encoding="utf-8") as f:
            stored = json.load(f)
        results[month] = stored["result"]
        aggregates[month] = stored["aggregate"]
        rows_by_month.setdefault(month, [None] * sum(stored["aggregate"]["classified_chats"]))

    report_months = [m for m in MONTHS if m in results]
    write(
        "assignment-report.md",
        report(report_months, results, aggregates, taxonomy, variance, rows_by_month),
    )
    print(f"[assignment] wrote {OUT_DIR}/assignment-report.md")


def compare_runs(a: dict, b: dict) -> dict:
    """How far two runs over the same month disagree, per axis."""
    by_id_a = {v["chat_db_id"]: v for v in a["verdicts"]}
    by_id_b = {v["chat_db_id"]: v for v in b["verdicts"]}
    shared = sorted(set(by_id_a) & set(by_id_b))
    quality_same = sum(1 for i in shared if by_id_a[i]["qualitaet"] == by_id_b[i]["qualitaet"])
    cause_same = sum(1 for i in shared if by_id_a[i]["ursache_id"] == by_id_b[i]["ursache_id"])
    country_same = sum(1 for i in shared if set(by_id_a[i]["laender"]) == set(by_id_b[i]["laender"]))
    return {
        "compared_chats": len(shared),
        "quality_agreement": round(quality_same / len(shared), 4) if shared else None,
        "cause_agreement": round(cause_same / len(shared), 4) if shared else None,
        "country_agreement": round(country_same / len(shared), 4) if shared else None,
        "disagreeing_countries": [
            {
                "chat_db_id": i,
                "run_a": sorted(by_id_a[i]["laender"]),
                "run_b": sorted(by_id_b[i]["laender"]),
            }
            for i in shared
            if set(by_id_a[i]["laender"]) != set(by_id_b[i]["laender"])
        ][:20],
    }


def report(months, results, aggregates, taxonomy, variance, rows_by_month) -> str:
    labels = {c["ursache_id"]: c["label"] for c in taxonomy}
    out = ["# Assignment — Klassifikation Juli und August 2026", ""]
    out.append("Rohergebnis des ersten bezahlten Laufs. Kein UI, keine Interpretation:")
    out.append("die Liste ist da, um zu entscheiden, ob die Achse trägt.")
    out.append("")

    for month in months:
        result = results[month]
        agg = aggregates[month]
        total = len(rows_by_month[month])
        out += [
            f"## {month}",
            "",
            f"- Chats im Monat: {total}",
            f"- klassifiziert: {len(result['verdicts'])} (unmapped: {len(result['unmapped'])})",
            f"- Laufzeit: {result['finished_at'] - result['started_at']:.0f}s · status: {result['status']}",
            "",
            "### Qualität",
            "",
            "| Klasse | gesamt | allgemein | meinchamaeleon | agentur |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for quality, vector in sorted(agg["qualitaet"].items(), key=lambda kv: -sum(kv[1])):
            out.append(f"| {quality} | {sum(vector)} | {vector[0]} | {vector[1]} | {vector[2]} |")

        out += ["", "### Ursachen (roh, ungefiltert)", "",
                "| ID | Label | gesamt | allgemein | meinchamaeleon | agentur |",
                "| --- | --- | ---: | ---: | ---: | ---: |"]
        for cause_id, vector in sorted(agg["ursachen"].items(), key=lambda kv: -sum(kv[1])):
            out.append(
                f"| {cause_id} | {labels.get(cause_id, '(neu)')} | {sum(vector)} "
                f"| {vector[0]} | {vector[1]} | {vector[2]} |"
            )

        out += ["", "### Länder (Rangfolge, keine exakten Zahlen)", ""]
        for entry in month_aggregate.top_countries(agg, limit=15):
            out.append(f"- {entry['land']}: {entry['count']}")
        out.append("")

    # Continuity: what the second month does with the first month's causes.
    if len(months) == 2:
        a, b = (aggregates[m]["ursachen"] for m in months)
        out += ["## Kontinuität der Ursachen", "",
                "| ID | Label | " + " | ".join(months) + " |",
                "| --- | --- | ---: | ---: |"]
        for cause_id in sorted(set(a) | set(b)):
            out.append(
                f"| {cause_id} | {labels.get(cause_id, '(neu)')} "
                f"| {sum(a.get(cause_id, [0,0,0]))} | {sum(b.get(cause_id, [0,0,0]))} |"
            )
        out.append("")

    out += ["## Varianz der Länderachse (derselbe Monat zweimal gerechnet)", "",
            f"- verglichene Chats: {variance['compared_chats']}",
            f"- Übereinstimmung Qualität: {variance['quality_agreement']}",
            f"- Übereinstimmung Ursache: {variance['cause_agreement']}",
            f"- Übereinstimmung Länder: {variance['country_agreement']}",
            "",
            "Daraus folgt, wie viele Ränge die Länderliste zeigen darf: im langen",
            "Schwanz kippen die Plätze an einer Handvoll Chats.",
            ""]

    # 30 country assignments to check by hand — the design asks for exactly this.
    first = months[0]
    verdicts = sorted(results[first]["verdicts"], key=lambda v: v["chat_db_id"])
    step = max(1, len(verdicts) // HAND_CHECK_SAMPLE)
    out += [f"## {HAND_CHECK_SAMPLE} Länderzuordnungen zum Nachprüfen ({first})", "",
            "| chat_db_id | URL-Prior | LLM | übernommen | Fallback |",
            "| --- | --- | --- | --- | --- |"]
    for verdict in verdicts[::step][:HAND_CHECK_SAMPLE]:
        out.append(
            f"| {verdict['chat_db_id'][:8]} | {', '.join(verdict['url_prior']) or '—'} "
            f"| {', '.join(verdict['llm_laender']) or '—'} "
            f"| {', '.join(verdict['laender']) or 'ohne Land'} "
            f"| {'ja' if verdict['fallback_used'] else 'nein'} |"
        )
    out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    main(sys.argv[1:] or MONTHS)
