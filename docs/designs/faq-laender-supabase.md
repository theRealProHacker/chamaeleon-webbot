# Plan: Länder-FAQs nach Supabase

Status: DRAFT (2026-10-08)
Vorgänger: docs/designs/faq-supabase-sync.md (allgemeine FAQs, dort „Länder-CSVs nicht in V1“).

## Ziel

Die Länder-FAQs (faqs/FAQ_{Afrika,Amerika,Asien_und_Ozeanien,Europa}.csv) ziehen in
dieselbe Tabelle `faq` wie die allgemeinen FAQs, als Frage-Antwort-Zeilen. Danach liest
kein Laufzeitcode mehr die CSVs. Pflege im Supabase Table Editor, wie bei `intern`.

## Heute (gemessen 2026-10-08)

- agent_base.py:138-166 parst die 4 CSVs beim Import zu `laender_faqs`
  (Land → Markdown `# Land\n\n## Frage\n\nAntwort…`) und `laender_faq_data`
  (Land → {Frage: Antwort}). 73 Länder, 333 Paare (inkl. Mehrfach-Ländern), ~78 KB.
- Verbraucher:
  - `country_faq_tool_description` (f-String beim Import, listet alle Länder) und
    `country_faq_tool_base` (agent_base.py:928-949).
  - Länder-Erkennung im Chat (agent.py:663, `from agent_base import laender_faqs`, also
    eine eigene Bindung) und Prompt-Einschub in `format_system_prompt` (agent_base.py:1872).
  - `chat_quality.countries()` (Vokabular, „73 countries“) und `faq_corpus()`.
  - tests/test_faq.py: `test_laender_fragen_gibt_es_noch` + Live-Evals `test_land`.
- Eigenheiten des Parsers, die das Bot-Wissen heute bestimmen:
  - Gruppen-Kopfzeilen „Nordmazedonien/Albanien/Montenegro“, „Argentinien/Chile“,
    „Chile/Bolivien/Peru“ gelten für mehrere Länder; „(MYBOR)“ wird abgeschnitten.
  - Eine spätere Kopfzeile setzt ein Land zurück. Dadurch sieht der Bot heute nicht:
    Chile die 7 Fragen aus „Argentinien/Chile“, Peru die 3 aus „Chile/Bolivien/Peru“,
    Albanien seine eine eigene Frage (Wanderungen).
  - 17 Länder haben keine einzige Frage (Belize, USA, Thailand, Italien, …); das Tool
    liefert für sie nur „# Land“.
  - Zeilen mit nur zwei Zellen (Frage ohne Antwort, Notizen wie „siehe oben“) fallen weg.

## Vorschlag

### Tabelle

Keine neue Tabelle, keine neue Spalte. `quelle` bekommt den Wert `land`, `kategorie`
trägt das Land (genau ein Land je Zeile). Einmal von Hand im SQL-Editor:

```sql
alter table faq drop constraint faq_quelle_check;
alter table faq add constraint faq_quelle_check
  check (quelle in ('website', 'intern', 'land'));
-- Name prüfen, falls das drop scheitert:
-- select conname from pg_constraint where conrelid = 'faq'::regclass;
```

Historie, `aktiv`, `ausgeblendet`, Trigger: alles wie gehabt.

- Mehrländer-Gruppen werden je Land eine eigene Zeile (Albanien/Nordmazedonien/Montenegro:
  dieselbe Frage dreimal). Ein Land = ein Filter im Table Editor.
- `position`: 2000 + laufende Nummer in heutiger Reihenfolge (Länder in der Reihenfolge
  von `laender_faqs`, Fragen in CSV-Reihenfolge). Die Länderreihenfolge im Tool bleibt so
  gleich.

### Import: genau das, was der Bot heute sieht

