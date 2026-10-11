# Plan: FAQ-Editor unter /admin (Variante A, Akkordeon)

Status: FREIGEGEBEN (Eng Review 2026-10-11, R1 bis R10 vom Owner mit A beantwortet); Bau noch nicht begonnen
Vorgänger: docs/designs/faq-supabase-sync.md (dort Offene Frage 4: „Editor für Mitarbeiter (V2)“),
docs/designs/faq-laender-supabase.md (dort „NOT in scope: Mitarbeiter-Editor“).
Vorlage: Design-Lab `~/.gstack/projects/theRealProHacker-chamaeleon-webbot/designs/admin-faq-editor-20261008/a.html`
(+ core.js, base.css). Der Owner hat Variante A gewählt.

## Ziel

Mitarbeiter pflegen die internen und die Länder-FAQs selbst, ohne Entwickler, ohne git und
ohne Supabase-Zugang. Eine Änderung gilt sofort für Leon. Jede frühere Fassung lässt sich
zurückholen. Vor einer Änderung, die etwas wegnimmt, sagt die Seite genau, was verloren geht.

## Heute (gelesen 2026-10-11)

- Tabelle `faq` (sql/faq.sql): `quelle` website | intern | land, `aktiv`, `ausgeblendet`,
  `position`. Trigger schreibt bei UPDATE und DELETE die alte Zeile nach `faq_history`.
  Kein Trigger bei INSERT, keine Spalte „wer“.
- `faq_sync.load()` liest nur `aktiv and not ausgeblendet` und bindet
  `agent_base.allgemeine_faqs`, `allgemeine_faqs_agentur`, `general_faq_data`,
  `laender_faqs`, `laender_faq_data` neu. Null intern-Zeilen: `load()` scheitert, alter
  Stand bleibt.
- `render()` schreibt `## Kategorie` bei jedem Kategoriewechsel entlang `position`.
  intern liegt lückenlos auf 0..37 (11 Kategorien), land auf 2000..2343.
- /admin: eine statische Seite (static/admin/index.html, alles inline), Routen als Tupel
  in `dashboard.routes`, `auth_required` (Basic Auth, dieselben Zugangsdaten wie
  /dashboard), POSTs mit `ADMIN_LIMIT` = 30 je Stunde.
- Ein gunicorn-Worker (Dockerfile `WEB_CONCURRENCY=1`): der Prozess, der schreibt, ist
  der Prozess, der die Chats beantwortet.
- `id` ist `generated always as identity`: ein Insert mit vorgegebener id scheitert in
  Postgres, und der Supabase-Client kann `overriding system value` nicht ausdrücken.

## Plan

### Umfang der Seite

Wie im Lab: Reiter „Allgemein“ (quelle intern) und „Länder“ (quelle land, alle 73 Länder
aus `faq_sync.LAENDER`, auch die 17 ohne Frage). Suche, „Neue Frage“, eingeklappte
Kategorien bzw. Länder, Frage öffnet das Formular an Ort und Stelle, „Frühere Fassungen“,
„Gelöscht“ als eigene, ruhige Liste, Rückfrage-Dialog vor zerstörenden Änderungen.
Kategorie: Auswahl plus „Neue Kategorie …“. Land: nur Auswahl, kein Freitext.

Website-FAQs (Entscheidung 1): im Reiter „Allgemein“ ganz am Ende, unter den editierbaren
Kategorien und unter „Gelöscht“, als eigener Abschnitt „Von der Website“ mit dem Satz
„Leon kennt diese Fragen von chamaeleon-reisen.de/Infos, geändert werden sie dort.“
Der Abschnitt hat keine weiße Karte und keinen Schatten. Aufklappen zeigt die Antwort
als Lesetext mit klickbaren Links und der Zeile „Nur lesbar. Diese Antwort wird auf der
Website gepflegt.“ Kein Formular, kein Ausblenden, kein Löschen. Die Suche findet sie
mit und zeigt sie in diesem Abschnitt.

### Löschen = `aktiv = false` (kein DDL)

„Löschen“ setzt bei intern- und land-Zeilen `aktiv = false`. Der Sync fasst `aktiv` nur
bei website-Zeilen an (über `extern_id`), `load()` und `export()` filtern schon darauf.
Damit gilt:

- „Gelöscht“ = Zeilen mit `aktiv = false` im Reiter. Wiederherstellen = `aktiv = true`.
  „Gelöscht am“ ist `updated_at` der Zeile (der Trigger setzt es beim UPDATE), die
  Liste braucht dafür keine Historie.
- Beides sind UPDATEs, der vorhandene Trigger protokolliert sie. Die id bleibt, die
  früheren Fassungen hängen weiter an der Frage.
- Die Punkte aus dem Checkpoint entfallen: kein `overriding system value`, und ein
  `TRUNCATE` kann der Editor nicht auslösen (bleibt ein Risiko nur im SQL-Editor).

sql/faq.sql bekommt nur einen Kommentar an `aktiv`: „website: vom Sync; intern/land: vom
Editor gelöscht“.

### Reihenfolge neuer Fragen

`render()` gruppiert künftig nach (`quelle`, `kategorie`) in der Reihenfolge des ersten
Auftretens, innerhalb der Gruppe nach `position`. Die Quelle gehört dazu, damit eine neue
interne Kategorie mit dem Namen einer Website-Kategorie nicht in den Website-Block rutscht. Geprüft am Snapshot: intern und website teilen
keinen Kategorienamen, und jede Kategorie steht heute in einem Block. Die Ausgabe bleibt
also zeichengleich (Test unten).

Damit braucht kein Schreibvorgang eine zweite Zeile anzufassen:

- Neue Frage: `position` = höchste Position aller Zeilen dieser `quelle` + 1, gelöschte
  und ausgeblendete mitgezählt (sonst teilt sich eine wiederhergestellte Zeile die
  Position mit einer neuen). Sie erscheint am Ende ihrer
  Kategorie; eine neue Kategorie erscheint am Ende des internen Blocks.
- Kategorie wechseln: dieselbe Regel, die Frage rückt ans Ende der Zielkategorie.
  „Rückgängig“ holt dabei auch die alte Position zurück (R4); der Platz ist frei, weil
  neue Positionen immer Maximum + 1 sind.
- Länder: `laender()` gruppiert schon nach Land, dort ändert sich nichts.
- Kein Umsortieren per Hand in V1 (das Lab hat es auch nicht).
- `load()` und `export()` sortieren nach `position`, dann `id`. Positionen sind nicht
  eindeutig erzwungen; bei Gleichstand bleibt die Reihenfolge so stabil.

Ohne diese Änderung müsste „ans Ende der Kategorie“ alle folgenden Zeilen neu
nummerieren, und jede davon stünde als Schein-Änderung in `faq_history`.

### Server (faq_sync.py, Funktionen, keine Klasse)

- `editor_zeilen() -> dict`: alle intern- und land-Zeilen mit `id`, `ausgeblendet`,
  `aktiv`, `updated_at`; dazu die website-Zeilen, die Leon gerade kennt (`aktiv`, nicht
  `ausgeblendet`), und `laender: LAENDER`. Rund 435 Zeilen (gemessen), unter der
  PostgREST-Grenze von 1000.
- `anlegen(d)`, `aendern(id, d, updated_at)`, `loeschen(id)`, `wiederherstellen(id)`,
  `fassung_zurueck(id, history_id)`, `verlauf(id) -> list` (Einträge aus `faq_history`
  zu einer Frage, neueste zuerst).
- `verlauf` lässt Einträge weg, deren Frage, Antwort, Kategorie und Ausblenden dem
  nächstneueren Stand gleichen. Löschen und Wiederherstellen protokollieren sonst jeweils
  eine inhaltsgleiche „Fassung“ und blähen „Frühere Fassungen (n)“ auf.
- `fassung_zurueck` schreibt `frage`, `antwort`, `kategorie`, `ausgeblendet` aus dem
  History-Eintrag, dazu `position`, wenn sich dabei die Kategorie ändert (R4); nie
  `aktiv`, `quelle` oder `id`. Der Eintrag muss zu dieser Frage gehören
  (`faq_history.faq_id == id`), sonst 404. `_pruefen` samt Wächter läuft auf dem
  Ergebnis, und `updated_at` wird wie bei `aendern` mitgeprüft.
- `aendern` und `fassung_zurueck` lehnen gelöschte Zeilen ab (erst wiederherstellen),
  sonst verschöbe jede Änderung das „Gelöscht am“. `quelle` ändert sich nie.
