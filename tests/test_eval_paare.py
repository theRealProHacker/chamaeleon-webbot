"""Unit-Tests fuer den Treiber der verschraenkten Messung — ohne Netz, ohne Modell."""

from pathlib import Path

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent
import agent_base
import eval_paare as ep


def test_schneiden_ergibt_die_aktuelle_vorlage():
    """R6: aus dem Quelltext geschnitten und ausgewertet == was der Import baute."""
    quelltext = (Path(ep.REPO) / "agent_base.py").read_text(encoding="utf-8")
    assert ep.vorlage_aus_quelle(quelltext, vars(agent_base)) == agent_base.system_prompt_template


def test_gleiche_vorlagen_brechen_vor_dem_ersten_aufruf_ab():
    """R5: b = c = 0 aus einem kaputten Tausch darf nie als Ergebnis dastehen."""
    vorlage = agent_base.system_prompt_template
    with pytest.raises(SystemExit):
        ep.tausch_nachweis(vorlage, vorlage)


def test_verschiedene_vorlagen_ergeben_zwei_hashes():
    nachweis = ep.tausch_nachweis(agent_base.system_prompt_template,
                                  agent_base.system_prompt_template + "\nX")
    assert nachweis["prompt_alt_sha256"] != nachweis["prompt_neu_sha256"]


def _fall(lauf):
    return {"id": "t", "block": "filter", "vorbereiten": lambda: None, "lauf": lauf}


def _antwort(text):
    """Ein Lauf, der wie agent.call eine Antwort in den Mitschnitt legt."""
    def lauf():
        ep._mitschnitt["antworten"].append(text)
    return lauf


def test_assertion_ist_rot_mit_befund_art():
    def lauf():
        # So wirft der Treiber (`assert not befunde, befunde`); im Testmodul
        # schriebe pytest ein assert um und aenderte die Meldung.
        raise AssertionError(["7 Saetze (hoechstens 5)"])
    ergebnis = ep.fahre_seite(_fall(lauf), "V")
    assert ergebnis["ausgang"] == "rot"
    assert ergebnis["befund_art"] == "Laenge"


def test_skip_ist_fehler_nicht_rot():
    ergebnis = ep.fahre_seite(_fall(lambda: pytest.skip("keine Umgebung")), "V")
    assert ergebnis["ausgang"] == "fehler"


def test_netzausnahme_ist_fehler():
    def lauf():
        raise ConnectionError("TourOne weg")
    assert ep.fahre_seite(_fall(lauf), "V")["ausgang"] == "fehler"


def test_leerantwort_wird_genau_einmal_wiederholt():
    """R1: bleibt sie leer, ist die Seite leer — egal, ob die Pruefung bestand."""
    aufrufe = []

    def lauf():
        aufrufe.append(1)
        ep._mitschnitt["antworten"].append(agent.EMPTY_ANSWER_FALLBACK)
    ergebnis = ep.fahre_seite(_fall(lauf), "V")
    assert len(aufrufe) == 2
    assert ergebnis["ausgang"] == "leer" and ergebnis["leer_wiederholt"]


def test_leerantwort_die_sich_erholt_zaehlt_normal():
    antworten = iter([agent.EMPTY_ANSWER_FALLBACK, "Mit Discover Airlines."])

    def lauf():
        ep._mitschnitt["antworten"].append(next(antworten))
    ergebnis = ep.fahre_seite(_fall(lauf), "V")
    assert ergebnis["ausgang"] == "gruen" and ergebnis["leer_wiederholt"]


def test_vorlage_gilt_nur_waehrend_der_seite():
    gesehen = []
    vorher = agent_base.system_prompt_template
    ep.fahre_seite(_fall(lambda: gesehen.append(agent_base.system_prompt_template)), "NEU")
    assert gesehen == ["NEU"]
    assert agent_base.system_prompt_template == vorher


