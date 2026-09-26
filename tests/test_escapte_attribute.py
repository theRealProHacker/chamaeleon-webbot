"""Escapte Anführungszeichen in Tags (<a href=\\"…\\">) werden repariert.

Gemessen 2026-09-25: Gemini schrieb im Koffer-Fall 1 von 5 Mal
``<a href=\\"https://…#unterlagen\\" target=\\"_blank\\">``; mistune maskierte den
Tag, und der Kunde sah den Link als Text.

    pytest tests/test_escapte_attribute.py -v
"""

import mistune

import common as _  # noqa: F401  (adds repo root to sys.path)

from agent import entferne_escapte_anfuehrungszeichen

ROH = (
    'In deinen <a href=\\"https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG=1#unterlagen\\" '
    'target=\\"_blank\\">Reiseunterlagen</a>. Er sagte \\"hallo\\".'
)


def test_link_bleibt_ein_link():
    gerendert = mistune.markdown(entferne_escapte_anfuehrungszeichen(ROH), escape=False)
    assert '<a href="https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG=1#unterlagen" target="_blank">' in gerendert
    assert "&lt;a" not in gerendert


def test_text_ausserhalb_von_tags_bleibt_unveraendert():
    assert entferne_escapte_anfuehrungszeichen(ROH).endswith('Er sagte \\"hallo\\".')


def test_ohne_escapes_unveraendert():
    text = '<a href="/Afrika/Namibia" target="_blank">Namibia</a>'
    assert entferne_escapte_anfuehrungszeichen(text) == text
