# Backlog

## Kunden-Modus (accepted MVP risks, 2026-07-13)
- **Data access surface documented** in `docs/kundendaten-datenzugriff.md`:
  what the API exposes, what we use, and exactly what goes out to Gemini
  (verified 2026-07-18; extended 2026-07-27 — the 6 whitelisted flight fields +
  trip title/dates + the customer's own Zahlstand (Gesamtpreis, offener Betrag,
  Zahlungstermine); the Kundennummer and scraped page content stay excluded).
  Regenerate the field list with `docs/explore_kunde.py`. **Fetching the full
  record is accepted** — it stays server-side; the boundary that matters is the
  model request, so review changes to `kundendaten.py` against that.
- [ ] **IDOR — verify `kunden_id` server-side. Still open in production.**
      Live, the widget asserts `kunden_id` and the server trusts it, so anyone
      with a valid Kundennummer can read that customer's whole booking history +
      Zahlstand through the chat endpoint. v2 (server derives the Kundennummer
      from a `ss.php`-verified MeinChamäleon session and binds it to
      `session_id`) is **implemented and unit-tested in the working tree,
      2026-07-28/29, and NOT pushed.** It closes nothing until it ships.
      → **`docs/kunden-auth-spec.md` is the authoritative status** — remaining
      work, owners, widget contract, go-live order and the fallback all live
      there. Do not track the state here as well; that is how the two drifted
      apart last time. `docs/kunden-auth-plan.md` is the rationale/history.
      Two things worth repeating outside the spec:
      **(a)** v1 was pushed and force-reverted from live on 2026-07-28 (it
      assumed same-site cookies, so it was inert) and a real customer sample
      incl. a password hash leaked into that commit — treat that hash as
      compromised and never let real `ss.php` output back into the repo.
      **(b)** Until v2 ships, the structural defenses are what is holding:
      closure tool with no customer parameter, GET-only, field whitelist,
      ID allowlist, 100/h rate limit.
      Separate owner reports (site-side, independent of the chatbot): `ss.php`
      over-exposes the session (hash/salt/PII to any cookie-bearer);
      `session.use_strict_mode` is off (session fixation).
- [ ] **`is_kunde` logging shares the `is_agentur` schema question** (below):
      kunden conversations are not logged to Supabase at all. The stdout
      `[tool_call] … is_kunde=True` line is now **DEBUG-only** (gated
      2026-07-18 — it was clogging prod logs), so **in prod there is currently
      no visibility into Kunden-Modus whatsoever.** If the message-log schema
      tolerates extra fields, log both flags — never the raw ID (DSGVO: linking
      transcripts to an identified person is a deliberate decision).

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
