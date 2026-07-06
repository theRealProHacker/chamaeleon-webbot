"""In-memory TourOne travel index + termine access.

Builds an in-memory index mapping each Chamäleon website trip URL to its
TourOne reisecode(s), so the website tool can inject current termine (dates /
prices) for the trip the user is looking at. Mirrors the shape of
``sitemap_sync.py``: module-level state, a ``rebuild()`` under a lock, a daily
scheduler, and a ``__main__`` dry run.

Mapping strategy (see the eng-review plans, 07-04 + 07-05/06):
- PRIMARY (authoritative): every live trip page embeds the ONE reisecode its
  own termine widget queries — the server-rendered ``data-terminliste``
  attribute. The site expands that code to itself plus the aktiv travels
  whose ``masterCode`` points at it; mirroring that expansion reproduces the
  page's termine list exactly (canary-verified), including season pages
  (Gjirokaster-NEU -> ALGJI_NEU only, never the whole family).
- FALLBACK: derive the website path from ``land2.seo`` + a slug of ``titel``
  and keep it only if it is a real URL in the in-memory sitemap
  (``agent_base.all_sites``). A derived URL that is not in the sitemap is
  never indexed, so a bad guess can never surface wrong termine.
- Remaining gaps (subpackage-chooser pages without the widget, slug oddities)
  are fixed by a committed ``travel_overrides.json`` (``{website_url:
  reisecode}``).
- One URL can map to several reisecodes (an ``-ALL`` master over sub-packages),
  so the index value is a LIST of codes.

Termine come from ``reiseliste`` (the dedicated ``saisonTermineListe`` endpoint
is 403 for this key). The daily build fetches all travels; ``get_termine()``
refreshes all codes of one trip URL with a SINGLE batched ``reisecode[]``
union call (short TTL — availability changes intra-day; no ``limit`` param:
the API default is unlimited, small limits truncate silently). The site's
visibility filter (status whitelist OK/VM/RQ, no past departures, Europe/
Berlin) runs INSIDE the cached fetch, so the cache stores filtered rows;
failures are never cached. ``get_termine_markdown()`` replicates the site's
full (vakanz-filter-OFF) #termine list as a compact markdown table with the
site's exact wording tokens — variant twins with identical dates are kept as
distinct rows (no (von, bis) dedupe; see the 2026-07-05 eng-review plan).

Run ``python travel_index.py`` for a live dry run (needs TOURONE_BEARER_TOKEN):
prints the derivation hit rate and the unmatched travels to seed overrides.
"""

import json
import os
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from cachetools.func import ttl_cache

BASE_URL = "https://api.tourone.de"
WEBSITE_URL = "https://www.chamaeleon-reisen.de"
_WEBSITE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
}
OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "travel_overrides.json")

# reiseliste pagination page size and termine cache TTL (seconds).
# Termine availability can change intra-day, so the refresh TTL is short.
PAGE_LIMIT = 100
TERMINE_TTL = int(os.getenv("TOURONE_TERMINE_TTL", "900"))  # 15 min
# Anomaly cap for the rendered termine table: if more rows survive filtering,
# render this many plus an explicit "… und N weitere Termine" marker — never
# truncate silently. (Largest real list observed: Limpopo, 57 rows.)
TERMINE_CAP = 100

_UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
}


def _token() -> str | None:
    # Read via agent_base so there is a single source of truth for the env var.
    import agent_base

    return agent_base.TOURONE_API_KEY


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}


def slugify(titel: str) -> str:
    """Turn a travel title into the website's URL slug form.

    The site uses ASCII title-case slugs with umlaut transliteration and
    hyphen-joined words, e.g. "Tempel und Tiger" -> "Tempel-und-Tiger",
    "Ägypten" -> "Aegypten".
    """
    text = titel.strip()
    for k, v in _UMLAUTS.items():
        text = text.replace(k, v)
    # Drop any remaining accents (é -> e) but keep ASCII letters/digits.
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text)
    return text.strip("-")


