import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

from faq_sync import _mit_a, parse_infos, parse_md, render


def _seite(n, ids=None):
    ids = ids or [f"CHA-T-FAQ-{i}" for i in range(n)]
    lis = "".join(
        f'<li tos-template="text_akkordeon" tos-txtnr="{t}">'
        f'<h4 class="uk-accordion-title">Frage {i}?</h4>'
        f'<div class="uk-accordion-content"><p>Ab 20 €, siehe <a href="/Club">Club</a>.</p></div></li>'
        for i, t in enumerate(ids)
    )
    return f"<h3>Unterwegs</h3><ul>{lis}</ul>".encode("cp1252")


def test_parse_infos():
    rows = parse_infos(_seite(20))
    assert len(rows) == 20
    assert rows[0] == {
        "quelle": "website",
        "extern_id": "CHA-T-FAQ-0",
        "kategorie": "Unterwegs",
        "frage": "Frage 0?",
        "antwort": "Ab 20 €, siehe [Club](/Club).",
        "position": 1000,
    }


def test_parse_infos_schuetzt_vor_fehlerseiten():
    with pytest.raises(ValueError, match="nur 19"):
        parse_infos(_seite(19))
    with pytest.raises(ValueError, match="doppelte"):
        parse_infos(_seite(20, ids=["X"] * 20))


def test_allgemein_md_rundlauf():
    # Der Import nach Supabase darf von allgemein.md nichts verlieren.
    with open("faqs/allgemein.md", encoding="utf-8") as f:
        md = f.read()
    assert render(parse_md(md)) == _mit_a(md)


def test_render_intern_vor_website():
    rows = parse_infos(_seite(20)) + parse_md("## Kataloge\n\n**F: Wo?**\nA: Hier.")
    assert render(rows).startswith("## Kataloge\n\n**F: Wo?**\nA: Hier.\n\n## Unterwegs")
