"""
Untested, AI-generated, probably broken code that tests the db_logging module. Run with pytest and fix as needed.
"""

import importlib
import sys
import time
import types
from types import SimpleNamespace
from datetime import datetime, timezone

import common as _
 


def _response(status_code, data):
    return SimpleNamespace(status_code=status_code, data=data)


class FakeTable:
    def __init__(self, *, select_responses=None, insert_responses=None, update_responses=None):
        self.select_responses = list(select_responses or [])
        self.insert_responses = list(insert_responses or [])
        self.update_responses = list(update_responses or [])
        self.calls = []
        self.operation = None

    def select(self, columns):
        self.calls.append(("select", columns))
        self.operation = "select"
        return self

    def insert(self, row):
        self.calls.append(("insert", row))
        self.operation = "insert"
        return self

    def update(self, payload):
        self.calls.append(("update", payload))
        self.operation = "update"
        return self

    def eq(self, *args):
        self.calls.append(("eq", args))
        return self

    def execute(self):
        self.calls.append(("execute", self.operation))
        if self.operation == "select":
            response = self.select_responses.pop(0)
        elif self.operation == "insert":
            response = self.insert_responses.pop(0)
        elif self.operation == "update":
            response = self.update_responses.pop(0)
        else:
            raise AssertionError("No operation selected before execute")

        if isinstance(response, Exception):
            raise response
        return response


class FakeSupabase:
    def __init__(self, table):
        self._table = table

    def table(self, name):
        assert name == "chats"
        return self._table


def _import_db_logging(monkeypatch, fake_supabase):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    sys.modules.pop("db_logging", None)

    fake_supabase_module = types.ModuleType("supabase")
    fake_supabase_module.Client = object
    fake_supabase_module.create_client = lambda *_args, **_kwargs: fake_supabase
    monkeypatch.setitem(sys.modules, "supabase", fake_supabase_module)

    return importlib.import_module("db_logging")


def test_hydrates_sessions_and_keeps_only_recent_history(monkeypatch):
    now = time.time()
    rows = [
        {
            "id": "db-old",
            "session_id": "old",
            "messages": [
                {"role": "user", "content": "first", "url": "", "timestamp": now - 8 * 24 * 60 * 60},
                {"role": "assistant", "content": "last", "url": "", "timestamp": now - 8 * 24 * 60 * 60 + 60},
            ],
            "timestamp": datetime.fromtimestamp(now - 8 * 24 * 60 * 60, tz=timezone.utc).isoformat(),
        },
        {
            "id": "db-mid",
            "session_id": "mid",
            "messages": [
                {"role": "user", "content": "first", "url": "", "timestamp": now - 2 * 24 * 60 * 60},
                {"role": "assistant", "content": "last", "url": "", "timestamp": now - 2 * 24 * 60 * 60 + 60},
            ],
            "timestamp": datetime.fromtimestamp(now - 2 * 24 * 60 * 60, tz=timezone.utc).isoformat(),
        },
        {
            "id": "db-new",
            "session_id": "new",
            "messages": [
                {"role": "user", "content": "first", "url": "", "timestamp": now - 120},
                {"role": "assistant", "content": "last", "url": "", "timestamp": now - 30},
            ],
            "timestamp": datetime.fromtimestamp(now - 300, tz=timezone.utc).isoformat(),
        },
    ]
    table = FakeTable(select_responses=[_response(200, rows)])
    module = _import_db_logging(monkeypatch, FakeSupabase(table))

    assert list(module._sessions.keys()) == ["mid", "new"]
    assert module._sessions["mid"]["db_id"] == "db-mid"
    # created_at should come from DB timestamptz (table.timestamp)
    assert abs(module._sessions["mid"]["created_at"] - datetime.fromisoformat(rows[1]["timestamp"]).timestamp()) < 1e-6
    assert module._sessions["mid"]["last_active"] == rows[1]["messages"][-1]["timestamp"]
    assert "history" not in module._sessions["mid"]
    assert module._sessions["new"]["history"] == rows[2]["messages"]


def test_hydration_retries_after_exception(monkeypatch):
    now = time.time()
    rows = [
        {
            "id": "db-1",
            "session_id": "s-1",
            "messages": [
                {"role": "user", "content": "first", "url": "", "timestamp": now - 60},
                {"role": "assistant", "content": "last", "url": "", "timestamp": now - 1},
            ],
        }
    ]
    table = FakeTable(select_responses=[Exception("boom"), _response(200, rows)])
    sleep_calls = []

    monkeypatch.setattr(time, "sleep", lambda seconds: sleep_calls.append(seconds))
    module = _import_db_logging(monkeypatch, FakeSupabase(table))

    assert sleep_calls == [1]
    assert list(module._sessions.keys()) == ["s-1"]


def test_log_messages_inserts_new_session(monkeypatch):
    table = FakeTable(
        select_responses=[_response(200, [])],
        insert_responses=[_response(201, [{"id": "db-new"}])],
    )
    module = _import_db_logging(monkeypatch, FakeSupabase(table))

    message = {"role": "user", "content": "hello", "url": "", "timestamp": time.time()}
    module.log_messages("session-new", [message])

    assert module._sessions["session-new"]["db_id"] == "db-new"
    assert module._sessions["session-new"]["history"] == [message]
    assert "created_at" in module._sessions["session-new"]
    assert "last_active" in module._sessions["session-new"]
    assert table.calls[:2] == [("select", "id, session_id, messages, timestamp"), ("execute", "select")]


def test_log_messages_fetches_missing_history_before_update(monkeypatch):
    now = time.time()
    existing_message = {"role": "assistant", "content": "old", "url": "", "timestamp": now - 100}
    new_message = {"role": "user", "content": "new", "url": "", "timestamp": now}
    table = FakeTable(
        select_responses=[
            _response(200, []),
            _response(200, [{"messages": [existing_message]}]),
        ],
        update_responses=[_response(200, [])],
    )
    module = _import_db_logging(monkeypatch, FakeSupabase(table))
    module._sessions = {
        "session-meta": {
            "db_id": "db-1",
            "created_at": now - 2 * 24 * 60 * 60,
            "last_active": now - 2 * 24 * 60 * 60,
        }
    }

    module.log_messages("session-meta", [new_message])

    assert module._sessions["session-meta"]["history"] == [existing_message, new_message]
    assert table.calls.count(("select", "messages")) == 1
    assert table.calls[-2:] == [("eq", ("id", "db-1")), ("execute", "update")]

if __name__ == "__main__":
    print(active_session_count())