- Jede schreibende Funktion: `_lock` nehmen, die Zeilen der Quelle frisch lesen (für
  Dubletten, Wächter und höchste Position, nie aus der Liste der Seite), prüfen,
  schreiben, `load()`,
  Ergebnis `{ok, zeile, geladen, historie_id}` zurückgeben. `historie_id` ist der
  Eintrag, den der Trigger für diesen Schreibvorgang angelegt hat (neuester Eintrag zu
  dieser `faq_id`, unter `_lock` gelesen); die Seite braucht ihn für „Rückgängig“.
- Prüfung `_pruefen(d)`, Fehler als deutscher Satz für die Seite:
  - `quelle` nur intern oder land; die Zeile, die geändert wird, muss selbst intern oder
    land sein (website-Zeilen sind über diese Routen nicht schreibbar).
  - `frage` und `kategorie` nicht leer, getrimmt, einzeilig (ein Zeilenumbruch zerbräche
    `**F: …**` und `## …` im Prompt). `frage` höchstens 300 Zeichen, `antwort` höchstens
    4000 (R8; längste Antwort heute 1 943). `antwort` nicht leer, außer die Zeile
    ist und bleibt `ausgeblendet` (die importierten Notizen ohne Antwort müssen sich
    bearbeiten lassen, ohne dass Leon je eine leere Antwort sieht).
  - land: `kategorie` muss in `LAENDER` stehen, sobald sie gesetzt oder geändert wird.
    Vorhandene Zeilen mit einem anderen Wert bleiben bearbeitbar, siehe „Daten, die das
    Lab nicht zeigte“ (R6).
  - `frage` nicht doppelt unter den Zeilen, die Leon sieht (aktiv, nicht ausgeblendet),
    im selben Schlüsselraum: intern gegen intern und website zusammen, land gegen dasselbe
    Land (R5). `general_faq_data` und `laender_faq_data` sind nach Frage verschlüsselt,
    die zweite würde die erste still ersetzen. Geprüft bei Anlegen, Ändern, Einblenden,
    Wiederherstellen und `fassung_zurueck`.
  - Wächter „letzte sichtbare intern-Zeile“: Löschen, Ausblenden und ein
    `fassung_zurueck`, das ausblendet, werden abgelehnt. Sonst scheitert `load()` und
    Leon bliebe unbemerkt auf dem alten Stand.
- Gleichzeitiges Bearbeiten: `aendern` schreibt mit `.eq("updated_at", …)`. Trifft das
  keine Zeile, kommt 409, wenn die id existiert, sonst 404. Die Seite sagt „Diese Frage
  wurde inzwischen geändert“, lädt neu und behält den getippten Text im Formular.
  `updated_at` ist auf der Seite eine undurchsichtige Zeichenkette, unverändert
  zurückgeschickt und nach jedem Schreiben aus der Antwort übernommen (nie durch `Date`
  geschickt, das kürzt auf Millisekunden und jedes Speichern endete als 409).
- Supabase nicht erreichbar: jede Route fängt die Ausnahme und antwortet mit einem Satz
  („Speichern hat nicht geklappt, bitte noch einmal versuchen“), nie mit einem nackten 500.
- Schreiben gelungen, `load()` gescheitert (`geladen: false`): die Seite meldet
  „Gespeichert. Leon antwortet erst nach dem nächsten Laden so.“ mit dem Knopf „Jetzt
  laden“ (`POST /admin/faq/laden`, ruft `load()` unter `_lock`).
- `sync()` ruft sein `load()` künftig innerhalb von `_lock` (R3). Damit gibt es genau
  eine Reihenfolge von Schreiben und Laden, und ein nächtlicher Sync kann eine eben
  gespeicherte Änderung nicht mit einem älteren Stand überdecken. Zwischen den
  Zuweisungen in `load()` darf kein `print` und kein Netzaufruf stehen.
- `country_faq_tool_description` (f-String beim Import) braucht kein Nachladen: der
  Editor kann kein Land anlegen, `LAENDER` ändert sich nur per Deploy.

### Daten, die das Lab nicht zeigte

`import_laender` hat den „Rest“ der CSVs als land-Zeilen mit `ausgeblendet = true`
angelegt: Fragen ohne Antwort, Notizen, und die Visum-Zeilen mit dem Hinweis „Nicht
einblenden …“ als Antwort. Der Editor zeigt sie als „(ausgeblendet)“. Eine Zeile ohne
Antwort lässt sich erst einblenden, wenn jemand eine Antwort schreibt; die Seite sagt
das am Antwortfeld. Ein solches Land gehört in die Design-Prüfung.

land-Zeilen, deren `kategorie` kein Land aus `LAENDER` ist (heute vermutlich eine
Visum-Zeile unter „Afrika“, später Tippfehler aus dem Table Editor), stehen als eigene
Gruppe am Ende des Reiters „Länder“ (R6). Dort lassen sie sich bearbeiten, einem Land
zuordnen oder löschen. Der Name der Gruppe wird in der Design-Prüfung festgelegt.

### Routen (dashboard.py, Tupel in `routes`, alle `auth_required`)

| Route | Methode | Funktion |
|---|---|---|
| `/admin/faq` | GET | Seite `static/admin/faq.html` |
| `/admin/faq/zeilen` | GET | `editor_zeilen()` |
| `/admin/faq/zeilen` | POST | `anlegen` |
| `/admin/faq/zeilen/<int:id>` | POST | `aendern` (auch Ausblenden) |
| `/admin/faq/zeilen/<int:id>/loeschen` | POST | `loeschen` |
| `/admin/faq/zeilen/<int:id>/wiederherstellen` | POST | `wiederherstellen` |
| `/admin/faq/zeilen/<int:id>/verlauf` | GET | `verlauf` |
| `/admin/faq/zeilen/<int:id>/verlauf/<int:hid>` | POST | `fassung_zurueck` |
| `/admin/faq/laden` | POST | `load()` unter `_lock`, liefert den Lade-Status |

Jede schreibende Route verlangt einen JSON-Rumpf (`request.is_json`, sonst 400); die
Seite schickt immer Daten oder `{}` (R2). Ein Formular auf einer fremden Seite kann kein
`application/json` senden, ein fremdes `fetch` scheitert an der Vorabanfrage.

Limit: neue Konstante `FAQ_EDIT_LIMIT = "300 per hour"` in rate_limit.py für die POSTs.
`ADMIN_LIMIT` (30 je Stunde) reicht für eine Pflege-Sitzung nicht. GETs mit
`DASHBOARD_LIMIT`; `verlauf` wird erst beim Aufklappen von „Frühere Fassungen“ geladen,
nicht bei jedem Öffnen einer Frage.

Erreichtes Limit (R1): `rate_limit._on_rate_limit` antwortet heute für fast jede Route
mit 200 und einem Chat-Ereignis, ein gebremstes Speichern sähe wie ein Erfolg aus. Neu:
429 mit JSON `{"error": …}` für alles außer `/chat/stream`. Das gilt auch für die
vorhandenen /admin-Knöpfe und das Dashboard. Die Seite zeigt „Zu viele Änderungen,
bitte kurz warten“.

### Seite (static/admin/faq.html, eine Datei wie index.html)

- Übernahme aus dem Lab: a.html (Aufbau, Zustände), aus core.js `analyze`, der
  Wort-Diff, `gate`, `katFieldHtml`, `matches`, `autoGrow`; aus base.css die Tokens
  (Schatten-Tokens, Knöpfe mit Zeiger und Hover). Nicht übernommen: Lab-Store,
  Beispiel-Historie, `labProbes`, `labSetState`.
- Der Store wird durch `fetch` auf die Routen ersetzt. „Frühere Fassungen“ lädt beim
  Aufklappen. Alle Texte außer dem Website-Lesetext gehen weiter durch `esc()`.
- „Rückgängig“ im Toast: nach Ändern und Ausblenden = `fassung_zurueck` auf die
  `historie_id` aus der Antwort; nach Löschen = `wiederherstellen`; nach Anlegen =
  `loeschen`.
- Website-Antworten sind Markdown. Lesetext über `marked` und `DOMPurify` von cdnjs mit
  fester Version und `integrity`-Hash (keine eigene Markdown-Umsetzung). Die editierbaren
  Antworten bleiben reiner Text im Textfeld.
- Fehler der Prüfung stehen am betroffenen Feld, in Worten, mit denen der Mitarbeiter
  etwas tun kann. Keine Statuszahlen, kein JSON auf der Seite.
