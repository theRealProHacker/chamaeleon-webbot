# Agentur-Modus — implementation plan for the TourOne agency API calls

**This is the authoritative document for this feature.** Self-contained: the
remaining work can be built from this file alone. **No other file may carry this
feature's status** — they defer here. (Duplicated status is what let the
Kunden-Modus docs contradict each other; same rule applies.)

- `docs/kunden-auth-spec.md` — the sibling feature this one mirrors. Read it
  first: every security property here is a copy of one established there.
- `docs/kundendaten-datenzugriff.md` — the model-boundary doc. §6 below is the
  agency half of it and must be folded in when this ships.
- `~/.gstack/projects/theRealProHacker-chamaeleon-webbot/rharvey-kunden-begruessung-plan-20260714.md`
  — the frontend-only greeting pattern (`oKundenData.vorname`, backend
  untouched). D10/§6b is the same pattern for `oAgtData.exp.vorname`; read it
  before writing the widget half.

Status: **PLAN ONLY — nothing implemented.** Written 2026-07-30.

**M0 is DONE — the session key is `SESSION_AGTNR`** (owner read a logged-in
agentur session, 2026-07-31). Value is bare digits, so `_AGTNR_RE_VALUE` holds.
Details and the other keys that session carries: §12.

---

## 1. What this adds

A logged-in Reiseprofi (Reisebüro, Expedient*in, mobile Reiseberater*in) on
`agt.chamaeleon-reisen.de` asks the bot about **their own agency's bookings** and
gets a real answer — the list, the status, the travel dates, the payment state —
instead of the phone number in `faqs/agentur.md`.

Identity is **derived server-side from a verified session**, never asserted by
the client. Today `is_agentur` is content-selection only and `app.py:57` says so
explicitly: Origin/Referer/`current_url` are all spoofable with curl, which is
why `faqs/agentur.md` must stay generic. This plan adds the verified channel that
per-agency data requires.

## 2. Decisions already made (owner, 2026-07-30)

| # | Decision |
|---|---|
| D2 | **New `POST /agentur/auth`**, mirroring `/kunde/auth`, with a **separate binding store**. One dict holding both identity kinds is how a customer binding gets read as an agency one — `resolve()` cannot tell them apart. |
| D5 | Bind the **Agenturnummer** (agency-level). Expedient-level scoping is recorded as an open question (§9), not built. |
| — | The bookings **list** is the feature ("list just like for Kunden"), not a single-booking lookup. |
| — | Server-side caching was investigated on request and is **not needed** — see §3, the agency filter is server-side and answers in ~0.12s. |
| D8 | **Provision: the amount is a fact, the system is a referral.** Per-booking commission values are IN the whitelist; agency-level commission *schemes* and any question about how a rate is determined or changed stay with the Vertriebsteam. The split is enforced in two different places — see §6a. |
| D9 | **Traveller identity is IN** — `TEILNEHMERS[]` name, Anrede, number, per-head price. Deliberately diverges from Kunden-Modus, which excludes `teilnehmerliste`. Traveller birth dates, passport-adjacent and per-traveller contact fields remain out (§6). |
| D10 | **Expedient: display name in the widget only, for now.** The backend does **not** bind, fetch or receive the Expedient — no `SESSION_EXPID`, no `/get/expedient` call, nothing in the whitelist, nothing in the prompt. The widget reads `oAgtData.exp.vorname` client-side and renders it itself. See §6b. |

## 3. Measured API contract (empirical, 2026-07-30)

Verified with live calls against `api.tourone.de` using the existing
`TOURONE_BEARER_TOKEN`. Shapes and counts only; no customer or agency values were
retained. The OpenAPI spec (`/api/doc.json`) documents endpoints and params but
**every response schema is an empty stub**, so all of this is empirical.

### Hop 1 — the agency's bookings

`GET /get/buchungLeistungenListe?agenturNummer=<agtNr>`

This is the per-agency booking path. It is **the only one**: `/get/buchungListe`
has no agency filter (all of `agtNr`, `agenturNr`, `agenturnummer`, `agenturId`,
`agtId`, `agentur` were tried and **ignored** — byte-identical rows to the
unfiltered baseline, `gesamt` unchanged at 223588), and `/get/agentur` carries no
bookings at all (`buchungen`, `showBuchungen`, `buchungListe` flags all ignored;
103 keys, none of them bookings).

- **The filter is real and sound.** Zero `vorgangsNummer` overlap across sampled
  agencies. Independently cross-checked: for 4/4 sampled bookings,
  `/get/buchung.agtNr` equals the queried `agenturNummer`.
- **Scope is exactly one agency — measured on every row, not sampled.**
  `ACTION.AgenturNummer` equalled the queried number on **all rows of 5 agencies
  (64 bookings), 0 exceptions**. So no booking, and therefore no traveller, from
  another agency appears. The three agency-ish fields on the payload are not
  peers and must not be mistaken for them:

  | Field | What it actually is |
  |---|---|
  | `ACTION.AgenturNummer` | the queried agency. The one to trust, and to re-check (G3). |
  | `mandantAgtNr` (hop 2) | **the Mandant — Chamäleon itself.** Took exactly **one distinct value across 3 different agencies** and never equals the agency's own number. It differing from `agtNr` is normal, not a leak. |
  | `MeldeAgenturNummer` (`LEISTUNGEN[]`) | **a third party's agency reference.** **48 distinct values over 353 occurrences across 8 agencies**, never the queried one. Not Chamäleon, not the queried agency — so it stays OUT of the whitelist (§6), and that exclusion is evidence-based, not precautionary. |

- **Oberagentur does NOT see its Unteragenturen's bookings.** Tested on an agency
  with a sub-agency: all 6 returned rows belonged to the Oberagentur itself, 0 to
  the sub. The filter is strictly the single number. This is a **product gap, not
  a safety property** — see §9.3.
- **Fast.** Median **0.12s**, 40 rows ≈ 96 KiB. (The unfiltered call takes 127s —
  the speed is the server-side filter doing its job.)
- **Complete history**, past and cancelled included. One sampled agency's rows
  spanned `vorgangNeuDat` 2007-10-24 … 2022-06-02.
- Envelope: `{"0": {...}, "1": {...}, ..., "anzahl": N}` — an **object, not an
  array**, same quirk as the other list endpoints. Iterate values, skip
  non-objects.
- `limit` / `offset` work and page disjointly.
- **`status[]` is IGNORED here** — `status[]=OK` and `status[]=XX` both returned
  all 191 rows for the same agency. Filter cancelled bookings in Python.

Row shape — 16 keys, but **the top-level `id`, `anforderung`, `leistung`,
`ekPreis`, `vkPreis`, `vondat`, `bisdat` are always `None`**. The data lives in
the nested `buchungLeistungen` envelope. Usable row-level keys:
`vorgangsNummer` (str), `vorgangsId` (int), `vorgangNeuDat`, `vorgangAenDat`,
`teilehmerNummern[]` (yes, the API misspells it), `xmlId`, `xmlNeuDat`.

`buchungLeistungen` has four sections:

| Section | Keys |
|---|---|
| `ACTION` | `Veranstalter`, `Reiseart`, `AgenturNummer`, `VorgangsNummer`, `GesamtPreis`, **`AgenturCommission`** |
| `KUNDE` | `VornameTitel`, **`StrasseHausNummer`**, **`Postleitzahl`**, **`Ort`**, **`TelefonNummer`** |
| `TEILNEHMERS[]` | `TeilnehmerNummer`, `Anrede`, `Name`, `PersonenPreis` |
| `LEISTUNGEN[]` | `VorgangsNummer`, `VorgangsId`, `Anforderung`, `Leistung`, `LeistungsBezeichnung`, `Unterbringung`, `Anzahl`, **`VonDatum`**, `leistungVonDat`, **`BisDatum`**, `leistungBisDat`, `TeilnehmerZuordnung`, `LeistungsStatus`, `LeistungsPreis`, `AgenturCommissionPreis`, and on some rows `MeldeAgenturNummer`, `AgenturCommissionProzent`, `LeistungKostentraeger` |

