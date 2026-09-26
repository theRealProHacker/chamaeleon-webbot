# Kundenfeedback September 2026 — Plan

Status: **Eng-Review durch** (2026-09-21, 20 Entscheidungen, siehe „Review-Entscheidungen" und Report am Ende). Nichts davon ist implementiert,
committet oder gepusht. Push auf `main` = Deploy und passiert erst auf
ausdrückliches Wort des Owners.

Aufbau: Befunde → Änderungen A–F (das *Was* und *Warum*) → **Ausführung**
(das *Wer* und *Wo*: Arbeitspakete für parallele Subagenten, mit
Dateibesitz, Vertrag und Abnahme). Wer nur reviewen will, liest bis F und die
„Offenen Punkte"; wer ausführt, liest sein Paket plus die dort genannten
Abschnitte.

## Auslöser

14 Feedbackpunkte zum öffentlichen Chat (Leon), überwiegend Tansania und
Buchungsfragen. Die Punktnummern unten sind die des Feedbacks.

## Die 14 Punkte haben vier Ursachen (plus zwei Einzelthemen)

| Ursache | Punkte | Änderung |
|---|---|---|
| Leon prüft nicht über alle Reisen eines Landes, sondern rät | 1, 10, (9) | A — Website-Tool für mehrere Seiten |
| FAQ-Text landet ungefragt im Prompt und schlägt die Webseite | 5, 12, 13 | B — FAQ-Nutzung im Prompt |
| Fachwissen fehlt oder ist falsch | 2, 4, 6, 7, 8, 9, 11 | C — FAQ-Einträge |
| Nichts davon ist abgesichert | 10, 12, 13 | D — Evals |
| Link-Regeln widersprechen sich | 14 | E |
| Elf Einzelbefunde aus Chats vom 26./27.07. | 3 | F |

---

## Befunde (gemessen 2026-09-20 über `chamaeleon_website_tool_base`)

**Sansibar (Punkt 10).** Alle sechs Tansania-Hauptreisen nennen „Sansibar" —
die Erwähnung allein unterscheidet also nichts. Entscheidend ist, *wo*:

    Cheetah 55×, Baobab-ALL 42×, Ngorongoro 42×, Serengeti 33×  → im Reiseverlauf
    Ruaha 6×, Mara-Fluss 6×  → alle sechs bei 93 % Seitentiefe, im Teaser
                               „Verlängerungen" — NICHT im Reiseverlauf

Die richtige Antwort auf „Tansania ohne Sansibar" wäre also Ruaha oder
Mara-Fluss gewesen. Leon hat Cheetah genannt, ohne eine Seite zu öffnen.

**Seitenaufbau.** Jede Reiseseite trägt dieselben Markdown-Überschriften in
derselben Reihenfolge: Reiseverlauf (9 %), Reisedetails (34 %), Leistungen
(75 %), Termine & Preise, Unterkünfte (79 %), Verlängerungen (91 %). Eine Seite
hat ~52.000 Zeichen; der Reiseverlauf allein ~13.000.

**Airline (Punkt 12).** Steht auf jeder geprüften Seite in den Leistungen:
Cheetah, Ngorongoro, Kasbah → Discover Airlines; Baobab, Serengeti, Ruaha,
Mara-Fluss → Ethiopian Airlines; Gorilla → Brussels Airlines. Leons Antwort
(„Rechnung über unseren Unterlagenlink") ist wörtlich `faqs/allgemein.md:46`
„Wo finde ich meine Flugzeiten?" — die FAQ hat die Webseite geschlagen.

**Ruanda (Punkt 13).** „Ruanda" steht in keiner FAQ, nicht in der Sitemap,
nicht in den Overrides. Die Gorilla-Seite nennt es dreimal, nur als Grenze und
als Zwischenstopp in Kigali. „Kombiniert Uganda und Ruanda" ist frei erfunden.

**Ramadan (Punkt 5).** `agent.py:205-209` hängt die komplette Länder-FAQ in
den Prompt, sobald der Ländername im Verlauf vorkommt; der Prompt sagt dazu
„nutze diese FAQs … als Inspiration" und „eigentlich immer nutzen". Genau das
hat Leon getan: Ramadan ist Marokko-FAQ Nr. 1 (`FAQ_Afrika.csv:104`).

**Reservierung (Punkt 6).** Die Reiseseite selbst trägt die Schaltflächen
„Unverbindlich reservieren", „Reservieren", „Vormerken". Leon hat bestritten,
was auf der Seite steht, auf der der Kunde gerade war.

**„Ab 2 Personen" (Punkt 2).** In keiner FAQ und auf keiner geprüften Seite zu
finden (dort nur Mindestteilnehmerzahlen für Ausflüge, z. B. Ballonfahrt 6).
Ohne den Chatverlauf ist die Quelle nicht zu bestimmen — vermutlich geraten.

---

## Änderung A — Website-Tool: mehrere Seiten, ein Abschnitt (Punkte 1, 10)

`chamaeleon_website_tool(url_path: str)` wird zu

    chamaeleon_website_tool(url_paths: list[str], abschnitt: str = "")

- `url_paths`: 1 bis 8 Pfade, parallel geholt (`ThreadPoolExecutor`, Vorbild
  `REISEINFO_FETCH_PARALLEL`). Ergebnis: die Seiten hintereinander, jede unter
  `# <Pfad>`. Ein Fehlschlag betrifft nur seine Seite.
- `abschnitt`: leer = ganze Seite wie heute. Sonst einer von `uebersicht`
  (Seitenanfang bis „Reiseverlauf": Titel mit Dauer, Highlights), `reiseverlauf`,
  `reisedetails`, `leistungen`, `unterkuenfte`, `zusatzprogramme` — dieselben
  Namen wie die Anker in der Tool-Beschreibung. Geschnitten wird an den
  Überschriften; fehlt die Überschrift, kommt die ganze Seite.
- Warum der Abschnitt dazugehört: sechs ganze Seiten sind ~310.000 Zeichen,
  sechs Reiseverläufe ~78.000. Ohne ihn ist der Mehrfachabruf zu teuer und zu
  langsam, um zur Regel zu werden.
- Der Termine-Anhang (`get_termine_markdown`) nur bei ganzer Seite.
- Tool-Historie: der Store schlüsselt auf `(name, args)` — eine Liste als
  Argument ändert daran nichts. Vor dem Bau gegen `docs/tool-history-plan.md`
  §3 prüfen, falls der zuerst landet.

Prompt (neuer Block „Reisen vergleichen", plus ein Beispiel bei den
Stil-Beispielen):

- Fragt jemand nach einer Reise MIT oder OHNE etwas Bestimmtem (ohne Sansibar,
  ohne Badeaufenthalt, reine Safari, mit Gorillas …), dann ruf ALLE Reisen des
  gewünschten Landes in einem Aufruf mit `abschnitt="reiseverlauf"` ab und
  empfiehl nur, was der Reiseverlauf belegt. Verlängerungen zählen nicht zur
  Reise.
- Bleib im gewünschten Land. Wer Tansania oder Kenia sagt, bekommt kein
  Namibia.
- Widerspricht der Kunde, dann Seite erneut abrufen und nach dem Ergebnis
  richten — gleiche Regel wie bei den Terminen.
- Beispiel wörtlich aus dem Feedback: Tansania ohne Sansibar → alle
  Tansania-Reisen abrufen, Reiseverlauf auf Sansibar prüfen.

## Änderung B — FAQs nur auf Frage (Punkte 5, 12, 13)

Prompt, wie im Feedback vorgegeben:

- FAQs beantworten eine gestellte Frage. Ungefragt wird nichts daraus erzählt;
  bei „erzähl mir was über Marokko / diese Reise" kommen die Infos von der
  Webseite.
- Die Worte „FAQ" und „Wissensbasis" kommen in einer Antwort nie vor.
- „Als Inspiration" und „eigentlich immer nutzen" entfallen.
- Flüge: Airline und Buchungsklasse stehen in den Leistungen der Reiseseite —
  dort nachsehen (`abschnitt="leistungen"`). Die FAQ „Wo finde ich meine
  Flugzeiten?" gilt nur für bereits gebuchte Reisen; das kommt als Halbsatz in
  die FAQ selbst.
- Nicht angebotene Länder: steht ein Land nicht in der Sitemap, gibt es dort
  keine Reise. Klar sagen, ein Nachbarland nur als Alternative anbieten, nie
  als „Kombination" ausgeben.

**Offene Entscheidung B1:** Die Auto-Injektion (`agent.py:205`) bleibt zunächst
stehen — das Feedback verlangt eine Prompt-Regel, keinen Umbau. Bleibt der
Marokko-Eval danach rot, ist der nächste Schritt, die Injektion zu streichen
und die Länder-FAQ nur noch über `country_faq_tool` zu holen. Das spart
nebenbei Prompt-Tokens, ändert aber Verhalten in jedem Länder-Chat — deshalb
nicht ohne dein Okay.

## Änderung C — Fachwissen (Punkte 2, 4, 6, 7, 8, 9, 11)

Neuer Abschnitt in `faqs/allgemein.md`, „Buchen und Reservieren":

| Punkt | Eintrag |
|---|---|
| 2 | Jede Reise findet statt, auch ab 1 Person. |
| 6 | Eine Reservierung gilt 7 Tage und wird danach automatisch verbindlich. |
| 4 | Reservieren geht direkt auf der Website (#termine). Soll die Buchung an ein Reisebüro gehen: Erlebnisberater kontaktieren, er überträgt sie. |
| 7 | Privatreisen: Erlebnisberater. |
| 11 | Just4You (diese Reise als eigene Reise): Erlebnisberater. |

`faqs/FAQ_Afrika.csv`, Tansania:

| Punkt | Eintrag |
|---|---|
| 8 | Dreibettzimmer auf Tansania-Reisen möglich, ausgenommen die Beachfront Villen im AQUA. |
| 9 | Zusatztage in der Hatari Lodge sind möglich — Anfrage über den Erlebnisberater. |

Bei 4, 7, 9, 11 nennt Leon den Erlebnisberater der Seite; abseits einer
Reiseseite greift das bestehende `erlebnisberater_tool`.

**Entschieden (Owner, 2026-09-20):**
- Punkt 6 gilt auch für Agenturen: `faqs/agentur.md` §6 „Wie kann ich eine
  Buchung oder Option anlegen?" bekommt denselben Satz wie die Option —
  Reservierungen werden 7 Tage gehalten und danach automatisch zur
  Festbuchung. Damit ist die TODOS-Frage „Option vs. Reservierung" erledigt
  und wird dort abgehakt. Der Just4You-Satz in §10 bleibt unberührt.
- Punkt 2 gilt ausnahmslos: garantierte Durchführung, jede Reise findet
  statt, auch ab 1 Person. (Deckt sich mit Katharinas Mail, F2–F4.) Die
  Mindestteilnehmerzahlen auf den Seiten betreffen nur Ausflüge — das kommt
  als Halbsatz dazu, damit Leon beides nicht verwechselt.
- Punkt 4: „Reisebüro finden" bleibt verlinkt, zusätzlich zur richtigen
  Auskunft (online reservieren, Übertragung über den Erlebnisberater).
- Punkt 9, zweiter Teil (Premium Economy bei Baobab): unklar, ob falsch.
  Keine Änderung; der Airline-Eval deckt die Leistungen ohnehin ab.

## Änderung D — Evals

Neue Datei `tests/test_kundenfeedback_eval.py`, Aufbau und Helfer wie
`tests/test_agentur_faq.py` (muss/darf_nicht je Fall, live, nie in der
Standard-Suite): `RUN_KUNDENFEEDBACK_EVAL=1`. Geprüft wird die Antwort, nie
der Prompt-Text.

1. **Länder-Filter (Punkt 10).** „Tansania-Reise ohne Sansibar" → muss Ruaha
   oder Mara-Fluss; darf nicht Cheetah, Baobab, Serengeti, Ngorongoro, Namibia,
   Etosha. Zweiter Fall zweizügig: nach der Antwort „Die ist auch mit
   Sansibar" → darf nicht umfallen. Die Erwartung wird zur Laufzeit aus den
   Seiten abgeleitet (Sansibar im Reiseverlauf ja/nein), damit der Eval einen
   Programmwechsel überlebt.
2. **Airline, 20+ Länder (Punkt 12).** Je Land eine Reise aus `trip_sites`.
   Die erwartete Airline liest der Test selbst aus den Leistungen der Seite;
   Frage „Mit welcher Airline fliegt man nach <Land>?" auf der Reiseseite →
   muss die Airline, darf nicht „Rechnung", „Unterlagenlink". Länder ohne
   erkennbare Airline werden übersprungen und gezählt; unter 20 bleibt rot.
3. **Nicht angebotene Länder (Punkt 13).** Kandidatenliste (Ruanda, Burundi,
   Nigeria, Afghanistan, Nordkorea, Venezuela, Pakistan, Ukraine …), zur
   Laufzeit gegen `all_countries` gefiltert. Muss: klare Absage. Darf nicht:
   „FAQ", „und <Land>", „nach <Land> führt".
4. **Ungefragte FAQ (Punkt 5).** „Ein paar Infos über Marokko und zu dieser
   Reise" auf `/Afrika/Marokko/Kasbah` → darf nicht „Ramadan", „FAQ".
5. **Fachwissen (Punkte 2, 4, 6, 7, 9, 11).** Je ein Fall mit der Frage aus
   dem Feedback. 4/7/9/11: muss Erlebnisberat-. 6: muss „7 Tage". 4 und 6:
   darf nicht „nicht möglich", „gibt es … nicht".

Bekannte Grenze: ab ~30 Gemini-Aufrufen am Stück kippt das Modell in leere
Antworten (TODOS, 2026-09-18). Die Datei hat ~45 Fälle — also blockweise
laufen lassen (`-k airline` usw.); rote Fälle erst einzeln wiederholen.

## E — Punkt 14: Reiseunterlagen-Link in MeinChamäleon

Befund (Owner, 2026-09-20): Leon gibt teilweise nur `/MeinChamaeleon` aus
statt des ganzen Links zu den Reiseunterlagen.

Ursache im Prompt, zwei Regeln ziehen gegeneinander:
- allgemein: „Verwende dafür einfach die relativen URLs, z.B. /Impressum";
- Kunden-Modus: „Verwende ausschließlich die hier genannten Links" (absolut,
  mit `?VRRVORGANG=…#unterlagen`).
Dazu kommt: die vier Reise-Links gibt es nur, wenn die aktuelle URL ein
`VRRVORGANG` trägt (`agent_base.py:865`). Auf der Übersichtsseite hat Leon
also gar keinen Unterlagen-Link und fällt auf `/MeinChamaeleon` zurück.
`/MeinChamaeleon` steht nicht in der Sitemap; der Server schreibt Links nicht
um.

Änderung:
1. Kunden-Block: MeinChamäleon-Links immer vollständig und wörtlich wie
   aufgelistet, nie gekürzt, nie relativ — die Relativ-Regel gilt dort nicht.
2. **Entschieden (Owner, 2026-09-20): die gemeinte Reise wird deterministisch
   bestimmt, nicht vom Modell.** 99 % der Kunden haben genau eine noch nicht
   abgeschlossene Reise. Die Auflösung gibt es schon: `reiseinfo_vorgang`
   (`agent_base.py:1436`) — offene Seite > nächste eigene Reise, gegen die
   eigenen Buchungen geprüft. Sie wird aus dem Reiseinfo-Tool herausgelöst
   und einmal pro Request beim Prompt-Bau gerufen; `trip_links_block` füllt
   sich dann aus ihrem Ergebnis statt nur aus der URL. Das Modell bekommt
   weiter ausschließlich fertige Links und hält nie einen Platzhalter.
   - Genau eine offene Reise, oder `VRRVORGANG` in der URL: vier fertige Links.
   - Mehrere offene Reisen ohne `VRRVORGANG` (das 1 %): Links zur nächsten
     Reise, im Prompt als „nächste Reise (<Ziel>, <Datum>)" benannt, damit
     Leon sagen kann, worauf er sich bezieht.
   - Keine offene Reise oder Abruf fehlgeschlagen: wie heute nur die
     Übersichts-Links. Der Abruf darf den Chat nie blockieren (Timeout,
     Fehler → leerer Block).
   - **Cache (Owner, 2026-09-20: Pflicht, kein neuer Mechanismus).**
     `kundendaten.vorgangsnummern` holt heute bei jedem Aufruf
     `/get/adresse` — ungecacht, und mit der Auflösung im Prompt-Bau wäre das
     ein TourOne-Abruf pro Kunden-Nachricht im kritischen Pfad. Genutzt wird,
     was schon da ist: `cachetools.func.ttl_cache`, dreimal in
     `agent_base.py` im Einsatz (Visa, Seiten-HTML, Berater). Der Dekorator
     kommt auf den reinen Abruf je kunden_id, **TTL 10 Minuten** (Owner,
     2026-09-20; 2 h verworfen, weil derselbe Cache das `buchungen_tool`
     speist und ein Kunde nach einer Zahlung sonst 2 h den alten Zahlstand
     sähe).
   - **Vorwärmen beim Login, damit auch die erste Nachricht nichts kostet.**
     Gemessen (8 Abrufe, Testkunde 999999999): Median 286 ms, kalt 768 ms,
     Ausreißer 1.268 ms — bis zu 1 s vor dem ersten Wort ist zu viel. Das
     Widget ruft `/kunde/auth` (`app.py:270`), bevor der Kunde tippt. Nach
     erfolgreichem `authenticate` stößt die Route den Abruf in einem
     Daemon-Thread an (Muster wie `_startup_warm`, `app.py:435`); die Route
     selbst wartet nicht darauf. Bis die erste Nachricht kommt, ist der Cache
     warm und der Prompt-Bau liest nur noch aus dem Speicher.
     Ist er es ausnahmsweise nicht (Login und Nachricht fast gleichzeitig,
     Cache nach 10 min abgelaufen), holt der Prompt-Bau selbst — mit hartem
     Timeout 1 s statt der 8 s aus `kundendaten.TIMEOUT`; danach leerer
     Link-Block, der Chat läuft normal weiter.
     Zwei Dinge, die dabei nicht kippen dürfen:
     (1) **Ein Ausfall wird nie gecacht.** `ttl_cache` merkt sich
     Rückgabewerte, auch `None`, aber keine Exceptions. Also wirft die
     gecachte innere Funktion, und `vorgangsnummern` fängt wie heute und gibt
     `None` zurück — sonst hielte ein einzelner Timeout den Kunden 10 Minuten
     in der Störungsmeldung.
     (2) Gecacht wird die rohe Buchungsliste, nicht die sortierte Nummernliste:
     die Sortierung hängt an `heute_berlin()` und wird pro Aufruf neu gemacht.
     Davon profitieren dieselben Aufrufer wie heute mit (reiseinfo_tool,
     Nummernprüfung); `fetch_buchungen_text` holt dieselbe `/get/adresse` und
     kann auf denselben Cache. Für `agenturdaten.vorgangsnummern` gilt es
     analog, ist aber nicht Teil dieses Plans.
     Ergebnis: ein TourOne-Abruf pro Gespräch statt pro Nachricht.
3. Eval in `tests/test_meinchamaeleon_faq.py`: „Wo finde ich meine
   Reiseunterlagen?" einmal mit, einmal ohne `VRRVORGANG` → muss den vollen
   Link mit `#unterlagen`; darf nicht mit einem nackten `/MeinChamaeleon`
   enden (geprüft am href, nicht am Prompt). Braucht weder Login noch echte
   Buchung: mit `VRRVORGANG` reicht eine ausgedachte Nummer in der
   Endpoint-URL; ohne läuft der Fall als Testkunde 999999999 (wie in
   `tests/test_kundendaten.py`), dessen Buchungen das Tool liefert.

## F — Katharinas Mail (Punkt 3)

Mail „Fw: chatbot" vom 27.07.2026, zwölf Dashboard-Screenshots, aus dem
lokalen Thunderbird-Postfach gelesen. **Alle Chats sind vom 26./27. Juli** —
ein Teil ist seither durch andere Arbeit berührt. Deshalb je Befund: gilt er
heute noch? Das klärt der Eval, nicht die Erinnerung.

| # | Chat | Befund | Plan |
|---|---|---|---|
| F1 | „wie erreiche ich den Ansprchpartener für die Reise nach Costrica?" → Zentrale +49 30 347 996 0 | Land trotz Tippfehler erkannt („Costa Rica"), aber kein Berater genannt. | `erlebnisberater_tool` gibt es erst seit 18.09. Es nimmt einen Pfad; für ein Land ist das die Länderseite (`/Amerika/Costa-Rica`), die den Berater trägt. Prompt: bei „Ansprechpartner für <Land>" das Tool mit der Länderseite rufen. Eval mit genau dieser Tippfehler-Frage. |
| F2 | „findet die Tour Outeniqua mit Startdatum 30.1.27 definitiv statt…?" → „liegen mir keine Termine vor", Tel. -167 | Zwei Fehler: Termin nicht gefunden, Garantie nicht genannt. | Garantie = Punkt 2. Termin: die Reise läuft unter `/Afrika/Suedafrika/Outeniqua`; prüfen, ob `termine_tool` den 30.01.2027 dort liefert. Eval: muss „findet statt"/„garantiert", darf nicht „keine Termine". |
| F3 | „wie finde ich das Dokument Reiseanmeldung in Mein Chamäleon" → „unter dem Punkt ‚Deine Einreisebestimmungen und Visainformationen'" | Leon hat `allgemein.md:99` (wo stehen die Einreisebestimmungen) auf eine andere Frage gelegt. | Katharinas Kommentar lautet vollständig „Das ist falsche Aussage:" — die richtige steht nicht in der Mail, und der Owner kennt sie nicht. Sicher umsetzbar: FAQ :99 bekommt einen Fragetext, der nur noch auf Einreisebestimmungen passt; bis die Antwort da ist, verweist Leon bei „Dokument Reiseanmeldung" auf den Erlebnisberater statt zu raten. **Rückfrage an Katharina:** wo liegt die Reiseanmeldung in MeinChamäleon? |
| F4 | „durchführungsgarantie" → „Chamäleon bietet keine generelle Durchführungsgarantie" | Frei erfunden, steht nirgends. | = Punkt 2. Eval-Fall mit genau diesem Ein-Wort-Input. |
| F5 | „Termine Januar 28" (Uluru) → „leider keine freien Termine verfügbar" | Klingt ausgebucht; richtig: 2028 ist ab Anfang 2027 online. | FAQ-Zeile + Regel im Termine-Block: kein Termin im gefragten Jahr = „noch nicht veröffentlicht", nie „ausgebucht"/„keine freien". Sauberer an der Quelle: `termine_tool_base` sagt es selbst, wenn das gefragte Jahr hinter dem letzten veröffentlichten Termin liegt. Eval-Fall. Nebenbei: zweimal „leider". |
| F6 | „Handelt es sich um Non-Stop-Flüge?" → Link „Fluginformationen" | Das PDF `tpl=fluginformationen` enthält Gepäckbestimmungen, keine Flugverbindung. Zweitens zeigt der Link im Dashboard auf den Railway-Host. | (a) Prompt: die PDFs unter „Berater Shortcuts" nicht als Antwort auf Flugfragen verlinken; Flugangaben aus Leistungen/Reiseverlauf, sonst Erlebnisberater. (b) Dashboard löst relative Links gegen `window.location` auf (`static/dashboard/index.html:3436` und Antwort-HTML) → gegen `https://www.chamaeleon-reisen.de`. |
| F7 | Frage zur Namensschreibweise im Pass → „030 - 833 93 93" | Die Nummer steht in keiner Datei des Repos — erfunden. | Regel wie im Agentur-Block schon vorhanden („Erfinde niemals eine Nummer"): in den allgemeinen Prompt übernehmen. Nummern nur aus Seite, Tool oder Prompt. Eval: Antwort darf keine Nummer ohne Stamm 347996 enthalten (Helfer `telefonnummern()` aus `test_agentur_faq.py`). |
| F8 | „Februar 2027, Kapstadt, Gartenroute, Weinroute und Krüger mit Inlandflug" → erst Panorama (mit Eswatini/Lesotho), dann „haben wir nicht im Programm", EB stellt Traumreise zusammen | Richtig: ZAOUT = `/Afrika/Suedafrika/Outeniqua`. Und „Traumreise zusammenstellen" verspricht eine Individualreise. | Änderung A: alle Südafrika-Reisen abrufen, Reiseverlauf gegen die genannten Orte prüfen. Eval: muss Outeniqua; darf nicht „nicht im Programm", „zusammenstellen". |
| F9 | „ich habe nur 14 tage zeit" → Etosha, „perfekt für zweiwöchig" | Richtig: NASOS = `/Afrika/Namibia/Sossusvlei` (14/15 Tage). Im Nachbarchat sagt Leon selbst „Etosha dauert 19 Tage". | Dauer vor jeder Empfehlung prüfen (Änderung A, `abschnitt` Kopf/Übersicht). Eval: muss Sossusvlei, darf nicht Etosha. Geprüft 2026-09-20: Etosha hat **19 Tage** (Seitentitel „Etosha - 19 Tage Erlebnisreise", Reiseverlauf endet am 19. Tag). 21 Tage haben Kwando, Diamonds, Sambesi, Limpopo — die „21" in der Mail ist ein Versehen, der Kern (zu lang für 14 Tage) stimmt. Nützlich für Änderung A: die Länderseite `/Afrika/Namibia` listet alle 13 Namibia-Reisen mit Dauer in EINEM Abruf (Sossusvlei 14, Moremi 14, Okavango 15, … Etosha 19) — Dauerfragen brauchen also keinen Mehrfachabruf. |
| F10 | Teenager, 14 Tage, „namibia" → Moremi (Botswana, Simbabwe & Namibia) | Land und Dauer verfehlt, obwohl beides genannt war. | Wie F9, gleicher Eval zweizügig: darf nicht Moremi, Botswana. |
| F11 | „Welche Sehenswürdigkeiten guckt man sich in Windhoek an" → Geldwechsel, Mückenspray, Visum; „Sehenswürdigkeiten liegen außerhalb" | FAQ-Leck wie Punkt 5 (`FAQ_Afrika.csv:140,148`) plus die Namibia-Visum-Sonderregel ungefragt. | Änderung B. Zusätzlich: die „Zusätzliche Regel für Namibia" gilt nur bei Visum-/Einreisefragen. Eval: darf nicht „Visum", „Mückenspray", „Geld"; muss etwas aus dem Reiseverlauf (Windhoek-Tag der Seite). |

