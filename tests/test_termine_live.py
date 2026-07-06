"""Live drift canary: the injected termine pipeline vs the real website.

Drives a REAL browser over ~10 reference pages (termine are client-rendered;
the server HTML only carries the row template), extracts the site's full
(vakanz-filter-OFF) #termine list, and compares NORMALIZED ROW TUPLES — dates,
Tage, availability wording, EZ wording, price, VM marker — against the
pipeline's rows for the same URL. Semantics are compared, not raw markdown.

NEVER part of the default suite. Run rarely and manually:

    RUN_LIVE_TERMINE=1 pytest tests/test_termine_live.py -v

Needs TOURONE_BEARER_TOKEN (.env), network, and playwright with a chromium:

    pip install -r requirements-dev.txt && playwright install chromium

A failure means the site's wording/visibility rules drifted (or TourOne data
changed mid-run — re-run once before believing a drift). KNOWN skew: on a day
a reference trip departs, the pipeline keeps the von==today row (locked
D5/3A, owner-confirmed 2026-07-06) while the site already hides it by evening
— a one-row offset on that page is expected; re-run the next day. Reference pages were
selected 2026-07-05 from a feed scan: volume, variant twins, every hidden
status, RQ, VM season, an override-mapped URL, -ALL masters, and the one
dead-season page.
"""

import os
import re

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import travel_index as ti

RUN_LIVE = os.getenv("RUN_LIVE_TERMINE") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_LIVE, reason="live drift canary - set RUN_LIVE_TERMINE=1 to run"
)

# (url_path, what this page proves)
REFERENCE_PAGES = [
    ("/Afrika/Namibia-Suedafrika-Botswana/Limpopo", "volume: 57 rows"),
    ("/Afrika/Marokko/Marrakesch", "variant twins, ausgebucht+EZ-suppression, 1 Platz, VM"),
    ("/Asien/Mongolei/Gobi", "RQ row rendered, SPERRE_TEMP hidden, 11 codes"),
    ("/Afrika/Uganda/Gorilla", "J4Y hidden, VM season"),
    ("/Afrika/Botswana-Namibia/Sambesi", "twin + volume 108, 9 codes, slow list"),
    ("/Europa/Spanien/Alhambra-ALL", "-ALL master with a real termine list"),
    ("/Afrika/Suedafrika-Eswatini/Addo", "RQ x2 at volume 102, J4Y"),
    ("/Europa/Grossbritannien-Schottland/Schottland", "override-mapped URL"),
    ("/Ozeanien/Neuseeland/Queen-Charlotte", "override-repaired mapping (NZQUE+NZQUE_UR)"),
    ("/Amerika/Peru/Machu-Picchu-ALL", "-ALL master, VM-heavy, seo-suffix match"),
]

# Not every page qualifies as a reference (verified 2026-07-05):
# /Afrika/Marokko/Atlas-ALL is a stale sitemap URL (the site 404s it);
# /Afrika/Kapverden/Cabo-Verde-ALL renders a subpackage chooser instead of a
# termine list; /Europa/Albanien/Gjirokaster-NEU renders only the NEU season
# while its mapping (and even its sku attribute) carries the whole ALGJI
# family — season-page granularity is a mapping question, not display drift;
# /Asien/Aserbaidschan-Georgien-Armenien/Kaukasus has no #TERMINLISTE widget.
# No genuinely dead-season page exists right now (the one candidate,
# Queen-Charlotte, turned out to be a mapping gap) — the explicit empty state
# is covered synthetically in test_travel_index.py.

# Site row text is free-form; pull the semantic tokens out with regexes.
# The site renders von WITHOUT a year ("18.09. - 04.10.26"), the pipeline
# renders both with year (plan D15) — so von is compared as DD.MM. and bis as
# DD.MM.YY; the bis year + Tage pin the von year unambiguously.
_DATE_RANGE = re.compile(
    r"(\d{2}\.\d{2}\.(?:\d{2,4})?)\s*[–—-]\s*(\d{2}\.\d{2}\.\d{2,4})"
)
_TAGE = re.compile(r"\b(\d+) Tage\b")
_PLAETZE = re.compile(r"\d+ Plätze verfügbar|1 Platz verfügbar|ausgebucht")
_EZ = re.compile(r"\d+ Einzelzimmer verfügbar|Einzelzimmer auf Anfrage")
_PREIS = re.compile(r"(\d{1,3}(?:\.\d{3})*)\s*€")

