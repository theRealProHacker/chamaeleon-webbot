# Backlog

## Kunden-Modus (accepted MVP risks, 2026-07-13)
- **Data access surface documented** in `docs/kundendaten-datenzugriff.md`:
  what the API exposes, what we use, and exactly what goes out to Gemini
  (verified 2026-07-18 — only the 6 whitelisted flight fields + trip title/
  dates; the Kundennummer and scraped page content are structurally excluded).
  Regenerate the field list with `docs/explore_kunde.py`. **Fetching the full
  record is accepted** — it stays server-side; the boundary that matters is the
  model request, so review changes to `kundendaten.py` against that.
- [ ] **IDOR revisit — verify kunden_id server-side.** The widget-sent
      `kunden_id` is client-asserted; anyone with a valid Kundennummer can read
      that customer's flights through the chat endpoint. Accepted for MVP
      ("IDs are unguessable" — note: 999999999 exists, but that is the
      designated test customer). Real fix: a server-verifiable MeinChamäleon
      session token — ask the TourOne/chamdev owner what exists, then verify it
      in `app.py` before enabling the mode. First thing to revisit post-MVP.
      Mitigations shipped: closure tool without ID parameter, GET-only, field
      whitelist (no PNR), input allowlist, global 100/h rate limit.
- [ ] **`is_kunde` logging shares the `is_agentur` schema question** (below):
      kunden conversations are currently only visible via the stdout
      `[tool_call] … is_kunde=True` lines, not in Supabase. If the message-log
      schema tolerates extra fields, log both flags — never the raw ID (DSGVO:
      linking transcripts to an identified person is a deliberate decision).

## Chatbot / Agenturbereich (deferred from 2026-07-06 ship review)
- [ ] **Log the `is_agentur` flag with chat messages** so agentur conversations
      are distinguishable in the dashboard/Supabase when detection came via
      Origin/Referer (today only the url is logged). Check first whether the
      message-log schema tolerates an extra field.
- [ ] **Test isolation:** `import app` in tests triggers live Supabase reads at
      import time (`month_cache.load_all()` fetches ~11k chat rows,
      `active_session_count()`), so tests are slow and need prod credentials.
      Pre-existing; gate the import-time work like the schedulers ($PORT /
      WERKZEUG_RUN_MAIN) or stub supabase in a fixture.
- [ ] **No local way to exercise the agentur path:** the dev proxy only fronts
      www, so the agentur prompt variant can only be tested on the live agt.
      hosts. Consider a loopback-only override (e.g. explicit `agentur` flag).
- [ ] **Content notes for the KB owner (faqs/agentur.md):** (a) KB §1.2 wants
      login answers available OUTSIDE the agt area too — move section 2 into
      the general FAQs? (b) Option vs. Reservierung: only "Option" states the
      after-7-days auto-conversion to Festbuchung; asked about a "Reservierung"
      the bot may answer it lapses. Confirm intended wording.

## Travel index / termine
- [x] **Drift canary scheduled 2026-07-06:** monthly user-crontab entry on the
      dev machine (1st of month, 10:00 — daytime on purpose) running
      `RUN_LIVE_TERMINE=1 pytest tests/test_termine_live.py`, appending to
      `~/.local/state/chamaeleon-webbot/termine-canary.log`. Check the log after
      the 1st, or run manually after site releases / before big deploys.
      Remove/edit with `crontab -e`.
- [x] **Berater reuse shipped 2026-07-06:** `format_system_prompt` fills
      kundenberater name/telefon from the travel index (`get_berater`, peek-only
      so a chat never blocks on the index build) whenever the embedding page
      does not pass an advisor; page-supplied values always win. The index also
      carries the berater `email` — currently unused because the prompt template
      only has name/telefon slots; add a slot if wanted.
- [x] ~~Authoritative URL→codes mapping~~ **SHIPPED 2026-07-06** as the
      widget-code refinement in `travel_index._build_index`: each trip page's
      server-rendered `data-terminliste` code (the ONE code the site's own
      termine widget queries), expanded like the site does — the code itself
      if aktiv plus aktiv travels whose `masterCode` points at it. 54 URLs
      refined on the first live build; Queen-Charlotte's manual override
      retired; Gjirokaster-NEU trimmed to its season code (was +9 stale rows).
      Canary 11/11. Derivation + travel_overrides.json remain as fallback for
      widget-less pages (subpackage choosers like Limpopo_ALL, stale 404s) and
      fetch-failure days. (The `sku` attribute lists the whole code family —
      wrong key for season pages; `data-terminliste` is the truth.)
- [ ] Still worth asking the TourOne/chamdev owner: is there a per-travel
      website-path key in the API itself (bookingURL carries `REICODE=...`)?
      Would replace the page-fetch refinement with pure API data.
- [x] **"Language API key" clarified 2026-07-06 (owner):** the third key of the
      three-way index means the travel's COUNTRY KEY — the (normally 5-letter)
      base reisecode stem (NPLUM, MAMAR, NASAM, …). Derivable from any code via
      `code.split("_")[0]`; nothing extra to build today.
- [x] **C1–C7 cleanups applied 2026-07-06 (owner picked all):** test.py scratch
      script, dead recommend_* tool machinery, all commented-out corpse blocks
      (charset/injection, process_links_in_reply, ChatOpenAI, OPENAI raise),
      stale Railway TODO + dead dashboard assert.

## Sitemap sync
- [x] **Supabase persistence + curation shipped 2026-07-06.** Changed syncs and
      human edits append versioned text rows to `sitemap_versions` (latest wins,
      full history, revert = re-save an old version); the newest version is
      restored at startup before the travel-index warm build; /admin got a
      sitemap textarea with guard rails (refuses truncated pastes and texts
      without Reiseziele URLs). Everything fails open until the table exists —
      **one manual step left: run the DDL from sitemap_store.py's docstring in
      the Supabase SQL editor** (the API key cannot create tables).