- static/admin/index.html bekommt im Abschnitt „FAQs“ einen Link „FAQs bearbeiten“.
- Bekannt aus dem Lab und hier zu lösen: auf dem Telefon liegt „Speichern“ bei langen
  Antworten unter dem Sichtfeld (Aktionszeile im offenen Formular unten anheften).

### Tests

Billige Unit-Tests in tests/test_faq_sync.py mit der vorhandenen Attrappe `_Abfrage`
(um insert/update erweitert), keine Prompt-Substring-Checks:

- Kein dauerhafter Vergleich alt gegen neu am Snapshot (R9): er würde nach der ersten
  echten Pflege rot. Vor Push 1 einmal von Hand vergleichen; dauerhaft tragen
  `test_allgemein_md_rundlauf` und `test_render_intern_vor_website`.
- `test_render_trennt_quellen`: gleicher Kategoriename in intern und website ergibt zwei
  Blöcke.
- `test_neue_frage_landet_in_ihrer_kategorie`: Zeile mit höchster Position + 1 erscheint
  im Block ihrer Kategorie.
- `test_pruefen_*`: unbekanntes Land, leere Felder, mehrzeilige Frage, Obergrenzen,
  doppelte Frage (auch gegen eine sichtbare website-Zeile, auch beim Wiederherstellen),
  website-Zeile, `quelle` ändern, gelöschte Zeile ändern, letzte interne Zeile.
- `test_fassung_zurueck_fremde_historie`, `test_fassung_zurueck_letzte_interne`,
  `test_fassung_zurueck_holt_position`.
- `test_position_zaehlt_geloeschte_mit`.
- `test_schreiben_ok_laden_scheitert`: Antwort trägt `geladen: false`.
- `test_loeschen_setzt_aktiv` und `test_wiederherstellen`.
- `test_aendern_konflikt`: keine Zeile getroffen ergibt 409, unbekannte id 404. Die
  Attrappe ignoriert Filter; ob der Filter auf `updated_at` wirklich trifft, zeigt nur
  die Pflichtprobe unten.
- `test_leere_antwort_nur_ausgeblendet`.
- `test_editor_zeilen_website_nur_sichtbare`.
- `test_verlauf_ohne_inhaltsgleiche`.
- Routen: 401 ohne Auth, 400 mit Fehlersatz bei Prüf-Fehler, 400 beim Schreiben auf eine
  website-Zeile, 400 bei POST ohne JSON-Rumpf, 429 JSON bei erreichtem Limit. Die
  Routen-Tests ersetzen `faq_sync._supabase`; `import app` arbeitet sonst gegen die
  echte Tabelle.

Evals: tests/test_faq.py hängt Schlüssel an Fragetexte (`EXPECTED_KEYWORDS_GENERAL_FAQ`,
`COUNTRY_EXPECTED_KEYWORDS`), gelesen aus dem committeten Snapshot. Benennt ein
Mitarbeiter eine Frage um, fällt das erst beim nächsten `faq_sync.py export` auf; dann
wird der Schlüssel nachgezogen, nie gelöscht.

### Design-Prüfung vor „fertig“

Die echte Seite lokal starten, Zustände wie im Lab durchklicken (Liste, Kategorie offen,
Frage offen, Neu, Länder, leeres Land, Land mit ausgeblendeten Zeilen ohne Antwort,
Website-Fragen, Warnung, Löschen) bei 1280 und 390 Pixeln, Screenshots, ein frischer
Subagent prüft die Bilder („Was ist an diesem Bild falsch?“), danach /design-review.

Ohne Testtabelle (Entscheidung 3) läuft die lokale Seite gegen die echte Tabelle. Für
die Prüfung deshalb: nur lesende Zustände frei durchklicken; Schreibwege an genau einer
Testfrage „TEST bitte ignorieren“ im Land Italien (heute ohne Fragen), am Ende
gelöscht. Sie wird über die Route mit `ausgeblendet: true` angelegt (`anlegen` nimmt
das Feld an, das Formular bietet es nicht), damit Leon sie zu keinem Zeitpunkt sieht. Sie bleibt als Zeile mit `aktiv = false` unter
„Gelöscht“ stehen, bis der Owner sie im Supabase Table Editor entfernt. Nicht um 02:05
und nicht während jemand „Jetzt syncen“ drückt.

Pflichtprobe an dieser Testfrage, über die echte Seite: speichern (200), in einem zweiten
Reiter mit altem Stand speichern (409), danach wieder speichern (200). Erst sie belegt,
dass der Abgleich auf `updated_at` über PostgREST trifft.

### Reihenfolge (Push auf main deployt)

1. Push 1, „render gruppiert“: `render()` gruppiert nach (`quelle`, `kategorie`),
   `load()` und `export()` sortieren zusätzlich nach `id`, `sync()` lädt innerhalb von
   `_lock`. Vorher einmal von Hand: `render_beide(snapshot)` alt und neu zeichengleich,
   `python faq_sync.py export` ergibt keinen Diff im Snapshot. Allein harmlos.
2. Push 2, „Editor-Server“: Funktionen in faq_sync.py, Routen, `FAQ_EDIT_LIMIT`, 429
   JSON bei erreichtem Limit, Kommentar in sql/faq.sql, Sperre für `import --replace`
   und `import-laender --replace` (brechen immer ab, außer mit `--pflege-verwerfen`;
   die Meldung sagt, dass alle gepflegten Zeilen und ihre früheren Fassungen verloren
   gehen, R7), Unit-Tests. Noch keine Seite; die Routen liegen hinter Basic
   Auth und ändern ohne Aufruf nichts.
3. Seite static/admin/faq.html aus dem Lab bauen, lokal gegen die Routen, Design-Prüfung
   wie oben. Erst wenn die sauber ist: Push 3 mit faq.html und dem Link in index.html.
4. Nach dem Deploy: /admin/faq einmal live öffnen, eine Frage aufklappen, nichts
   speichern. Testfrage aus Schritt 3 dem Owner zum Entfernen nennen.
5. Nach der ersten echten Pflege: `python faq_sync.py export`, Snapshot committen,
   Eval-Schlüssel in tests/test_faq.py nachziehen, falls eine Frage umbenannt wurde.

## Entscheidungen des Owners (2026-10-11)

1. Website-FAQs stehen im Reiter „Allgemein“, ganz am Ende und klar abgesetzt: eigener
   Abschnitt „Von der Website“ unter den editierbaren Kategorien und unter „Gelöscht“,
   mit einem Satz, dass sie auf chamaeleon-reisen.de/Infos gepflegt werden. Aufklappen
   zeigt Frage und Antwort als Lesetext (Markdown gerendert, Links klickbar), kein
   Formular, kein Ausblenden, kein Löschen. Die Suche findet sie mit. `editor_zeilen()`
   liefert dafür auch die aktiven, nicht ausgeblendeten website-Zeilen; die
   Schreib-Routen lehnen website-Zeilen ab. Im Lab umgesetzt (a.html, Zustand
   „Website-Fragen“), Details oben unter „Umfang der Seite“.
2. Eigene Zugangsdaten für den Editor: jetzt nicht. /admin/faq nutzt `auth_required`
   wie der Rest von /admin.
3. Testtabelle für lokales Ausprobieren: jetzt nicht. Folge: Vorgehen mit einer
   Testfrage, siehe „Design-Prüfung“.

4. Eng Review R1 bis R10: alle mit A beantwortet („all A“), oben eingearbeitet. R10 steht
   als Eintrag in TODOS.md.

## Zurückgestellt (nicht eingeplant, wartet auf den Owner)

- Eigenes Passwort nur für `/admin/faq*` (heute teilt /admin die Zugangsdaten mit
  /dashboard und damit mit den Chat-Verläufen).
- Testtabelle `faq_test` mit eigener Historie und eigenem Trigger, lokal per
  `FAQ_TABLE`. Bis dahin schreibt jeder lokale Testklick in die echte Tabelle.
- Website-Zeilen im Editor ausblenden.
- „Rückgängig“ im Toast durch die Rückfrage für zerstörende Änderungen führen.
- Diffs, die auch Änderungen an Frage, Kategorie und Ausblenden zeigen.
- Spalte `geaendert_von` in `faq_history` (Schema-Änderung; braucht das eigene Editor-Passwort mit
  Namen je Person).
- Suchtreffer, die nur in der Antwort liegen, mit Textausschnitt zeigen.
- Reihenfolge per Hand ändern.