**Consequence for the design: hop 1 alone builds the whole coarse list.**
`LEISTUNGEN[].leistungVonDat` / `leistungBisDat` give the travel dates,
`LeistungsBezeichnung` the trip description, `LeistungsStatus` the status,
`KUNDE.VornameTitel` whose booking it is. **Not** `VonDatum` / `BisDatum`, and
**not** `LeistungsBezeichnung` alone — see §3.3, which measured both and found
each of them wrong in ways that throw no exception.

**It also means the payload is PII-heavy and commission-bearing from the first
call** — the end customer's street, postcode, town and phone, plus
`AgenturCommission`, arrive whether we want them or not. The whitelist in §6 is
therefore not optional bookkeeping; it is the whole control.

### 3.3 `LEISTUNGEN[]` — measured formats, types and collapse rules (2026-08-02)

Sampled live: 27 agencies tried, **15 with bookings** (1 hard-failed, §7),
**486 bookings**, **2131 `LEISTUNGEN[]` entries**. Aggregates only; no customer,
agency or date values were retained.

**Every value in `LEISTUNGEN[]` is a JSON string** — including `Anzahl`,
`LeistungsPreis`, `AgenturCommissionPreis` and `AgenturCommissionProzent`. The
sole exception is `VorgangsId` (`int`, 2131/2131). `ACTION.AgenturNummer` is
`str` on 486/486 bookings — but G3 must still normalise both sides, because one
measured tenant is not a type guarantee (§8).

Presence, out of 2131 entries: `VorgangsNummer`, `VorgangsId`, `Anforderung`,
`Leistung`, `Unterbringung`, `Anzahl`, `VonDatum`, `leistungVonDat`, `BisDatum`,
`leistungBisDat`, `TeilnehmerZuordnung` on **all 2131**; `LeistungsStatus`,
`LeistungsPreis`, `AgenturCommissionPreis` on 2127; `LeistungsBezeichnung` on
**2042 (89 missing)**; `MeldeAgenturNummer` 1698; `AgenturCommissionProzent`
1670; `LeistungKostentraeger` 1571 (and its only values are `""`, `None`, `"0"` —
carries no information, stays out).

#### The date trap — `VonDatum` is `DDMMYY`, and it silently destroys the sort

`VonDatum` and `BisDatum` are **bare six-digit strings** (`DDMMYY`), 2131/2131,
no separators and no exceptions. `leistungVonDat` and `leistungBisDat` are
`YYYY-MM-DD HH:MM:SS`, 2131/2131.

The encoding was settled structurally, not guessed:

| Evidence | `DDMMYY` | `YYMMDD` |
|---|---|---|
| reconstructs `leistungVonDat`'s calendar day | **1917/1917** | 60/1917 |
| `VonDatum <= BisDatum` holds | **1917/1917** | 1236/1917 (681 violations) |
| chars 1–2 range | 01–31, **>12 on 1158 entries** → day | — |
| chars 3–4 range | 01–12, **never >12** → month | — |
| chars 5–6 range | 08–27 → year 2008–2027 | — |

`BisDatum` reconstructs `leistungBisDat`'s day on **2131/2131** under `DDMMYY`.

**Why this is make-or-break for C2.** `kundendaten.select` (`:253-275`) and
`zeit_marker` (`:197-206`) compare **lexically** on `str(...)[:10]`. Feeding them
`VonDatum` means comparing `"030326" >= "2026-08-02"`, which is `"0" < "2"` →
**False for every booking, forever**. Every trip lands in the *vergangene*
bucket, `auswahl="kommende"` returns **empty**, and the bot tells every agency it
has no upcoming trips. Nothing raises: `fmt_datum` (`:129-134`) catches the
`ValueError` and returns the raw string, so the user is shown `030326` as a date.

Even confined to one booking, a lexical sort on raw `VonDatum` mis-orders
**72 of 315** multi-entry bookings (23%).

`leistungVonDat[:10]` is exactly `YYYY-MM-DD`, so it satisfies `select`,
`zeit_marker` and `fmt_datum` unchanged. **`_normalise_row` reads
`leistungVonDat` / `leistungBisDat` and never `VonDatum` / `BisDatum`.**

#### `Anforderung` is the service-type code, and `P` is the trip

`Anforderung` (2131 entries): `P` 626, `T` 189 (Transfer), `F` 175 (Flug), `L`
166 (Landaufenthalt), `V` 103 (Versicherung), `RF` 88 (Rail&Fly), `FO` 86
(Regenwald-Spende), `KV` 85 (keine Versicherung), `AF` 80 (Abflughafen), `FK` 79
(Flugklasse), `FL` 69, `X` 58 (Storno/Gebühren), `NB` 53 (NatureBottle), `IF` 51
(Inlandsflug), plus a tail of ~15 more.

`P` is the booked trip: its `LeistungsBezeichnung` values are
"Rundreise Namibia Sossusvlei", "Rundreise Namibia Etosha", "Erlebnis-Reise". It
is the **first** entry on 422/486 bookings and carries the **longest date span**
on 391/486.

#### The four collapse rules — settled

**1. Which dates → the `P` entry's, falling back to min/max across the array.**
The first entry is *not* a safe proxy: it is the chronological minimum `von` on
317/334 multi-entry bookings (**17 wrong**) and the last entry is the maximum
`bis` on only 242/334 (**92 wrong**). min/max across *all* entries differs from
the `P` entry's own span on **41/436** bookings (9%) — add-ons such as insurance,
`FO` Regenwald donations and `GU` vouchers carry dates with no travel meaning and
can widen the window wrongly. So: **take `P`'s `leistungVonDat`/`leistungBisDat`;
where no `P` entry exists (50/486 bookings, 10%), fall back to
`min(leistungVonDat)` / `max(leistungBisDat)` across the array.**

**2. `VonDatum` or `leistungVonDat` → `leistungVonDat`, always.** See the date
trap above. `VonDatum` is not a second format of a second semantic; it is the
same calendar day in an unsortable encoding.

**3. Which `LeistungsBezeichnung` → the `P` entry's, with the travel index as
fallback. This reverses the "do not copy that workaround" instruction above.**
Measured on the 436 bookings that have a `P` entry, `P.LeistungsBezeichnung` is:

| | count | |
|---|---|---|
| a specific trip title | 303 | usable as-is |
| a generic product category | 86 | "Erlebnis-Reise", "Genießer-Reise" — names a product line, not a trip |
| missing entirely | 47 | |

So it is directly usable on **69%** of bookings, not all of them. `P.Leistung`
is a reisecode (`NASOS`, `NAETO`, `SAPAN`, `HANKS`) and resolves through
`travel_index.get_titel_for_code` on **310/436**; against the 133 generic-or-
missing cases specifically it **rescues 116 and misses 17**. Resulting chain —
specific `LeistungsBezeichnung` → `get_titel_for_code(P.Leistung)` → generic
`LeistungsBezeichnung` → the §7 fallback wording: **303 / 116 / 6 / 11**. Without
the index, 133 bookings (27%) would be titled "Erlebnis-Reise" or nothing at all.

`get_titel_for_code` is a non-blocking peek at an already-built map
(`travel_index.py:794-810`), so this costs no request. It is exact-match only and
returns `""` on a miss, which is why the chain keeps its own fallback.

**4. Status when services disagree → the `P` entry's status wins.**
`LeistungsStatus` values across 2131 entries: `OK` 1508, `XX` 612, `RF` 5, `""`
2, `None` 4. Per booking: 346 all-`OK`, 127 all-`XX`, and **10 mixed** (2%) —
`OK`+`XX` 6, `OK`+`RF` 3, `""`+`OK` 1.

The two candidate rules were compared on all 436 bookings with a `P` entry:
`all_xx=False p_xx=False` 317, `all_xx=True p_xx=True` 113, **`all_xx=False
p_xx=True` 6**, and `all_xx=True p_xx=False` **0**. So "all services cancelled"
and "the trip is cancelled" agree except in one direction: 6 bookings where the
trip itself is cancelled while a residual line (a `X` Storno fee, a voucher)
stays open. An all-`XX` rule reports those 6 as active trips. **Use
`P.LeistungsStatus == "XX"` → storniert; where there is no `P` entry, fall back
to all-entries-`XX`.**

Treat `""` and `None` as "not cancelled" — 6 entries total, and the alternative
is calling a live trip cancelled on a blank field.

#### 3.4 Two `ACTION` shapes the review caught unmeasured (2026-08-03)

