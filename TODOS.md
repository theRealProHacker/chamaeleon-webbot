# Backlog

## Travel index / termine
- [ ] **Run the live termine test periodically (drift canary).** The termine
      display rules are reverse-engineered from live pages (2026-07-05: Marrakesch
      33/33, Limpopo 57/57 rows replicated); a site JS or feed change would drift
      silently. `tests/test_termine_live.py` (env-gated real-browser test over ~10
      reference pages, part of the termine PR) doubles as the canary — schedule it
      (monthly / after site releases) or run before deploys. Depends on: termine
      pipeline shipped. Cons: needs a machine with a browser (not Railway).
- [ ] Reuse the TourOne `berater` data (name/telefon/email already captured in the
      travel index) to populate the bot's kundenberater answers instead of the
      static values.
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

## Sitemap sync
- [ ] Move the sitemap sync out of memory into Supabase and make it human-editable.
      Current state: daily 2am APScheduler job merges the live sitemap into an
      **in-memory** set only (no persistence, diff to logs).
      Future:
      - Persist the merged sitemap + each day's diff to Supabase.
      - Auth-protected dashboard view to curate / edit URLs by hand.