NOT in scope: Website-FAQs bearbeiten, neue Länder anlegen, agentur.md, Varianten B und C,
Schutz gegen `TRUNCATE` im SQL-Editor, Abschluss des design-shotgun-Ablaufs (approved.json).

Dateien: faq_sync.py, dashboard.py, rate_limit.py, static/admin/faq.html (neu),
static/admin/index.html, sql/faq.sql (Kommentar), tests/test_faq_sync.py, TODOS.md. Kein DDL, keine
neue Abhängigkeit im Python-Teil, keine Klasse.

## Eng Review (/plan-eng-review, 2026-10-11)

Target: docs/designs/admin-faq-editor.md, Stand HEAD da424f9. Prüfer: Subagent, der den Plan
nicht geschrieben hat. Der Owner war nicht erreichbar: jede Frage, die der Skill gestellt
hätte, stand im Ledger zunächst als offen mit Optionen und Empfehlung. Der Owner hat
danach alle zehn mit A beantwortet („all A“, 2026-10-11); der Plan oben ist entsprechend
geändert. Zeilenangaben in den Befunden beziehen sich auf den Stand vor dieser Änderung.
Die „Entscheidungen des Owners“ und „Zurückgestellt“ wurden nicht wieder geöffnet.

Belegarten: „gelesen“ = Code gelesen, „gemessen“ = lokal ausgeführt (nur lesend, ohne Netz,
gegen faqs/snapshot.json sha256 318237bd… und die CSVs), „abgeleitet“ = nicht nachprüfbar
ohne Datenbank oder Browser.

Scope Challenge: 7 Dateien, keine neue Klasse, also kein Komplexitäts-Gate. Kein Teil ist
schon gebaut (`editor_zeilen`, `FAQ_EDIT_LIMIT`, `pflege-verwerfen` kommen im Repo nicht
vor). Wiederverwendet werden `_lock`, `load()`, `LAENDER`, `auth_required`, das
`routes`-Tupel, der Historien-Trigger. scope accepted as-is.

### Was die Prüfung bestätigt

- `render()` gruppiert: für den committeten Snapshot zeichengleich, beide Blöcke (gemessen:
  34 710 und 12 081 Zeichen, alt == neu). intern 0..37 lückenlos in 11 Blöcken, website 4
  Blöcke, kein gemeinsamer Kategoriename, keine Positions-Gleichstände. Auch
  `render(parse_md(allgemein.md)) == _mit_a(md)` hält mit der neuen Fassung, also bleiben
  `test_allgemein_md_rundlauf` und die Abbruch-Prüfung in `import_md` grün.
- Löschen per `aktiv = false` ist gegen alle Leser und Schreiber sicher (gelesen):
  `load()` faq_sync.py:239 und `export()` :373 filtern `aktiv`; `sync()` liest nur
  `quelle = website` (:278), setzt `aktiv` nur über `extern_id` (:290) und upsertet auf
  `extern_id` (:286), das bei intern und land null ist; chat_quality.py:125 und :1058 lesen
  nur die Dicts in agent_base; der Snapshot enthält nur sichtbare Zeilen. Einzige Ausnahme:
  `import --replace` und `import-laender --replace` löschen hart (:320, :351), siehe F9.
- `_lock` blockiert keine Chats. Kein Chat-Pfad nimmt die Sperre (agent.py:663,
  agent_base.py:910, :1839, :1847 lesen nur). Unter gevent ist `threading.Lock` ein
  `gevent.thread.LockType` (gemessen mit gevent 26.5.0: ein Greenlet hält die Sperre über
  eine Wartezeit, ein drittes läuft dazwischen). Abgeleitet, nicht gemessen: dass gunicorn
  im gevent-Worker vor dem App-Import patcht (gunicorn ist nur im Image installiert).
- `load()` ist für laufende Chats atomar genug (gelesen): nach `.execute()` in :239 folgt
  bis :258 nur Rechnen, kein Netz, also unter gevent kein Wechsel zwischen den fünf
  Zuweisungen. Die Dicts werden neu gebunden, nie verändert; die Leser nutzen `.get`
  (Entscheidung D3 des Vorgängers). Vorsicht: ein `print` oder Netzaufruf zwischen :247
  und :254 würde das aufheben.
- Optimistische Prüfung: postgrest 2.30.0 baut
  `updated_at=eq.2026-10-08T05%3A28%3A11.123456%2B00%3A00` mit `Prefer:
  return=representation` (gemessen, Anfrage gebaut, nicht gesendet). Das `+` wird also
  richtig kodiert, und Postgres vergleicht timestamptz als Wert. Der Abgleich in der
  Datenbank ist der richtige Weg, siehe F11 für die Stelle, an der er trotzdem bricht.
- Wächter „letzte sichtbare intern-Zeile“ trifft genau die Bedingung in `load()` :241
  (intern, aktiv, nicht ausgeblendet). Lücken siehe F7.
- Doppelte Fragen im Bestand: keine. Weder unter intern, noch intern gegen website, noch
  je Land, noch unter den 14 ausgeblendeten CSV-Zeilen oder gegen sichtbare Zeilen
  (gemessen). Die Regel sperrt also keine vorhandene Zeile aus.

### Befunde

Architektur:
- F1 [P1] (9/10) rate_limit.py:159-170, gelesen. `_on_rate_limit` antwortet für jede Route
  außer den beiden Auth-Routen mit `status=200`, `mimetype="text/event-stream"` und dem
  Rumpf `data: {"type": "error", "data": "rate_limited"}`. Ein gebremstes Speichern im
  Editor kommt also als 200 zurück, ohne dass etwas geschrieben wurde. Der Plan nennt
  400 und 409, diese Antwort nicht. Ohne Behandlung zeigt die Seite „Gespeichert“ oder
  wirft beim JSON-Lesen. Kritische Lücke: kein Test, keine Behandlung, still. → R1.
- F2 [P2] (7/10) dashboard.py:898-905, gelesen; Browser-Verhalten abgeleitet.
  `auth_required` prüft nur Basic Auth. Der Browser schickt gemerkte Basic-Auth-Daten auch
  bei einem Formular-POST von einer fremden Seite mit. `loeschen`, `wiederherstellen` und
  `fassung_zurueck` brauchen keinen Rumpf (Plan Zeilen 150-153). Eine fremde Seite kann
  damit bei einem angemeldeten Mitarbeiter Fragen löschen, id für id. Die CORS-Liste in
  app.py:33-52 hilft nicht, ein einfaches Formular löst keine Vorabanfrage aus. → R2.
- F3 [P3] (6/10) faq_sync.py:304-305, gelesen. `sync()` ruft `load()` nach dem Verlassen
  von `_lock`, der Plan lädt innerhalb („wie `sync()`“ stimmt also nicht ganz). Läuft ein
  Sync, während jemand speichert, kann das ältere Lese-Ergebnis des Syncs nach dem
  Laden des Editors binden; die Änderung fehlt Leon dann bis zum nächsten Laden, ohne
  Meldung. Kleines Zeitfenster, einzeilige Abhilfe. → R3.
- F4 [P2] (9/10) Plan Zeilen 128-130 gegen die Routentabelle 144-153. „Jetzt laden“ ruft
  `load()`, dafür gibt es keine Route. `/admin/faq-sync` POST ruft `sync()` (holt /Infos,
  80-%-Schwelle), das ist etwas anderes. Korrektur ohne Entscheidung: Route
  `POST /admin/faq/laden`, die `load()` unter `_lock` ruft.

Code-Qualität:
- F5 [P2] (9/10) Plan Zeilen 82 und 103-105. Kategoriewechsel setzt `position` auf
  Maximum + 1, `fassung_zurueck` schreibt `position` nie. „Rückgängig“ nach einem
  versehentlichen Kategoriewechsel holt die Kategorie zurück, die Frage steht dann aber
  am Ende ihrer alten Kategorie statt an ihrem Platz. Ohne Umsortieren in V1 (Zeile 84)
  lässt sich das im Editor nicht mehr richten. → R4.
- F6 [P2] (9/10) faq_sync.py:142 `return {r["frage"]: r["antwort"] for r in rows if
  r["quelle"] in ("website", "intern")}`. Der Schlüsselraum ist website und intern
  zusammen. Die Regel im Plan (Zeile 118) sagt „im selben Reiter … unter den aktiven
  Zeilen“ und lässt offen, ob website-Zeilen und ausgeblendete Zeilen zählen und ob sie
  auch beim Einblenden, Wiederherstellen und bei `fassung_zurueck` läuft. → R5.
