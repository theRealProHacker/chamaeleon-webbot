# Backlog

## Travel index / termine
- [ ] Reuse the TourOne `berater` data (name/telefon/email already captured in the
      travel index) to populate the bot's kundenberater answers instead of the
      static values.
- [ ] Confirm with the TourOne/chamdev owner whether a per-travel website-path key
      exists (bookingURL carries `REICODE=...`); it would replace URL derivation +
      overrides entirely.

## Sitemap sync
- [ ] Move the sitemap sync out of memory into Supabase and make it human-editable.
      Current state: daily 2am APScheduler job merges the live sitemap into an
      **in-memory** set only (no persistence, diff to logs).
      Future:
      - Persist the merged sitemap + each day's diff to Supabase.
      - Auth-protected dashboard view to curate / edit URLs by hand.