# Visible-text walk per row: the <li> carries hidden template labels that
# pollute raw innerText, so only text inside visible elements counts.
_VISIBLE_ROWS_JS = """
() => Array.from(document.querySelectorAll('li.terminliste__termin'))
  .filter(li => li.offsetParent !== null)
  .map(li => {
    const parts = [];
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) { parts.push(node.textContent); return; }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const cs = getComputedStyle(node);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      for (const child of node.childNodes) walk(child);
    };
    walk(li);
    return parts.join(' ').replace(/\\s+/g, ' ').trim();
  })
"""


def _ddmm(d: str) -> str:
    """'18.09.26' / '18.09.' / '18.09.2026' -> '18.09.'"""
    dd, mm = d.split(".")[:2]
    return f"{dd}.{mm}."


def _ddmmyy(d: str) -> str:
    """'04.10.2026' -> '04.10.26' (idempotent for DD.MM.YY input)."""
    dd, mm, yy = d.split(".")[:3]
    return f"{dd}.{mm}.{yy[-2:]}"


def _normalize_site_row(text: str) -> tuple:
    dates = _DATE_RANGE.search(text)
    tage = _TAGE.search(text)
    plaetze = _PLAETZE.search(text)
    ez = _EZ.search(text)
    preis = _PREIS.search(text)
    return (
        _ddmm(dates.group(1)) if dates else None,
        _ddmmyy(dates.group(2)) if dates else None,
        f"{tage.group(1)} Tage" if tage else None,
        plaetze.group(0) if plaetze else None,
        ez.group(0) if ez else None,
        f"{preis.group(1)} €" if preis else None,
        "Jetzt vorausbuchen" in text,
    )


def _pipeline_rows(codes: list[str]) -> list[tuple]:
    """The pipeline's rows in table order, normalized to the same tuple shape.

    Uses the fetch+filter and the collapse+sort the real table uses. The
    TERMINE_CAP is deliberately NOT applied: the site shows every row and the
    cap is covered synthetically — comparing pre-cap tests filter semantics
    on the >100-row pages too.
    """
    rows = ti._collapse_and_sort(ti.get_termine(tuple(codes)))
    out = []
    for t in rows:
        von = t.get("von") or ""
        bis = t.get("bis") or ""
        out.append((
            _ddmm(ti._fmt_date(von)) if von else None,
            _ddmmyy(ti._fmt_date(bis)) if bis else None,
            ti._fmt_tage(von, bis, t.get("dauer")) or None,
            ti._fmt_plaetze(t.get("vakanzSync")) or None,
            ti._fmt_einzelzimmer(t.get("vakanzSync3"), t.get("vakanzSync")) or None,
            ti._fmt_preis(t.get("abPreis")) or None,
            bool(ti._fmt_hinweis(t.get("status"), t.get("vakanzSync"))),
        ))
    return out


def _first_diff(site: list, pipeline: list) -> str:
    for i, (s, p) in enumerate(zip(site, pipeline)):
        if s != p:
            return f"first mismatch at row {i}:\n  site:     {s}\n  pipeline: {p}"
    return f"row-count mismatch: site={len(site)} pipeline={len(pipeline)}"


@pytest.fixture(scope="session")
def live_index():
    """Full live index build once per run (real fetch + real 200-checks)."""
    summary = ti.rebuild()
    assert "error" not in summary, f"index build failed: {summary}"
    return summary


@pytest.fixture(scope="session")
def browser_page():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="pip install -r requirements-dev.txt"
    )
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.set_default_timeout(20_000)
        yield page
        browser.close()


_VISIBLE_COUNT_JS = """
() => Array.from(document.querySelectorAll('li.terminliste__termin'))
  .filter(li => li.offsetParent !== null).length
"""

# The termine list lives in a UIkit tab panel that only becomes visible once
# the Termine tab is active; navigating with the #termine fragment activates
# it. The vakanz toggle is a styled (invisible) checkbox — JS click required.
_UNCHECK_TOGGLE_JS = """
() => {
  const t = document.querySelector('#terminliste-vakanzfilter-toggle');
  if (t && t.checked) { t.click(); return true; }
  return false;
}
"""

