"""Erlebnisberater von der Reiseseite — deterministisch, ohne Netz und Modell.

Der TourOne-Index liefert das Berater-Feld seit jeher leer (gemessen
2026-09-18: 670 von 670 Reisen tragen ein berater-Dict, in allen 670 sind
vorname, nachname, telefon und email leer). Die Reiseseite traegt die Angabe
dagegen zuverlaessig, in zwei festen Formen. Diese Datei haelt beide Formen als
Markup-Schnipsel fest, damit ein Umbau der Website hier auffaellt und nicht
erst an einer falschen Auskunft im Chat.

    pytest tests/test_berater.py -v
"""

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base


# Wortgetreu aus dem Seiten-Markdown, gezogen 2026-09-18.
REISESEITE = """[Inlands- & Regionalfluege](/start/report.loadpdf.php?tpl=x&REICODE=AMKAU_ALL)

Buche die Reise deines Lebens
-----------------------------

![Mira Feldmann](/data/thumbs/fc95a4918921dea964f11c6def212d2d/PASSBILD900.jpg)

Erlebnisberater\\*in

Mira Feldmann

[+49 30 347996-901](tel:+49 30 347996-901)

[Zur Buchung](#termine)
"""

UEBERSICHTSSEITE = """![Jonas Wehrle](/data/thumbs/8be7a1fbb42c3b53e1d007fe336ffce1/Jonas-Wehrle.jpg)

![Jonas Wehrle](/data/thumbs/23a1185e90daea5618940e4ff68343c7/Jonas-Wehrle.jpg)

**Jonas\xa0Wehrle**
Erlebnisberaterin

Ich bin fuer dich da.

[+49 30 347996-902](tel:+49 30 347996-902 "Anruf starten")

Was Gaeste ueber unsere Marokko-Reisen sagen
"""

OHNE_BERATER = """Buche die Reise deines Lebens
-----------------------------

[Zur Buchung](#termine)
[Unverbindlich reservieren](#termine)
"""


@pytest.fixture(autouse=True)
def leerer_cache():
    """berater_von_seite ist ttl_cache't und keyt auf der URL allein.

    Ohne dieses Leeren bekaeme der zweite Test mit derselben URL die Antwort des
    ersten — gruen, und ohne je das neue Markup gesehen zu haben.
    """
    agent_base.berater_von_seite.cache_clear()
    yield
    agent_base.berater_von_seite.cache_clear()


@pytest.fixture
def seite(monkeypatch):
    """Liefert das uebergebene Markdown und erklaert jede URL zur Reise-URL."""

    def _setze(markdown, *, ist_reise=True):
        import travel_index

        monkeypatch.setattr(
            agent_base, "chamaeleon_website_tool_base", lambda _url: markdown
        )
        monkeypatch.setattr(travel_index, "is_reise_url", lambda _url: ist_reise)

    return _setze


def test_reiseseite(seite):
    seite(REISESEITE)
    assert agent_base.berater_von_seite("/Asien/X/Y") == (
        "Mira Feldmann",
        "+49 30 347996-901",
    )


def test_uebersichtsseite(seite):
    """Zweite Form: Name fett, Rolle darunter, geschuetztes Leerzeichen im Namen."""
    seite(UEBERSICHTSSEITE)
    assert agent_base.berater_von_seite("/Afrika/Marokko/Marrakesch-ALL") == (
        "Jonas Wehrle",
        "+49 30 347996-902",
    )


@pytest.mark.parametrize(
    "telefon",
    ["+49 30 347996-901", "+49 30-347996-903", "030347996905"],
    ids=["mit-leerzeichen", "mit-bindestrich", "ohne-trenner"],
)
def test_telefonformate(seite, telefon):
    """Alle drei in 25 Seiten gemessenen Schreibweisen kommen unveraendert durch.

    Unveraendert ist der Punkt: wer normalisiert, gibt eine Nummer aus, die so
    auf der Seite nicht steht.
    """
    seite(REISESEITE.replace("+49 30 347996-901", telefon))
    assert agent_base.berater_von_seite("/Asien/X/Y") == ("Mira Feldmann", telefon)


def test_seite_ohne_berater(seite):
    """Zwei von 25 Reisen fuehren keinen Berater — dort ist nichts zu holen."""
    seite(OHNE_BERATER)
    assert agent_base.berater_von_seite("/Afrika/Namibia/Erongo-ALL") == ("", "")


def test_keine_reise_url_ruft_die_seite_gar_nicht_ab(monkeypatch):
    """Die Schranke, ohne die jeder Agentur-Request in den Timeout liefe."""
    import travel_index

    def _darf_nicht(_url):
        raise AssertionError("Seitenabruf fuer eine Nicht-Reise-URL")

    monkeypatch.setattr(agent_base, "chamaeleon_website_tool_base", _darf_nicht)
    monkeypatch.setattr(travel_index, "is_reise_url", lambda _url: False)
    assert agent_base.berater_von_seite("/Agentur/Buchungen") == ("", "")


def test_abrufausnahme_bleibt_drin(seite, monkeypatch):
    """Ein Netzfehler darf den Promptbau nie mitreissen."""
    import travel_index

    def _wirft(_url):
        raise RuntimeError("HTTP 406")

    monkeypatch.setattr(agent_base, "chamaeleon_website_tool_base", _wirft)
    monkeypatch.setattr(travel_index, "is_reise_url", lambda _url: True)
    assert agent_base.berater_von_seite("/Asien/X/Y") == ("", "")


def test_leeres_markdown(seite):
    seite("")
    assert agent_base.berater_von_seite("/Asien/X/Y") == ("", "")


def test_name_ohne_telefon_gibt_nichts(seite):
    """Kein Teiltreffer: ein halber Kontakt ist schlechter als keiner."""
    seite(REISESEITE.replace("[+49 30 347996-901](tel:+49 30 347996-901)", ""))
    assert agent_base.berater_von_seite("/Asien/X/Y") == ("", "")
