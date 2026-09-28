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
- **IDOR geschlossen, Kunden-Modus live** (geprüft 2026-09-26): der Server
  leitet die Kundennummer seit 2026-07-29 aus einer `ss.php`-verifizierten
  MeinChamäleon-Session ab; die Widget-Hälfte (`cham-chatbot` `a59935c`, PR #22)
  ist über PR #21 seit 2026-08-19 auf `main` (`authenticateSession()` ruft
  `/kunde/auth` bzw. `/agentur/auth`). Nicht geprüft: ob die Live-Seite dieses
  Widget ausliefert. `docs/kunden-auth-spec.md` behauptet noch „widget on
  `main` does not call it“ — veraltet.
  Zwei Dinge bleiben wichtig:
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
- [ ] **Verifizierte Bindung getrennt vom URL-Pfad loggen** (Kunden und
      Agentur). Seit `2f6cdaa` (2026-09-02) trägt jede Assistant-Nachricht ein
      `segment` (`chat_segments.segment_from_context`): verifizierte Bindung →
      `meinchamaeleon`/`agentur`, sonst entscheidet der Pfad. Beides fällt im
      selben Feld zusammen, also ist nicht erkennbar, ob ein Chat auf
      `/MeinChamaeleon…` wirklich verifiziert war. Die stdout-Zeile
      `[tool_call] … is_kunde=True` ist DEBUG-only. Nie die rohe ID loggen
      (DSGVO: Transkript an identifizierte Person binden ist eine bewusste
      Entscheidung).

