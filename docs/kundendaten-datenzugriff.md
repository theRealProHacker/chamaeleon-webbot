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

## Reference customer

- **`999999999`** (nine nines) — the designated test customer, fully populated.
  Use this for any live inspection.
- **`99999999`** (eight nines) and every other unknown Kundennummer → `[]` with
  HTTP 200. An empty list *is* the "unknown ID" signal; a real customer comes
  back as an object. (Contract confirmed live; see `kundendaten.py`.)

## What we actually access (the necessary set)

Two authenticated GETs, fired **only** when a logged-in customer explicitly asks
for their own flights (closure tool, no ID parameter — see `kundendaten.py`).

### Hop 1 — `GET /get/adresse?kundennummer=<kunden_id>`

Consumed fields (everything else in the response is received but **ignored**):

| Field | Use |
| --- | --- |
| *(dict vs. list)* | response shape is the known/unknown-customer signal |
| `buchungen[]` | list of the customer's bookings |
| `buchungen[].vorgang` | key for hop 2 |
| `buchungen[].bisDat` | keep only upcoming trips (`bisDat >= heute`, Europe/Berlin) |
| `buchungen[].vonDat` | sort order |
| `buchungen[].reiseCode` | fallback trip title |

### Hop 2 — `GET /get/buchung?vorgangsNummer=<vorgang>` (max 3 upcoming bookings)

| Field | Use |
| --- | --- |
| `status` | must be `"OK"`; anything else (e.g. `"XX"` = storniert) → skip |
| `beschreibungen[].titel` | trip title for the header |
| `flugdaten[].flugnr` | output (whitelisted) |
| `flugdaten[].airline` | output (whitelisted) |
| `flugdaten[].vonCo3Code` | output (whitelisted) |
| `flugdaten[].nachCo3Code` | output (whitelisted) |
| `flugdaten[].abflug` | output (whitelisted) |
| `flugdaten[].ankunft` | output (whitelisted) |
| `flugdaten[].rang` | sort only (not shown) |

`FLUG_FELDER` in `kundendaten.py` is the enforced whitelist (6 fields). `rang`
is read for ordering but never emitted. **`pnrFileKey` (PNR) and all internal
IDs are deliberately excluded.**

## What reaches Gemini (the boundary that matters)

In Kunden-Modus exactly three things go into the model request:

1. **The system prompt's `kunden_modus_block`** — static instruction text
   (read-only access, when to call the tool, what to defer to the
   Erlebnisberater). Contains **no customer data**; it is gated by a plain
   `bool`.
2. **The customer's own chat messages** — whatever they type. (They may
   volunteer PII themselves; that is their choice and outside our control.)
3. **The `kunden_fluege_tool` result** — formatted German text, nothing else:
   trip title, trip date range, and per flight `flugnr`, `airline`,
   `vonCo3Code`, `nachCo3Code`, `abflug`, `ankunft`.

### Structurally excluded from Gemini

| Excluded | Mechanism |
| --- | --- |
| The Kundennummer (`kunden_id`) | Only `is_kunde=bool(kunden_id)` reaches `format_system_prompt` (`agent.py`); the ID itself lives in the tool **closure** and is not a tool parameter, so the model can neither see it nor choose whose data is fetched (`agent_base.py:567-569` states this as an invariant). |
| Scraped page content | `page_content` is injected only when `is_agentur` (`agent_base.py:608`), and `kunden_id` is forced to `""` on agentur requests (`app.py:99`). The two modes are **mutually exclusive**, so a logged-in customer's MeinChamäleon page is never scraped into the prompt. |
| Everything else from both endpoints | The `FLUG_FELDER` whitelist — PII, financials, fellow travellers, `chroniken` notes, `pnrFileKey`, `sitzplatz` are never formatted into the tool result. |

Raw `/get/adresse` and `/get/buchung` JSON exists only in `fetch_fluege_text`
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
- Giving `kunden_fluege_tool` a parameter, or otherwise letting the model
  influence *which* customer is looked up — that breaks the closure guarantee.
- Putting the `kunden_id` into the system prompt or any tool argument.
- Injecting `page_content` on a Kunden-Modus request, or otherwise allowing
  `is_agentur` and `is_kunde` to be true at once — a MeinChamäleon page can
  itself contain PII, so mode exclusivity is a privacy control, not a detail.
- Logging tool results or raw responses to Supabase/stdout.

**Open:** the `kunden_id` is client-asserted and unverified, so the full surface
above is the exposure if any of those mechanisms were loosened. Fix is
server-side verification — see the IDOR item in `TODOS.md`.
