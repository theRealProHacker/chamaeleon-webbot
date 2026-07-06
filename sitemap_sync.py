"""Daily in-memory sitemap sync, persisted to Supabase.

Fetches the live HTML sitemap from chamaeleon-reisen.de, diffs it against the
in-memory sitemap the bot uses, and merges the result back into memory so the
bot immediately knows about new pages. Versions that changed something are
appended to Supabase (``sitemap_store``), the newest persisted version is
restored at server startup (``restore_from_db``), and /admin offers a
curation textarea whose saves go through ``apply_human_edit``.

Rules:
- Canonicalisation strips a trailing "-ALL" for comparison only. Existing URL
  forms are left untouched.
- Additions (live URLs not in the current set) are merged in, labelled by
  continent (Reiseziele) or an auto-added section.
- A baseline URL missing from the live sitemap is NOT dropped on that basis
  alone: it is HEAD-checked, and only removed if it no longer serves 200.
- The diff is logged to stdout (Railway logs).

Run `python sitemap_sync.py` for a dry-run diff against sitemap.txt (no
in-memory mutation, no imports of the agent).
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.chamaeleon-reisen.de"
LIVE_SITEMAP_PATH = "/Sitemap"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
}
CONTINENTS = ("Afrika", "Amerika", "Asien", "Europa", "Ozeanien")
AUTO_SECTION = "## Automatisch ergänzt (Sitemap-Sync)"

_ALL_SUFFIX = re.compile(r"-ALL$", re.IGNORECASE)


def canonical(path: str) -> str:
    """Canonical comparison key: drop a trailing '-ALL' from the last segment."""
    return _ALL_SUFFIX.sub("", path)


def _normalize(href: str) -> str | None:
    """Normalise an href to an internal path, or None if it is not one."""
    href = href.strip()
    if href.startswith(BASE_URL):
        href = href[len(BASE_URL):]
    href = href.split("?")[0].split("#")[0]
    if not href.startswith("/") or href.startswith("//"):
        return None
    if href != "/" and href.endswith("/"):
        href = href.rstrip("/")
    return href or None


def fetch_live_sitemap(timeout: int = 20) -> set[str]:
    """Fetch and parse the live HTML sitemap into a set of internal paths.

    The page is served as ISO-8859-1 (like the proxy in app.py). URL paths are
    ASCII, so only the hrefs matter.
    """
    resp = requests.get(
        BASE_URL + LIVE_SITEMAP_PATH, headers=_HEADERS, timeout=timeout
    )
    resp.raise_for_status()
    html = resp.content.decode("ISO-8859-1")
    soup = BeautifulSoup(html, "html.parser")
    paths: set[str] = set()
    for a in soup.find_all("a", href=True):
        path = _normalize(a["href"])
        if path and path != "/":
            paths.add(path)
    return paths


def static_paths(sitemap_text: str) -> list[str]:
    """The URL lines of a sitemap text (non-empty, non-header)."""
    return [
        line.strip()
        for line in sitemap_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def compute_diff(live: set[str], static: list[str]) -> tuple[list[str], list[str]]:
    """Return (additions, would_be_removals) by canonical key.

    additions        - live URLs whose canonical key is not in the baseline
    would_be_removals - baseline URLs whose canonical key is not live
    """
    live_canon = {canonical(p) for p in live}
    static_canon = {canonical(p) for p in static}
    additions = sorted(p for p in live if canonical(p) not in static_canon)
    would_remove = sorted(p for p in static if canonical(p) not in live_canon)
    return additions, would_remove


def is_alive(path: str, timeout: int = 10) -> bool:
    """True if path serves a 200 (following redirects).

    Conservative: any network error returns True, so a transient failure never
    drops a page.
    """
    url = BASE_URL + path
    try:
        r = requests.head(
            url, headers=_HEADERS, timeout=timeout, allow_redirects=True
        )
        if r.status_code in (403, 405, 501):  # some servers dislike HEAD
            r = requests.get(
                url,
                headers=_HEADERS,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
            )
            r.close()
        return r.status_code == 200
    except requests.RequestException:
        return True


def _continent_of(path: str) -> str | None:
    segs = path.strip("/").split("/")
    return segs[0] if segs and segs[0] in CONTINENTS else None


def _reiseziele_bounds(lines: list[str]) -> tuple[int | None, int | None]:
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "## Reiseziele":
            start = i
        elif s == "## Nachhaltigkeit" and start is not None:
            end = i
            break
    return start, end


def _subsection_insert_index(lines: list[str], continent: str) -> int:
    """Index at which to insert a URL under '### <continent>' inside the
    Reiseziele region, creating the region/subsection if needed. May mutate
    ``lines`` (to add a missing header)."""
    start, end = _reiseziele_bounds(lines)
    if start is None:
        lines.extend(["", "## Reiseziele", f"### {continent}"])
        return len(lines)
    region_end = end if end is not None else len(lines)
    header = f"### {continent}"
    for i in range(start, region_end):
        if lines[i].strip() == header:
            j = i + 1
            while j < region_end and not lines[j].strip().startswith("#"):
                j += 1
            return j
    # subsection not present: create it just before the region end
    lines.insert(region_end, header)
    return region_end + 1


def merge_text(sitemap_text: str, additions: list[str], dead: list[str]) -> str:
    """Return a new sitemap text with dead lines removed and additions inserted
    under their continent (or an auto-added section for non-continent URLs)."""
    dead_set = set(dead)
    lines = [ln for ln in sitemap_text.splitlines() if ln.strip() not in dead_set]

    by_continent: dict[str, list[str]] = {}
    misc: list[str] = []
    for path in additions:
        cont = _continent_of(path)
        if cont:
            by_continent.setdefault(cont, []).append(path)
        else:
            misc.append(path)

    for cont, paths in by_continent.items():
        idx = _subsection_insert_index(lines, cont)
        for p in reversed(paths):
            lines.insert(idx, p)

    if misc:
        if AUTO_SECTION not in (ln.strip() for ln in lines):
            lines.extend(["", AUTO_SECTION])
        lines.extend(misc)

    return "\n".join(lines) + "\n"


def _log_summary(summary: dict) -> None:
    print(
        f"[sitemap-sync] added={len(summary['added'])} "
        f"dropped_404={len(summary['dropped_404'])} "
        f"kept_despite_absent={len(summary['kept_despite_absent'])}"
    )
    for p in summary["added"]:
        print(f"[sitemap-sync]   + {p}")
    for p in summary["dropped_404"]:
        print(f"[sitemap-sync]   - {p}  (no longer 200)")


def _check_removals(would_remove: list[str]) -> tuple[list[str], list[str]]:
    """Split would-be removals into (dead, kept) by live status code."""
    if not would_remove:
        return [], []
    with ThreadPoolExecutor(max_workers=16) as ex:
        checked = list(ex.map(lambda p: (p, is_alive(p)), would_remove))
    dead = [p for p, alive in checked if not alive]
    kept = [p for p, alive in checked if alive]
    return dead, kept


_lock = threading.Lock()


def sync(verbose: bool = True) -> dict:
    """Fetch the live sitemap, merge additions and drop dead pages, all in the
    bot's in-memory sitemap. Returns a summary dict."""
    import agent
    import agent_base

    with _lock:
        current_text = agent_base.sitemap
        static = static_paths(current_text)
        try:
            live = fetch_live_sitemap()
        except Exception as e:  # network/parse failure: leave the sitemap as-is
            print(f"[sitemap-sync] fetch failed, keeping current sitemap: {e}")
            return {"error": str(e)}

        additions, would_remove = compute_diff(live, static)
        dead, kept = _check_removals(would_remove)

        new_text = merge_text(current_text, additions, dead)
        new_desc = agent_base.apply_sitemap(new_text)
        try:
            agent.chamaeleon_website_tool.description = new_desc
        except Exception as e:
            print(f"[sitemap-sync] could not update tool description: {e}")

        summary = {"added": additions, "dropped_404": dead, "kept_despite_absent": kept}
        # Persist only versions that changed something — a no-change day would
        # add a full-text row for nothing. Fail-open: persistence never blocks
        # or fails the sync itself.
        if additions or dead:
            try:
                import sitemap_store

                sitemap_store.save_version(new_text, "sync", summary)
            except Exception as e:
                print(f"[sitemap-sync] persist failed: {e}")
        if verbose:
            _log_summary(summary)
        return summary