## Agentur-Modus
- [ ] **Datenschutzfrage §9.4 klären — das Widget ist schon live.** M1–M4
      (Server) und M5 (Widget, PR #24, über PR #21 seit 2026-08-19 auf `main`)
      sind umgesetzt. Offen ist `docs/agentur-modus-plan.md` §9 Punkt 4: teilen
      sich unabhängige mobile Reiseberater*innen eine Agenturnummer, sieht jede
      die Buchungen der anderen — kein Code kann das beheben. Der Plan wollte
      das vor dem Go-live geklärt haben. Sein Status („PLAN ONLY — nothing
      implemented“, §10 nicht abgehakt) ist veraltet.

## Chatbot / Agenturbereich (deferred from 2026-07-06 ship review)
- [ ] **Test isolation:** `import app` in tests triggers live Supabase work at
      import time: `db_logging.py` asserts credentials and creates the client,
      `_load_sessions_from_db()` loads the last 7 days of chats,
      `active_session_count()` runs and the log worker thread starts. So tests
      need prod credentials. (`dashboard.load_all` is on demand since the
      rebuild.) Same root as T23 below; gate the import-time work like the
      schedulers ($PORT / WERKZEUG_RUN_MAIN) or stub supabase in a fixture.
- [ ] **No local way to exercise the agentur path through the widget:** the
      dev proxy only fronts www. A raw request with
      `Origin: https://agt.chamaeleon-reisen.de` against localhost does trigger
      the agentur prompt variant; there is no flag. Side finding: `endpoint` is
      normalised to the bare path (app.py) before `is_agentur_request` checks
      it, so its host candidate can practically never match.
- [ ] **Content note for the KB owner (faqs/agentur.md):** KB §1.2 wants
      login answers available OUTSIDE the agt area too — move section 2 into
      the general FAQs?
- [ ] **Empty-Reply-Retry reicht unter Last nicht (gemessen 2026-09-18).**
      Bei ~30 Gemini-Aufrufen am Stück (voller `RUN_AGENTUR_EVAL=1`-Lauf)
      kippt das Modell reproduzierbar in die leere Antwort
      (`finish_reason='STOP'`, `tool_calls=0`, `output_tokens=0`). Die
      Retry-Kette versucht dreimal und gibt dann „Entschuldige, da ist mir
      gerade keine Antwort gelungen." aus — das sieht der Nutzer. Es trifft
      wechselnde Fälle, am häufigsten den teuersten (zwei Züge plus
      Tool-Aufruf): in der Suite 0 von 3, einzeln 6 von 6. Deckt sich mit dem
      bekannten Gemini-Verhalten (Learning
      `gemini-strukturierte-ausgabe-kommt-still-kurz`). Offen ist, ob im
      Livebetrieb dieselbe Dichte je auftritt — wenn ja, ist es ein
      Produktfehler, kein Testartefakt. Vor einer Änderung an der Retry-Logik
      erst messen, wie oft die Entschuldigungszeile in Supabase steht.
      Zu beachten: Retries laufen nur innerhalb von `_RETRY_ZEITBUDGET_S`
      (6 s), und der Ankündigungs-Anstoß (`ab20d27`) verbraucht einen Versuch
      aus demselben Zähler `_MAX_VERSUCHE`.

## Travel index / termine
- [ ] Still worth asking the TourOne/chamdev owner: is there a per-travel
      website-path key in the API itself (bookingURL carries `REICODE=...`)?
      Would replace the page-fetch refinement with pure API data.

## Dashboard-Neubau (vertagt aus dem /autoplan-Review, 2026-09-01)
Die Kürzel (T23, T24, A4, C1, C2, P3a/b) stammen aus dem gelöschten Design:
`git show 58737df^:docs/designs/dashboard-segmente-report-retention.md`.

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
      unbewiesen ist. `month_stats` hat 2026-07 und 2026-08 (beide per Backfill
      am 2026-09-06); der erste automatische Monatslauf (`quality_job`) rechnet
      September am 2026-10-01. Sinnvoll frühestens danach.
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
      Stand 2026-09-26 unverändert (nur 2026-07 und 2026-08 haben eine Zeile).
      Juli wird am 2026-09-29 „alt“; er hat einen Report, aber keinen
      Kurzreport (`summary` NULL) — nachgeholt wird der nur für den Vormonat.
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
- [ ] **`:focus`-Regeln fehlen komplett** in `static/dashboard/index.html`
      (nachgezählt 2026-09-26: 0 Treffer für `:focus` und `outline` in 3.761
      Zeilen), und die Monatsauswahl ist ausschließlich ein Klick auf ein
      `<canvas>` (`#monthlyChart`, `aria-hidden`) — es gibt keinen
      Tastaturpfad zur Monatsauswahl und damit zu keiner Monatsauswertung.
      Die Heatmap-Zellen werden erst nach der Monatswahl zu Buttons, die
      Ursachen-Zeilen sind reine `tr`-Klicks.
- [ ] **Kontrast der Diagrammfarben unter 3:1** (WCAG SC 1.4.11). Das
      Monatsdiagramm nutzt grau `#94a3b8` + Akzent `#2563eb` (auch die
      Schraffur), Wochentag/Tag/Stunde sind blau `#60a5fa` mit Auswahl
      `#1d4ed8` und Vormonat `#d4d4d4`. Gegen den weißen Kartengrund:
      `#94a3b8` 2.56:1, `#60a5fa` 2.54:1, `#d4d4d4` 1.42:1.
- [ ] **Monatsdiagramm: der schraffierte letzte Balken (laufender Monat) hat
      keine Legende;** nur der Tooltip sagt „(läuft noch)“. Er liest sich als
      Darstellungsfehler.
- [ ] **„Δ Vormonat“ im Dashboard: Minus ist ein ASCII-Bindestrich**
      (`deltaZelle`, ``${delta > 0 ? "+" : ""}${delta}``), schmaler als das
      Plus, die negativen Zeilen wirken versetzt. `report.html` nutzt seit
      `0a0eefd` das echte „−“; das Dashboard noch nicht.
- [ ] **Report: ein ungültiger Monat (`/dashboard/report/2026-13`) liefert
      rohes JSON** (`dashboard.py`, `jsonify({"error": …}), 400`) statt einer
      Seite — Admin-URL, tippt niemand von Hand.

## Dashboard-Kundenfeedback September 2026 (übernommen aus dem Plan, 2026-09-06)
Die Kürzel (T-A, T-B, D21, A0–A4) stammen aus dem gelöschten Plan:
`git show 58737df^:docs/designs/dashboard-kundenfeedback-2026-09.md`.

- [ ] **T-A — Gesamtzustand aus `month_stats.counts` statt aus dem Live-Cache.**
      Hängt an Löschstufe 2: solange die Rohzeilen da sind, rechnet der Cache
      den Gesamtzustand ohnehin. `counts` wird nur geschrieben, nie gelesen.
      (Einen Knopf „Gesamt“ gibt es seit `428695c` nicht mehr; der
      Gesamtzustand ist der Ruhezustand ohne gewählten Monat.)
- [ ] **T-B — Auftrag-3-Seite: Übergabequote und Entlastung über Monate.**
      Ein Feld `uebergaben` gibt es nicht; die Zahl wird beim Lesen aus
      `qualitaet_ursache` abgeleitet (`month_aggregate.py`) und seit
      `428695c` nirgends mehr angezeigt. Zwei Monate mit Daten liegen vor
      (07, 08, beide Backfill); die Seite braucht einen echten Monatslauf
      (frühestens 2026-10-01). Bis dahin zeigt der Gesamtzustand keine
      Qualität (D21).
- [ ] **T-C — Eval-Suite für den Kurzreport-Prompt.** Prompt v1 kam am
      2026-09-10 und ist gebaut (`month_summary.py`, Karte unten im Report).
      Die Eval-Suite steht noch aus.
- [ ] **Dashboard bei 375px: die Seite scrollt seitlich.** Gemessen
      2026-09-06: Kopfzeile (`.controls` mit `#lastUpdated` ragt 57px hinaus)
      und `#causeTable` (412px breit). Beide ohne Mobil-Regel, also
      vermutlich weiter so. Neu dazu (gerechnet 2026-09-26, nicht gerendert):
      seit `0748ba2` gilt `.left-panel { min-width: 22rem }` auch einspaltig,
      mit dem Padding von `main` 384px bei 375px Fensterbreite. Die alten
      Belege (`.gstack/qa-reports/screenshots/`, nur lokal) zeigen einen
      älteren Stand.

## Tool-Historie (vertagt aus dem /autoplan-Review, 2026-09-08)
Plan: `docs/tool-history-plan.md`.

- [ ] **Serverseitige Tool-Historie, dann voller Server-Umzug der
      Chat-History.** Stand 2026-09-26: weder der Tool-Store (Zwischenschritt
      laut Plan, ENTWURF) noch der Umzug sind gebaut; `app.py` nimmt weiter
      `messages` aus dem Body. Ziel: Server besitzt den ganzen Verlauf
      (LangGraph-Checkpointer mit `thread_id=session_id` — im Plan §2 steht,
      warum er für den Tool-only-Zwischenschritt verliert); Widget schickt nur
      noch die neue Nachricht. Löst turn_index-Anker, DOM-Scraping-Verluste
      (HTML→textContent) und die Payload-Frage auf einmal. Voraussetzung:
      cham-chatbot-Release-Koordination.
- [ ] **Widget: session_id-Erzeugung auf `crypto.getRandomValues` umstellen**
      (cham-chatbot, anderes Repo). Auf `origin/main` (`chatbot.html`) und
      allen Branches weiter `'session_' + Date.now() + '_' +
      Math.random()…` — nicht kryptografisch, und die session_id ist seit der
      Kunden-Bindung der Bearer-Token (12 h TTL); mit einem Tool-Store würde
      sie ihn zusätzlich schalten. Der billigste Härtungsschritt der Kette.

## Kundenfeedback September 2026 (Welle 3, 2026-09-24)
Die Kürzel (F1–F11, W5, D13) stammen aus dem gelöschten Plan:
`git show 58737df^:docs/kundenfeedback-2026-09-plan.md`.

- [ ] **Passolution-API als Stufe 2 (Eng-Review 2026-09-27, D8).** Das Design
      `docs/designs/unterlagen-tripurl-leon2.md` verlinkt die TripURL nur. Die
      Passolution Dataservice API (`api.passolution.eu/api/v2`,
      `/content/all/text?lang=de&countries=..&nat=..`, Fließtext, kein
      Visum-JSON) braucht einen Bearer-Token, den Passolution auf Anfrage
      ausstellt (kein Self-Service). Owner-Entscheidung 2026-09-27: vorerst
      nur verlinken, kein Token anfragen, keine Mail an Johannes. Wieder
      aufnehmen erst, wenn das Verlinken nicht reicht. Start: Token in `.env`, `passolution_tool(land, nat)` neben `visa_tool`,
      Nationalität DE angenommen. Alternative im Zwei-Wochen-Fenster: TripURL
      headless rendern (geprüft 2026-09-27, Vue-App, Schnellübersicht je Land).
- [ ] **Passdaten-Eintragen und Doppel-Buchungsnummer (Eng-Review 2026-09-27,
      D10).** (a) Rund zehn MeinChamäleon-Chats seit Juli: Gäste finden das
      Feld für ihre Passdaten nicht, der Bot antwortet inkonsistent
      (`/MeinChamaeleon/Daten`, „per Mail“, „geht hier nicht“). Bei Katharina
      klären, wo die Eingabe liegt (Gäste-Seite?), dann feste Antwort in
      `faqs/allgemein.md` und im Kunden-Block. (b) Die zwei Nummern auf der
      Reiseseite (Chamäleon-Vorgang gegen Airline-PNR) verwirren wiederholt,
      besonders bei Sitzplatzreservierung: Hinweis an Chamäleon zur
      Beschriftung, plus Prompt-Satz „Buchungsnummer = Chamäleon-Vorgang,
      PNR = Airline“. Quelle: Supabase-Auswertung 2026-09-26.
- [ ] **`agenturdaten.vorgangsnummern` ist ungecacht:** jeder Aufruf holt
      TourOne neu. `kundendaten` hat seit W5 einen `ttl_cache` auf der rohen
      Buchungsliste (10 min, `maxsize=512`, Ausfall nie gecacht); die
      Agentur-Seite analog umstellen.
- [ ] **Vergleichstabelle je Land (Review D13) — teilweise erledigt.**
      Seit `0cd259c`/`93b353f` zeigt das Website-Tool auf Länderseiten oben die
      Reiseliste (Name · Tage · Länder · Pfad, Kombireisen markiert), und der
      Parameter `orte` prüft Orte (auch OHNE) wörtlich am Wortanfang im
      Reiseverlauf; der `filter`-Block ist damit 7/8 grün. Offen: keine
      Vorberechnung beim Sitemap-Sync (die Liste entsteht je Anfrage), keine
      Airline-Spalte (weiter über `abschnitt="leistungen"`), und Synonyme
      hängen daran, dass das Modell Varianten mit `|` liefert.
- [ ] **Eval-Flakes nach dem Unterlagen-Ship (2026-09-29, /ship).** Einzeln
      wiederholt, nicht dauerhaft rot: `test_unterlagen_eval`
      `test_einreise_mit_stand_datum` (Modell verlinkt das Einreise-PDF, statt
      es zu lesen und das Stand-Datum zu nennen) und
      `test_ausfuellhilfe_vor_visum_de` (4/5; verlinkt den Bereich `#unterlagen`
      statt der Ausfüllhilfe direkt); `test_meinchamaeleon_faq` `rail-and-fly`
      (Antwort ohne Link). `test_kundenfeedback_eval` `flug-umstieg-schwester`
      ist auf main genauso wacklig (verschränkt gemessen: Branch 2/6, main 1/6
      — Website-Besucher bekommt „findest du in deinen Reiseunterlagen“).
      Vorsicht beim Vergleichen: main und Branch nur verschränkt zur selben
      Zeit messen, Gemini driftet über den Tag.
- [ ] **Unterlagen: Lücken aus echten PDFs (2026-09-28).** (a) „Ausfüllhilfe
      Namibia.pdf“ u. ä. erkennt `dokument_art` nicht (nur „Visum
      Ausfüllhilfe…“): verlinkt ja, aber kein `dokument:`-Slug. (b) In den
      echten Reiseunterlagen (drei Live-Buchungen, 2026-09-28) findet `gliedern` keine
      Abschnitte `leistungen`/`hinweise` — schon vor dem Review so; prüfen,
      wie die Überschriften dort heißen.
