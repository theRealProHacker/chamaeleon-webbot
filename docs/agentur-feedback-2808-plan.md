# Agentur-Feedback 28.08 — Kontakt-Routing

Status: Eng-Review durch (2026-09-18). Zwei Änderungen, in dieser Reihenfolge.
Nichts davon ist implementiert oder gepusht. Push auf `main` = Deploy.

## Auslöser

Kundenfeedback "Chatbot Leon für den Agenturbereich – Aktualisierungen 28.08.":
Leon verweist Reisebüro-Mitarbeiter bei Flug-, Buchungsstatus- und
Ansprechpartnerfragen an das Vertriebsteam. Falsch — das gehört zur
Erlebnisberatung der jeweiligen Reise.

## Leitfrage (Owner, 2026-09-17)

> Geht es um die Reise selber (Ablauf etc.), dann Erlebnisberatung.
> Geht es um das Drumherum, dann Vertrieb.

Die bestehende Vertriebs-Themenliste in `faqs/agentur.md` ist damit die
Aufzählung des Drumherums, kein konkurrierendes Regelwerk. Eine Provisionsfrage
zu einer konkreten Buchung ist Drumherum und bleibt beim Vertrieb.

---

## Änderung 1 — Erlebnisberater aus der Webseite (zuerst, eigener Commit)

Kein Agentur-Thema: das wirkt auf allen öffentlichen Reiseseiten genauso.

### Befund, der sie auslöst (gemessen 2026-09-18)

`agent_base.py:627-628` hat zwei Prompt-Slots für Beratername und -telefon.
Gefüllt werden sie aus `travel_index.get_berater()` (`agent_base.py:693-699`).

    670 von 670 TourOne-Reisen tragen ein berater-Dict mit den richtigen
    Feldern (vorname, nachname, telefon, email, id, kuerzel, …).
    In 670 von 670 sind alle Werte leer.

Die API liefert die Hülle ohne Inhalt. Die Slots sind seit jeher tot; nur ein
vom Widget mitgeschickter Berater kommt je an. Das ist der eigentliche Defekt
hinter dem Agentur-Feedback.

### Die Seite trägt die Angabe — in zwei Formen

Reisedetailseite (~50k Zeichen, Box bei 84–99 % Tiefe):

    ![Mira Feldmann](/data/thumbs/…/PASSBILD900.jpg)

    Erlebnisberater\*in

    Mira Feldmann

    [+49 30 347996-901](tel:+49 30 347996-901)

Länder- und `-ALL`-Übersichtsseite (~15–22k Zeichen, Box bei 4–6 % Tiefe):

    ![Jonas Wehrle](/data/thumbs/…/Jonas-Wehrle.jpg)
    ![Jonas Wehrle](/data/thumbs/…/Jonas-Wehrle.jpg)

    **Jonas Wehrle**
    Erlebnisberaterin

    Ich bin für dich da.

    [+49 30 347996-902](tel:+49 30 347996-902 "Anruf starten")

Trefferquote über 25 zufällige indizierte Reise-URLs: **23/25**. Die zwei
Fehlschläge (`Sossusvlei-ALL`, `Erongo-ALL`) führen gar keinen Berater auf der
Seite — dort ist für niemanden etwas zu holen.

**Drei Telefonformate in 25 Seiten:** `+49 30 347996-901`,
`+49 30-347996-903`, `030347996905`. Nichts darf auf ein Nummernformat prüfen.

### Umsetzung (gebaut 2026-09-18)

Rangfolge in `format_system_prompt`, als `or`-Kette ohne Guard:

```python
seite_name, seite_telefon = berater_von_seite(endpoint)
index_berater = _berater_aus_index(endpoint)
kundenberater_name = seite_name or kundenberater_name or index_berater.get("name", "")
kundenberater_telefon = (
    seite_telefon or kundenberater_telefon or index_berater.get("telefon", "")
)
```

