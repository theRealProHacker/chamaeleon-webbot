# Chamaeleon Reisen Chatbot Proxy

Flask backend for the Gemini-powered chatbot on `chamaeleon-reisen.de`. It serves
the chat API, proxies the site, keeps a travel/termine index in sync, and hosts
the stats dashboard. The chat **widget itself is not in this repo** — it is an
inline script in the site's own pages (`cham-chatbot/chatbot.html`, owner-side,
ISO-8859-1).

Deployed on Railway with a single worker (`WEB_CONCURRENCY=1` — rate-limit and
auth state are in-process). **Pushing to `main` deploys.**

## Quick Start

1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/).
2. Create `.env` (see below). The app **refuses to boot** without the four
   required values.
3. `pip install -r requirements.txt`
4. `python app.py` → http://localhost:5000

Requests from `127.0.0.1` are exempt from rate limiting, so local dev never hits
the cap.

### Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | **yes** — `ValueError` at import (`agent_base.py`) | the model |
| `SUPABASE_URL` / `SUPABASE_KEY` | **yes** — assert at import (`db_logging.py`) | chat logging, dashboard, sitemap versions |
| `LOGGING_PASSWORD` | **yes** — `RuntimeError` at import (`dashboard.py`) | dashboard basic-auth; no default on purpose, see below |
| `LOGGING_USERNAME` | no (`admin`) | dashboard basic-auth user |
| `TOURONE_BEARER_TOKEN` | no, but warns | TourOne API: termine index and Kunden-Modus bookings |
| `DEBUG` | no (`false`) | verbose logs, incl. the `[tool_call]` line |

`LOGGING_PASSWORD` has **no fallback and that is deliberate.** The dashboard
serves live `session_id` values, and `session_id` is the Kunden-Modus bearer
token — a forgotten variable used to mean `"change-me"` guarded a customer's
bookings and Zahlstand. Boot fails loudly instead. Any new environment (staging,
a second service, a bare local run) needs it set.

## Layout

| Path | What |
| --- | --- |
| `app.py` | Flask app: `/chat/stream` (SSE), `/kunde/auth`, dashboard/admin routes, site catch-all proxy |
| `agent.py` / `agent_base.py` | LangGraph agent, tools, system prompt |
| `kundendaten.py` | TourOne customer data: `buchungen_tool` (closure-bound), field whitelist |
| `kunden_auth.py` | Kunden-Modus auth: verify `ss.php` session → bind Kundennummer to `session_id` |
| `rate_limit.py` | flask-limiter wiring, per-endpoint rejection rendering |
| `db_logging.py` | Supabase chat logging |
| `travel_index.py`, `sitemap_sync.py`, `sitemap_store.py` | trip/termine index and sitemap |
| `dashboard.py`, `static/dashboard`, `static/admin` | stats dashboard and admin UI |
| `faqs/` | knowledge base fed into the prompt |
| `docs/` | see below |
| `tests/` | pytest; live-only suites are opt-in via env flags |

## Docs

- **`docs/kunden-auth-spec.md`** — Kunden-Modus auth. **Authoritative**: status,
  remaining work and owners, widget contract, go-live order, accepted risks.
  Read this before touching `kunden_auth.py`, `/kunde/auth` or the widget.
- `docs/kunden-auth-plan.md` — history and rationale for the above (why v1
  failed, review findings, the entropy measurement). Not instructions.
- **`docs/agentur-modus-plan.md`** — Agentur-Modus (verified Reiseprofi identity
  + agency booking data). **Authoritative** for that feature: measured TourOne
  agency API contract, whitelist, structural guarantees, milestones, open
  questions. The session key was found on 2026-07-31 (`SESSION_AGTNR`); the
  server half M1–M4 is implemented (`agentur_auth.py`, `agenturdaten.py`,
  `POST /agentur/auth`). **The transport is proven end to end for the agt hosts**
  — C1(agt) 2026-08-03 (the agt `PHPSESSID` is readable from `document.cookie`)
  and C2(agt) 2026-08-04 (an agt session replays from Railway's egress) — so the
  §7 signed-token fallback is not needed. Not live: the widget half (M5) sits on
  `cham-chatbot` PR #24. Before go-live, the plan's §9.4 privacy question is the
  remaining blocker: if independent mobile Reiseberater*innen share one
  Agenturnummer, each sees the others' bookings, and no code change can fix it.
  See §12 and §10 M5/M6.
- `docs/kundendaten-datenzugriff.md` — what customer data the TourOne API
  exposes, what we use, and exactly what crosses into a Gemini request. The
  boundary to protect when changing `kundendaten.py`.
- `docs/explore_kunde.py` — regenerates that field list (redacts values).
- `TODOS.md` — backlog.

## Tests

```bash
python -m pytest tests/test_general.py tests/test_kunden_auth.py \
  tests/test_kundendaten.py tests/test_previews.py tests/test_streaming.py -q
```

That is the non-live suite (70 tests). The remaining files need network,
credentials or playwright and are gated behind env flags
(`RUN_LIVE_TERMINE`, `RUN_AGENTUR_EVAL`, `RUN_MEINCHAMAELEON_EVAL`).

Note: `import app` still performs live Supabase reads at import time, so those
suites need real credentials — see the test-isolation item in `TODOS.md`.

**Never commit real `ss.php` output or real customer records.** A v1 commit
leaked a customer's password hash and salt; fixtures are fabricated only.
