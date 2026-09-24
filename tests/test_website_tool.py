"""website_tool_multi — mehrere Seiten, ein Abschnitt, ohne Netz und Modell.

Gemockt wird ``get_chamaeleon_website_html``: alles darueber (Cache, Schnitt,
Schranken, Zusammenbau) laeuft echt. ``travel_index.get_termine_markdown`` ist
in jedem Test stillgelegt, sonst ginge der Termine-Anhang live an TourOne.

Der wichtigste Test der Datei ist ``test_eine_seite_zeichengleich_wie_vorher``:
der Cache ist von rohem HTML auf Titel + Markdown umgezogen (Review D9), und
die Ausgabe der Ein-Seiten-Funktion muss das ueberlebt haben — ``berater_tool_base``
liest sie, und die Reihenfolge acht Leerzeichen vor dem Markdown ist Teil davon.

    pytest tests/test_website_tool.py -v
"""

from types import SimpleNamespace

import pytest
import requests

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
from agent_base import WEBSITE_TOOL_MAX_CHARS, WEBSITE_TOOL_MAX_PATHS, website_tool_multi


# Aufbau einer echten Reiseseite, gekuerzt (gezogen 2026-09-21 ueber
# /Afrika/Tansania/Cheetah). Die Abschnitte stehen unterstrichen (setext) da.
def _reiseseite(titel: str, reiseverlauf: str) -> str:
    return (
        f"<html><title>{titel}</title><main>"
        "<h2>Herzklopf-Momente</h2>"
        "<p>Hatari Lodge.</p>"
        "<h2>Reiseverlauf</h2>"
        f"<p>{reiseverlauf}</p>"
        "<h2>Reisedetails</h2>"
        "<p>Mindestteilnehmerzahl fuer die Ballonfahrt: 6.</p>"
        "<h2>Leistungen</h2>"
        "<p>Linienflug mit Discover Airlines.</p>"
        "</main></html>"
    )


# Eine Laenderseite (/Afrika/Namibia, /Afrika/Namibia/Etosha-ALL) traegt keinen
# einzigen Abschnitt — gemessen 2026-09-23: Etosha-ALL hat 27.456 Zeichen und
# keinen Reiseverlauf, waehrend die Reiseseite /Afrika/Namibia/Etosha alle acht
# Abschnitte fuehrt. Genau dafuer ist der Hinweis je Seite da.
LAENDERSEITE = (
    "<html><title>Namibia</title><main><p>Unsere Reisen durch Namibia.</p></main></html>"
)

SEITEN = {
    "/Afrika/Tansania/Cheetah": _reiseseite("Cheetah", "Weiterflug nach Sansibar."),
    "/Afrika/Tansania/Ruaha": _reiseseite("Ruaha", "Fahrt in den Ruaha-Nationalpark."),
    "/Afrika/Namibia/Etosha-ALL": LAENDERSEITE,
}


@pytest.fixture(autouse=True)
def seiten(monkeypatch):
    """Liefert SEITEN statt der echten Website und leert den Markdown-Cache.

    Ohne das Leeren bekaeme der zweite Test mit derselben URL die Seite des
    ersten — gruen, ohne je das neue Markup gesehen zu haben.
    """
    import travel_index

    agent_base.seite_markdown.cache_clear()
    monkeypatch.setattr(travel_index, "get_termine_markdown", lambda _url: "")

    def _html(url_path):
        if url_path not in SEITEN:
            raise requests.RequestException(f"404 fuer {url_path}")
        return SEITEN[url_path]

    monkeypatch.setattr(agent_base, "get_chamaeleon_website_html", _html)
    yield
    agent_base.seite_markdown.cache_clear()


def test_eine_seite_zeichengleich_wie_vorher():
    """Der Cache-Umzug (Review D9) darf die Ausgabe nicht um ein Zeichen aendern.

    Die acht Leerzeichen vor dem Markdown kommen aus der eingerueckten
    Literalform und stehen seit jeher in der Ausgabe. Sie hier wortgetreu
    festzuhalten ist der Sinn des Tests: ``berater_tool_base`` und
    ``seiten_abschnitt`` lesen genau diesen Text.
    """
    erwartet = (
        "# Cheetah\n"
        "\n"
        "        Herzklopf-Momente\n"
        "-----------------\n"
        "\n"
        "Hatari Lodge.\n"
        "\n"
        "Reiseverlauf\n"
        "------------\n"
        "\n"
        "Weiterflug nach Sansibar.\n"
        "\n"
        "Reisedetails\n"
        "------------\n"
        "\n"
        "Mindestteilnehmerzahl fuer die Ballonfahrt: 6.\n"
        "\n"
        "Leistungen\n"
        "----------\n"
        "\n"
        "Linienflug mit Discover Airlines."
    )
    alt = agent_base.chamaeleon_website_tool_base("/Afrika/Tansania/Cheetah")
    assert alt == erwartet
    # Und der neue Weg liefert bei einem Pfad ohne Abschnitt dasselbe.
    assert website_tool_multi(["/Afrika/Tansania/Cheetah"]) == alt