**Bei einer Reise gewinnt die Angabe von der Reiseseite** (Owner-Ansage), dann
die vom einbettenden Widget, zuletzt der Index. Der Index bleibt in der Kette,
damit es von selbst wirkt, falls TourOne das Feld je befüllt.

`berater_von_seite` ist auf Reise-URLs beschränkt. `travel_index.is_reise_url`
(neu) peekt in den Index und baut ihn nie — dieselbe Disziplin wie
`get_berater`, weil das auf jeder Chatnachricht läuft. Ohne die Schranke liefe
jeder Agentur-Request in den Abruf-Timeout, denn der Agenturbereich ist
login-geschützt und über das Website-Tool gar nicht erreichbar.

Bei Misserfolg `("", "")` — nie ein Teiltreffer, nie eine geratene Nummer.
Die Telefonnummer wird genommen, wie sie dasteht; wer normalisiert, gibt eine
Nummer aus, die so auf der Seite nicht steht.

`@ttl_cache(maxsize=1024, ttl=86400)` auf `berater_von_seite`: der HTML-Cache
erspart den Abruf, nicht das Markdownifizieren der 50k-Zeichen-Seite. Ohne den
Dekorator kostete der Promptbau gemessen 85 ms je Nachricht.

### Tests (`tests/test_berater.py`, 10 Fälle, 0,05 s)

Gegen gespeicherte Markup-Schnipsel, **kein Live-Modell, kein Netz**: beide
Box-Formen, alle drei Telefonformate, Seite ohne Berater, leeres Markdown,
Abrufausnahme, Name ohne Telefon, und die Schranke (eine Nicht-Reise-URL darf
die Seite gar nicht erst abrufen). Eine autouse-Fixture leert den `ttl_cache`
zwischen den Fällen — sonst bekäme der zweite Fall mit derselben URL die
Antwort des ersten und wäre grün, ohne das neue Markup je gesehen zu haben.

### Belegt

| Prüfung | Ergebnis |
|---|---|
| `pytest tests/test_berater.py` | 10 passed, 0,05 s |
| Restliche Suite (ohne `test_tool_history.py`) | 301 passed, 43 skipped |
| Prompt live, Reiseseite | "Mira Feldmann … +49 30 347996-901", 660 ms kalt |
| Prompt live, Übersichtsseite | "Jonas Wehrle … +49 30 347996-902", 2,2 s kalt |
| Prompt live, `/Agentur/Buchungen` | kein Berater, 0,1 ms — kein Abruf |
| Warm, nach `ttl_cache` | 0,0 ms |

`tests/test_tool_history.py` bricht die Sammlung (`No module named
'tool_history'`) — unfertige, nicht eingecheckte Arbeit aus einem anderen
Strang, nicht Teil dieser Änderung.

---

## Änderung 2 — Wissensbasis + Evals (danach)

### `faqs/agentur.md`

1. **Neue Routing-Sektion ersetzt den Header** (Wortlaut beschlossen, Kurzfassung):

```markdown
## Wen nennst du bei welcher Frage?

Geht es um die Reise selbst, geht es an die Erlebnisberatung. Geht es um das
Drumherum, geht es an das Vertriebsteam.

**1. Erlebnisberater*in der Reise** — Ablauf, Termine, Fluganfragen,
Buchungsstatus, Reservierungen, Optionen, "Wer ist mein Ansprechpartner?".
Frage nach der Reise und nenne dann Name und Telefonnummer von der Reiseseite.

**2. Vertriebsteam** — agentur@chamaeleon-reisen.de, +49 30 347 996 290.
Für die Themen der Liste unten, auch wenn sie an einer konkreten Buchung hängen.

**3. Empfang: +49 30 347 996 0** — wenn die Reise unklar bleibt oder die
Erlebnisberater*in nicht von der Reiseseite zu lesen ist.

Schicke Fluganfragen, Buchungsstatus und die Frage nach dem Ansprechpartner nie
an das Vertriebsteam.
```