`python faq_sync.py import-laender [--replace]`: der heutige CSV-Parser zieht unverändert
von agent_base.py nach faq_sync.py (`parse_laender_csv()`), daraus werden land-Zeilen.
Rundlauf-Prüfung im Import (wie bei `import_md`): `laender(rows)` ergibt exakt
`parse_laender_csv()` und die CSV-Länder sind genau `LAENDER`, sonst Abbruch. Nach dem
Insert liest der Import mit der `load()`-Abfrage zurück und vergleicht noch einmal. Die oben genannten heute verschluckten Fragen
werden nicht still nachgeholt; der Import gibt sie als Liste aus, der Owner entscheidet.

### Laufzeit

- `faq_sync.laender(rows) -> (laender_faqs, laender_faq_data)` baut beide Dicts aus
  land-Zeilen, gleiches Markdown wie heute.
- `render_beide` rendert nur `website` + `intern` (sonst landeten 78 KB Länder-FAQs im
  allgemeinen Block).
- agent_base: liest beim Import den Snapshot (der jetzt auch land-Zeilen enthält) statt
  der CSVs. `load()` setzt zusätzlich `agent_base.laender_faqs` / `laender_faq_data`
  (Neu-Bindung, atomar). Fehlt in Supabase jede land-Zeile, behält `load()` nur die
  Länder-FAQs im Speicher (Grund im Status) und übernimmt website/intern trotzdem; ein
  Push vor dem Import ist damit harmlos, ohne den Website-Sync zu blockieren. Null
  intern-Zeilen lassen `load()` wie bisher ganz scheitern.
- agent.py liest `agent_base.laender_faqs` zur Laufzeit statt der eigenen Import-Bindung,
  sonst sähe die Länder-Erkennung nie den Supabase-Stand.
- `country_faq_tool_description` bleibt beim Import aus dem Snapshot gebaut: Ein in
  Supabase neu angelegtes Land steht erst nach dem nächsten Snapshot-Export + Deploy in
  der Tool-Beschreibung; Erkennung, Prompt-Einschub und Tool-Antwort kennen es sofort.
- `/admin` zeigt im Load-Status die Zahl der land-Zeilen mit und listet unter
  `unbekannte_laender` jedes Land aus Supabase, das nicht in `LAENDER` steht
  (Tippfehler wie „Namibia “ legten sonst still ein neues Land an).
- Länderliste (D1): Konstante `LAENDER` (73) in faq_sync; `laender_faqs` = `LAENDER` ∪
  Länder aus Zeilen, Länder ohne Frage bleiben als „# Land“.

### Snapshot und CSVs

`python faq_sync.py export` (unverändert) schreibt jetzt auch die land-Zeilen. Ab dem Push
liest kein Laufzeitcode mehr die CSVs. Die 4 CSVs und faqs/laender.json (heute von nichts
gelesen) bleiben liegen, bis der Owner das Löschen freigibt.

### Tests

- Kein Test Snapshot gegen CSV: der bräche bei der ersten Pflege im Table Editor. Der
  Rundlauf ist eine Abbruch-Prüfung in `import-laender`. Als Unit-Test nur
  `test_laender_csv_rundlauf`: `laender(land_zeilen(parse_laender_csv()))` ==
  `parse_laender_csv()` (Parser gegen Datei, wie `test_allgemein_md_rundlauf`).
- `test_render_beide_ohne_laender`: prüft die Ausgabe von `render_beide(rows)`, nie den
  System-Prompt.
- `test_laender_ohne_frage_bleiben`, `test_load_ohne_land_zeilen_behaelt_laender`,
  `test_load_meldet_unbekannte_laender` (Supabase per Attrappe).
- `test_laender_fragen_gibt_es_noch` und die Live-Evals `test_land` bleiben unverändert;
  der Inhalt ist identisch, also ein Kontrolllauf `-k land`, kein A/B nötig.

### Reihenfolge (Push auf main deployt), zwei Phasen (D4)

Der heute deployte `load()` filtert nicht nach `quelle` und rendert jede aktive Zeile in
den allgemeinen Block. Stünden land-Zeilen in Supabase, während dieser Code läuft (auch
nach einem Revert), landeten beim nächsten Laden (02:05 oder „Jetzt syncen“) ~78 KB
Länder-FAQs im Endkunden-Prompt. Deshalb:

