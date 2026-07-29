# Kunden-Modus — TourOne data access surface

What customer data the chatbot **can** reach through the TourOne API, what it
**actually** uses, and — the part that matters — **what of it leaves the server
and goes out to Gemini**.

> **The boundary that matters is the model boundary.** Fetching the full record
> from TourOne is fine: it stays server-side, in process memory, and is never
> persisted. What must stay minimal is what we put into a Gemini request. Judge
> every change to `kundendaten.py` against that line, not against the API call.

Captured live against the reference customer on **2026-07-18**. Re-run
`docs/explore_kunde.py` (redacts values, shows only field shape) to refresh if
the API changes.

**Scope.** This file answers *what data is reachable and what crosses to Gemini*.
It does **not** answer *who is allowed to be that customer* — that is the auth
question, and `docs/kunden-auth-spec.md` owns it. See "Rules for changing this"
below for how the two meet today.

## Reference customer

- **`999999999`** (nine nines) — the designated test customer, fully populated.
  Use this for any live inspection.
- **`99999999`** (eight nines) and every other unknown Kundennummer → `[]` with
  HTTP 200. An empty list *is* the "unknown ID" signal; a real customer comes
  back as an object. (Contract confirmed live; see `kundendaten.py`.)

## What we actually access (the necessary set)

Authenticated GETs, fired **only** when a logged-in customer asks about their own
bookings (closure tool, no ID parameter — the selector picks *which* of the
customer's own bookings, never *whose* — see `kundendaten.py`). The rough list
(`details=false`) uses Hop 1 only; the detail view (`details=true`) adds Hop 2
per selected booking.

### Hop 1 — `GET /get/adresse?kundennummer=<kunden_id>`

Consumed fields (everything else in the response is received but **ignored**):

| Field | Use |
| --- | --- |
| *(dict vs. list)* | response shape is the known/unknown-customer signal |
| `buchungen[]` | list of the customer's bookings (past + upcoming) |
| `buchungen[].vorgang` | key for hop 2 + shown as booking number |
| `buchungen[].bisDat` | past/upcoming split + shown in header |
| `buchungen[].vonDat` | sort order + shown in header |
| `buchungen[].reiseCode` | key for the trip title, and last-resort title itself |

`buchungen[].beschreibungen` exists in this hop but is **always an empty list**
(measured live 2026-07-30 against the reference customer), so the rough list has
no title of its own. It resolves `reiseCode` through the travel index
(`travel_index.get_titel_for_code`, an in-memory peek at the catalogue built from
`/get/reiseliste` — no extra request, no new data leaving TourOne) and falls back
to the raw code on a miss. Without that, customers were shown internal codes like
`COSAN_NEU` instead of `San Agustín`.

The lookup is **exact-match only**. 442 of 448 suffixed codes share their base
code's title, but the 6 exceptions are genuinely different trips (`NAFAM_DRR` is
"Deutscher Reisering Jubiläumsreise", `NAFAM` is "Erfahrungsreise"), and in a
booking context a confidently wrong trip name is worse than an unresolved code.

### Hop 2 — `GET /get/buchung?vorgangsNummer=<vorgang>` (only on `details=true`, once per selected booking)

| Field | Use |
| --- | --- |
| `status` | `"OK"` → "gebucht"; anything else (e.g. `"XX"`) → "storniert", detail stops there |
| `beschreibungen[].titel` | trip title for the header |
| `persAdult` / `persChild` / `persBaby` / `personen` | output — Reisende |
| `preis` | output — Gesamtpreis (Zahlstand) |
| `anzahlungBetrag` / `anzahlungDat` | output — Anzahlung + Fälligkeit |
| `restBetrag` / `schlussZahlungDat` | output — offener Betrag + Fälligkeit |
| `eingangBetrag` | output — bereits eingegangen |
| `flugdaten[].{flugnr,airline,vonCo3Code,nachCo3Code,abflug,ankunft}` | output (the 6 `FLUG_FELDER`) |
| `flugdaten[].rang` | sort only (not shown) |