- F7 [P2] (8/10) Plan Zeilen 103-105 und 121. Der Wächter ist für „Löschen und
  Ausblenden“ beschrieben. `fassung_zurueck` schreibt `ausgeblendet` aus der Historie und
  kann die letzte sichtbare intern-Zeile ebenfalls verstecken. Dazu fehlt: Der Eintrag
  `hid` muss zu `id` gehören (`faq_history.faq_id == id`), sonst schreibt die Route den
  Inhalt einer fremden Frage in diese Zeile. `quelle` darf sich in `aendern` nie ändern.
  `aendern` und `fassung_zurueck` auf einer gelöschten Zeile ablehnen, sonst verschiebt
  jede Änderung das „Gelöscht am“ (`updated_at`). Der Wächter zählt aus einem frischen
  Lesen unter `_lock`, nie aus der Liste der Seite. Korrekturen ohne Entscheidung.
- F8 [P2] (8/10) faq_sync.py:176 `aktuelle: list[str] = [kontinent]` und :187-189,
  gemessen am CSV-Parse. Eine der beiden Visum-Zeilen hängt an `kategorie = "Afrika"`,
  das nicht in `LAENDER` steht (die andere an Namibia). Der Reiter „Länder“ läuft über
  `LAENDER` (Plan Zeilen 38-39), diese Zeile erscheint also nirgends, und „Der Editor
  zeigt sie als (ausgeblendet)“ (Zeile 138) stimmt für sie nicht. Dass die Zeile wirklich
  in Supabase steht, ist abgeleitet (aus `import_laender` :335-352), nicht nachgesehen.
  → R6.
- F9 [P2] (7/10) Plan Zeilen 224-226, sql/faq.sql:19-25. `faq_history` hat keine Spalte
  `quelle`; der Filter geht über `alt->>quelle` (postgrest baut
  `alt-%3E%3Equelle=eq.land`, gemessen). Wichtiger: Die Historie enthält vermutlich schon
  Einträge je Quelle, aus der Pflege im Table Editor und aus früheren `--replace`-Läufen
  (:320 und :351 „alte Zeilen landen in faq_history“). Dann schlägt die Sperre immer an
  und wird zur Gewohnheit, das Flag mitzugeben. Abgeleitet, Datenbank nicht gelesen. → R7.
- F10 [P3] (8/10) Plan Zeile 113. `_pruefen` kennt keine Obergrenze und keine
  Einzeiligkeit. Jede intern-Antwort steht in jedem Chat-Prompt (Block heute 34 710
  Zeichen, längste Antwort 1 943). Eine eingefügte Seite Text verteuert jeden Chat. Ein
  Zeilenumbruch in `frage` oder `kategorie` zerbricht `**F: …**` bzw. `## …` im Prompt.
  Einzeiligkeit ist eine Korrektur, die Obergrenze eine neue Regel. → R8.
- F11 [P2] (8/10) Plan Zeilen 123-127. Der Abgleich bricht nicht in PostgREST, sondern im
  Browser: `new Date(ts).toISOString()` kürzt auf Millisekunden, danach trifft kein
  Speichern mehr und jedes endet als 409. Anforderung: `updated_at` ist auf der Seite
  eine undurchsichtige Zeichenkette, nach jedem Schreiben aus der Antwort übernommen.
  „0 Zeilen getroffen“ heißt 409 nur, wenn die id existiert, sonst 404. Die Ausweichlösung
  im Plan („Vergleich im Code nach einem Lesen unter `_lock`“) ist schwächer: `_lock` gilt
  nur im Prozess, und nach Entscheidung 3 schreibt ein lokaler Prozess in dieselbe
  Tabelle. `loeschen` und `wiederherstellen` prüfen `updated_at` nicht; das ist vertretbar,
  weil beides umkehrbar ist.
- F12 [P3] (8/10) Plan Zeile 71, gemessen. Gruppiert `render()` nur nach `kategorie`,
  zieht eine neue intern-Frage mit dem Namen einer website-Kategorie („Neue Kategorie …“
  ist Freitext) in den website-Block des Endkunden-Prompts. Abhilfe ohne Entscheidung:
  nach (`quelle`, `kategorie`) gruppieren; für den heutigen Bestand gleich, weil kein
  Name geteilt wird.

Performance:
- F13 [P3] (7/10) Je Speichern etwa fünf bis sechs Aufrufe an Supabase (Prüf-Lesen,
  Schreiben, Historie lesen, `load()` mit rund 435 Zeilen und etwa 170 KB). Bei
  Pflege von Hand unkritisch, kein Chat wartet darauf. Sparsam: ein Lesen aller Zeilen
  für Dubletten, Wächter und höchste Position zusammen.
- F14 [P3] (7/10) Plan Zeilen 156-157 und 165-166. `verlauf` lädt bei jedem Öffnen einer
  Frage und zählt gegen `DASHBOARD_LIMIT` (300 je Stunde, je IP und Route). Ein Büro
  teilt eine IP (rate_limit.py:189-191). Wer alle 396 pflegbaren Fragen einmal durchgeht,
  läuft in das Limit und damit in F1. Abhilfe mit R1: `verlauf` erst beim Aufklappen von
  „Frühere Fassungen“ laden oder ein eigenes Limit.
- F15 [P3] (7/10) sql/faq.sql:19-25. `faq_history` hat keinen Index auf `faq_id`. Bei
  einigen hundert Einträgen egal, DDL ist ohnehin ausgeschlossen. Nur vermerkt.

Zahlen: Der Plan nennt „rund 490 Zeilen“ (Zeile 95). Gemessen: 38 intern + 344 land +
14 ausgeblendete land + 39 website = 435, dazu eine unbekannte Zahl inaktiver
website-Zeilen. Die Folgerung (unter 1000) bleibt richtig.

### Tests

```
CODE (faq_sync.py, dashboard.py)                       ABLÄUFE AUF DER SEITE
render() gruppiert                                     Frage ändern und speichern
  ├── [★★★ TESTED] Bestand unverändert:                  ├── [GAP] [→E2E] Speichern trifft (echtes updated_at)
  │     test_allgemein_md_rundlauf :51,                  ├── [GAP] [→E2E] zwei Reiter, zweiter bekommt 409
  │     test_render_intern_vor_website :58               ├── [GAP] Doppelklick auf „Speichern“
  ├── [GEPLANT ★★] neue Frage in ihrer Kategorie         └── [GAP] Limit erreicht (F1)
  └── [GAP] gleicher Name intern/website (F12)         Rückgängig
_pruefen()                                               ├── [GAP] nach Kategoriewechsel (F5)
  ├── [GEPLANT ★★★] Land, leere Felder, Dublette,        └── [GAP] nach Anlegen (Zeile bleibt unter „Gelöscht“)
  │     website-Zeile, letzte interne Zeile            Löschen und Wiederherstellen
  ├── [GAP] Dublette gegen sichtbare website-Zeile       ├── [GEPLANT] Design-Prüfung an der Testfrage
  ├── [GAP] Wächter über fassung_zurueck (F7)            └── [GAP] Wiederherstellen erzeugt Dublette
  └── [GAP] quelle ändern, gelöschte Zeile ändern      Land außerhalb LAENDER
anlegen()                                                └── [GAP] Zeile „Afrika“ sichtbar? (F8)
  ├── [GEPLANT ★★] leere Antwort nur ausgeblendet      Laden gescheitert
  └── [GAP] Position zählt gelöschte Zeilen mit          └── [GAP] „Jetzt laden“ (Route fehlt, F4)
aendern()
  ├── [GEPLANT ★] Konflikt gegen die Attrappe
  └── [GAP] [→E2E] Abgleich gegen echtes PostgREST
loeschen(), wiederherstellen()   [GEPLANT ★★]
fassung_zurueck()
  ├── [GAP] hid gehört nicht zu id (F7)
  └── [GAP] schreibt nie aktiv, quelle, id
verlauf()            [GEPLANT ★★] ohne inhaltsgleiche
editor_zeilen()      [GEPLANT ★★] website nur sichtbare
Schreiben ok, load() scheitert   [GAP] geladen: false
import --replace Sperre          [GAP] nur von Hand
Routen
  ├── [GEPLANT ★★] 401, 400, website-Zeile 400
  ├── [GAP] 409 und 404
  ├── [GAP] Antwort bei erreichtem Limit (F1)
  └── [GAP] POST ohne JSON-Rumpf (F2)

COVERAGE: 9/32 Pfade geplant oder getestet (28 %)  |  Code: 8/22  |  Abläufe: 1/10
QUALITY: ★★★:2 ★★:6 ★:1  |  GAPS: 22 (3 E2E, 0 Eval)
```
Legende: ★★★ Verhalten mit Rand- und Fehlerfällen | ★★ nur der gute Fall | ★ zählt nicht
als Abdeckung | GEPLANT = im Plan zugesagt, noch nicht geschrieben | [→E2E] = braucht
einen echten Lauf.

