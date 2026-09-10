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
- [ ] **IDOR — verify `kunden_id` server-side. Server half shipped, widget half
      not.** Until 2026-07-29 the widget asserted `kunden_id` and the server
      trusted it, so anyone with a valid Kundennummer could read that customer's
      whole booking history + Zahlstand through the chat endpoint. v2 (server
      derives the Kundennummer from a `ss.php`-verified MeinChamäleon session and
      binds it to `session_id`) is **deployed since 2026-07-29**: a body
      `kunden_id` is now ignored outright, so the spoofing path is gone.
      Still unchecked because the widget change that supplies a real session is
      **committed but not pushed** (`cham-chatbot` `a59935c`, branch `kunden-id`)
      — so Kunden-Modus currently resolves to `""` for every customer. The box
      gets ticked when that reaches `main`, not before.
      → **`docs/kunden-auth-spec.md` is the authoritative status** — remaining
      work, owners, widget contract, go-live order and the fallback all live
      there. Do not track the state here as well; that is how the two drifted
      apart last time. `docs/kunden-auth-plan.md` is the rationale/history.
      Two things worth repeating outside the spec:
      **(a)** v1 was pushed and force-reverted from live on 2026-07-28 (it
      assumed same-site cookies, so it was inert) and a real customer sample
      incl. a password hash leaked into that commit — treat that hash as
      compromised and never let real `ss.php` output back into the repo.
      **(b)** The structural defenses still stand behind the session check and
      are what keep a bug in it from being fatal: closure tool with no customer
      parameter, GET-only, field whitelist, ID allowlist, 100/h rate limit.
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

## Agentur-Modus (planned 2026-07-30)
- [ ] **Verified Reiseprofi identity + agency booking data.** Pointer only —
      **`docs/agentur-modus-plan.md` is authoritative** for status, the measured
      TourOne agency API contract, the whitelist, the guarantees and the
      milestones. Do not track state here as well; that is how the Kunden docs
      drifted apart.

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

## Dashboard-Neubau (vertagt aus dem /autoplan-Review, 2026-09-01)
Vorlage und Begründungen: `docs/designs/dashboard-segmente-report-retention.md`.

- [x] **v1 gebaut 2026-09-02.** Segmentachse, Heatmap, Qualitäts-/Ursachenachse
      und die Oberfläche stehen; `chat_segments`, `month_aggregate`,
      `chat_quality`, `quality_job`, `month_stats`, `pii_scan` sind neu, die
      Aggregation liegt als reine Funktionen neben dem Requestpfad. Der erste
      bezahlte Lauf über Juli und August ist gerechnet und liegt als Gold-Set in
      `data/` (`assignment-report.md`).
      **Ein manueller Schritt offen: die DDL aus `sql/month_stats.sql` im
      Supabase-SQL-Editor ausführen** — der API-Key kann keine Tabellen anlegen.
      Bis dahin läuft alles fail-open: die deterministischen Achsen werden live
      gerechnet, die Qualitäts- und Länderachse melden `status: missing`, und es
      wird nichts geschrieben. Gleiche Lage wie bei `sitemap_versions` oben.
