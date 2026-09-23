"""termine_tool_base — „noch nicht veröffentlicht" statt „keine freien Termine".

Ohne Netz und ohne Modell: get_reisecodes und der gecachte Feed-Abruf sind
ersetzt, die echte Filterung in travel_index laeuft mit.

Befund F5 (2026-09-20): eine Kundin fragte „Termine Januar 28" zur
Uluru-Reise und bekam „keine freien Termine verfügbar" — das liest sich als
ausgebucht. Die Trennlinie ist genau eine Frage: liegt das gefragte Jahr
(bzw. Jahr+Monat) hinter der letzten veröffentlichten Abreise dieser Reise?

    pytest tests/test_termine_tool.py -v
"""

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import travel_index


PFAD = "/Ozeanien/Australien/Uluru"

# Eine Reise, die bis November 2027 veröffentlicht ist. Der Maerz-Termin ist
# ausgebucht (vakanzSync 0) — das ist der Fall, der weiterhin die alte,
# belastbare Auskunft bekommen muss.
TERMINE = (
    {"von": "2027-03-10 00:00:00", "bis": "2027-03-23 00:00:00",
     "status": "OK", "vakanzSync": 0, "abPreis": 4999},
    {"von": "2027-11-05 00:00:00", "bis": "2027-11-18 00:00:00",
     "status": "OK", "vakanzSync": 6, "abPreis": 5199},
)


@pytest.fixture
def reise(monkeypatch):
    """Eine indexierte Reise mit TERMINE; aendert sich per `setzen(...)`."""
    feed = {"termine": TERMINE}
    monkeypatch.setattr(travel_index, "get_reisecodes", lambda url: ["AUULU"])
    monkeypatch.setattr(
        travel_index, "_fetch_termine_filtered", lambda codes: feed["termine"]
    )
    return feed


def test_jahr_hinter_dem_letzten_termin_ist_nicht_veroeffentlicht(reise):
    """Der F5-Fall: Januar 2028, letzte Abreise November 2027."""
    out = agent_base.termine_tool_base(PFAD, jahr=2028, monat=1)

    assert "noch keine Termine veröffentlicht" in out
    assert "November 2027" in out  # bis wohin veröffentlicht ist
    assert f"{PFAD}#termine" in out
    assert "Keine Termine für" not in out
    assert "belastbar" not in out
    assert "leider" not in out.lower()


def test_jahr_ohne_monat_reicht_fuer_die_aussage(reise):
    out = agent_base.termine_tool_base(PFAD, jahr=2028)

    assert "noch keine Termine veröffentlicht" in out


def test_monat_hinter_der_letzten_abreise_im_selben_jahr(reise):
    """Dezember 2027 liegt hinter November 2027 — auch das ist noch nichts."""
    out = agent_base.termine_tool_base(PFAD, jahr=2027, monat=12)

    assert "noch keine Termine veröffentlicht" in out


def test_ausgebuchter_monat_behaelt_die_alte_auskunft(reise):
    """Maerz 2027 ist veröffentlicht und voll — das ist wirklich ausgebucht."""
    out = agent_base.termine_tool_base(PFAD, jahr=2027, monat=3, nur_freie=True)

    assert out == (
        f"Keine Termine für {PFAD} (März 2027, nur freie). Diese Auskunft ist "
        "belastbar. Frage ohne Filter erneut ab, um Alternativen zu nennen."
    )


def test_reise_ganz_ohne_termine_behaelt_die_alte_auskunft(reise):
    """Aus einer leeren Liste laesst sich über die Veröffentlichung nichts
    schliessen — eine erfundene Zuversicht waere schlimmer als heute."""
    reise["termine"] = ()
    out = agent_base.termine_tool_base(PFAD, jahr=2028)

    assert "noch keine Termine veröffentlicht" not in out
    assert "Diese Auskunft ist belastbar" in out


def test_monat_ohne_jahr_bleibt_bei_der_alten_auskunft(reise):
    """Ohne Jahr ist die Frage „liegt das dahinter?" nicht zu beantworten."""
    out = agent_base.termine_tool_base(PFAD, monat=6)

    assert "noch keine Termine veröffentlicht" not in out
    assert "Diese Auskunft ist belastbar" in out


def test_vergangenes_jahr_ist_nicht_unveroeffentlicht(reise):
    out = agent_base.termine_tool_base(PFAD, jahr=2025)

    assert "noch keine Termine veröffentlicht" not in out


def test_nach_letzter_abreise():
    from agent_base import _nach_letzter_abreise

    letzte = (2027, 11)
    assert _nach_letzter_abreise(2028, 1, letzte)
    assert _nach_letzter_abreise(2028, None, letzte)
    assert _nach_letzter_abreise(2027, 12, letzte)
    assert not _nach_letzter_abreise(2027, 11, letzte)
    assert not _nach_letzter_abreise(2027, None, letzte)
    assert not _nach_letzter_abreise(2026, 12, letzte)
    # Monat 0/13 kommt vom Modell und bedeutet „kein Monatsfilter"
    assert not _nach_letzter_abreise(2027, 13, letzte)


def test_letzte_abreise_bei_kaputter_abfrage_ist_unbekannt(monkeypatch):
    """Ein Ausfall darf nie zu „noch nicht veröffentlicht" werden."""
    from agent_base import _letzte_abreise

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(travel_index, "query_termine", boom)
    assert _letzte_abreise(PFAD) is None