- F16 [P2] (10/10) Plan Zeilen 184-185, gemessen. `test_render_gruppiert_wie_bisher`
  vergleicht alt gegen neu am committeten Snapshot. Nach der ersten neuen Frage in einer
  vorhandenen Kategorie und dem Export aus Schritt 5 schreibt die alte Fassung die
  Überschrift zweimal, die neue einmal: alt != neu, der Test wird rot, obwohl nichts kaputt
  ist. Simuliert mit einer Zeile auf Position 38 in „Reisearten“. → R9.
- F17 [P2] (8/10) tests/test_faq_sync.py:88-89 `def __getattr__(self, name): return
  lambda *a, **k: self`. Die Attrappe ignoriert jeden Filter. `test_aendern_konflikt`
  beweist damit nur, dass leere Daten zu 409 werden, nicht dass der Filter auf
  `updated_at` trifft. Pflichtprobe in Schritt 3 an der ausgeblendeten Testfrage, über die
  echte Seite: speichern (200), in einem zweiten Reiter mit altem Stand speichern (409),
  danach wieder speichern (200). Das ersetzt „Zu prüfen beim Bau“ durch einen benannten
  Schritt. Kein Mock steht für PostgREST.
- F18 [P3] (8/10) TODOS.md:50-56. Routen-Tests brauchen `import app`, das beim Import
  echte Supabase-Arbeit macht (so wie tests/test_general.py:78 heute schon). Die
  Routen-Tests müssen `faq_sync._supabase` ersetzen, sonst schreibt ein Test in die
  echte Tabelle.

Regressionsregel: Das einzige bestehende Verhalten in Gefahr ist die Ausgabe von
`render()`. Es ist durch `test_allgemein_md_rundlauf` gedeckt (mit der neuen Fassung
gemessen: grün), dazu der einmalige Vergleich am Snapshot vor Push 1. Die Form des
dauerhaften Tests ist R9.

Evals: Kein Prompt-Text, keine Tool-Beschreibung und kein Modell ändert sich. Nach Push 1
genügt der Vergleich der beiden Blöcke; ein Eval-Lauf ist nicht nötig. Nach Schritt 5
laufen `test_allgemeine_fragen_gibt_es_noch` und `test_laender_fragen_gibt_es_noch`
gegen den neuen Snapshot (im Plan schon vorgesehen).

### Failure modes

| Pfad | Was in Produktion schiefgeht | Test | Behandlung | Sichtbar? |
|---|---|---|---|---|
| Speichern am Limit | 200 mit SSE-Rumpf, nichts geschrieben | nein | nein | nein, still: kritische Lücke (F1) |
| Speichern, Supabase weg | Ausnahme in der Route | nein | Plan nennt nur `geladen: false` | 500 ohne Satz; Route braucht try/except mit Fehlersatz |
| Schreiben ok, `load()` scheitert | Leon bleibt alt | nein | `geladen: false`, „Jetzt laden“ | ja, sobald die Route existiert (F4) |
| Zwei Mitarbeiter, eine Frage | zweiter überschreibt | Attrappe | `updated_at` | ja (409), wenn F11 beachtet wird |
| Sync läuft während Speichern | älteres Laden bindet zuletzt | nein | nein | nein, still, heilt beim nächsten Laden (F3) |
| Deploy, `load()` scheitert beim Start | Leon fällt auf den Snapshot zurück, gelöschte Fragen sind wieder da | nein | Status unter /admin | nur für den, der /admin öffnet |
| Lokaler Prozess schreibt | Produktion lädt erst um 02:05 | nein | Testfrage ist ausgeblendet | bewusst in Kauf genommen (Entscheidung 3) |
| Fremde Seite löst POST aus | Fragen gelöscht | nein | nein | erst, wenn Leon etwas nicht mehr weiß (F2) |

Kritische Lücken: 1 (F1).

### Outside Voice

Codex ist auf diesem Konto nicht nutzbar (Modell nicht unterstützt) und wurde nicht
versucht. Ersatz: ein zweiter, gegnerischer Durchgang desselben Prüfers. Das ist keine
unabhängige Zweitmeinung und gibt keine Outside-Abdeckung.

- OV1 Geteilte Zugangsdaten (Entscheidung 2, steht): Wer FAQs pflegt, kann damit auch
  die Chat-Verläufe unter /dashboard lesen und die bezahlten Läufe auslösen
  (dashboard.py:1157, :1159). Der Vorgänger nannte genau das als Grund für einen eigenen
  Editor (faq-supabase-sync.md:232-233). Risiko vermerkt, nicht wieder geöffnet.
- OV2 Der Snapshot veraltet jetzt täglich statt selten. Fällt `load()` nach einem Deploy
  aus, antwortet Leon mit dem Stand des letzten Exports. Schritt 5 exportiert einmal.
  → R10 (TODO).
- OV3 „Rückgängig“ nach „Neue Frage“ lässt eine Zeile unter „Gelöscht“ zurück, die im
  Editor niemand entfernen kann. Auf Dauer sammelt sich dort Abfall. Vom Plan in Kauf
  genommen (Testfrage), hier nur als Folge benannt.
- OV4 `historie_id` als „neuester Eintrag zu dieser faq_id“ stimmt nur, solange niemand
  gleichzeitig im Table Editor schreibt. Selten; die `updated_at`-Prüfung in
  `fassung_zurueck` fängt es ab.
- OV5 `anlegen` nimmt `ausgeblendet` nur für die Design-Prüfung an. Das ist eine Naht
  ohne Aufrufer in der Seite, aber sie schützt die echte Tabelle. In Ordnung.
- OV6 Beim Deploy laufen kurz zwei Container. Ein Speichern im alten erreicht den neuen
  nicht, bis dort wieder geladen wird. Sekundenfenster, vermerkt.
- OV7 `marked` und `DOMPurify` von cdnjs auf einer Seite hinter Basic Auth: mit fester
  Version und `integrity` in Ordnung. Alle anderen Texte gehen über `esc()` aus core.js;
  das beim Umbau auf `fetch` beibehalten.

## Reality rows

| Prämisse | Beleg | Urteil |
|---|---|---|
| Trigger bei UPDATE und DELETE, nicht bei INSERT, kein „wer“ | sql/faq.sql:27-38 | stimmt |
| `load()` liest nur aktive, nicht ausgeblendete Zeilen; ohne intern scheitert es | faq_sync.py:239-246 | stimmt |
| `render()` schreibt die Überschrift bei jedem Wechsel | faq_sync.py:125-128 | stimmt |
| intern 0..37, 11 Kategorien; land 2000..2343 | Snapshot, gemessen | stimmt |
| Routen als Tupel, `auth_required`, `ADMIN_LIMIT` 30 je Stunde | dashboard.py:1147-1175, rate_limit.py:51 | stimmt |
| Ein Worker | Dockerfile:35 | stimmt |
| `id` ist `generated always` | sql/faq.sql:6 | stimmt |
| Der Client kann `overriding system value` nicht ausdrücken | kein Beleg | unverified (ohne Folge) |
| 73 Länder, 17 ohne Frage, Italien leer | faq_sync.py:43-57, Snapshot | stimmt |
| Ausgabe von `render()` bleibt zeichengleich | gemessen | stimmt |
| Sync fasst `aktiv` nur bei website an | faq_sync.py:278, :290 | stimmt |
| Rund 490 Zeilen | gemessen 435 | ungenau, Folgerung hält |
| Editor zeigt die Visum-Zeilen als ausgeblendet | faq_sync.py:176 | falsch für die Zeile „Afrika“ (F8) |
| „Jetzt laden“ ruft `load()` | Routentabelle des Plans | Route fehlt (F4) |
| Schreibende Funktionen sperren „wie `sync()`“ | faq_sync.py:304-305 | ungenau (F3) |
| `eq` auf `updated_at` trifft über PostgREST | Anfrage gebaut, nicht gesendet | unverified bis zur Probe F17 |
| Sperre „sobald `faq_history` Einträge zu dieser Quelle hat“ | sql/faq.sql:19-25 | unverified, Inhalt der Historie nicht gelesen (F9) |