**Was die Mail am Plan ändert**
- Änderung A wird breiter: nicht nur „mit/ohne X", sondern jede Empfehlung
  mit harten Kriterien — Land, Dauer, genannte Orte — wird an den Seiten
  geprüft, bevor ein Reisename fällt (F8, F9, F10, Punkt 10).
- Neue Prompt-Regel „nichts erfinden": keine Telefonnummer, keine
  Geschäftsaussage (Garantie, Reservierung, Länderkombination), die nicht aus
  Seite, Tool oder FAQ stammt (F4, F7, Punkte 6, 13).
- Evals D bekommen einen Block „Mail 27.07." mit den elf Fragen wörtlich.

---

## Offene Punkte (keiner blockiert den Start)

| # | Frage | Bis zur Antwort |
|---|---|---|
| B1 | FAQ-Auto-Injektion (`agent.py:205`) streichen? | Bleibt stehen. Entscheidung erst nach dem Eval-Lauf in Welle 3, mit Zahlen. |
| F3 | Wo liegt die „Reiseanmeldung" in MeinChamäleon? (Rückfrage an Katharina) | Leon verweist auf den Erlebnisberater. |
| 9b | Premium Economy bei Baobab — war die Antwort falsch? | Keine Änderung. |

---

## Ausführung — parallele Subagenten

### Warum Worktrees, und was daraus folgt

Vier Pakete ändern `agent_base.py`. In einem gemeinsamen Arbeitsverzeichnis
würden sich parallele Agenten gegenseitig die Datei unter den Händen
wegschreiben, und der Owner editiert ebenfalls parallel. Deshalb läuft jedes
Paket in einem eigenen Git-Worktree (`isolation: "worktree"`), und der
Orchestrator führt die Ergebnisse nacheinander auf `main` zusammen. Damit das
konfliktfrei bleibt, gehört **jede Dateiregion genau einem Paket** (Tabelle
unten). Was nicht in der eigenen Spalte steht, fasst ein Agent nicht an —
auch nicht „kurz mit".

Folgen, die jeder Agent kennen muss:
- Ein Worktree startet vom letzten Commit. Der Plan muss also vorher
  committet sein (Welle 0). `TODOS.md` hat uncommittete Änderungen des Owners
  und wird deshalb **von keinem Agenten** angefasst — der Orchestrator pflegt
  sie am Ende im Hauptverzeichnis.
- `.env` ist gitignored und fehlt im Worktree. Erster Schritt jedes Agenten,
  der live testet: `ln -s /home/rharvey/Dokumente/Programmieren/cham/chamaeleon-webbot/.env .env`
  (nicht `source` — die Datei hat Leerzeichen um `=`).
- Python: `/home/rharvey/Dokumente/Programmieren/cham/.venv-webbot/bin/python`.
  `python`/`python3` haben die Abhängigkeiten nicht.
- Netz: `requests` geht, `curl` in der Sandbox nicht.

### Regeln für alle Agenten (in jeden Auftrag wörtlich übernehmen)

1. Genau ein Commit pro Paket im eigenen Worktree (Ausnahme W3: ein Commit
   je Regelgruppe, siehe dort), Nachricht im Stil des
   Repos (`feat(scope): …` / `fix(scope): …`, deutsch), Abschlusszeile
   `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
   **Nie pushen. Kein Branch-Wechsel, kein `git restore`, kein `git stash`.**
2. Keine Klassen für Zustand — Factory mit Dict plus Modulfunktionen.
3. Nie Teilstrings im Ergebnis von `format_system_prompt` prüfen. Verhalten
   prüft der Live-Eval, Logik ein billiger Unit-Test ohne Netz.
4. Wiederholungen im System-Prompt sind Absicht — nichts deduplizieren,
   nichts umsortieren, keine Nachbarregeln „mitverbessern".
5. Tests: nur billige Unit-Tests zur eigenen Logik. Keine Test-Infrastruktur,
   keine Fixtures-Frameworks, keine CI-Dateien.
6. In Prompt- und FAQ-Texten nie „leider"; Kund*innen per Du; Genderstern.
7. Kommentardichte und Ton wie im umgebenden Code (deutsch, begründend,
   Messwerte mit Datum).
8. Bei Widerspruch zwischen Plan und Code: anhalten und im Abschlussbericht
   melden, nicht eigenmächtig umplanen.
9. Abschlussbericht: geänderte Dateien, Commit-Hash, Ausgabe der
   Abnahme-Befehle wörtlich, alles Unerwartete.

### Dateibesitz

| Paket | Besitzt | Liest nur |
|---|---|---|
| Welle 0 | `agent_base.py`: neue Funktion `seiten_abschnitt` + `SEITEN_ABSCHNITTE`, `tests/test_seiten_abschnitt.py` | — |
| W1 Evals | `tests/test_kundenfeedback_eval.py` (neu), `tests/test_meinchamaeleon_faq.py` (nur anhängen) | `tests/test_agentur_faq.py`, `agent.py` |
| W2 Website-Tool | `agent_base.py` Z. 218–336 (`build_website_tool_description`, neue `website_tool_multi`, Cache-Umzug in `chamaeleon_website_tool_base` bei unveränderter Ausgabe; NICHT `seiten_abschnitt` aus Welle 0, NICHT `berater_tool_base`), `agent.py` Z. 44–47 (Wrapper), `tests/test_website_tool.py` (neu); falls der Cache-Umzug es erzwingt, die eine Aufrufzeile in `recommendations.py` | `sitemap_sync.py` |
| W4 FAQs | `faqs/allgemein.md`, `faqs/Allgemeine_FAQ.csv`, `faqs/FAQ_Afrika.csv`, `faqs/agentur.md` | — |
| W5 Kunden-Reise | `kundendaten.py`, `app.py` (Route `/kunde/auth`), `agent_base.py` Z. 813–960 (`format_system_prompt`: nur zwei neue String-Parameter + `kunden_modus_block`) und Z. 1418–1480, `agent.py` (`call_stream`: Auflösung + Übergabe, ~Z. 205–225), `tests/test_kundendaten.py` (anhängen) | `session_binding.py`, `kunden_auth.py` |
| W6 Dashboard-Links | `static/dashboard/index.html`, ggf. `static/dashboard/report.html` | `dashboard.py` |
| W7 Termine 2028 | `agent_base.py` Z. 441–497 (`termine_tool_base`), `tests/test_termine_live.py` bzw. neuer Unit-Test | `travel_index.py` |
| W3 Prompt (Welle 2) | `agent_base.py` Z. 908–1038 (`system_prompt_template`) | alles |

Zeilennummern = Stand `d920db3`; maßgeblich sind die Funktionsnamen.

### Welle 0 — Orchestrator, seriell

- Plan committen (`docs: Plan Kundenfeedback September 2026`), sonst sehen
  die Worktrees ihn nicht. Nichts anderes mitcommitten (`TODOS.md`,
  `docs/tool-history-plan.md`, `tests/test_tool_history.py` bleiben liegen).
- **Schneidefunktion vorab (Review D5):** zweiter Commit mit der reinen
  Funktion `seiten_abschnitt(markdown: str, abschnitt: str) -> str | None` in
  `agent_base.py` (direkt vor `chamaeleon_website_tool_base`) samt
  `tests/test_seiten_abschnitt.py`. Tool (W2) und Evals (W1) importieren
  beide genau diese Funktion — der Eval prüft damit denselben Ausschnitt, den
  Leon sieht. Vertrag: gültige Namen `uebersicht`, `reiseverlauf`,
  `reisedetails`, `leistungen`, `unterkuenfte`, `zusatzprogramme`
  (Konstante `SEITEN_ABSCHNITTE`); geschnitten wird von der Überschrift bis
  zur nächsten bekannten Überschrift; `uebersicht` = Anfang bis
  „Reiseverlauf"; unbekannter Name → `ValueError`; Überschrift fehlt auf der
  Seite → `None` (der Aufrufer entscheidet, W2 meldet es dem Modell).
  Unit-Test: jeder Abschnitt, fehlende Überschrift, unbekannter Name, Seite
  ohne jede Überschrift (Länderseite).

### Welle 1 — sechs Agenten parallel (Modell: opus, je eigener Worktree)

**W1 — Evals** (Plan: Abschnitt D, E.3, F-Tabelle; Messung siehe unten, Review D20)
- Neue Datei nach dem Muster von `tests/test_agentur_faq.py`: Fälle als
  Dicts mit `frage`/`verlauf`, `endpoint`, `muss`, `darf_nicht`;
  `keyword_matches` und `telefonnummern` von dort importieren, nicht kopieren.
  Schalter `RUN_KUNDENFEEDBACK_EVAL=1`, sonst übersprungen.
- Blöcke per pytest-Marker/`-k` einzeln lauffähig: `filter` (D1 + F8–F10),
  `airline` (D2, ≥20 Länder, Erwartung zur Laufzeit aus der Seite),
  `nichtangeboten` (D3), `ungefragt` (D4 + F11), `fachwissen` (D5 + F2, F4,
  F5), `erfinden` (F1, F7), `flug` (F6a).
- In `test_meinchamaeleon_faq.py` zwei Fälle anhängen (E.3): mit
  ausgedachtem `VRRVORGANG`, und ohne als Testkunde `999999999`.
- Erwartungen, die am Programm hängen (welche Tansania-Reise hat Sansibar,
  welche Airline), leitet der Test aus den Seiten ab — nie hart kodieren.
  Den Ausschnitt liefert `agent_base.seiten_abschnitt` aus Welle 0, keine
  eigene Kopie (Review D5).
- **Querschnitts-Check für jeden Fall (Review D7):** ein gemeinsamer Helfer
  prüft zusätzlich zu `muss`/`darf_nicht`: höchstens 5 Sätze (HTML vorher
  entfernen; Fälle können `max_saetze` überschreiben), kein „leider", kein
  „FAQ"/„Wissensbasis". Die Ausgangsmessung hält auch die heutige Satzzahl
  je Fall fest.
- **Schwesterfälle gegen Auswendiglernen (Review D15):** jeder Kernfall
  bekommt eine Umformulierung mit anderem Land oder Wortlaut, die in KEINEM
  Prompt-Beispiel steht (z. B. „Kenia ohne Badeaufenthalt", „Peru ohne
  Amazonas", „10 Tage Marokko"). Erwartung wie immer aus den Seiten.
- **Ankerwerte gegen den Zirkelschluss (Review D16):** Tool und Eval teilen
  sich `seiten_abschnitt`; ein Fehler darin macht beide gleich falsch. Darum
  je Block wenige fest eingetragene Wahrheiten mit Datum, geprüft OHNE
  Live-Modell als Netz-Test vor dem Block: Ruaha ohne Sansibar, Cheetah mit,
  Kasbah fliegt Discover Airlines, Etosha 19 Tage, Gorilla nennt Ruanda nur
  als Grenze/Zwischenstopp (Stand 2026-09-20). Eine leere ODER vollständige
  Erwartungsmenge (keine/alle Tansania-Reisen ohne Sansibar) ist ein
  Fehlschlag, kein Skip. Werden Ankerwerte bei einem Programmwechsel rot,
  ist das eine Information — nachziehen und Datum erneuern.
- **Wiederholungen:** Schalter `EVAL_N` (Default 1); bei `EVAL_N=3` meldet
  jeder Fall eine Quote (z. B. 2/3) statt rot/grün.
- W1 selbst fährt in Welle 1 nur EINEN Probelauf je Block (nie mehr als ~25
  Live-Aufrufe am Stück, siehe TODOS „Empty-Reply"), um zu zeigen, dass die
  Fälle laufen. Rot ist hier das erwartete Ergebnis — nichts „grün biegen".
  Die eigentliche Ausgangsmessung macht der Orchestrator (siehe unten).
- Abnahme: `pytest tests/test_kundenfeedback_eval.py -q` ohne Schalter →
  alles `skipped`, 0 Fehler beim Sammeln; Ankerwert-Test grün; Probelauf im
  Bericht.

**W2 — Website-Tool für mehrere Seiten** (Plan: Abschnitt A, ohne Prompt-Block)
- **Die Ein-Seiten-Funktion bleibt (Review D12).**
  `chamaeleon_website_tool_base(url_path)` behält Signatur und Ausgabe —
  `berater_tool_base` (`agent_base.py:733`) liest damit Name und Durchwahl,
  und `tests/test_berater.py` patcht sie mit einem Argument. Neu ist
  `website_tool_multi(url_paths, abschnitt)`, die sie je Pfad ruft, schneidet,
  deckelt und zusammensetzt. Nur der Wrapper in `agent.py` wechselt auf die
  neue Funktion. Die Kopfzeile `# <Pfad>` erscheint nur bei mehr als einem
  Pfad. Einziger Eingriff in die alte Funktion ist der Cache-Umzug (D9), mit
  einem Test „Ausgabe zeichengleich wie vorher".
- Vertrag (W3 und W1 verlassen sich darauf, nicht ändern):
  `chamaeleon_website_tool(url_paths: list[str], abschnitt: str = "")`.
  `abschnitt` ∈ `""`, `uebersicht`, `reiseverlauf`, `reisedetails`,
  `leistungen`, `unterkuenfte`, `zusatzprogramme`. `uebersicht` = Seitenanfang
  bis zur Überschrift „Reiseverlauf" (trägt Titel mit Dauer und Highlights).
- 1–8 Pfade, mehr → klare Fehlermeldung an das Modell. Ein einzelner String
  statt Liste wird toleriert (Gemini schickt das gelegentlich).
- Parallel holen (`ThreadPoolExecutor`, Vorbild `REISEINFO_FETCH_PARALLEL`);
  Ausgabe in Eingabereihenfolge; Fehler bleiben lokal.
- **Den einzigen Worker nicht blockieren (Review D14).** Gemessen
  2026-09-21: HTML→Markdown kostet 153 ms je Seite im Median (75–191 ms),
  reine Rechenzeit; acht kalte Seiten ≈ 1,2 s, in denen kein anderer
  Chat-Stream bedient wird (`WEB_CONCURRENCY=1`, gevent). Nach jeder
  umgewandelten Seite gibt `website_tool_multi` kurz ab (`gevent.sleep(0)`,
  Import so, dass Unit-Tests ohne gevent laufen). Im Bericht: Parse-Zeit je
  Seite und Gesamtzeit kalt/warm.
- Geschnitten wird ausschließlich mit `seiten_abschnitt` aus Welle 0.
  Termine-Anhang nur bei `abschnitt=""`.
- **Größenschranke im Tool, nicht im Prompt (Review D8).** Gemessen: eine
  Reiseseite ≈ 52.000 Zeichen, acht ganze Seiten ≈ 416.000 Zeichen ≈ 100.000
  Tokens. Deshalb: mehr als ein Pfad mit `abschnitt=""` → keine Seiten,
  sondern eine kurze Anweisung mit den gültigen Abschnittsnamen. Unbekannter
  Abschnittsname → dieselbe Anweisung (nie stillschweigend die ganze Seite).
  Überschrift fehlt auf EINER Seite → für diese Seite ein Hinweis statt
  Inhalt. Gesamtdeckel `WEBSITE_TOOL_MAX_CHARS = 100_000`: wird er
  überschritten, bekommt jede Seite denselben Anteil und einen
  Kürzungsmarker, der sagt, dass gekürzt wurde.
- **Cache: Markdown statt HTML (Review D9).** Heute liegt rohes HTML im
  `ttl_cache` (`agent_base.py:268`, gemessen 218–374 KB je Seite, 513 Seiten
  in der Sitemap → bis ~150 MB im einzigen Worker). Der Cache wandert auf die
  Stufe „Seite → Titel + Markdown" (~52 KB). `recommendations.py:34` braucht
  weiter rohes HTML (Vorschaubilder) und `berater_von_seite` liest die Seite
  ebenfalls: beide Aufrufer prüfen und im Bericht nennen, was sie künftig
  bekommen. Kein zweiter großer HTML-Cache daneben.
- Die heutige Normalisierung gilt je Pfad: `https://chamaeleon-reisen.de`-
  Präfix und `#Anker` abschneiden, Warnung bei Pfad außerhalb der Sitemap.
- Tool-Beschreibung (`build_website_tool_description`) um beide Argumente und
  ein Beispiel ergänzen; `sitemap_sync.py` setzt `.description` weiter nur
  über diese Funktion — dort nichts ändern.
- Unit-Tests ohne Netz: `get_chamaeleon_website_html` monkeypatchen;
  Reihenfolge, ein Fehlschlag unter mehreren, >8 Pfade, String statt Liste,
  **>1 Pfad ohne Abschnitt, unbekannter Abschnitt, Überschrift fehlt auf einer
  von zwei Seiten, Gesamtdeckel greift, Anker/https-Präfix je Pfad,
  zweiter Aufruf trifft den Markdown-Cache** (Review D7–D9).
- Abnahme: `pytest tests/test_website_tool.py tests/test_general.py tests/test_berater.py -q`
  grün; live einmal `["/Afrika/Tansania/Ruaha","/Afrika/Tansania/Cheetah"]`
  mit `reiseverlauf` → Zeichenzahl und „Sansibar"-Treffer je Seite im Bericht
  (Erwartung: Ruaha 0, Cheetah >0). Dazu ein Live-Rauchtest: zehn echte
  Chat-Aufrufe mit einer Vergleichsfrage — ruft Gemini das Tool mit einer
  LISTE auf? Quote im Bericht.

