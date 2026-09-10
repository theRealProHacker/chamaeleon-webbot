"""Pure unit tests for month_summary — no Supabase, no Gemini.

The input builder is the part worth testing: it decides which numbers the
model gets to see, and a wrong "keine Vergleichsdaten" here becomes a
confident sentence about a month the model never saw.
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

from datetime import datetime, timedelta

import month_aggregate as ma
import month_summary as ms


BASE = datetime(2026, 8, 3, 10, 0, 0, tzinfo=ma.tz)


def chat(seconds=90, user_messages=2, day=3):
    turns = max(user_messages * 2, 2)
    step = seconds / (turns - 1)
    messages = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": "x",
            "timestamp": (BASE + timedelta(seconds=step * i)).timestamp(),
        }
        for i in range(turns)
    ]
    return {
        "timestamp": (BASE + timedelta(days=day - 3)).isoformat(),
        "messages": messages,
        "url": "https://chamaeleon-reisen.de/",
    }


TAXONOMY = [
    {"ursache_id": "u01", "label": "Verweis auf Beratung", "definition": "", "verweis": True},
    {"ursache_id": "u02", "label": "Preis unbekannt", "definition": ""},
]
TOPICS = [{"thema_id": "t01", "label": "Reisepreis", "definition": ""}]


def quality(month, classified=10, bad=3, version=1):
    v = lambda n: [n, 0, 0]  # noqa: E731
    return {
        "month": month,
        "taxonomy_version": version,
        "status": "ok",
        "classified_chats": v(classified),
        "qualitaet": {
            "beantwortet": v(classified - bad),
            "ausgewichen": v(bad),
            "falsch": v(0),
            "abgebrochen": v(0),
        },
        "qualitaet_ursache": {"ausgewichen": {"u01": v(1), "u02": v(bad - 1)}},
        "ursachen": {"u01": v(1), "u02": v(bad - 1), "neu": v(0)},
        "themen": {"t01": v(classified), "neu": v(0)},
        "laender": {"Namibia": v(4), ma.NO_COUNTRY: v(6)},
    }


def test_referral_cause_is_not_a_problem_in_the_prompt():
    agg = ma.aggregate_month([chat() for _ in range(10)], "2026-08")
    data = ms.build_input("2026-08", agg, quality("2026-08"), TAXONOMY, TOPICS, None, None)
    labels = [c["label"] for c in data["causes"]]
    assert labels == ["Preis unbekannt"]
    # Uebergaben sind geholfen, nicht danebengelegen.
    assert data["hilfe"]["Geholfen"] == 8
    assert data["uebergaben"] == 1


def test_without_previous_month_the_text_says_so():
    agg = ma.aggregate_month([chat() for _ in range(10)], "2026-08")
    data = ms.build_input("2026-08", agg, quality("2026-08"), TAXONOMY, TOPICS, None, None)
    text = ms.render_input(data)
    assert "keine Vergleichsdaten" in text
    assert "Juli 2026" in text and "August 2026" in text
    assert all(row["before"] is None for row in data["causes"])


def test_previous_month_across_taxonomy_change_is_not_compared():
    agg = ma.aggregate_month([chat() for _ in range(10)], "2026-08")
    # The caller applies the taxonomy guard and hands None; the builder must
    # not invent zeros for it.
    data = ms.build_input("2026-08", agg, quality("2026-08"), TAXONOMY, TOPICS, agg, None)
    assert data["hilfe_before"] is None
    assert data["activity_before"] is not None  # the deterministic half compares fine


def test_previous_month_counts_land_in_the_table():
    agg = ma.aggregate_month([chat() for _ in range(10)], "2026-08")
    before = quality("2026-07", classified=20, bad=6)
    data = ms.build_input("2026-08", agg, quality("2026-08"), TAXONOMY, TOPICS, agg, before)
    assert data["causes"][0]["before"] == 5
    assert data["countries"][0] == {"label": "Namibia", "count": 4, "before": 4}
    text = ms.render_input(data)
    assert "Preis unbekannt: 2 → 5" in text
    assert "ohne Land" not in text
