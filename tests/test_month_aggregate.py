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
