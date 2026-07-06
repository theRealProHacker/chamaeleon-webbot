"""Supabase persistence for the merged sitemap.

One row per VERSION in ``sitemap_versions`` (latest row wins): daily syncs
that changed something and human edits from /admin both append, so the full
history is kept and a revert is just re-saving an old version's text.

Every call fails open — Supabase being down or the table missing must never
affect the bot; the in-memory sitemap (seeded from sitemap.txt, evolved by
sitemap_sync) keeps working and the failure is logged.

The table is created once by hand in the Supabase SQL editor (the API client
cannot run DDL):

    create table sitemap_versions (
      id           bigint generated always as identity primary key,
      created_at   timestamptz not null default now(),
      source       text not null,          -- 'sync' | 'human'
      sitemap_text text not null,
      added        jsonb,                  -- sync diff vs the previous version
      dropped      jsonb,                  -- removed because no longer 200
      kept         jsonb                   -- absent from live sitemap but kept (still 200)
    );
    alter table sitemap_versions enable row level security;
    -- service-role key bypasses RLS; no public policies on purpose.
"""

from db_logging import supabase  # single initialised client for the process

TABLE = "sitemap_versions"


def load_latest() -> dict | None:
    """The newest version row ({sitemap_text, source, created_at}) or None."""
    try:
        rows = (
            supabase.table(TABLE)
            .select("sitemap_text, source, created_at")
            .order("id", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception as e:
        print(f"[sitemap-store] load failed (table missing? see module docstring): {e}")
        return None


def save_version(text: str, source: str, summary: dict | None = None) -> bool:
    """Append a version row. ``summary`` is sitemap_sync's diff dict."""
    row: dict = {"sitemap_text": text, "source": source}
    if summary:
        row["added"] = summary.get("added") or []
        row["dropped"] = summary.get("dropped_404") or []
        row["kept"] = summary.get("kept_despite_absent") or []
    try:
        supabase.table(TABLE).insert(row).execute()
        return True
    except Exception as e:
        print(f"[sitemap-store] save failed: {e}")
        return False


def recent_versions(limit: int = 20) -> list[dict]:
    """Metadata of the newest versions (no text) for the admin view."""
    try:
        return (
            supabase.table(TABLE)
            .select("id, created_at, source, added, dropped, kept")
            .order("id", desc=True)
            .limit(limit)
            .execute()
            .data
        ) or []
    except Exception as e:
        print(f"[sitemap-store] history failed: {e}")
        return []
