"""In-memory TourOne travel index + termine access.

Builds an in-memory index mapping each Chamäleon website trip URL to its
TourOne reisecode(s), so the website tool can inject current termine (dates /
prices) for the trip the user is looking at. Mirrors the shape of
``sitemap_sync.py``: module-level state, a ``rebuild()`` under a lock, a daily
scheduler, and a ``__main__`` dry run.

Mapping strategy (see the eng-review plan):
- Derive the website path from ``land2.seo`` + a slug of ``titel`` and keep it
  only if it is a real URL in the in-memory sitemap (``agent_base.all_sites``).
  A derived URL that is not in the sitemap is never indexed, so a bad guess can
  never surface wrong termine.
- Combos (``/Afrika/Botswana-Namibia/...``) and slug oddities that the
  derivation misses are fixed by a committed ``travel_overrides.json``
  (``{website_url: reisecode}``).
- One URL can map to several reisecodes (an ``-ALL`` master over sub-packages),
  so the index value is a LIST of codes.

Termine come from ``reiseliste`` (the dedicated ``saisonTermineListe`` endpoint
is 403 for this key). The daily build fetches all travels; ``get_termine()``
does a cheap per-trip refresh with a short TTL because availability can change
intra-day.

Run ``python travel_index.py`` for a live dry run (needs TOURONE_BEARER_TOKEN):
prints the derivation hit rate and the unmatched travels to seed overrides.
"""

import json
import os
import re
import threading
import unicodedata

import requests
from cachetools.func import ttl_cache

BASE_URL = "https://api.tourone.de"
OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "travel_overrides.json")

# reiseliste pagination page size and per-trip termine cache TTL (seconds).
# Termine availability can change intra-day, so the refresh TTL is short.
PAGE_LIMIT = 100
TERMINE_TTL = int(os.getenv("TOURONE_TERMINE_TTL", "900"))  # 15 min

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


@ttl_cache(maxsize=1024, ttl=TERMINE_TTL)
def get_termine(reisecode: str) -> tuple:
    """Fresh termine for a single reisecode (short-TTL cached).

    Fetches only that one travel (never all reise). Returns a tuple of termine
    dicts (tuple so ttl_cache can key/store it). Fails open: on any error the
    caller gets an empty tuple and simply shows no termine.
    """
    try:
        page = _tourone_get(
            "/get/reiseliste",
            {"reisecode[]": reisecode, "showtermine": "true"},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"[travel-index] termine fetch failed for {reisecode}: {e}")
        return ()
    travels = _travels_from_page(page)
    if not travels:
        return ()
    return tuple(travels[0].get("termine") or ())


def format_termine_markdown(termine, limit: int = 4) -> str:
    """A SHORT markdown summary of the next termine (not a full table).

    The system prompt enforces 2-4 sentence answers, so we keep this compact and
    let the model link to #termine for the full, live-bookable list.
    """
    upcoming = [t for t in termine if t.get("von")]
    upcoming.sort(key=lambda t: t.get("von", ""))
    if not upcoming:
        return ""
    lines = []
    for t in upcoming[:limit]:
        von = (t.get("von") or "")[:10]
        bis = (t.get("bis") or "")[:10]
        preis = t.get("abPreis")
        preis_s = f" ab {int(preis)} €" if isinstance(preis, (int, float)) else ""
        lines.append(f"- {von} bis {bis}{preis_s}")
    return "## Nächste Termine\n" + "\n".join(lines)


# --- Index -------------------------------------------------------------------

_lock = threading.Lock()          # guards the atomic index swap
_build_lock = threading.Lock()    # serialises lazy first-build so it runs once
_index: dict[str, dict] = {}  # url -> {"codes": [...], "titel", "land", "lang", "berater"}
_name_to_url: dict[str, str] = {}
_built = False
_last_summary: dict = {}


# Variant suffixes the website appends to a trip slug that the TourOne title
# does not carry: -ALL / -NEU / -ALT masters and trailing year/version tags
# (-2024, -23, -16NF). Stripped for matching only; the real URL is preserved.
_VARIANT_SUFFIX = re.compile(r"-(?:ALL|NEU|ALT|\d+[A-Za-z]*)$", re.IGNORECASE)


def _canon(url: str) -> str:
    """Canonical match key: strip a trailing variant suffix from the last segment."""
    head, _, last = url.rpartition("/")
    return f"{head}/{_VARIANT_SUFFIX.sub('', last)}"


def _build_index(travels: list[dict]) -> tuple[dict, dict, dict]:
    """Pure builder: returns (index, name_to_url, summary). No shared state.

    Matches a travel's derived path to real sitemap trip URLs by canonical key
    (suffix-stripped), so `Tempel und Tiger` maps to `…-2024` and `…-ALL`. One
    URL can collect several reisecodes (season/package variants) — that is the
    intended 1:N.
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

    for travel in travels:
        url = derive_url(travel)
        real_urls = canon_to_urls.get(_canon(url)) if url else None
        if real_urls:
            for real in real_urls:
                add(real, travel)
            derived += 1
        else:
            unmatched.append(f"{travel.get('code')} / {travel.get('titel')} -> {url}")

    # Apply overrides ({url: code} or {url: [codes]}). These win / add.
    for url, codes in overrides.items():
        code_list = codes if isinstance(codes, list) else [codes]
        for code in code_list:
            travel = by_code.get(code)
            if travel is not None:
                add(url, travel)
                overridden += 1

    total_trip_urls = len(agent_base.trip_sites)
    summary = {
        "total_travels": len(travels),
        "total_trip_urls": total_trip_urls,
        "matched_urls": len(index),
        "url_coverage_pct": round(100 * len(index) / total_trip_urls, 1) if total_trip_urls else 0,
        "derived_hits": derived,
        "override_hits": overridden,
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
    print(
        f"[travel-index] rebuilt: {summary['matched_urls']} urls, "
        f"{summary['override_hits']} via overrides, "
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
    """Short termine markdown for a trip URL, or '' if no match / no termine.

    Merges termine across every reisecode mapped to the URL (1:N master/subs),
    deduped by (von, bis). Fails open.
    """
    codes = get_reisecodes(url_path)
    if not codes:
        return ""
    seen: set[tuple] = set()
    merged: list[dict] = []
    for code in codes:
        for t in get_termine(code):
            key = (t.get("von"), t.get("bis"))
            if key not in seen:
                seen.add(key)
                merged.append(t)
    return format_termine_markdown(merged)


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
    print(f"travels: {summary['total_travels']}")
    print(
        f"trip urls covered: {summary['matched_urls']}/{summary['total_trip_urls']} "
        f"({summary['url_coverage_pct']}%)"
    )
    print(f"travels matched by derivation: {summary['derived_hits']}")
    print(f"travels unmatched: {len(summary['unmatched'])}")
    for u in summary["unmatched"]:
        print("  ?", u)
