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
- [ ] Confirm with the TourOne/chamdev owner whether a per-travel website-path key
      exists (bookingURL carries `REICODE=...`); it would replace URL derivation +
      overrides entirely.
      **Lead found 2026-07-05:** every trip page embeds its full reisecode list in
      the server-rendered HTML — the Trustpilot widget attribute
      `sku="MAMAR,MAMAR_RAK,MAMAR_FRA,MAMAR_ALL,…,Marrakesch"` — fetchable with
      plain `requests` (no JS needed). Parsing that per sitemap URL would give an
      authoritative URL→codes mapping and could retire derivation + most of
      travel_overrides.json (intersect with active travels to drop retired codes).
      **Nuance found 2026-07-06 (canary):** `sku` lists the whole code FAMILY,
      not what the page's termine widget shows — the widget queries ONE code
      (`data-terminliste='{"reisecode": "NASAM_ALL"}'`) and its backend expands
      it. Gjirokaster-NEU has sku=ALGJI,ALGJI_NEU,ALGJI_ALL but renders only the
      NEU season; our override merges all three, so the bot would show 9 extra
      2026 rows there. For season-suffixed URLs, `data-terminliste` (also plain
      server HTML) is the better key than `sku`.

## Sitemap sync
- [ ] Move the sitemap sync out of memory into Supabase and make it human-editable.
      Current state: daily 2am APScheduler job merges the live sitemap into an
      **in-memory** set only (no persistence, diff to logs).
      Future:
      - Persist the merged sitemap + each day's diff to Supabase.
      - Auth-protected dashboard view to curate / edit URLs by hand.