Die Vertriebs-Themenliste bleibt unverändert darunter stehen.

2. **§6:257 und §6:279 ziehen mit** (Review 1A): heute "findest du auf der
   jeweiligen Reiseseite rechts in der gelben Berater-Box" (der Nutzer sucht
   selbst) → Leon nennt Name und Telefon. Sonst steht die neue Regel einmal
   gegen zwei gegenteilige Sätze.
3. **§9:374** → "Erlebnisberater*in der Reise, sonst Empfang".
4. **Schlussblock `# Vertriebsteam`** → kurze Drei-Kontakt-Zusammenfassung.
5. **§8** bekommt die Archiv-FAQ → buchhaltung@chamaeleon-reisen.de.
6. **§10 Just4You:** bestehender Text und Überschrift bleiben **exakt** wie in
   der Datei, inklusive "Das Prinzip: Zwölf Gäste reisen mit, und für dich ist
   ein Freiplatz vorgesehen." Nur zwei Zeilen angehängt:
   - nie Preise, Provisionshöhen oder individuelle Konditionen nennen, auch nicht
     wenn sie auf der Seite stehen; auf die Just4You-Seite verweisen.
   - Ansprechpartner: Vertriebsteam telefonisch, just4you@chamaeleon-reisen.de
     per Mail.
7. **Telefonformat** der von uns geschriebenen Kontakte bleibt "+49 30 …"
   (das Feedback schrieb "030 …"). Gilt nicht für Nummern von der Webseite.

### Evals (`tests/test_agentur_faq.py`)

Die DONE-Liste wird von Dreier- auf Vierer-Tupel erweitert (Review 4A):
`(id, frage, muss, darf_nicht)`, bestehende Fälle bekommen eine leere Liste.
`test_agentur_answer` prüft beide Richtungen mit dem vorhandenen
`keyword_matches`; der Deko-Sondertest bleibt, wo er ist. Modul-Docstring auf
das 28.08-Feedback nachziehen.

Fälle: Just4You-Provision (Link, keine Zahl) · Sonderreise (just4you@, 290) ·
archivierte Unterlagen (buchhaltung@) · Fluganfrage ohne Reise · Fluganfrage +
"Kaukasus" · "Wer ist mein Ansprechpartner?" · §9 "Gast legt selbst Buchung an"
(Review 5A) · Terminfrage + "Kaukasus" (Review 6A) · nicht existierende Reise
(Review 7A).

**Regression, gesetzt:** alle 19 Bestandsfälle laufen mit. Sie hängen am
geänderten Prompt-Kopf, besonders `vertriebsteam-mailto` (:89) und
`kundenabend` (:107), die beide die 290 erwarten.

---

## NOT in scope

- **Paxlounge** — Owner-Ansage 2026-09-16. Keine Login-Warnregel, keine
  "nie MeinChamäleon"-Regel, keine Umformulierung der Troubleshooting-FAQ.
  Erkennungssignal `<main data-affiliate-modus="paxconnect">` ist im Live-JS
  gefunden, Landing-Host ungemessen.
- **Just4You-Text umschreiben** — bleibt exakt wie in der Datei. Nur zwei Zeilen
  angehängt. Owner 2026-09-17.
- **TourOne-Berater-Feld befüllen lassen** — die eigentliche Quelle ist leer
  (0/670). Das ist eine Frage an den TourOne-Betreiber, kein Code. Der Parser
  ist der Umweg, nicht die Heilung.
- **`is_agentur`-Logging** — offenes TODO, blockiert die Erfolgsmessung
  (siehe offene Punkte), aber nicht diese Änderung.
- **Option vs. Reservierung in §6** — Owner will mitfixen (Review 8A), die
  Fachlage steht aber noch aus. Siehe offene Punkte.

## What already exists (und wird wiederverwendet)

