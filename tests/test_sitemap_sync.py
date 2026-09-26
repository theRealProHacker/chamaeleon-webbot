"""Sitemap-Sync: tote Reise-URLs erkennen, auch hinter dem "-ALL"-Vergleich.

Gemessen 2026-09-25: 60 von 322 Reise-URLs der Sitemap lieferten mit Status
200 nur die Laenderuebersicht, u. a. "/Afrika/Suedafrika/Outeniqua-ALL" — die
Reise liegt unter ".../Outeniqua", und Leon fand sie nie.

    pytest tests/test_sitemap_sync.py -v
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import sitemap_sync as s


class _Antwort:
    def __init__(self, html: bytes):
        self._html = html

    def iter_content(self, chunk_size):
        for i in range(0, len(self._html), chunk_size):
            yield self._html[i : i + chunk_size]


def test_verdeckte_tote_all_url_wird_durch_die_lebende_ersetzt():
    live = {"/Afrika/Suedafrika/Outeniqua", "/Afrika/Suedafrika/Pinotage"}
    static = ["/Afrika/Suedafrika/Outeniqua-ALL", "/Afrika/Suedafrika/Pinotage"]

    # compute_diff allein sieht nichts: Outeniqua-ALL gilt als live.
    assert s.compute_diff(live, static) == ([], [])
    assert s.verdeckt(live, static) == ["/Afrika/Suedafrika/Outeniqua-ALL"]
    assert s.ersatz_fuer(["/Afrika/Suedafrika/Outeniqua-ALL"], live, static) == [
        "/Afrika/Suedafrika/Outeniqua"
    ]


def test_ersatz_nur_wenn_die_lebende_form_noch_fehlt():
    live = {"/Afrika/Namibia/Sossusvlei"}
    static = ["/Afrika/Namibia/Sossusvlei-ALL", "/Afrika/Namibia/Sossusvlei"]
    assert s.verdeckt(live, static) == ["/Afrika/Namibia/Sossusvlei-ALL"]
    assert s.ersatz_fuer(["/Afrika/Namibia/Sossusvlei-ALL"], live, static) == []


def test_echte_reiseseite_hat_canonical_attrappe_nicht():
    echt = b'<html><head><title>Outeniqua</title><link\n  href="https://x/y"\n  rel="canonical"\n/></head><body>'
    attrappe = b"<html><head><title>S\xfcdafrika Urlaub</title></head><body>" + b"x" * 50_000
    assert s._hat_canonical(_Antwort(echt))
    assert not s._hat_canonical(_Antwort(attrappe))


def test_nur_reisepfade_brauchen_eine_echte_reiseseite():
    assert s._ist_reise_pfad("/Afrika/Suedafrika/Outeniqua-ALL")
    assert not s._ist_reise_pfad("/Afrika/Suedafrika")
    assert not s._ist_reise_pfad("/Infos/Reiseversicherung/Details")


def test_netzfehler_wird_wiederholt_und_bleibt_sonst_stehen(monkeypatch):
    # je Pfad: Ergebnis im ersten und im zweiten Versuch (None = Netzfehler)
    antworten = {"/A/B/tot": [None, False], "/A/B/wackelt": [None, None], "/A/B/lebt": [True]}
    versuche = {p: iter(a) for p, a in antworten.items()}
    monkeypatch.setattr(s, "_lebt", lambda path, timeout=10: next(versuche[path]))

    dead, kept = s._check_removals(list(antworten))
    assert dead == ["/A/B/tot"]
    assert kept == ["/A/B/wackelt", "/A/B/lebt"]