**W4 — FAQ-Einträge** (Plan: Abschnitt C, F5-FAQ-Zeile, F3, B-Halbsatz)
- `allgemein.md`: neuer Abschnitt „Buchen und Reservieren" mit den Punkten
  2, 4, 6, 7, 11 im F:/A:-Format der Datei; Punkt 2 inkl. Halbsatz zu
  Ausflugs-Mindestteilnehmerzahlen; Punkt 4 mit Link „Reisebüro finden".
  „Termine und Preise für 2028 sind ab Anfang 2027 online" (F5).
  :46/:49 (Flugzeiten/Sitzplätze): Frage so fassen, dass sie nur bereits
  gebuchte Reisen meint. :99: Frage nur noch auf Einreisebestimmungen; neue
  F: „Wo finde ich meine Reiseanmeldung?" → Erlebnisberater (bis F3 geklärt).
- Prüfen, ob `Allgemeine_FAQ.csv` dieselben Einträge spiegelt
  (`agent_base.py:112`) und was davon tatsächlich im Prompt landet; nur die
  Quelle pflegen, die geladen wird, und das im Bericht begründen.
- `FAQ_Afrika.csv`, Block Tansania (ab Z. 219): Punkte 8 und 9, Format
  `Nr.;Frage;Antwort;`, fortlaufende Nummer.
- `agentur.md` §6 „Wie kann ich eine Buchung oder Option anlegen?": Satz zu
  Reservierungen = 7 Tage, danach automatisch Festbuchung. **§10
  (Just4You-Freiplatz) nicht anfassen.**
- Abnahme: `python -c "import agent_base"` lädt ohne Fehler;
  `pytest tests/test_agentur_faq.py -q` sammelt fehlerfrei.

**W5 — MeinChamäleon: Reise deterministisch + Cache** (Plan: Abschnitt E)
- `kundendaten.py`: eine gecachte Funktion
  `_buchungen_roh(kunden_id) -> list` hinter
  `cachetools.func.ttl_cache(maxsize=512, ttl=600)`. **Schlüssel ist NUR die
  kunden_id (Review D11)** — kein Timeout-Argument, sonst träfe der
  Vorwärm-Aufruf einen anderen Eintrag als der Prompt-Bau. Intern gilt der
  feste `kundendaten.TIMEOUT`. **Gecacht wird nur `adresse["buchungen"]`
  (Review D19)**, nie Name, Anschrift oder Kontaktdaten; beide Aufrufer lesen
  ohnehin nur dieses Feld. Die Funktion **wirft** bei Fehlern;
  `vorgangsnummern` und `fetch_buchungen_text` fangen wie heute. Sortiert wird
  pro Aufruf.
- **Neue reine Funktion statt `reiseinfo_vorgang` (Review D10).**
  `reiseinfo_vorgang` gibt `eigene[0]` zurück, laut eigenem Kommentar „die
  kommende Reise (sonst die zuletzt gereiste)" — ein Kunde mit nur
  vergangenen Reisen bekäme Links zur Vorjahresreise, und ein Label liefert
  sie nicht. Für die Links deshalb:
  `naechste_offene_reise(buchungen: list, heute: str) -> tuple[str, str]`
  in `kundendaten.py`: filtert über den vorhandenen `zeit_marker` auf
  kommend/läuft, nimmt die näheste, gibt `(vorgang, label)` mit Ziel und
  Datum zurück, sonst `("", "")`. Ohne Netz, arbeitet auf der gecachten
  Liste. `reiseinfo_vorgang` bleibt für das Reiseinfo-Tool unverändert; der
  Kommentar an der neuen Funktion sagt, warum es zwei gibt.
- **Aufgelöst wird im Aufrufer, nicht in der Textfunktion (Review D4).**
  `agent.py` (`call_stream`) ruft die aus `reiseinfo_vorgang` herausgelöste
  Auflösung und übergibt `format_system_prompt` nur zwei Strings:
  `reise_vorgang` und `reise_label` (Default `""`). `format_system_prompt`
  bleibt ohne Netz und bekommt die kunden_id weiterhin nie zu sehen.
- **Die URL-Nummer bleibt der Rückfall (Review D3).** Rangfolge für
  `reise_vorgang`: (1) formal gültiges `VRRVORGANG` aus der URL — wie heute,
  ohne API, funktioniert auch bei TourOne-Ausfall; (2) sonst die nächste
  eigene Reise aus der API; (3) sonst leer → nur Übersichts-Links. Der
  API-Abruf läuft also nur, wenn die URL keine Nummer trägt.
- **Gesamt-Timeout außen (Review D11):** der Aufrufer begrenzt die Wartezeit
  mit `gevent.Timeout(1.0)` um den Cache-Zugriff (requests-`timeout` gilt je
  Socket-Schritt, nicht für die Gesamtdauer). Läuft die Zeit ab → `""`, der
  Chat läuft weiter; der Abruf darf im Hintergrund fertig werden und füllt den
  Cache für die nächste Nachricht. Login und erste Nachricht gleichzeitig
  ergeben zwei TourOne-Abrufe — bewusst hingenommen, keine
  In-Flight-Verwaltung. Mehrere offene Reisen ohne `VRRVORGANG` → die nächste,
  `reise_label` = Ziel und Datum.
- `app.py` `/kunde/auth`: nach `authenticated=True` Abruf im Daemon-Thread
  anstoßen, Route wartet nicht. Die Route kennt die Kundennummer nicht direkt
  (`authenticate` liefert nur `authenticated, session_id`): der Thread holt
  sie über `kunden_auth.resolve(session_id)` wie `app.py:117`. Annahme im
  Kommentar festhalten: das Vorwärmen wirkt nur, solange `WEB_CONCURRENCY=1`
  (gleiche Annahme wie `rate_limit.py:17`). Fehler im Thread nur loggen (ohne
  Kundennummer, siehe Kommentar in `vorgangsnummern`).
- Text im `kunden_modus_block`: MeinChamäleon-Links immer vollständig und
  wörtlich, nie relativ, nie gekürzt (E.1). Das ist die einzige Prompt-Text-
  Änderung außerhalb von W3.
- Unit-Tests: Cache-Treffer = ein Abruf bei zwei Aufrufen; Ausfall wird nicht
  gecacht (zweiter Aufruf versucht es erneut); Auflösung mit 0/1/2 offenen
  Reisen und mit/ohne `VRRVORGANG`. Cache im Test per `cache_clear()` leeren.
  **Schlüssel-Gleichheit (Review D11):** vorwärmen, dann über den
  Prompt-Pfad lesen = genau EIN Abruf. **`naechste_offene_reise` (D10):** nur
  vergangene Reisen → leer; eine kommende; zwei kommende → die näheste;
  laufende Reise zählt als offen; Label trägt Ziel und Datum.
  **Pflicht-Regressionstests (Review D3):** Abruf wirft + Nummer in der URL →
  `reise_vorgang` = URL-Nummer und die vier Links stehen im Prompt-Block;
  Nummer in der URL → es findet GAR KEIN Abruf statt.
  **Zusätzlich (Review D7):** Eintrag läuft nach TTL ab und wird neu geholt
  (Zeit per monkeypatch/`timer`-Argument, nicht per `sleep`); Timeout → `""`
  in unter ~1,5 s; `/kunde/auth` antwortet, ohne auf den Vorwärm-Thread zu
  warten; eine Exception im Thread ändert die Auth-Antwort nicht.
- Abnahme: `pytest tests/test_kundendaten.py tests/test_kunden_auth.py tests/test_reiseinfo.py tests/test_general.py -q`
  grün; Zeitmessung Prompt-Bau kalt/warm für `999999999` im Bericht.

**W6 — Dashboard: relative Links** (Plan: F6b)
- Relative `href` in Chat-Antworten und Empfehlungskarten gegen
  `https://www.chamaeleon-reisen.de` auflösen statt gegen `window.location`
  (`index.html:3436` und die Stelle, die das Antwort-HTML einsetzt;
  `report.html` prüfen). Protokollprüfung (http/https) bleibt.
- Kein neues Design, keine neuen Komponenten. Lokal starten und prüfen nach
  Memory „Dashboard lokal pruefen" (Basic Auth; Screenshots nur mit `--clip`).
