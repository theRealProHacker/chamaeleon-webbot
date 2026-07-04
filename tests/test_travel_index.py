"""Tests for the TourOne travel index (travel_index.py).

Pure logic (slugify / canonical / derive / termine markdown) is tested directly.
The matcher is tested against the REAL in-memory sitemap (agent_base.trip_sites),
so it verifies the derivation actually resolves known trips. Network calls are
mocked — no live TourOne requests here.
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import requests

import travel_index as ti


# --- slugify -----------------------------------------------------------------


def test_slugify_words_and_umlauts():
    assert ti.slugify("Tempel und Tiger") == "Tempel-und-Tiger"
    assert ti.slugify("Ägypten") == "Aegypten"
    assert ti.slugify("Krüger") == "Krueger"
    assert ti.slugify("1001 Nacht") == "1001-Nacht"


def test_slugify_strips_accents_and_edges():
    assert ti.slugify("  Machu Picchu  ") == "Machu-Picchu"
    assert ti.slugify("Café del Mar") == "Cafe-del-Mar"


# --- canonical suffix stripping ---------------------------------------------


def test_canon_strips_variant_suffixes():
    assert ti._canon("/Asien/Nepal/Tempel-und-Tiger-2024") == "/Asien/Nepal/Tempel-und-Tiger"
    assert ti._canon("/Afrika/Aegypten/Nofretete-ALL") == "/Afrika/Aegypten/Nofretete"
    assert ti._canon("/Asien/Japan/Kyoto-NEU") == "/Asien/Japan/Kyoto"
    assert ti._canon("/Asien/Japan/Kyoto-alt") == "/Asien/Japan/Kyoto"
    # bare (hyphenless) year suffix from the seo field, and -ALLG masters
    assert ti._canon("/Asien/Nepal/Lumbini26") == "/Asien/Nepal/Lumbini"
    assert ti._canon("/Amerika/Peru/Machu-Picchu-2025") == "/Amerika/Peru/Machu-Picchu"
    assert ti._canon("/Asien/Mongolei/Gobi-ALLG") == "/Asien/Mongolei/Gobi"


def test_canon_preserves_real_slugs():
    # Real multi-word slugs must not be mangled.
    assert ti._canon("/Afrika/Botswana-Suedafrika/Big-Five") == "/Afrika/Botswana-Suedafrika/Big-Five"
    assert ti._canon("/Asien/Oman/1001-Nacht") == "/Asien/Oman/1001-Nacht"
    assert ti._canon("/Amerika/Kuba/Wann-wenn-nicht-jetzt") == "/Amerika/Kuba/Wann-wenn-nicht-jetzt"


# --- derive_url --------------------------------------------------------------


def test_derive_url_happy():
    travel = {"land2": {"seo": "Asien/Nepal"}, "titel": "Lumbini"}
    assert ti.derive_url(travel) == "/Asien/Nepal/Lumbini"


def test_derive_url_missing_pieces():
    assert ti.derive_url({"titel": "x"}) is None
    assert ti.derive_url({"land2": {"seo": "Asien/Nepal"}}) is None
    assert ti.derive_url({"land2": {}, "titel": "x"}) is None


# --- matcher against the REAL sitemap ---------------------------------------


def _travel(code, titel, landseo, seo=None, termine=None, land="Nepal"):
    return {
        "code": code,
        "titel": titel,
        "seo": seo,
        "land2": {"seo": landseo, "bezeichnung": land},
        "kategorie": {"lang": "de"},
        "berater": {},
        "termine": termine or [],
    }


def test_candidate_urls_prefers_seo_then_title():
    t = _travel("X", "Jökulsárlón", "Europa/Island", seo="Jokulsarlon-2024")
    cands = ti.candidate_urls(t)
    assert cands[0] == "/Europa/Island/Jokulsarlon-2024"  # seo first
    assert "/Europa/Island/Joekulsarlon" in cands  # title slug fallback
    # no land2.seo -> no candidates
    assert ti.candidate_urls({"titel": "x", "seo": "y"}) == []


def test_build_index_exact_match():
    # /Asien/Nepal/Lumbini exists verbatim in the sitemap.
    index, _names, summary = ti._build_index(
        [_travel("NPLUM", "Lumbini", "Asien/Nepal")], check_live=False
    )
    assert "/Asien/Nepal/Lumbini" in index
    assert "NPLUM" in index["/Asien/Nepal/Lumbini"]["codes"]
    assert summary["matched_urls"] >= 1


def test_build_index_uses_seo_field_when_title_slug_differs():
    # Title slug is 'Joekulsarlon' (ö->oe) which is NOT in the sitemap; the seo
    # field 'Jokulsarlon-2024' (ö->o) is. The seo candidate must win.
    t = _travel("ISJOK", "Jökulsárlón", "Europa/Island", seo="Jokulsarlon-2024")
    index, _n, _s = ti._build_index([t], check_live=False)
    matched = [u for u in index if u.startswith("/Europa/Island/Jokulsarlon")]
    assert matched, "seo field should resolve trips the title slug misses"
    for u in matched:
        assert "ISJOK" in index[u]["codes"]


def test_build_index_canonical_variant_match():
    # Sitemap only has /Asien/Nepal/Tempel-und-Tiger-2024 and -ALL; the title
    # derives to the base, which must resolve via canonical matching.
    index, _n, _s = ti._build_index(
        [_travel("NPYAN", "Tempel und Tiger", "Asien/Nepal")], check_live=False
    )
    matched = [u for u in index if u.startswith("/Asien/Nepal/Tempel-und-Tiger")]
    assert matched, "canonical matching should map the base title to suffixed sitemap URLs"
    for u in matched:
        assert "NPYAN" in index[u]["codes"]


def test_build_index_one_url_many_codes():
    # Two travels with the same title/land collapse onto the same URL(s).
    travels = [_travel("A1", "Lumbini", "Asien/Nepal"), _travel("A2", "Lumbini", "Asien/Nepal")]
    index, _n, _s = ti._build_index(travels, check_live=False)
    codes = index["/Asien/Nepal/Lumbini"]["codes"]
    assert "A1" in codes and "A2" in codes


def test_build_index_unmatched_is_not_indexed():
    # A derived URL that is not in the sitemap must never be indexed.
    index, _n, summary = ti._build_index(
        [_travel("ZZZ", "Definitely Not A Real Trip", "Asien/Nepal")], check_live=False
    )
    assert "/Asien/Nepal/Definitely-Not-A-Real-Trip" not in index
    assert any("ZZZ" in u for u in summary["unmatched"])


def test_build_index_live_adds_page_missing_from_sitemap(monkeypatch):
    # A travel WITH termine whose page is not in the sitemap but returns 200
    # gets added; one whose page 404s does not.
    calls = {"/Amerika/Bolivien/Uyuni": True, "/Amerika/Bolivien/Ghost": False}
    monkeypatch.setattr(ti, "_page_exists", lambda path, **k: calls.get(path, False))
    travels = [
        _travel("BOUYU", "Uyuni", "Amerika/Bolivien", seo="Uyuni", termine=[{"von": "2026-01-01"}]),
        _travel("GHOST", "Ghost", "Amerika/Bolivien", seo="Ghost", termine=[{"von": "2026-01-01"}]),
    ]
    index, _n, summary = ti._build_index(travels, check_live=True)
    assert "/Amerika/Bolivien/Uyuni" in index and "BOUYU" in index["/Amerika/Bolivien/Uyuni"]["codes"]
    assert "/Amerika/Bolivien/Ghost" not in index
    assert summary["live_added"] == 1


def test_build_index_live_skipped_without_termine(monkeypatch):
    # No termine -> never a 200-check candidate, even if the page would 200.
    monkeypatch.setattr(ti, "_page_exists", lambda path, **k: True)
    index, _n, summary = ti._build_index(
        [_travel("NOTM", "Uyuni", "Amerika/Bolivien", seo="Uyuni")], check_live=True
    )
    assert "/Amerika/Bolivien/Uyuni" not in index
    assert summary["live_added"] == 0


# --- termine markdown --------------------------------------------------------


def test_format_termine_markdown():
    termine = [
        {"von": "2026-09-30 00:00:00", "bis": "2026-10-15 00:00:00", "abPreis": 4899.0},
        {"von": "2026-08-01 00:00:00", "bis": "2026-08-16 00:00:00", "abPreis": 4699.0},
    ]
    md = ti.format_termine_markdown(termine)
    assert "Nächste Termine" in md
    # sorted ascending: August date comes first
    assert md.index("2026-08-01") < md.index("2026-09-30")
    assert "4699" in md


def test_format_termine_markdown_empty():
    assert ti.format_termine_markdown([]) == ""
    assert ti.format_termine_markdown([{"bis": "x"}]) == ""  # no 'von'


def test_format_termine_markdown_limit():
    termine = [{"von": f"2026-0{i}-01 00:00:00", "bis": "x", "abPreis": 100} for i in range(1, 8)]
    md = ti.format_termine_markdown(termine, limit=3)
    bullets = [ln for ln in md.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 3 and md.startswith("## Nächste Termine")


# --- get_termine (mocked network) -------------------------------------------


def test_get_termine_success(monkeypatch):
    ti.get_termine.cache_clear()
    page = {"0": {"code": "XCODE", "termine": [{"von": "2026-01-01", "bis": "2026-01-10"}]}, "anzahl": 1}
    monkeypatch.setattr(ti, "_tourone_get", lambda *a, **k: page)
    out = ti.get_termine("XCODE")
    assert isinstance(out, tuple) and out[0]["von"] == "2026-01-01"


def test_get_termine_fail_open(monkeypatch):
    ti.get_termine.cache_clear()

    def boom(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(ti, "_tourone_get", boom)
    assert ti.get_termine("ERRCODE") == ()  # fails open, no exception


def test_get_termine_markdown_merges_and_dedupes(monkeypatch):
    monkeypatch.setattr(ti, "get_reisecodes", lambda url: ["A", "B"])
    termine_by_code = {
        "A": ({"von": "2026-05-01", "bis": "2026-05-10", "abPreis": 100},),
        "B": (
            {"von": "2026-05-01", "bis": "2026-05-10", "abPreis": 100},  # dup of A
            {"von": "2026-06-01", "bis": "2026-06-10", "abPreis": 200},
        ),
    }
    monkeypatch.setattr(ti, "get_termine", lambda code: termine_by_code[code])
    md = ti.get_termine_markdown("/Asien/Nepal/Lumbini")
    bullets = [ln for ln in md.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 2  # 3 termine, one duplicate deduped away
    assert "2026-05-01" in md and "2026-06-01" in md


def test_get_termine_markdown_no_match(monkeypatch):
    monkeypatch.setattr(ti, "get_reisecodes", lambda url: [])
    assert ti.get_termine_markdown("/nope") == ""
