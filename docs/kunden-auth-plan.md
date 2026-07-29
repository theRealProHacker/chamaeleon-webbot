# Kunden-Modus — real auth plan (v2, cross-origin corrected)

> **HISTORY, NOT INSTRUCTIONS.** The authoritative document for this feature is
> **`docs/kunden-auth-spec.md`** — status, remaining work, the widget contract
> and the go-live sequence live there. This file records *why*: what v1 got
> wrong, the eng-review findings, the entropy measurement, and the decisions
> behind each choice. Read it for rationale. If the two ever disagree, the spec
> wins and this file is stale.

Closes the IDOR: today the widget asserts `kunden_id` and the server trusts it,
so anyone with a valid Kundennummer can read that customer's whole booking
history + Zahlstand. This derives the id from a verified MeinChamäleon session
instead.

## What v1 got wrong

v1 assumed our API was same-site with MeinChamäleon, so the browser would attach
the session cookie automatically and the backend could read it from
`request.cookies`. **It isn't — the API is a different origin.** A cookie is only
sent to requests going to its own domain, so the `chamaeleon-reisen.de` session
cookie never reaches our backend on a cross-origin call (CORS / `credentials`
don't change that). v1's `/kunde/auth` is therefore inert. (It was pushed and
force-reverted from live on 2026-07-28; a real customer sample incl. a password
hash leaked into that commit — treat that hash as compromised, rotate it, and
never let real `ss.php` output back into the repo.)

## Chosen transport (owner decision 2026-07-28): frontend forwards the cookie value

The widget runs on the chamaeleon page, reads its own `PHPSESSID` via
`document.cookie` (same-origin to the page), and sends it to our backend in the
`/kunde/auth` body. The backend calls `ss.php` with that `PHPSESSID`, reads
`SESSION_ADRKUNDENNR`, binds it to `session_id`, and discards the token.
`session_id` is the bearer token thereafter (`/chat/stream` reads only the
binding, never the body id).

**Why it's real auth, not IDOR:** to read customer V's data an attacker must
present V's live `PHPSESSID` — which already grants full access to V's account.
No new privilege. The Kundennummer is derived server-side from a real session,
never asserted by the client.

**Decision status (2026-07-28):** confirmed after independent review — this
transport is kept. The independent review judged the signed-token fallback
strictly superior (no live credential transits our server, survives the site
adding `HttpOnly`, no `ss.php` replay); it stays the documented fallback if a gate
fails. The hardening requirements below are mandatory for this transport, not
optional.

**Gate revised (eng review 2026-07-28, D6):** the gate is now on the **push**, not
on writing the code. The endpoint is the cheapest and most faithful way to test
C2/C3, so it is implemented first and deployed dark; C2 is answered by curling it.
A failed C2 means revert and switch to the fallback. See Order of work.

```
Browser on chamaeleon page (logged in)      Our backend (different origin)     chamaeleon-reisen.de
  │  read PHPSESSID via document.cookie        │                                │
  │  POST /kunde/auth {session_id, phpsessid}─►│  GET ss.php  Cookie: PHPSESSID ►│ ss.php
  │  (cross-origin, CORS-allowed, no cookie)   │  ◄── SESSION_ADRKUNDENNR ───────│
  │  ◄── {authenticated:true} ─────────────────│  bind session_id→kunden_id      │
  │                                            │  discard phpsessid              │
  │  POST /chat/stream {session_id, messages} ►│  kunden_id = resolve(session_id)│
  │  ◄── SSE reply ────────────────────────────│  buchungen_tool → api.tourone.de│
```

## Live-system conditions — VERIFY EACH BEFORE THE FEATURE GOES LIVE

This is where v1 failed: it shipped on an unverified assumption. None of these are
"probably fine"; each has a concrete test. (Revised 2026-07-28: the gate is on the
push and on the widget, not on writing the code — see Order of work.)

**C1 — `PHPSESSID` is readable by the widget's JS.**
- Requires: the *authenticated* `PHPSESSID` is not `HttpOnly`, AND the widget is an
  inline script on the chamaeleon page (a cross-origin iframe served from our
  domain sees its *own* cookies via `document.cookie`, not the page's).
- Evidence: anonymous `PHPSESSID` carries no `HttpOnly/Secure/SameSite` (probed
  2026-07-28). Authenticated flags + the widget's execution context unconfirmed.
- Test: on a logged-in MeinChamäleon page, DevTools console `document.cookie`
  shows `PHPSESSID`; confirm the widget is not a cross-origin iframe.

**C2 — the PHP session is portable server-side (make-or-break).**
- `ss.php` must return the Kundennummer when called **from our backend's IP** with
  the customer's `PHPSESSID`. If the session is bound to the login IP (or UA), the
  backend call returns an empty array → auth silently fails for everyone.
- Evidence: `ss.php` accepts client-supplied and reused SIDs and does not rotate
  them; the cookie config looks unhardened — suggests no IP-binding, but this is
  NOT proven (anonymous sessions can't tell "not logged in" from "IP mismatch").
- Test (needs one real logged-in cookie): take a throwaway/test account's
  logged-in `PHPSESSID` from a browser and replay it **from Railway's actual
  egress IP** — NOT a residential or other machine. A home IP can false-pass
  while the datacenter IP that will make this call in prod trips a WAF/allowlist;
  the test must run where the backend runs.
- **Mechanism: `/kunde/auth` IS the probe** (eng review 2026-07-28, D6 — replaces
  the earlier one-shot debug route). The route has to be written either way and
  the delta is one signature plus one route body, so building a throwaway probe
  to avoid writing it costs more than writing it — and the real route is the
  better test, because a hand-rolled curl can pass on a header the real client
  never sends. Deploy it dark (no widget calls it yet; fail-closed; 100/h), then
  once:
  ```bash
  curl -sX POST https://<railway-host>/kunde/auth \
    -H 'Content-Type: application/json' \
    -d '{"session_id":"c2-probe","phpsessid":"<test-account PHPSESSID>"}'
  ```
  `{"authenticated":true}` → **C2 and C3 both hold** (one `PHPSESSID`, no other
  cookie, from Railway's IP, through the production code path). `false` → C2
  fails → this transport cannot work; use the fallback. No debug route is
  deployed and there is nothing to remember to remove.
- If it turns out UA-bound: also forward the customer's `User-Agent` and replay it.

**C3 — `PHPSESSID` is the only cookie `ss.php` needs.**
- Test: answered by the same C2 curl above — it sends ONLY `PHPSESSID`, so
  `authenticated:true` proves C3 at the same time. If it needs other cookies,
  the widget forwards those names too (and they inherit the same "live
  credential" handling).

## Fallback if C1 or C2 fails

- **C1 fails** (cookie not JS-readable, or widget is a cross-origin iframe): the
  client can't read the token → frontend-forwards-cookie is impossible.
- **C2 fails** (session IP/UA-bound): the backend can't replay `ss.php` from its
  own IP → frontend-forwards-cookie is impossible. Note a reverse-proxy variant
  does NOT rescue this: the `ss.php` call still originates from our backend's IP.
- Robust fallback: **the chamaeleon server mints a short-lived signed token**
  (HMAC/JWT with the Kundennummer, secret shared with us). Frontend fetches it
  same-origin (cookie-authed), sends it to `/kunde/auth`, we verify the signature.
  No session token transits, no `ss.php` replay, forgery-proof — but needs the
  owner to build the endpoint. Keep this ready in case a condition fails.

## Code delta — IMPLEMENTED 2026-07-28 (not pushed; C1/C2 still unproven)

- `kunden_auth.verify_meinchamaeleon_session(phpsessid, user_agent="")` — takes the
  forwarded token as an argument, builds `cookies={"PHPSESSID": phpsessid}` and a
  browser-like UA header (the customer's own UA wins when forwarded, which is
  what makes the replay work if the session turns out UA-bound), calls `ss.php`,
  extracts **only** `SESSION_ADRKUNDENNR`, runs it through `parse_kunden_id`.
  Never raises, never logs the token or body. Rejects malformed tokens against
  `^[A-Za-z0-9,-]{16,128}$` **before** the value can reach a Cookie header.
- `kunden_auth.unbind(session_id)` — NEW, see Issue 1 below.
- `/kunde/auth` — reads `session_id` AND `phpsessid` from the JSON body (NOT
  `request.cookies`); **unbinds first**, then verifies, then binds only on
  success; logs `authenticated=<bool>` and nothing else. Rate-limited. Fail closed.
- `/chat/stream` — unchanged (`kunden_auth.resolve(session_id)`; body id ignored).
- CORS — the cross-origin POST from `chamaeleon-reisen.de` is already in the
  origins list; the token rides in the body, so no cookie/credentials needed.
- Tests — `tests/test_kunden_auth.py` rewritten with **fabricated** sample data
  and un-gitignored (16 tests, all passing). The old local-only fixture held a
  real customer's hash and salt; it is gone.

## Security / privacy

- Only `SESSION_ADRKUNDENNR` leaves `verify`. `ss.php` also returns the password
  hash, salt and full PII — never logged, never stored, discarded with the
  response.
- The forwarded `PHPSESSID` is a live credential (it unlocks the hash via
  `ss.php`). Handle like a password: HTTPS only, never logged, discarded right
  after the single call.
- **`session_id` as bearer token — accepted (eng review 2026-07-28, D3), and
  MEASURED 2026-07-29: it is NOT guessable.** See "Is `session_id` guessable?"
  below. The short version: the entropy is fine, the exposure is the risk.
- Tests and docs use ONLY fabricated sample data. The leaked real sample must
  never re-enter the repo.
- Separate owner report: `ss.php` over-exposing the session (hash/salt/PII) to any
  cookie-bearer is a site vulnerability independent of the chatbot.

## Is `session_id` guessable? No — measured 2026-07-29

Making `session_id` the auth token raises the obvious objection: the widget mints
it with `Math.random()`. That objection was measured rather than argued, because
"it's `Math.random()`" is a real smell but not automatically a real hole.

**Verdict: a remote attacker cannot guess a bound `session_id`.** Against 100
concurrently bound sessions, a 100,000-IP botnet saturating the 100/h/IP limit
needs **4.9 × 10⁸ years** on average. From a single IP: 4.9 × 10¹³ years, roughly
3,600× the age of the universe. Do not spend effort here.

### The generator

```js
// cham-chatbot/chatbot.html
'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9)
```

V8's `Math.random()` emits exactly `k · 2⁻⁵²` (verified: `r * 2^52` was
non-integer 0 times in 3,000,000 draws), so the token is
`floor(k · 36⁹ / 2⁵²)`. Because `2⁵² // 36⁹ = 44` remainder `34,961,533,960,192`,
the most probable token has 45 preimages:

```
H∞ = -log2(45 / 2^52) = 46.508147 bits      (naive 9·log2(36) = 46.529325)
```

The naive figure is right to 0.021 bits. Two plausible-sounding weaknesses were
tested and did **not** hold up:

- **Position 0 is not biased.** All nine positions use the full `0-9a-z` set;
  measured per-position entropy 5.169920 vs. ideal 5.169925, and that 5×10⁻⁶ gap
  is exactly the plug-in estimator bias, not a real skew.
- **Short tokens are rarer, not commoner.** `substr` returns 8 or 7 chars in
  ~0.02% of draws (a double's base-36 expansion can terminate early). In
  40,000,000 draws all 8,291 short strings were *distinct* — they are a thinner
  tail, not a dictionary.

Timestamp component over the 12h binding window: `log2(43,200,000) = 25.36` bits.
**Total ≈ 71.87 bits ≈ 4.32 × 10²¹.**

### Why the search is hopeless even with generous assumptions

| Bound sessions | 1 IP | 1k botnet | 100k botnet |
|---|---|---|---|
| 10 | 4.9e14 yr | 4.9e11 yr | 4.9e9 yr |
| 100 | 4.9e13 yr | 4.9e10 yr | **4.9e8 yr** |
| 1000 | 4.9e12 yr | 4.9e9 yr | 4.9e7 yr |

**The rate limit is not what makes this safe.** Remove it entirely and an
unthrottled 10,000 req/s host still needs 1.4 × 10⁸ years. Hand the attacker the
entire timestamp for free and a 100k botnet against 100 sessions still needs
11.6 years. The generator carries this alone — the 100/h cap is a cost and DoS
control, not a confidentiality control.

### The oracle is expensive for the attacker

`/chat/stream` runs the full agent whether or not the session is bound, with no
distinguishable status code, so the only way to test a guess is to send a chat
message and read the reply: **one guess = one request + one LLM call.**
`/kunde/auth` is not a cheaper oracle — it unbinds *before* verifying, so probing
it destroys the binding instead of revealing it. A 10⁷ guesses/hour campaign is
~10⁷ Gemini turns and ~10⁷ Supabase rows per hour of *our* spend: a
self-announcing DoS long before it is a breach.

### `Math.random()`'s non-crypto nature is not reachable here

xorshift128+ state recovery needs ~4-5 consecutive outputs from the victim's
stream. `generateSessionId()` runs **once per browser per 12h**, so exactly one
output exists and it *is* the secret. Fresh V8 realms are independently seeded
(three realms measured: 0.0882 / 0.2592 / 0.2396), so the attacker's own tokens
reveal nothing about anyone else's. No response path echoes a `session_id` back —
it appears only in a literal `400` message and in stdout error strings, never in a
URL, so there is no Referer, history or access-log leak.

The one nuance: the widget is an inline script, sharing the page's PRNG stream
with every third-party tag on chamaeleon-reisen.de. Such a script could recover
the state — but it can also just read `localStorage` in one line. **The PRNG
weakness adds zero marginal capability to an attacker who has that position, and
is unreachable by one who doesn't.** Switching to `crypto.getRandomValues()` is
worth one line as hygiene (it retires the objection permanently) but closes no
reachable attack path. File it as tidiness, not as a fix.

### What the real exposure is, ranked

Guessing is last. The token's risk is **storage and distribution**, not entropy:

1. **Supabase** — `session_id` is a column on every logged turn. One leaked
   credential yields every bound session in the window, plus transcripts.
2. **Third-party scripts on chamaeleon-reisen.de** — the widget is inline and
   `localStorage` is same-origin. Network-recording session-replay tools
   (Hotjar/FullStory/Clarity) capture the `/chat/stream` POST body verbatim.
   This is the realistic remote attack.
3. **The dashboard** — serves live `session_id` values and, unlike `/chat/stream`
   and `/kunde/auth`, carries **no rate limit at all** (`default_limits=[]`, no
   `@limiter.limit` on any dashboard route). `DASHBOARD_PASSWORD` is now
   mandatory, but it is open to unthrottled online guessing with the username
   defaulting to `admin`.
4. **Shared / kiosk browsers** — mitigated by unbind-before-verify, but only if
   the widget calls `/kunde/auth` unconditionally on every open.
5. Browser extensions, TLS-terminating middleboxes.
6. **Guessing** — 10⁸+ years. Not a threat.

**Follow-up worth taking seriously:** `session_id` now does two jobs — Supabase
log correlation key and bearer credential. Splitting them (a separate random log
id) is a few lines and deletes item 1 from that list entirely.

## Hardening requirements (from independent review — mandatory for this transport)

Keeping the cookie-forwarding transport makes these requirements, not nice-to-haves:

- **`/kunde/auth` is an `ss.php` oracle + IP-laundering proxy.** It lets anyone
  probe whether a `PHPSESSID` is a live session (via the boolean) and makes *our*
  IP do the `ss.php` call. Return only `{authenticated: bool}` (already), keep a
  tight per-IP rate limit (tighter than chat), and accept it stays abuse-exposed
  — it can't be fully closed while it forwards raw cookies.
- **The forwarded `PHPSESSID` is a password-grade credential in transit** (it
  unlocks the hash via `ss.php`). Audit EVERY ingress logging path so the
  `/kunde/auth` body never lands in a log. **Audited 2026-07-28 — clean:**
  `rate_limit._log_rejected_turn` reads the body on a 429 but only ever touches
  `session_id` and `messages[-1]`, and a `/kunde/auth` body has no `messages`, so
  the `if session_id and messages` guard drops it before anything is queued;
  gunicorn access logs record the request line, not the body; `db_logging` is
  only reached with explicit message payloads. HTTPS only; discard right after
  the single call. **Re-audit this if `_log_rejected_turn` ever starts logging
  whole bodies.**
- **Send a browser-like `User-Agent`** on the `ss.php` call (not
  `python-requests`) — a datacenter UA/IP can be filtered even if the session
  isn't IP-bound. **Done** (`kunden_auth.DEFAULT_USER_AGENT`; the customer's own
  UA wins when forwarded).
- **Observability, or you fly blind.** Fail-closed collapses "not logged in", "IP
  mismatch", "WAF block", "expired" into one `authenticated:false`; if C2 fails in
  prod it looks identical to "widget not shipped." **Done (D7):** `/kunde/auth`
  prints `[kunden_auth] authenticated=<bool>` ungated — once per chat open, not
  per message, so it cannot clog logs the way the per-tool-call line did. No id,
  no token. The startup canary the earlier draft floated is **rejected as not
  buildable**: it would need a stored logged-in `PHPSESSID`, and PHP sessions
  expire, so the only way to hold one would be storing a real customer's password
  in env — strictly worse than no canary.
- **Binding TTL (12h) outlives site logout — accepted explicitly (D4).** We never
  re-verify after the one call, so a logged-out customer keeps getting bookings
  for up to 12h *in an already-open chat tab*. Every fresh chat open re-auths and
  fails closed (see Issue 1), so the shared-browser case is covered; the residual
  is the open tab. Shortening the TTL was rejected: it downgrades a customer
  mid-conversation, which reads as a broken bot, and 12h is deliberately matched
  to `db_logging.SESSION_MESSAGE_EXPIRY_SECONDS`.
- **`/kunde/auth` rate limit is 100/h, the same as chat — not tighter.** The
  earlier draft asked for a tighter limit. Deliberately not applied: flask-limiter
  scopes per endpoint, so this is already a separate 100/h bucket, and the 15/h
  limit that got suspended for false positives is the cautionary tale. Customers
  behind a corporate or hotel NAT share an IP. Revisit if the oracle is actually
  probed in the logs.

## Local state to clean up (from the reverted v1) — DONE 2026-07-28

- `app.py` called `verify_meinchamaeleon_session(request.cookies)` (inert
  cross-origin) and its docstring claimed "same-site" — both rewritten.
- `kunden_auth.verify_*` now takes the forwarded `phpsessid` string; the module
  docstring says plainly that we are a different origin and that v1 was inert.
- `TODOS.md`'s IDOR item was corrected back to open (it wrongly said `[x] fixed`).
- **`docs/kundendaten-datenzugriff.md` (missed by the earlier draft, eng review
  Issue 6):** the working tree had it claiming "**IDOR fixed: server-side session
  verification shipped**" and "the `kunden_id` is no longer client-asserted" —
  false, and directly contradicting the corrected `TODOS.md` entry, in the very
  file `TODOS.md` names as the authority on what the API exposes. Reverted to the
  honest pre-edit text.

  **Update 2026-07-29:** that file now carries a two-row table under "Rules for
  changing this" — *live* (client-asserted, still exposed) vs. *this working tree*
  (server-derived, not pushed) — plus an explicit "do not read this as fixed".
  That is the honest shape and it can stay. **What still must not happen before
  M2+M3+M5 have all landed: collapsing it to a single "verified" story.** The
  Issue 6 failure mode is a doc that describes the tree as if it were production.

## Rollout (fail-closed)

`/chat/stream` ignores the body id, so on deploy Kunden-Modus is off for everyone
until the widget sends `phpsessid` to `/kunde/auth`. Safe, but the feature is dark
until the widget ships. Widget change is owner-side.

## Eng review findings (2026-07-28)

### Issue 1 [P1] — a failed re-auth kept the previous customer bound (FIXED)

The worst finding, and it was not in any earlier draft. `/kunde/auth` bound on
success but did nothing on failure, and `kunden_auth` had no `unbind` at all:

```python
kunden_id = kunden_auth.verify_meinchamaeleon_session(...)
if kunden_id:
    kunden_auth.bind(session_id, kunden_id)      # ← no else. no unbind.
```

The widget keeps the same `session_id` in localStorage for up to 12h, so on a
shared browser:

```
  Customer A            logs in → /kunde/auth → bind(sess-42 → 123456)
  Customer A            chats, logs out of the website, leaves
  Person B (same PC)    opens chat, widget re-auths with the SAME sess-42
                        ss.php: nobody logged in → verify → None
                        → no bind, no unbind → sess-42 STILL → 123456
  Person B              "wann geht mein Flug?" → A's bookings + Zahlstand
```

The endpoint answered `authenticated:false` and leaked anyway — the exact IDOR
this plan exists to close, back through the logout path. Fix: `unbind()` at the
top of the route, before verification, so every failure path (logged out, WAF,
timeout, expired, malformed token) lands anonymous. Pinned by
`test_failed_reauth_does_not_keep_previous_customer`.

### Issues 2-6

- **Issue 2 [P2]** — `session_id` is a `Math.random()` token now guarding
  financial data. Accepted + documented (see Security / privacy).
- **Issue 3 [P2]** — 12h TTL vs. site logout. Accepted explicitly (see Hardening).
- **Issue 4 [P1]** — the C2 debug route would have published an unauthenticated
  ss.php oracle on a live host, before any hardening existed, with removal
  depending on memory. Deleted; the endpoint is the probe (D6).
- **Issue 5 [P2]** — no prod visibility at all. One ungated log line (D7); canary
  rejected as not buildable.
- **Issue 6 [P1]** — `docs/kundendaten-datenzugriff.md` claimed the IDOR was
  fixed and shipped while `TODOS.md` said the opposite. Reverted.

### Outside voice — findings verified and fixed (2026-07-28)

An independent reviewer found four more holes, **each reproduced locally before
acting on it** (the subagent's transcript carried an injected instruction aimed at
its own reviewer, so nothing was taken on trust):

- **OV1 [P1] — a 429 on `/kunde/auth` skipped `unbind()` entirely. FIXED.**
  flask-limiter rejects in `before_request`, so the view body never runs.
  Reproduced: `req0 200 view_entered=1 / req2 429 view_entered=2` — the view is
  never entered on rejection. That means Issue 1's fix had a hole an attacker
  could *force*: anyone sharing the victim's egress IP (office, hotel, café NAT)
  burns the 100/h budget and every subsequent re-auth from that IP fails OPEN,
  keeping the previous customer bound. Now handled in
  `rate_limit._on_rate_limit`, which unbinds before rendering the rejection, and
  returns JSON `{"authenticated": false}` with a real 429 instead of the chat
  route's SSE-200 (which the widget would have parsed as "not logged in").
  Regression: `test_rate_limited_auth_still_clears_the_binding`.
- **OV2 [P1] — the `ss.php` call followed redirects and the forwarded cookie had
  no domain. FIXED.** `requests` builds the jar via `cookiejar_from_dict`, which
  sets `domain=""`, and a blank-domain cookie matches every host. Reproduced: the
  same `PHPSESSID` was sent to `www.chamaeleon-reisen.de`, `evil.example.com` and
  `attacker.tld`, and a live 302 forwarded it to the redirect target. Any 3xx out
  of `ss.php` — WAF interstitial, maintenance redirect, `http://`
  canonicalisation — would hand a password-grade credential to that host in
  cleartext, and `resp.content` would come from the target, feeding
  `extract_kundennr`. Now `allow_redirects=False`; anything but a direct 200 fails.
- **OV3 [P2] — `extract_kundennr` took the first match anywhere in the body.
  FIXED.** `print_r` emits session values raw, so a field serialised before the
  real key could shadow it and bind us to another Kundennummer. Now `findall`
  with exactly-one-match required; ambiguity is rejected, not resolved.
  Regression: `test_extract_rejects_ambiguous_dump`.
- **OV4 [P2] — `/chat/stream` called `resolve()` before validating `session_id`.
  FIXED.** Reproduced: a JSON object or array `session_id` reaches
  `_bindings.get()` with an unhashable key → `TypeError` → HTTP 500. The type
  check now runs first.
- **OV5 — the fail-closed guarantee is client-triggered. DOCUMENTED, not fixed.**
  `unbind()` only ever runs inside `/kunde/auth`, so "a fresh chat open fails
  closed" holds only if the widget calls it unconditionally on every open,
  *including when it finds no `PHPSESSID`*. The widget is owner-side. This is now
  written into `kunden_auth.py` as a contract rather than a guarantee — **it is a
  requirement on the widget change, not an optional detail.**

### Outside voice — OV6: dashboard password now mandatory (FIXED, owner decision)

**`DASHBOARD_PASSWORD` used to fall back to the literal `"change-me"`**
(`dashboard.py`), while `/api/dashboard` serves `session_id` values. This plan
turns `session_id` into an auth token, so the documented "a dashboard reader can
lift it" risk silently changed meaning: with the fallback in force and the env var
unset, "dashboard reader" would mean anyone on the internet, who could dump live
session_ids and replay them against `/chat/stream` to read those customers'
Buchungen and Zahlstand.

**Owner decision 2026-07-28: the default is removed and startup fails without it.**
`API_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")` plus a `RuntimeError`
naming the variable and why it matters. Verified both directions: with
`DASHBOARD_PASSWORD=""` the import raises; with it set the app boots and
`check_auth` accepts only the real credentials. `check_auth` also moved to
`hmac.compare_digest` so a wrong password cannot be recovered by timing.

Deploy note: **this turns a silent misconfiguration into a hard boot failure.** If
the variable is missing on Railway the service will not start — that is the
intended trade (fail loudly at boot, never fail open in production), but it means
the variable must exist there *before* this is pushed.

### Outside voice — flagged, NOT fixed (pre-existing, outside this change)

- **The catch-all proxy caches process-wide on path alone** — `@cache` on
  `proxy(path)` (`app.py:270-273`) ignores query string, method, body and
  cookies while forwarding the visitor's cookies upstream. Pre-existing and
  outside this change, but it shares a host with password-grade credentials.
- **`POST /kunde/auth/` (trailing slash) does not reach the route** — Werkzeug
  matches the catch-all proxy first, so a body containing `phpsessid` would be
  proxied upstream. Worth a redirect or an explicit rule.
- **`_bindings` is only pruned on access** — sessions never revisited stay in
  memory for the process lifetime. Bounded by real logins; low priority.

### What already exists (reused, nothing rebuilt)

`extract_kundennr`'s exact-key regex (already avoided `SESSION_ADRID` and the
hash), `bind`/`resolve` with TTL and lock, `parse_kunden_id` as the allowlist,
the fail-closed error handling, the armed 100/h limiter and its SSE rejection
handler, and `/chat/stream` already ignoring the body id. The review changed one
signature, added `unbind`, and added a log line.

### NOT in scope (considered, deferred, with reasons)

- **Widget `crypto.randomUUID()` / separate auth token** — owner accepted the
  current token instead (D3). Revisit with the signed-token fallback.
- **Startup auth canary** — not buildable without storing a real password (D7).
- **Tighter rate limit on `/kunde/auth`** — re-introduces the false-positive class
  that suspended the 15/h limit; already a separate 100/h bucket.
- **Route-level test of the unbind ordering** — needs `import app`, which triggers
  live Supabase reads (the standing test-isolation TODO). The module-level
  regression test catches a broken `unbind`, not a deleted call site.
- **Reply redaction in logs, automated rate-limit tests** — previously declined,
  unchanged.
- **Signed-token transport** — the documented fallback, built only if C2 fails.

## Order of work (revised 2026-07-28, D6 — the endpoint is the probe)

1. **Implement** `/kunde/auth` + `kunden_auth` with fabricated test data. **DONE**
   — not pushed.
2. **Get explicit sign-off, then push** (public repo = one-way door). Safe to
   deploy dark: no widget calls it, fail-closed, rate-limited, and `/chat/stream`
   ignores the body id, so Kunden-Modus is off for everyone until the widget ships.
3. **Verify C2 + C3** with the one curl in the C2 section, against the deployed
   route, using a throwaway/test account's live `PHPSESSID`. `true` → both hold.
   `false` → **stop, revert, switch to the signed-token fallback.**
4. **Verify C1** — DevTools on a logged-in MeinChamäleon page: `document.cookie`
   shows `PHPSESSID`, and the widget is not a cross-origin iframe. Pure frontend
   check, no backend work, can happen any time.
5. Widget change (owner-side): read `PHPSESSID`, POST it to `/kunde/auth` on chat
   open. Kunden-Modus goes live only here.

The earlier "verify C2 before writing any code" ordering was written when the code
looked expensive. It is one signature and one route body — building a throwaway
probe to defer that costs more than writing it, and leaves an unauthenticated
session-validity oracle on a live host if anyone forgets to remove it.

## Test coverage (eng review 2026-07-28)

```
CODE PATHS                                          USER FLOWS
[+] kunden_auth.verify_meinchamaeleon_session       [+] Customer opens chat while logged in
  ├── [***] malformed token -> no outbound call       ├── [GAP] [->E2E] happy path, needs the widget
  ├── [***] logged in -> Kundennummer                 └── [***] no /kunde/auth call -> feature dark
  ├── [***] anonymous dump -> None
  ├── [***] non-200 -> None                         [+] Shared browser / logout
  ├── [***] exception -> None                         ├── [***] failed re-auth -> anonymous
  └── [***] cookie + browser UA forwarded             └── [**]  binding expiry -> anonymous
[+] kunden_auth.unbind
  ├── [***] idempotent, empty-safe                  [+] Error states the customer sees
  └── [***] clears an existing binding                ├── [**] ss.php down -> normal bot, no leak
[+] kunden_auth.bind / resolve                        └── [GAP] mid-chat TTL expiry wording
  ├── [***] roundtrip, unknown, empty, expired
[+] app.py /kunde/auth route ordering
  └── [GAP] unbind-call-site deletion (needs import app -> live Supabase)
[+] app.py /chat/stream kunden_id source
  └── [GAP] body-supplied kunden_id ignored (same import-app blocker)

COVERAGE: 16/21 paths tested (76%)  |  Code paths: 14/16 (88%)  |  User flows: 4/6 (67%)
QUALITY: ***:13 **:3  |  GAPS: 5 (1 E2E, 0 eval)
```

Legend: `***` behavior + edge + error | `**` happy path | `[->E2E]` needs integration test

The two route-level gaps share one root cause: `import app` triggers live Supabase
reads at import time (the standing test-isolation TODO). Both are covered manually
by the C2 curl and by the existing `/chat/stream` behaviour. No failure mode is
silent AND untested AND unhandled — **0 critical gaps.**

## Failure modes (per new codepath)

| Failure | Test? | Handled? | Customer sees |
|---|---|---|---|
| Malformed / injected `phpsessid` | unit | rejected before any outbound call | normal bot, no Kunden-Modus |
| ss.php timeout / 5xx / network error | unit (mocked) | fail closed, no binding | normal bot, no leak |
| Not logged in (empty dump) | unit | fail closed | normal bot |
| **Failed re-auth on a shared browser** | **unit (regression)** | **unbind before verify** | **normal bot — no other customer's data** |
| Binding expired mid-chat (12h) | unit | resolve evicts, returns None | Kunden-Modus quietly off |
| C2 fails in prod (WAF / IP-bound) | manual curl | fail closed for everyone | feature simply never activates |
| ss.php slow (8s cap) | — | timeout bounded, gevent worker yields | chat open slightly delayed |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN, 2026-07-28, commit 13bc7a4) | 6 issues + 6 outside-voice, 0 critical gaps remaining |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | backend-only, not applicable |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CROSS-MODEL:** Outside voice (Claude subagent, fresh context) returned 6 findings.
  Its transcript carried an injected instruction targeting its own reviewer, so every
  claim was reproduced locally before being acted on. 5 confirmed and FIXED (OV1 429
  bypasses unbind, OV2 redirect leaks the cookie, OV3 first-match shadowing, OV4
  unhashable session_id 500, OV6 dashboard password default removed); 1 confirmed and
  DOCUMENTED as a widget contract (OV5 client-triggered fail-close); the rest flagged
  as pre-existing and outside this change (proxy cache, trailing-slash routing,
  binding pruning). No cross-model tension: the outside voice found gaps in this
  review rather than disagreeing with it.
- **VERDICT:** ENG CLEARED — v2 implemented locally, 49 tests passing, **not pushed**.
  Sign-off gate stands. Remaining gates: C1 (DevTools), C2/C3 (one curl against the
  deployed route). A failed C2 means revert and switch to the signed-token fallback.
  Deploy prerequisite: `DASHBOARD_PASSWORD` must exist on Railway before the push, or
  the service will refuse to boot by design.

NO UNRESOLVED DECISIONS
