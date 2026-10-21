"""Unit: passolution.laender_der_seite. Offline, Website gepatcht."""

import pytest
import requests

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import passolution

CAPRIVI = (
    '<div data-passolution-covid-info=\'{"laendercodes": "BW, NA, ZW", '
    '"laendercode": "BNZ"}\'>'
)


@pytest.fixture(autouse=True)
def frischer_cache():
    passolution.laender_der_seite.cache_clear()
    yield
    passolution.laender_der_seite.cache_clear()


def seite(monkeypatch, antwort):
    aufrufe = []

    def holen(url_path):
        aufrufe.append(url_path)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort

    monkeypatch.setattr(agent_base, "get_chamaeleon_website_html", holen)
    return aufrufe


def test_mehrlaenderreise_alle_codes_ohne_pseudocode(monkeypatch):
    aufrufe = seite(monkeypatch, CAPRIVI)
    assert passolution.laender_der_seite("/Afrika/Botswana-Namibia/Caprivi") == ["BW", "NA", "ZW"]
    passolution.laender_der_seite("/Afrika/Botswana-Namibia/Caprivi")
    assert aufrufe == ["/Afrika/Botswana-Namibia/Caprivi"]  # gecacht


def test_laenderseite_ohne_feld_leer(monkeypatch):
    seite(monkeypatch, '<div data-trigger-passolution-select="DE">Deutschland</div>')
    assert passolution.laender_der_seite("/Amerika/Kolumbien") == []


def test_ausfall_wirft_und_wird_nicht_gecacht(monkeypatch):
    seite(monkeypatch, requests.ConnectionError("weg"))
    with pytest.raises(requests.ConnectionError):
        passolution.laender_der_seite("/Amerika/Kolumbien/San-Agustin")
    aufrufe = seite(monkeypatch, '{"laendercodes": "CO", "laendercode": "CO"}')
    assert passolution.laender_der_seite("/Amerika/Kolumbien/San-Agustin") == ["CO"]
    assert aufrufe == ["/Amerika/Kolumbien/San-Agustin"]