`FLUG_FELDER` (6 flight fields) plus the Zahlstand set above are the enforced
whitelist. `rang` is read for ordering but never emitted. **`pnrFileKey` (PNR),
`sitzplatz`, `provision`, all tax/currency (`*Cy`, `steuer*`) fields and internal
IDs are deliberately excluded.**

## What reaches Gemini (the boundary that matters)

In Kunden-Modus exactly three things go into the model request:

1. **The system prompt's `kunden_modus_block`** — static instruction text
   (read-only access, when to call the tool, what to defer to the
   Erlebnisberater). Contains **no customer data**; it is gated by a plain
   `bool`.
2. **The customer's own chat messages** — whatever they type. (They may
   volunteer PII themselves; that is their choice and outside our control.)
3. **The `buchungen_tool` result** — formatted German text, nothing else. Rough
   list: trip title, date range, booking number, past/upcoming marker. Detail
   view adds: status, Reisende (headcount), the **Zahlstand** (Gesamtpreis,
   Anzahlung + date, offener Betrag + date, bereits eingegangen) and the six
   flight fields.

   The trip title may come from the travel index rather than the booking (see
   Hop 1 above). That does **not** widen this boundary: the index holds the public
   website catalogue, so the substituted value is public data keyed by a code the
   booking already supplied — the same one field, more readable.

### Structurally excluded from Gemini

| Excluded | Mechanism |
| --- | --- |
| The Kundennummer (`kunden_id`) | Only `is_kunde=bool(kunden_id)` reaches `format_system_prompt` (`agent.py:127`); the ID itself lives in the tool **closure** (`agent.py:147-148`, `make_buchungen_tool(kunden_id)`) and is not a tool parameter, so the model can neither see it nor choose whose data is fetched (`agent_base.py:703-705` states this as an invariant). |
| Scraped page content | `page_content` is injected only when `is_agentur` (`agent_base.py:810`), and `kunden_id` is forced to `""` on agentur requests (`app.py:109`). The two modes are **mutually exclusive**, so a logged-in customer's MeinChamäleon page is never scraped into the prompt. |
| Everything else from both endpoints | The whitelist (6 flight fields + the customer's own Zahlstand) — fellow-traveller PII (`teilnehmerliste`), emergency contact (`adrNotfallKontakt`), `chroniken` notes, `provision`/agency fields, tax/currency detail, `pnrFileKey`, `sitzplatz` are never formatted into the tool result. |

Raw `/get/adresse` and `/get/buchung` JSON exists only in `fetch_buchungen_text`
locals and is never returned to the model.

### Persistence (secondary, but consistent)

- **Supabase `chats`**: only the user message + assistant reply (+ rec previews).
  No tool arguments, no tool result, no raw TourOne JSON.
- **stdout**: `[tool_call] session=… tool=… is_kunde=…` — no arguments, no
  customer data, no Kundennummer. **DEBUG-only** since 2026-07-18 (it clogged
  prod logs), so prod emits nothing for Kunden-Modus at all.

## Full available surface — what must never cross into the model

Both endpoints hand back far more than the six flight fields. This all arrives
server-side on every lookup (accepted, see above) — it is listed here so the
set we must keep out of Gemini is documented, not discovered later.

### `/get/adresse` also returns

- **Customer PII**: `anrede`, `titel`, `vorname`, `nachname`, `geschlecht`,
  `gebDat`, `firma`, `strasse`, `zusatz`, `plz`, `ort`, `bundesland`, `land`,
  `cca2`, `nationalitaet`, `sprache`, `email`, `emailGeschaeftlich`, `tel`,
  `telGeschaeftlich`, `handy`, `fax`, `homepage`
- **Account / marketing / flags**: `kdSeit`, `kundeVonAgentur`, `group`,
  `newsletterstatus`, `buchungsSperre`, `selektionSperre`, `kontaktSperre`,
  `rueckkehrKontaktSperre`, `neuDat`, `aenDat`, `type`, `id`
- **Club / loyalty**: `clubTitle`, `clubDesc`, `clubStufeAenDat`, `bookings`,
  `bookAdjust`, `bookAdjusted`
- **`teilnehmerliste[]`** — **fellow travellers' full PII** (`vorname`,
  `nachname`, `gebDat`, `anrede`, `email`, `tel`, `mobil`, `strasse`, `plz`,
  `ort`, `land`, `reEmpf`, `id`)