1. Push 1 (Commit „Schutz“): nur der `quelle`-Filter in `render_beide`. Allein harmlos,
   ab dann ist jeder spätere Schritt und jeder Revert sicher.
2. SQL oben im Supabase-SQL-Editor (Owner), dann `python faq_sync.py import-laender`
   (Rundlauf und Gegenprobe gegen Supabase grün), dann `python faq_sync.py export`. Der
   Export muss dem committeten Snapshot gleichen (`git diff faqs/snapshot.json` leer).
3. Push 2 (Commit „Umstellung“): Leser auf Snapshot/Supabase. Der Snapshot darin ist
   lokal aus dem CSV-Parse gebaut und in Schritt 2 durch den Export bestätigt.
4. Owner entscheidet später über die verschluckten Fragen (Import listet sie mit vollem
   Text) und das Löschen der CSVs.

## Offene Fragen

1. Import wie heute gesehen (Vorschlag) oder die verschluckten Fragen (Chile 7, Peru 3,
   Albanien 1) gleich mit aufnehmen?
2. Die 17 Länder ohne Frage: fallen weg (keine Zeile, kein Eintrag im Tool, Vokabular in
   chat_quality schrumpft auf 56) oder bleiben als Länderliste erhalten?
3. Mehrländer-Gruppen als Kopien je Land (Vorschlag) oder eine Zeile mit mehreren Ländern?

## Eng Review (/plan-eng-review, 2026-10-08)

Target: docs/designs/faq-laender-supabase.md. Fragen gehen auf Wunsch des Owners an Fable
(„ask Fable if you have any questions“), nicht an ihn.

Scope Challenge: 7 Dateien (faq_sync.py, agent_base.py, agent.py, sql/faq.sql,
faqs/snapshot.json, tests/test_faq_sync.py, chat_quality.py-Docstring), keine neue Klasse
→ Komplexitäts-Gate übersprungen. scope accepted as-is.

Befunde:
- F1 [P1] (9/10) chat_quality.py:125 `return sorted(agent_base.laender_faqs)`: das
  geschlossene Länder-Vokabular der Chat-Klassifizierung. Fallen die 17 leeren Länder weg
  (USA, Thailand, Italien, Nepal, Sri Lanka …), landen deren Chats auf „ohne Land“ oder
  einem Nachbarland. → D1.
- F2 [P1] (9/10) agent.py:25 `from agent_base import (… laender_faqs, …)`: eigene
  Bindung, sieht ein neu gebundenes Dict nie. Im Plan schon gelöst (Laufzeitzugriff).
- F3 [P2] (8/10) agent_base.py:1872 `laenderspezifische_faqs += laender_faqs[country]`:
  Erkennung (agent.py:663) und Prompt-Bau lesen das Dict zu zwei Zeitpunkten; ersetzt
  `load()` es dazwischen ohne ein Land, gibt es KeyError mitten im Chat. → D3.
- F4 [P2] (8/10) Mehrländer-Gruppen (3 Gruppen, Albanien/Nordmazedonien/Montenegro 7
  Fragen): Kopie je Land oder eine Zeile für mehrere. → D2.
- F5 [P2] (8/10) Ein Land, das in Supabase dazukommt, steht erst nach Export + Deploy in
  `country_faq_tool_description` (f-String beim Import, agent_base.py:928). Im Plan
  benannt, akzeptiert.

## Decision ledger