- [ ] **Validierungs-Gate für die Qualitätsachse (T24) — nicht gebaut.**
      Der Plan verlangte 120 handgelabelte Chats, Cohens κ ≥ 0,6 und eine
      Formkontroll-Regression, bevor die Achse als tragfähig gilt. Auf
      Owner-Ansage („keine Tests") ausgelassen. Was stattdessen gemessen
      vorliegt, steht in `data/assignment-report.md`: derselbe Monat zweimal
      gerechnet stimmt zu 97,8 % (Qualität), 95,9 % (Ursache) und 99,95 %
      (Länder) mit sich selbst überein. **Das ist Reproduzierbarkeit, nicht
      Richtigkeit** — es sagt, dass das Modell sich nicht widerspricht, nicht,
      dass es mit einem Menschen übereinstimmt. Die Achse trägt v1 allein;
      wer ihr Zahlen glauben will, braucht das Gate.
- [ ] **Import-Nebenwirkungen kappen (T23) — nicht gebaut.** Begründung war
      ausschließlich Testisolation, und die ist mit „keine Tests" entfallen.
      `dashboard.py` lädt beim Import nichts mehr (der Cache füllt sich beim
      ersten Request), aber `db_logging.py` legt weiterhin beim Import den
      Supabase-Client an und lädt Sessions. Wer je Tests hinzufügt, fängt hier an.
- [ ] **Trend-Sparkline** pro Ursache über die letzten N Monate. Vertagt,
      weil sie an der Cluster-Kontinuität (A4) hängt, deren Nutzen selbst noch
      unbewiesen ist. Sinnvoll frühestens nach dem zweiten echten Monatslauf.
- [ ] **CSV-Export der Ursachenliste.** Ungefragt, aber plausibel: der Anfragende
      arbeitet vermutlich mit Excel. Erst bauen, wenn er danach fragt.
- [ ] **Aufbewahrungsregel für `month_stats` selbst** (P3b im Design). Die
      Tabelle ist als unbefristet gedacht; das ist nur zulässig, solange der
      Payload anonym ist. Fällt PII hinein, fällt die Begründung.
- [ ] **Stufe 1 der Löschung ist gebaut, aber AUS (2026-09-03).** Owner-Ansage:
      90 Tage Löschfrist. Ein Monat gilt als „alt", sobald sein **erster Tag**
      90 Tage zurückliegt; dann werden seine Rohtranskripte nicht mehr
      ausgeliefert und es bleibt der Report. Der Report wird beim
      Monatsabschluss gerechnet, nicht erst am Cutoff — sonst fallen Lauf und
      Verschwinden der Quelldaten auf denselben Tag, und ein fehlgeschlagener
      Lauf lässt den Monat ohne beides zurück.
      **Der Schalter steht auf `CUTOFF_ENABLED=false`, und das ist die
      vereinbarte Reihenfolge, keine Vorsicht:** heute sind zehn Monate
      (Sep 2025 – Jun 2026) älter als 90 Tage und **keiner** hat einen Report.
      Scharf geschaltet verlören sie sofort ihre Chats, ohne dass an ihrer
      Stelle etwas stünde. Erst die zehn Monate nachrechnen (~4 $, mehrere
      Stunden), prüfen, dann `CUTOFF_ENABLED=true` setzen.
      **Weiterhin offen — und Teil der ursprünglichen Vorbedingung: wer
      verantwortet die Frist.** Anlass und Frist stehen jetzt fest, der
      Verantwortliche nicht. Und Stufe 1 erfüllt die Frist ausdrücklich nicht:
      es wird nichts gelöscht, nur nicht mehr angezeigt.
- [ ] **DSGVO-Retention Stufe 2 (echtes Löschen in der DB)**
      Der zweistufige Cutoff ist kein Teil des Dashboard-Neubaus mehr. Er kommt
      als eigenes, kleines Vorhaben zurück, und zwar **erst wenn drei Dinge
      feststehen: Anlass, Frist, Verantwortlicher.** Das ist die Vorbedingung,
      nicht ein Detail darin — ein Mechanismus, der Daten unsichtbar macht,
      gehört nicht in ein Release, das die Frist nicht kennt, der er dient.
      Stufe 1 (Nicht-Anzeigen im Dashboard) erfüllt die Aufbewahrungsfrist
      ausdrücklich **nicht**; ohne Stufe 2 ist sie Compliance-Theater.
      Gute Nachricht aus C1: `month_stats` wird trotzdem gebaut (als
      Aggregat-Cache), der Cutoff kann also später allein nachgezogen werden.
- [ ] **Fragen-Häufigkeitsliste** (die ursprüngliche Anforderung 4). Am Gate
      2026-09-02 durch die Antwortqualitäts-Achse ersetzt (C2), nicht verworfen:
      derselbe LLM-Lauf könnte beide Felder liefern. Sinnvoll, sobald die
      Fragenliste nach außen soll — an Agenturen oder Berater. Dann ist
      Häufigkeit das Produkt und das Gegenargument („daraus folgt keine Arbeit")
      fällt weg.
- [ ] **Auftragsverarbeitung muss die Massenverarbeitung der Nutzernachrichten
      durch Gemini decken** (P3a im Design). Der Chatbetrieb schickt Nachrichten
      ohnehin an Gemini; ein monatlicher Batch über alle Nachrichten ist eine
      zusätzliche Verarbeitung und braucht dieselbe Grundlage.
- [x] **Toter „🤖 KI-Bericht (Gemini)"-Knopf gelöscht** (2026-09-02). Er hat den
      Monatsreport versprochen, den es damals nicht gab; jetzt gibt es ihn, und
      er steht auf der Seite statt hinter einem deaktivierten Knopf.
- [ ] **`:focus`-Regeln fehlen komplett** in `static/dashboard/index.html`
      (nachgezählt: 0 Treffer in 1.708 Zeilen), und die Monatsauswahl ist
      ausschließlich ein Klick auf ein `<canvas>` — es gibt heute keinen
      Tastaturpfad zu irgendeiner Auswertung.
- [ ] **Diagrammfarben teilweise migriert.** Beide Balkendiagramme sprechen
      jetzt dieselbe Sprache (grau + Akzent für die Auswahl), und Segment- sowie
      Heatmap-Tokens stehen in `:root`. Offen bleibt der Kontrast: `#94a3b8`
      erreicht 2.45:1 gegen `--bg` und verfehlt die 3:1-Schwelle für
      Diagrammelemente (WCAG SC 1.4.11).
- [x] **Kartenschatten auf die Token umgestellt** (2026-09-02): `--shadow-s/m/l`
      stehen in `:root`, Karten und Knöpfe benutzen sie.
- [ ] ~~**Kartenschatten verstoßen gegen die globale Designregel**~~ (einlagige
      `box-shadow`, u. a. `index.html:126`), statt gestapelter `--shadow-s/m/l`.
- [ ] **Report: drei kleine Reste aus dem /qa-Lauf (2026-09-04).** (a) Ein
      Monat ohne Auswertung (`/dashboard/report/2026-09`) zeigt unter dem
      Hinweis eine leere Trennlinie, weil `load()` vor der Fusszeile
      aussteigt. (b) Ein ungültiger Monat (`/2026-13`) liefert rohes JSON
      statt einer Seite — Admin-URL, tippt niemand von Hand. (c) Die Kachel
      „Gesprächsdauer 4 s“ ist rechnerisch richtig (Median, viele
      Ein-Satz-Chats), liest sich aber wie ein Fehler; ob sie als Schlagzahl
      auf den Report gehört, ist eine Produktfrage. **(c) erledigt 2026-09-06
      mit A3:** die Kachel zählt jetzt Gespräche ab zwei Nutzernachrichten und
      nennt den Nenner ("Median · Gespräche mit Rückfrage"), August 1:46 Min
      statt 4 Sek. Beleg:
      `.gstack/qa-reports/qa-report-localhost-2026-09-04.md`.
- [ ] **Dashboard unter 1024px: das Monatsdiagramm liegt HINTER dem rechten
      Panel.** Zwischen der Kennzahlen-Karte und „Wo der Bot danebenliegt"
      schaut ein Streifen des Balkendiagramms hervor (Achse „1.400" bzw. „40"
      und Balken). Auf 1024 und 375px reproduziert, auch auf dem Stand vor der
      Gesprächsdauer-Kachel (4dc4cf8) — also vorbestehend, nicht durch sie
      verursacht. Dazu scrollt die Seite auf dem Telefon seitlich (Bereichs-
      leiste, Ursachentabelle). Belege: `.gstack/qa-reports/screenshots/qa2-old-1024.png`,
      `qa2-mobile-aug.png`.

## Dashboard-Kundenfeedback September 2026 (übernommen aus dem Plan, 2026-09-06)

Plan: `docs/designs/dashboard-kundenfeedback-2026-09.md`.

- [ ] **T-A — „Gesamt" aus `month_stats.counts` statt aus dem Live-Cache.**
      Hängt an Löschstufe 2: solange die Rohzeilen da sind, rechnet der Cache
      den Gesamtzustand ohnehin. `counts` hat bis heute keinen Leser.
- [ ] **T-B — Auftrag-3-Seite: Übergabequote und Entlastung über Monate.**
      Die erste Zahl dafür steht seit 2026-09-06 in der Zeile (`uebergaben`);
      die Seite braucht zusätzlich einen zweiten echten Monatslauf. Bis dahin
      ist „Gesamt" ausdrücklich die Volumenseite und zeigt keine Qualität (D21).
- [ ] **T-C — Eval-Suite für den Kurzreport-Prompt.** Prompt v1 kam am
      2026-09-10 und ist gebaut (`month_summary.py`, Karte unten im Report).
      Die Eval-Suite steht noch aus.
- [ ] **DDL für den KI-Kurzreport von Hand ausführen:** die beiden
      `alter table`-Zeilen am Ende von `sql/month_stats.sql` (`summary`,
      `summary_computed_at`). Bis dahin läuft der Gemini-Aufruf, der Upsert
      schlägt fehl und die Karte zeigt „liegt noch kein Kurzreport vor“.
- [ ] **Dashboard bei 375px: die Seite scrollt seitlich (57px).** Zwei
      Verursacher, beide vorbestehend: die Kopfzeile (`.controls` mit
      `#lastUpdated` ragt 57px hinaus) und `#causeTable` (412px breit in einem
      375px-Fenster). Gemessen 2026-09-06 am Stand nach A0-A4. Gehört zum
      offenen Mobil-Punkt weiter oben, ist aber die konkrete Ursache.
- [ ] **Zwei unabhaengige Bildbefunde vom 2026-09-06, beide vorbestehend.**
      (a) Bei 1280px sind die beiden Diagramme in der linken Spalte zu schmal:
      13 Monatsbalken ohne Zwischenraum, und ausgerechnet der ausgewaehlte
      Monat traegt keine Beschriftung, weil nur jeder dritte Tick gesetzt ist.
      Bei 1024px (einspaltig) sind dieselben Diagramme gut lesbar. (b) Der
      schraffierte letzte Balken (laufender Monat) hat keine Legende; er liest
      sich als Darstellungsfehler. (c) In der Spalte „Δ Vormonat" steht das
      Minus mit Abstand („- 4"), das Plus ohne („+16") — die negativen Zeilen
      wirken versetzt.