§3.3 measured `LEISTUNGEN[]` exhaustively and `ACTION` **not at all** beyond
`AgenturNummer`. The pre-deploy review flagged both consequences. Sampled live:
**284 bookings across 12 agencies**, aggregates only.

**`ACTION.AgenturCommission` is a euro AMOUNT, not a rate.** This needed
settling because the sibling fields in the same payload are explicitly split
into `AgenturCommissionPreis` (amount) and `AgenturCommissionProzent` (rate), so
the unsuffixed name carried no answer — and rendering a rate as euros is a
confident wrong number about money, the one thing this feature cannot afford.
Measured: **280/280 bookings match the sum of `LEISTUNGEN[].AgenturCommissionPreis`
exactly** (largest delta 1 cent, rounding). Median ratio to `GesamtPreis` = 0.10,
against a median `AgenturCommissionProzent` of 10.00. JSON type is `str` (280)
or `None` (4) — both covered by `agenturdaten._euro`. **The euro rendering in
`_detail_block` is therefore correct and now documented as such.** This is the
same argument that kept hop 2's `provision` / `eigenProvBetrag` out (§10) —
applied, not assumed.

**A booking can carry several `P` entries, and they are separate legs.** 367 `P`
entries on 260 bookings-with-`P` (1.41 each). **65 bookings (25%) have more than
one, and on 45 of those (69%) the date spans DIFFER** — real legs, not
per-traveller duplicates. Taking only the first (the original `_reise_eintrag`)
rendered one leg's span as the whole trip on **~17% of all bookings**, and the
error is invisible: nothing raises, the trip just looks short. **The span is now
min/max across all `P` entries** (`agenturdaten._reise_eintraege`); ranging over
`P` entries does not reopen the 41/436 window-widening problem above, because
every `P` entry *is* trip.

`LeistungsStatus` never differs between the `P` entries of one booking (0 of 65),
so Rule 4 above stands unchanged — `all()` over the `P` entries is equivalent to
the old first-`P` rule and is the more conservative form.

### Hop 2 — per-booking detail

`GET /get/buchung?vorgangsNummer=<vorgangsNummer>` — the **same endpoint
Kunden-Modus already uses**, so its contract is already documented in
`docs/kundendaten-datenzugriff.md`. Adds over hop 1: the authoritative trip title
(`beschreibungen[].titel`), `status` (`OK` / `XX`), `flugdaten[]`, the full
Zahlstand, and `agtNr` / `mandantAgtNr` for the ownership cross-check.

Latency ~0.13s each; 6 sequential calls measured 0.8s, 3-parallel 0.3s.

### Agency master data

`GET /get/agentur?agenturId=<id>` or `?agenturNr=<agtNr>` (both work;
`showExpedienten=true` fills `expedienten[]`). Returns **103 fields** including
`bankIban`, `bankBic`, `kontoInhaber`, `steuerNr`, `ustIdent`, `kreditorKonto`,
`fibuProvisionKonto`, `mandatReferenz`, `glaeubigerIdent`, `buchungSperre`,
`selektionSperre`, `datasyncSperre`, plus `provisionsSchemas[]`,
`stornoTabellen[]`, `zahlungsProfile[]`, `oberagenturen[]`, `unteragenturen[]`,
`merkmale[]`. No bookings.

### The Expedient — identifiable, but bookings cannot be attributed to one

Two separate questions, and they have opposite answers.

> **Scope decision D10 (2026-07-31): none of this is consumed server-side.** The
> Expedient is used for the **display name in the widget only** — see §6b. The
> findings below are kept because they are what rules per-Expedient data scoping
> out (§9.4), and because they are the answer if the scope ever widens. Nothing
> here is work for M1–M4.

**Who is logged in: yes, this is knowable.**

- The agt bundle reads `window.oAgtData.exp` with `.id`, `.vorname`, `.nachname`,
  `.email` and `.position`, and branches on
  `position == 'Mobile/r Reiseberater/in'`. Login is two-step: agency first, then
  an Expedient is picked from a list (`data-set-expedient-link`,
  `data-expid="${arEach.EXPID}"`), with a `logoutWithoutExpSet` path for the
  not-yet-chosen state. **Confirmed in the real session (§12):** it carries
  `SESSION_EXPID`, `SESSION_EXPVORNAME`, `SESSION_EXPNACHNAME` and
  `SESSION_EXPKRZ`. Mind the trap documented there — `SESSION_EXPNAME` held the
  *agency* name, not the person's.
- `GET /get/expedient?expId=` returns 23 fields: `id`, `agtid`, `krz`, `name`,
  `vorname`, `rufname`, `titel`, `briefanrede`, `position`, `bereich`, `email`,
  `tel`, `handy`, `sprache`, `land`, `geschlecht`, `gebDat`, `aktiv`,
  `synonym`, `merkmale`, `marketingAktivKz`, `neuDat`, `aenDat`. Same shape as
  the `expedienten[]` entries on the agency record.

**Which bookings are theirs: no, not reliably. Measured, and this kills
per-Expedient scoping.**

| Evidence | Result |
|---|---|
| hop 1 `buchungLeistungen.ACTION.ExpedientenNummer` | present on a **minority** of rows (15/40, 138/191 occurrences) |
| do its values identify a current Expedient of that agency? | **0 of 13, 0 of 9, 0 of 75** matched either `krz` or `id` — and 75 distinct values for an agency with 2 Expedienten. Whatever this field is, it is **not** a key into the agency's Expedient list. Not characterised further. |
| hop 2 `expId` | **empty on 5 of 12** bookings; 7 distinct values for an agency with 2 current Expedienten (former staff, presumably) |
| undocumented hop-1 Expedient filter | none — `expId`, `expedientId`, `expedient`, `EXPID`, `expNr` all ignored (row count unchanged) |

So a booking list filtered to the logged-in Expedient would **silently omit most
of the agency's bookings** — the exact "bot says *keine Buchungen*, Reiseprofi
concludes it does not exist" failure §7 exists to prevent. See §9.4.

### Scale

21,166 agencies (`/get/agenturenliste`, `gesamt`); 223,588 bookings tenant-wide.
Of 60 sampled agencies: 27 had bookings (min 1, **median 4**, max 191), 29
answered 200 with none, 4 hard-failed (§7).

### Error paths — measured, and they matter

| Call | Result |
|---|---|
| `buchungLeistungenListe`, **`agenturNummer` omitted entirely** | **HTTP 200 with rows — the tenant's bookings, unfiltered.** See §8, guard G2. |
| `buchungLeistungenListe?agenturNummer=` (empty string) | HTTP **500** |
| `buchungLeistungenListe?agenturNummer=abc` | 200, 0 rows (fails safe) |
| `buchungLeistungenListe?agenturNummer=999999999` | 200, 0 rows |
| `buchungLeistungenListe` for a real agency | **HTTP 500 for 4/60 sampled (7%), persistent** — one agency returned 500 on 5/5 retries at 0.15s while a control agency answered 200. 3 of the 4 had `aktiv=0`; one had `aktiv=1`. Inactive agencies cannot log in, so the rate among *logged-in* agencies is likely nearer 1–2%, but it is not zero. |
| one sampled call | **timed out at 60s** — median is 0.12s, the tail is not |
| `agentur?agenturNr=999999999` | 200, `[]` — the discriminate-by-type contract, same as `/get/adresse` |
| `agentur?agenturNr=` non-numeric or empty | HTTP **500** — a 500 here is *not* "not found" |

### Write endpoints exist on this token

`/post/merkmal/aktivieren` and `/post/merkmal/deaktivieren` are POST routes on
the same bearer token. The GET-only discipline in `kundendaten.py` is a real
control, not decoration. Keep it.

## 4. Architecture

```
Browser on agt.chamaeleon-reisen.de (logged in)   Our backend            agt.chamaeleon-reisen.de
  │  read PHPSESSID via document.cookie             │                          │
  │  POST /agentur/auth {session_id, phpsessid} ────►│ begin_auth(session_id)   │
  │  (cross-origin, CORS already allows agt)         │ GET ss.php Cookie: PHPSESSID ►│
  │                                                  │ ◄─ SESSION_<AGT-KEY> ────│
  │  ◄── {authenticated:true} ───────────────────────│ commit_auth → sid→agtNr  │
  │                                                  │ discard phpsessid        │
  │  POST /chat/stream {session_id, messages} ──────►│ agentur_id = resolve(sid)│
  │  ◄── SSE reply ─────────────────────────────────│ buchungen_agentur_tool ──► api.tourone.de
```