- `tests/test_agentur_faq.py:134-165` — der "darf nicht enthalten"-Mechanismus
  existiert bereits (`_claims_free` + `test_agentur_deko_price_points_to_page`).
  Wird erweitert, nicht neu gebaut.
- `keyword_matches` (:54) und `_params` (:122) tragen die neuen Vierer-Tupel
  ohne Umbau. `_params` muss vier statt drei Felder entpacken.
- `agent_base.py:627-628` — die Prompt-Slots für Berater existieren seit jeher.
  Änderung 1 füllt sie, statt neue anzulegen.
- `agent_base.py:693-699` — die Fallback-Kette existiert. Es kommt ein Glied
  dazu, kein zweiter Mechanismus.
- `ttl_cache` auf `get_chamaeleon_website_html` — der Parser zahlt keinen
  zusätzlichen HTTP-Abruf.
- `agentur.md:255-259, :275-279` — §6 zeigt bereits auf die Erlebnisberatung,
  nur in der falschen Richtung (Nutzer sucht selbst). Wird gedreht, nicht ergänzt.

## Failure modes

| Pfad | Realistischer Ausfall | Test? | Fehlerbehandlung? | Sichtbar? |
|---|---|---|---|---|
| `berater_von_seite` | Website ändert das Markup | ja (Unit) | `("", "")` | still, Slot bleibt leer → KB-Regel 3 greift |
| `berater_von_seite` | Abruf 406 / Timeout | ja (Unit) | Ausnahme gefangen | still, wie oben |
| Seite ohne Berater (2/25) | kein Treffer | ja (Unit) | `("", "")` | still, korrekt |
| KB-Regel 3 | Leon rät trotzdem eine Durchwahl | Eval (7A) | keine | **Nutzer sieht falsche Nummer** |
| Regel 2 vs. Agentur-Modus | authentifizierte Agentur, Buchungsfrage | **kein Test** | keine | **offener Punkt** |

**Kritische Lücke:** die letzte Zeile. Bei verifizierter Agentur sagt
`agent_base.py:824-828` "nutze `buchungen_agentur_tool`, sobald nach konkreten
Buchungen, Reisenden, Terminen, Preisen oder der Provision zu einer Buchung
gefragt wird" — die neue KB-Regel sagt für Buchungsstatus "frag nach der Reise
und verweise an die Erlebnisberatung". Kein Eval-Fall läuft mit
`agentur_id != ""`. Eine Regression im wertvolleren Feature wäre unsichtbar.

## Parallelisierung

Sequenziell. Änderung 2 setzt auf Änderung 1 auf (die KB-Regel verweist auf eine
Angabe, die der Parser in den Prompt legt). Keine parallelen Bahnen.

## Implementation Tasks

- [ ] **T1 (P1, human: ~3h / CC: ~25min)** — agent_base — Berater aus der Seite parsen
  - Surfaced by: Outside Voice #1, nachgemessen — 670/670 Reisen ohne Berater-Daten im Index
  - Files: `agent_base.py`, `tests/test_berater.py` (neu)
  - Verify: `pytest tests/test_berater.py -v` (kein Live-Modell)
- [ ] **T2 (P1, human: ~1h / CC: ~10min)** — faqs — Routing-Sektion, §6, §9, Schlussblock, §8, §10
  - Surfaced by: Review 1A/2A/3A + Leitfrage des Owners
  - Files: `faqs/agentur.md`
  - Verify: Evals aus T3
- [ ] **T3 (P1, human: ~2h / CC: ~20min)** — tests — Vierer-Tupel + 9 Fälle + Regression
  - Surfaced by: Review 4A/5A/6A/7A, Outside Voice #8
  - Files: `tests/test_agentur_faq.py`
  - Verify: `RUN_AGENTUR_EVAL=1 pytest tests/test_agentur_faq.py -v`

## Offene Punkte (vor Änderung 2 zu entscheiden)

Aus der Outside Voice, jeweils im Code bestätigt. Keiner blockiert Änderung 1.

