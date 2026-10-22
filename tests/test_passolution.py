"""Unit: passolution. Offline, Website und Passolution gepatcht."""

import json
import os

import pytest
import requests

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import kundendaten
import passolution
import rate_limit
import unterlagen

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


# --- Abruf, Format, Fallback (Commit 3) --------------------------------------
#
# Offline: requests.get ist gepatcht, die Antworten sind echte Bodies aus
# tests/fixtures/passolution/ (nur Länderregeln). Die TripURL-Antwort ist
# synthetisch (keine echte tid).

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "passolution")


def _body(name: str) -> bytes:
    with open(os.path.join(_FIX, name), "rb") as f:
        return f.read()


def _records(name: str) -> list:
    return json.loads(_body(name))["records"]


def _antwort(status: int, body: bytes = b"{}") -> requests.Response:
    r = requests.Response()
    r.status_code, r._content, r.url = status, body, "https://api.passolution.eu/x"
    return r


@pytest.fixture
def api(monkeypatch):
    """``api(antwort, ...)``: requests.get liefert der Reihe nach; gibt die Aufrufe zurück."""
    for f in (passolution._abrufen, passolution._tripurl_daten, passolution._reise_laender):
        f.cache_clear()
    aufrufe = []

    def einrichten(*antworten):
        folge = list(antworten)

        def get(url, params=None, headers=None, timeout=None):
            aufrufe.append((url, dict(params or {})))
            antwort = folge.pop(0) if len(folge) > 1 else folge[0]
            if isinstance(antwort, Exception):
                raise antwort
            return antwort

        monkeypatch.setattr(passolution.requests, "get", get)
        return aufrufe

    yield einrichten
    for f in (passolution._abrufen, passolution._tripurl_daten, passolution._reise_laender):
        f.cache_clear()


@pytest.fixture
def zaehler(monkeypatch):
    gezaehlt = []
    monkeypatch.setattr(rate_limit, "passolution_gezaehlt", gezaehlt.append)
    return gezaehlt


def test_formatieren_mit_stand_ohne_schlussbestimmungen():
    text = passolution._formatieren(_records("all_text_co_de.json"), ["CO"], ["DE"])
    assert text.startswith("## Kolumbien (CO)")
    assert "### Visum – für Staatsangehörige DE (Stand 01.09.2026)" in text
    assert "90 Tage" in text and "6 Monate" in text
    assert "Anforderungen der Fluggesellschaft" not in text  # final_provisions
    assert "\r" not in text


def test_formatieren_legt_gleiche_abschnitte_zusammen_und_haelt_den_deckel():
    text = passolution._formatieren(
        _records("all_text_bw_na_zw_dach.json"), ["BW", "NA", "ZW"], ["DE", "AT", "CH"]
    )
    assert "## Simbabwe (ZW)" in text
    assert "### Visum – für Staatsangehörige DE, AT, CH" in text  # Botswana: eine Fassung
    assert len(text) <= passolution.MAX_ZEICHEN


def test_formatieren_kuerzt_je_land_sichtbar(monkeypatch):
    monkeypatch.setattr(passolution, "MAX_ZEICHEN", 3000)
    text = passolution._formatieren(
        _records("all_text_bw_na_zw_dach.json"), ["BW", "NA", "ZW"], ["DE"]
    )
    assert text.count("(Gekürzt") == 3 and "## Simbabwe (ZW)" in text


def test_erfolg_zaehlt_einmal_cache_treffer_nicht(api, zaehler):
    aufrufe = api(_antwort(200, _body("all_text_co_de.json")))
    for _ in range(2):
        text = passolution.einreise_text(["CO"], ["DE"], schluessel="1.2.3.4", fallback="F")
        assert "Kolumbien" in text
    assert len(aufrufe) == 1 and zaehler == ["1.2.3.4"]
    assert aufrufe[0][1] == {"lang": "de", "countries": "CO", "nat": "DE"}


@pytest.mark.parametrize(
    "fehler",
    [_antwort(503), _antwort(429), _antwort(204, b""), requests.Timeout("langsam")],
    ids=["503", "429", "204", "timeout"],
)
def test_fehler_ergibt_fallback_und_wird_nicht_gecacht(api, zaehler, fehler):
    aufrufe = api(fehler, _antwort(200, _body("all_text_co_de.json")))
    assert passolution.einreise_text(["CO"], ["DE"], schluessel="k", fallback="F") == "F"
    assert "Kolumbien" in passolution.einreise_text(["CO"], ["DE"], schluessel="k", fallback="F")
    assert len(aufrufe) == 2 and zaehler == ["k"]


def test_unbekannter_code_geht_ans_modell_nicht_in_den_fallback(api, zaehler):
    api(_antwort(400, _body("all_text_xx_de.json")))
    text = passolution.einreise_text(["XX"], ["DE"], schluessel="k", fallback="F")
    assert text.startswith("Unbekannte Ländercodes: XX") and zaehler == []


def test_kontingent_erschoepft_ohne_abruf_aber_cache_geht(api, monkeypatch, zaehler):
    aufrufe = api(_antwort(200, _body("all_text_co_de.json")))
    passolution.einreise_text(["CO"], ["DE"], schluessel="k", fallback="F")
    monkeypatch.setattr(rate_limit, "passolution_erlaubt", lambda s: False)
    assert "Kolumbien" in passolution.einreise_text(["CO"], ["DE"], schluessel="k", fallback="F")
    text = passolution.einreise_text(["NA"], ["DE"], schluessel="k", fallback="F")
    assert text == "F" + passolution.LIMIT_SATZ and "visum.de" not in text
    assert len(aufrufe) == 1