### R1 (D1): Länder ohne Frage
Finding: F1. Plan baseline: Offene Frage 2. Runtime evidence: 17 von 73 Ländern ohne Frage;
`country_vocabulary()` = Schlüssel von `laender_faqs`.
| Choice | Current | A | B |
|---|---|---|---|
| Leere Länder | 73 Länder, 17 leer | Länderliste (73) bleibt als Konstante `LAENDER` in faq_sync; `laender_faqs` = Liste ∪ Länder aus Zeilen, leere als „# Land“ | wegfallen: 56 Länder in Tool, Erkennung, Vokabular |
Options: A) Länderliste behalten (recommended) / B) Leere Länder fallen weg
State: approved
Actual answer: A (Fable für den Owner, 2026-10-08): die Liste ist Vokabular, kein FAQ-Inhalt. Folge: ein Land kommt per Supabase-Zeile dazu, verschwindet aber nur per Code.
Accepted scope: Konstante LAENDER (73) in faq_sync; laender_faqs = LAENDER ∪ Länder aus Zeilen, leere als „# Land“.

### R2 (D2): Mehrländer-Gruppen
Finding: F4.
| Choice | Current | A | B |
|---|---|---|---|
| Gruppen | eine CSV-Zeile gilt für 2-3 Länder | eine Zeile je Land (Kopie) | Spalte/Liste mit mehreren Ländern |
Options: A) Kopie je Land (recommended) / B) Eine Zeile, mehrere Länder
State: approved
Actual answer: A (Fable, 2026-10-08).
Accepted scope: eine Zeile je Land, kategorie = genau ein Land.

### R3 (D3): KeyError zwischen Erkennung und Prompt
Finding: F3.
| Choice | Current | A | B |
|---|---|---|---|
| Fehlt ein erkanntes Land im Prompt-Bau | KeyError (heute unmöglich, Dict fix) | `laender_faqs.get(country)`, fehlendes Land überspringen | nichts tun |
Options: A) Überspringen (recommended) / B) Nichts tun
State: approved
Actual answer: A (Fable, 2026-10-08).
Accepted scope: format_system_prompt nutzt laender_faqs.get(country) und überspringt fehlende Länder.

### Zusätze des Owners (Fable, 2026-10-08)
1. `test_render_ohne_laender` prüft die Ausgabe von `render_beide(rows)`, nie `format_system_prompt`.
2. `import-laender` gibt die verschluckten Fragen mit vollem Frage- und Antworttext aus (zum
   Einfügen im Table Editor von Hand), keine Zähler, kein zweiter Import-Modus.
3. Schritt 3 (import-laender → export → Commit → Push) in einem Zug, nicht um 02:05, und
   niemand drückt dazwischen „Jetzt syncen“.

### R4 (D4): Rollout
Finding: Outside Voice #1 (High): „in einem Zug“ hat keinen Rückweg; nach einem Revert
rendert der alte `load()` die land-Zeilen in jeden Endkunden-Prompt.
| Choice | Current | A | B |
|---|---|---|---|
| Rollout | ein Zug: import → export → commit → push | zwei Phasen: Push 1 nur `quelle`-Filter, dann SQL + Import, Push 2 Umstellung | ein Zug |
Options: A) Zwei Phasen (recommended) / B) Ein Zug
State: approved
Actual answer: A (Fable, 2026-10-08): „one extra push is cheap, a broken customer prompt at 02:05 is not“.
Accepted scope: zwei Commits; Reihenfolge-Abschnitt oben.

### R5 (D5): D1 wieder öffnen?
Finding: Outside Voice #2 (High): Vokabular von chat_quality hängt an FAQ-Inhalt.
| Choice | Current | A | B |
|---|---|---|---|
| Länderliste | approved D1: `LAENDER` in faq_sync ∪ Zeilen | D1 bleibt, unbekannte Länder im /admin-Status | statische Liste in chat_quality, Bot verliert leere Länder |
Options: A) D1 behalten (recommended) / B) Liste nach chat_quality
State: approved
Actual answer: A (Fable, 2026-10-08): eine zweite Liste würde driften; „# USA“ sagt dem Modell, dass es USA-Reisen gibt.
Accepted scope: unverändert D1 + `unbekannte_laender` im Load-Status.
History: D1 approved A; wieder geöffnet wegen Outside Voice #2, bestätigt.

