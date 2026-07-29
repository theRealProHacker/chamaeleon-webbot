# Kunden-Modus Auth — final spec

**This is the authoritative document for this feature.** It is self-contained:
you can build the remaining work from this file alone. **No other file may carry
this feature's status** — they defer here. Duplicated status is what let the docs
contradict each other last time (plan, eng review Issue 6).

- `docs/kunden-auth-plan.md` — the history: why each decision was made, what v1
  got wrong, the entropy measurement. Rationale, not instructions.
- `docs/kundendaten-datenzugriff.md` — the data surface a `kunden_id` unlocks and
  what of it crosses to Gemini. Split of concerns: **this file decides *who* the
  customer is, that one decides *what* they can reach.**
- `TODOS.md` — backlog pointer only.
- `README.md` — environment and layout; names this file as the thing to read
  before touching `kunden_auth.py`, `/kunde/auth` or the widget.

Status: **server deployed and dark.** `/kunde/auth` is live and fail-closed; the
widget on `main` does not call it, so Kunden-Modus is off for every customer.
**Transport proven end to end (C1, C2, C3 all hold).** The M5 widget change is
merged to `cham-chatbot` `develop` (PR #22, 2026-07-30) and therefore live on the
dev host, but **not on `main`** — go-live is owner-side in that repo.
Last updated 2026-07-30.

---

## 1. Status at a glance

| Component | State | Owner |
|---|---|---|
| `kunden_auth.py` — verify / begin_auth / commit_auth / resolve | **DONE**, 39 unit tests | — |
| `app.py` — `POST /kunde/auth`, `/chat/stream` binding lookup | **DONE** | — |
| `rate_limit.py` — 429 clears the binding, JSON response | **DONE** | — |
| `dashboard.py` — `DASHBOARD_PASSWORD` mandatory | **DONE** | — |
| Docs aligned (`README`, `TODOS`, datenzugriff, plan) | **DONE** 2026-07-29 — no file claims the IDOR is closed | — |
| Independent review, round 2 (2026-07-29) | **DONE** — 5 fixed: bind/unbind race under gevent, unbind-skipping body paths, `compare_digest` on non-ASCII, `$` anchor, loose Kundennummer gate | — |
| Independent review, round 3 (2026-07-29) | **DONE** — 3 more fixed: body-read memory amplification *introduced by round 2*, nested-key parse bug, endpoint-name coupling. One finding **not** closeable in code → §8 | — |
| `DASHBOARD_PASSWORD` set on Railway | **DONE** — confirmed by owner 2026-07-29 | owner |
| Sign-off + push | **DONE** 2026-07-29 — `2976e85`; service booted, `/kunde/auth` answers 400, proxy healthy | — |
| C2/C3 verification (one curl) | **DONE** 2026-07-29 — `{"authenticated":true}` against the deployed route with a test account. Session is portable to Railway's egress IP; `PHPSESSID` is the only cookie `ss.php` needs | — |
| C1 — widget is not a cross-origin iframe | **DONE** — verified live 2026-07-29 | — |
| C1 — authenticated cookie is JS-readable | **DONE** — verified 2026-07-29, logged-in Console returned the token | — |
| Widget: read `PHPSESSID`, call `/kunde/auth` | **ON `develop`, NOT ON `main`** — `cham-chatbot` `a59935c`, merged via PR #22 on 2026-07-30. Live on `leon.chamdev.tourone.de`; customers are still on the `main` widget, which has no Kunden-Modus at all | owner |
| Origin → `ss.php` host map | **DONE** 2026-07-30 — dev-host sessions were verified against production `ss.php` and always failed closed. `SS_URLS` + 7 tests, both mutations caught | — |

Deployed as `aaa77ef` (code + tests) and `2976e85` (docs), pushed 2026-07-29
together with the older unpushed `13bc7a4`.

## 2. What the feature does

A logged-in MeinChamäleon customer asks the bot about their own bookings,
flights and Zahlstand. The customer's identity is **derived server-side from a
verified session**, never asserted by the client.

```
Browser on chamaeleon page (logged in)      Our backend (different origin)     chamaeleon-reisen.de
  │  read PHPSESSID via document.cookie        │                                │
  │  POST /kunde/auth {session_id, phpsessid}─►│  begin_auth(session_id)        │
  │  (cross-origin, CORS-allowed, no cookie)   │  GET ss.php  Cookie: PHPSESSID ►│ ss.php
  │                                            │  ◄── SESSION_ADRKUNDENNR ───────│
  │  ◄── {authenticated:true} ─────────────────│  commit_auth → session_id→kdnr  │
  │                                            │  discard phpsessid              │
  │  POST /chat/stream {session_id, messages} ►│  kunden_id = resolve(session_id)│
  │  ◄── SSE reply ────────────────────────────│  buchungen_tool → api.tourone.de│
```

`session_id` is the bearer token after the one verification. `/chat/stream`
**ignores any `kunden_id` in the request body.**

## 3. What is DONE (server)

**`kunden_auth.py`**
- `verify_meinchamaeleon_session(phpsessid, user_agent="", origin="") -> str | None` —
  validates the token shape against `\A[A-Za-z0-9,-]{16,128}\Z` *before* it can
  reach a Cookie header, replays it against `ss.php` with a browser-like UA and
  `allow_redirects=False`, extracts only `SESSION_ADRKUNDENNR`. Never raises,
  never logs the token or body. (`\A..\Z`, not `^..$`: Python's `$` also matches
  before a trailing newline, so the old anchor let `"…\n"` through and the
  no-CRLF guarantee held only by accident further down the stack.)
- `extract_kundennr(body)` — `findall` requiring **exactly one** match, then
  `parse_kunden_id`'s allowlist, then a second, stricter gate: the value must
  look like a Kundennummer (`[0-9]{4,12}`). The allowlist only proves a string is
  safe in a URL; a nested print_r value renders as the literal `Array` and a
  logged-in non-customer session can carry `0`, and either would otherwise be
  bound and sent to TourOne with our bearer token. Ambiguity is rejected, not
  resolved.
- `begin_auth` / `commit_auth` — the auth sequence, made atomic. `begin_auth`
  clears the binding and claims a generation; `commit_auth` writes the result
  only if nothing superseded it. **This is not decoration:** the Dockerfile runs
  `gunicorn -k gevent --worker-connections 1000`, so "single worker" means one
  *process*, not one request at a time — the `ss.php` call yields, and without
  the generation a slow logged-in auth could land its `bind()` *after* a later
  anonymous auth had already cleared it, handing the next person the previous
  customer's Buchungen. Reproduced before the fix, and pinned by
  `test_superseded_auth_cannot_resurrect_the_previous_customer`.
- `SS_URLS` / `ss_url_for_origin(origin)` — **which** `ss.php` the token is
  replayed against, keyed on the request's `Origin`. A PHP session exists in
  exactly one host's store, and `PHPSESSID` cannot cross registrable domains at
  all, so verifying a `leon.chamdev.tourone.de` token against
  `chamaeleon-reisen.de` always returned an empty dump — Kunden-Modus was
  unreachable for anyone testing on the dev host (found 2026-07-30 from a live
  session where the bot correctly denied having booking access).
  `Origin` is client-controlled, so it is only ever a **key into the table, never
  the URL** — otherwise an attacker points verification at an `ss.php` they
  control and mints any Kundennummer. Unknown or absent origin, and any `http://`
  variant of a known one, fall back to production and therefore fail closed. All
  values are `https` because the token is password-grade; the bare domain maps
  straight to `www` since the replay refuses redirects. Agentur origins are
  deliberately absent — the two modes are mutually exclusive, so a binding made
  there could never be read. See §8 for the risk this accepts.
- `unbind` / `bind` / `resolve` — in-memory `session_id → (kunden_id, expiry)`,
  12h TTL, lock-guarded. `unbind` also cancels any auth in flight. Restart clears
  everything (fails closed). `bind` is the unconditional primitive — the route
  must not use it.
- `authenticate(body, user_agent, origin)` — owns the ORDER (clear → verify → commit
  only if not superseded). It lives here, not in the view, because a mutation run
  showed the view could be reordered to clear the binding *after* `ss.php` with
  the whole suite still green — `import app` triggers live Supabase reads, so
  nothing tested the view. Pinned by
  `test_binding_is_cleared_before_ss_php_is_called`, which asserts from *inside*
  verification that the previous customer is already gone.
- `read_capped_body` / `coerce_json_body` — see §10.

**`app.py`**
- `POST /kunde/auth` — **clears the binding first**, then verifies, then commits
  only if it was not superseded. Logs `[kunden_auth] authenticated=<bool>`
  (ungated; no id, no token).
- `/chat/stream` — validates `session_id` type, then
  `kunden_id = "" if is_agentur else (kunden_auth.resolve(session_id) or "")`.

**`rate_limit.py`** — a 429 on `/kunde/auth` clears the binding and returns JSON
`{"authenticated": false}` with status 429. Without this the view body (and its
unbind) is skipped entirely and the endpoint fails *open*. Parses the body the
same lenient way the view does, for the same reason.

**`dashboard.py`** — `DASHBOARD_PASSWORD` has no default; startup raises without
it. `check_auth` uses `hmac.compare_digest` **on utf-8 bytes**: on `str` it
raises `TypeError` for non-ASCII, which would have turned any umlaut in a
Basic-Auth header into an unauthenticated 500 — and a `DASHBOARD_PASSWORD`
containing `ä` or `€` into a total lockout, since every attempt including the
correct one would raise. The boot check only catches an *empty* password.

**Tests** — `tests/test_kunden_auth.py`, 32 tests, fabricated data only. Full
non-live suite 70 passing. Mutation-checked: reverting any of the fixes above
(clear-after-verify, bind-bypassing-generation, loose indent, uncapped body)
fails the suite. Run: `python -m pytest tests/test_kunden_auth.py -q`

## 4. What is MISSING — the remaining work

### M1. `DASHBOARD_PASSWORD` on Railway — DONE (confirmed by owner 2026-07-29)
The service **refuses to boot** without it, so this had to be in place before the
push. Owner confirmed the Railway service variable exists. Nothing further.

Keep it in mind for any future environment (a second Railway service, a staging
instance, a local run without `.env`): a missing value is now a hard boot
failure, not a silent fallback. That is deliberate — the dashboard serves live
bearer tokens and `"change-me"` was guarding them.

### M2. Sign-off, then push — head of the critical path
Public repo, push = deploy. Safe to deploy: the endpoint is fail-closed,
rate-limited, and nothing calls it, so Kunden-Modus is off for everyone until
M5 ships.

Pre-push checklist:

1. `DASHBOARD_PASSWORD` exists on Railway — **the service will not boot without
   it.** (M1, confirmed 2026-07-29. Re-check if the service was recreated.)
2. Non-live suite green: `python -m pytest tests/test_general.py
   tests/test_kunden_auth.py tests/test_kundendaten.py tests/test_previews.py
   tests/test_streaming.py -q` → 49 passing.
3. No real `ss.php` output or real customer record anywhere in the diff. This is
   not paranoia: a v1 commit leaked a customer's password hash and salt.
4. After the deploy, watch the logs for `[kunden_auth] authenticated=` — until
   M5 there should be **none**, because nothing calls the endpoint yet. One
   appearing means something is calling it that you did not expect.

### M3. Verify C2 + C3 — make-or-break (and finishes C1 in the same step)

Needs one throwaway/test account's live `PHPSESSID`. **Get it from the DevTools
Console, not from Application → Cookies:**

```js
document.cookie.match(/(?:^|;\s*)PHPSESSID=([^;]+)/)?.[1] ?? 'NOT READABLE — C1 FAILS'
```

The Application panel lists `HttpOnly` cookies too, so reading it there would hand
you a working token while telling you nothing about C1. The Console can only see
the cookie if it is *not* `HttpOnly` — which is exactly the open half of C1
(§M4). One login answers both.

- Value returned → **C1 fully holds**, and you are holding the token for the curl
  below.
- Nothing in the Console but the cookie visible in Application → the
  authenticated cookie is `HttpOnly`. **C1 fails, the transport is dead
  regardless of what the curl would say — go to §7 and do not push.**

Then, against the **deployed** host, so the `ss.php` call originates from
Railway's real egress IP:

```bash
curl -sX POST https://chamaeleon-webbot-production.up.railway.app/kunde/auth \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"c2-probe","phpsessid":"<test-account PHPSESSID>"}'
```

- `{"authenticated":true}` → **C2 and C3 both hold.** The session is portable to
  our IP, and `PHPSESSID` is the only cookie `ss.php` needs. Proceed.
  **This is what happened, 2026-07-29.** The §7 fallback is therefore not needed;
  it stays documented in case the site later adds `HttpOnly` or IP-binds sessions.
- `{"authenticated":false}` → C2 fails. The session is IP- or UA-bound, or a WAF
  blocks the datacenter IP. **Stop. Revert. Go to §7.**

A home or office IP is not a valid test — it can pass where the datacenter IP
fails. If it turns out UA-bound, the customer's own `User-Agent` is already
forwarded, so retry with the test account's UA in the header.

### M4. Verify C1 — DONE 2026-07-29, both halves

C1 had two halves. Both are now confirmed against the live site.

**✔ Half 1 — the widget is NOT a cross-origin iframe.** Verified against
`https://www.chamaeleon-reisen.de/MeinChamaeleon/Login`, i.e. inside the
MeinChamäleon area where this has to work:

```
chatbot_div_in_page_DOM : true    (#chatbot, .chatbot-dialog, .chatbot-messages …)
inline_widget_scripts   : 1       (38,657 chars, contains generateSessionId
                                   and the /chat/stream Railway endpoint)
widget_iframes          : 0
```

The only iframes on the page are Trustpilot and Cookiebot. The widget is an
inline `<script>` in the page's own DOM and origin, so `document.cookie` in it is
the *page's* cookie jar. This was the half that could have killed the transport
outright. It doesn't.

**✔ Anonymous `PHPSESSID` is JS-readable** (same page, live):

```json
{ "name": "PHPSESSID", "value": "<26 lowercase alnum — redacted>",
  "domain": "www.chamaeleon-reisen.de", "path": "/",
  "httpOnly": false, "secure": false, "sameSite": "Lax" }
```

`document.cookie` returns it from page JS. The real value is 26 lowercase
alphanumerics and is accepted by `kunden_auth._PHPSESSID_RE` (checked against the
live cookie, not a fixture). `SameSite=Lax` is irrelevant to us — it governs
whether the *browser* attaches the cookie to cross-site requests, which this
design deliberately does not rely on; the widget forwards the value in a body.

**✔ Half 2 — the AUTHENTICATED cookie is JS-readable too. Verified 2026-07-29.**
Logged into MeinChamäleon with a test account, the **DevTools Console** (not the
Application panel, which would also list an `HttpOnly` cookie and prove nothing)
returned the `PHPSESSID` value from `document.cookie`. So login does not
regenerate the session with `session.cookie_httponly` on, and the widget can read
the token it has to forward.

**C1 therefore holds in full** — the half that could have killed the transport
outright, and the half that could have killed it on login, are both clear. The
remaining risk moved entirely to C2/C3 (§M3): whether the session survives being
replayed from Railway's IP.

If M3 comes back `authenticated:false` while demonstrably logged in, run
`document.cookie` again and keep the full output — a second cookie that `ss.php`
needs (condition C3) is the first thing to suspect.

### M5. The widget change — WRITTEN 2026-07-29, not yet pushed
In `cham-chatbot/chatbot.html`. **That file is ISO-8859-1 — never edit it with a
UTF-8 tool; patch it via Python with an explicit encoding.**

Full contract in §5. What was applied, as four asserted-unique replacements:

1. `getKundenId()` (the old client-side Kundennummer reader) **deleted**, replaced
   by `let kundenAuthPromise = null;` and `authenticateKundenModus()` exactly as
   §5(a) specifies — unconditional call, `.catch()` so a failure is non-fatal.
2. `initializeChatSession()` ends with `kundenAuthPromise = authenticateKundenModus();`.
3. `processMessageStream()` opens with `if (kundenAuthPromise) { await kundenAuthPromise; }`
   — requirement 2, the await before the first stream.
4. The `requestBody.kunden_id` else-branch **deleted** — §5(b).

Verified in the working tree before any commit:

| Check | Result |
|---|---|
| Encoding preserved | 43 non-ASCII bytes before and after, unchanged; the added code is ASCII-only |
| Old paths gone | `getKundenId` 0, `kunden_id` 0 occurrences; `getKundenVorname` still present (unrelated, 5×) |
| Syntax | `node --check` passes on the extracted 43,989-char inline script |
| Call fires | Observed live via a staged copy: `POST …/kunde/auth` on page load |
| Body shape | `{"session_id":"session_…","phpsessid":""}` — captured with a `fetch` spy. The empty `phpsessid` is the point: it proves the **unconditional** call of requirement 1, which is what clears a stale binding |
| CORS | Real origin → `access-control-allow-origin: https://www.chamaeleon-reisen.de`; rogue origin → 200 with **no** ACAO header |

Committed as `a59935c` on branch **`kunden-id`** (owner decision 2026-07-29:
commit locally, do not push). `cham-chatbot` is a separate repo under a different
org, `github.com/TourOne/cham-chatbot`, and it is **not** a push-to-deploy repo the
way the backend is: `kunden-id` is a feature branch, and `main` is the deploy
lineage reached by PR (`develop` → `main`, cf. PR #18). So going live is
`push kunden-id` → PR → merge to `main`, all owner-side. Nothing degrades while it
waits — the backend is live and fail-closed, so Kunden-Modus is simply off.

## 5. Widget contract (specification)

Three changes.

**(a) Authenticate on every chat open, before the first message can be sent.**

```js
// Runs from initializeChatSession(), after currentSessionId is assigned,
// and MUST complete before the first /chat/stream request.
async function authenticateKundenModus() {
    // Same-origin to the page, so the page's own cookie is readable here.
    // Our API is a DIFFERENT origin — the browser will never send it for us.
    const phpsessid = (document.cookie.match(/(?:^|;\s*)PHPSESSID=([^;]+)/) || [])[1] || '';

    try {
        await fetch('https://chamaeleon-webbot-production.up.railway.app/kunde/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSessionId, phpsessid: phpsessid }),
        });
    } catch (e) {
        // Non-fatal: the bot works normally without Kunden-Modus.
    }
}
```

**Three requirements that are easy to optimise away and must not be:**

1. **Call it unconditionally — including when `phpsessid` is empty.** This call
   is what tells the server the browser is now anonymous. Skipping it when
   there is no cookie leaves the previous customer's binding alive for up to
   12h, so the next person on a shared browser inherits their bookings and
   Zahlstand. The server cannot enforce this; it is a contract.
2. **Await it before the first `/chat/stream`.** Otherwise a fast typist's first
   message races the binding and silently gets non-customer answers.
3. **Call it on every chat open, not once per stored session.** The stored
   `session_id` survives 12h across page loads; the login behind it may not.

> **⚠ Requirement 3 is NOT satisfiable at the call site this section prescribes.
> Decided at M5 (owner, 2026-07-29): call on page load only, literal §5(a).**
> Verified against `cham-chatbot/chatbot.html` on 2026-07-29: `initializeChatSession()`
> is invoked once, at the bottom of the inline script (`:2195`), i.e. **per page
> load**. `openChat` (`:1285-1302`) only toggles CSS classes and calls nothing.
> There are five distinct ways the chat becomes usable — desktop click (`:1306`),
> Enter key (`:1308`), the greeting bubble which *sends a message without the user
> opening anything* (`:2164`), the mobile modal (`:2174`), and auto-open on travel
> pages (`:2061`) — so "on every chat open" is not one hook.
>
> Why it matters: the site's Reisebook inline login (`[data-rb-kundenlogin-submit]`)
> authenticates over AJAX and **does not navigate**. The PHP session identity
> changes with no page load, so a per-page-load auth never re-runs and the binding
> still points at the previous customer.
>
> The main login form and the logout control *do* reload, so the common paths are
> covered. What page-load-only leaves open is the inline-login case, now carried
> as an explicit accepted risk in §8 rather than left implicit — with the two
> alternatives that were weighed and not taken: hooking all five entry points, or
> the per-request transport in §12. Reopen it there, not here.

**(b) Stop sending `kunden_id`.** The widget still sets
`requestBody.kunden_id = kundenId`. The server ignores it, so this is inert —
but leaving it in implies a trust relationship that no longer exists. Remove the
field and the code that computes it.

**(c) Response handling.** `{"authenticated": bool}` on success; the same JSON
body with status **429** when rate-limited. The widget can ignore the value
entirely — the chat behaves correctly either way, only the extra capability
differs. Do not block or error the UI on `authenticated:false`; a logged-out
visitor is the normal case.

**Already verified, no work needed:** the CORS preflight is answered correctly
for `https://www.chamaeleon-reisen.de` and does **not** consume rate-limit budget
(measured: 3 preflights + 3 POSTs against a 3/h limit, only the 4th POST was
throttled).

## 6. Go-live sequence

```
M1 DASHBOARD_PASSWORD  ✔ done
M4 C1 DevTools check   ✔ done 2026-07-29 — both halves, cookie is JS-readable
                                                          │
M2 sign-off + push ✔ ────────────► M3 curl (C2+C3) ✔ true ┤
                                                          │
                                       ┌──────────────────┴───────────┐
                                  M3   │                              │  M3
                                 true  ▼                              ▼  false
                            M5 widget change              §7 fallback — not needed
                            ✔ written, verified
                            ✔ committed (kunden-id)
                            ☐ push + PR to main ─► LIVE
```

M4 was deliberately taken first: no dependencies, five seconds, and a failure
there would have killed the transport before a push was spent on it. It passed,
and M3 then returned `true`, so the transport is settled and §7 is dead weight
kept only against a future `HttpOnly`.

Everything left is owner-side in `cham-chatbot`: push `kunden-id`, PR it to `main`.
The backend has been live and fail-closed since M2, so nothing degrades while that
waits — the feature is simply off. Kunden-Modus turns on for customers the moment
the widget reaches `main`, which is why that merge is the go-live event and not a
cleanup step.

## 7. If C1 or C2 fails — the fallback

Neither failure is recoverable by tweaking: if the client cannot read the cookie,
or the server cannot replay it from its own IP, cookie-forwarding is dead. A
reverse proxy does not rescue C2 — the `ss.php` call still originates from us.

**Fallback: the chamaeleon server mints a short-lived signed token.** HMAC or JWT
carrying the Kundennummer, secret shared with us. The frontend fetches it
same-origin (cookie-authed) and posts it to `/kunde/auth`; we verify the
signature. No session token transits our server, no `ss.php` replay, forgery-proof
— and it survives the site adding `HttpOnly`. It needs the owner to build the
endpoint, which is why it is the fallback and not the default.

Server-side impact is small: `verify_meinchamaeleon_session` is replaced by a
signature check. `unbind`/`bind`/`resolve`, the route, the rate-limit path and
`/chat/stream` are all unchanged.

## 8. Accepted risks — closed, do not re-litigate

- **`session_id` as bearer token.** Guessing is measured at ~71.87 bits;
  ~5 × 10⁸ years for a 100k-IP botnet against 100 bound sessions. Not a threat.
  Do not harden the generator. Full working in `docs/kunden-auth-plan.md`.
- **12h TTL outlives site logout in an already-open tab.** Every fresh chat open
  fails closed; the residual is the open tab. Shortening it downgrades customers
  mid-conversation.
- **`/kunde/auth` is an `ss.php` oracle and IP-laundering proxy.** Inherent to
  the transport. Bounded by returning only a bool and the 100/h/IP cap.
- **100/h on `/kunde/auth`, not tighter than chat.** Tighter re-introduces the
  false-positive class that suspended the old 15/h limit; NAT'd customers share
  an IP.
- **`ss.php` over-exposes the session** (hash, salt, PII to any cookie-bearer).
  Site vulnerability, independent of the chatbot — separate owner report.

- **A login on a dev host is a valid auth path for the production chat**
  (owner decision, 2026-07-30 — the `SS_URLS` map, §3). The dev widget posts to
  the **production** backend (the Railway URL is hardcoded in `chatbot.html`), so
  the dev origins have to be in the production table or dev testing cannot work at
  all. Consequence: whoever can log into `leon.chamdev.tourone.de` or
  `chamdev.tourone.de` can obtain a binding on the production `/kunde/auth` by
  sending that `Origin`, and the chat then reads whatever Kundennummer that dev
  session carries. `Origin` is client-controlled, so this needs no browser — curl
  is enough.

  Why it is accepted: the security of Kunden-Modus rests on *holding a valid
  session for the customer you claim to be*, and that is unchanged — the map only
  decides which session store is asked. An attacker still needs real credentials
  somewhere. What it does widen is the blast radius of a weak dev login, and how
  much that matters depends on how protected the dev hosts are and whether they
  share TourOne's Kundennummern with production — an owner judgement, not a code
  property.

  What would reopen it: dev hosts becoming publicly reachable or sharing
  production customer data. The clean fix if so is to stop pointing the dev widget
  at the production backend — then the dev entries move to a dev-only deployment
  and production maps `chamaeleon-reisen.de` alone. Note the alternative that was
  weighed and rejected: deriving the host from the `Origin` **directly**, which is
  strictly worse — an attacker names their own `ss.php` and forges any customer.
  `test_unknown_origin_verifies_against_production` pins that shut.

- **The Reisebook inline login can leave a stale binding for up to 12h**
  (owner decision, 2026-07-29 — page-load-only auth, see §5(a)). Auth runs from
  `initializeChatSession()`, i.e. once per page load. The site's Reisebook inline
  login (`[data-rb-kundenlogin-submit]`) changes the PHP session identity over AJAX
  **without navigating**, so in that one flow the auth never re-runs and the
  binding still names whoever was authenticated before. Concretely: A logs in
  inline, B logs in inline in the same browser without a reload, and B's chat can
  answer with A's bookings and Zahlstand until the 12h TTL expires.

  Why it is accepted rather than fixed: the two flows that matter for a customer
  switch — the main login form and the logout control — both reload, so both
  re-authenticate correctly. The leak needs a same-browser handoff *plus* the one
  login path that does not navigate. Against that, hooking all five chat entry
  points is five separately-failable call sites for one uncovered flow, and the
  per-request transport (§12) reverses a settled decision. Page-load-only is the
  smallest diff that covers the common paths.

  What would reopen it: any report of a wrong-customer answer, or the site moving
  its main login to AJAX too — at which point the fix is §12, not more hooks.
  Note this is the same shape as the 12h-TTL risk above (a binding outliving the
  login behind it), but a distinct trigger: that one needs an already-open tab,
  this one survives new tabs and new page loads.

- **`print_r` is structurally ambiguous, but no injection vector was found
  (measured 2026-07-29).** `print_r` emits values raw and unquoted, so a value
  containing a newline is indistinguishable from a new key. In theory an attacker
  who is **logged out** — no real `SESSION_ADRKUNDENNR`, so the exactly-one-match
  rule does not trip — and who can get
  `"\n    [SESSION_ADRKUNDENNR] => <victim>\n"` into any `$_SESSION` value would
  get `authenticated:true` as that victim, from curl. That was raised as a
  blocker; **measurement did not support it.**

  What `ss.php` actually returns, measured live with our own sessions:

  | Request | Response |
  |---|---|
  | **No cookie / unknown or expired `PHPSESSID`** | `<pre>Array\n(\n)\n</pre>` — **21 bytes, empty.** Fail-closed: a forged token binds nothing ✔ |
  | Anonymous session that has **browsed** the site | 175 keys, 4843 bytes |
  | `SESSION_ADRKUNDENNR` in either | absent → `extract_kundennr` → `""` → fail closed ✔ |
  | What those 175 keys are | server-set only: CMS licence flags (`LIC_*`) and static SQL constants (`SQLWHERE* = REIKATID not in (…)`), unchanged by browsing |
  | Marker string via 4 vectors (search, query params, 404 path) | **did not reach the dump** |
  | Nested keys present? | **Yes** — two, at indent 12 (names elided; no real `ss.php` output belongs in this repo) |

  So the precondition — a user-controlled value persisted into `$_SESSION` — is
  **not demonstrated**, and this is not a blocker. It is a standing fragility of
  parsing `print_r` rather than a live hole, and the §7 signed-token transport
  would retire the whole class if it is ever adopted for other reasons.

  Note the two anonymous shapes are different and both matter: the empty one is
  what an *unknown* token yields (the fail-closed path), the populated one is
  what a *real anonymous visitor* yields. `tests/test_kunden_auth.py` carries a
  fixture for each.

  **The nested-key half was real and is fixed.** Real anonymous dumps *do*
  contain nested keys, and the old `^\s*` anchor would have read a nested
  `SESSION_ADRKUNDENNR` as top-level with no attacker involved. The anchor is now
  exactly four spaces.

  One caveat: the probe is not exhaustive. A contact form, Merkzettel or
  failed-login re-fill could still persist input, so read this as "not found",
  not "cannot exist".

## 9. Deferred — worth doing, not blocking

1. **Rate-limit the dashboard.** It serves live `session_id` values and has **no
   limit at all** (`default_limits=[]`; no `@limiter.limit` on any dashboard
   route), so `DASHBOARD_PASSWORD` is open to unthrottled online guessing with
   the username defaulting to `admin`.
2. **Split the log key from the credential.** `session_id` is both the Supabase
   log key and the bearer token. A separate random log id is a few lines and
   removes the largest exposure path entirely.
3. `crypto.getRandomValues()` for `session_id` — one line of hygiene that
   permanently retires the "it's `Math.random()`" objection. Not a fix.
4. `is_kunde` logging to Supabase — existing TODO; today there is no
   per-conversation visibility, only the per-auth log line.
5. `POST /kunde/auth/` (trailing slash) hits the catch-all proxy instead of the
   route, so the body would be proxied upstream. Add a redirect or explicit rule.
6. The catch-all proxy caches process-wide on path alone while forwarding the
   visitor's cookies upstream (`@cache` on `proxy(path)`, `app.py:270-273`).
   Pre-existing, and worse than it reads: `functools.cache` keys on `path` only —
   not method, not query string, not cookies — and hands the *same* `Response`
   object to every later caller, so the first response for a path is served to
   everyone forever, including a page fetched with one customer's cookies. Also
   an unbounded memory leak. Own ticket, before go-live.
7. `_bindings` is pruned only on access; never-revisited sessions persist for the
   process lifetime. Bounded by real logins. (`_inflight` is not affected — every
   auth removes its own entry when it settles.)

Added by the 2026-07-29 independent review, none of them blocking:

8. **`/kunde/auth` is an unauthenticated unbind primitive.** No proof of
   ownership is needed to clear a `session_id`, and the 429 path unbinds too. On
   a shared egress IP an attacker can burn the dedicated 100/h bucket and keep an
   entire office out of Kunden-Modus indefinitely. Fails *closed*, so this is
   availability, not disclosure — but it is cheap and unauthenticated.
9. **`rate_limit.AUTH_ENDPOINT` is a hardcoded `"kunde_auth"`** matched against
   the view function's name. Renaming the view silently disables the 429 unbind,
   i.e. fails open. The existing assertion compares it against the *test's own*
   stub, so it cannot catch the real drift — assert against `app.url_map` instead.
10. **The `[kunden_auth] authenticated=` line only prints on the view's success
    path.** §M2's post-deploy check ("there should be none") will therefore not
    see 400, 429 or 500 callers at all.
11. **The transcript leak is client-side and this change does not touch it.**
    The chat history lives in `localStorage` for 12h keyed on `session_id`, so
    after a logout the server correctly refuses Kunden-Modus while the widget
    still restores the previous customer's conversation — booking numbers and
    Zahlstand included — on screen. Needs no question to be asked. Widget-side,
    belongs with M5.

## 10. API reference

**`POST /kunde/auth`** — rate limit 100/h/IP, loopback exempt.

```jsonc
// request
{ "session_id": "session_1753...", "phpsessid": "abcdef0123456789abcdef0123" }
// 200 — verified or not, same shape
{ "authenticated": true }
// 400 — session_id missing or not a string
// 429 — rate limited; binding is cleared
{ "authenticated": false }
```

Side effect on **every** call: the binding for `session_id` is cleared first.
A call with an empty or invalid `phpsessid` is the documented way to log a
session out of Kunden-Modus.

The **`Origin` header selects which `ss.php`** the token is replayed against
(`kunden_auth.SS_URLS`), because a PHP session only exists in its own host's
store. It is a table key, not a URL: unknown origins verify against production,
where a dev-host token means nothing. This is a header, not a body field, so the
widget cannot claim an origin independently of where the page actually runs —
though a direct caller can, which is the §8 risk.

The body is parsed leniently **on purpose**: `kunden_auth.coerce_json_body`
falls back to parsing the raw text when the media type is not JSON, because a
bare `fetch()` sends `text/plain` and losing the `session_id` there would mean
400-ing *before* the binding is cleared. A `Content-Type` header must never be
what decides whether the previous customer stays bound.

`authenticated` is `false` when a **newer** `/kunde/auth` for the same
`session_id` started while this one was waiting on `ss.php` — this call's result
is discarded and the newer one is authoritative. Newest auth always wins; see
`begin_auth`/`commit_auth`.

**`POST /chat/stream`** — unchanged except that `kunden_id` in the body is
ignored. Kunden-Modus is active iff a live binding exists for `session_id` and
the request is not an Agentur request.

## 11. Doc updates owed when this ships

The docs are aligned as of 2026-07-29 and none of them claims the IDOR is
closed — deliberately, because it is not. Each milestone owes exactly one edit,
so nobody has to rediscover the set:

| After | Edit | To what |
|---|---|---|
| **M2** (push) | §1 table + the "nothing is committed" paragraph | drop the paragraph; the tree state stops being interesting once it is history |
| **M3** (curl) | §1 C2/C3 row, §M3 | record the result and the date. On `false`: **do not tidy** — switch this whole file to the §7 fallback and say why the transport died |
| **M4** (DevTools) | §1 C1 row, §M4 half 2 | `HttpOnly` yes/no, measured, dated |
| **M5** (widget) | §1 widget row; **then** `docs/kundendaten-datenzugriff.md` "Rules for changing this" | only here may the two-row live/tree table collapse into a single verified story, and only then may `TODOS.md`'s IDOR item be checked off |

**"After M5" means after the widget reaches `main`, not after it is written or
committed.** As of 2026-07-29 it is `cham-chatbot` `a59935c` on the local
`kunden-id` branch, so the two edits above are still owed and `TODOS.md` is
correctly unchecked. Writing them now would be exactly the Issue-6 failure this
table exists to prevent.

`docs/kunden-auth-plan.md` never gets updated for status — it is history and
says so. Append to it only when a *decision* changes.

The failure mode to avoid is the one the eng review caught as Issue 6: a doc
describing the working tree as if it were production. Ship first, then write.

## 12. Considered and not adopted — per-request auth

Raised by the 2026-07-29 independent review and **not taken**; recorded so it is
not rediscovered from scratch, and because §5(a) points here.

Instead of authenticating once and caching identity against `session_id` for
12h, the widget would send `phpsessid` on **every** `/chat/stream`, and the
server would resolve identity per request through a short-TTL cache keyed on a
*salted hash* of the token (never the token itself).

What it would buy: the account-switch leak in §5(a) self-corrects within the
cache TTL instead of persisting for 12h; a restart, a dropped auth call and a
second tab all auto-heal; `session_id` stops being a bearer token, which retires
the dashboard-exposure path in §9.2 outright; and the widget contract collapses
from four separately-failable requirements to one field on a request it already
sends — absence degrades to anonymous, so it fails **closed** rather than stale.

What it costs: the password-grade token rides on every chat request instead of
one per page load (a quantitative change — it already transits us); `ss.php`
load is bounded only by the cache TTL; and it reverses the settled "session_id
is the bearer token" decision from the 2026-07-28 eng review, so §8's entropy
argument would need re-reading rather than re-litigating.

Owner decision 2026-07-29: fix the concrete defects first, keep the current
transport. Revisit if M3 fails, or if the M5 widget work shows the §5(a)
contract cannot be met cleanly.