- Pflicht laut Owner-Designregeln: Screenshot → **frischer Subagent** sieht
  ihn ohne Kontext („Was ist falsch an diesem Bild?") → fixen → wiederholen,
  bis sauber.
- Abnahme: ein Chat mit `/start/report.loadpdf.php…`-Link zeigt im DOM einen
  `href` auf `www.chamaeleon-reisen.de`; Screenshot im Bericht.

**W7 — Termine: „noch nicht veröffentlicht"** (Plan: F5, Tool-Seite)
- Ursache: `termine_tool_base` antwortet bei gefiltertem Jahr ohne Treffer
  „Keine Termine für … Diese Auskunft ist belastbar." — für 2028 liest Leon
  daraus „ausgebucht". Wenn das gefragte Jahr (bzw. Jahr+Monat) **nach** dem
  letzten veröffentlichten Termin der Reise liegt, sagt das Tool
  stattdessen: Termine für dieses Jahr sind noch nicht veröffentlicht; nicht
  „ausgebucht", nicht „keine freien" sagen; `#termine` verlinken.
- Kein festes „2028"/„Anfang 2027" im Code — die Jahresangabe steht als
  FAQ-Zeile (W4) und altert dort.
- Unit-Test ohne Netz: Reise mit Terminen bis 2027, Frage nach 2028 → neuer
  Text; Frage nach ausgebuchtem Monat 2027 → alter Text unverändert; Reise
  ganz ohne Termine + Jahresfilter → alter Text, kein „noch nicht
  veröffentlicht" (Review D7).
- Abnahme: `pytest tests/test_travel_index.py tests/test_termine_live.py -q`
  plus neuer Test grün; live `termine_tool_base("/Australien/…/Uluru…", jahr=2028)`
  im Bericht (Pfad aus der Sitemap nehmen).

### Zusammenführen nach Welle 1 — Orchestrator, seriell

Reihenfolge W4 → W7 → W2 → W5 → W6 → W1, per `git cherry-pick` des
Paket-Commits auf `main`; die Worktree-Branches sind lokale
Wegwerf-Branches, werden nie gepusht und danach gelöscht (kein
Feature-Branch, kein PR). Bei Konflikt: nicht raten, Owner fragen.
Danach im Hauptverzeichnis die komplette Offline-Suite:
`pytest -q` → grün, bevor Welle 2 startet.

### Verschränkte Messung — ersetzt Ausgangs- und Schlussmessung (2026-09-23, dreht D20)

**Eine Kampagne statt zwei.** Jeder Fall wird zweimal unmittelbar
nacheinander im selben Prozess gestellt: einmal mit dem alten, einmal mit dem
neuen `system_prompt_template`, die Reihenfolge je Fall alternierend. Gemessen
wird NACH den vier W3-Commits, nicht davor.

**Warum.** Zwei getrennte Kampagnen vermengen die Prompt-Änderung mit allem,
was dazwischen liegt: Zeit, Last, Modell-Serving, Webseiteninhalt,
Zwischen-Commits. Das ist hier kein theoretisches Risiko, sondern zweimal
belegt: der W1-Probelauf unter paralleler Last riss reproduzierbar das
10-s-Timeout der Website (~9 s Antwortzeit) und war nach unten verzerrt; und
`7c6ed5e` hat am selben Tag den Nenner des `airline`-Blocks verschoben —
`hauptseiten(land)[:3]` wählt seither für 7 der 32 Airline-Länder andere
Seiten, drei davon hatten vorher nur eine `-ALL`-Seite ohne Leistungen und
fielen als „übersprungen" heraus. Ein Vorher-Wert von 12:58 ist gegen einen
Nachher-Wert von morgen nicht vergleichbar. Verschränkt ist innerhalb des
Paares alles konstant außer dem Prompt.

**Baubar, geprüft 2026-09-23, präzisiert 2026-09-24 (Review R6).** Der Eval
ruft `agent.call` im selben Prozess (`tests/test_kundenfeedback_eval.py:69`),
und `format_system_prompt` liest `system_prompt_template` erst zur Aufrufzeit
(`agent_base.py:1494`). Umschalten ist eine Zuweisung auf das Modulglobal.
Die ALTE Vorlage holt der Treiber aus Git: `git show <Eltern-Commit von
W3-Commit 1>:agent_base.py`, f-String zwischen `system_prompt_template = f"""`
und `""".strip()` (heute `agent_base.py:908-1038`) ausschneiden und mit `eval`
im Namensraum von `agent_base` auswerten. Der Eltern-SHA steht als Konstante
im Treiber und im Protokoll. Ein netzfreier Unit-Test prüft das Schneiden
gegen den aktuellen Stand.

**Der Treiber (Review R2/R8/R9).** Eine neue Datei unter `tests/`, kein
Eingriff in die drei Suiten. Er importiert die Listen (`FILTER`, `UNGEFRAGT`,
`FACHWISSEN`, `ERFINDEN`, `FLUG`, `DONE`, `URL_CASES`), baut `airline` und
`nichtangeboten` mit den Modul-Helfern (`AIRLINE_LAENDER`, `reise_mit_airline`,
`NICHT_ANGEBOTEN`) nach und wärmt alle Seitenabrufe vor dem ersten
Modellaufruf vor. Für die neue Suite ruft er `call` + `pruefe(fall, reply)`
direkt (Befund-Art aus den Befunden); die Einzelfunktionen der alten Suiten
ruft er direkt auf, parametrisierte über ihre Modul-Listen,
`test_buchungsstatus_mit_verifizierter_agentur` nur mit gesetzter
`AGENTUR_TEST_NUMMER`. Jede Seite hat einen von vier Ausgängen: grün, rot
(`AssertionError` / Befunde), leer (Fallback-Text, nach einmaliger
Wiederholung), Fehler (`Skipped`, Netz-/TourOne-Ausnahme, fehlende Umgebung).
Paare mit einer Leer- oder Fehlerseite fallen aus b/c und werden je Block
gezählt.

**Umfang: 107 Fälle, 214 Aufrufe, eine Sitzung.** Gegen 450–600 im alten
Zuschnitt.

| Block | Fälle | warum drin |
|---|---|---|
| `filter` | 8 | W3-Regelgruppe 1, Kern von Änderung A |
| `airline` | 28 | W3-Regelgruppe 4 und Punkt 12; breitester Block der Suite |
| `nichtangeboten` | 4 (von 10) | prüft EINEN Prompt-Satz; zehn Länder kaufen dafür nichts |
| `ungefragt` | 4 | Regelgruppe 2, dazu B1 |
| `fachwissen` | 12 | Regelgruppe 2/4 |
| `erfinden` | 4 | Regelgruppe 3 |
| `flug` | 2 | Regelgruppe 4 |
| Agentur | 33 (22 `DONE` + 9 Einzelfunktionen + 2 Deko; 1 davon nur mit `AGENTUR_TEST_NUMMER`) | Regressionssperre Push 2 |
| MeinChamäleon | 13 | Regressionssperre Push 2 |

`anker` (5) läuft als Vorbedingung vor jedem Block, macht keine
Modellaufrufe und zählt nicht als Messung.

**Blockgröße.** Verschränkt kostet ein Fall ZWEI Aufrufe, plus Wiederholungen
bei Leerantworten (R1). Die Grenze von ~25 Aufrufen am Stück (TODOS,
Empty-Reply) gilt für Aufrufe, nicht für Fälle: ein Prozess darf höchstens
~10–12 Fälle fahren. `airline` wird in drei Läufe geteilt, `fachwissen` in
zwei, Agentur in drei. Zwischen zwei Prozessen wartet der Orchestrator 60 s
(`EVAL_PAUSE_S`, Review R12), weil ungemessen ist, ob die Kippe am Prozess
oder an der Aufrufrate hängt; die Zeitdaten (R11) beantworten das nach der
Kampagne.

**Maßstab: `EVAL_N` bleibt 1, das Paar IST die Wiederholung.** Wichtig und
leicht zu übersehen: `fahre()` (`tests/test_kundenfeedback_eval.py:600-613`)
legt bei `EVAL_N=1` und `EVAL_N>=2` VERSCHIEDENE Maßstäbe an — bei N=1 muss der
eine Lauf grün sein, ab N=2 genügt EINER von N. Ein Fall mit echter Quote 1/3
ist bei N=3 immer grün und bei N=1 zu zwei Dritteln rot. Beide Seiten dürfen
deshalb nie mit verschiedenem N laufen.

**Auswertung: gepaart, als Fall-Listen (Review R10).** Je Block die
diskordanten Paare zählen — b = alt rot/neu grün, c = alt grün/neu rot — und
die c-Fälle namentlich mit Befund-Art auflisten. Die Entscheidungsregel ist
für ALLE Blöcke dieselbe wie bei D3: ganze Fälle, keine Prozentwerte, keine
Einteilung in „Quote"- und „kaputt"-Blöcke. Ein p-Wert (McNemar exakt,
zweiseitig, α = 0,05; bei c=0 ist `p = 2·(1/2)^b`, also erst b=6 → 0,031)
steht nur als Zusatzzeile bei `airline` (28) und Agentur (33), wo n ihn trägt.
Das Push-2-Tor entscheidet auf der c-Liste der beiden alten Suiten: leer, oder
je Fall begründet.

**Leerantworten (Review R1, 2026-09-24).** Eine Seite des Paares, deren Reply
den Fallback-Text `EMPTY_ANSWER_FALLBACK` enthält, wird genau einmal sofort
wiederholt, bevor gewertet wird; bleibt sie leer, zählt sie rot. Die
Wiederholungen werden je Block gezählt (leer alt / leer neu) und stehen in der
Vorher/Nachher-Tabelle. Sie zählen zur ~25er-Aufrufgrenze je Prozess.

**Tausch-Nachweis (Review R5, 2026-09-24).** Bevor der erste Modellaufruf
fällt, rendert der Treiber `format_system_prompt` für einen festen Fall unter
alter und neuer Vorlage, verlangt `alt != neu` und schreibt SHA-256 beider
Prompts plus den Git-Commit je Vorlage ins Protokoll. Gleichheit ist ein
harter Abbruch, kein Ergebnis. Ein netzfreier Unit-Test deckt den Abbruch.

**Mitzuschneiden: Tool-Aufrufe, Zeit, Dauer (Review R7/R11).** `agent.call`
verwirft die `tool_call`-Events (`agent.py:477-491`), und geloggt wird nur bei
auffälligem `finish_reason` oder leerer Antwort. Der Treiber ersetzt deshalb
für die Kampagne `agent.call_stream` durch einen zählenden Wrapper (`call`
löst den Namen zur Laufzeit auf). Protokollzeile je Fall × Seite: Fall-ID,
Seite, Ausgang, Befund-Art, leer-Wiederholung, Tool-Aufrufe, Wanddauer,
Zeitstempel (UTC), Prozess-Nr., Hash der Vorlage. Der Bericht weist je Block
Median-Dauer alt/neu aus und trägt Leerantworten gegen die Aufrufrate auf.
Welle 3 Punkt 3 (Overhead-Kontrolle) ist damit aus der Kampagne erfüllt.

**Was diese Messung NICHT mehr beantwortet.** Der alte Zuschnitt wollte „ist
das Kundenfeedback behoben" (Baseline `d920db3`). Verschränkt misst „wirkt
W3" — W4, W5, W6 und W7 stecken in beiden Zweigen. Der Beleg für die
Feedback-Frage steht damit in den Paketabnahmen, nicht in dieser Tabelle.
Welle 3 Punkt 1 ist entsprechend umzuformulieren.

**Offen für den Review:**
1. Die alten Suiten sind nur teilweise datengetrieben: `test_agentur_faq` hat
   einen parametrisierten Block (`DONE`, Z. 208) plus neun Einzelfunktionen,
   `test_meinchamaeleon_faq` `URL_CASES` (Z. 92) plus drei. Für die
   Einzelfunktionen gibt es eine Paarung je Fall nicht ohne Eingriff in die
   Dateien. **Entschieden (Review R2, D2, 2026-09-24):** ein eigener Treiber
   importiert die Einzelfunktionen und ruft sie alt/neu unmittelbar
   nacheinander; `AssertionError` = rot, Funktionsname = Fall-ID. Kein
   Eingriff in die Dateien, echte Paare.
2. Der 80-%-Auslöser für die Vergleichstabelle je Land (Welle 3, Punkt 4) ist
   bei 8 `filter`-Fällen nicht auflösbar: 6/8 = 75 %, 7/8 = 87,5 %, die
   Schwelle liegt dazwischen. **Entschieden (R3, D3):** umgeschrieben auf
   ganze Fälle, siehe Welle 3 Punkt 4.
3. Mindestens 2 der 8 `filter`-Fälle sind heute allein wegen der Satzzahl rot
   (gemessen 8, 7, 3, 3 Sätze gegen `MAX_SAETZE = 5`). Der Auslöser misst dort
   teilweise die Länge, nicht das Filtern. **Entschieden (R3, D3):** Spalte
   „Befund-Art" je rotem Fall; Satzzahl-Fälle zählen nicht zum Auslöser.
4. **Entschieden mit R2 (D2):** eigener Runner (neue Datei unter `tests/`),
   der Falllisten UND Einzelfunktionen importiert. Er erreicht die
   Einzelfunktionen sehr wohl — sie sind fixture-frei aufrufbar.

### Welle 2 — ein Agent, seriell (Modell: opus)

**W3 — System-Prompt** (Plan: A-Prompt-Block, B, F-„nichts erfinden", F1, F6a, F11)
Läuft nach dem Zusammenführen, weil er Tool und Evals zum Prüfen braucht.
Besitzt nur `system_prompt_template`. Änderungen:
- Block „Reisen vergleichen und empfehlen": harte Kriterien (Land, Dauer,
  genannte Orte, mit/ohne X) werden an den Seiten geprüft, bevor ein
  Reisename fällt. Dauer/Überblick: Länderseite (`/Kontinent/Land`,
  mit Reiseliste) oder `abschnitt="uebersicht"`; Inhalt:
  `abschnitt="reiseverlauf"` über ALLE Reisen des Landes in einem Aufruf.
  Verlängerungen zählen nicht zur Reise. Im gewünschten Land bleiben. Bei
  Widerspruch erneut abrufen. Beispiel Tansania/Sansibar wörtlich.
- **Das Stil-Beispiel „erste Safari" wird umgeschrieben (Review D17).**
  Heute macht es „empfehlen ohne prüfen" vor, ausgerechnet mit Etosha
  (`agent_base.py:609`) — der Reise, die in F9, F10 und Punkt 10 fälschlich
  fällt. Neu: die Musterantwort fragt kurz nach Land oder Dauer und verlinkt
  die Safari-Übersicht, ohne feste Reise; Länge und Ton wie heute. Daneben
  das neue Beispiel „Tansania ohne Sansibar" für den Fall MIT hartem
  Kriterium. Bekannte Folge: bei ganz offenen Fragen nennt Leon seltener
  sofort eine konkrete Reise.
- FAQs nur auf gestellte Frage; „FAQ"/„Wissensbasis" nie in einer Antwort;
  „als Inspiration" und „eigentlich immer nutzen" entfallen.
- Flüge: Airline/Buchungsklasse aus `abschnitt="leistungen"`; die PDFs unter
  „Berater Shortcuts" nicht als Antwort auf Flugfragen verlinken.
- Nicht angebotene Länder: nicht in der Sitemap = keine Reise; nie als
  Kombination ausgeben.
- „Nichts erfinden": keine Telefonnummer, keine Geschäftsaussage, die nicht
  aus Seite, Tool oder FAQ stammt; keine Individualreise versprechen.
- Ansprechpartner für ein Land: `erlebnisberater_tool` mit der Länderseite.
- Namibia-Visum-Zusatzregel nur bei Visum-/Einreisefragen.
- Termine: kein Termin im Jahr ≠ ausgebucht (deckt sich mit W7).
- **Ein Commit je Regelgruppe (Review D2)**, damit jede einzeln per
  `git revert` zurückzunehmen ist: (1) Reisen vergleichen und empfehlen,
  (2) FAQ-Nutzung + nicht angebotene Länder, (3) Nichts erfinden +
  Ansprechpartner, (4) Flüge/PDFs + Namibia-Visum + Termine.
- Arbeitsweise: eine Regelgruppe nach der anderen, danach den zugehörigen
  Eval-Block (`-k filter` usw.), Ergebnis notieren, dann committen. Rote Einzelfälle einmal
  einzeln wiederholen, bevor am Prompt gedreht wird. Längenregel (2–4 Sätze)
  nicht aufweichen.
- Abnahme (Review D6, umgeschrieben 2026-09-24 nach D20): je Eval-Block
  gepaart alt/neu in der verschränkten Kampagne NACH den vier W3-Commits
  (siehe „Verschränkte Messung"). Keine getrennte Ausgangsmessung. Während
  der Arbeit an einer Regelgruppe fährt W3 den zugehörigen Block nur als
  Sichtprüfung (`-k filter` usw.), nicht als Messung.

### Zwei Pushes (Review D2) — beide nur auf ausdrückliches Wort des Owners

- **Push 1, risikoarm, nach dem Zusammenführen von Welle 1:** W4 (FAQs),
  W7 (Termine-Text), W6 (Dashboard-Links), W5 (Kunden-Links + Cache). W2
  ist dann zwar gemergt, aber ohne den Prompt aus W3 ändert das neue Tool am
  Verhalten wenig; trotzdem vor Push 1 den Block `-k airline` und
  `RUN_MEINCHAMAELEON_EVAL=1` einmal laufen lassen. Danach kurz auf die
  Live-Chats im Dashboard schauen.
- **Push 2, Tool-Nutzung + Prompt:** erst, wenn in der verschränkten Messung
  keine der beiden alten Suiten schlechter ist als mit dem alten Prompt und
  der Längen-Check nicht gekippt ist.
  **Korrigiert 2026-09-23:** die alte Fassung verlangte „in der Quote
  (`EVAL_N=3`)". Das war nie ausführbar — `EVAL_N` existiert ausschließlich in
  `tests/test_kundenfeedback_eval.py:88`; `test_agentur_faq.py` und
  `test_meinchamaeleon_faq.py` kennen keine Wiederholung und liefern nur
  rot/grün. Maßgeblich ist jetzt das gepaarte Ergebnis je Fall (b/c), nicht
  eine Quote.
  Dazu eine Warnung aus `TODOS.md:79-91`: ein voller `RUN_AGENTUR_EVAL=1`-Lauf
  (32 Fälle) ist selbst der dokumentierte Auslöser für die Empty-Reply-Kippe,
  und sie trifft wechselnde Fälle. Verschränkt sind es 64 Aufrufe — die Suite
  MUSS geteilt werden, sonst löst das Tor auf Rauschen aus und wird beim ersten
  Mal von Hand übergangen. Danach ist es kein Tor mehr.

### Welle 3 — Orchestrator

1. Vorher/Nachher-Tabelle aller Eval-Blöcke in diesen Plan schreiben.
2. B1 mit Zahlen vorlegen: bleibt `ungefragt` rot, Vorschlag Injektion
   streichen — Entscheidung beim Owner.
3. Tool-Aufrufe pro Chat stichprobenartig vorher/nachher (Overhead-Kontrolle
   für den Mehrfachabruf).
4. `TODOS.md` im Hauptverzeichnis: „Option vs. Reservierung" abhaken; neu
   eintragen: F3-Rückfrage, `agenturdaten.vorgangsnummern` ungecacht, und
   **Vergleichstabelle je Land (Review D13)** — beim Sitemap-Sync je Land eine
   kleine Tabelle (Reise, Tage, Airline, ggf. Orte) vorberechnen, damit „ohne
   Sansibar"/„14 Tage" ein Nachschlagen wird statt 78.000 Zeichen Lesen.
   Auslöser (umgeschrieben, Review R3/D3 2026-09-24): mindestens 2 der 8
   `filter`-Fälle sind auch mit neuem Prompt rot, reine Satzzahl-Fälle nicht
   mitgezählt, ODER die Tool-Aufrufe je Chat steigen spürbar. Die Tabelle
   weist je rotem Fall die Befund-Art aus (Länge / Inhalt / Link / leer). Offen dabei: „Orte im Verlauf"
   ist unscharf (Ortsliste oder zweites Modell nötig).
5. `/review` über den Gesamtdiff. **Kein Push** — der Owner entscheidet.

### Ergebnis der verschränkten Messung (2026-09-24, T20)

Treiber `tests/eval_paare.py` (`aba2ce9`), Protokoll
`data/eval_paare/2026-09-24.jsonl`, 15 Prozesse, 18:06–18:47 UTC, 60 s Pause.
Alt = Vorlage aus `a84f3f1` (Eltern von W3-Commit 1), Neu = `4825b64`
(W3-Commit 4). Tausch-Nachweis: Prompt alt `c00b5c7abf06…`, neu
`d43ba884bf05…` — verschieden, wie verlangt. `EVAL_N` = 1, das Paar ist die
Wiederholung.

| Block | Fälle | b (neu besser) | c (neu schlechter) | beide grün | beide rot | ausgefallen | leer alt/neu | Fehler alt/neu | Median s alt/neu | Tools Ø alt/neu | p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `filter` | 8 | 2 | 2 | 0 | 3 | 1 | 0/0 | 1/1 | 4.5/4.1 | 2.4/7.6 |  |
| `airline` | 31 | 5 | 0 | 26 | 0 | 0 | 0/0 | 0/0 | 2.0/1.9 | 3.9/3.2 | 0.062 |
| `nichtangeboten` | 4 | 0 | 1 | 3 | 0 | 0 | 0/0 | 0/0 | 2.6/1.3 | 1.5/0.0 |  |
| `ungefragt` | 4 | 1 | 1 | 2 | 0 | 0 | 0/0 | 0/0 | 2.9/2.7 | 4.5/3.8 |  |
| `fachwissen` | 12 | 1 | 1 | 9 | 1 | 0 | 0/0 | 0/0 | 1.8/1.2 | 1.2/0.8 |  |
| `erfinden` | 4 | 1 | 0 | 1 | 2 | 0 | 0/0 | 0/0 | 1.4/1.8 | 0.8/1.5 |  |
| `flug` | 2 | 1 | 0 | 0 | 1 | 0 | 0/0 | 0/0 | 2.2/3.1 | 3.0/3.0 |  |
| Agentur | 31 | 1 | 0 | 29 | 0 | 1 | 1/1 | 0/0 | 1.7/1.7 | 0.1/0.0 | 1.000 |
| MeinChamäleon | 13 | 0 | 2 | 10 | 1 | 0 | 0/0 | 0/0 | 1.9/1.7 | 0.2/0.0 |  |

Summe: b = 12, c = 7 über 109 Paare. Leerantworten: ein einziges Paar
(Agentur `test_fluganfrage_mit_reise_nennt_die_durchwahl`, beide Seiten leer
auch nach Wiederholung) — die Kippe hat bei ≤ 11 Fällen je Prozess nicht
zugeschlagen.

**c-Fälle (alt grün, neu rot), mit Befund-Art:**
- `filter-namibia-teenager-14-tage` — Inhalt: nennt Sossusvlei nicht.
- `filter-peru-ohne-amazonas-schwester` — Inhalt: verlinkt `/Amerika/Peru/Pachamama-ALL` (mit Amazonas).
- `nichtangeboten-Nigeria` — Inhalt: Absage-Muster greift nicht (W3-Sichtprüfung sah dasselbe bei Afghanistan: „kein Reiseziel, das Chamäleon anbietet" ist eine richtige Absage, die `_ABSAGE` nicht kennt — Eval-Lücke, nicht Prompt).
- `ungefragt-windhoek-sehenswuerdigkeiten` — Inhalt: „Mückenspray", „Geld".
- `fachwissen-hatari-zusatztage` — Inhalt: kein Erlebnisberater, dazu „leider".
- MeinChamäleon `rail-and-fly` — Link. **Nachmessung: alt 2/2, neu 2/2 grün → Rauschen.**
- MeinChamäleon `unterlagen-ohne-vrrvorgang-testkunde` — Link (nur `/MeinChamaeleon`, Punkt 14). **Nachmessung:** alt 3/3 grün; neu insgesamt 2 von 7 rot. Je W3-Zwischenstand je 4 Läufe: `d4771ad` 0/4 rot, `23567b5` 2/4, `07c7119` 1/4, `4825b64` 0/4. Rot erst ab Regelgruppe 2 (FAQ-Nutzung), bei dieser Fallzahl aber nicht sauber zuzuordnen.

**b-Fälle:** `filter-namibia-14-tage`, `filter-italien-ohne-rom-schwester`,
fünf Airline-Länder (Argentinien-Chile, Madagaskar, Mexiko, Mongolei,
Namibia-Südafrika), `ungefragt-marokko-infos`, `fachwissen-termine-2029-schwester`,
`erfinden-passname`, `flug-non-stop`, Agentur `test_ansprechpartner_fragt_zurueck`.

**Push-2-Tor (c-Liste der beiden alten Suiten):** Agentur leer. MeinChamäleon
ein Fall nach Nachmessung: `unterlagen-ohne-vrrvorgang-testkunde`, neu 2/7 rot
gegen alt 0/3. **Nicht begründet — das Tor ist damit nicht offen; Entscheidung
beim Owner.** Die Satzzahl-Regel ist nicht gekippt (kein c-Fall mit Befund-Art
Länge).

**Welle 3 Punkt 2 — B1 mit Zahlen.** `ungefragt-marokko-infos` war in der
Kampagne alt rot („Ramadan") und neu grün (b). In der W3-Sichtprüfung war er
mit neuem Prompt dreimal rot, ebenfalls „Ramadan". Neu also 1 von 4 grün.
Ramadan steht nicht auf der Kasbah-Seite, sondern in der automatisch
eingehängten Marokko-FAQ (`FAQ_Afrika.csv:104`, `agent.py:205`). Die
Prompt-Regel allein reicht nicht; Vorschlag wie geplant: Injektion streichen,
Länder-FAQ nur noch über `country_faq_tool`. Entscheidung beim Owner.

**B1 gemessen (2026-09-25) — Injektion bleibt.** Schalter: ein pytest-Plugin
setzt `agent.laender_faqs = {}` (nur die Erkennung; `country_faq_tool` gleich in
beiden Armen), beide Arme je Block direkt hintereinander.

| Block | mit Injektion | ohne |
|---|---|---|
| Länder-Fälle `test_faq.py` (135, je 1 Lauf) | 119/135 | 41/135 |
| `ungefragt-marokko-infos` (EVAL_N=3) | 0/3 | 1/3 |
| `ungefragt-vietnam-infos-schwester` | 3/3 | 1/3 |
| Windhoek, Hanoi | 3/3, 3/3 | 3/3, 3/3 |
| `fachwissen-hatari` (Kontrolle, nennt kein Land) | 0/3 | 3/3 |

Ohne Injektion ruft Leon `country_faq_tool` kaum auf, sucht im Website-Tool,
findet nichts und verweist auf die Reiseseite — teils falsch (Australien
Eigenanreise: „musst dich nicht kümmern“). Blöcke dauern 2–3× so lang.
Hatari bekommt in beiden Armen denselben Prompt und schwankt trotzdem 0/3 ↔
3/3: bei N=3 ist das Rauschen so groß wie der Marokko-Effekt.
Entscheidung (Owner): Injektion bleibt; der eingehängte Block sagt vor und
nach den Einträgen, dass sie nur auf ausdrückliche Frage zum Thema gelten,
nie bei allgemeinen Fragen zum Land oder zur Reise.
Nachmessung mit der Regel: `ungefragt-marokko-infos` 10/10 (3–4 Sätze, kein
Ramadan), `ungefragt-vietnam-infos-schwester` 10/10, Länder-Fälle 117/135
(10 neu rot, 8 neu grün; alle 18 roten Antworten beantworten die gestellte
Frage — Rauschen und Schlüsselwort-Wortlaut, keine unterdrückte Antwort).

**Welle 3 Punkt 3 — Tool-Aufrufe.** Nur `filter` steigt spürbar (Ø 2,4 → 7,6
je Chat), die Mediandauer bleibt gleich (4,5 → 4,1 s). Alle anderen Blöcke
flach oder leicht fallend.

**Welle 3 Punkt 4 — D13-Auslöser: erfüllt.** Neu rot in `filter`: beide
Tansania-Fälle, Südafrika/Gartenroute, Namibia-Teenager, Peru — alle Inhalt,
keiner nur Satzzahl. Dazu die Tool-Aufrufe. Eingetragen in `TODOS.md`.

**Nicht vom Prompt lösbar, gefunden bei W3:**
- F1: Länderseiten (`/Amerika/Costa-Rica`, `/Afrika/Namibia`) tragen keine
  Erlebnisberater*in; `erlebnisberater_tool` liefert die Zentrale.
  `erfinden-ansprechpartner-*` bleiben beidseitig rot, bis feststeht, woher
  der Berater eines Landes kommt.
- `filter-marokko-10-tage-schwester`: keine Marokko-Reise hat ≤ 10 Tage, die
  Ableitung trennt nicht → beidseitig „Fehler". Fall umformulieren (z. B. 12
  Tage) oder streichen.
- Es gibt keine allgemeine Safari-Übersicht (`/Safari` ist 404); das neue
  Stil-Beispiel „erste Safari" verlinkt `/Namibia-Safari`, `/Botswana-Safari`,
  `/Safari-Suedafrika`.

### Nicht Teil dieses Plans

Reisebüro-Suche nach PLZ, Retry-Logik bei leeren Antworten, Umbau der
Länder-Erkennung, Cache für `agenturdaten`, alles in `cham-chatbot`.

---

## Review-Entscheidungen (/plan-eng-review, 2026-09-21)

Alle vom Owner einzeln entschieden; die Pakettexte oben sind bereits angepasst.

| # | Thema | Entscheidung |
|---|---|---|
| D1 | Umfang | Alles in einem Plan, parallel durch Subagenten |
| D2 | Rollout | W3: ein Commit je Regelgruppe; zwei Pushes (risikoarm zuerst, Tool + Prompt danach) |
| D3 | Reise-Links bei TourOne-Ausfall | URL-Nummer bleibt Rückfall wie heute; API nur ohne Nummer in der URL; Regressionstests Pflicht |
| D4 | Ort der Auflösung | Im Aufrufer (`agent.py`); `format_system_prompt` bleibt reine Textfunktion |
| D5 | Schneidefunktion | `seiten_abschnitt` vorab in Welle 0, von Tool und Eval geteilt |
| D6 | Eval-Umfang | Neu + Agentur + MeinChamäleon, vorher und nachher |
| D7 | Testlücken | Längen-/„leider"-/„FAQ"-Check in jedem Eval-Fall + sieben Unit-Tests |
| D8 | Tool-Größe | Abschnitt Pflicht ab 2 Pfaden, unbekannter Abschnitt → Anweisung, Gesamtdeckel 100.000 Zeichen |
| D9 | Seiten-Cache | Markdown statt rohem HTML |
| D10 | Reise-Auflösung | Neue reine Funktion `naechste_offene_reise` (nur offene Reisen, mit Label) |
| D11 | Timeout/Cache-Schlüssel | Schlüssel nur kunden_id; Gesamt-Timeout 1 s außen per `gevent.Timeout` |
| D12 | Tool-Schnitt | Ein-Seiten-Funktion bleibt unverändert, `website_tool_multi` obendrauf |
| D13 | Vergleichstabelle je Land | Nicht jetzt; TODO mit Auslöser |
| D14 | Worker-Blockade | Zwischen den Seiten abgeben (`gevent.sleep(0)`) |
| D15 | Eval-Statistik | ~~`EVAL_N=3` für Ausgangs- und Schlussmessung~~; Schwesterfälle bleiben. **Korrigiert 2026-09-23:** `EVAL_N=3` war für zwei der drei Suiten nie verfügbar (der Schalter existiert nur in der neuen Suite) und ließ sich mit „blockweise ≤25 Aufrufe" ohnehin nicht vereinbaren — `airline` wären 84 Aufrufe am Stück gewesen. Die Streuung dämpft jetzt die Paarung statt der Wiederholung |
| D16 | Zirkelschluss im Eval | Feste Ankerwerte mit Datum; leere/volle Erwartungsmenge ist rot |
| D17 | Stil-Beispiel „erste Safari" | Umschreiben: Rückfrage statt Etosha |
| D18 | Längenregel gegen mehrteilige Pflichtantworten | **Fallengelassen (Owner, 2026-09-23).** Die Regel bleibt wie sie ist, W3 startet damit. Nicht erneut aufrollen — nur bei einem konkreten Fall, der dagegenläuft, wieder vorlegen |
| D19 | Cache-Inhalt | Nur die Buchungsliste, `maxsize=512` |
| D20 | Messzeitpunkt | ~~W1 schreibt nur die Fälle; Ausgangsmessung fährt der Orchestrator allein nach Welle 1~~ — **gedreht 2026-09-23:** keine getrennte Ausgangsmessung, stattdessen EINE verschränkte Kampagne nach W3 (siehe „Verschränkte Messung"). Grund: zwei Kampagnen vermengen die Prompt-Änderung mit Zeit, Last und Zwischen-Commits; `7c6ed5e` hat den `airline`-Nenner am selben Tag verschoben und einen bereits gefahrenen Vorher-Wert entwertet |

## Was es schon gibt (und wie der Plan es nutzt)

| Vorhanden | Nutzung |
|---|---|
| `chamaeleon_website_tool_base` (`agent_base.py:282`) | Bleibt der Baustein; `website_tool_multi` setzt darauf auf (D12) |
| `cachetools.func.ttl_cache` (dreimal in `agent_base.py`) | Buchungs-Cache und Markdown-Cache; kein neuer Mechanismus |
| `zeit_marker`, `select`, `heute_berlin` (`kundendaten.py`) | Grundlage für `naechste_offene_reise` (D10) |
| `_vrrvorgang_from_url` (`agent_base.py`) | Bleibt erster Rang und Offline-Rückfall für die Links (D3) |
| `kunden_auth.resolve(session_id)` (`app.py:117`) | Liefert dem Vorwärm-Thread die Kundennummer (D11) |
| `_startup_warm` (`app.py:435`) | Muster für den Daemon-Thread |
| `REISEINFO_FETCH_PARALLEL` + `ThreadPoolExecutor` | Muster für den parallelen Seitenabruf |
| `erlebnisberater_tool` / `berater_von_seite` (seit 18.09.) | Ziel aller „→ Erlebnisberater"-Antworten; bleibt unberührt |
| `keyword_matches`, `telefonnummern` (`tests/test_agentur_faq.py`) | Werden importiert, nicht kopiert |
| Länderseiten `/Kontinent/Land` | Liefern die Dauer aller Reisen eines Landes in einem Abruf |
| `reiseinfo_vorgang` | Wird NICHT wiederverwendet (fällt auf vergangene Reisen zurück, kein Label) — bleibt für das Reiseinfo-Tool |

## Datenfluss

```
ÖFFENTLICHER CHAT — Frage mit hartem Kriterium („Tansania ohne Sansibar")

  Kunde ──► call_stream ──► Gemini ──► chamaeleon_website_tool(url_paths=[6 Pfade], abschnitt="reiseverlauf")
                                              │
                                              ▼
                                   website_tool_multi
                                     ├─ >8 Pfade? ──────────────► Fehlermeldung ans Modell
                                     ├─ >1 Pfad & abschnitt=""? ─► Anweisung mit gültigen Namen     (D8)
                                     ├─ unbekannter Abschnitt? ──► dieselbe Anweisung               (D8)
                                     └─ je Pfad (parallel holen, Eingabereihenfolge):
                                          chamaeleon_website_tool_base(pfad)   ← Markdown-Cache 24 h (D9)
                                          seiten_abschnitt(md, abschnitt)      ← Welle 0 (D5)
                                            └─ None? → Hinweis „Abschnitt fehlt auf dieser Seite"
                                          gevent.sleep(0)                      ← andere Chats kommen dran (D14)
                                     └─ Summe > 100.000 Zeichen? → anteilig kürzen + Marker          (D8)
                                              │
                                              ▼
                                   Gemini liest Reiseverläufe ──► 2–4 Sätze + Link


MEINCHAMÄLEON — welche Reise ist gemeint?

  Widget ──► POST /kunde/auth ──► authenticate ──► 200 sofort
                                       │
                                       └─(Daemon-Thread) resolve(session_id) → _buchungen_roh(kunden_id)
                                                                                   └─ füllt ttl_cache (10 min, nur „buchungen", maxsize 512)

  Kunde ──► call_stream
              ├─ VRRVORGANG formal gültig in der URL? ── ja ──► reise_vorgang = URL-Nummer      (kein Abruf, D3)
              └─ nein ─► with gevent.Timeout(1.0):
                           _buchungen_roh(kunden_id)  ── Treffer ─► naechste_offene_reise(...)  (D10)
                             ├─ wirft / Zeit läuft ab ────────────► ("", "")   Chat läuft weiter
                             └─ keine offene Reise ───────────────► ("", "")
              ▼
           format_system_prompt(..., reise_vorgang, reise_label)     ← reine Textfunktion (D4)
              ├─ reise_vorgang gesetzt → vier fertige Links (+ „nächste Reise (Ziel, Datum)")
              └─ leer                  → nur Übersichts-Links
```

Inline-Diagramme im Code: die Rangfolge URL → API → leer als Kommentar über
der Auflösung in `agent.py`; die Schranken-Reihenfolge als Kommentar über
`website_tool_multi`.

## Fehlermodi

| Codepfad | Realistischer Ausfall | Test | Behandlung | Was der Kunde sieht |
|---|---|---|---|---|
| `website_tool_multi` | Eine von sechs Seiten antwortet mit 500 | ja (W2) | Fehler bleibt lokal | Antwort aus fünf Seiten |
| `website_tool_multi` | Modell vergisst den Abschnitt bei acht Pfaden | ja (W2) | Anweisung statt Seiten | Eine Tool-Runde mehr, sonst nichts |
| `seiten_abschnitt` | Website benennt „Reiseverlauf" um | ja (Ankerwerte W1, Unit Welle 0) | `None` → Hinweis je Seite | Leon verweist auf die Seite statt zu raten; Ankerwert-Test wird rot |
| Markdown-Cache | Seite ändert sich, Cache hält 24 h alten Stand | nein | wie heute (HTML-Cache hatte dieselbe TTL) | Bis 24 h alte Angaben — unverändert gegenüber heute |
| `_buchungen_roh` | TourOne-Timeout | ja (W5) | wirft, wird nicht gecacht | Übersichts-Links bzw. URL-Links; Chat läuft |
| Auflösung im Aufrufer | TourOne hängt 10 s | ja (W5) | `gevent.Timeout(1.0)` | Höchstens 1 s Verzögerung, einmalig |
| Vorwärm-Thread | Exception im Thread | ja (W5) | nur Log, ohne Kundennummer | Nichts; erste Nachricht holt selbst |
| Vorwärmen | Mehr als ein Worker | nein | Kommentar zur Annahme | Erste Nachricht ~0,3 s langsamer, sonst nichts |
| `naechste_offene_reise` | Kunde hat nur vergangene Reisen | ja (W5) | leer | Übersichts-Links statt Vorjahresreise |
| `termine_tool_base` | Jahr noch nicht veröffentlicht | ja (W7) | neuer Text | „noch nicht veröffentlicht" statt „keine freien Termine" |
| Prompt (W3) | Neue Regel verschlechtert Agentur-Routing | ja (D6, gepaart alt/neu, D20) | Push 2 gesperrt, `git revert` je Regelgruppe | Nichts, weil nicht deployt |
| Dashboard-Links (W6) | Link mit anderem Schema (`mailto:`, `tel:`) | manuell | Protokollprüfung bleibt | Link unverändert |

Kein Pfad ist zugleich ungetestet, unbehandelt UND still: **0 kritische Lücken.**
Einzige bewusst hingenommene Stille: der 24-h-Seiten-Cache, unverändert gegenüber heute.

## Parallelisierung

| Schritt | Module | Hängt ab von |
|---|---|---|
| Welle 0 (Plan + `seiten_abschnitt`) | `docs/`, `agent_base.py`, `tests/` | — |
| W1 Evals | `tests/` | Welle 0 |
| W2 Website-Tool | `agent_base.py` (Tool-Region), `agent.py` (Wrapper), `tests/` | Welle 0 |
| W4 FAQs | `faqs/` | — |
| W5 Kunden-Reise | `kundendaten.py`, `app.py`, `agent.py` (call_stream), `agent_base.py` (Kunden-Block) | — |
| W6 Dashboard | `static/dashboard/` | — |
| W7 Termine | `agent_base.py` (`termine_tool_base`), `tests/` | — |
| Ausgangsmessung | — (nur lesen, live) | W1 |
| W3 Prompt | `agent_base.py` (`system_prompt_template`) | W2, W4, W5, W7 |

- **Bahn A:** W1 (nur Fälle; keine Ausgangsmessung, D20)
- **Bahn B:** W2 · **Bahn C:** W4 · **Bahn D:** W5 · **Bahn E:** W6 · **Bahn F:** W7
- **Danach seriell:** Zusammenführen → Push 1 → W3 → verschränkte Kampagne → Push 2
- **Konfliktflaggen:** W2, W5, W7 (und später W3) ändern alle `agent_base.py`;
  W2 und W5 ändern beide `agent.py`. Die Regionen sind disjunkt
  (Funktionsnamen, nicht Zeilen, sind maßgeblich), zusammengeführt wird per
  Cherry-pick in fester Reihenfolge. Welle 0 verschiebt Zeilennummern in
  `agent_base.py` — Agenten orientieren sich an den Funktionsnamen.

6 parallele Bahnen, danach 5 serielle Schritte.

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific
finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — Welle 0 — `seiten_abschnitt` + Unit-Test vorab committen
  - Surfaced by: Code-Qualität D5 — Tool und Eval würden den Schnitt doppelt bauen
  - Files: `agent_base.py`, `tests/test_seiten_abschnitt.py`
  - Verify: `pytest tests/test_seiten_abschnitt.py -q`
- [ ] **T2 (P1, human: ~1h / CC: ~5min)** — W2 — Ein-Seiten-Funktion unverändert lassen, `website_tool_multi` obendrauf
  - Surfaced by: Außenstimme 8 / D12 — `berater_tool_base` (`agent_base.py:733`) und `tests/test_berater.py:79` hängen an der alten Signatur
  - Files: `agent_base.py`, `agent.py`, `tests/test_website_tool.py`
  - Verify: `pytest tests/test_berater.py tests/test_travel_index.py tests/test_website_tool.py -q`
- [ ] **T3 (P1, human: ~1h / CC: ~10min)** — W2 — Größenschranke: Abschnitt Pflicht ab 2 Pfaden, unbekannter Abschnitt → Anweisung, Deckel 100.000 Zeichen
  - Surfaced by: Performance D8 — acht ganze Seiten ≈ 416.000 Zeichen in einem Tool-Ergebnis
  - Files: `agent_base.py`, `tests/test_website_tool.py`
  - Verify: Unit-Tests „>1 Pfad ohne Abschnitt", „unbekannter Abschnitt", „Deckel greift"
- [ ] **T4 (P2, human: ~2h / CC: ~15min)** — W2 — Markdown statt HTML cachen, Ausgabe zeichengleich
  - Surfaced by: Performance D9 — `agent_base.py:268` cacht 218–374 KB je Seite, bis ~150 MB
  - Files: `agent_base.py`, ggf. `recommendations.py`
  - Verify: Test „Ausgabe zeichengleich wie vorher" + „zweiter Aufruf trifft Cache"
- [ ] **T5 (P2, human: ~30min / CC: ~5min)** — W2 — nach jeder Seite abgeben (`gevent.sleep(0)`), Zeiten berichten
  - Surfaced by: Außenstimme 4 / D14 — 153 ms Parse je Seite blockiert den einzigen Worker
  - Files: `agent_base.py`
  - Verify: Parse-Zeit je Seite und Gesamtzeit kalt/warm im W2-Bericht
- [ ] **T6 (P1, human: ~2h / CC: ~10min)** — W5 — `naechste_offene_reise` statt `reiseinfo_vorgang`
  - Surfaced by: Außenstimme 1 / D10 — `eigene[0]` fällt auf vergangene Reisen zurück, kein Label
  - Files: `kundendaten.py`, `tests/test_kundendaten.py`
  - Verify: Unit-Tests 0/1/2 offene, nur vergangene, laufende Reise
- [ ] **T7 (P1, human: ~2h / CC: ~10min)** — W5 — Cache-Schlüssel nur kunden_id, `gevent.Timeout(1.0)` außen, nur `buchungen`, `maxsize=512`
  - Surfaced by: Außenstimme 3 / D11, D19 — Timeout-Argument spaltet den Cache; ganze Adresse läge im Speicher
  - Files: `kundendaten.py`, `agent.py`, `app.py`
  - Verify: Test „vorwärmen, dann lesen = ein Abruf"; Test „Ausfall nicht gecacht"; Test „Ablauf nach TTL"
- [ ] **T8 (P1, human: ~1h / CC: ~5min)** — W5 — URL-Nummer als erster Rang und Offline-Rückfall + Regressionstests
  - Surfaced by: Architektur D3 — Plan hätte Links bei TourOne-Ausfall entfernt, die es heute gibt
  - Files: `agent.py`, `agent_base.py`, `tests/test_kundendaten.py`
  - Verify: Tests „Abruf wirft + URL-Nummer → Links" und „URL-Nummer → kein Abruf"
- [ ] **T9 (P2, human: ~1h / CC: ~5min)** — W5 — Auflösung im Aufrufer, `format_system_prompt` bekommt nur `reise_vorgang`/`reise_label`
  - Surfaced by: Architektur D4 — Textfunktion bekäme Netzzugriff und die kunden_id
  - Files: `agent.py`, `agent_base.py`
  - Verify: `pytest tests/test_general.py tests/test_kunden_auth.py -q`
- [ ] **T10 (P1, human: ~3h / CC: ~20min)** — W1 — Querschnitts-Check, Schwesterfälle, Ankerwerte, `EVAL_N`
  - Surfaced by: Tests D7, Außenstimme 6/7 / D15, D16 — Länge ungemessen, Einzelläufe sind Rauschen, Eval zirkulär
  - Files: `tests/test_kundenfeedback_eval.py`, `tests/test_meinchamaeleon_faq.py`
  - Verify: ohne Schalter alles `skipped`; Ankerwert-Test grün; `EVAL_N=3` meldet Quoten
- [ ] ~~**T11** — Ausgangs- und Schlussmessung mit `EVAL_N=3`~~ — **ersetzt durch T15–T20 (D20 gedreht, 2026-09-23/24)**
- [x] **T12 (P1, human: ~30min / CC: ~5min)** — W3 — ein Commit je Regelgruppe; Stil-Beispiel „erste Safari" umschreiben
  - Surfaced by: Architektur D2, Außenstimme 9a / D17 — ein Commit für neun Regeln; Beispiel macht Etosha ohne Prüfung vor
  - Files: `agent_base.py`
  - Verify: vier Commits in `git log`; Eval-Block `filter` vorher/nachher
- [ ] **T13 (P1, human: ~1h / CC: ~5min)** — Orchestrator — zwei Pushes, Push 2 nur bei nicht schlechteren Alt-Suiten
  - Surfaced by: Architektur D2 — ein Push für alles macht einen Rückschritt unzuordenbar
  - Files: —
  - Verify: Owner-Go je Push; Blick auf Live-Chats nach Push 1
- [x] **T14 (P3, human: ~15min / CC: ~2min)** — Orchestrator — TODO „Vergleichstabelle je Land" mit Auslöser eintragen
  - Surfaced by: Außenstimme 5 / D13
  - Files: `TODOS.md`
  - Verify: Eintrag mit Auslöser und offener Frage „Orte im Verlauf"

_Nachtrag /plan-eng-review „Verschränkte Messung", 2026-09-24:_

- [x] **T15 (P1, human: ~2h / CC: ~15min)** — Treiber — alte Vorlage aus Git schneiden und im `agent_base`-Namensraum auswerten; Eltern-SHA als Konstante; Unit-Test Schneiden == aktuelles Template
  - Surfaced by: Außenstimme 1 / R6 (D5) — Quelle der alten Vorlage war unbenannt
  - Files: `tests/eval_paare.py` (neu), `tests/test_eval_paare.py` (neu)
  - Verify: `pytest tests/test_eval_paare.py -q`
- [x] **T16 (P1, human: ~1h / CC: ~10min)** — Treiber — Vorbedingung: beide Vorlagen rendern, `alt != neu`, SHA-256 + Commit ins Protokoll, sonst Abbruch; Unit-Test Abbruch bei Gleichheit
  - Surfaced by: Tests R5 (D4) — b = c = 0 wäre von „W3 wirkt nicht" nicht unterscheidbar
  - Files: `tests/eval_paare.py`, `tests/test_eval_paare.py`
  - Verify: Unit-Test „identische Vorlagen → SystemExit vor erstem Aufruf"
- [x] **T17 (P1, human: ~3h / CC: ~20min)** — Treiber — Fallquellen: Listen importieren, `airline`/`nichtangeboten` mit Modul-Helfern nachbauen und vorwärmen, Einzelfunktionen direkt (parametrisierte über Modul-Listen, skipif nur mit Nummer); vier Ausgänge je Seite; Paare mit leer/Fehler aus b/c
  - Surfaced by: Code Quality R2 (D2), Außenstimme 3/4 / R8 (D7), R9 (D8)
  - Files: `tests/eval_paare.py`, `tests/test_eval_paare.py`
  - Verify: Unit-Tests mit gefälschtem `call`: AssertionError → rot, Skipped → Fehler, Fallback-Text → leer + eine Wiederholung; Fallzählung Agentur = 33
- [x] **T18 (P1, human: ~1h / CC: ~10min)** — Treiber — `agent.call_stream` zählend einwickeln; Tool-Aufrufe, Wanddauer, Zeitstempel, Prozess-Nr. je Seite protokollieren
  - Surfaced by: Außenstimme 2 / R7 (D6), Außenstimme 6+8 / R11 (D10)
  - Files: `tests/eval_paare.py`, `tests/test_eval_paare.py`
  - Verify: Unit-Test mit gefälschtem Stream (2 `tool_call`-Events → 2)
- [x] **T19 (P1, human: ~2h / CC: ~15min)** — Treiber/Bericht — Auswertung als Fall-Listen je Block (b, c, c-Fälle mit Befund-Art), McNemar nur für `airline` und Agentur; D13-Auslöser auf ganze Fälle; Befund-Art-Spalte
  - Surfaced by: Tests R3 (D3), Außenstimme 5 / R10 (D9)
  - Files: `tests/eval_paare.py`, `docs/kundenfeedback-2026-09-plan.md` (Tabelle)
  - Verify: Unit-Test der Auswertung auf einem synthetischen Protokoll (b/c/leer/Fehler korrekt getrennt)
- [x] **T20 (P2, human: ~1 Tag / CC: ~1.5h Laufzeit)** — Orchestrator — Kampagne fahren: ≤ ~12 Fälle je Prozess, 60 s Pause (`EVAL_PAUSE_S`), Protokoll und Tabelle in den Plan
  - Surfaced by: Architektur D20, Außenstimme 6 / R12 (D11)
  - Files: `docs/kundenfeedback-2026-09-plan.md`
  - Verify: Tabelle je Block mit b, c, leer, Fehler, Median-Dauer, Tool-Aufrufe; Hashes beider Vorlagen im Kopf


## Decision ledger (/plan-eng-review „Verschränkte Messung", 2026-09-24)

### R1: Leerantworten in der gepaarten Auswertung
Finding: R1, P1, confidence 9/10, `agent.py:426` (`if not reply.strip(): reply = EMPTY_ANSWER_FALLBACK`) und `TODOS.md:83-87` („trifft wechselnde Fälle, am häufigsten den teuersten"), Reviewer: plan-eng-review (Architektur)
Plan baseline: Abschnitt „Verschränkte Messung" zählt je Block b/c aus rot/grün; eine Leerantwort ist dort nicht erwähnt und zählt still als rot auf ihrer Seite. Keine frühere Freigabe.
Runtime evidence: `agent.call` liefert bei leerer Modellantwort nach ≤3 Versuchen/6 s den Fallback-Text (`agent.py:129-137`, `:426`). Kein Eval prüft darauf (nur `tests/test_empty_reply.py`). Die Kippe setzt bei ~30 Aufrufen am Stück ein und trifft den teuersten Fall (zwei Züge plus Tool). W3 macht die neue Seite jedes Paares teurer (Mehrseiten-Abruf) — die Asymmetrie ist systematisch, nicht zufällig.
Comparison grid:

| Choice | Current | A | B | C |
|---|---|---|---|---|
| R1 Leerantwort im Paar | zählt still als rot | Signatur je Aufruf erkennen (Fallback-Text), Paar mit ≥1 Leerseite aus b/c raus, eigene Spalte „leer alt / leer neu" je Block | wie heute: rot auf der Seite | eine Leerseite einmal sofort wiederholen, erst dann werten; Wiederholung protokollieren |
| Zusatzaufrufe | 0 | 0 | 0 | bis +1 je Leerseite |
| Sichtbar im Bericht | nein | ja, je Block | nein | ja (Anzahl Wiederholungen) |
| R2–R5 | pending | pending | pending | pending |

Question D1:
D1 — Wie zählt eine Leerantwort in der gepaarten Auswertung?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Gemini gibt unter Last manchmal gar keine Antwort; der Bot sagt dann „Entschuldige, da ist mir gerade keine Antwort gelungen." Im Paar-Vergleich alt/neu zählt das heute stillschweigend als „neu ist schlechter", und es trifft bevorzugt die teure neue Seite. Die Messung würde W3 systematisch schlechter aussehen lassen, als es ist.
Stakes if we pick wrong: Push 2 hängt am gepaarten Ergebnis; ein Leerantwort-Artefakt kann das Tor auslösen oder es beim ersten Mal von Hand übergehen lassen, danach ist es kein Tor mehr.
Recommendation: A, weil sie null Zusatzaufrufe kostet, das Paar-Prinzip erhält und die Störung sichtbar macht statt sie zu verstecken.
Completeness: A=10/10, B=4/10, C=7/10
Pros / cons:
A) Signatur erkennen, Paar ausweisen (recommended)
  ✅ Fallback-Text ist ein fester String (`agent.py:135`), Erkennung ist ein Substring-Vergleich ohne Codeänderung am Bot
  ✅ b/c bleiben sauber; Leerantworten je Seite werden zur eigenen Zahl und beantworten die TODOS-Frage gleich mit
  ❌ Blöcke mit vielen Leerpaaren verlieren Fälle; bei `filter` (8) kann das die Signifikanz kosten
B) Wie heute, rot auf der Seite
  ✅ Kein Aufwand, der Treiber bleibt schlank
  ✅ Zählt so, wie der Nutzer es erleben würde
  ❌ Verzerrt systematisch gegen die neue Seite; Push-2-Tor misst dann Last statt Prompt
C) Leerseite einmal sofort wiederholen
  ✅ Paar bleibt vollständig, keine Fallverluste
  ✅ Wiederholung ist im selben Prozess, Reihenfolge bleibt benachbart
  ❌ Zusatzaufrufe rücken den Block näher an die ~25er-Kippe, die die Leerantwort überhaupt auslöst
Net: Sichtbar machen und ausklammern (A) gegen bequem, aber verzerrt (B) gegen vollständig, aber lastverstärkend (C).
Header: Leerantwort im Paar
Options:
A) Signatur erkennen, Paar ausweisen (recommended)
✅ Fallback-Text ist ein fester String (`agent.py:135`), Erkennung ist ein Substring-Vergleich ohne Codeänderung am Bot. ✅ b/c bleiben sauber; Leerantworten je Seite werden zur eigenen Zahl und beantworten die TODOS-Frage gleich mit. ❌ Blöcke mit vielen Leerpaaren verlieren Fälle; bei `filter` (8) kann das die Signifikanz kosten.
B) Wie heute, rot auf der Seite
✅ Kein Aufwand, der Treiber bleibt schlank. ✅ Zählt so, wie der Nutzer es erleben würde. ❌ Verzerrt systematisch gegen die neue Seite; Push-2-Tor misst dann Last statt Prompt.
C) Leerseite einmal sofort wiederholen
✅ Paar bleibt vollständig, keine Fallverluste. ✅ Wiederholung ist im selben Prozess, Reihenfolge bleibt benachbart. ❌ Zusatzaufrufe rücken den Block näher an die ~25er-Kippe, die die Leerantwort überhaupt auslöst.