def derive_url(travel: dict) -> str | None:
    """Best-effort website path for a travel: /{land2.seo}/{slug(titel)}.

    Returns None when the pieces are missing. The caller validates the result
    against the real sitemap before trusting it.
    """
    land = (travel.get("land2") or {}).get("seo")
    titel = travel.get("titel")
    if not land or not titel:
        return None
    return "/" + land.strip("/") + "/" + slugify(titel)


def candidate_urls(travel: dict) -> list[str]:
    """Candidate website paths for a travel, best first.

    The `seo` field is the website's own slug with the site's transliteration
    (e.g. 'Jokulsarlon-2024', 'Machu-Picchu-2025') and is the authoritative
    source; the title slug is a fallback for travels whose seo is missing. Both
    are validated by the caller, so extra candidates only ever help.
    """
    land = (travel.get("land2") or {}).get("seo")
    if not land:
        return []
    base = "/" + land.strip("/")
    slugs: list[str] = []
    if travel.get("seo"):
        slugs.append(travel["seo"])
    if travel.get("titel"):
        s = slugify(travel["titel"])
        if s and s not in slugs:
            slugs.append(s)
    return [f"{base}/{s}" for s in slugs]


def _page_exists(path: str, timeout: int = 10) -> bool:
    """True only if the website serves a 200 for this path (following redirects).

    Strict on purpose: any error or non-200 returns False, so a page is only
    added to the index when it demonstrably exists (the opposite bias to
    sitemap_sync.is_alive, which is conservative about *removing* pages)."""
    url = WEBSITE_URL + path
    try:
        r = requests.head(
            url, headers=_WEBSITE_HEADERS, timeout=timeout, allow_redirects=True
        )
        if r.status_code in (403, 405, 501):  # some servers dislike HEAD
            r = requests.get(
                url, headers=_WEBSITE_HEADERS, timeout=timeout,
                allow_redirects=True, stream=True,
            )
            r.close()
        return r.status_code == 200
    except requests.RequestException:
        return False


def _page_widget_code(html: str) -> str | None:
    """The reisecode the page's own termine widget queries.

    Server-rendered ``data-terminliste="{&quot;reisecode&quot;: &quot;X&quot;}"``
    — the authoritative source for WHICH termine the site shows on that URL.
    Season pages carry their season's code, regular pages the family master.
    Subpackage-chooser pages and 404s have no attribute -> None.
    """
    # Server HTML single-quotes the attribute (clean JSON inside); a DOM
    # re-serialization double-quotes it with &quot; entities. Accept both.
    m = re.search(r'data-terminliste="([^"]*)"', html) or re.search(
        r"data-terminliste='([^']*)'", html
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1).replace("&quot;", '"'))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data.get("reisecode") or None


def _fetch_widget_code(path: str, timeout: int = 15) -> str | None:
    """Widget code for a website path, or None (non-200, no widget, error)."""
    try:
        r = requests.get(
            WEBSITE_URL + path, headers=_WEBSITE_HEADERS, timeout=timeout
        )
        if r.status_code != 200:
            return None
        return _page_widget_code(r.text)
    except requests.RequestException:
        return None


