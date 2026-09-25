"""Nackte Website-Pfade im Chat werden anklickbar (Owner, 2026-09-25).

Leon schrieb "… unter /Afrika/Uganda/Gorilla." als Text statt als Link.

    pytest tests/test_nackte_pfade.py -v
"""

import mistune
import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

from agent import verlinke_nackte_pfade

GORILLA = '<a href="/Afrika/Uganda/Gorilla" target="_blank">/Afrika/Uganda/Gorilla</a>'


def gerendert(reply: str) -> str:
    return verlinke_nackte_pfade(mistune.markdown(reply, escape=False)).strip()


def test_satzpunkt_gehoert_nicht_zum_link():
    assert gerendert("Mehr unter /Afrika/Uganda/Gorilla.") == f"<p>Mehr unter {GORILLA}.</p>"


def test_anker_bleibt_am_link():
    assert 'href="/Afrika/Uganda/Gorilla#termine"' in gerendert(
        "Termine: /Afrika/Uganda/Gorilla#termine, bitte."
    )


@pytest.mark.parametrize(
    "reply",
    [
        '<a href="/Afrika/Uganda/Gorilla" target="_blank">Gorilla</a>',
        '<a href="/x">Siehe /Afrika/Uganda/Gorilla</a>',
        "https://www.chamaeleon-reisen.de/Afrika/Uganda/Gorilla",
        "Pfad /Gibt/Es/Nicht, dazu 1/2 und und/oder.",
    ],
    ids=["schon-verlinkt", "im-linktext", "volle-url", "unbekannt"],
)
def test_bleibt_unveraendert(reply):
    vorher = mistune.markdown(reply, escape=False).strip()
    assert gerendert(reply) == vorher
