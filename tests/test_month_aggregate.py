"""Pure unit tests for month_aggregate — no Supabase, no network.

The module is deterministic by design (same rows, same aggregate), so the
tests build rows in memory and assert on the numbers the dashboard reads.
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

from datetime import datetime, timedelta

import month_aggregate as ma


BASE = datetime(2026, 8, 3, 10, 0, 0, tzinfo=ma.tz)


def chat(seconds, user_messages=2, day=3, url="https://chamaeleon-reisen.de/"):
    """One raw row: `user_messages` user turns spread over `seconds`."""
    turns = max(user_messages * 2, 2)
    step = seconds / (turns - 1) if turns > 1 else 0
    messages = []
    for i in range(turns):
        stamp = (BASE + timedelta(seconds=step * i)).timestamp()
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": "x", "timestamp": stamp})
    # `turns` alternates user/assistant starting at user, so user turns are
    # ceil(turns/2) — trim the tail when the caller asked for an exact count.
    while sum(1 for m in messages if ma.is_user(m)) > user_messages:
        for index, m in enumerate(messages):
            if ma.is_user(m):
                messages.pop(index)
                break
    return {
        "timestamp": (BASE + timedelta(days=day - 3)).isoformat(),
        "messages": messages,
        "url": url,
    }


def aggregate(rows):
    return ma.aggregate_month(rows, "2026-08")


# --- the old median must not move (IRON RULE regression) ----------------------


def test_old_median_unchanged_by_the_dialog_histogram():
    """duration_buckets and its median are exactly what they were before E1.

    The dialog histogram is a second field, not a change of denominator inside
    the old one. If this drifts, every stored month silently re-reads.
    """
    rows = [chat(2, user_messages=1) for _ in range(57)]
    rows += [chat(106, user_messages=3) for _ in range(43)]
    agg = aggregate(rows)

    assert sum(ma.vector_total(b) for b in agg["duration_buckets"]) == 100
    assert ma.vector_total(agg["duration_samples"]) == 100
    median = ma.median_duration(agg)
    assert median is not None and median < 5, median


def test_dialog_median_lands_on_the_conversation_length():
    """Chats with one user message do not count; the median is the dialog one."""
    rows = [chat(2, user_messages=1) for _ in range(57)]
    rows += [chat(106, user_messages=3) for _ in range(43)]
    agg = aggregate(rows)

    assert ma.vector_total(agg["duration_samples_dialog"]) == 43
    median = ma.median_duration_dialog(agg)
    assert median is not None
    assert abs(median - 106) <= 5, median


def test_dialog_edges_resolve_to_five_seconds_between_30_and_300():
    """The whole point of DURATION_EDGES_DIALOG: no bucket wider than 10 s there."""
    edges = ma.DURATION_EDGES_DIALOG
    for low, high in zip(edges, edges[1:]):
        if low is None or high is None:
            continue
        if low >= 30 and high <= 300:
            assert high - low <= 10, (low, high)


def test_missing_dialog_field_reads_as_unknown_not_zero():
    """A row stored before E1 has no dialog histogram — say None, never 0."""
    agg = aggregate([chat(106, user_messages=3)])
    del agg["duration_buckets_dialog"]
    assert ma.median_duration_dialog(agg) is None
    del agg["duration_samples_dialog"]
    assert ma.dialog_samples(agg) is None


def test_combine_skips_months_without_the_dialog_histogram():
    """Folding a pre-E1 month in as zeros would bias the all-time median."""
    new = aggregate([chat(106, user_messages=3) for _ in range(10)])
    old = aggregate([chat(200, user_messages=3) for _ in range(10)])
    old["duration_buckets_dialog"] = []
    old["duration_samples_dialog"] = ma.empty_vector()

    combined = ma.combine([new, old])
    assert ma.vector_total(combined["duration_samples_dialog"]) == 10
    median = ma.median_duration_dialog(combined)
    assert median is not None and abs(median - 106) <= 5, median


def test_chats_without_timestamps_stay_out_of_both_denominators():
    """Rows before 2026-05-22 carry no per-message stamps: no duration at all."""
    row = chat(106, user_messages=3)
    for m in row["messages"]:
        del m["timestamp"]
    agg = aggregate([row])

    assert ma.vector_total(agg["duration_samples"]) == 0
    assert ma.vector_total(agg["duration_samples_dialog"]) == 0
    assert ma.median_duration(agg) is None
    assert ma.median_duration_dialog(agg) is None


# --- Kreuztabelle, Hilfe-Klassen, Rundung, Bereichszahlen ---------------------


def verdict(chat_id, quality, cause="", topic="t1"):
    return {
        "chat_db_id": chat_id,
        "qualitaet": quality,
        "ursache_id": cause,
        "thema_id": topic,
        "laender": [],
    }


TAXONOMY = [
    {"ursache_id": "u01", "label": "Verweis auf die Beratung", "verweis": True},
    {"ursache_id": "u04", "label": "Keine Buchungsdaten", "verweis": False},
]


def quality_rows(spec):
    """spec: list of (quality, cause) — one chat each, all in `allgemein`."""
    rows, verdicts = [], []
    for index, (quality, cause) in enumerate(spec):
        chat_id = f"c{index}"
        rows.append({"id": chat_id, "messages": [{"role": "user", "content": "x"}]})
        verdicts.append(verdict(chat_id, quality, cause))
    return rows, verdicts


def fold(spec):
    rows, verdicts = quality_rows(spec)
    return ma.aggregate_quality(rows, verdicts, "2026-08", taxonomy_version=3)


def test_cross_table_is_a_cell_not_two_marginals():
    """u01 sits in ausgewichen AND in abgebrochen; the marginals cannot say how."""
    agg = fold(
        [("ausgewichen", "u01")] * 3
        + [("abgebrochen", "u01")] * 2
        + [("ausgewichen", "u04")] * 4
    )
    cross = agg["qualitaet_ursache"]
    assert ma.vector_total(cross["ausgewichen"]["u01"]) == 3
    assert ma.vector_total(cross["abgebrochen"]["u01"]) == 2
    assert ma.vector_total(cross["ausgewichen"]["u04"]) == 4
    # Randverteilung bleibt, was sie war.
    assert ma.vector_total(agg["ursachen"]["u01"]) == 5
    assert ma.vector_total(agg["qualitaet"]["ausgewichen"]) == 7


def test_hilfe_counts_a_referral_as_helped():
    """Branch A0a: handing over to the consultancy is correct behaviour."""
    agg = fold(
        [("beantwortet", "")] * 10
        + [("ausgewichen", "u01")] * 3
        + [("abgebrochen", "u01")] * 2
        + [("ausgewichen", "u04")] * 4
        + [("falsch", "u04")] * 1
    )
    result = ma.hilfe_for(agg, TAXONOMY)
    counts = {k["id"]: k["count"] for k in result["klassen"]}
    assert result["status"] == "ok"
    assert result["uebergaben"] == 5
    assert counts == {"geholfen": 15, "teilweise": 4, "nicht": 1}
    assert sum(counts.values()) == result["classified"] == 20


def test_shares_always_sum_to_one_hundred():
    """August rounds naively to 99 — largest remainder is not cosmetic."""
    assert ma.largest_remainder([1394, 280, 60], 1734) == [80, 16, 4]
    assert sum(ma.largest_remainder([1394, 280, 60], 1734)) == 100
    assert ma.largest_remainder([1, 1, 1], 3) == [34, 33, 33]
    assert ma.largest_remainder([0, 0, 0], 0) == [0, 0, 0]


def test_referral_is_read_from_the_flag_not_from_the_literal_id():
    """A renamed cause must still be subtracted — SC6."""
    renamed = [{"ursache_id": "x42", "label": "Verweis", "verweis": True}]
    rows, verdicts = quality_rows(
        [("beantwortet", "")] * 5 + [("ausgewichen", "x42")] * 5
    )
    agg = ma.aggregate_quality(rows, verdicts, "2026-08")
    result = ma.hilfe_for(agg, renamed)
    assert result["uebergaben"] == 5
    assert {k["id"]: k["count"] for k in result["klassen"]}["geholfen"] == 10


def test_guards_say_unknown_instead_of_zero():
    agg = fold([("beantwortet", "")] * 5 + [("ausgewichen", "u01")] * 5)

    # (1) Kreuztabelle fehlt — eine Zeile von vor der Nachfaltung.
    without = dict(agg)
    del without["qualitaet_ursache"]
    assert ma.hilfe_for(without, TAXONOMY)["status"] == "missing"

    # (2) Taxonomie trägt kein Verweis-Flag.
    unflagged = [{"ursache_id": "u01", "label": "Verweis", "verweis": False}]
    assert ma.hilfe_for(agg, unflagged)["status"] == "missing"

    # (3) Auswahl leer.
    empty = ma.hilfe_for(agg, TAXONOMY, ["agentur"])
    assert empty["status"] == "empty"
    assert empty["klassen"] == []

    # (4) Eine Klasse würde negativ: die Kreuztabelle nennt mehr u01 in
    #     `ausgewichen`, als die Randverteilung dort überhaupt hat.
    broken = dict(agg)
    broken["qualitaet_ursache"] = {"ausgewichen": {"u01": [99, 0, 0]}}
    assert ma.hilfe_for(broken, TAXONOMY)["status"] == "partial"


def test_segment_counts_start_where_segments_start():
    """Ohne Monatswahl zählen die Bereiche ab dem 22. Mai, nicht ab dem 1."""
    may = ma.empty_aggregate("2026-05")
    may["daily"] = {"1": [400, 0, 0], "22": [10, 5, 1], "31": [20, 10, 2]}
    june = ma.empty_aggregate("2026-06")
    june["total_chats"] = [100, 50, 5]

    vector, boundary_seen = ma.segments_since_cutoff({"2026-05": may, "2026-06": june})
    assert boundary_seen
    assert vector == [130, 65, 8]  # der 1. Mai bleibt draußen


def test_missing_boundary_month_contributes_nothing_and_says_so():
    june = ma.empty_aggregate("2026-06")
    june["total_chats"] = [100, 50, 5]
    vector, boundary_seen = ma.segments_since_cutoff({"2026-06": june})
    assert vector == [100, 50, 5]
    assert boundary_seen is False