State: approved
Actual answer: C) Leerseite einmal sofort wiederholen (Owner, D1, 2026-09-24)
Accepted scope: Der Treiber erkennt eine Leerantwort am Fallback-Text (`agent.EMPTY_ANSWER_FALLBACK` im Reply) und wiederholt genau diese Seite des Paares einmal sofort, bevor gewertet wird; bleibt sie leer, zählt sie rot. Jede Wiederholung wird je Block protokolliert (Anzahl leer alt / leer neu) und steht in der Vorher/Nachher-Tabelle. Die Wiederholungen zählen zur Aufruf-Obergrenze je Prozess (~25) — die Blockteilung rechnet mit Fällen × 2 plus Reserve. Kein Eingriff in `agent.py`.
History: none

### R2: Einzelfunktionen der alten Suiten paaren
Finding: R2, P2, confidence 9/10, `tests/test_agentur_faq.py:222-357` (neun Einzelfunktionen und eine kleine Parametrisierung neben `DONE`, Z. 208) und `tests/test_meinchamaeleon_faq.py:140-191` (drei neben `URL_CASES`, Z. 92), Reviewer: plan-eng-review (Code Quality)
Plan baseline: Abschnitt „Verschränkte Messung", offener Punkt 1: „diese Fälle laufen zweimal unmittelbar hintereinander in derselben Sitzung (alt, dann neu), zeitlich benachbart statt paarweise". Keine Freigabe.
Runtime evidence: Jede Einzelfunktion ruft `_frage(...)` → `agent.call` und endet in `assert` (z. B. `test_provision_bleibt_beim_vertrieb`, Z. 222-231). Sie sind ohne Fixtures aufrufbar; ein Treiber kann sie importieren, unter altem und neuem `system_prompt_template` je einmal ausführen und `AssertionError` als rot werten. Ein Umbau der Dateien ist dafür nicht nötig. Nachbarschaft (Datei zweimal fahren) bringt bei 32 Agentur-Fällen ~32 Aufrufe Abstand — genau die Spanne, über die die Empty-Reply-Kippe einsetzt (`TODOS.md:80`).
Comparison grid:

| Choice | Current | A | B | C |
|---|---|---|---|---|
| R2 Paarung der Einzelfunktionen | offen (Vorschlag Nachbarschaft) | Treiber importiert jede Testfunktion und ruft sie alt/neu direkt nacheinander; `AssertionError` = rot | Datei zweimal per pytest fahren (alt, dann neu), ~32 Aufrufe Abstand | Einzelfunktionen in Datenlisten umbauen, dann wie `DONE` paaren |
| Eingriff in W1-Dateien | keiner | keiner | keiner | ja, 12 Funktionen |
| Paarabstand | — | 1 Aufruf | ~32 Aufrufe | 1 Aufruf |
| R1 | approved (C) | fix | fix | fix |
| R3–R5 | pending | pending | pending | pending |

Question D2:
D2 — Wie werden die 12 Einzelfunktionen der alten Suiten alt/neu gepaart?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Die alten Suiten haben neben ihren Fall-Listen zwölf handgeschriebene Testfunktionen. Für die Paarung muss jede unter altem und neuem Prompt direkt hintereinander laufen. Der Treiber kann sie einfach importieren und zweimal aufrufen; die Alternative wäre, die ganze Datei zweimal zu fahren, dann liegen zwischen alt und neu ~32 Aufrufe und damit genau die Last, die Leerantworten auslöst.
Stakes if we pick wrong: Die Regressionssperre für Push 2 misst dann Last statt Prompt bei einem Drittel der Agentur-Fälle.
Recommendation: A, weil sie echte Paare ohne Eingriff in fremde Dateien liefert und der Treiber ohnehin die Fall-Listen importiert.
Completeness: A=10/10, B=5/10, C=9/10
Pros / cons:
A) Treiber ruft die Testfunktionen direkt (recommended)
  ✅ Echte Paare mit einem Aufruf Abstand, gleiche Auswertung wie für `DONE` und `URL_CASES`
  ✅ Keine Änderung an `test_agentur_faq.py` oder `test_meinchamaeleon_faq.py`, nichts kollidiert mit W1
  ❌ Der Treiber muss `AssertionError` fangen und den Funktionsnamen als Fall-ID führen (human: ~1h / CC: ~10 min)
B) Datei zweimal fahren, zeitlich benachbart
  ✅ Kein Treiber-Code für die Einzelfunktionen, pytest macht alles
  ✅ Ergebnis bleibt pro Datei lesbar wie heute
  ❌ ~32 Aufrufe Abstand zwischen alt und neu, die Kippe liegt dazwischen; kein sauberes b/c
C) Einzelfunktionen in Datenlisten umbauen
  ✅ Danach ist alles datengetrieben und einheitlich paarbar
  ✅ Zukünftige Kampagnen brauchen keinen Sonderweg
  ❌ Zwölf Funktionen mit eigener Logik (Durchwahl, Vertrieb, Rückfragen) in Daten pressen, fremde Dateien anfassen (human: ~3h / CC: ~30 min)
Net: Paare ohne Dateieingriff (A) gegen bequem, aber unpaarig (B) gegen sauber, aber Umbau fremder Dateien (C).
Header: Einzelfunktionen paaren
Options:
A) Treiber ruft die Testfunktionen direkt (recommended)
✅ Echte Paare mit einem Aufruf Abstand, gleiche Auswertung wie für `DONE` und `URL_CASES`. ✅ Keine Änderung an `test_agentur_faq.py` oder `test_meinchamaeleon_faq.py`, nichts kollidiert mit W1. ❌ Der Treiber muss `AssertionError` fangen und den Funktionsnamen als Fall-ID führen (human: ~1h / CC: ~10 min).
B) Datei zweimal fahren, zeitlich benachbart
✅ Kein Treiber-Code für die Einzelfunktionen, pytest macht alles. ✅ Ergebnis bleibt pro Datei lesbar wie heute. ❌ ~32 Aufrufe Abstand zwischen alt und neu, die Kippe liegt dazwischen; kein sauberes b/c.
C) Einzelfunktionen in Datenlisten umbauen
✅ Danach ist alles datengetrieben und einheitlich paarbar. ✅ Zukünftige Kampagnen brauchen keinen Sonderweg. ❌ Zwölf Funktionen mit eigener Logik (Durchwahl, Vertrieb, Rückfragen) in Daten pressen, fremde Dateien anfassen (human: ~3h / CC: ~30 min).

State: approved
Actual answer: A) Treiber ruft die Testfunktionen direkt (Owner, D2, 2026-09-24)
Accepted scope: Ein eigener Treiber (neue Datei unter `tests/`, kein Eingriff in `test_agentur_faq.py`, `test_meinchamaeleon_faq.py`, `test_kundenfeedback_eval.py`) importiert die Fall-Listen (`DONE`, `URL_CASES`, die Blöcke der neuen Suite) UND die zwölf Einzelfunktionen, führt jeden Fall alt/neu unmittelbar nacheinander aus, fängt `AssertionError` als rot und führt den Funktionsnamen als Fall-ID. Damit ist R4 (Treiber-Ort) entschieden: eigener Runner, weil nur er die Einzelfunktionen erreicht; ein Paar-Modus in der Eval-Datei entfällt.
History: none

### R3: 80-%-Auslöser bei acht `filter`-Fällen
Finding: R3, P2, confidence 10/10, Plan „Welle 3" Punkt 4 („Eval-Block `filter` liegt in der Schlussmessung unter 80 %") gegen die Blocktabelle (`filter` = 8 Fälle); dazu offener Punkt 3: mindestens 2 der 8 sind heute allein wegen der Satzzahl rot (gemessen 8, 7, 3, 3 Sätze gegen `MAX_SAETZE = 5`, `tests/test_kundenfeedback_eval.py:474`). Reviewer: plan-eng-review (Tests)
Plan baseline: D13 (2026-09-21): TODO „Vergleichstabelle je Land" mit Auslöser „`filter` unter 80 % ODER Tool-Aufrufe je Chat steigen spürbar". Der Prozentwert ist bei n=8 nicht erreichbar (6/8 = 75 %, 7/8 = 87,5 %). Zudem verschwindet die Quote in der gepaarten Auswertung (D15 korrigiert): es gibt nur noch rot/grün je Seite.
Runtime evidence: `FILTER` hat 8 Einträge (`tests/test_kundenfeedback_eval.py:679-747`), `querschnitt()` färbt bei > `MAX_SAETZE` rot (Z. 581). Ein Fall kann also inhaltlich richtig filtern und trotzdem rot sein.
Comparison grid:

| Choice | Current | A | B | C |
|---|---|---|---|---|
| R3 Auslöser für D13-TODO | „unter 80 %" (nicht auflösbar bei n=8) | „mindestens 2 der 8 `filter`-Fälle auch mit neuem Prompt rot, Satzzahl-only-Rot nicht mitgezählt" | `filter` auf 10 Fälle aufstocken (zwei Schwesterfälle in W1-Datei), Auslöser 8/10 | Auslöser nur über Tool-Aufrufe je Chat, Fallzahl streichen |
| Befund-Art in der Tabelle sichtbar (Länge vs. Filtern) | nein | ja, Spalte „Befund-Art" je rotem Fall | ja | nein |
| Zusatzaufrufe | 0 | 0 | +4 | 0 |
| Eingriff in W1-Datei | keiner | keiner | ja | keiner |
| R1, R2 | approved (C, A) | fix | fix | fix |
| R5 | pending | pending | pending | pending |

Question D3:
D3 — Wie lautet der Auslöser für die Vergleichstabelle je Land (D13), wenn `filter` nur 8 Fälle hat?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Der Plan sagt „unter 80 % → TODO ziehen". Bei acht Fällen gibt es kein 80 %, nur 75 oder 87,5. Und zwei der acht sind heute rot, weil die Antwort zu lang ist, nicht weil falsch gefiltert wurde. Der Auslöser muss auf ganze Fälle umgeschrieben werden und Länge von Filtern trennen, sonst wird er falsch gelesen.
Stakes if we pick wrong: Das TODO wird auf Rauschen gezogen oder nie, und der Owner liest „filter rot" als Filterfehler, wo es Satzzahl ist.
Recommendation: A, weil sie null Aufrufe kostet, in der gepaarten Auswertung direkt ablesbar ist und die Längenfälle sichtbar trennt.
Completeness: A=10/10, B=9/10, C=5/10
Pros / cons:
A) Auf ganze Fälle umschreiben, Befund-Art ausweisen (recommended)
  ✅ „2 von 8 auch neu rot, ohne reine Längenfälle" ist mit b/c und Befundliste direkt abzulesen
  ✅ Kein Eingriff in die W1-Datei, keine zusätzlichen Modellaufrufe
  ❌ Bei n=8 bleibt jeder Schwellenwert grob; ein einzelner Ausreißer wiegt 12,5 %
B) `filter` auf 10 aufstocken, Auslöser 8/10
  ✅ 80 % wird exakt erreichbar, Block wird etwas belastbarer
  ✅ Zwei Schwesterfälle kosten wenig (human: ~30 min / CC: ~5 min)
  ❌ Vier Zusatzaufrufe je Kampagne und ein Eingriff in `test_kundenfeedback_eval.py`, dessen Fälle W1 gerade fixiert hat
C) Nur Tool-Aufrufe je Chat als Auslöser
  ✅ Keine Fallzahl-Arithmetik mehr
  ✅ Misst genau den Overhead, den die Tabelle je Land senken soll
  ❌ Verliert die Qualitätsseite: `filter` kann rot bleiben, ohne dass das TODO je gezogen wird
Net: Ganze Fälle (A) gegen mehr Fälle für eine runde Zahl (B) gegen nur Kosten messen (C).
Header: filter-Auslöser
Options:
A) Auf ganze Fälle umschreiben, Befund-Art ausweisen (recommended)
✅ „2 von 8 auch neu rot, ohne reine Längenfälle" ist mit b/c und Befundliste direkt abzulesen. ✅ Kein Eingriff in die W1-Datei, keine zusätzlichen Modellaufrufe. ❌ Bei n=8 bleibt jeder Schwellenwert grob; ein einzelner Ausreißer wiegt 12,5 %.
B) `filter` auf 10 aufstocken, Auslöser 8/10
✅ 80 % wird exakt erreichbar, Block wird etwas belastbarer. ✅ Zwei Schwesterfälle kosten wenig (human: ~30 min / CC: ~5 min). ❌ Vier Zusatzaufrufe je Kampagne und ein Eingriff in `test_kundenfeedback_eval.py`, dessen Fälle W1 gerade fixiert hat.
C) Nur Tool-Aufrufe je Chat als Auslöser
✅ Keine Fallzahl-Arithmetik mehr. ✅ Misst genau den Overhead, den die Tabelle je Land senken soll. ❌ Verliert die Qualitätsseite: `filter` kann rot bleiben, ohne dass das TODO je gezogen wird.

State: approved
Actual answer: A) Auf ganze Fälle umschreiben, Befund-Art ausweisen (Owner, D3, 2026-09-24)
Accepted scope: Der D13-Auslöser lautet neu: „mindestens 2 der 8 `filter`-Fälle sind auch mit neuem Prompt rot, wobei Fälle, die allein an der Satzzahl scheitern, nicht mitzählen — ODER die Tool-Aufrufe je Chat steigen spürbar". Die Vorher/Nachher-Tabelle bekommt je rotem Fall eine Spalte „Befund-Art" (aus den `pruefe()`-Befunden: Länge / Inhalt / Link / leer), damit Länge und Filtern getrennt lesbar sind. Kein Eingriff in `test_kundenfeedback_eval.py`, keine Zusatzaufrufe.
History: none

### R5: Beweis, dass der Vorlagen-Tausch greift
Finding: R5, P1 (Test), confidence 9/10, `agent_base.py:1494` (`return system_prompt_template.format(`) — der Treiber schaltet per Zuweisung auf das Modulglobal um; nichts prüft, dass die beiden Seiten eines Paares tatsächlich verschiedene Prompts bekommen. Reviewer: plan-eng-review (Tests)
Plan baseline: Abschnitt „Verschränkte Messung": „Umschalten ist eine Zuweisung auf das Modulglobal." Kein Nachweis vorgesehen. Keine Freigabe.
Runtime evidence: `format_system_prompt` liest `system_prompt_template` bei jedem Aufruf (`agent_base.py:1494`), `agent.call` ruft es je Anfrage. Greift der Tausch nicht (falsches Modul importiert, `from agent_base import system_prompt_template` als Kopie, Reihenfolge der Zuweisung), sind alt und neu identisch, jedes Paar konkordant, b = c = 0 — und das liest sich als „W3 bewirkt nichts". Das ist der eine Fehler, den die Paarung selbst nicht entdecken kann.
Comparison grid:

| Choice | Current | A | B | C |
|---|---|---|---|---|
| R5 Nachweis des Tauschs | keiner | Vorbedingung im Treiber: beide Vorlagen für einen festen Fall rendern, `assert alt != neu`, SHA-256 je Seite ins Protokoll; bei Gleichheit Abbruch vor dem ersten Modellaufruf | nur SHA-256 je Seite ins Protokoll, kein Abbruch | kein Nachweis |
| Protokoll je Lauf | — | Hash alt / Hash neu / Git-Commit der beiden Vorlagen | Hash alt / Hash neu | — |
| Zusatzaufrufe | 0 | 0 (nur Rendern) | 0 | 0 |
| R1–R4 | approved | fix | fix | fix |

Question D4:
D4 — Wie wird nachgewiesen, dass jedes Paar wirklich alten und neuen Prompt gesehen hat?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Der Treiber tauscht die Prompt-Vorlage per Zuweisung um. Klappt das nicht, laufen beide Seiten mit demselben Prompt, alle Paare sind gleich, und das Ergebnis sagt „W3 wirkt nicht". Genau diesen Fehler kann die Paarung nicht selbst bemerken. Ein Vorab-Check „beide Vorlagen rendern, sie müssen sich unterscheiden" kostet nichts.
Stakes if we pick wrong: 214 Aufrufe und eine Stunde Laufzeit liefern eine Null-Aussage, die als Ergebnis gelesen wird; Push 2 wird auf falscher Grundlage entschieden.
Recommendation: A, weil Rendern und Hashen gratis sind und ein Abbruch vor dem ersten Aufruf die einzige Stelle ist, an der der Fehler noch billig ist.
Completeness: A=10/10, B=6/10, C=1/10
Pros / cons:
A) Rendern, vergleichen, hashen, sonst Abbruch (recommended)
  ✅ Ein stiller Nicht-Tausch wird vor dem ersten Modellaufruf zum harten Fehler, nicht zu einem Ergebnis
  ✅ Hash je Seite im Protokoll macht jede Tabelle später der genauen Vorlage zuordenbar
  ❌ Ein paar Zeilen mehr im Treiber und eine feste Render-Eingabe, die gepflegt werden muss
B) Nur Hashes protokollieren
  ✅ Zuordenbarkeit ist gegeben, minimaler Code
  ✅ Kein Abbruchpfad, der selbst falsch feuern könnte
  ❌ Der Fehler fällt erst beim Lesen auf, nach 214 Aufrufen — wenn überhaupt jemand die Hashes vergleicht
C) Kein Nachweis
  ✅ Nichts zu bauen
  ✅ Vertraut darauf, dass die Zuweisung auf das Modulglobal wie geprüft funktioniert
  ❌ b = c = 0 ist von „W3 wirkt nicht" nicht unterscheidbar
Net: Harter Vorab-Check (A) gegen nachträgliche Zuordenbarkeit (B) gegen Vertrauen (C).
Header: Tausch-Nachweis
Options:
A) Rendern, vergleichen, hashen, sonst Abbruch (recommended)
✅ Ein stiller Nicht-Tausch wird vor dem ersten Modellaufruf zum harten Fehler, nicht zu einem Ergebnis. ✅ Hash je Seite im Protokoll macht jede Tabelle später der genauen Vorlage zuordenbar. ❌ Ein paar Zeilen mehr im Treiber und eine feste Render-Eingabe, die gepflegt werden muss.
B) Nur Hashes protokollieren
✅ Zuordenbarkeit ist gegeben, minimaler Code. ✅ Kein Abbruchpfad, der selbst falsch feuern könnte. ❌ Der Fehler fällt erst beim Lesen auf, nach 214 Aufrufen — wenn überhaupt jemand die Hashes vergleicht.
C) Kein Nachweis
✅ Nichts zu bauen. ✅ Vertraut darauf, dass die Zuweisung auf das Modulglobal wie geprüft funktioniert. ❌ b = c = 0 ist von „W3 wirkt nicht" nicht unterscheidbar.

State: approved
Actual answer: A) Rendern, vergleichen, hashen, sonst Abbruch (Owner, D4, 2026-09-24)
Accepted scope: Vorbedingung im Treiber, vor dem ersten Modellaufruf: `format_system_prompt` für einen festen Fall einmal unter alter und einmal unter neuer Vorlage rendern, `assert alt != neu`, SHA-256 beider gerenderten Prompts plus Git-Commit je Vorlage ins Laufprotokoll und in die Vorher/Nachher-Tabelle; bei Gleichheit harter Abbruch der Kampagne. Dazu ein Unit-Test im Treiber-Modul (ohne Netz), der den Abbruch bei identischen Vorlagen nachweist.
History: none

### Korrekturen ohne Frage (Außenstimme, Claude-Subagent, 2026-09-24)
- Außenstimme 7 (P2, bestätigt): Plan Z. 779-783, 937, 955, 959 trugen noch die Zwei-Kampagnen-Fassung (Ausgangsmessung, `EVAL_N=3`, Schlussmessung) gegen D20/D15. Umgeschrieben auf die verschränkte Kampagne; kein Verhalten geändert, nur Text an bereits genehmigte Entscheidungen angepasst.
- Außenstimme 9 (P3, bestätigt): `system_prompt_template` liegt bei `agent_base.py:908-1038`, nicht 499-628; `from agent import call` ist `tests/test_kundenfeedback_eval.py:69`. Korrigiert.

### R6: Woher nimmt der Treiber die ALTE Vorlage?
Finding: Außenstimme 1, P1, confidence 10/10 (bestätigt), `agent_base.py:908` (`system_prompt_template = f"""`) bis `:1038` (`""".strip()`), Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: „Umschalten ist eine Zuweisung auf das Modulglobal." Nach den vier W3-Commits enthält der Prozess nur die NEUE Vorlage; die Quelle der alten ist nirgends benannt. R5 prüft nur `alt != neu`, nicht, dass „alt" wirklich der Stand vor W3 ist. Keine Freigabe.
Runtime evidence: Die Vorlage ist ein f-String, der beim Import Modulzustand interpoliert (`{allgemeine_faqs}` u. a.). Ein zweites Modul aus der alten Datei zu laden würde Sitemap- und FAQ-Laden wiederholen und W2/W5/W7-Code der alten Datei mitbringen. `git show <sha>:agent_base.py` liefert den Text; der f-String muss daraus geschnitten und im Namensraum von `agent_base` ausgewertet werden.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R6 Quelle der alten Vorlage | unbenannt | `git show <Eltern-Commit von W3-Commit 1>:agent_base.py`, f-String zwischen `system_prompt_template = f"""` und `""".strip()` schneiden, mit `eval` im `agent_base.__dict__` auswerten; SHA des Eltern-Commits ins Protokoll; Unit-Test für das Schneiden (ohne Netz) | unbenannt lassen, W3-Agent entscheidet | Erst prüfen, ob ein zweites Modul (`importlib` aus altem Text) sauber lädt, dann wählen | diese Zeile offen lassen |
| R5 Hash/Commit im Protokoll | approved | erfüllt: Commit = Eltern-SHA | unverändert | unverändert | unverändert |
| R1–R5 | approved | fix | fix | fix | fix |