_CLICK_TERMINE_TAB_JS = """
() => {
  const a = Array.from(document.querySelectorAll('a'))
    .find(a => (a.getAttribute('href') || '').endsWith('#termine'));
  if (a) a.click();
}
"""


def _site_rows(page, path: str) -> list[str]:
    """Visible #termine rows of the FULL list (vakanz filter toggled OFF)."""
    page.goto(ti.WEBSITE_URL + path + "#termine", wait_until="domcontentloaded")
    # Cookie banner (first navigation of the session): decline via /ablehnen/i.
    try:
        page.get_by_role(
            "button", name=re.compile("ablehnen", re.I)
        ).first.click(timeout=4_000)
    except Exception:
        pass
    # The list is fetched client-side and can take >20s on big trips (the
    # widget's payload is 2.25 MB / 342 rows for Limpopo's NALIM_ALL). Poll:
    # rows appeared -> go on; no #TERMINLISTE container at all -> not a
    # termine page; still loading after a minute -> reload once and retry.
    # 'empty' (no spinner, no rows) must persist ~10s before it counts: there
    # is a real gap between spinner removal and row injection while the big
    # payload parses, and before the widget's fetch even starts — sampling
    # that gap once misreads a healthy page as empty.
    state = "loading"
    for attempt in range(2):
        empty_streak = 0
        for _ in range(30):
            state = page.evaluate(
                """() => {
                  if (document.querySelectorAll('li.terminliste__termin').length)
                    return 'rows';
                  if (!document.querySelector('#TERMINLISTE')) return 'no-liste';
                  if (!document.querySelector('.terminliste__loading')) return 'empty';
                  return 'loading';
                }"""
            )
            if state in ("rows", "no-liste"):
                break
            empty_streak = empty_streak + 1 if state == "empty" else 0
            if empty_streak >= 5:
                break
            page.wait_for_timeout(2_000)
        if state != "loading" or attempt:
            break
        page.reload(wait_until="domcontentloaded")
    if state == "loading":
        return None  # site-side outage: the widget never finished loading
    if state != "rows":
        return []
    page.wait_for_timeout(1_500)  # let the client-side list settle
    if page.evaluate(_VISIBLE_COUNT_JS) == 0:
        # Fragment did not activate the tab (rare): click the tab link.
        page.evaluate(_CLICK_TERMINE_TAB_JS)
        page.wait_for_timeout(1_500)
    # Default filter hides ausgebucht rows; the owner wants the full list.
    if page.evaluate(_UNCHECK_TOGGLE_JS):
        page.wait_for_timeout(1_500)
    return page.evaluate(_VISIBLE_ROWS_JS)


@pytest.mark.parametrize(
    "path,reason", REFERENCE_PAGES, ids=[p.rsplit("/", 1)[-1] for p, _r in REFERENCE_PAGES]
)
def test_pipeline_matches_site(browser_page, live_index, path, reason):
    codes = ti.get_reisecodes(path)
    assert codes, f"{path} not in the index ({reason})"

    raw = _site_rows(browser_page, path)
    if raw is None:
        pytest.skip(f"{path}: site termine widget stuck loading — site-side "
                    "outage, not pipeline drift; re-run later")
    site = [_normalize_site_row(t) for t in raw]

    if not site:
        # Site shows nothing: the pipeline must emit the explicit empty state,
        # never invented rows and never a bare "".
        md = ti.get_termine_markdown(path)
        assert "Derzeit keine buchbaren Termine." in md, (
            f"{path}: site shows 0 rows but pipeline says:\n{md[:500]}"
        )
        return

    pipeline = _pipeline_rows(codes)
    # A pipeline row that omits Tage (feed dauer missing, owner-decided
    # fail-safe) cannot be verified against the site's value — mask the cell.
    site = [
        s[:2] + (None,) + s[3:]
        if i < len(pipeline) and pipeline[i][2] is None
        else s
        for i, s in enumerate(site)
    ]
    assert site == pipeline, f"{path} ({reason})\n{_first_diff(site, pipeline)}"
