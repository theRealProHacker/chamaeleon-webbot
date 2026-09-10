-- Aggregate cache for the dashboard. ONE ROW PER MONTH, latest write wins.
--
-- Run this by hand in the Supabase SQL editor: the API key cannot execute DDL.
-- Until it exists, the dashboard runs fail-open — deterministic axes computed
-- live, LLM axes reported as `status: missing`, nothing written. (The same
-- manual step for `sitemap_versions` has been open since 2026-07-06, which is
-- why fail-open is a requirement here and not a nicety.)
--
-- What this table is, and is not: it is a CACHE for the paid LLM run, not a
-- retention mechanism. Raw chats stay in `chats`, so every value in here is
-- recomputable from source — a wrong aggregate is a cache bug, never data loss,
-- and the row may be dropped and rebuilt at any time.
--
-- `payload` carries `schema_version`. The truth is versioned JSON, not rendered
-- HTML: a month-over-month comparison has to be computable later, and it is not
-- from markup.

create table month_stats (
  month             text primary key,          -- '2026-07'
  computed_at       timestamptz not null default now(),

  -- Deterministic half: counts, buckets, heatmap. Cheap, recomputed often.
  counts            jsonb,
  counts_computed_at timestamptz,
  counts_version    integer,

  -- Paid half: quality classification, causes, countries. One LLM run.
  -- Kept in its own columns because one `computed_at` cannot date two things
  -- that are refreshed on completely different schedules.
  quality           jsonb,
  quality_computed_at timestamptz,
  quality_version   integer,
  quality_status    text,                      -- 'ok' | 'partial' | 'missing'
  run_id            text,

  -- The cause taxonomy this month was classified against. A month is always
  -- read with the taxonomy it was RUN with; promotions apply to the next month.
  taxonomy          jsonb,
  taxonomy_version  integer
);

alter table month_stats enable row level security;
-- The service-role key bypasses RLS; no public policies on purpose. The payload
-- is anonymous by construction (SC3), but "anonymous" is an argument for
-- keeping it indefinitely, not for exposing it.

-- KI-Kurzreport (A5): ein Gemini-Aufruf je Monat ueber das fertige Aggregat.
-- Eigene Spalten, weil der Text auf einem dritten Zeitplan entsteht. Auch
-- dieser Schritt ist manuell; bis dahin fehlt die Karte im Report, sonst nichts.
alter table month_stats add column if not exists summary jsonb;
alter table month_stats add column if not exists summary_computed_at timestamptz;