1. **§9:376-377** — Mail- und Telefonblock direkt unter der geänderten Zeile.
   Muss mit weg, sonst hebt er die Änderung auf. Ebenso Zeile 10
   ("grundsätzlich an das Vertriebsteam verweisen, besonders wenn es um
   individuelle Fälle … geht") als generischer Sog.
2. **Evals laufen ohne `page_content`.** Produktion schickt es immer
   (`app.py:102`); der Block `if is_agentur and page_content` fehlt im Test
   komplett. Die Evals messen einen anderen Prompt als die Produktion.
3. **Kein Eval mit `agentur_id != ""`** — siehe kritische Lücke oben.
4. **Fall 5 und 8 prüfen den Status quo.** Assertion nur `"Erlebnisberat"` —
   das besteht die heutige Antwort schon, weil §6:255 "wende dich bitte an die
   Erlebnisberatung" sagt. Nach Änderung 1 kann stattdessen auf ein
   Durchwahl-Muster geprüft werden, nicht auf den Personennamen (Personal
   wechselt) und nicht auf ein Nummernformat (drei Formate gemessen).
5. **`agent_base.py:563`** — "Wenn das mal nicht funktioniert, dann sage dem
   Kunden aber nichts davon … Versuche es geschickt zu umspielen." Arbeitet
   direkt gegen Regel 3 und gegen Eval-Fall 7A.
6. **Schritt 0 zieht eine verzerrte Stichprobe.** `chat_segments` erkennt
   `agentur` nur am Pfad `/Agentur`; per Origin/Referer erkannte Agentur-Chats
   auf Reiseseiten sind in Supabase nicht von Publikums-Chats unterscheidbar —
   also genau die fehlen, in denen Flug- und Buchungsstatusfragen vorkommen.
7. **"Wer ist mein Ansprechpartner?"** ist nach der Leitfrage eher Drumherum
   (Vertrieb); der Plan schickt es an den Empfang. Owner-Frage.
8. **Kein Erfolgsmaß** nach dem Deploy, und wegen Punkt 6 auch keins
   nachrüstbar, solange `is_agentur` nicht geloggt wird.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | Claude subagent (Codex `model_unusable`) | Independent 2nd opinion | 1 | unavailable (outside) | 10 Befunde, 6 im Code bestätigt, 1 falsch zitiert |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 8 Befunde, 1 kritische Lücke |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**OUTSIDE COVERAGE:** Codex nicht gelaufen — `CODEX_MODE: model_unusable`
(angefordert `gpt-6-astra`; CLI scheitert am Dekodieren der Modellliste,
`unknown variant 'max'`, Versions-Mismatch). Fallback auf einen Claude-Subagenten
im selben Harness: **keine echte Fremdmodell-Abdeckung**. Reparatur:
`GSTACK_CODEX_MODEL=gpt-5.5` oder Codex-CLI aktualisieren.

**CROSS-MODEL:** Tension am Umfang. Das Eng-Review akzeptierte "nur Markdown"
als kleinsten sauberen Diff; die Outside Voice hielt die Grenze selbst für die
Ursache des teuren Wegs. Nachgemessen: 670/670 Reisen ohne Berater-Daten,
Seitenparse 23/25. Owner entschied für den Parser, als eigene erste Änderung.

**VERDICT:** ENG REVIEW DURCH, Umsetzung freigegeben für Änderung 1.
Änderung 2 hat 8 offene Punkte, die vor der Umsetzung zu entscheiden sind.

**UNRESOLVED DECISIONS:**
- Option vs. Reservierung in §6: Owner will mitfixen (8A), die Fachlage steht aus — verhält sich eine Reservierung wie eine Option (7 Tage, dann Festbuchung) oder verfällt sie?
- Die 8 offenen Punkte zu Änderung 2 oben, jeder einzeln vorzulegen, bevor die Wissensbasis angefasst wird.
