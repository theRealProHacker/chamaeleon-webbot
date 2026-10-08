import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import faq_sync
from faq_sync import (
    LAENDER,
    _mit_a,
    land_zeilen,
    laender,
    parse_infos,
    parse_laender_csv,
    parse_md,
    render,
    render_beide,
)


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


def test_laender_csv_rundlauf():
    # Der Import nach Supabase darf von den Laender-CSVs nichts verlieren.
    daten, _ = parse_laender_csv()
    assert set(daten) == set(LAENDER)
    faqs, neu = laender(land_zeilen(daten))
    # Reihenfolge zaehlt: Tool-Beschreibung und Prompt zeigen sie so.
    assert list(faqs) == list(LAENDER)
    assert [list(neu[land].items()) for land in daten] == [list(f.items()) for f in daten.values()]


def test_laender_ohne_frage_bleiben():
    faqs, daten = laender(land_zeilen({"Namibia": {"Visum?": "Ja."}}))
    assert faqs["Namibia"] == "# Namibia\n\n## Visum?\n\nJa."
    assert faqs["USA"] == "# USA" and daten["USA"] == {}


def test_render_beide_ohne_laender():
    rows = parse_md("## Kataloge\n\n**F: Wo?**\nA: Hier.") + land_zeilen({"Namibia": {"Visum?": "Ja."}})
    assert render_beide(rows) == ("## Kataloge\n\n**F: Wo?**\nA: Hier.",) * 2


class _Abfrage:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self.rows})


def _load(monkeypatch, rows):
    monkeypatch.setattr(faq_sync, "_supabase", lambda: type("S", (), {"table": lambda self, t: _Abfrage(rows)})())
    for name in ("allgemeine_faqs", "allgemeine_faqs_agentur", "laender_faqs", "laender_faq_data"):
        monkeypatch.setattr(agent_base, name, getattr(agent_base, name))
    assert faq_sync.load()


def test_load_ohne_land_zeilen_behaelt_laender(monkeypatch):
    # Vor dem Import: website/intern kommen an, die Laender-FAQs bleiben stehen.
    vorher = agent_base.laender_faqs
    _load(monkeypatch, parse_md("## Kataloge\n\n**F: Neu?**\nA: Ja."))
    assert agent_base.allgemeine_faqs == "## Kataloge\n\n**F: Neu?**\nA: Ja."
    assert agent_base.laender_faqs is vorher


def test_load_meldet_unbekannte_laender(monkeypatch):
    rows = parse_md("## K\n\n**F: A?**\nA: B.") + land_zeilen({"Namibia ": {"Visum?": "Ja."}})
    _load(monkeypatch, rows)
    assert faq_sync.status["load"]["unbekannte_laender"] == ["Namibia "]
    assert agent_base.laender_faqs["Namibia "].startswith("# Namibia ")


def test_laender_csv_gruppen_verschlucken_nichts():
    # Bis 2026-10 setzte "Chile/Bolivien/Peru" Chile und Peru zurueck und
    # versteckte so 10 Fragen; eine Gruppen-Kopfzeile ergaenzt nur.
    daten, _ = parse_laender_csv()
    assert daten["Argentinien"].items() <= daten["Chile"].items()
    assert daten["Bolivien"].items() <= daten["Peru"].items()
    assert "Wie anspruchsvoll sind die Wanderungen ?" in daten["Albanien"]


def test_laender_csv_rest_landet_ausgeblendet():
    # Alles mit Text kommt nach Supabase; was keine ganze Frage-Antwort-Zeile ist,
    # ausgeblendet. Visum bleibt mit Warnung draussen.
    _, versteckt = parse_laender_csv()
    texte = {(land, frage) for land, frage, _ in versteckt}
    assert ("Island", "Wie funktioniert die Fahrt mit dem Flybus?") in texte
    assert ("Lettland", "Kombireise mit Estland und Litauen, siehe Estland") in texte
    assert not any(f.startswith(("Nr.", "Reisenspezifische")) for _, f, _ in versteckt)
    visum = [a for _, f, a in versteckt if "visum" in f.lower()]
    assert len(visum) == 2 and all(a == faq_sync.VISUM_HINWEIS for a in visum)