def test_eine_seite_ohne_kopfzeile():
    """Die Kopfzeile ``# <Pfad>`` erscheint nur bei mehr als einem Pfad."""
    out = website_tool_multi(["/Afrika/Tansania/Cheetah"], "reiseverlauf")
    assert not out.startswith("# /Afrika/Tansania/Cheetah")
    assert out.startswith("Reiseverlauf")


def test_reihenfolge_und_kopfzeilen():
    """Ausgabe in Eingabereihenfolge, jede Seite unter ihrem Pfad."""
    out = website_tool_multi(
        ["/Afrika/Tansania/Ruaha", "/Afrika/Tansania/Cheetah"], "reiseverlauf"
    )
    assert out.index("# /Afrika/Tansania/Ruaha") < out.index("# /Afrika/Tansania/Cheetah")
    assert "Ruaha-Nationalpark" in out
    assert "Sansibar" in out
    # Geschnitten heisst geschnitten: die Leistungen sind nicht mitgekommen.
    assert "Discover Airlines" not in out


def test_ein_fehlschlag_bleibt_lokal():
    """Eine von zwei Seiten faellt aus — die andere kommt trotzdem an.

    Und der Abruffehler darf NICHT als „Abschnitt fehlt" erscheinen: sonst
    schliesst das Modell „diese Reise hat keinen Reiseverlauf" statt „ich
    konnte die Seite nicht lesen".
    """
    out = website_tool_multi(
        ["/Afrika/Tansania/Gibtsnicht", "/Afrika/Tansania/Cheetah"], "reiseverlauf"
    )
    assert "Fehler beim Abrufen der Seite" in out
    assert "hat keinen Abschnitt" not in out
    assert "Sansibar" in out


def test_zu_viele_pfade():
    out = website_tool_multi([f"/Seite/{i}" for i in range(9)], "reiseverlauf")
    assert "Zu viele Seiten: 9" in out
    assert str(WEBSITE_TOOL_MAX_PATHS) in out


def test_einzelner_string_statt_liste():
    """Gemini schickt gelegentlich einen blanken String."""
    assert website_tool_multi("/Afrika/Tansania/Cheetah") == website_tool_multi(
        ["/Afrika/Tansania/Cheetah"]
    )


def test_mehrere_pfade_ohne_abschnitt_liefert_anweisung():
    """Review D8: keine Seiten, sondern die gueltigen Abschnittsnamen."""
    out = website_tool_multi(["/Afrika/Tansania/Ruaha", "/Afrika/Tansania/Cheetah"])
    assert "reiseverlauf" in out
    assert "uebersicht" in out
    # Kein Seiteninhalt durchgerutscht.
    assert "Hatari Lodge" not in out


def test_unbekannter_abschnitt_liefert_dieselbe_anweisung():
    """Nie stillschweigend die ganze Seite."""
    out = website_tool_multi(["/Afrika/Tansania/Cheetah"], "preise")
    assert "reiseverlauf" in out
    assert "Hatari Lodge" not in out


def test_abschnitt_fehlt_auf_einer_von_zwei_seiten():
    """Laenderseiten tragen keine Abschnitte — Hinweis nur fuer diese Seite."""
    out = website_tool_multi(
        ["/Afrika/Namibia/Etosha-ALL", "/Afrika/Tansania/Cheetah"], "reiseverlauf"
    )
    assert "Die Seite /Afrika/Namibia/Etosha-ALL hat keinen Abschnitt" in out
    assert "Sansibar" in out


def test_gesamtdeckel_greift(monkeypatch):
    """Eine Seite ueber dem Deckel wird gekuerzt, und sie sagt es auch."""
    riesig = (
        "<html><title>Gross</title><main><p>"
        + ("x" * (WEBSITE_TOOL_MAX_CHARS + 20_000))
        + "</p></main></html>"
    )
    monkeypatch.setattr(agent_base, "get_chamaeleon_website_html", lambda _p: riesig)

    out = website_tool_multi(["/A", "/B"])
    # Zwei ganze Seiten ohne Abschnitt kommen gar nicht erst durch (D8).
    assert "reiseverlauf" in out

    out = website_tool_multi(["/A"])
    assert len(out) <= WEBSITE_TOOL_MAX_CHARS
    assert "gekürzt" in out


def test_gesamtdeckel_teilt_gleichmaessig(monkeypatch):
    """Acht grosse Abschnitte: Summe unter dem Deckel, jede Seite gekuerzt."""
    gross = (
        "<html><title>Gross</title><main>"
        "<h2>Reiseverlauf</h2><p>" + ("y" * 40_000) + "</p>"
        "<h2>Reisedetails</h2><p>Ende.</p>"
        "</main></html>"
    )
    monkeypatch.setattr(agent_base, "get_chamaeleon_website_html", lambda _p: gross)

    pfade = [f"/Seite/{i}" for i in range(WEBSITE_TOOL_MAX_PATHS)]
    out = website_tool_multi(pfade, "reiseverlauf")
    assert out.count("gekürzt") == WEBSITE_TOOL_MAX_PATHS
    # Die Kopfzeilen kommen zum Inhalt dazu, der Inhalt selbst haelt den Deckel.
    assert len(out) <= WEBSITE_TOOL_MAX_CHARS + 200 * WEBSITE_TOOL_MAX_PATHS