- **`merkmale[]`** — CRM tags/segments (`code`, `gruppe`, `bezeichnung`, `typ`,
  `lang`, `aktiv`, …)
- **`gutscheine[]`** — vouchers (`code`, `betrag`, `kontingent`, `eingeloest`,
  `gueltigVonDat`, `gueltigBisDat`, `kamCode`, `kamBez`, …)
- **`chroniken[]`** — **internal agent notes / correspondence** incl. free-text
  `betreff` and `text`, `vertraulichkeitsStufe`, `autorId`, `mailFrom/To/Cc/Bcc`

### `/get/buchung` also returns

- **Financials (extensive)**: `preis`, `provision`, `zahlung`, `rechBetrag`,
  `restBetrag`, `eingangBetrag`, `anzahlungBetrag`/`Dat`,
  `schlussZahlungBetrag`/`Dat`, all tax fields (`steuerBetrag`, `mwstSteuerBetrag`,
  `revSteuerBetrag`, `steuerProzenz`), every `…Cy` currency variant,
  `wahIsoCode`/`wahKurs`, `zahlsystem`/`zahlungArt`/`zahlungBrand`, `inkassoArt`,
  `mahnstufe`, `gutCode`/`gutschrift…`, `rechnung…`, `fibuSperre`
- **Contact snapshot** (`adr…`): full name/address/phone/email again, plus
  **`adrNotfallKontakt`** (emergency contact)
- **Agency / agent / consultant**: `agtNr`, `mandantAgtNr`, `agenturenIds[]`,
  `expId/Krz/Name/Email/Tel`, `benId/benutzer/benTel/benEmail`
- **Trip detail**: `katCode`, `anreise`, `abflughafenCode`, `personen`/
  `persAdult`/`persChild`/`persBaby`, `herkunft`, `beschreibungen[]` (`titel`,
  `untertitel`, `text`), `teilnehmerIds[]`, `vrrHash`, `optionDat`, `bookNotiz`
- **`chroniken[]`** — internal notes as above, each with a nested `workflow`
  (`workflowBezeichnung`, `workflowText`, `erledigenBis`, …)
- **`flugdaten[]`** — the source of our six fields; full shape also carries
  `status`, `sitzplatz`, `pnrFileKey`, `id` (all excluded from output)

## Rules for changing this

**Explicitly accepted, do not "fix":** we fetch the whole customer + booking
object. The API offers no field projection, and it does not matter — the surplus
stays server-side in process memory, is never persisted, and never reaches
Gemini. Do not spend effort narrowing the API call.

**The invariant to protect:** nothing beyond the six whitelisted flight fields
(plus trip title and date range) may enter a Gemini request. Any change that
widens the model boundary needs a deliberate decision:

- Adding a field to `FLUG_FELDER`.
- Giving `buchungen_tool` a parameter that selects a *customer*, or otherwise
  letting the model influence *whose* record is looked up — that breaks the
  closure guarantee. (`auswahl`/`anzahl`/`details` are fine: they only slice the
  bound customer's own bookings.)
- Putting the `kunden_id` into the system prompt or any tool argument.
- Injecting `page_content` on a Kunden-Modus request, or otherwise allowing
  `is_agentur` and `is_kunde` to be true at once — a MeinChamäleon page can
  itself contain PII, so mode exclusivity is a privacy control, not a detail.
- Logging tool results or raw responses to Supabase/stdout.

**Where `kunden_id` comes from — server-verified since 2026-07-29.** This
document describes *what* is reachable once a `kunden_id` exists; who is allowed
to name one was the separate IDOR question, and the server half has shipped:

| | Source of `kunden_id` | Exposure |
| --- | --- | --- |
| **Before 2026-07-29** | a `kunden_id` in the `/chat/stream` body, trusted as-is | anyone knowing a valid Kundennummer reached the whole surface documented here — **by crafting the request directly**, see below |
| **Live now** | `kunden_auth.resolve(session_id)` only, bound from a `ss.php`-verified MeinChamäleon session (`app.py:109`); a body `kunden_id` is **ignored outright** | a spoofed ID reaches nothing. And because the widget on `cham-chatbot` `main` never calls `/kunde/auth`, `resolve` returns `""` for every customer request — so **nothing** in this document is currently reachable in production |

**Correction, 2026-07-30.** An earlier version of this table said the *live widget*
asserted `kunden_id`. It never did: the widget deployed to customers is older than
that feature — checked against the live page, which contains no `kunden_id`, no
`phpsessid` and no `/kunde/auth`. The IDOR was real but reachable only by building
the POST yourself, which is exactly why it mattered: the exposure never depended on
what the widget chose to send. Beware `grep` here — the page is ISO-8859-1 and grep
treats it as binary, silently reporting zero matches for everything; count in Python
with an explicit encoding.

The client-asserted path is gone, not merely deprecated: the field is read
nowhere. What is still outstanding is the widget change (M5) that supplies a real
session — merged to `develop`, so it is live on the dev host but not for customers.
The surface here is therefore dark in production rather than customer-accessible.
**`docs/kunden-auth-spec.md` is authoritative for that status** — including the two
residual risks that survive the fix (a Reisebook inline re-login can leave a stale
binding for up to 12h, and a dev-host login is a valid auth path for the production
chat; both in spec §8).

**2026-07-30 — display caps removed (owner decision).** The bot must be able to
see **every** booking of its customer, in full. Previously `MAX_DETAIL=5` capped
the detail view and `OVERVIEW_CAP=25` the rough list. Because `anzahl` slices only
from the front and there is no offset, those were not per-call limits but hard
ceilings: at most the 5 soonest upcoming plus the 5 most recent past could ever be
seen in detail, no matter how often the model asked. A long-standing customer's
older trips were unreachable.

Both caps are gone. Hop 2 now runs concurrently (`DETAIL_PARALLEL=8`) so latency
stops being linear in the booking count — measured against a 0.15s-per-request
stub: 20 bookings 0.95s instead of 3.50s, 40 bookings 1.26s instead of 6.50s. The
bound limits only how many requests hit TourOne at once, never how much the
customer can see. A booking whose Hop 2 fails is skipped and the gap is stated in
the answer, so a partial list never reads as complete.

**This widens what reaches Gemini in volume, not in kind** — same whitelisted
fields per booking, but now potentially dozens of blocks in one tool result.

**2026-07-30 — ordering fixed, "next trip" now labelled.** `auswahl="alle"` (the
default) sorted by `vonDat` **descending**, so with several future bookings the
*furthest away* came first and `anzahl=1` returned the last trip while the model
called it "deine nächste Reise" — observed live: Gobi (08.08.2026) was next, the
bot named San Agustín (17.08.2026). `alle` now lists upcoming soonest-first, then
past newest-first, and the rough list marks the first not-yet-started booking
`nächste Reise` so the model does not have to infer it from ordering. A currently
running trip keeps `läuft gerade` and is never the "next" one.

**2026-07-24 — upcoming-only filter removed (owner decision).** The tool
returns past *and* upcoming bookings, on the explicit assumption
that the `kunden_id` is not guessable. Under a spoofed ID this widens the
exposure from a single upcoming trip to the customer's whole booking history;
still only the six whitelisted flight fields + title/dates reach Gemini. That
assumption is what the auth work (spec §2) replaces with a verified session.

**2026-07-27 — flights tool → `buchungen_tool`; Zahlstand now crosses to Gemini
(owner decision).** `kunden_fluege_tool` folded into `buchungen_tool` (rough list
+ detail view, selector `auswahl`/`anzahl`/`details`; closure preserved — the
selector only slices the customer's *own* bookings). The detail view widens the
model boundary: the customer's **Zahlstand** (Gesamtpreis, Anzahlung, offener
Betrag + due dates, bereits eingegangen), status, and headcount now reach Gemini
in addition to the flight fields — grounded in the chat analysis (payment is the
#1 unserved data lookup). Fellow-traveller PII, emergency contact, chroniken,
provision/agency and tax/currency detail stay out. Under a spoofed ID the
exposure now includes the customer's financials, which is why the auth work is
the head of this feature's critical path (`docs/kunden-auth-spec.md`).