`session_id` is the bearer token after the one verification, exactly as in
Kunden-Modus. `/chat/stream` ignores any agency identifier in the request body.

**`ss.php` is available on the agt hosts** — verified 2026-07-30: 200 on both
`agt.chamaeleon-reisen.de` and `agt.chamdev.tourone.de`, anonymous → `Array ( )`.

**The agt session is a different session from www's.** The `PHPSESSID` cookie is
set host-only (`path=/`, no `Domain`), so a www token is meaningless to agt's
`ss.php` and vice versa. This needs its own origin→URL table; `kunden_auth.SS_URLS`
deliberately omits the agt hosts and its comment explaining why must be updated
to point here.

## 5. Files to change

| File | Change |
|---|---|
| `agentur_auth.py` | **new.** `SS_URLS_AGENTUR` (agt hosts only), its own `_bindings` / `_inflight` / `_lock`, `begin_auth` / `commit_auth` / `unbind` / `resolve` / `authenticate`, `_AGTNR_RE` matching the literal `[SESSION_AGTNR]` (**§12** — full-bracket match, never a suffix: four other session keys end in `AGTNR`) and a strict `_AGTNR_RE_VALUE` (digits only — the same reason `kunden_auth._KUNDENNR_RE_VALUE` exists: a print_r sub-array renders as the literal `Array`, and a non-agency session can carry `0`), plus the `/get/agentur?agenturNr=` existence check before binding. Import the low-level primitives (`_PHPSESSID_RE`, `read_capped_body`, `coerce_json_body`, the `requests.get(..., allow_redirects=False)` replay) from `kunden_auth` rather than copying them. |
| `agenturdaten.py` | **new.** Mirrors `kundendaten.py`: `parse_agentur_id`, `_select`, formatters, `fetch_buchungen_text`, `make_buchungen_agentur_tool(agentur_id)` closure. Reuses `travel_index._tourone_get` (the one-implementation rule, decision 2A). |
| `app.py` | `POST /agentur/auth` route; `agentur_id = agentur_auth.resolve(session_id) if is_agentur else ""`; pass it into `call_stream`. Type-check `session_id` **before** `resolve()` — an unhashable body value would otherwise 500 (same bug already fixed on the Kunden path, `app.py:107`). |
| `agent.py` | Build `buchungen_agentur_tool` when `agentur_id` is non-empty. |
| `agent_base.py` | Extend the existing `agentur_block` with the tool-usage rules; `format_system_prompt` gains a flag (`has_agentur_daten`). The Agenturnummer itself must **not** enter the prompt — it stays in the closure, same rule as `kunden_id`. **This is also where the Provision split lives (§6a):** state a per-booking commission when the tool returns it, but refer any question about how a rate is determined, raised or settled to the Vertriebsteam. Both halves must be in the prompt explicitly — the model cannot infer the line from the data alone. |
| `rate_limit.py` | Register the new auth endpoint; the 429 handler must clear the **agentur** binding too. A 429 is raised in `before_request`, so the view body never runs — this is the already-learned `flask-limiter` trap. |
| `kunden_auth.py` | Update the `SS_URLS` comment that says agentur hosts are deliberately absent. |
| `docs/kundendaten-datenzugriff.md` | Add the agency data surface (§6 here folds in). |
| `faqs/agentur.md` | **No change.** Its Vertriebsteam list is topic-scoped and was never a rule about booking data — see §6a. |
| `tests/test_agentur_auth.py`, `tests/test_agenturdaten.py` | new — §10. |

Mode exclusivity stays: `app.py` forces `kunden_id = ""` on agentur requests, and
Agentur-Modus must not read a Kunden binding. The two stores never cross.

## 6. The whitelist — what may reach Gemini

The boundary that matters is the **model request**, not the API call. Fetching the
whole record is accepted (it stays server-side, in process memory, never
persisted) — that is the settled rule in `docs/kundendaten-datenzugriff.md` and it
carries over unchanged.

**Coarse list (hop 1 only)** — proposed IN:
`vorgangsNummer`, `LEISTUNGEN[].VonDatum` / `BisDatum`,
`LEISTUNGEN[].LeistungsBezeichnung`, `LeistungsStatus`, `ACTION.Reiseart`,
`KUNDE.VornameTitel` (the Reiseprofi has to be able to tell whose booking it is).

**Travellers — IN** (owner decision D9, 2026-07-30):
`TEILNEHMERS[].TeilnehmerNummer` / `Anrede` / `Name` / `PersonenPreis`. The agency
booked these people and already knows them, so "wer reist bei Buchung X" is
answerable. Every traveller shown belongs to a booking of the verified agency —
`ACTION.AgenturNummer` matched on all 64 rows measured (§3), so there is no path
by which a foreign agency's traveller appears.