REALITY: premise-table finding 17 Prämissen, 2 falsch oder fehlend, unverified=3 docs/designs/admin-faq-editor.md:95
REALITY: already-done pass nichts davon gebaut, kein Treffer für editor_zeilen oder FAQ_EDIT_LIMIT faq_sync.py:1
REALITY: surface-check finding Route für „Jetzt laden“ fehlt; `--pflege-verwerfen` ist ein neues Flag dashboard.py:1147
REALITY: binding-decisions pass Entscheidungen 1 bis 3 des Owners und „Zurückgestellt“ unangetastet docs/designs/admin-faq-editor.md:235
REALITY: numbers finding 490 Zeilen genannt, 435 gemessen faqs/snapshot.json:1
REALITY: deferred-asks pass acht zurückgestellte Punkte mit Grund gelistet docs/designs/admin-faq-editor.md:250
REALITY: pr-count pass drei Pushes auf main wie im Plan, keine ship-policy.json, je Push ein Deploy docs/designs/admin-faq-editor.md:218
REALITY: transaction-semantics finding UPDATE plus Trigger ist ein Statement; Schreiben und load() sind es nicht, der Plan fängt das mit `geladen: false` ab; Prüfen und Schreiben sind nur durch `_lock` im Prozess verbunden sql/faq.sql:37

## Decision ledger

Alle Punkte vom Owner mit A beantwortet (2026-10-11, „all A“).

### R1: Antwort bei erreichtem Limit
Finding: F1 [P1] (9/10) rate_limit.py:159-170, F14. Plan baseline: nicht behandelt.
Runtime evidence: 200 `text/event-stream` für jede Route außer `kunde_auth` und
`agentur_auth` (gelesen).
| Choice | Heute | A | B | C |
|---|---|---|---|---|
| Antwort auf einer gebremsten /admin- oder /dashboard-Route | 200 SSE | 429 JSON `{"error": …}` für alles außer `/chat/stream`; die Seite zeigt „Zu viele Änderungen, bitte kurz warten“ | Handler bleibt; faq.html prüft `Content-Type` und behandelt SSE als Fehler | nichts tun |
Options: A) Handler antwortet 429 JSON außerhalb des Chats (recommended) / B) Nur die
Seite prüft / C) Nichts tun
Empfehlung: A. Eine Stelle, behebt denselben Fehler für die vorhandenen /admin-Knöpfe und
das Dashboard. Ändert Verhalten bestehender Routen, deshalb eine Frage. Aufwand: Mensch
etwa 1 Stunde, CC etwa 10 Minuten.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R2: Schutz gegen fremde POSTs
Finding: F2 [P2] (7/10) dashboard.py:898-905.
| Choice | Heute | A | B |
|---|---|---|---|
| Schreibende Editor-Routen | nur Basic Auth | jede verlangt einen JSON-Rumpf (`request.is_json`, sonst 400); die Seite schickt immer `{}` oder Daten | nichts tun |
Options: A) JSON-Rumpf verlangen (recommended) / B) Nichts tun
Empfehlung: A. Drei Zeilen, kein Token, keine Sitzung. Ein fremdes Formular kann kein
`application/json` senden, und ein fremdes `fetch` scheitert an der Vorabanfrage.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R3: `load()` immer unter `_lock`
Finding: F3 [P3] (6/10) faq_sync.py:304-305.
| Choice | Heute | A | B |
|---|---|---|---|
| Wo `sync()` lädt | nach der Sperre | innerhalb der Sperre, wie der Editor | bleibt |
Options: A) `sync()` lädt innerhalb der Sperre (recommended) / B) Bleibt
Empfehlung: A. Eine eingerückte Zeile; danach gibt es genau eine Reihenfolge von
Schreiben und Laden. Ändert `sync()`, deshalb eine Frage.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R4: „Rückgängig“ und Position
Finding: F5 [P2] (9/10) Plan Zeilen 82, 103-105.
| Choice | Plan | A | B |
|---|---|---|---|
| `fassung_zurueck` und `position` | nie geschrieben | `position` aus dem Historien-Eintrag mitschreiben, wenn sich die Kategorie dabei ändert | bleibt; die Frage landet am Ende der alten Kategorie |
Options: A) Position mit zurückholen (recommended) / B) Bleibt
Empfehlung: A. Der alte Platz ist frei, weil neue Positionen immer Maximum + 1 sind; bei
Gleichstand entscheidet `id`. Ohne A ist ein Fehlklick nicht mehr zu richten.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R5: Wogegen „doppelt“ geprüft wird
Finding: F6 [P2] (9/10) faq_sync.py:142.
| Choice | Plan | A | B |
|---|---|---|---|
| Vergleichsmenge | „aktive Zeilen im selben Reiter“ | sichtbare Zeilen (aktiv, nicht ausgeblendet) im selben Schlüsselraum: intern gegen intern und website, land gegen dasselbe Land; geprüft bei Anlegen, Ändern, Einblenden, Wiederherstellen, `fassung_zurueck` | wie im Plan, website zählt nicht, ausgeblendete zählen |
Options: A) Sichtbare Zeilen im Schlüsselraum (recommended) / B) Wie im Plan
Empfehlung: A. Genau die Zeilen, die sich in `general_faq_data` und `laender_faq_data`
überschreiben. Bestand: keine Dublette (gemessen), niemand wird ausgesperrt.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R6: land-Zeilen mit einer Kategorie außerhalb von `LAENDER`
Finding: F8 [P2] (8/10) faq_sync.py:176, :187-189.
| Choice | Plan | A | B | C |
|---|---|---|---|---|
| Zeile „Afrika“ und spätere Tippfehler aus dem Table Editor | unsichtbar | eigene Gruppe am Ende des Reiters „Länder“; `_pruefen` verlangt ein Land aus `LAENDER` nur, wenn die Kategorie gesetzt oder geändert wird | Owner ordnet die Zeile vor Push 3 im Table Editor einem Land zu oder löscht sie | unsichtbar lassen |
Options: A) Eigene Gruppe am Ende (recommended) / B) Owner räumt von Hand auf / C)
Unsichtbar lassen
Empfehlung: A. Deckt auch `unbekannte_laender` aus dem Lade-Status ab, und nichts in
Supabase bleibt für Mitarbeiter verborgen. Der Name der Gruppe ist eine Frage für die
Design-Prüfung.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R7: Sperre für `--replace`
Finding: F9 [P2] (7/10) Plan Zeilen 224-226.
| Choice | Plan | A | B |
|---|---|---|---|
| Wann `import --replace` und `import-laender --replace` abbrechen | sobald `faq_history` Einträge zu dieser Quelle hat | immer, außer mit `--pflege-verwerfen`; keine Abfrage | wie im Plan, Filter über `alt->>quelle` |
Options: A) Immer abbrechen ohne das Flag (recommended) / B) Wie im Plan
Empfehlung: A. Sobald der Editor läuft, verwirft jedes Ersetzen Pflege. Die Historie hat
vermutlich schon Einträge, dann ist B dasselbe wie A mit einer Abfrage mehr. Ein Ersetzen
vergibt neue ids, alle „Früheren Fassungen“ hängen danach an keiner Frage mehr; das
gehört in die Abbruch-Meldung.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R8: Obergrenze für Texte
Finding: F10 [P3] (8/10) Plan Zeile 113.
| Choice | Plan | A | B |
|---|---|---|---|
| Länge von `frage` und `antwort` | unbegrenzt | `antwort` höchstens 4000 Zeichen, `frage` höchstens 300, Fehlersatz am Feld | unbegrenzt |
Options: A) Obergrenzen (recommended) / B) Unbegrenzt
Empfehlung: A. Längste Antwort heute 1 943 Zeichen, die Grenze stört niemanden und hält
den Prompt jedes Chats klein. Einzeiligkeit von `frage` und `kategorie` gilt unabhängig
davon als Korrektur.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R9: Dauerhafter Test für `render()`
Finding: F16 [P2] (10/10) Plan Zeilen 184-185.
| Choice | Plan | A | B |
|---|---|---|---|
| Regressionsschutz | alt gegen neu am committeten Snapshot, als Test | einmaliger Vergleich am Snapshot vor Push 1 (nicht committet); dauerhaft bleiben `test_allgemein_md_rundlauf`, `test_render_intern_vor_website` und der neue `test_neue_frage_landet_in_ihrer_kategorie` | Test wie im Plan, aber gegen eine feste Eingabe im Test statt gegen den Snapshot |
Options: A) Einmal vergleichen, vorhandene Tests tragen (recommended) / B) Feste Eingabe
Empfehlung: A. Der Vergleich ist für HEAD da424f9 schon gelaufen und gleich. Die alte
Fassung als Hilfsfunktion im Test wäre Code, der nur für diesen Test lebt und ab Schritt 5
falsch liegt.
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