def test_anker_und_https_praefix_je_pfad():
    """Die heutige Normalisierung gilt fuer jeden Pfad der Liste."""
    out = website_tool_multi(
        [
            "https://chamaeleon-reisen.de/Afrika/Tansania/Ruaha#termine",
            "/Afrika/Tansania/Cheetah#reiseverlauf",
        ],
        "reiseverlauf",
    )
    assert "# /Afrika/Tansania/Ruaha\n" in out
    assert "# /Afrika/Tansania/Cheetah\n" in out
    assert "#termine" not in out
    assert "Ruaha-Nationalpark" in out
    assert "Sansibar" in out


def test_zweiter_aufruf_trifft_den_markdown_cache(monkeypatch):
    """Review D9: gecacht wird Titel + Markdown, nicht mehr das rohe HTML."""
    abrufe = []

    def _zaehlend(url_path):
        abrufe.append(url_path)
        return SEITEN["/Afrika/Tansania/Cheetah"]

    monkeypatch.setattr(agent_base, "get_chamaeleon_website_html", _zaehlend)

    erst = website_tool_multi(["/Afrika/Tansania/Cheetah"], "reiseverlauf")
    zweit = website_tool_multi(["/Afrika/Tansania/Cheetah"], "reiseverlauf")

    assert erst == zweit
    assert len(abrufe) == 1


def test_doppelter_pfad_nur_einmal(monkeypatch):
    """Derselbe Pfad zweimal in der Liste: einmal holen, einmal ausgeben."""
    abrufe = []

    def _zaehlend(url_path):
        abrufe.append(url_path)
        return SEITEN["/Afrika/Tansania/Cheetah"]

    monkeypatch.setattr(agent_base, "get_chamaeleon_website_html", _zaehlend)

    out = website_tool_multi(
        ["/Afrika/Tansania/Cheetah", "/Afrika/Tansania/Cheetah#termine"], "reiseverlauf"
    )
    assert len(abrufe) == 1
    assert out.count("Sansibar") == 1


def test_leere_liste():
    assert "Kein Pfad angegeben" in website_tool_multi([])


def test_uebersicht_traegt_den_titel():
    """``uebersicht`` ist Seitenanfang bis Reiseverlauf — dort steht der Titel."""
    out = website_tool_multi(["/Afrika/Tansania/Cheetah"], "uebersicht")
    assert out.startswith("# Cheetah")
    assert "Hatari Lodge" in out
    assert "Sansibar" not in out


def test_beschreibung_nennt_beide_argumente():
    """Die Tool-Beschreibung ist das Einzige, was das Modell vorher liest."""
    beschreibung = agent_base.build_website_tool_description()
    assert "url_paths" in beschreibung
    assert "abschnitt" in beschreibung
    assert "reiseverlauf" in beschreibung


# Die echte Abruffunktion, festgehalten vor der autouse-Fixture oben, die sie
# fuer alle anderen Tests durch einen Fake ersetzt.
_ECHTER_ABRUF = agent_base.get_chamaeleon_website_html


@pytest.mark.parametrize("pfad", ["@evil.com/x", ".evil.com/x", "%40evil.com"])
def test_pfad_fuehrt_nie_auf_einen_fremden_host(monkeypatch, pfad):
    """Review 2026-09-24: der Pfad kommt vom Modell; mit "@" davor waere der
    Rest der Host. Kein Abruf, sondern ein Fehler vor dem Request."""
    geholt = []
    monkeypatch.setattr(requests, "get", lambda url, **_k: geholt.append(url))
    with pytest.raises(ValueError):
        _ECHTER_ABRUF(pfad)
    assert geholt == []


def test_echter_pfad_wird_geholt(monkeypatch):
    geholt = []

    antwort = SimpleNamespace(text="<html></html>", raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "get", lambda url, **_k: geholt.append(url) or antwort)
    _ECHTER_ABRUF("/Afrika/Tansania/Ruaha")
    assert geholt == ["https://www.chamaeleon-reisen.de/Afrika/Tansania/Ruaha"]


@pytest.mark.parametrize(
    "eingabe",
    [
        "https://www.chamaeleon-reisen.de/Afrika/Namibia",
        "https://chamaeleon-reisen.de/Afrika/Namibia",
        "https://www.chamaeleon-reisen.de/Afrika/Namibia#termine",
        "/Afrika/Namibia",
    ],
)
def test_pfad_normalisierung_kennt_beide_hosts(eingabe):
    """Review 2026-09-25: die volle www-URL blieb stehen, und der Abruf lief
    auf ``BASE_URL + BASE_URL + pfad`` gegen den Host-Check."""
    assert agent_base._normalisiere_pfad(eingabe) == "/Afrika/Namibia"
