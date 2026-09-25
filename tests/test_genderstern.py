"""Genderstern im gerenderten Chat: immer ein Stern, nie ein Backslash, nie kursiv.

Gemini schreibt mal Markdown, mal selbst HTML ("<p>…</p>"). Das HTML reicht
mistune unveraendert durch — ein vorher eingefuegtes "\\*" stand dann sichtbar
im Chat ("Mitarbeiter\\*innen", 2026-09-25). Geprueft wird deshalb, was der
Browser anzeigt, nicht der Zwischenschritt.

    pytest tests/test_genderstern.py -v
"""

import html
import re

import mistune
import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

from agent import escape_genderstern


def angezeigt(reply: str) -> str:
    """Was der Kunde liest: gerendert wie in agent.call_stream, Tags weg, Entities aufgeloest."""
    gerendert = mistune.markdown(escape_genderstern(reply), escape=False)
    return html.unescape(re.sub(r"<[^>]+>", "", gerendert)).strip()


@pytest.mark.parametrize(
    "reply",
    [
        "Frag die Berater*innen oder die Mitarbeiter*innen.",
        "<p>Frag die Berater*innen oder die Mitarbeiter*innen.</p>",
        "<p>Frag die Berater\\*innen oder die Mitarbeiter\\*innen.</p>",
        "Frag die Berater\\*innen oder die Mitarbeiter\\*innen.",
    ],
    ids=["markdown", "html", "html-selbst-escaped", "markdown-selbst-escaped"],
)
def test_genderstern_bleibt_ein_stern(reply):
    assert angezeigt(reply) == "Frag die Berater*innen oder die Mitarbeiter*innen."


def test_zwei_gendersterne_werden_nicht_kursiv():
    gerendert = mistune.markdown(
        escape_genderstern("Berater*innen und Mitarbeiter*innen"), escape=False
    )
    assert "<em>" not in gerendert


def test_echte_betonung_und_stern_im_link_bleiben():
    reply = '**Wichtig** und <a href="/Suche?q=a*b">Link</a>'
    gerendert = mistune.markdown(escape_genderstern(reply), escape=False)
    assert "<strong>Wichtig</strong>" in gerendert
    assert 'href="/Suche?q=a*b"' in gerendert