def test_zaehlender_stream_zaehlt_tool_aufrufe():
    """R7: zwei tool_call-Events im Stream = zwei Tool-Aufrufe im Protokoll."""
    def falscher_stream(*_args, **_kwargs):
        yield {"type": "tool_call", "data": {"name": "a"}}
        yield {"type": "tool_call", "data": {"name": "b"}}
        yield {"type": "response", "data": {"reply": "ok"}}

    stream = ep._zaehlender_stream(falscher_stream)
    ep._mitschnitt["tools"], ep._mitschnitt["antworten"] = 0, []
    assert [e["type"] for e in stream()][-1] == "response"
    assert ep._mitschnitt["tools"] == 2
    assert ep._mitschnitt["antworten"] == ["ok"]


def test_agentur_hat_31_faelle_plus_einen_mit_nummer(monkeypatch):
    monkeypatch.delenv("AGENTUR_TEST_NUMMER", raising=False)
    assert len(ep.faelle("agentur")) == 31
    monkeypatch.setenv("AGENTUR_TEST_NUMMER", "12345")
    assert len(ep.faelle("agentur")) == 32


def test_meinchamaeleon_hat_13_faelle():
    assert len(ep.faelle("meinchamaeleon")) == 13


def test_teilung_und_stuecke():
    assert ep.teile(28) == 3 and ep.teile(12) == 2 and ep.teile(8) == 1
    liste = list(range(10))
    assert sum((ep.stueck(liste, t, 3) for t in (1, 2, 3)), []) == liste


def _seite(fall, seite, ausgang, art=""):
    return {"typ": "seite", "block": "airline", "fall": fall, "seite": seite,
            "ausgang": ausgang, "befund_art": art, "befunde": ["x"] if ausgang == "rot" else [],
            "leer_wiederholt": ausgang == "leer", "tool_aufrufe": 1, "dauer_s": 2.0}


def test_auswertung_trennt_b_c_leer_und_fehler():
    """R8/R10: Paare mit leer oder Fehler fallen aus b/c, werden aber gezaehlt."""
    zeilen = [
        _seite("b1", "alt", "rot"), _seite("b1", "neu", "gruen"),
        _seite("c1", "alt", "gruen"), _seite("c1", "neu", "rot", "Inhalt"),
        _seite("g", "alt", "gruen"), _seite("g", "neu", "gruen"),
        _seite("l", "alt", "gruen"), _seite("l", "neu", "leer"),
        _seite("f", "alt", "fehler"), _seite("f", "neu", "rot", "Laenge"),
    ]
    e = ep.auswerten(zeilen)["airline"]
    assert (e["faelle"], e["b"], e["c"], e["beide_gruen"], e["ausgefallen"]) == (5, 1, 1, 1, 2)
    assert (e["leer_neu"], e["fehler_alt"]) == (1, 1)
    assert e["c_faelle"][0][:2] == ("c1", "Inhalt")
    assert e["p"] == 1.0


def test_mcnemar_bei_c_null():
    """Plan-Arithmetik (R10): bei c=0 erst b=6 unter 0,05."""
    assert ep.mcnemar_p(5, 0) == pytest.approx(0.0625)
    assert ep.mcnemar_p(6, 0) == pytest.approx(0.03125)


def test_d13_zaehlt_reine_satzzahl_nicht():
    zeilen = []
    for fall, art in (("a", "Laenge"), ("b", "Laenge"), ("c", "Inhalt")):
        zeilen += [{**_seite(fall, "alt", "gruen"), "block": "filter"},
                   {**_seite(fall, "neu", "rot", art), "block": "filter"}]
    assert not ep.d13_ausloeser(ep.auswerten(zeilen))
    zeilen += [{**_seite("d", "alt", "rot"), "block": "filter"},
               {**_seite("d", "neu", "rot", "Link"), "block": "filter"}]
    assert ep.d13_ausloeser(ep.auswerten(zeilen))