def restore_from_db() -> bool:
    """Load the newest persisted sitemap (incl. human edits) into memory.

    Called once at server startup so curation survives restarts/deploys; the
    daily sync keeps evolving the restored text from there. Fail-open: no
    row, missing table, or Supabase being down keeps the sitemap.txt baseline.
    """
    import agent
    import agent_base

    try:
        import sitemap_store

        latest = sitemap_store.load_latest()
    except Exception as e:
        print(f"[sitemap-sync] restore failed: {e}")
        return False
    text = (latest or {}).get("sitemap_text") or ""
    if not text.strip():
        return False
    with _lock:
        if text == agent_base.sitemap:
            return False
        new_desc = agent_base.apply_sitemap(text)
        try:
            agent.chamaeleon_website_tool.description = new_desc
        except Exception as e:
            print(f"[sitemap-sync] could not update tool description: {e}")
    print(
        f"[sitemap-sync] restored persisted sitemap "
        f"({latest.get('source')}, {latest.get('created_at')})"
    )
    return True


def apply_human_edit(new_text: str) -> dict:
    """Validate, apply, and persist an admin-edited sitemap text.

    Guard rails: an accidental empty/truncated paste must never nuke the
    bot's world — require a sane URL count and at least one trip URL (the
    Reiseziele section feeds the travel index and termine display).
    """
    import agent
    import agent_base

    paths = static_paths(new_text)
    bad = [p for p in paths if not p.startswith("/")]
    if bad:
        return {"error": f"Zeilen ohne führenden '/': {bad[:3]}"}
    if len(paths) < 20:
        return {"error": f"nur {len(paths)} URLs — abgelehnt (Schutz gegen versehentliches Leeren)"}
    parsed_sites, parsed_trips, _countries = agent_base._parse_sitemap(new_text)
    if not parsed_trips:
        return {"error": "keine Reise-URLs unter '## Reiseziele' — abgelehnt"}

    with _lock:
        new_desc = agent_base.apply_sitemap(new_text)
        try:
            agent.chamaeleon_website_tool.description = new_desc
        except Exception as e:
            print(f"[sitemap-sync] could not update tool description: {e}")

    try:
        import sitemap_store

        persisted = sitemap_store.save_version(new_text, "human")
    except Exception as e:
        print(f"[sitemap-sync] persist failed: {e}")
        persisted = False
    return {
        "applied": True,
        "persisted": persisted,
        "paths": len(parsed_sites),
        "trip_paths": len(parsed_trips),
    }


_scheduler = None


def start_scheduler():
    """Start the daily 02:00 Europe/Berlin sync. Idempotent per process."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    import pytz
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(timezone=pytz.timezone("Europe/Berlin"))
    _scheduler.add_job(
        sync,
        "cron",
        hour=2,
        minute=0,
        id="sitemap-sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    print("[sitemap-sync] scheduler started - daily at 02:00 Europe/Berlin")
    return _scheduler


if __name__ == "__main__":
    # Dry run against sitemap.txt: show the diff, no in-memory mutation.
    with open("sitemap.txt", encoding="utf-8") as f:
        static_text = f.read()
    static = static_paths(static_text)
    live = fetch_live_sitemap()
    additions, would_remove = compute_diff(live, static)
    dead, kept = _check_removals(would_remove)
    print(f"live paths: {len(live)}   static paths: {len(static)}")
    print(f"\nadditions ({len(additions)}):")
    for p in additions:
        print("  +", p)
    print(
        f"\nwould-remove: {len(would_remove)}  ->  gone/404: {len(dead)}  "
        f"kept (still 200): {len(kept)}"
    )
    for p in dead:
        print("  -", p, "(gone)")
