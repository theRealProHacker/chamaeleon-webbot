"""Reiseliste der Laenderseiten und Ortspruefung im Website-Tool, ohne Netz.

Katharinas Mail (27.07.2026), Punkte 8 und 10: Leon empfahl fuer "14 Tage
Namibia" Moremi (Botswana, Simbabwe & Namibia) und fuer "Kapstadt, Gartenroute,
Krueger" Pinotage, das den Krueger nicht enthaelt. Beides stand auf den Seiten;
Leon hat es in 18.000 bzw. 75.000 Zeichen nicht gefunden.

    pytest tests/test_reiseliste.py -v
"""

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
from agent_base import ort_im_text, reiseliste, website_tool_multi

# So schreibt markdownify einen Teaser der Laenderseite (gezogen 2026-09-25
# ueber /Afrika/Namibia, gekuerzt).
LAENDERSEITE = """# Namibia Urlaub

  [Namibia

  ### Sossusvlei **14 Tage Erlebnisreise**

  + 5 Safaris

  ![Details zu Sossusvlei](/html/img/btn.svg)](/Afrika/Namibia/Sossusvlei "Sossusvlei - Namibia")
* Bild

  ### Moremi **14 Tage Erlebnisreise**

  + Okavango-Delta

  ![Details zu Moremi](/html/img/btn.svg)](/Afrika/Botswana-Namibia/Moremi "Moremi - Botswana, Simbabwe & Namibia")
"""


def _reiseseite(verlauf: str) -> str:
    return f"Herzklopf-Momente\n---\n\nx\n\nReiseverlauf\n------------\n\n{verlauf}\n\nLeistungen\n----------\n\ny"


SEITEN = {
    "/Afrika/Namibia": ("Namibia Urlaub", LAENDERSEITE),
    "/Afrika/Suedafrika/Outeniqua": (
        "Outeniqua - 15 Tage Erlebnisreise | Südafrika | Chamäleon",
        _reiseseite("Johannesburg, Krüger-Nationalpark, Garden Route, Kapstadt."),
    ),
    "/Afrika/Suedafrika/Pinotage-ALL": (
        "Pinotage - 17 Tage Erlebnisreise | Südafrika | Chamäleon",
        _reiseseite("Kapstadt, Garden Route, Addo."),
    ),
    "/Afrika/Suedafrika-Eswatini-Lesotho/Panorama-ALL": (
        "Panorama - 20 Tage Erlebnisreise | Südafrika, Eswatini und Lesotho | Chamäleon",
        _reiseseite("Krüger, Garden Route, Kapstadt."),
    ),
}


@pytest.fixture(autouse=True)
def seiten(monkeypatch):
    import travel_index

    monkeypatch.setattr(travel_index, "get_termine_markdown", lambda _url: "")
    monkeypatch.setattr(agent_base, "seite_markdown", lambda pfad: SEITEN[pfad])


def test_reiseliste_liest_name_dauer_laender_pfad():
    assert reiseliste(LAENDERSEITE) == [
        {"name": "Sossusvlei", "tage": 14, "laender": "Namibia", "pfad": "/Afrika/Namibia/Sossusvlei"},
        {
            "name": "Moremi",
            "tage": 14,
            "laender": "Botswana, Simbabwe & Namibia",
            "pfad": "/Afrika/Botswana-Namibia/Moremi",
        },
    ]


def test_laenderseite_bekommt_die_liste_mit_kombireise_markiert():
    out = website_tool_multi(["/Afrika/Namibia"])
    assert "- Sossusvlei · 14 Tage · Namibia · /Afrika/Namibia/Sossusvlei" in out
    assert "- Moremi · 14 Tage · Kombireise: Botswana, Simbabwe & Namibia" in out


def test_abschnitt_auf_laenderseite_liefert_die_liste_statt_nichts():
    out = website_tool_multi(["/Afrika/Namibia"], "reiseverlauf")
    assert "/Afrika/Namibia/Sossusvlei" in out


@pytest.mark.parametrize(
    "ort, gefunden",
    [("Krüger", True), ("Krueger", True), ("Kruger", True), ("Gartenroute|Garden Route", True),
     ("Gartenroute", False), ("Sansibar", False)],
)
def test_ort_im_text(ort, gefunden):
    assert ort_im_text(ort, "Safari im Krüger-Nationalpark, dann die Garden Route.") is gefunden


def test_ortspruefung_nennt_die_reisen_mit_allen_orten_und_reine_zuerst():
    out = website_tool_multi(
        [
            "/Afrika/Suedafrika-Eswatini-Lesotho/Panorama-ALL",
            "/Afrika/Suedafrika/Pinotage-ALL",
            "/Afrika/Suedafrika/Outeniqua",
        ],
        orte=["Kapstadt", "Gartenroute|Garden Route", "Krüger"],
    )
    kopf = out.split("\n")[1]
    assert kopf.startswith("Alle gewünschten Orte im Reiseverlauf: ")
    assert "Outeniqua (/Afrika/Suedafrika/Outeniqua)" in kopf
    assert "Panorama, Kombireise" in kopf
    assert "Pinotage" not in kopf
    assert "Pinotage (/Afrika/Suedafrika/Pinotage-ALL): Kapstadt ja · Gartenroute ja · Krüger nicht gefunden" in out
    # Seiten: reine Reisen vor der Kombireise, sonst in Anfragereihenfolge
    assert out.index("# /Afrika/Suedafrika/Pinotage-ALL") < out.index("# /Afrika/Suedafrika/Outeniqua")
    assert out.index("# /Afrika/Suedafrika/Outeniqua") < out.index("# /Afrika/Suedafrika-Eswatini-Lesotho/Panorama-ALL — Kombireise:")