Question D5:
D5 — Woher bekommt der Treiber die alte Prompt-Vorlage?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Die Vorlage ist ein f-String, der beim Import gebaut wird. Nach W3 gibt es im laufenden Prozess nur noch die neue. Der Treiber muss die alte irgendwoher holen, sonst gibt es nichts zu tauschen. Naheliegend: aus Git den Stand vor W3 lesen, den Vorlagentext ausschneiden und im selben Modul auswerten.
Stakes if we pick wrong: Der W3-Agent improvisiert, lädt womöglich die alte Datei als zweites Modul samt alter Tool-Logik, und „alt" ist dann nicht der Prompt vor W3, sondern ein anderer Bot.
Recommendation: A, weil Git die einzige eindeutige Quelle für „vor W3" ist und das Schneiden des f-Strings mit einem Unit-Test billig abgesichert wird.
Completeness: A=10/10, B=2/10, C=6/10, D=0/10
Pros / cons:
A) Aus Git schneiden und im Modul auswerten (recommended)
  ✅ „Alt" ist per SHA definiert, R5 bekommt seinen Commit gratis, keine doppelte Modulinitialisierung
  ✅ Ein Unit-Test prüft das Schneiden gegen den aktuellen Stand, bevor die Kampagne startet
  ❌ `eval` auf Quelltext aus Git und ein festes Marker-Paar, das bei einer Umbenennung bricht (human: ~2h / CC: ~15 min)
B) Unbenannt lassen
  ✅ Kein Planaufwand jetzt
  ✅ Der W3-Agent hat den Code vor sich und kann selbst wählen
  ❌ Genau die Lücke, die die Außenstimme als größtes Feasibility-Risiko nennt; R5 kann sie nicht auffangen
C) Erst zweites Modul prüfen
  ✅ Vermeidet `eval`, falls `importlib` sauber lädt
  ✅ Bounded: eine Stunde Probe, dann Entscheidung
  ❌ Zweites Modul wiederholt Sitemap/FAQ-Laden und bringt alten W2/W5/W7-Code mit; Ergebnis sehr wahrscheinlich „nein"
D) Diese Zeile offen lassen
  ✅ Nichts wird jetzt festgelegt
  ✅ Alle anderen Entscheidungen bleiben unberührt
  ❌ Der Treiber kann nicht gebaut werden, solange das offen ist
Net: Definierte Git-Quelle (A) gegen Improvisation (B) gegen eine Probe mit absehbarem Ausgang (C) gegen Stillstand (D).
Header: Alte Vorlage
Options:
A) Aus Git schneiden und im Modul auswerten (recommended)
✅ „Alt" ist per SHA definiert, R5 bekommt seinen Commit gratis, keine doppelte Modulinitialisierung. ✅ Ein Unit-Test prüft das Schneiden gegen den aktuellen Stand, bevor die Kampagne startet. ❌ `eval` auf Quelltext aus Git und ein festes Marker-Paar, das bei einer Umbenennung bricht (human: ~2h / CC: ~15 min).
B) Unbenannt lassen
✅ Kein Planaufwand jetzt. ✅ Der W3-Agent hat den Code vor sich und kann selbst wählen. ❌ Genau die Lücke, die die Außenstimme als größtes Feasibility-Risiko nennt; R5 kann sie nicht auffangen.
C) Erst zweites Modul prüfen
✅ Vermeidet `eval`, falls `importlib` sauber lädt. ✅ Bounded: eine Stunde Probe, dann Entscheidung. ❌ Zweites Modul wiederholt Sitemap/FAQ-Laden und bringt alten W2/W5/W7-Code mit; Ergebnis sehr wahrscheinlich „nein".
D) Diese Zeile offen lassen
✅ Nichts wird jetzt festgelegt. ✅ Alle anderen Entscheidungen bleiben unberührt. ❌ Der Treiber kann nicht gebaut werden, solange das offen ist.

State: approved
Actual answer: A) Aus Git schneiden und im Modul auswerten (Owner, D5, 2026-09-24)
Accepted scope: Der Treiber liest `git show <Eltern-Commit von W3-Commit 1>:agent_base.py`, schneidet den f-String zwischen `system_prompt_template = f"""` und `""".strip()` heraus und wertet ihn mit `eval` im Namensraum von `agent_base` aus; das Ergebnis ist die alte Vorlage. Der Eltern-SHA wird als Konstante im Treiber gesetzt und ins Protokoll geschrieben (erfüllt R5 „Git-Commit je Vorlage"). Ein netzfreier Unit-Test prüft das Schneiden gegen den aktuellen `agent_base.py` (Ergebnis == aktuelles `system_prompt_template`).
History: none

### R7: Tool-Aufrufe je Chat sind nicht mitgeschnitten
Finding: Außenstimme 2, P1, confidence 10/10 (bestätigt), `agent.py:477-491` (`call` gibt nur `event["data"]["reply"]` zurück, `tool_call`-Events verworfen), `agent.py:359-366` und `:412-419` loggen nur bei auffälligem `finish_reason` bzw. leerer Antwort; `from agent import call` in `tests/test_kundenfeedback_eval.py:70`, `test_agentur_faq.py:48`, `test_meinchamaeleon_faq.py:46`. Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: „Mitzuschneiden: Tool-Aufrufe je Chat. `agent.py:362` und `:415` loggen sie … Im verschränkten Lauf fällt sie beidseitig gratis an." Falsch: ein normaler Chat loggt nichts, und `call` liefert keine Zählung. Welle 3 Punkt 3 hängt daran. Keine Freigabe.
Runtime evidence: `call_stream` liefert `tool_call`-Events (`agent.py`, Zweig „Check for tool calls in AI messages"), `call` iteriert sie und wirft sie weg. Da jedes Testmodul `call` per Import kopiert, greift ein Patch auf `agent.call` dort nicht; gepatcht werden muss der Name im jeweiligen Modul — oder der Treiber ersetzt `agent.call_stream`, das `call` intern über das Modulglobal aufruft.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R7 Zählung der Tool-Aufrufe | keine | Treiber wickelt `agent.call_stream` (Modulglobal, von `call` zur Laufzeit aufgelöst) und zählt `tool_call`-Events je Aufruf; Zahl je Seite ins Protokoll; Unit-Test mit gefälschtem Stream | Plansatz streichen: Welle 3 Punkt 3 misst Tool-Aufrufe getrennt per Stichprobe über `call_stream` | Erst prüfen, ob `call` das Modulglobal `call_stream` zur Laufzeit auflöst (ja, `agent.py:477`), dann A | diese Zeile offen lassen |
| Welle 3 Punkt 3 (Overhead-Kontrolle) | verlangt Vorher/Nachher | erfüllt aus der Kampagne | eigener Lauf, ~10 Zusatzaufrufe | wie A | offen |
| R1–R6 | approved | fix | fix | fix | fix |

Question D6:
D6 — Wie kommt die Zahl der Tool-Aufrufe je Chat in die Kampagne?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Der Plan behauptet, Tool-Aufrufe würden ohnehin geloggt. Stimmt nicht: geloggt wird nur bei Fehlern, und die Funktion, die die Tests rufen, wirft die Tool-Ereignisse weg. Der Treiber kann den Strom darunter einwickeln und zählen; das ist ein Patch an einer Stelle, weil die Testfunktion den Strom zur Laufzeit nachschlägt.
Stakes if we pick wrong: Welle 3 Punkt 3 (Overhead durch Mehrseiten-Abruf) bleibt ohne Zahl, und der D13-Auslöser „Tool-Aufrufe steigen spürbar" ist nicht messbar.
Recommendation: A, weil ein Wrapper um `call_stream` alle drei Suiten trifft, ohne deren Import zu berühren, und die Zahl je Seite gratis mitläuft.
Completeness: A=10/10, B=6/10, C=8/10, D=0/10
Pros / cons:
A) `call_stream` im Treiber einwickeln und zählen (recommended)
  ✅ Ein Patch auf `agent.call_stream` reicht, weil `call` den Namen erst zur Laufzeit auflöst (`agent.py:477`)
  ✅ Zahl je Seite landet im selben Protokoll wie rot/grün, leer und Hash; Unit-Test mit gefälschtem Stream ist netzfrei
  ❌ Der Treiber hängt an der Event-Form (`type == "tool_call"`); ändert W2 sie, bricht die Zählung still (human: ~1h / CC: ~10 min)
B) Plansatz streichen, Stichprobe getrennt fahren
  ✅ Treiber bleibt schlanker
  ✅ Welle 3 Punkt 3 war ohnehin als Stichprobe formuliert
  ❌ Zweite Messung außerhalb des Paares, mit genau den Störgrößen, die D20 vermeiden sollte
C) Erst Laufzeitauflösung prüfen, dann A
  ✅ Nimmt der Annahme in A das Restrisiko
  ✅ Fünf Minuten Probe
  ❌ Die Auflösung ist im Code bereits sichtbar (`for event in call_stream(` im Funktionskörper); die Probe bestätigt nur
D) Diese Zeile offen lassen
  ✅ Nichts wird festgelegt
  ✅ Andere Entscheidungen unberührt
  ❌ Der falsche Plansatz bleibt stehen und wird vom W3-Agenten geglaubt
Net: Wrapper im Treiber (A) gegen Extra-Messung (B) gegen bestätigende Probe (C) gegen Stillstand (D).
Header: Tool-Aufrufe zählen
Options:
A) `call_stream` im Treiber einwickeln und zählen (recommended)
✅ Ein Patch auf `agent.call_stream` reicht, weil `call` den Namen erst zur Laufzeit auflöst (`agent.py:477`). ✅ Zahl je Seite landet im selben Protokoll wie rot/grün, leer und Hash; Unit-Test mit gefälschtem Stream ist netzfrei. ❌ Der Treiber hängt an der Event-Form (`type == "tool_call"`); ändert W2 sie, bricht die Zählung still (human: ~1h / CC: ~10 min).
B) Plansatz streichen, Stichprobe getrennt fahren
✅ Treiber bleibt schlanker. ✅ Welle 3 Punkt 3 war ohnehin als Stichprobe formuliert. ❌ Zweite Messung außerhalb des Paares, mit genau den Störgrößen, die D20 vermeiden sollte.
C) Erst Laufzeitauflösung prüfen, dann A
✅ Nimmt der Annahme in A das Restrisiko. ✅ Fünf Minuten Probe. ❌ Die Auflösung ist im Code bereits sichtbar (`for event in call_stream(` im Funktionskörper); die Probe bestätigt nur.
D) Diese Zeile offen lassen
✅ Nichts wird festgelegt. ✅ Andere Entscheidungen unberührt. ❌ Der falsche Plansatz bleibt stehen und wird vom W3-Agenten geglaubt.

State: approved
Actual answer: A) `call_stream` im Treiber einwickeln und zählen (Owner, D6, 2026-09-24)
Accepted scope: Der Treiber ersetzt für die Dauer der Kampagne `agent.call_stream` durch einen Wrapper, der die `tool_call`-Events je Aufruf zählt und die Zahl je Seite (alt/neu) ins Protokoll und in die Vorher/Nachher-Tabelle schreibt. `agent.call` bleibt unberührt und löst den Namen zur Laufzeit auf (`agent.py:477`). Netzfreier Unit-Test mit gefälschtem Stream. Der Plansatz „fällt gratis an" wird durch diese Beschreibung ersetzt.
History: none

### R8: Drei Ausgänge je Seite statt rot/grün
Finding: Außenstimme 3, P2, confidence 9/10 (bestätigt), `tests/test_agentur_faq.py:311-316` (`@pytest.mark.skipif(not os.getenv("AGENTUR_TEST_NUMMER"))`, direkt aufgerufen wird der Marker ignoriert), `test_agentur_faq.py:353-357` und `test_meinchamaeleon_faq.py:183-191` (`parametrize`), `test_kundenfeedback_eval.py:805` (`pytest.skip` → `Skipped`, kein `AssertionError`), Netz-/TourOne-Ausnahmen in `test_reiseunterlagen_link_ist_vollstaendig`. Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: R2 (D2): „fängt `AssertionError` als rot". Zu eng: Skip, Fehler und fehlende Umgebung fallen weder auf rot noch auf grün. Die Fallzahl „Agentur 32" und „zwölf Einzelfunktionen" sind zudem nicht deckungsgleich (32 = 22 `DONE` + 9 + 2 Deko − 1 Skip).
Runtime evidence: Direkt aufgerufen wirft die skipif-Funktion `KeyError`/ruft ohne Nummer; `pytest.skip` wirft `_pytest.outcomes.Skipped`; ein Timeout aus `kundendaten` wirft `requests`-Ausnahmen. Ohne dritten Ausgang zählt der Treiber diese Seiten falsch oder stürzt ab.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R8 Ausgänge je Seite | rot / grün | rot / grün / **Fehler** (Skipped, Netz-/TourOne-Ausnahme, fehlende Umgebung); Paar mit ≥1 Fehlerseite aus b/c raus, eigene Spalte „Fehler alt / Fehler neu"; parametrisierte Funktionen über die Listen im Modul aufgerufen; skipif-Funktion nur mit gesetzter `AGENTUR_TEST_NUMMER`, sonst als „übersprungen" ausgewiesen; Fallzahl im Plan auf 31 + optional 1 korrigiert | rot/grün lassen, Fehler = rot | Erst zählen, wie oft Fehler in einem Probelauf auftreten, dann entscheiden | offen lassen |
| R1 Leerantwort (D1) | approved | bleibt eigener Ausgang „leer" neben „Fehler" | fix | fix | fix |
| R2–R7 | approved | fix | fix | fix | fix |

Question D7:
D7 — Wie geht der Treiber mit Seiten um, die weder rot noch grün sind?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Manche Testfunktionen überspringen sich selbst, brauchen eine Umgebungsvariable oder scheitern am Netz. Das ist keine Aussage über den Prompt. Wenn der Treiber nur rot und grün kennt, zählt er solche Seiten als Prompt-Fehler oder stürzt ab. Ein dritter Ausgang „Fehler" hält das Paar aus der Statistik heraus und macht es sichtbar.
Stakes if we pick wrong: Ein TourOne-Timeout auf der neuen Seite wird zum „W3 hat die Regression verursacht" und sperrt Push 2; oder der Treiber bricht mitten im Block ab und der Prozessplan geht nicht auf.
Recommendation: A, weil drei Ausgänge die einzige Form sind, in der b/c nur Prompt-Effekte zählen, und die Handhabung von Skip/Parametrize ohnehin gebaut werden muss.
Completeness: A=10/10, B=4/10, C=6/10, D=0/10
Pros / cons:
A) Dritter Ausgang „Fehler", Skip/Parametrize/Umgebung explizit (recommended)
  ✅ b/c enthalten nur Paare, bei denen beide Seiten wirklich geantwortet haben
  ✅ Parametrisierte Funktionen werden über die Modul-Listen aufgerufen, skipif nur mit gesetzter Nummer, sonst sichtbar übersprungen (human: ~2h / CC: ~15 min)
  ❌ Mehr Zustände im Protokoll; die Fallzahlen im Plan müssen einmal sauber neu gezählt werden
B) Fehler als rot zählen
  ✅ Einfachster Treiber
  ✅ Konservativ: ein Fehler kann Push 2 nur sperren, nie freigeben
  ❌ Netz und Umgebung landen in der Prompt-Statistik; genau die Vermengung, die D20 vermeiden sollte
C) Erst Fehlerhäufigkeit im Probelauf messen
  ✅ Entscheidet auf Daten
  ✅ Ein Probelauf ist ohnehin nötig
  ❌ Der Treiber muss Skip/Parametrize trotzdem können, um überhaupt zu laufen; C verschiebt nur die Frage
D) Offen lassen
  ✅ Nichts festgelegt
  ✅ Andere Entscheidungen unberührt
  ❌ Der Treiber stürzt an der ersten skipif-Funktion ab
Net: Sauberes b/c (A) gegen konservativ, aber vermengt (B) gegen vertagen (C) gegen Absturz (D).
Header: Dritter Ausgang
Options:
A) Dritter Ausgang „Fehler", Skip/Parametrize/Umgebung explizit (recommended)
✅ b/c enthalten nur Paare, bei denen beide Seiten wirklich geantwortet haben. ✅ Parametrisierte Funktionen werden über die Modul-Listen aufgerufen, skipif nur mit gesetzter Nummer, sonst sichtbar übersprungen (human: ~2h / CC: ~15 min). ❌ Mehr Zustände im Protokoll; die Fallzahlen im Plan müssen einmal sauber neu gezählt werden.
B) Fehler als rot zählen
✅ Einfachster Treiber. ✅ Konservativ: ein Fehler kann Push 2 nur sperren, nie freigeben. ❌ Netz und Umgebung landen in der Prompt-Statistik; genau die Vermengung, die D20 vermeiden sollte.
C) Erst Fehlerhäufigkeit im Probelauf messen
✅ Entscheidet auf Daten. ✅ Ein Probelauf ist ohnehin nötig. ❌ Der Treiber muss Skip/Parametrize trotzdem können, um überhaupt zu laufen; C verschiebt nur die Frage.
D) Offen lassen
✅ Nichts festgelegt. ✅ Andere Entscheidungen unberührt. ❌ Der Treiber stürzt an der ersten skipif-Funktion ab.

State: approved
Actual answer: A) Dritter Ausgang „Fehler", Skip/Parametrize/Umgebung explizit (Owner, D7, 2026-09-24)
Accepted scope: Jede Seite eines Paares hat einen von vier Ausgängen: grün, rot, leer (D1, nach Wiederholung), Fehler (`Skipped`, Netz-/TourOne-Ausnahme, fehlende Umgebung). Paare mit ≥1 Fehlerseite fallen aus b/c und stehen in einer eigenen Spalte „Fehler alt / Fehler neu". Parametrisierte Einzelfunktionen ruft der Treiber über die Listen im Modul auf; `test_buchungsstatus_mit_verifizierter_agentur` nur bei gesetzter `AGENTUR_TEST_NUMMER`, sonst sichtbar „übersprungen". Die Blocktabelle im Plan wird auf die tatsächliche Zählung korrigiert (Agentur: 22 `DONE` + 9 Einzelfunktionen + 2 Deko = 33, davon 1 nur mit Nummer). R2-Scope „`AssertionError` = rot" bleibt, wird um diese Ausgänge ergänzt.
History: none

### R9: `airline` und `nichtangeboten` sind keine importierbaren Listen
Finding: Außenstimme 4, P2, confidence 10/10 (bestätigt), `tests/test_kundenfeedback_eval.py:796-817` (`test_airline` baut den Fall aus `reise_mit_airline(land)` innerhalb der Funktion), `:868` (`test_nichtangeboten` ebenso); Listen sind nur `FILTER`, `UNGEFRAGT`, `FACHWISSEN`, `ERFINDEN`, `FLUG`. Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: R2 (D2): „importiert die Fall-Listen (…, die Blöcke der neuen Suite)". Für `airline` (28) und `nichtangeboten` (4) gibt es keine Liste; ruft der Treiber stattdessen die Testfunktionen, bekommt er keine `pruefe()`-Befunde und damit keine Befund-Art (D3). Keine Freigabe.
Runtime evidence: `fahre()` (Z. ~598) kapselt `call` + `pruefe`; `test_airline` ruft `fahre` mit einem vor Ort gebauten Fall-Dict, dessen Erwartung aus `reise_mit_airline` (lru_cache, parallele Seitenabrufe) stammt.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R9 Fallquelle für airline/nichtangeboten | unklar | Treiber baut die Fall-Dicts mit denselben Helfern (`reise_mit_airline`, `_nicht_angeboten`, `AIRLINE_LAENDER`, `NICHT_ANGEBOTEN`) nach und ruft für ALLE Fälle der neuen Suite `pruefe(fall, reply)` direkt (statt `fahre`), damit Befund-Art vorliegt; Seitenabrufe vor dem ersten Modellaufruf vorwärmen | Testfunktionen rufen, Befund-Art aus dem Assertion-Text parsen | Erst prüfen, ob ein `fahre`-Hook (z. B. Rückgabe der Befunde) billiger ist — Eingriff in W1-Datei | offen lassen |
| D3 Befund-Art | approved | erfüllt | nur per Text-Parsing | offen | offen |
| R1–R8 | approved | fix | fix | fix | fix |

Question D8:
D8 — Wie kommt der Treiber an die Fälle der Blöcke `airline` und `nichtangeboten`?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Fünf Blöcke der neuen Suite sind fertige Listen, zwei bauen ihre Fälle erst in der Testfunktion aus Webseiten. Der Treiber kann diese Bauschritte mit denselben Helfern nachmachen und dann die Prüfung selbst aufrufen; so bekommt er auch die Befund-Art, die D3 verlangt. Die Alternative ist, die Testfunktion zu rufen und den Fehlertext zu parsen.
Stakes if we pick wrong: 32 der 62 Fälle der neuen Suite fehlen in der Kampagne, oder die Befund-Art-Spalte ist für sie leer.
Recommendation: A, weil die Helfer öffentlich im Modul liegen, `pruefe` die Befunde strukturiert liefert und kein Text-Parsing nötig ist.
Completeness: A=10/10, B=6/10, C=7/10, D=0/10
Pros / cons:
A) Fall-Dicts mit den Modul-Helfern nachbauen, `pruefe` direkt rufen (recommended)
  ✅ Alle 62 Fälle der neuen Suite laufen durch denselben Pfad `call` → `pruefe`; Befund-Art liegt strukturiert vor
  ✅ Vorwärmen der Seitenabrufe vor dem ersten Modellaufruf hält den Website-Timeout aus dem Paar heraus (human: ~2h / CC: ~15 min)
  ❌ Der Treiber dupliziert ~15 Zeilen Fallbau aus `test_airline`/`test_nichtangeboten`; driften die, driftet die Kampagne
B) Testfunktionen rufen, Assertion-Text parsen
  ✅ Kein duplizierter Fallbau
  ✅ Gleicher Weg wie für die Einzelfunktionen der alten Suiten
  ❌ Befund-Art aus Fehlertext raten; `pytest.skip` in `test_airline` muss als Fehler-Ausgang gefangen werden