### R10: TODO „Snapshot nach Pflege nachziehen“
Finding: OV2.
What: regelmäßig `python faq_sync.py export` und Snapshot committen, oder ein Hinweis
unter /admin, wenn Supabase und Snapshot auseinanderliegen. Why: Der Snapshot ist der
Rückfall bei einem gescheiterten `load()` und die Grundlage der Evals. Pros: Rückfall und
Evals bleiben nah am echten Stand. Cons: ein wiederkehrender Handgriff oder ein weiterer
Status. Context: Plan Schritt 5 exportiert genau einmal. Depends on: Push 3.
Options: A) In TODOS.md aufnehmen (recommended) / B) Weglassen / C) Jetzt mitbauen
State: approved
Actual answer: A (Owner, 2026-10-11, „all A“)
Accepted scope: Option A, im Plan oben eingearbeitet

Approval readiness: PASS (R1 bis R10 approved, Antworten vom Owner).

## Review-Abschnitte

NOT in scope (aus dieser Prüfung, zusätzlich zum Plan): eigene Zugangsdaten für den
Editor (Entscheidung 2), Testtabelle (Entscheidung 3), endgültiges Löschen im Editor,
Index auf `faq_history.faq_id`, Entkopplung von `import app` und Supabase in Tests
(TODOS.md:50).

What already exists: `_lock` und `status` (faq_sync.py:60-61), `load()` (:232), `LAENDER`
(:43), `auth_required` (dashboard.py:895), `routes` (:1147), die Limit-Schleife
(app.py:391-398), der Trigger (sql/faq.sql:27-38), `esc`, `analyze`, `wordDiff`, `gate`
im Lab. Der Plan baut auf allem auf und erfindet nichts doppelt.

Ablauf eines Schreibvorgangs mit den Empfehlungen:
```
POST (JSON, R2) -> auth_required -> Limit (429 JSON, R1) -> Route
   -> _lock
        lesen: Zeilen der Quelle (Dubletten R5, Wächter, höchste Position)
        _pruefen  -- Fehler --> 400 mit Satz
        UPDATE ... where id = ? and updated_at = ?   -- 0 Zeilen --> 409 oder 404
        Historien-Eintrag lesen (historie_id)
        load()  -- Fehler --> geladen: false, „Jetzt laden“ (F4)
   <- {ok, zeile (neues updated_at), geladen, historie_id}
```

Parallelisierung: Sequential implementation, no parallelization opportunity. Push 1, 2 und
3 hängen aneinander, und Server und Seite teilen sich die Antwortform.

## Implementation Tasks
Aus den Befunden oben. Alle R sind beantwortet, die Aufgaben gelten.

- [ ] **T1 (P1, Mensch: ~1h / CC: ~10min)** rate_limit.py: 429 JSON außerhalb von `/chat/stream`
  - Surfaced by: F1
  - Files: rate_limit.py, static/admin/faq.html
  - Verify: Test mit kleinem Limit auf einer Editor-Route, Antwort ist 429 JSON
- [ ] **T2 (P2, Mensch: ~30min / CC: ~5min)** dashboard.py: JSON-Rumpf auf jeder schreibenden Editor-Route
  - Surfaced by: F2
  - Files: dashboard.py, static/admin/faq.html
  - Verify: Routen-Test, POST ohne `Content-Type: application/json` ergibt 400
- [ ] **T3 (P2, Mensch: ~30min / CC: ~5min)** Route `POST /admin/faq/laden`, `load()` unter `_lock`
  - Surfaced by: F4, F3
  - Files: dashboard.py, faq_sync.py
  - Verify: Test mit Attrappe, Route liefert den Lade-Status
- [ ] **T4 (P2, Mensch: ~1h / CC: ~10min)** `fassung_zurueck`: `faq_id == id`, Wächter, gelöschte Zeile ablehnen, Position (R4)
  - Surfaced by: F5, F7
  - Files: faq_sync.py, tests/test_faq_sync.py
  - Verify: `test_fassung_zurueck_fremde_historie`, `test_fassung_zurueck_letzte_interne`
- [ ] **T5 (P2, Mensch: ~1h / CC: ~10min)** `_pruefen`: Dubletten nach R5, `quelle` fest, einzeilig, Obergrenzen (R8)
  - Surfaced by: F6, F7, F10
  - Files: faq_sync.py, tests/test_faq_sync.py
  - Verify: `test_pruefen_*` um website-Dublette und Wiederherstellen erweitert
- [ ] **T6 (P2, Mensch: ~1h / CC: ~10min)** Editor zeigt land-Zeilen außerhalb von `LAENDER`
  - Surfaced by: F8
  - Files: faq_sync.py, static/admin/faq.html
  - Verify: Design-Prüfung, Zustand „Länder“ zeigt die Zeile „Afrika“
- [ ] **T7 (P2, Mensch: ~15min / CC: ~5min)** `updated_at` als Zeichenkette, 409 gegen 404, Pflichtprobe an der Testfrage
  - Surfaced by: F11, F17
  - Files: faq_sync.py, static/admin/faq.html
  - Verify: Schritt 3, zwei Reiter, 200 dann 409 dann 200
- [ ] **T8 (P3, Mensch: ~15min / CC: ~5min)** `render()` gruppiert nach (`quelle`, `kategorie`); Vergleich am Snapshot vor Push 1
  - Surfaced by: F12, F16
  - Files: faq_sync.py, tests/test_faq_sync.py
  - Verify: `pytest tests/test_faq_sync.py`, `python faq_sync.py export` ohne Diff
- [ ] **T9 (P3, Mensch: ~15min / CC: ~5min)** Sperre für `--replace` nach R7
  - Surfaced by: F9
  - Files: faq_sync.py
  - Verify: von Hand, Aufruf ohne Flag bricht mit Meldung ab
- [ ] **T10 (P3, Mensch: ~15min / CC: ~5min)** `verlauf` erst beim Aufklappen laden oder eigenes Limit; Routen-Tests ersetzen `faq_sync._supabase`
  - Surfaced by: F14, F18
  - Files: static/admin/faq.html, rate_limit.py, tests/test_faq_sync.py
  - Verify: Durchklicken aller Fragen eines Reiters ohne Limit-Antwort

Unresolved decisions: keine.

Completion summary:
- Step 0: Scope Challenge: scope accepted as-is
- Reality rows: 8/8 Zeilen ausgegeben, 3 unverified Prämissen
- Architecture Review: 4 issues found
- Code Quality Review: 8 issues found
- Test Review: diagram produced, 22 gaps identified
- Performance Review: 3 issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 1 item (R10) aufgenommen
- Failure modes: 1 critical gap flagged (F1)
- Unresolved decisions: 0 (R1 bis R10 approved)
- Outside voice: codex unavailable (Modell nicht unterstützt, nicht versucht); zweiter Durchgang desselben Prüfers, 7 Punkte, keine Outside-Abdeckung
- Parallelization: 1 lane, 0 parallel / 1 sequential
- Lake Score: 10/10 Empfehlungen angenommen

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | - |
| Outside Review | codex, plan-review | Independent 2nd opinion | 1 | unavailable (zweiter Durchgang desselben Prüfers, keine Outside-Abdeckung) | 7 Punkte, 1 → R10 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 37 issues, 1 critical gap, alle in den Plan eingearbeitet |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | - |

- **OUTSIDE COVERAGE:** codex, plan-review, unavailable (Modell für das Konto nicht nutzbar, nicht versucht). Ersatz war ein zweiter Durchgang desselben Prüfers mit 7 Punkten; das zählt nicht als Outside-Abdeckung.
- **VERDICT:** ENG CLEARED. R1 bis R10 vom Owner mit A beantwortet und eingearbeitet, die kritische Lücke F1 ist durch R1 geschlossen. Offen bleibt die Pflichtprobe auf `updated_at` beim Bau; eine unabhängige Outside-Prüfung gab es nicht.

NO UNRESOLVED DECISIONS