### Outside Voice (Claude-Subagent, Codex nicht nutzbar: Modell gpt-6-astra für das Konto nicht verfügbar)
- #1 Rollout → D4 A, umgesetzt.
- #2 Vokabular → D5 A, D1 bleibt.
- #3 Rundlauf zirkulär → angenommen: Import liest nach dem Insert mit der `load()`-Abfrage zurück und vergleicht.
- #4 `kategorie` frei getippt → angenommen in kleiner Form: `unbekannte_laender` im Status; kein Normalisieren (würde Tippfehler verstecken).
- #5 `Allgemeine_FAQ.csv`-Assert beim Import (agent_base.py) → nicht in diesem Scope; als Risiko an den Owner gemeldet: ein in Supabase umbenannte intern-Frage + Export lässt `import agent_base` am Assert scheitern.
- #6 Snapshot driftet → bekannt und akzeptiert seit dem Vorgänger-Design (ponytail: Export, wenn nötig).
- #7 Doppeltes Lesen → angenommen: `country_faq_tool_base` liest einmal per `.get`; Prompt-Bau überspringt fehlende Länder (D3).
- #8 Reise-Kürzel (CRMON …) flach → abgelehnt: der heutige Parser verliert sie schon, Import bildet genau das heutige Bot-Wissen ab.
- #9 Kopien driften → bekannt (D2), bei 3 Gruppen akzeptiert.
- #10 Lohnt sich das? → Owner hat den Umzug ausdrücklich verlangt.
- #11 Faktencheck bestätigt die Liste der verschluckten Fragen.

Approval readiness: PASS (R1–R5 approved, Antworten von Fable für den Owner).

## Review-Abschnitte

1. Architektur: eine Tabelle, ein Lader, ein Snapshot; keine neue Komponente. Fehlerfall
   „keine land-Zeilen“ hält die Länder im Speicher (getestet). Fehlerfall „Supabase liefert
   > 1000 Zeilen“ (PostgREST-Standardgrenze) würde still kappen: heute 444 Zeilen, Abstand
   groß, nicht behoben. [P3] (6/10)
2. Code-Qualität: Parser unverändert verschoben (byte-gleiche `laender_faqs` gegen HEAD
   geprüft, inkl. Reihenfolge). agent.py liest zur Laufzeit über das Modul.
3. Tests:
```
faq_sync.laender()            [★★★] test_laender_ohne_frage_bleiben, test_laender_csv_rundlauf
faq_sync.parse_laender_csv()  [★★ ] test_laender_csv_rundlauf (gegen echte CSVs)
faq_sync.render_beide()       [★★★] test_render_beide_ohne_laender
faq_sync.load()  land da      [★★ ] test_load_meldet_unbekannte_laender
                 land fehlt   [★★★] test_load_ohne_land_zeilen_behaelt_laender
                 Fehler       [★★ ] unverändert (Vorgänger)
import_laender()              [GAP] nur manuell (Supabase); Abbruch-Prüfungen im Code
agent.py Erkennung            [→EVAL] test_faq.py -k land (Kontrolllauf)
```
4. Performance: Snapshot +80 KB beim Import, `load()` eine Abfrage mehr Zeilen; keine
   Schleifen mit Abfragen. Prompt unverändert.

Failure modes: Import halb geschrieben → Gegenprobe bricht ab, Bot unberührt (load()
ignoriert Länder nicht, aber Push 2 erst nach grünem Export). Keine stillen kritischen Lücken.
NOT in scope: verschluckte Fragen nachtragen, CSV-Löschung, Allgemeine_FAQ.csv-Assert, Mitarbeiter-Editor.
Parallelisierung: Sequential implementation, no parallelization opportunity.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | Claude-Subagent (Codex unusable) | Independent 2nd opinion | 1 | unavailable (native fallback completed) | 11 findings, 2 → Entscheidungen, 3 umgesetzt |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open (alle entschieden) | 6 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex, plan-review, unavailable (model_unusable); Claude-Subagent als Ersatz, 11 Befunde.
- **VERDICT:** Eng Review mit offenen, aber vollständig entschiedenen Befunden; bereit zur Umsetzung.

NO UNRESOLVED DECISIONS