def test_website_ohne_nationalitaet_fragt_ohne_abruf(api):
    aufrufe = api(requests.ConnectionError("darf nicht"))
    text = passolution.einreise_antwort("CO", "", endpoint="/Amerika/Kolumbien")
    assert text == passolution.NATIONALITAET_FEHLT and aufrufe == []


def test_ungueltige_codes_verworfen_und_gemeldet(api, zaehler):
    api(_antwort(200, _body("all_text_co_de.json")))
    text = passolution.einreise_antwort("Kolumbien, co", "de", endpoint="/")
    assert "verworfen: Kolumbien" in text and "## Kolumbien (CO)" in text


def test_website_nimmt_die_laender_der_reiseseite(api, monkeypatch, zaehler):
    aufrufe = api(_antwort(200, _body("all_text_bw_na_zw_dach.json")))
    seite(monkeypatch, CAPRIVI)
    text = passolution.einreise_antwort(
        "", "DE", endpoint="https://www.chamaeleon-reisen.de/Afrika/Botswana-Namibia/Caprivi"
    )
    assert aufrufe[0][1]["countries"] == "BW,NA,ZW" and "Simbabwe" in text


def test_fallback_website_nennt_reiseseite_ohne_visum_de(api, monkeypatch):
    api(_antwort(503))
    seite(monkeypatch, CAPRIVI)
    text = passolution.einreise_antwort("", "DE", endpoint="/Afrika/Botswana-Namibia/Caprivi")
    assert "/Afrika/Botswana-Namibia/Caprivi" in text and "Erlebnisberater" in text
    assert "visum.de" not in text and "Fehler" not in text


# --- Kunden-Modus ------------------------------------------------------------

BN = "9900001"
TRIPURL = "https://travel-details.eu/de?tid=TEST-TEST-0001"


@pytest.fixture
def kunde(monkeypatch):
    """Eine erfundene, eigene Buchung mit TripURL und Einreise-PDF (DE)."""
    buchung = {"vorgang": BN, "status": "OK", "reiseCode": "ZAFT", "tripurl": TRIPURL,
               "vonDat": "2099-01-10 00:00:00", "bisDat": "2099-01-24 00:00:00", "unterlagen": []}
    monkeypatch.setattr(kundendaten, "vorgangsnummern", lambda kid: [BN])
    monkeypatch.setattr(kundendaten, "_buchung_roh", lambda v: buchung)
    monkeypatch.setattr(kundendaten, "_buchungen_roh", lambda kid: [buchung])
    monkeypatch.setattr(unterlagen, "nationalitaeten", lambda u: ["DE"])
    return buchung


def _kunden_antwort(**kw):
    return passolution.einreise_antwort(
        "", "", endpoint=kw.pop("endpoint", "/MeinChamaeleon"), kunden_id="K1",
        reise_vorgang=BN, reise_label="Testreise, 10.01.2099", **kw,
    )


def test_kunde_tripurl_vor_pdf(api, kunde, zaehler):
    aufrufe = api(
        _antwort(200, _body("travel_details_synthetisch.json")),
        _antwort(200, _body("all_text_sz_za_ch.json")),
    )
    text = _kunden_antwort()
    assert aufrufe[0][0].endswith("/travel-details/TEST-TEST-0001")
    assert aufrufe[1][1]["countries"] == "SZ,ZA" and aufrufe[1][1]["nat"] == "CH"
    assert "Testreise, 10.01.2099" in text and TRIPURL in text


def test_kunde_ohne_tripurl_laender_aus_reise_nats_aus_pdf(api, kunde, monkeypatch, zaehler):
    import travel_index

    monkeypatch.setattr(
        travel_index, "_tourone_get", lambda path, params, timeout=20: {"land2": {"iso2Liste": "BW,NA,ZW"}}
    )
    aufrufe = api(
        _antwort(400, _body("travel_details_abgelaufen.json")),
        _antwort(200, _body("all_text_bw_na_zw_dach.json")),
    )
    text = _kunden_antwort()
    assert aufrufe[1][1] == {"lang": "de", "countries": "BW,NA,ZW", "nat": "DE"}
    assert "Simbabwe" in text


def test_kunde_fremde_buchung_wird_nicht_aufgeloest(api, kunde, monkeypatch):
    monkeypatch.setattr(kundendaten, "vorgangsnummern", lambda kid: ["1234567"])
    aufrufe = api(requests.ConnectionError("darf nicht"))
    assert _kunden_antwort() == passolution.LAND_FEHLT and aufrufe == []


def test_kunde_fallback_mit_tripurl_und_unterlagen(api, kunde):
    api(_antwort(200, _body("travel_details_synthetisch.json")), _antwort(503))
    text = _kunden_antwort()
    assert TRIPURL in text and f"VRRVORGANG={BN}#unterlagen" in text
    assert "visum.de" not in text


def test_reiseseite_ergaenzt_laender_aus_der_url(api, monkeypatch, zaehler):
    aufrufe = api(_antwort(200, _body("all_text_bw_na_zw_dach.json")))
    seite(monkeypatch, CAPRIVI)
    passolution.einreise_antwort("BW,NA", "DE", endpoint="/Afrika/Botswana-Namibia/Caprivi")
    assert aufrufe[0][1]["countries"] == "BW,NA,ZW"
    passolution.einreise_antwort("CO", "DE", endpoint="/Afrika/Botswana-Namibia/Caprivi")
    assert aufrufe[1][1]["countries"] == "CO"  # fremdes Land bleibt allein
