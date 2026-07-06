"""Tests for the TourOne travel index (travel_index.py).

Pure logic (slugify / canonical / derive / termine markdown) is tested directly.
The matcher is tested against the REAL in-memory sitemap (agent_base.trip_sites),
so it verifies the derivation actually resolves known trips. Network calls are
mocked — no live TourOne requests here.
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

from datetime import datetime, timedelta

import pytest
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


def _travel(code, titel, landseo, seo=None, termine=None, land="Nepal", aktiv=1):
    return {
        "code": code,
        "titel": titel,
        "seo": seo,
        "aktiv": aktiv,
        "land2": {"seo": landseo, "bezeichnung": land},
        "kategorie": {"lang": "de"},
        "berater": {},
        "termine": termine or [],
    }


def test_build_index_skips_inactive_travel():
    # A retired travel (aktiv=0) must not be indexed even if its URL would match.
    index, _n, summary = ti._build_index(
        [_travel("TZMIG", "Lumbini", "Asien/Nepal", aktiv=0)], check_live=False
    )
    assert "/Asien/Nepal/Lumbini" not in index
    assert summary["active_travels"] == 0


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


# --- berater ------------------------------------------------------------------


def test_get_berater_from_index(monkeypatch):
    # get_berater peeks without building (never blocks a chat reply)
    monkeypatch.setattr(ti, "_index", {
        "/Asien/Nepal/Lumbini": {
            "codes": ["NPLUM"],
            "berater": {"name": "Maxi Muster", "telefon": "+49 30 1234", "email": "m@x.de"},
        }
    })
    assert ti.get_berater("/Asien/Nepal/Lumbini#termine")["name"] == "Maxi Muster"
    assert ti.get_berater("/Asien/Nepal/Lumbini/")["telefon"] == "+49 30 1234"
    assert ti.get_berater("/unbekannt") == {}


def test_system_prompt_berater_fallback(monkeypatch):
    import agent_base

    monkeypatch.setattr(
        ti, "get_berater",
        lambda url: {"name": "Maxi Muster", "telefon": "+49 30 1234", "email": ""},
    )
    md = agent_base.format_system_prompt("/Asien/Nepal/Lumbini", [])
    assert "Maxi Muster" in md and "+49 30 1234" in md
    # page-supplied values always win over the index
    md = agent_base.format_system_prompt("/x", [], "Explizit E.", "+49 40 9999")
    assert "Explizit E." in md and "+49 40 9999" in md
    assert "Maxi Muster" not in md
    # partial: page passes only the name -> telefon still filled from the index
    md = agent_base.format_system_prompt("/x", [], "Explizit E.", "")
    assert "Explizit E." in md and "+49 30 1234" in md
    assert "Maxi Muster" not in md


def test_system_prompt_survives_berater_error(monkeypatch):
    import agent_base

    def boom(url):
        raise RuntimeError("index down")

    monkeypatch.setattr(ti, "get_berater", boom)
    md = agent_base.format_system_prompt("/x", [])
    assert "Der Kunde befindet sich" in md  # prompt still assembles


# --- widget-code refinement ---------------------------------------------------


def test_page_widget_code_parsing():
    # the server-rendered form (single-quoted attribute, clean JSON)
    html = "<ul class=\"list uk-list\"\n data-terminliste='{\"reisecode\": \"MAMAR_ALL\"}'\n data-texte='{}'>"
    assert ti._page_widget_code(html) == "MAMAR_ALL"
    # the DOM re-serialized form (double quotes, &quot; entities)
    html = '<ul data-terminliste="{&quot;reisecode&quot;: &quot;NALIM_ALL&quot;}">'
    assert ti._page_widget_code(html) == "NALIM_ALL"
    # valueless attrs like data-terminliste-filter must not match
    assert ti._page_widget_code("<div data-terminliste-filter uk-grid>") is None
    assert ti._page_widget_code("<html>no widget</html>") is None
    assert ti._page_widget_code('<ul data-terminliste="not json">') is None
    assert ti._page_widget_code('<ul data-terminliste="{}">') is None


def _master_family():
    """Master M_ALL (aktiv, no termine) + two aktiv children + one retired."""
    termin = {"von": "2099-01-01 00:00:00"}
    master = _travel("M_ALL", "Lumbini", "Asien/Nepal", seo="Lumbini")
    child_a = _travel("M_A", "Lumbini A", "Asien/Nepal", termine=[termin])
    child_b = _travel("M_B", "Lumbini B", "Asien/Nepal", termine=[dict(termin)])
    retired = _travel("M_OLD", "Lumbini Old", "Asien/Nepal", termine=[dict(termin)], aktiv=0)
    for t in (child_a, child_b, retired):
        t["masterCode"] = "M_ALL"
    return [master, child_a, child_b, retired]


def test_widget_refinement_replaces_codes(monkeypatch):
    # The page's widget code wins over derivation: master + aktiv children,
    # in feed order; retired children excluded.
    monkeypatch.setattr(ti, "_page_exists", lambda *a, **k: False)
    monkeypatch.setattr(
        ti, "_fetch_widget_code",
        lambda path, **k: "M_ALL" if path == "/Asien/Nepal/Lumbini" else None,
    )
    index, _n, summary = ti._build_index(_master_family(), check_live=True)
    assert index["/Asien/Nepal/Lumbini"]["codes"] == ["M_ALL", "M_A", "M_B"]
    assert summary["widget_refined"] == 1


def test_widget_refinement_maps_underivable_url(monkeypatch):
    # A URL derivation cannot reach (title/seo do not match) still gets mapped
    # when its page carries a widget code (the Gjirokaster-NEU case).
    travels = _master_family()
    for t in travels:  # break derivation entirely
        t["seo"] = None
        t["titel"] = "Nicht Ableitbar " + t["code"]
    monkeypatch.setattr(ti, "_page_exists", lambda *a, **k: False)
    monkeypatch.setattr(
        ti, "_fetch_widget_code",
        lambda path, **k: "M_A" if path == "/Asien/Nepal/Lumbini" else None,
    )
    index, _n, summary = ti._build_index(travels, check_live=True)
    assert index["/Asien/Nepal/Lumbini"]["codes"] == ["M_A"]  # season page: no family
    assert summary["widget_added"] == 1


def test_widget_refinement_keeps_base_when_expansion_empty(monkeypatch):
    # Widget code with no aktiv travels / no termine (Cabo-Verde-ALL case):
    # the derived/override mapping stays.
    travels = _master_family()
    travels[0]["aktiv"] = 0  # master retired; children keep master=M_ALL but aktiv filter...
    for t in travels[1:3]:
        t["aktiv"] = 0  # no aktiv members at all
    monkeypatch.setattr(ti, "_page_exists", lambda *a, **k: False)
    monkeypatch.setattr(ti, "_fetch_widget_code", lambda path, **k: "M_ALL")
    index, _n, summary = ti._build_index(
        travels + [_travel("NPLUM", "Lumbini", "Asien/Nepal")], check_live=True
    )
    # base derivation for NPLUM survives untouched
    assert "NPLUM" in index["/Asien/Nepal/Lumbini"]["codes"]
    assert summary["widget_refined"] == 0 and summary["widget_added"] == 0


def test_widget_refinement_skipped_offline():
    index, _n, summary = ti._build_index(_master_family(), check_live=False)
    assert summary["widget_refined"] == 0 and summary["widget_added"] == 0


# --- termine: synthetic fixtures ---------------------------------------------
#
# Owner rule (eng review 2026-07-05, D9): SYNTHETIC fixtures only, dates
# computed relative to now so the tests never rot. No live-data copies.


def _d(days: int) -> str:
    """Feed-format date `days` from today (Europe/Berlin, the code's clock)."""
    base = datetime.strptime(ti._today_berlin(), "%Y-%m-%d")
    return (base + timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")


def _termin(von=30, tage=14, status="OK", gp=5, ez=2, preis=4099.0):
    """Minimal synthetic feed termin. von/tage in days relative to today."""
    return {
        "von": _d(von),
        "bis": _d(von + tage - 1),
        "status": status,
        "dauer": tage - 1,  # feed field = nights; the site displays Tage
        "vakanzSync": gp,
        "vakanzSync2": 0,
        "vakanzSync3": ez,
        "abPreis": preis,
    }


def _page(*travels):
    """reiseliste response shape: digit keys + counters."""
    page = {str(i): t for i, t in enumerate(travels)}
    page["anzahl"] = len(travels)
    return page


def _travel_t(code, *termine):
    return {"code": code, "termine": list(termine)}


def _table_rows(md: str) -> list[str]:
    """Data rows of the rendered markdown table (header/separator excluded)."""
    return [
        ln for ln in md.splitlines()
        if ln.startswith("| ") and not ln.startswith("| Zeitraum")
    ]


@pytest.fixture(autouse=True)
def _fresh_termine_state(monkeypatch):
    ti._fetch_termine_filtered.cache_clear()
    ti._unknown_statuses_logged.clear()
    # Unit tests never hit the website for widget codes; the refinement tests
    # override this stub explicitly.
    monkeypatch.setattr(ti, "_fetch_widget_code", lambda path, **k: None)
    yield


# --- get_termine: batched fetch + filter + cache (mocked network) -------------


def test_get_termine_batched_single_call_merges_across_travels(monkeypatch):
    calls = []

    def fake_get(path, params, **kw):
        calls.append((path, params))
        # API return order differs from code order; master code yields nothing.
        return _page(
            _travel_t("B", _termin(von=40)),
            _travel_t("A", _termin(von=10), _termin(von=20)),
        )

    monkeypatch.setattr(ti, "_tourone_get", fake_get)
    out = ti.get_termine(("A", "B", "MAMAR_ALL"))
    assert len(calls) == 1  # ONE union call, never per-code
    assert calls[0][1]["reisecode[]"] == ["A", "B", "MAMAR_ALL"]
    # fewer travels than codes is normal; merge order pinned to the code tuple
    assert [t["von"] for t in out] == [_d(10), _d(20), _d(40)]


def test_get_termine_empty_codes_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network must not be called for empty codes")

    monkeypatch.setattr(ti, "_tourone_get", boom)
    assert ti.get_termine(()) == ()


def test_get_termine_error_not_cached_next_call_retries(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.RequestException("boom")
        return _page(_travel_t("A", _termin()))

    monkeypatch.setattr(ti, "_tourone_get", flaky)
    assert ti.get_termine(("A",)) == ()  # fails open, no exception
    assert len(ti.get_termine(("A",))) == 1  # failure was NOT cached
    assert calls["n"] == 2


def test_get_termine_success_cached(monkeypatch):
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        return _page(_travel_t("A", _termin()))

    monkeypatch.setattr(ti, "_tourone_get", once)
    ti.get_termine(("A",))
    ti.get_termine(("A",))
    assert calls["n"] == 1  # tuple-keyed ttl_cache


def test_get_termine_travel_without_termine_key_skipped(monkeypatch):
    page = _page({"code": "A"}, _travel_t("B", _termin()))
    monkeypatch.setattr(ti, "_tourone_get", lambda *a, **k: page)
    assert len(ti.get_termine(("A", "B"))) == 1


def test_get_termine_status_whitelist_and_unknown_logged(monkeypatch, capsys):
    termine = [
        _termin(von=10, status="OK"),
        _termin(von=11, status="VM"),
        _termin(von=12, status="RQ"),  # RQ renders like a bookable row
        _termin(von=13, status="SPERRE_TEMP"),
        _termin(von=14, status="SPERRE"),
        _termin(von=15, status="J4Y"),
        _termin(von=16, status="WAT"),
        _termin(von=17, status="WAT"),  # second occurrence: logged only once
    ]
    monkeypatch.setattr(
        ti, "_tourone_get", lambda *a, **k: _page(_travel_t("A", *termine))
    )
    out = ti.get_termine(("A",))
    assert [t["status"] for t in out] == ["OK", "VM", "RQ"]
    assert capsys.readouterr().out.count("unknown termin status: WAT") == 1


def test_get_termine_past_filter_boundary(monkeypatch):
    no_von = _termin(von=5)
    no_von["von"] = None
    termine = [
        _termin(von=-1),  # departed yesterday: dropped
        _termin(von=0),   # departs today: stays visible
        _termin(von=1),   # future: stays
        no_von,           # unplaceable in time: dropped
    ]
    monkeypatch.setattr(
        ti, "_tourone_get", lambda *a, **k: _page(_travel_t("A", *termine))
    )
    out = ti.get_termine(("A",))
    assert [t["von"] for t in out] == [_d(0), _d(1)]


# --- formatter: site row semantics --------------------------------------------


def test_twins_same_dates_are_distinct_rows_in_pinned_order(monkeypatch):
    # CRITICAL regression (Marrakesch): same (von, bis) on two variant codes
    # are DISTINCT bookable variants; the site shows BOTH, FRA twin first.
    fra = _termin(von=100, tage=17, gp=0, ez=0, preis=2999.0)
    f18 = _termin(von=100, tage=17, gp=5, ez=2, preis=3199.0)
    # API returns them in the "wrong" order; the code-tuple order must win.
    page = _page(_travel_t("MAMAR_18F", f18), _travel_t("MAMAR_FRA", fra))
    monkeypatch.setattr(ti, "_tourone_get", lambda *a, **k: page)
    out = ti.get_termine(("MAMAR_FRA", "MAMAR_18F"))
    rows = _table_rows(ti.format_termine_markdown(out))
    assert len(rows) == 2  # NO (von, bis) dedupe
    assert "ausgebucht" in rows[0]  # FRA twin first (stable sort, merge order)
    assert "5 Plätze verfügbar" in rows[1]


def test_gobi_twin_one_ok_one_sperre_temp(monkeypatch):
    ok = _termin(von=50, gp=7)
    blocked = _termin(von=50, gp=0, status="SPERRE_TEMP")
    monkeypatch.setattr(
        ti, "_tourone_get", lambda *a, **k: _page(_travel_t("GOBI", ok, blocked))
    )
    out = ti.get_termine(("GOBI",))
    assert len(out) == 1 and out[0]["status"] == "OK"


def test_exact_tuple_duplicate_collapsed():
    a = _termin(von=60)
    b = dict(a)  # identical in every displayed field (master-shell copy)
    assert len(_table_rows(ti.format_termine_markdown([a, b]))) == 1


def test_plaetze_wording_and_ez_suppression_when_sold_out():
    md = ti.format_termine_markdown([
        _termin(von=10, gp=5, ez=2),
        _termin(von=20, gp=1, ez=2),
        _termin(von=30, gp=0, ez=2),  # sold out: EZ suppressed despite feed EZ=2
    ])
    rows = _table_rows(md)
    assert "5 Plätze verfügbar" in rows[0]
    assert "1 Platz verfügbar" in rows[1] and "Plätze" not in rows[1]  # singular!
    assert "ausgebucht" in rows[2] and "Einzelzimmer" not in rows[2]


def test_einzelzimmer_wording():
    md = ti.format_termine_markdown([
        _termin(von=10, gp=6, ez=3),
        _termin(von=20, gp=6, ez=0),  # EZ=0 while GP>0 -> auf Anfrage
    ])
    rows = _table_rows(md)
    assert "3 Einzelzimmer verfügbar" in rows[0]
    assert "Einzelzimmer auf Anfrage" in rows[1]


def test_vm_shows_vorausbuchen_and_keeps_vakanz():
    row = _table_rows(
        ti.format_termine_markdown([_termin(von=200, status="VM", gp=12, ez=4)])
    )[0]
    assert "Jetzt vorausbuchen" in row
    assert "12 Plätze verfügbar" in row  # vakanz cells still shown for VM


def test_vm_sold_out_renders_plain_ausgebucht():
    # Observed live (Machu-Picchu 2027): a sold-out VM row shows 'ausgebucht'
    # WITHOUT the vorausbuchen CTA.
    row = _table_rows(
        ti.format_termine_markdown([_termin(von=200, status="VM", gp=0, ez=2)])
    )[0]
    assert "ausgebucht" in row
    assert "Jetzt vorausbuchen" not in row


def test_tage_is_inclusive_span_not_dauer():
    t = _termin(von=100, tage=18)
    assert t["dauer"] == 17  # the feed's nights field must NOT be displayed
    assert "18 Tage" in ti.format_termine_markdown([t])


def test_tage_omitted_when_feed_dauer_missing():
    # Feed rows without a dauer cannot back their length (OAP add-on case) —
    # the cell is omitted, never a possibly-false span claim.
    t = _termin(von=100, tage=18)
    t["dauer"] = None
    assert "Tage" not in _table_rows(ti.format_termine_markdown([t]))[0]


def test_price_format_and_failsafe_cells():
    priced = _termin(von=10, preis=4099.0)
    unpriced = _termin(von=20)
    unpriced["abPreis"] = None
    novak = _termin(von=30)
    novak["vakanzSync"] = None
    rows = _table_rows(ti.format_termine_markdown([priced, unpriced, novak]))
    assert "4.099 €" in rows[0]  # German thousands format
    assert "€" not in rows[1]  # missing price -> empty cell
    # missing vakanz -> cells omitted: never crash, never claim "ausgebucht"
    assert "verfügbar" not in rows[2] and "ausgebucht" not in rows[2]


def test_sort_ascending_by_von_across_codes(monkeypatch):
    page = _page(_travel_t("B", _termin(von=10)), _travel_t("A", _termin(von=40)))
    monkeypatch.setattr(ti, "_tourone_get", lambda *a, **k: page)
    md = ti.format_termine_markdown(ti.get_termine(("A", "B")))  # merge: A(40), B(10)
    assert md.index(ti._fmt_date(_d(10))) < md.index(ti._fmt_date(_d(40)))


def test_cap_100_with_overflow_marker():
    termine = [_termin(von=10 + i, preis=100.0 + i) for i in range(101)]
    md = ti.format_termine_markdown(termine)
    assert len(_table_rows(md)) == 100
    assert md.splitlines()[-1] == "… und 1 weitere Termine"


def test_format_termine_markdown_empty():
    assert ti.format_termine_markdown([]) == ""


# --- get_termine_markdown: injection contract ---------------------------------


def test_matched_but_all_filtered_explicit_empty_state(monkeypatch):
    monkeypatch.setattr(ti, "get_reisecodes", lambda url: ["A"])
    page = _page(_travel_t("A", _termin(von=-30)))  # dead season: only past rows
    monkeypatch.setattr(ti, "_tourone_get", lambda *a, **k: page)
    md = ti.get_termine_markdown("/Asien/Nepal/Lumbini")
    assert "Derzeit keine buchbaren Termine." in md  # explicit, NOT ""
    assert "#termine" in md


def test_get_termine_markdown_error_returns_empty_not_false_claim(monkeypatch):
    monkeypatch.setattr(ti, "get_reisecodes", lambda url: ["A"])

    def boom(*a, **k):
        raise requests.RequestException("api down")

    monkeypatch.setattr(ti, "_tourone_get", boom)
    # No section beats a false "keine Termine" claim during an API blip.
    assert ti.get_termine_markdown("/Asien/Nepal/Lumbini") == ""


def test_get_termine_markdown_no_match(monkeypatch):
    monkeypatch.setattr(ti, "get_reisecodes", lambda url: [])
    assert ti.get_termine_markdown("/nope") == ""


def test_website_tool_appends_termine(monkeypatch):
    import agent_base

    monkeypatch.setattr(
        agent_base,
        "get_chamaeleon_website_html",
        lambda p: "<html><title>Lumbini</title><main>Inhalt</main></html>",
    )
    monkeypatch.setattr(ti, "get_termine_markdown", lambda p: "## Termine\n\n| x |")
    out = agent_base.chamaeleon_website_tool_base("/Asien/Nepal/Lumbini")
    assert "Inhalt" in out
    assert out.rstrip().endswith("| x |")  # termine appended after the page