def _load_overrides() -> dict[str, str]:
    try:
        with open(OVERRIDES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # {url: code} or {url: [codes]} both accepted.
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[travel-index] could not read overrides: {e}")
        return {}


def _berater(travel: dict) -> dict[str, str]:
    b = travel.get("berater") or {}
    name = " ".join(p for p in (b.get("vorname"), b.get("nachname")) if p).strip()
    return {"name": name, "telefon": b.get("telefon") or "", "email": b.get("email") or ""}


# --- TourOne API access ------------------------------------------------------


def _tourone_get(path: str, params: dict, timeout: int = 20) -> object:
    """Authenticated GET against the TourOne API. Raises on HTTP error."""
    resp = requests.get(
        BASE_URL + path, headers=_headers(), params=params, timeout=timeout
    )
    resp.raise_for_status()
    return resp.json()


def _travels_from_page(page: object) -> list[dict]:
    """reiseliste returns {"0": {...}, ..., "anzahl", "gesamt"}."""
    if isinstance(page, dict):
        return [v for k, v in page.items() if k.isdigit() and isinstance(v, dict)]
    if isinstance(page, list):
        return [t for t in page if isinstance(t, dict)]
    return []


def fetch_all_travels(show_termine: bool = True) -> list[dict]:
    """Fetch every travel from reiseliste, following pagination."""
    travels: list[dict] = []
    offset = 0
    total = None
    while True:
        params = {
            "offset": offset,
            "limit": PAGE_LIMIT,
            "totalcount": "true",
            "ignoretermine": "true",
        }
        if show_termine:
            params["showtermine"] = "true"
        page = _tourone_get("/get/reiseliste", params)
        batch = _travels_from_page(page)
        if isinstance(page, dict) and total is None:
            total = page.get("gesamt")
        if not batch:
            break
        travels.extend(batch)
        offset += PAGE_LIMIT
        if total is not None and len(travels) >= int(total):
            break
    return travels


# Statuses rendered on the site's full (vakanz-filter-OFF) #termine list. RQ
# renders like a normal bookable row (observed live: Gobi future RQ, vak=7).
_STATUS_SHOWN = {"OK", "VM", "RQ"}
# Known-hidden statuses. Anything outside shown|hidden is hidden too, but
# logged loudly so a new bookable status cannot disappear silently.
_STATUS_HIDDEN = {"SPERRE", "SPERRE_TEMP", "J4Y"}
_unknown_statuses_logged: set = set()  # once per status per process/rebuild


def _today_berlin() -> str:
    """Today as YYYY-MM-DD in Europe/Berlin — the site's clock for termine."""
    import pytz

    return datetime.now(pytz.timezone("Europe/Berlin")).strftime("%Y-%m-%d")


def _termin_visible(termin: dict, today: str) -> bool:
    """Site visibility rule: known-shown status AND not departed yet.

    ``von == today`` stays visible; a missing ``von`` is dropped (a row we
    cannot place in time must not be shown as bookable).
    """
    von = (termin.get("von") or "")[:10]
    if not von or von < today:
        return False
    status = termin.get("status")
    if status in _STATUS_SHOWN:
        return True
    if status not in _STATUS_HIDDEN and status not in _unknown_statuses_logged:
        _unknown_statuses_logged.add(status)
        print(f"[travel-index] unknown termin status: {status}")
    return False


@ttl_cache(maxsize=256, ttl=TERMINE_TTL)
def _fetch_termine_filtered(codes: tuple) -> tuple:
    """ONE batched reiseliste call for a code tuple; returns FILTERED termine.

    Raises on any error so failures are never cached — the next call retries
    (a blip costs one answer, not 15 blank minutes). No ``limit`` param: the
    API default is unlimited; ``limit=0`` returns 0 rows and small limits
    truncate silently. The batch may return FEWER travels than codes (master
    codes like MAMAR_ALL yield nothing) — that is normal. Merge order is the
    code-tuple order (pinned), so the stable (von, bis) sort downstream
    reproduces the site's row order for variant twins.
    """
    page = _tourone_get(
        "/get/reiseliste",
        {"reisecode[]": list(codes), "showtermine": "true"},
        timeout=10,
    )
    by_code: dict = {}
    for t in _travels_from_page(page):
        by_code.setdefault(t.get("code"), t)
    today = _today_berlin()
    merged: list[dict] = []
    for code in codes:
        travel = by_code.get(code)
        if not travel:
            continue
        merged.extend(t for t in (travel.get("termine") or ()) if _termin_visible(t, today))
    return tuple(merged)


def get_termine(codes: tuple) -> tuple:
    """Visible termine for a tuple of reisecodes (batched, filtered, cached).

    Returns a tuple of termine dicts, already visibility-filtered and merged
    in code order. Fails open: on any error THIS call gets an empty tuple and
    the failure is not cached.
    """
    if not codes:
        return ()
    try:
        return _fetch_termine_filtered(tuple(codes))
    except Exception as e:
        print(f"[travel-index] termine fetch failed for {codes}: {e}")
        return ()


def _fmt_date(iso: str) -> str:
    """'2026-10-16 00:00:00' -> '16.10.26' (the site's date format)."""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%y")
    except ValueError:
        return iso[:10]


def _fmt_tage(von: str, bis: str, dauer) -> str:
    """Trip length in days INCLUSIVE of both ends: (bis - von) + 1.

    This is what the site displays; the feed's ``dauer`` field (nights, one
    less) is NOT the displayed value — but its ABSENCE marks a row whose
    length the feed cannot back (observed 2026-07-05: an OAP add-on termin
    spanning 21 days that the site labels 27 Tage). For those rows the cell
    is omitted rather than risk a false claim (owner decision 2026-07-06).
    """
    if dauer is None:
        return ""
    try:
        d_von = datetime.strptime(von[:10], "%Y-%m-%d")
        d_bis = datetime.strptime(bis[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{(d_bis - d_von).days + 1} Tage"


def _fmt_plaetze(gp) -> str:
    """vakanzSync -> the site's wording; '' when the field is missing/None
    (never guess availability)."""
    if not isinstance(gp, (int, float)):
        return ""
    gp = int(gp)
    if gp == 0:
        return "ausgebucht"
    if gp == 1:
        return "1 Platz verfügbar"
    return f"{gp} Plätze verfügbar"


def _fmt_einzelzimmer(ez, gp) -> str:
    """vakanzSync3 -> the site's wording. Suppressed entirely on sold-out rows
    (GP==0 shows plain 'ausgebucht' even when the feed still has EZ>0)."""
    if not isinstance(gp, (int, float)) or int(gp) == 0:
        return ""
    if not isinstance(ez, (int, float)):
        return ""
    ez = int(ez)
    if ez == 0:
        return "Einzelzimmer auf Anfrage"
    return f"{ez} Einzelzimmer verfügbar"


def _fmt_preis(preis) -> str:
    """abPreis in the site's German format: 4099 -> '4.099 €'."""
    if not isinstance(preis, (int, float)):
        return ""
    return f"{int(preis):,} €".replace(",", ".")


def _fmt_hinweis(status, gp) -> str:
    """VM rows carry the site's CTA token — except sold-out VM rows, which
    the site renders as plain 'ausgebucht' (observed live 2026-07-05 on
    Machu-Picchu 2027 rows)."""
    if status == "VM" and _fmt_plaetze(gp) != "ausgebucht":
        return "Jetzt vorausbuchen"
    return ""


def _collapse_and_sort(termine) -> list[dict]:
    """Exact-tuple collapse + stable (von, bis) sort — the table's row order.

    Also used by the live drift canary (tests/test_termine_live.py) so the
    site comparison sees exactly the rows the table would render.
    """
    rows: list[dict] = []
    seen: set[tuple] = set()
    for t in termine:
        key = (
            t.get("von"), t.get("bis"), t.get("status"),
            t.get("vakanzSync"), t.get("vakanzSync3"), t.get("abPreis"),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(t)
    rows.sort(key=lambda t: ((t.get("von") or "")[:10], (t.get("bis") or "")[:10]))
    return rows


def format_termine_markdown(termine) -> str:
    """The site's full (vakanz-filter-OFF) #termine list as a markdown table.

    One row per termin with the site's exact wording tokens. Rows arrive
    merged in code order; the stable (von, bis) sort keeps that order for
    variant twins sharing dates — twins are DISTINCT bookable variants and are
    both kept. Only rows identical in every displayed field are collapsed
    (safety net against master shells carrying copied termine). Returns ''
    for zero rows; the caller owns the explicit empty-state wording.
    """
    rows = _collapse_and_sort(termine)
    if not rows:
        return ""
    lines = [
        "## Termine",
        "",
        "| Zeitraum | Tage | Verfügbarkeit | Einzelzimmer | Preis | Hinweis |",
        "|---|---|---|---|---|---|",
    ]
    for t in rows[:TERMINE_CAP]:
        von = t.get("von") or ""
        bis = t.get("bis") or ""
        zeitraum = _fmt_date(von) + (f" – {_fmt_date(bis)}" if bis else "")
        hinweis = _fmt_hinweis(t.get("status"), t.get("vakanzSync"))
        lines.append(
            f"| {zeitraum} | {_fmt_tage(von, bis, t.get('dauer'))} "
            f"| {_fmt_plaetze(t.get('vakanzSync'))} "
            f"| {_fmt_einzelzimmer(t.get('vakanzSync3'), t.get('vakanzSync'))} "
            f"| {_fmt_preis(t.get('abPreis'))} | {hinweis} |"
        )
    if len(rows) > TERMINE_CAP:
        lines.append(f"… und {len(rows) - TERMINE_CAP} weitere Termine")
    return "\n".join(lines)


# --- Index -------------------------------------------------------------------

_lock = threading.Lock()          # guards the atomic index swap
_build_lock = threading.Lock()    # serialises lazy first-build so it runs once
_index: dict[str, dict] = {}  # url -> {"codes": [...], "titel", "land", "lang", "berater"}
_name_to_url: dict[str, str] = {}
_built = False
_last_summary: dict = {}


# Variant suffixes the website / seo field append to a trip slug: -ALL / -ALLG /
# -NEU / -ALT masters, and trailing year/version tags with or without a hyphen
# (-2024, Lumbini26, -16NF). Stripped for matching only; the real URL is kept.
_VARIANT_SUFFIX = re.compile(r"-(?:ALL|ALLG|NEU|ALT)$", re.IGNORECASE)
_VERSION_SUFFIX = re.compile(r"-?\d{2,4}[A-Za-z]{0,2}$")


def _canon(url: str) -> str:
    """Canonical match key: strip trailing variant/version suffixes from the last
    segment, looping so 'Machu-Picchu-2025' and 'Lumbini26' reduce to the base."""
    head, _, last = url.rpartition("/")
    prev = None
    while last != prev:
        prev = last
        last = _VARIANT_SUFFIX.sub("", last)
        last = _VERSION_SUFFIX.sub("", last)
    return f"{head}/{last}"


def _build_index(travels: list[dict], check_live: bool = True) -> tuple[dict, dict, dict]:
    """Build (index, name_to_url, summary) from the travel list.

    Matching, best first:
    1. Each travel yields candidate paths from its seo slug and its title slug
       (candidate_urls); a candidate is matched to real sitemap trip URLs by
       canonical key (suffix-stripped), so `Machu-Picchu-2025` maps to the
       sitemap's `…-2025`/`…-ALL`. One URL can collect several reisecodes
       (season/package variants) — the intended 1:N.
    2. A travel that has termine but no sitemap match is a real bookable trip
       missing from the sitemap: if `check_live`, its candidate URL is verified
       by HTTP 200 and added anyway.
    3. travel_overrides.json fills whatever is left.
    4. If `check_live`, the widget-code refinement then overrides 1-3 wherever
       a page's own ``data-terminliste`` code yields a usable expansion — the
       authoritative per-URL truth.

    `check_live=False` skips both network steps (used by tests).
    """
    import agent_base

    overrides = _load_overrides()

    # canonical trip-URL key -> real sitemap URLs sharing that base.
    canon_to_urls: dict[str, list[str]] = {}
    for url in agent_base.trip_sites:
        canon_to_urls.setdefault(_canon(url), []).append(url)

    index: dict[str, dict] = {}
    name_to_url: dict[str, str] = {}
    derived = overridden = 0
    unmatched: list[str] = []

    def add(url: str, travel: dict) -> None:
        entry = index.setdefault(
            url,
            {
                "codes": [],
                "titel": travel.get("titel"),
                "land": (travel.get("land2") or {}).get("bezeichnung"),
                "lang": (travel.get("kategorie") or {}).get("lang", "de"),
                "berater": _berater(travel),
            },
        )
        code = travel.get("code")
        if code and code not in entry["codes"]:
            entry["codes"].append(code)
        if travel.get("titel"):
            name_to_url.setdefault(travel["titel"].lower(), url)

    # Map override URL -> travel by code for a quick lookup.
    by_code = {t.get("code"): t for t in travels if t.get("code")}

    # Only index active travels. Retired ones (aktiv=0) have no termine and would
    # otherwise flood the unmatched list as false gaps (e.g. TZMIG "Migration").
    active = [t for t in travels if t.get("aktiv")]

    pending: list[tuple[dict, list[str]]] = []  # (travel, candidates) for 200-check
    for travel in active:
        cands = candidate_urls(travel)
        real_urls: list[str] = []
        for c in cands:
            real_urls += canon_to_urls.get(_canon(c), [])
        if real_urls:
            for real in dict.fromkeys(real_urls):  # dedupe, keep order
                add(real, travel)
            derived += 1
        elif travel.get("termine") and cands:
            pending.append((travel, cands))  # real page, maybe missing from sitemap
        else:
            unmatched.append(
                f"{travel.get('code')} / {travel.get('titel')} -> {cands[0] if cands else None}"
            )

    # 200 + has-termine fallback: verify candidate pages that are not in the
    # sitemap and add the ones that really exist.
    live_added = 0
    if check_live and pending:

        def _resolve(item: tuple[dict, list[str]]):
            travel, cands = item
            tried: list[str] = []
            for c in cands:
                for u in (_canon(c), c):  # prefer the clean base slug
                    if u not in tried:
                        tried.append(u)
            for u in tried:
                if _page_exists(u):
                    return travel, u
            return travel, None

        with ThreadPoolExecutor(max_workers=16) as ex:
            for travel, u in ex.map(_resolve, pending):
                if u:
                    add(u, travel)
                    live_added += 1
                else:
                    unmatched.append(f"{travel.get('code')} / {travel.get('titel')} (no 200)")
    else:
        for travel, cands in pending:  # network skipped: report as unmatched
            unmatched.append(f"{travel.get('code')} / {travel.get('titel')} (unchecked)")

    # Apply overrides ({url: code} or {url: [codes]}). These win / add.
    for url, codes in overrides.items():
        code_list = codes if isinstance(codes, list) else [codes]
        for code in code_list:
            travel = by_code.get(code)
            if travel is not None:
                add(url, travel)
                overridden += 1

    # Widget-code refinement (the PRIMARY mapping, see module docstring):
    # fetch every sitemap trip page and read the reisecode its termine widget
    # queries; expand it like the site does (the code itself if aktiv, plus
    # aktiv travels whose masterCode points at it). Where the expansion holds
    # any termine it REPLACES the derived/override codes for that URL — and
    # maps URLs derivation could not reach at all. Pages without a usable
    # widget (choosers, 404s, empty expansions) keep the mapping from above.
    widget_refined = widget_added = 0
    if check_live:
        children_by_master: dict[str, list[dict]] = {}
        for t in travels:
            if t.get("aktiv") and t.get("code") and t.get("masterCode"):
                children_by_master.setdefault(t["masterCode"], []).append(t)

        def _widget_family(path: str) -> tuple[str, list[dict] | None]:
            w = _fetch_widget_code(path)
            if not w:
                return path, None
            w_travel = by_code.get(w)
            fam = [w_travel] if (w_travel and w_travel.get("aktiv")) else []
            fam += [t for t in children_by_master.get(w, ()) if t is not w_travel]
            if not any(t.get("termine") for t in fam):
                return path, None  # widget shows nothing usable: keep base
            return path, fam

        with ThreadPoolExecutor(max_workers=16) as ex:
            for path, fam in ex.map(_widget_family, agent_base.trip_sites):
                if not fam:
                    continue
                fam_codes = [t["code"] for t in fam]
                entry = index.get(path)
                if entry is None:
                    for t in fam:
                        add(path, t)
                    widget_added += 1
                elif entry["codes"] != fam_codes:
                    entry["codes"] = fam_codes
                    widget_refined += 1

    total_trip_urls = len(agent_base.trip_sites)
    summary = {
        "total_travels": len(travels),
        "active_travels": len(active),
        "total_trip_urls": total_trip_urls,
        "matched_urls": len(index),
        "url_coverage_pct": round(100 * len(index) / total_trip_urls, 1) if total_trip_urls else 0,
        "derived_hits": derived,
        "live_added": live_added,
        "override_hits": overridden,
        "widget_refined": widget_refined,
        "widget_added": widget_added,
        "unmatched": unmatched,
    }
    return index, name_to_url, summary


def rebuild() -> dict:
    """Fetch all travels and atomically swap in a fresh index. Returns summary.

    On a fetch failure the current index is left untouched (like sitemap_sync).
    """
    global _index, _name_to_url, _built, _last_summary
    try:
        travels = fetch_all_travels()
    except Exception as e:  # network / API failure: keep the old index
        print(f"[travel-index] rebuild fetch failed, keeping current index: {e}")
        return {"error": str(e)}

    new_index, new_names, summary = _build_index(travels)
    with _lock:
        _index = new_index  # atomic reassignment, never in-place mutation
        _name_to_url = new_names
        _built = True
        _last_summary = summary
    _unknown_statuses_logged.clear()  # re-arm the once-per-status warning
    print(
        f"[travel-index] rebuilt: {summary['matched_urls']} urls, "
        f"{summary['override_hits']} via overrides, "
        f"{summary['widget_refined']} widget-refined, "
        f"{summary['widget_added']} widget-added, "
        f"{len(summary['unmatched'])} unmatched"
    )
    return summary


def ensure_built() -> None:
    """Lazy build on first use so a request before the startup build still works.

    Serialised so concurrent first requests trigger a single build, not several.
    """
    if _built:
        return
    with _build_lock:
        if not _built:
            rebuild()


def get_reisecodes(url_path: str) -> list[str]:
    """Reisecode(s) for a website trip URL, or [] if unknown."""
    ensure_built()
    path = url_path.split("#")[0].split("?")[0].rstrip("/") or "/"
    entry = _index.get(path)
    return list(entry["codes"]) if entry else []


def get_termine_markdown(url_path: str) -> str:
    """Termine table for a trip URL; '' only when the URL is not indexed.

    ONE batched fetch for all of the URL's reisecodes (1:N master/subs).
    Codes matched but zero visible rows -> an explicit "keine buchbaren
    Termine" line, so the model cannot hallucinate dates on dead-season
    pages. A fetch ERROR also returns '' (skipping the section is honest;
    claiming "keine Termine" during an API blip would be a false statement)
    — the failure is never cached, the next call retries.
    """
    codes = get_reisecodes(url_path)
    if not codes:
        return ""
    try:
        rows = _fetch_termine_filtered(tuple(codes))
    except Exception as e:
        print(f"[travel-index] termine fetch failed for {codes}: {e}")
        return ""
    md = format_termine_markdown(rows)
    if md:
        return md
    path = url_path.split("#")[0].split("?")[0].rstrip("/") or "/"
    return (
        "## Termine\n\nDerzeit keine buchbaren Termine. "
        f"(Aktuelle Termine: {WEBSITE_URL}{path}#termine)"
    )


def last_summary() -> dict:
    return dict(_last_summary)


# --- Scheduling / warmup -----------------------------------------------------

_scheduler = None


def warm_async() -> None:
    """Build the index in a background thread so process startup is not blocked."""
    threading.Thread(target=rebuild, name="travel-index-warm", daemon=True).start()


def rebuild_async() -> None:
    """Trigger a rebuild off the request thread (used by the dashboard button)."""
    threading.Thread(target=rebuild, name="travel-index-rebuild", daemon=True).start()


def start_scheduler():
    """Daily 03:00 Europe/Berlin rebuild. Idempotent per process.

    Mirrors sitemap_sync.start_scheduler (offset one hour so the two daily jobs
    do not fire at the same minute).
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    import pytz
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(timezone=pytz.timezone("Europe/Berlin"))
    _scheduler.add_job(
        rebuild,
        "cron",
        hour=3,
        minute=0,
        id="travel-index",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    print("[travel-index] scheduler started - daily at 03:00 Europe/Berlin")
    return _scheduler


if __name__ == "__main__":
    # Live dry run: build the index and report the derivation hit rate and the
    # unmatched travels (to seed travel_overrides.json). No scheduler, no server.
    travels = fetch_all_travels()
    _, _, summary = _build_index(travels)
    print(f"travels: {summary['total_travels']} ({summary['active_travels']} active)")
    print(
        f"urls in index: {summary['matched_urls']} "
        f"(sitemap-derived {summary['derived_hits']}, live-added {summary['live_added']}, "
        f"widget-refined {summary['widget_refined']}, widget-added {summary['widget_added']}); "
        f"sitemap trip urls: {summary['total_trip_urls']}"
    )
    print(f"travels unmatched: {len(summary['unmatched'])}")
    for u in summary["unmatched"]:
        print("  ?", u)
