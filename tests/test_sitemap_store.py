"""Tests for the persisted sitemap: sitemap_store + the sync/admin plumbing.

Supabase is stubbed — no network, no real DB. apply_sitemap mutates
agent_base globals in place, so every test that applies a text restores the
original afterwards (fixture).
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import pytest

import agent_base
import sitemap_store
import sitemap_sync


# --- stub supabase client ------------------------------------------------------


class _StubTable:
    def __init__(self, client):
        self._c = client

    def insert(self, row):
        self._c.inserted.append(row)
        return self

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        class R:
            data = self._c.rows

        return R()


class _StubClient:
    def __init__(self, rows=None, fail=False):
        self.inserted = []
        self.rows = rows or []
        self.fail = fail

    def table(self, name):
        if self.fail:
            raise RuntimeError("db down")
        return _StubTable(self)


@pytest.fixture()
def restore_sitemap():
    original = agent_base.sitemap
    yield
    agent_base.apply_sitemap(original)


# --- sitemap_store -------------------------------------------------------------


def test_store_save_and_load(monkeypatch):
    stub = _StubClient(rows=[{"sitemap_text": "/x", "source": "human", "created_at": "t"}])
    monkeypatch.setattr(sitemap_store, "supabase", stub)
    assert sitemap_store.save_version("text", "sync", {"added": ["/a"], "dropped_404": []})
    assert stub.inserted[0]["source"] == "sync"
    assert stub.inserted[0]["added"] == ["/a"]
    assert sitemap_store.load_latest()["sitemap_text"] == "/x"
    assert sitemap_store.recent_versions() == stub.rows


def test_store_fails_open(monkeypatch):
    monkeypatch.setattr(sitemap_store, "supabase", _StubClient(fail=True))
    assert sitemap_store.save_version("text", "sync") is False
    assert sitemap_store.load_latest() is None
    assert sitemap_store.recent_versions() == []


# --- apply_human_edit ----------------------------------------------------------


def test_human_edit_applies_and_persists(monkeypatch, restore_sitemap):
    saved = {}

    def capture(text, source, summary=None):
        saved.update(text=text, source=source)
        return True

    monkeypatch.setattr(sitemap_store, "save_version", capture)
    new_text = agent_base.sitemap + "\n/Test/Kuratiert-123\n"
    result = sitemap_sync.apply_human_edit(new_text)
    assert result.get("applied") and result.get("persisted")
    assert "/Test/Kuratiert-123" in agent_base.all_sites
    assert saved["source"] == "human"


def test_human_edit_rejects_truncated_paste():
    before = agent_base.sitemap
    assert "error" in sitemap_sync.apply_human_edit("")
    assert "error" in sitemap_sync.apply_human_edit("/a\n/b\n/c")
    assert agent_base.sitemap == before  # nothing applied


def test_human_edit_rejects_text_without_trip_urls():
    # Plenty of URLs, but no ## Reiseziele section -> the travel index and
    # termine would die silently. Refused.
    text = "\n".join(f"/Seite-{i}" for i in range(30))
    assert "error" in sitemap_sync.apply_human_edit(text)


def test_human_edit_rejects_non_path_lines():
    text = agent_base.sitemap + "\nhttps://example.com/absolute\n"
    assert "error" in sitemap_sync.apply_human_edit(text)


# --- restore_from_db -----------------------------------------------------------


def test_restore_applies_newest_version(monkeypatch, restore_sitemap):
    new_text = agent_base.sitemap + "\n/Test/Restored-456\n"
    monkeypatch.setattr(
        sitemap_store, "load_latest",
        lambda: {"sitemap_text": new_text, "source": "human", "created_at": "t"},
    )
    assert sitemap_sync.restore_from_db() is True
    assert "/Test/Restored-456" in agent_base.all_sites


def test_restore_noops_without_row_or_change(monkeypatch):
    monkeypatch.setattr(sitemap_store, "load_latest", lambda: None)
    assert sitemap_sync.restore_from_db() is False
    monkeypatch.setattr(
        sitemap_store, "load_latest",
        lambda: {"sitemap_text": agent_base.sitemap, "source": "sync", "created_at": "t"},
    )
    assert sitemap_sync.restore_from_db() is False


# --- sync persistence hook -----------------------------------------------------


def test_sync_persists_only_changed_versions(monkeypatch, restore_sitemap):
    saved = []
    monkeypatch.setattr(
        sitemap_store, "save_version",
        lambda text, source, summary=None: saved.append((source, summary)) or True,
    )
    static = set(sitemap_sync.static_paths(agent_base.sitemap))

    # no diff -> no persisted version
    monkeypatch.setattr(sitemap_sync, "fetch_live_sitemap", lambda **k: static)
    sitemap_sync.sync(verbose=False)
    assert saved == []

    # one addition -> persisted with source 'sync' and the diff summary
    monkeypatch.setattr(
        sitemap_sync, "fetch_live_sitemap",
        lambda **k: static | {"/Afrika/Testland/Neue-Reise-XYZ"},
    )
    sitemap_sync.sync(verbose=False)
    assert saved and saved[0][0] == "sync"
    assert "/Afrika/Testland/Neue-Reise-XYZ" in saved[0][1]["added"]