**There is no provenance field, and that limit is worth knowing.** `TEILNEHMERS[]`
has exactly four keys, and hop 2's participant record adds only contact/identity
fields — nothing records *who entered* a traveller. So a co-traveller the customer
added later in MeinChamäleon is indistinguishable from one the agency keyed in
itself, and both appear. If "only the people the agency itself entered" were ever
required, the API cannot express it; the scope is per **booking**, not per data
entry. **Note the deliberate divergence from Kunden-Modus**, which excludes
`teilnehmerliste` outright (`kundendaten.py` docstring, "Bewusst DRAUSSEN:
Mitreisende-PII"). That asymmetry is intentional — do not "fix" it in either
direction: a customer asking about co-travellers and an agency asking about its
own booked guests are different situations. Record it in
`docs/kundendaten-datenzugriff.md` when this ships.

**Detail (hop 2)** — proposed IN, additionally:
`beschreibungen[].titel`, `status`, `personen` / `persAdult` / `persChild` /
`persBaby`, the six existing `FLUG_FELDER`, and the Zahlstand fields already
whitelisted for customers (`preis`, `anzahlungBetrag`/`-Dat`, `restBetrag`,
`schlussZahlungDat`, `eingangBetrag`).

**Commission — IN** (owner decision D8, 2026-07-30):
`ACTION.AgenturCommission`, `LEISTUNGEN[].AgenturCommissionPreis`,
`LEISTUNGEN[].AgenturCommissionProzent`, and hop 2's `provision` /
`eigenProvBetrag`. Rationale in §6a.

**Proposed OUT — deliberately:**
`KUNDE.StrasseHausNummer` / `Postleitzahl` / `Ort` / `TelefonNummer`,
`adrStrasse` / `adrPlz` / `adrOrt` / `adrTel` / `adrHandy` / `adrEmail`,
`adrNotfallKontakt`, `adrKundenNr`;
`chroniken[]` and `bookNotiz` (internal notes); `pnrFileKey` and all internal
ids; every `*Cy` and `steuer*` field; `MeldeAgenturNummer`,
`LeistungKostentraeger`.

**Traveller fields beyond identity stay OUT — and this was not part of D9.**
Hop 2's participant records carry more than hop 1's four fields: per
`docs/kundendaten-datenzugriff.md` they include `gebDat`, `email`, `tel`, `mobil`,
`strasse`, `plz`, `ort`, `land`. D9 was "names etc." on the back of a question
about *who is travelling*, so it covers the identity fields listed above and not
birth dates, passport-adjacent data or per-traveller contact details. Those are a
separate one-line decision if a real use case turns up (a Passdaten-completeness
check is the plausible one) — until then, the narrower reading holds.

**Agency master data** — only reach for `/get/agentur` if a concrete question
needs it, and then only: `agenturnummer`, `name`, `krz`, `type`, `aktiv`,
`strasse`, `plz`, `ort`, `land`, `tel`, `fax`, `email`, `website`, and
`expedienten[].{name, vorname, email, telefon}`. **`provisionsSchemas[]`,
`stornoTabellen[]` and `zahlungsProfile[]` stay OUT** — those are the contract
terms themselves, not facts about a booking (§6a). Everything fiscal, banking or
Sperren-related stays out too — an allowlist, never a denylist, so the next field
TourOne adds is excluded by default.

### 6a. Provision — the amount is a fact, the system is a referral

Two different things were being conflated, and they are enforced in two
different places.

**The whitelist decides what data exists. The prompt decides which questions get
answered.** Excluding commission from the whitelist to honour a *topic* policy was
the wrong mechanism: it would have made the bot unable to state a number the
Reiseprofi can already read on their own screen, while doing nothing about the
questions the policy actually covers.

| | Mechanism | Example |
|---|---|---|
| **Fact about an existing booking** → answer it | field whitelist (§6) | "Wie viel Provision bekomme ich für Buchung X?", "Wie hoch war die Provision bei der Reise im Mai?" |
| **The commission system** → refer to the Vertriebsteam | prompt rule (§5, `agent_base.py`) | how a rate is determined, individuelle Provisionshöhe, raising it, Verkettung, Rückvergütung, Cashback, Provisionsabrechnung disputes, anything contractual |

Why the fact side is safe:

- **No new disclosure.** A logged-in agency already sees its own commission and
  Umsatz at `agt.chamaeleon-reisen.de/Agentur/Buchungen` — `faqs/agentur.md`
  points them there by name. Same data, same audience, same authentication.
- **The KB's referral list is topic-scoped by its own wording:** *"Bei folgenden
  Themen grundsätzlich an das Vertriebsteam verweisen, besonders wenn es um
  individuelle Fälle, Konditionen, Vertragsdetails oder Änderungen geht."*
  Conditions, contract details, changes — not fact retrieval.
- **The KB already answers factual Provision questions itself** ("Wann erhalte
  ich meine Provision? — automatisch nach Buchungseingang, sobald der Gast die
  Anzahlung geleistet hat"). Refusing to state a per-booking amount would be
  *more* restrictive than the KB the bot is already given.

So **`faqs/agentur.md` needs no change.** Its list stands as written; it was never
a rule about booking data.

The one field closest to the line is `AgenturCommissionProzent` — the rate applied
to *this* booking. It is in, as a fact about work already done. What stays out is
the rate *schedule* (`provisionsSchemas[]`): that is the Konditionen, and a bot
reading contract terms aloud is exactly what the referral exists for.

**The principle generalises — apply it when reading the rest of the KB list.**
Several other referral topics split the same way once the agency is verified, and
the prompt should not blanket-refer them:

- *"Agenturnummer vergessen"* is on the referral list, but a verified session
  **is** the answer — the bot can state that agency's own Agenturnummer. Referring
  a caller to a human for a number we just authenticated them against would be
  absurd.
- *Umsatz* — an aggregate the agency can compute from its own bookings is a fact;
  a dispute about an Abrechnung is not.
- *Bankdaten* stay out regardless, but for a different reason than the referral:
  `bankIban` / `bankBic` / `kontoInhaber` are credential-grade and answer no
  question anyone asks a chatbot. Changing them is the referral; reading them has
  no legitimate use here at all.

When writing the §5 prompt block, walk the `faqs/agentur.md` list once and sort
each entry into *fact about this agency's own records* (answer) or *conditions,
contract, change, dispute* (refer). Do not carry the list across wholesale.

### 6b. Expedient — widget-side display name, nothing server-side (D10)

The Reiseprofi's first name is shown in the chat UI. That is all it is used for
right now, and it has three consequences worth stating so nobody re-derives them:

- **No server-side lookup and no verification.** The backend never *fetches* the
  Expedient: it ignores `SESSION_EXPID` (which is present — §12), never calls
  `/get/expedient`, and binds nothing.
- **But the name does reach the model — via the message history, and that is
  intended.** The widget builds the request's `messages` by scraping the DOM
  (`chatbotMessages.querySelectorAll('.message')`, `.bot-message` → role
  `assistant`, `querySelector('p').textContent`). The personalised greeting is
  one of those nodes, so "Hallo Anna! …" is posted to `/chat/stream` as an
  assistant turn. **The bot therefore does know the name and can use it.**
  What is avoided is a *second*, server-derived copy — not the name itself.
- **It is the one piece of this feature that may safely be client-asserted.**
  `oAgtData.exp.vorname` is rendered by the site into a page the user is already
  logged into, and tampering with it only changes the tamperer's own screen. No
  session replay, no binding, no trust boundary. Contrast the Agenturnummer,
  where the identical shortcut is exactly the IDOR the rest of this plan exists
  to prevent — the difference is that one only labels a UI, the other selects
  data.
- **This is the established pattern, not a limitation of D10.** The identical
  decision was already made for customers
  (`~/.gstack/projects/…/rharvey-kunden-begruessung-plan-20260714.md`, 2026-07-14
  — *"Kunden-Modus, frontend-only … Backend: no change needed"*), sourced from
  `oKundenData.vorname`. "Frontend-only" means **no backend change**, not "the
  model never sees it": there is no name parameter (`call_stream()` has none, and
  `format_system_prompt`'s only name slot is `kundenberater_name` — the Chamäleon
  **Erlebnisberater**, not the user), yet the greeting still arrives inside
  `messages`. Customers have worked this way since 2026-07-14. Agentur-Modus
  copies it exactly.

**Consequence for the model boundary.** The user's own first name does cross into
the Gemini request, as conversation content rather than tool output. Deliberate,
and low-sensitivity — it is the person's own name, rendered to their own screen,
from a page they are logged into. Two notes so it is not mistaken for a hole:

- It is **not** logged to Supabase by this path: `app.py` logs `messages[-1:]`
  (the current user turn) plus the assistant reply, not the historical welcome
  node. A name only lands in the log if the bot repeats it in an answer.
- The 40-char / empty guard from the Kunden plan does more than protect the
  layout: because the greeting arrives as an **assistant**-role turn, whatever
  sits in `oAgtData.exp.vorname` is content in a slightly more trusted position
  than anything the user could type themselves. No new capability — the user can
  already put arbitrary text in a user turn, and only their own session is
  affected — but keep the guard, and keep taking `textContent`, not HTML.

### Widget implementation — reuse the Kunden-Begrüßung pattern

That plan is the template; the agentur half is smaller because the hook already
exists.

- **`getAgenturWelcomeMessage()` is already in `chatbot.html`** (the Kunden plan
  calls it "the existing agentur rewrite" and notes "Agentur wins; kunden data
  never exists on agt hosts anyway"). D10 is: read the Vorname and extend that
  function, mirroring `getKundenWelcomeMessage()`.
- **Read at init, not at send time.** `getKundenId()` reads at send time, but the
  welcome rewrite runs during script initialisation — the global must exist
  before the widget script. Same applies here.
- **`escapeHtml()`** before interpolation: the welcome messages are assembled as
  `innerHTML` strings, so `O'Brien <3` has to render literally. The helper is
  already specified in the Kunden plan.
- **Length/garbage guard:** empty, absent, or longer than 40 chars → skip the
  personalisation rather than truncate a name or break the layout.
- **Guard defensively**, the way the site's own bundle does
  (`window.oAgtData === undefined || window.oAgtData?.exp !== undefined …`).
  Login is two-step and there is a legitimate state where the agency is logged in
  but no Expedient has been picked yet (`logoutWithoutExpSet`). No `exp` → render
  no name; never "Hallo undefined".
- **Encoding:** `chatbot.html` is ISO-8859-1. Patch stays pure ASCII (umlauts as
  `\uXXXX` JS escapes) and is applied via the python iso-8859-1 method, never
  Edit/Write directly. Verify with `git diff --stat` (insertions only).

## 7. Error handling — the failure mode to avoid

**A hop-1 failure must never render as "keine Buchungen".** If it does, a
Reisebüro concludes its booking does not exist. This is not hypothetical: 7% of
sampled agencies return a *persistent* 500, and a genuinely empty agency returns
200 with 0 rows. The two are indistinguishable in the output unless kept apart.

| Condition | Text |
|---|---|
| HTTP 500 / timeout / exception | `FEHLER_TEXT` equivalent — "gerade nicht abrufbar … später erneut" |
| 200, 0 rows | "zu dieser Agenturnummer finde ich keine Buchungen" |
| 200, rows present, some hop 2 failed | render what resolved **plus** an explicit partial-result note — copy `kundendaten.fetch_buchungen_text`'s `fehler_gesehen` handling |
| session verified but no agency key | fail closed, anonymous, Agentur-Modus stays content-only |

`TIMEOUT = 8` per call, as on the Kunden path — the 20s `_tourone_get` default is
for index builds. One sampled call hung to 60s; the median is 0.12s. Cap
parallelism if hop 2 is batched, and treat the measured latency as bursty: the
same endpoints returned 0.13s and 6s within one session.

## 8. Structural guarantees

These are what keep a bug in the session check from being fatal. Same posture as
Kunden-Modus, plus one new guard that is specific to this endpoint.

- **G1 — closure, no agency parameter.** The tool takes selector arguments
  (`auswahl` / `anzahl` / `details`) and **never** an agency identifier. The model
  chooses *which* of the agency's own bookings, never *whose*. Prompt injection
  cannot cross agencies.
- **G2 — never call hop 1 without a non-empty verified Agenturnummer.**
  Omitting `agenturNummer` returns **the tenant's bookings, unfiltered, HTTP
  200**. A falsy `agentur_id` reaching a params dict built with `if` would
  silently serve 21,166 other agencies' data. Assert non-empty at the top of the
  fetch and return the error text otherwise — this is the sharpest footgun in the
  whole feature.
- **G3 — cross-check ownership on both hops.** Hop 1: `ACTION.AgenturNummer` must
  equal the bound Agenturnummer, else drop the row. Hop 2: `buchung.agtNr` must
  equal it too. Hop 1's filter is sound as measured (0 exceptions in 64 bookings),
  but the check is free and makes the guarantee local instead of trusting a
  vendor's WHERE clause. **Compare against `ACTION.AgenturNummer` only** —
  `mandantAgtNr` is Chamäleon and `MeldeAgenturNummer` is a third party, so a
  check written against either would reject every legitimate row (§3).
- **G4 — GET only.** Write endpoints exist on this token (§3).
- **G5 — field whitelist** (§6), allowlist not denylist.
- **G6 — ID allowlist.** Digits-only for the Agenturnummer before it reaches a
  URL, validated separately from the transport-safety allowlist.
- **G7 — the Agenturnummer never enters the prompt**, never a tool argument,
  never a log line, never Supabase.
- **G8 — order of operations in `authenticate()`:** clear the binding → verify →
  commit only if nothing superseded. On a shared Reisebüro workstation this is
  what stops person B from inheriting person A's agency. It lives in the module,
  not the view, because that is where it is testable — `import app` triggers live
  Supabase reads.

## 9. Open questions

1. ~~**The `$_SESSION` key name.**~~ **RESOLVED 2026-07-31 — `SESSION_AGTNR`.**
   Confirmed against a real logged-in session; bare digits. See §12 for the full
   key inventory, the suffix-collision trap, and the dropped "discovering
   extractor" design. Historical note kept below because the derivation was
   right and may help next time:

   **Narrowed from evidence, 2026-07-30** — from the site's own bundle,
   `agt.chamaeleon-reisen.de/start/script.jqueryload.php` (the single
   site-served script; everything else is CDN):

   - The agency login field is `#AGTLOGIN_AGTNR`, and the site posts its value
     under the name **`AGTNR`** (`Expedient.add`: `data.push({name:'AGTNR',
     value: agtnr})`).
   - On the customer side the posted key is `ADRKUNDENNR` and the session key is
     `SESSION_ADRKUNDENNR` — `SESSION_` + the posted key. If that convention
     holds, **`SESSION_AGTNR`** is the most likely name, with
     `SESSION_AGENTURNR` second (`AGENTURNR` also appears in the bundle).
   - **`window.oAgtData.nr`** exists as the exact analogue of `oKundenData.nr`.
     Client-side and therefore useless for auth — the same reason the widget's
     asserted `kunden_id` was dropped — but it confirms the agency identity is
     server-rendered into logged-in agt pages.
   - `BENAGTNR` is **not** it: `ben*` fields are the Chamäleon staff user
     (`benId`/`benutzer`/`benTel` on bookings), not the partner agency.

   The derivation turned out right — §12 confirms `SESSION_AGTNR` — but note
   what it did **not** predict: the four decoy keys ending in `AGTNR`, and the
   ~130 `SESSION_*AGT*` keys that broke the first parsing design. Reasoning from
   a naming convention gave the right answer and the wrong parser.
2. ~~**Provision — policy conflict.**~~ **RESOLVED** (owner, 2026-07-30, D8):
   there is a difference between a question about the provision *system* (how the
   rate is determined, how to change it) and the provision *itself* on a booking
   that already exists. Per-booking amounts are whitelisted facts; the system is a
   prompt-level referral. Full reasoning in §6a. Residual work, not a question:
   the prompt in §5 must carry **both** halves explicitly, and the gated eval
   (`RUN_AGENTUR_EVAL`) should cover one of each — "wie viel Provision bei
   Buchung X" answered, "kann ich eine höhere Provision bekommen" referred.
3. **Oberagentur / Unteragentur — measured, and it is a product question now.**
   `mandantAgtNr` turned out to be Chamäleon, not a parent agency (§3), so there
   is no leak here. But the tested Oberagentur saw **0** of its Unteragentur's
   bookings. If a Reisebüro chain, a Kooperation or a head office expects to see
   its branches' bookings in the chat, **it will not**, and that will read as the
   bot being broken rather than as a scope decision. Two sub-questions for the
   Vertriebsteam: (a) is that the desired behaviour, and (b) which login does a
   chain's head office actually use — its own Agenturnummer, or each branch's?
   Sample was one Oberagentur with one Unteragentur, and the sub-agency had no
   bookings, so the reverse direction is untested.

   **§12 supplies the missing lever:** the session carries
   `SESSION_AGTKETTENLOGINKZ` (chain-login flag) and `SESSION_AGTOBER` (array of
   parent agency numbers). So if the answer is "yes, a head office should see its
   branches", it can be implemented on real session state rather than guessed —
   bind the parents too, and widen the G3 ownership check to that set. Do not
   build it until the Vertriebsteam has answered; the point is only that the data
   is there.
4. **Expedient scope — D5 (agency-level) is now the only workable option, not
   just the simpler one.** The identity is available, the attribution is not: see
   §3. Per-Expedient filtering of the booking list is **off the table** — it
   would drop the rows with no attribution (a majority on hop 1, 42% on hop 2)
   and every booking made by a colleague who has since left. Do not implement it,
   and do not treat it as deferred work either; it needs different data from
   TourOne first.

   **The privacy question therefore stands unresolved and cannot be closed in
   code.** If several independent mobile Reiseberater*innen share one
   Agenturnummer, each sees the others' bookings and their customers' names.
   Filtering cannot fix that, so it is an organisational decision: ask the
   Vertriebsteam whether independent advisors ever share an Agenturnummer. If
   they do, the options are to exclude those agency types from Agentur-Modus, or
   to accept it explicitly and write it down. **This is the one open item that
   should be settled before go-live rather than after.**

   **What the Expedient is used for instead (D10):** the display name in the
   widget, client-side only — §6b. Not bound server-side, so no employee PII
   (`gebDat`, `geschlecht`, `tel`, `handy`, `email`, `merkmale`) is in scope at
   all. Still unused and available if wanted later: branching on
   `position == 'Mobile/r Reiseberater/in'`, where tone and the relevant parts of
   `faqs/agentur.md` differ from a bricks-and-mortar Reisebüro. That one would
   need the Expedient server-side.
5. **`page_content` + agency data in one prompt.** On agentur requests the widget
   already scrapes the page (`agent_base.py:810`). `/Agentur/Buchungen` contains
   customer names and Umsatz, so once agency data is also injected the prompt
   carries two independent PII sources. Mode exclusivity was a privacy control
   for Kunden-Modus; decide explicitly whether the scrape stays on when
   `has_agentur_daten` is true.
6. **Logging.** `is_agentur` is still not logged with chat messages (`TODOS.md`),
   so there is no way to tell agentur conversations apart in the dashboard.
   Log the flag, never the Agenturnummer — linking transcripts to an identified
   business is a deliberate decision, not a default.
7. **The 7% hard-fail rate.** 3 of the 4 failing agencies had `aktiv=0` and
   cannot log in, so the live rate is probably 1–2%. Worth one question to
   TourOne: is the 500 a data defect on those records, and can it be fixed?
8. ~~**Traveller names and per-head pricing.**~~ **RESOLVED** (owner,
   2026-07-30, D9): included. Residual, narrower question left open on purpose:
   traveller `gebDat` / passport-adjacent / per-traveller contact fields are still
   out (§6) — revisit only against a concrete use case such as a
   Passdaten-completeness check.

## 10. Milestones

| # | Work | Owner |
|---|---|---|
| ~~**M0**~~ | ~~Read the agency session key.~~ **DONE 2026-07-31 — `SESSION_AGTNR`, bare digits** (§12). | owner |
| **M1** | `agentur_auth.py` + `POST /agentur/auth` + `rate_limit` wiring, fail-closed, unit-tested against fabricated dumps. Deployable **dark** — no widget calls it yet, exactly how Kunden-Modus shipped. | — |
| **M2** | `agenturdaten.py` hop 1 → coarse list (`auswahl` / `anzahl`), G2 guard, error-text split per §7. | — |
| ~~**M3**~~ | ~~Hop 2 detail view + G3 ownership cross-check + partial-failure note.~~ **DONE** — but built differently than planned, see below. | — |
| **M4** | Prompt block in `agent_base.py`: when to call the tool, read-only framing, no self-built agt URLs. | — |
| ~~**T1**~~ | ~~Verify C1(agt): is the agt `PHPSESSID` readable from `document.cookie`, or `HttpOnly`?~~ **DONE 2026-08-03 — readable.** The transport holds; the §7 signed-token fallback is not needed. This was the make-or-break check the server half was built ahead of. | owner |
| ~~**T2**~~ | ~~Verify C2(agt): does an agt session replay from Railway's datacentre egress?~~ **DONE 2026-08-04 — it does.** With T1 this makes the transport **proven end to end for agt**, the same standard `www` reached on 2026-07-29. It mattered because a failure here is the silent kind: `verify_agentur_session` would return `None` for every real login — fail-closed and correct, but dead in production with all 220 tests green, since every one of them fakes the transport. | owner |
| **M5** | Widget: read `PHPSESSID` on agt pages, `POST /agentur/auth` **unconditionally on every chat open** — including when no cookie is found, or a stale binding survives on a shared browser. Plus the D10 display name: read `oAgtData.exp.vorname` defensively and render it in the UI (§6b) — client-side only, never sent to the backend. `cham-chatbot`, feature branch → PR, never straight to `main`. | owner |
| **M6** | Go-live: M1–M4 pushed (webbot `main` deploys on push) before M5 reaches the widget's `main`. Server first, always. | owner |

Sequencing note: Kunden-Modus is the precedent and it is **still not live for
real customers** — the widget half sits on `cham-chatbot` `develop` (PR #22), not
`main`. Do not let this feature's server half imply the customer one shipped.

### How M3 actually came out (2026-08-03)

§3's measurement predates §3.3's. Hop 1 turned out to carry `KUNDE`,
`TEILNEHMERS[]` and `LEISTUNGEN[]` in full, so the split between the hops is
**not** the one this plan assumed:

- **Hop 2 is now only what hop 1 lacks:** the Zahlstand, `flugdaten[]`,
  `personen`/`persAdult`/`persChild`/`persBaby`, and the authoritative
  `beschreibungen[].titel`. The Zahlstand is the reason it exists at all — "is
  this booking paid" is a counter question and appears nowhere in
  `buchungLeistungenListe`.
- **A hop-2 failure no longer drops the booking.** `kundendaten` skips a booking
  whose hop 2 failed, because there its hop 1 carries almost nothing. Here hop 1
  carries title, dates, Besteller, travellers, price, commission and
  Leistungen — discarding all of it because a Zahlstand fetch failed would be
  worse than naming the gap. So the block renders without the hop-2 lines and
  the answer ends with an explicit note. §7's rule, one level down: a missing
  value must never read as a zero one.
- **G3 runs twice**, and the second one is not ceremonial: `/get/buchung` takes a
  bare `vorgangsNummer` and has **no agency filter at all**, so the only thing
  binding a detail record to the agency is that the number came from a
  G3-checked hop-1 row. The returned `agtNr` is therefore re-checked
  independently, and a missing `agtNr` fails closed.
- **Not done, deliberately: hop 2's `provision` / `eigenProvBetrag`.** §6
  whitelists them, but §3.3 never measured their format, and hop 1's
  `ACTION.AgenturCommission` already renders the commission. If `provision` is a
  *percentage*, formatting it as euros yields a confident wrong number about
  money — the one place this feature cannot afford one. Measure the two fields
  before adding them; until then the single measured field stands alone.

`DETAIL_ROW_CAP = 25` stays, and stays deliberately unlike the customer path,
which the owner uncapped on 2026-07-30. A customer has a handful of their own
bookings; an agency has up to 191 of other people's, and the cap is a prompt-size
limit before it is a request-count one.

## 11. Test plan

Unit, no network — mirror `tests/test_kunden_auth.py` (39 tests) and
`tests/test_kundendaten.py`:

- **Auth:** empty/short/CRLF-bearing `PHPSESSID` rejected before it reaches a
  Cookie header; non-200 / redirect / network error → `None`; `Array` and `0`
  rejected by the value regex; exactly-one-match rule on the print_r parse;
  nested-key indentation rejected (4-space anchor, `\A...\Z` not `^...$`);
  begin/commit generation ordering under interleaved auths; unknown origin →
  production URL; TTL expiry; `unbind` clears both dicts.
- **Data:** **G2 — a falsy `agentur_id` must never produce an API call** (assert
  the fetch short-circuits; this is the test that matters most); 500 → error text,
  not the empty-state text; 200/0-rows → empty-state text; selector filtering and
  ordering against fabricated hop-1 fixtures; G3 — a hop-2 record whose `agtNr`
  differs is dropped.
- **Session-key parsing, against the real key inventory (§12).** The four
  decoys — `SESSION_AGTALTAGTNR`, `SESSION_AGTNEUAGTNR`,
  `SESSION_AGTCRSTOMAAGTNR`, `SESSION_AGTCRSMERLINAGTNR` — must **not** match
  `_AGTNR_RE`; a fixture containing all of them plus the real key must yield the
  real one. Also: a fixture with **only** a decoy must yield nothing, not a
  binding. This test is the reason the plan's first parsing design was caught.
- **Whitelist, both directions.** Assert the excluded fields do **not** reach the
  rendered output: `KUNDE.StrasseHausNummer` / `Postleitzahl` / `Ort` /
  `TelefonNummer`, `adrNotfallKontakt`, `chroniken`, `bookNotiz`, `pnrFileKey`,
  traveller `gebDat` / contact fields, `provisionsSchemas`. And assert the
  *included* ones do — per-booking commission (D8) and traveller names (D9) —
  because both were excluded in the first draft of this plan, and a whitelist test
  that only checks exclusions will happily pass on a whitelist that dropped them
  again.
- **No prompt-content assertions.** Repo rule: `format_system_prompt` output is
  never substring-asserted; behaviour is checked with the gated evals
  (`RUN_AGENTUR_EVAL`).
- **Fixtures are fabricated only.** Never commit real `ss.php` output or real
  agency/customer records — a Kunden v1 commit leaked a password hash and salt.

### 11.1 Pre-deploy review, 2026-08-03 — what got pinned, what stayed open

The review mutated each guard and re-ran the suite. Everything below **survived
its mutation with 213/213 green**, i.e. the guard was correct but nothing would
have caught it regressing. Seven are now pinned (suite 220):

| Guard | Mutation that used to pass | Test |
|---|---|---|
| `agentur_id` comes from the binding, not the body | read it from `data.get("agentur_id")` | `test_agentur_id_kommt_aus_der_bindung_nicht_aus_dem_body` |
| Kunden/Agentur modes are exclusive | drop the `"" if is_agentur` guard | `test_kunden_und_agentur_modus_schliessen_sich_aus` |
| G3 checks **per row**, not per response | keep every row unless *no* row matches | `test_g3_verwirft_die_fremde_zeile_zwischen_eigenen` |
| G3 fails closed on a missing `AgenturNummer` | admit rows where the field is absent | `test_g3_verwirft_zeile_ohne_agenturnummer` |
| `begin_auth` runs **before** ss.php | move it into the `finally` block | `test_die_bindung_ist_waehrend_ss_php_schon_geloest` |
| hop-2 detail pairs with **its own** booking | `reversed()` the zip | `test_hop2_detail_landet_bei_seiner_eigenen_buchung` |
| span covers **all** `P` entries (§3.4) | take `reisen[:1]` | `test_zeitraum_spannt_ueber_alle_p_eintraege` |

Every test page before this was homogeneous (all own rows, or a single foreign
one), which is why the per-row property was unpinned: a per-batch check passes
both shapes.

**Still open, deliberately (owner, 2026-08-03):**

- **The 429 path on `/agentur/auth` is not exercised end-to-end.** The existing
  test calls `rate_limit._unbind_rate_limited_session("agentur_auth")` by hand;
  `_on_rate_limit` itself is only covered for `/kunde/auth`
  (`tests/test_kunden_auth.py:571`). Reverting the dispatch to `== AUTH_ENDPOINT`
  makes a 429 on `/agentur/auth` skip the unbind **and** return an SSE 200 —
  fail-OPEN — with the whole suite green. Both route decorators now bind their
  endpoint from `rate_limit.AUTH_ENDPOINT` / `AGENTUR_AUTH_ENDPOINT`, so a rename
  can no longer split them, but the behaviour itself stays unpinned.
- **Hop 2 has no G2-style transport guard.** `_hop2_alle` calls `_tourone_get`
  directly, and `_normalise_row` can yield `vorgang == ""`, which `requests` sends
  as `?vorgangsNummer=` (it drops only `None`). G3-on-hop-2 still gates what is
  rendered, so this is a missing depth layer, not a live leak.
- **`agt.chamdev.tourone.de` is a valid auth path to production booking data.**
  The same trade-off `kunden_auth.py` documents and the owner accepted for the
  customer path — but the agency payload is a whole book of business, not one
  customer's trips, and `agentur_auth.py` does not restate it.
- **Untested:** the `if agentur_id:` tool gate in `agent.call_stream`; the origin
  pass-through in `authenticate` (dropping it kills the mode silently — there is
  no production fallback here); the `DETAIL_ROW_CAP` boundary at exactly the cap.
- **Outside this feature:** `app.proxy` is wrapped in an unbounded `functools.cache`
  keyed only on `path` (query string ignored, no rate limit) in the same single
  worker that holds every binding; `kundendaten` logs full exception objects,
  whose `HTTPError` message embeds the query string and so the Kundennummer —
  `agenturdaten` deliberately logs only `type(e).__name__`.

## 12. The agency session — confirmed 2026-07-31

Read from a real logged-in agentur session on `agt.chamaeleon-reisen.de/ss.php`.
**No real values are recorded here or anywhere in this repo** — key names and
shapes only (see the warning at the end of this section).

### The key

```
SESSION_AGTNR   →  bare digits, the Agenturnummer
```

So `_AGTNR_RE` matches the literal `[SESSION_AGTNR]`, with the same anchoring as
`kunden_auth._KUNDENNR_RE`: `^ {4}\[SESSION_AGTNR\][ \t]*=>[ \t]*(.+?)[ \t]*$`,
`re.MULTILINE`, four spaces exactly, and `\A…\Z` on the value regex.

**The bracket anchoring is load-bearing here, more than on the customer path.**
The same session carries `SESSION_AGTALTAGTNR`, `SESSION_AGTNEUAGTNR`,
`SESSION_AGTCRSTOMAAGTNR` and `SESSION_AGTCRSMERLINAGTNR` — four other keys
*ending* in `AGTNR`. A sloppy substring match binds the wrong agency (the "old"
or "new" number after a migration). Match the full bracketed key, never a suffix.

### Correction — the Path 2 "discovering extractor" would NOT have worked

The earlier draft proposed matching any `SESSION_*` key containing `AGT` or
`AGENTUR` and demanding exactly one match. The real session has **roughly 130
such keys**. That rule would have rejected every legitimate login, permanently
and silently. It is dropped: use the literal.

Worth keeping as a lesson — the "exactly one match" safety rule was designed
against an imagined dump, and only real data showed it inverted into a
guaranteed-failure rule. Do not design parsing rules against a hypothetical
payload shape.

### Other keys in the same session, and what they change

| Key | Use |
|---|---|
| `SESSION_AGTID` | internal agency id — the key for `GET /get/agentur?agenturId=`, which worked in every probe. Useful second lookup path. |
| `SESSION_AGTKETTENLOGINKZ` | **chain-login flag.** This is the missing lever for §9.3: chain logins are detectable, so "head office sees branches" can be decided on real state instead of guessed. |
| `SESSION_AGTOBER` | **array of parent agency numbers** — the hierarchy, in the session. Same relevance as above. |
| `SESSION_AGTHIERstr` | hierarchy string; note the mixed-case suffix, easy to mistype. |
| `SESSION_EXPID`, `SESSION_EXPVORNAME`, `SESSION_EXPNACHNAME`, `SESSION_EXPKRZ` | the Expedient **is** in the session, as §3 predicted. D10 keeps this server-side-unused (widget-only name), but it is here if the scope widens. |
| `SESSION_AGTTYPE` | `TA` in this session — a **third** type beyond the `VK`/`NA` seen in `agenturenliste`. Do not enumerate agency types from the list endpoint alone. |
| `SESSION_AGTAKTIV`, `SESSION_AGTBUCHUNGSPERRE`, `SESSION_AGTSELEKTIONSPERRE` | activity/lock state, available at auth time if we ever want to refuse locked agencies. |
| **no `SESSION_ADRKUNDENNR`** | this agentur session carried no customer key, so mode exclusivity holds — one sample, not a proof. |

**Trap:** `SESSION_EXPNAME` is **not** the Expedient's name — in the observed
session it held the *agency* name, while the person's surname was in
`SESSION_EXPNACHNAME` and the first name in `SESSION_EXPVORNAME`. If the widget
name ever moves server-side, do not read `SESSION_EXPNAME`.

### ⚠️ What this dump proves about `ss.php` exposure

The session contains, to any holder of the `PHPSESSID`: the agency's password
**hash** (`SESSION_AGTPASSWORD`) and **salt** (`SESSION_AGTSALT`), `BANKIBAN`,
`BANKBIC`, `USTIDENT`, `STEUERNR`, `FIBUPROVISIONKONTO`, full address, phone and
emails — and in the observed record, a **plaintext password sitting in
`SESSION_AGTBEZ`**, i.e. the agency's free-text `bezeichnung` field.

Two consequences:

1. **The `ss.php` over-exposure already reported to the owner is confirmed and
   worse than "hash and PII".** The replay token really is password-grade;
   `kunden_auth`'s rule — used for exactly one call, never logged, never stored,
   never returned, response body never printed even on error — carries over to
   `agentur_auth` unchanged and non-negotiably.
2. **The allowlist is vindicated by a concrete case.** `bezeichnung` is an
   innocuous-looking free-text field that a denylist would have kept, and it
   contained a live password. §6's agency whitelist excludes it, and no
   free-text agency field may ever be added without re-reading this paragraph.

**Never commit a real dump.** Fixtures are fabricated. The observed session's
credentials should be treated as compromised and rotated — see the report to the
owner, 2026-07-31.

## The Assignment

M0 is done, so the remaining non-code item is the one question code cannot
answer:

**Ask the Vertriebsteam whether independent mobile Reiseberater*innen ever share
one Agenturnummer.** Agency-level binding is the only workable scope (§9.4 —
per-Expedient filtering was measured and would hide most bookings), so if they do
share, each sees the others' bookings and their customers' names. Filtering
cannot fix it: the options are to exclude those agency types, or to accept it
explicitly and write it down.

Attempted and failed as a measurement, so it really does need asking: of 240
sampled agencies, **not one** had an Expedient with position
`Mobile/r Reiseberater/in` (234× `Expedient/in`, plus Büroleiter/in,
Auszubildende/r, Geschäftsführer/in). The field is not maintained for mobile
advisors, so the API cannot answer this.

Second, smaller, same conversation: **should a chain's head office see its
branches' bookings?** Today it does not (§3). §12 found
`SESSION_AGTKETTENLOGINKZ` and `SESSION_AGTOBER` in the session, so either answer
is implementable — but it is a product decision, not a technical one.