C) Erst `fahre`-Hook in der W1-Datei prüfen
  ✅ Könnte Duplizierung und Parsing beide vermeiden
  ✅ Kleine Probe
  ❌ Eingriff in `test_kundenfeedback_eval.py`, den D2 gerade vermieden hat
D) Offen lassen
  ✅ Nichts festgelegt
  ✅ Andere Entscheidungen unberührt
  ❌ Der breiteste Block der Suite fällt aus der Kampagne
Net: Nachbauen + strukturierte Befunde (A) gegen Text-Parsing (B) gegen Eingriff in W1 (C) gegen Verlust des Blocks (D).
Header: airline-Fälle
Options:
A) Fall-Dicts mit den Modul-Helfern nachbauen, `pruefe` direkt rufen (recommended)
✅ Alle 62 Fälle der neuen Suite laufen durch denselben Pfad `call` → `pruefe`; Befund-Art liegt strukturiert vor. ✅ Vorwärmen der Seitenabrufe vor dem ersten Modellaufruf hält den Website-Timeout aus dem Paar heraus (human: ~2h / CC: ~15 min). ❌ Der Treiber dupliziert ~15 Zeilen Fallbau aus `test_airline`/`test_nichtangeboten`; driften die, driftet die Kampagne.
B) Testfunktionen rufen, Assertion-Text parsen
✅ Kein duplizierter Fallbau. ✅ Gleicher Weg wie für die Einzelfunktionen der alten Suiten. ❌ Befund-Art aus Fehlertext raten; `pytest.skip` in `test_airline` muss als Fehler-Ausgang gefangen werden.
C) Erst `fahre`-Hook in der W1-Datei prüfen
✅ Könnte Duplizierung und Parsing beide vermeiden. ✅ Kleine Probe. ❌ Eingriff in `test_kundenfeedback_eval.py`, den D2 gerade vermieden hat.
D) Offen lassen
✅ Nichts festgelegt. ✅ Andere Entscheidungen unberührt. ❌ Der breiteste Block der Suite fällt aus der Kampagne.

State: approved
Actual answer: A) Fall-Dicts mit den Modul-Helfern nachbauen, `pruefe` direkt rufen (Owner, D8, 2026-09-24)
Accepted scope: Der Treiber baut die Fälle für `airline` und `nichtangeboten` mit den Modul-Helfern (`AIRLINE_LAENDER`, `reise_mit_airline`, `NICHT_ANGEBOTEN`, `_nicht_angeboten`) nach, wärmt alle Seitenabrufe vor dem ersten Modellaufruf vor und ruft für ALLE Fälle der neuen Suite `call` + `pruefe(fall, reply)` direkt statt `fahre`; die Befunde liefern die Befund-Art (D3). Ein netzfreier Unit-Test prüft, dass der nachgebaute `airline`-Fall strukturgleich zu dem aus `test_airline` ist (gleiche Schlüssel).
History: none

### R10: Signifikanz-Sprache und Block-Einteilung
Finding: Außenstimme 5, P2, confidence 10/10 (Arithmetik geprüft), Plan-Abschnitt „Auswertung: gepaart": `p = 2·(1/2)^b` bei c=0 ergibt b=5 → 0,0625 (nicht < 0,05); erst b=6 → 0,031. Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: „ein Block mit weniger als 5 Fällen kann bei keiner Effektgröße signifikant werden" und Einteilung: `filter`/`fachwissen` als Quote, `flug`/`erfinden`/`ungefragt` als „kaputt / nicht kaputt". Nach eigener Arithmetik braucht `filter` (8) 6 von 8 Umschwünge in eine Richtung, gehört also in dieselbe Klasse wie die kleinen Blöcke. Ein α ist nicht genannt. Keine Freigabe.
Runtime evidence: reine Rechnung, keine Codeabhängigkeit. Für das Push-2-Tor zählt ohnehin die Liste der Fälle mit c (alt grün / neu rot), nicht ein p-Wert.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R10 Auswertungsform | p-Wert-Sprache, zwei Klassen von Blöcken | Signifikanz-Sprache streichen; je Block b, c, Fallliste der c-Fälle mit Befund-Art; Entscheidungsregel wie D3 auf ganze Fälle für ALLE Blöcke; McNemar nur als Zusatzzeile für `airline` (28) und Agentur (33) mit α = 0,05 genannt | wie heute, Schwellenbeispiel auf b=6 korrigieren | Erst Owner fragen, ob p-Werte überhaupt gewünscht sind | offen lassen |
| Push-2-Tor | „keine alte Suite schlechter" | unverändert: c-Liste der alten Suiten leer oder begründet | unverändert | unverändert | unverändert |
| R1–R9 | approved | fix | fix | fix | fix |

Question D9:
D9 — Wie wird das gepaarte Ergebnis berichtet: p-Werte oder Fall-Listen?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Der Plan rechnet mit McNemar und behauptet, ab 5 Fällen könne ein Block „signifikant" werden. Die eigene Formel sagt: erst ab 6 Umschwüngen, also müsste `filter` mit 8 Fällen zu drei Vierteln kippen. Die Einteilung in „Quote"- und „kaputt/nicht kaputt"-Blöcke ist damit falsch. Ehrlicher ist: je Block b und c zählen, die gekippten Fälle namentlich auflisten, und nur bei den zwei großen Blöcken zusätzlich ein p nennen.
Stakes if we pick wrong: Der Bericht suggeriert Statistik, die die Fallzahlen nicht tragen; ein „nicht signifikant" wird als „kein Effekt" gelesen, oder ein 5/8 als bewiesen.
Recommendation: A, weil das Tor ohnehin auf der c-Liste entscheidet und p-Werte nur dort stehen sollten, wo n sie trägt.
Completeness: A=10/10, B=6/10, C=5/10, D=0/10
Pros / cons:
A) Fall-Listen je Block, p nur für airline und Agentur (recommended)
  ✅ Jede Zahl im Bericht ist mit der Fallzahl vereinbar; die c-Liste ist genau das, was Push 2 braucht
  ✅ D3-Regel („ganze Fälle") gilt einheitlich für alle Blöcke, keine zwei Klassen mehr
  ❌ Kein kompaktes „signifikant ja/nein" für kleine Blöcke; der Leser muss die Liste lesen
B) Wie heute, nur b=6 korrigieren
  ✅ Minimaler Texteingriff
  ✅ Behält die McNemar-Zeile für alle Blöcke
  ❌ `filter` bleibt in der falschen Klasse; ein p für n=4 ist Dekoration
C) Erst fragen, ob p-Werte gewünscht sind
  ✅ Vermeidet Statistik, die niemand liest
  ✅ Kleine Rückfrage
  ❌ Diese Frage IST die Rückfrage; C vertagt sie nur
D) Offen lassen
  ✅ Nichts festgelegt
  ✅ Andere Entscheidungen unberührt
  ❌ Die falsche Arithmetik bleibt im Plan und wird in die Tabelle übernommen
Net: Ehrliche Listen (A) gegen kosmetische Korrektur (B) gegen Vertagen (C, D).
Header: Auswertungsform
Options:
A) Fall-Listen je Block, p nur für airline und Agentur (recommended)
✅ Jede Zahl im Bericht ist mit der Fallzahl vereinbar; die c-Liste ist genau das, was Push 2 braucht. ✅ D3-Regel („ganze Fälle") gilt einheitlich für alle Blöcke, keine zwei Klassen mehr. ❌ Kein kompaktes „signifikant ja/nein" für kleine Blöcke; der Leser muss die Liste lesen.
B) Wie heute, nur b=6 korrigieren
✅ Minimaler Texteingriff. ✅ Behält die McNemar-Zeile für alle Blöcke. ❌ `filter` bleibt in der falschen Klasse; ein p für n=4 ist Dekoration.
C) Erst fragen, ob p-Werte gewünscht sind
✅ Vermeidet Statistik, die niemand liest. ✅ Kleine Rückfrage. ❌ Diese Frage IST die Rückfrage; C vertagt sie nur.
D) Offen lassen
✅ Nichts festgelegt. ✅ Andere Entscheidungen unberührt. ❌ Die falsche Arithmetik bleibt im Plan und wird in die Tabelle übernommen.

State: approved
Actual answer: A) Fall-Listen je Block, p nur für airline und Agentur (Owner, D9, 2026-09-24)
Accepted scope: Der Abschnitt „Auswertung: gepaart" wird umgeschrieben: je Block b, c und die namentliche Liste der c-Fälle (alt grün / neu rot) mit Befund-Art; Entscheidungsregel auf ganze Fälle (wie D3) für alle Blöcke; keine Einteilung in Quote-/kaputt-Blöcke mehr; McNemar exakt (zweiseitig, α = 0,05) nur als Zusatzzeile für `airline` und Agentur. Das Push-2-Tor bleibt: c-Liste der beiden alten Suiten leer oder je Fall begründet.
History: none

### R11: Zeitstempel und Wanddauer je Aufruf
Finding: Außenstimme 6 und 8, P2/P3, confidence 8/10, `TODOS.md:80-88` (Kippe „bei ~30 Aufrufen am Stück", kein Nachweis einer Prozessgrenze) und `agent_base.py` (`chamaeleon_website_tool_base` per `ttl_cache` 24 h, erste Seite eines Paares kalt, zweite warm). Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: „~25 Aufrufe je Prozess" wird als Reset-Grenze angenommen; „alles konstant außer dem Prompt" übersieht die Cache-Asymmetrie innerhalb des Paares. Nichts davon ist im Protokoll sichtbar. Keine Freigabe.
Runtime evidence: Ob die Leerantwort an der Aufrufdichte (Zeitfenster) oder an der Prozesslebensdauer hängt, ist ungemessen (Checkpoint-Notiz „ungeklärt"). Die Wanddauer je Seite zeigt Cache-kalt/warm und Website-Timeouts direkt.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R11 Protokoll je Aufruf | Ausgang, leer, Fehler, Tool-Aufrufe, Hash | zusätzlich: Zeitstempel (UTC) je Aufruf und Wanddauer je Seite; Bericht weist je Block Median-Dauer alt/neu und Leerantworten je Aufrufrate aus | wie bisher, keine Zeitdaten | erst prüfen, ob `call_stream` schon Zeiten liefert | offen lassen |
| Kosten | — | 0 Aufrufe, ~10 Zeilen im Treiber | 0 | 0 | 0 |
| R1–R10 | approved | fix | fix | fix | fix |

Question D10:
D10 — Zeitstempel und Wanddauer je Aufruf mitschreiben?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Zwei Annahmen im Plan sind ungeprüft: dass ein neuer Prozess die Leerantwort-Kippe zurücksetzt, und dass beide Seiten eines Paares gleiche Bedingungen haben (die erste Seite holt Webseiten kalt, die zweite aus dem Cache). Beides wird sichtbar, wenn der Treiber je Aufruf Uhrzeit und Dauer notiert. Kostet keine Modellaufrufe.
Stakes if we pick wrong: Häufen sich Leerantworten trotz Blockteilung, weiß niemand, ob es an der Rate oder am Prozess lag; und ein Website-Timeout auf der kalten Seite bleibt als „Prompt-Effekt" unerkannt.
Recommendation: A, weil es zehn Zeilen kostet und beide offenen Annahmen mit derselben Kampagne beantwortet.
Completeness: A=10/10, B=3/10, C=5/10, D=0/10
Pros / cons:
A) Zeitstempel und Wanddauer je Seite protokollieren (recommended)
  ✅ Leerantworten lassen sich gegen die Aufrufrate auftragen; die Prozessgrenzen-Frage wird mit der Kampagne beantwortet
  ✅ Median-Dauer alt/neu je Block macht Cache-kalt/warm und Timeouts sichtbar, ohne die Messung zu ändern
  ❌ Zwei Spalten mehr im Protokoll und im Bericht
B) Keine Zeitdaten
  ✅ Schlankstes Protokoll
  ✅ Nichts zu bauen
  ❌ Beide Annahmen bleiben ungeprüft; bei Leerantworten gibt es keine Diagnose
C) Erst prüfen, ob der Stream Zeiten liefert
  ✅ Vermeidet doppelte Messung, falls vorhanden
  ✅ Kurze Probe
  ❌ `call_stream` liefert keine Zeiten (nur `response`/`tool_call`/`error`-Events); der Treiber misst ohnehin außen
D) Offen lassen
  ✅ Nichts festgelegt
  ✅ Andere Entscheidungen unberührt
  ❌ Wird beim Bau vergessen und ist nachträglich nicht zu holen
Net: Billige Diagnose (A) gegen blindes Protokoll (B) gegen unnötige Probe (C) gegen Vergessen (D).
Header: Zeitdaten je Aufruf
Options:
A) Zeitstempel und Wanddauer je Seite protokollieren (recommended)
✅ Leerantworten lassen sich gegen die Aufrufrate auftragen; die Prozessgrenzen-Frage wird mit der Kampagne beantwortet. ✅ Median-Dauer alt/neu je Block macht Cache-kalt/warm und Timeouts sichtbar, ohne die Messung zu ändern. ❌ Zwei Spalten mehr im Protokoll und im Bericht.
B) Keine Zeitdaten
✅ Schlankstes Protokoll. ✅ Nichts zu bauen. ❌ Beide Annahmen bleiben ungeprüft; bei Leerantworten gibt es keine Diagnose.
C) Erst prüfen, ob der Stream Zeiten liefert
✅ Vermeidet doppelte Messung, falls vorhanden. ✅ Kurze Probe. ❌ `call_stream` liefert keine Zeiten (nur `response`/`tool_call`/`error`-Events); der Treiber misst ohnehin außen.
D) Offen lassen
✅ Nichts festgelegt. ✅ Andere Entscheidungen unberührt. ❌ Wird beim Bau vergessen und ist nachträglich nicht zu holen.

State: approved
Actual answer: A) Zeitstempel und Wanddauer je Seite protokollieren (Owner, D10, 2026-09-24)
Accepted scope: Der Treiber schreibt je Aufruf Zeitstempel (UTC) und Wanddauer der Seite ins Protokoll (Zeile je Fall × Seite: Fall-ID, Seite alt/neu, Ausgang, Befund-Art, leer-Wiederholung, Tool-Aufrufe, Dauer, Zeitstempel, Prozess-Nr.). Der Bericht weist je Block Median-Dauer alt/neu aus und trägt Leerantworten gegen die Aufrufrate auf.
History: none

### R12: Pause zwischen den Prozessen
Finding: Außenstimme 6, P2, confidence 7/10 (Annahme, nicht belegt), `TODOS.md:80` („bei ~30 Gemini-Aufrufen am Stück"); ob ein Prozessneustart die Kippe zurücksetzt, ist nirgends gemessen. Reviewer: Claude-Subagent (Outside Voice)
Plan baseline: „Blockgröße: ein Prozess darf höchstens ~12 Fälle fahren" — setzt voraus, dass der Prozess die Einheit ist. Läuft die Kippe über ein Zeitfenster, reproduzieren drei Prozesse Rücken an Rücken dieselbe Dichte, und D1 (Wiederholung) treibt jeden Prozess an die Kante. Keine Freigabe.
Runtime evidence: Einzelne Läufe waren 6/6 grün, die Suite 0/3 (`TODOS.md:85-86`). Das ist mit beiden Erklärungen (Prozess oder Rate) vereinbar. Mit R11 wird die Frage nach der Kampagne beantwortbar.
Comparison grid:

| Choice | Current | A | B | C | D |
|---|---|---|---|---|---|
| R12 Pause zwischen Prozessen | keine | feste Pause von 60 s zwischen zwei Prozessen (Konstante im Treiber, per Umgebungsvariable überschreibbar); Pausen im Protokoll vermerkt | keine Pause, Prozesse Rücken an Rücken | erst einen Zwei-Prozess-Probelauf mit und ohne Pause fahren, dann wählen (~48 Aufrufe extra) | offen lassen |
| Laufzeit der Kampagne | ~10 Prozesse | +~10 min | +0 | +~1 h Probelauf | — |
| R1–R11 | approved | fix | fix | fix | fix |

Question D11:
D11 — Feste Pause zwischen den Kampagnen-Prozessen?
Project/branch/task: chamaeleon-webbot auf main, Plan „Verschränkte Messung" für W3.
ELI10: Der Plan teilt die Kampagne in kleine Prozesse, damit Gemini nicht in leere Antworten kippt. Ob die Kippe am Prozess oder an der Aufrufrate hängt, weiß niemand. Eine Minute Pause zwischen den Prozessen kostet zehn Minuten Laufzeit und nimmt der Rate-Erklärung den Zahn. Die Zeitdaten aus D10 zeigen hinterher, ob es nötig war.
Stakes if we pick wrong: Ohne Pause und bei Rate-Erklärung häufen sich Leerantworten in jedem Prozess, die Wiederholungen aus D1 verschärfen es, und das Push-2-Tor läuft auf Rauschen.
Recommendation: A, weil zehn Minuten Laufzeit die billigste Versicherung gegen eine ungemessene Annahme sind und die Pause per Variable auf null gesetzt werden kann.
Completeness: A=9/10, B=4/10, C=10/10, D=0/10
Pros / cons:
A) 60 s Pause, überschreibbar (recommended)
  ✅ Deckt beide Erklärungen ab, ohne die Messung zu verändern; im Protokoll sichtbar
  ✅ Ein Zehntel Zusatzlaufzeit, keine Zusatzaufrufe (human: ~10 min / CC: ~2 min)
  ❌ Bleibt eine Annahme (60 s statt gemessen); bei Rate-Erklärung mit längerem Fenster reicht sie eventuell nicht
B) Keine Pause
  ✅ Kürzeste Kampagne
  ✅ Wenn die Prozess-Erklärung stimmt, völlig ausreichend
  ❌ Setzt auf eine ungemessene Annahme, und D1 treibt jeden Prozess zusätzlich an die Grenze
C) Probelauf mit und ohne Pause
  ✅ Beantwortet die Frage empirisch vor der Kampagne
  ✅ Liefert gleich die Kalibrierung der Pausenlänge
  ❌ ~48 Zusatzaufrufe und eine Stunde, für eine Frage, die R11 aus der Kampagne selbst beantwortet
D) Offen lassen
  ✅ Nichts festgelegt
  ✅ Andere Entscheidungen unberührt
  ❌ Der W3-Agent fährt dann Rücken an Rücken, weil nichts anderes dasteht
Net: Billige Versicherung (A) gegen Vertrauen (B) gegen teure Gewissheit (C) gegen Zufall (D).
Header: Prozess-Pause
Options:
A) 60 s Pause, überschreibbar (recommended)
✅ Deckt beide Erklärungen ab, ohne die Messung zu verändern; im Protokoll sichtbar. ✅ Ein Zehntel Zusatzlaufzeit, keine Zusatzaufrufe (human: ~10 min / CC: ~2 min). ❌ Bleibt eine Annahme (60 s statt gemessen); bei Rate-Erklärung mit längerem Fenster reicht sie eventuell nicht.
B) Keine Pause
✅ Kürzeste Kampagne. ✅ Wenn die Prozess-Erklärung stimmt, völlig ausreichend. ❌ Setzt auf eine ungemessene Annahme, und D1 treibt jeden Prozess zusätzlich an die Grenze.
C) Probelauf mit und ohne Pause
✅ Beantwortet die Frage empirisch vor der Kampagne. ✅ Liefert gleich die Kalibrierung der Pausenlänge. ❌ ~48 Zusatzaufrufe und eine Stunde, für eine Frage, die R11 aus der Kampagne selbst beantwortet.
D) Offen lassen
✅ Nichts festgelegt. ✅ Andere Entscheidungen unberührt. ❌ Der W3-Agent fährt dann Rücken an Rücken, weil nichts anderes dasteht.

State: approved
Actual answer: A) 60 s Pause, überschreibbar (Owner, D11, 2026-09-24)
Accepted scope: Zwischen zwei Kampagnen-Prozessen wartet der Orchestrator 60 s (Konstante im Treiber, per `EVAL_PAUSE_S` überschreibbar, 0 erlaubt). Jede Pause wird mit Zeitstempel im Protokoll vermerkt.
History: none

Approval readiness: PASS — R1 (D1=C), R2 (D2=A, deckt R4), R3 (D3=A), R5 (D4=A), R6 (D5=A), R7 (D6=A), R8 (D7=A), R9 (D8=A), R10 (D9=A), R11 (D10=A), R12 (D11=A). Korrekturen ohne Frage: Außenstimme 7 und 9. Keine offenen Remedies.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | Claude-Subagent (Rückfall; Codex `model_unusable`) | Independent 2nd opinion | 3 | unavailable (kein Fremdmodell) | 9 Befunde: 7 entschieden (R6–R12), 2 als Korrektur übernommen |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | issues_open | 14 issues (5 eigene + 9 Außenstimme), 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex, phase plan-review, unavailable (CLI kann die Modell-Liste nicht dekodieren, `MODEL_UNUSABLE`; Fix: `GSTACK_CODEX_MODEL=<unterstütztes Modell>`). Native Rückfall-Prüfung durch einen Claude-Subagenten (Plan-Agent, frischer Kontext) lief durch und lieferte 9 Befunde, davon 4 bestätigte Feasibility-Lücken im Treiber (alte Vorlage, Tool-Zählung, Fallquellen, Ausgänge). Das ersetzt keine Fremdmodell-Abdeckung.
- **VERDICT:** ENG REVIEW durchgeführt (Abschnitt „Verschränkte Messung", 2026-09-24): 11 Entscheidungen D1–D11 vom Owner beantwortet und eingearbeitet, 2 Korrekturen ohne Frage, 0 kritische Lücken. Frühere Läufe: 2026-09-21 (20 Entscheidungen, D18 fallengelassen 2026-09-23). Nicht CLEAR, weil Befunde in Arbeit gemappt sind (T15–T20) — eng review required.

NO UNRESOLVED DECISIONS
