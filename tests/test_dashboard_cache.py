"""The running month's chat list must not freeze while its aggregate refreshes."""

import dashboard


def test_chatliste_veraltet_trotz_frischer_kennzahlen(monkeypatch):
    clock = [1000.0]
    reads = []
    monkeypatch.setattr(dashboard.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        dashboard, "fetch_month_chats", lambda key: (reads.append(key) or [], 0)
    )
    monkeypatch.setattr(dashboard, "aggregate_month", lambda rows, key: {})
    monkeypatch.setattr(dashboard, "analyse_chats", lambda rows: [len(reads)])

    cache = dashboard.new_month_cache()
    cache["aggregates"] = {cache["current_month"]: {}}
    key = cache["current_month"]

    assert dashboard.cached_chats(cache, key) == [1]
    clock[0] += dashboard.EXPIRY_SECONDS + 1
    # The dashboard's /data call refreshes the aggregate right before the chats.
    dashboard.refresh_current_month(cache)
    assert dashboard.cached_chats(cache, key) == [3]


def test_abgeschlossener_monat_liest_chats_neu(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_month_chats", lambda key: ([], 0))
    monkeypatch.setattr(dashboard, "aggregate_month", lambda rows, key: {})
    cache = dashboard.new_month_cache()
    cache["current_month"] = "1999-12"
    cache["aggregates"] = {"1999-12": {}}
    cache["chats"]["1999-12"] = ["ohne die letzten Chats des Monats"]

    dashboard.rollover(cache)

    assert "1999-12" not in cache["chats"]
