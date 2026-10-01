"""Reiseinfo-Tool: die „Wichtigen Informationen“ zu einer gebuchten Reise.

Getestet wird die Tool-Schicht über ``reiseinfo_tools`` (Formatierung, die drei
Fälle unbekannt/leer/Ausfall, der Schutz der Vorgangsnummer) und die Bindung im
Agenten. TourOne wird gepatcht — kein Live-Request, kein Modell-Aufruf.
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import pytest

import agent
import agent_base as ab
import travel_index

VORGANG = "226177"

REISE = {
    "land2": {"iso2Liste": "CN,HK"},
    "zusatzfelder": {
        "INFOALLLAND": {"label": "Info alle", "value": "INFO-ALLE"},
        "INFOLAND": {"label": "Info Land", "value": "INFO-CN"},
        # Fließfeld-Müll: darf nie als Textcode durchgehen.
        "INFOZUSATZ": {"label": "Zusatz", "value": " kein Code, nur Text "},
    },
}

BAUSTEINE = {
    "HIN-CN": {"bezeichnung": "Hinweis China", "text": "<p>Wer holt mich <b>ab</b>?</p>"},
    "CHECK-CN": {"bezeichnung": "Checkliste China", "text": "<ul><li>Reisepass</li></ul>"},
    "INFO-ALLE": {"bezeichnung": "Info für alle Länder", "text": "<p>Trinkgeld: 5 €</p>"},
    "INFO-CN": {"bezeichnung": "Info China", "text": "<p>Devisen</p>"},
}


class _HTTPFehler(Exception):
    """requests-artiger Fehler: der Statuscode hängt an ``response``."""

    def __init__(self, status: int):
        super().__init__(f"{status} Error")
        self.response = type("R", (), {"status_code": status})()


def fake_tourone(
    monkeypatch,
    buchung=None,
    reise=None,
    bausteine=None,
    fehler=None,
    baustein_fehler=None,
):
    """Patcht travel_index._tourone_get (agent_base ruft es über das Modul auf).

    Ein FEHLENDER Textbaustein antwortet wie die echte API mit HTTP 404 — das
    heißt „diesen Block hat das Land nicht“. ``baustein_fehler`` bildet dagegen
    den AUSFALL nach (Timeout/500), der genau nicht so gelesen werden darf.
    """
    calls = []

    def fake(path, params, timeout=20):
        calls.append({"path": path, "params": dict(params)})
        if fehler:
            raise fehler
        if path == "/get/buchung":
            # Treffer → Objekt, unbekannte Nummer → leere Liste (beides HTTP 200).
            return buchung if buchung is not None else {"reiseCode": "CNZHA_NEU"}
        if path == "/get/reise":
            return reise if reise is not None else REISE
        if baustein_fehler:
            raise baustein_fehler
        code = params["textcode"]
        quelle = BAUSTEINE if bausteine is None else bausteine
        if code not in quelle:
            raise _HTTPFehler(404)
        return quelle[code]

    monkeypatch.setattr(travel_index, "_tourone_get", fake)
    return calls


def eigene(monkeypatch, *nummern):
    """Der Kunde besitzt genau diese Buchungen (in dieser Reihenfolge)."""
    monkeypatch.setattr(
        "kundendaten.vorgangsnummern", lambda kunden_id: list(nummern)
    )


# --- die drei Fälle, die für den Kunden verschieden sind ----------------------


def test_unbekannte_buchungsnummer(monkeypatch):
    eigene(monkeypatch, "999999999")
    fake_tourone(monkeypatch, buchung=[])
    assert (
        ab.reiseinfo_tool_base("999999999", kunden_id="472325")
        == ab.REISEINFO_UNBEKANNT_TEXT
    )


def test_totalausfall_ist_kein_leeres_ergebnis(monkeypatch):
    """Ein API-Ausfall darf nie als „dazu gibt es nichts“ beim Kunden ankommen."""
    eigene(monkeypatch, VORGANG)
    fake_tourone(monkeypatch, fehler=RuntimeError("boom"))
    assert ab.reiseinfo_tool_base(kunden_id="472325") == ab.REISEINFO_FEHLER_TEXT


def test_teilausfall_der_bausteine_ist_kein_leeres_ergebnis(monkeypatch):
    """Der Fall, der wirklich passiert: Buchung und Reise antworten, die 20
    nebenläufigen Baustein-Requests laufen ins Timeout. Ohne Unterscheidung
    kam das als „zu dieser Reise ist nichts hinterlegt“ beim Kunden an."""
    eigene(monkeypatch, VORGANG)
    fake_tourone(monkeypatch, baustein_fehler=TimeoutError("read timeout"))
    assert ab.reiseinfo_tool_base(kunden_id="472325") == ab.REISEINFO_FEHLER_TEXT


def test_teilweise_geladen_wird_als_lueckenhaft_ausgewiesen(monkeypatch):
    """Ein Baustein fällt aus, die übrigen kommen: liefern, aber sagen dass es
    lückenhaft ist — sonst liest sich die Lücke wie Vollständigkeit."""
    eigene(monkeypatch, VORGANG)

    echt = ab._lade_textbaustein

    def waehlerisch(code):
        if code == "CHECK-CN":
            raise TimeoutError("read timeout")
        return echt(code)

    fake_tourone(monkeypatch)
    monkeypatch.setattr(ab, "_lade_textbaustein", waehlerisch)
    text = ab.reiseinfo_tool_base(kunden_id="472325")
    assert "# Reisehinweise" in text, "die geladenen Blöcke müssen bleiben"
    assert "Checkliste" not in text
    assert "nicht laden" in text, "die Lücke muss benannt werden"


def test_reise_ohne_bausteine(monkeypatch):
    """404 auf alle Bausteine heißt wirklich „gibt es nicht“ — kein Ausfall."""
    eigene(monkeypatch, VORGANG)
    fake_tourone(monkeypatch, bausteine={})
    assert ab.reiseinfo_tool_base(kunden_id="472325") == ab.REISEINFO_LEER_TEXT


# --- die Vorgangsnummer kommt aus der Modellantwort ---------------------------


def test_fremde_buchungsnummer_wird_nicht_beantwortet(monkeypatch):
    """Der Kern der Ownership-Prüfung: eine syntaktisch gültige Nummer, die dem
    Kunden NICHT gehört, darf kein Enumerations-Orakel sein. Sie fällt still auf
    die eigene Reise zurück — die fremde wird nie abgefragt."""
    eigene(monkeypatch, VORGANG)
    calls = fake_tourone(monkeypatch)
    text = ab.reiseinfo_tool_base("226999", kunden_id="472325")
    gefragt = [c["params"].get("vorgangsNummer") for c in calls if "vorgangsNummer" in c["params"]]
    assert gefragt == [VORGANG], f"fremde Nummer erreichte die API: {gefragt}"
    assert "226999" not in text


def test_eigene_zweitreise_ist_weiter_waehlbar(monkeypatch):
    """Die Prüfung darf den legitimen Fall nicht kaputtmachen: eine ANDERE
    eigene Buchung als die nächste bleibt adressierbar."""
    eigene(monkeypatch, VORGANG, "300000")
    calls = fake_tourone(monkeypatch)
    ab.reiseinfo_tool_base("300000", kunden_id="472325")
    gefragt = [c["params"].get("vorgangsNummer") for c in calls if "vorgangsNummer" in c["params"]]
    assert gefragt == ["300000"]


def test_muell_erreicht_die_api_nie(monkeypatch):
    eigene(monkeypatch, VORGANG)
    calls = fake_tourone(monkeypatch)
    for eingabe in ("226177 OR 1=1", "226177/../adresse"):
        # Fällt auf die eigene Reise zurück — der Müll selbst geht nie raus.
        ab.reiseinfo_tool_base(eingabe, kunden_id="472325")
    gefragt = {c["params"].get("vorgangsNummer") for c in calls if "vorgangsNummer" in c["params"]}
    assert gefragt == {VORGANG}, f"Müll erreichte die API: {gefragt}"


def test_ohne_buchung_wird_gefragt_statt_geraten(monkeypatch):
    eigene(monkeypatch)  # Kunde ohne jede Buchung
    calls = fake_tourone(monkeypatch)
    assert (
        ab.reiseinfo_tool_base(kunden_id="472325") == ab.REISEINFO_OHNE_BUCHUNG_TEXT
    )
    assert calls == []


def test_ausfall_der_buchungsliste_ist_keine_rueckfrage(monkeypatch):
    """Fällt der Adress-Lookup aus, ist das eine Störung — nicht „welche
    Buchung meinst du?“. Der Kunde soll nicht nach einer Nummer suchen, die
    ihm gerade sowieso nicht weiterhilft."""
    monkeypatch.setattr("kundendaten.vorgangsnummern", lambda kunden_id: None)
    calls = fake_tourone(monkeypatch)
    assert ab.reiseinfo_tool_base(kunden_id="472325") == ab.REISEINFO_FEHLER_TEXT
    assert calls == []


# --- welche Buchung ist gemeint? ----------------------------------------------


def test_reihenfolge_der_aufloesung(monkeypatch):
    """Modellangabe schlägt offene Seite, offene Seite schlägt nächste Reise —
    aber alle drei nur aus dem eigenen Bestand."""
    eigene(monkeypatch, "300000", "111", "222")
    aufloesen = ab.reiseinfo_vorgang
    assert aufloesen("111", seiten_vorgang="222", kunden_id="k") == ("111", "")
    assert aufloesen("", seiten_vorgang="222", kunden_id="k") == ("222", "")
    assert aufloesen("", seiten_vorgang="", kunden_id="k") == ("300000", "")
    # Fremde Nummern in beiden Slots → die eigene nächste Reise.
    assert aufloesen("999", seiten_vorgang="888", kunden_id="k") == ("300000", "")


def test_agentur_nummern_als_int_kippen_das_tool_nicht(monkeypatch):
    """TourOne mischt die Typen (vorgangsNummer str, vorgangsId int). Als int
    fiele die eigene Nummer durch die Prüfung, und der Fallback flöge dann in
    der Regex — außerhalb des try/except, also trotz „Wirft nie“."""
    import agenturdaten as ad

    monkeypatch.setattr(
        ad, "_agentur_get", lambda path, params: {"0": {"roh": True}}
    )
    monkeypatch.setattr(
        ad, "_normalise_row", lambda row, agentur_id: {"vorgang": 500001}
    )
    assert ad.vorgangsnummern("12345") == ["500001"], "muss str sein"

    fake_tourone(monkeypatch)
    # Darf nicht werfen und muss die eigene Nummer akzeptieren.
    assert "# Reisehinweise" in ab.reiseinfo_tool_base("500001", agentur_id="12345")


def test_kein_lookup_fehler_leakt_die_vorgangsnummer(monkeypatch, capsys):
    """Die requests-Exception trägt die volle URL inklusive vorgangsNummer."""
    eigene(monkeypatch, "226177")
    fake_tourone(
        monkeypatch,
        fehler=RuntimeError(
            "404 for url: https://api.tourone.de/get/buchung?vorgangsNummer=226177"
        ),
    )
    assert ab.reiseinfo_tool_base(kunden_id="472325") == ab.REISEINFO_FEHLER_TEXT
    assert "226177" not in capsys.readouterr().out


def test_agentur_pruegt_gegen_ihre_eigenen_buchungen(monkeypatch):
    """Der Agenturpfad hat keine „nächste Reise“, aber dieselbe Grenze:
    /get/buchung kennt keinen Agenturfilter, die Prüfung ist die einzige
    Bindung an die Agentur."""
    monkeypatch.setattr("agenturdaten.vorgangsnummern", lambda agentur_id: ["500001"])
    calls = fake_tourone(monkeypatch)
    ab.reiseinfo_tool_base("999999", agentur_id="12345")
    gefragt = [c["params"].get("vorgangsNummer") for c in calls if "vorgangsNummer" in c["params"]]
    assert gefragt == ["500001"], f"fremde Nummer erreichte die API: {gefragt}"


def test_naechste_reise_ohne_zutun_des_modells(monkeypatch):
    """Der Kunden-Pfad braucht kein Argument — genau das entfernt den zweiten
    Tool-Aufruf, in dem Gemini die Antwort ankündigte statt sie zu geben."""
    eigene(monkeypatch, VORGANG)
    fake_tourone(monkeypatch)
    assert "# Checkliste" in ab.reiseinfo_tool_base(kunden_id="472325")


# --- Inhalt -------------------------------------------------------------------


def test_bloecke_und_markdown(monkeypatch):
    eigene(monkeypatch, VORGANG)
    calls = fake_tourone(monkeypatch)
    text = ab.reiseinfo_tool_base(VORGANG, kunden_id="472325")

    assert "# Reisehinweise" in text
    assert "# Checkliste" in text
    assert "## Info für alle Länder" in text
    # Der API-Text ist HTML; das Modell bekommt Markdown.
    assert "<b>" not in text and "<li>" not in text
    assert "**ab**" in text and "* Reisepass" in text
    # Leere Blöcke fehlen: es gibt keinen FLUG-CN-INL-Baustein.
    assert "Inlands- und Regionalflüge" not in text
    # Der Müll-Zusatzfeldwert wurde nie als Textcode abgefragt.
    codes = [c["params"].get("textcode") for c in calls if c["path"] == "/get/textbaustein"]
    assert all(c and c.strip() == c for c in codes), codes
    assert "HIN-HK" in codes, "das zweite Zielland muss mitgezogen werden"


# --- die Prüfliste selbst ------------------------------------------------------


def _adresse(monkeypatch, ergebnis):
    """kundendaten hat _tourone_get modulweit importiert — dort patchen."""
    import kundendaten as kd

    def fake(path, params, timeout=20):
        if isinstance(ergebnis, Exception):
            raise ergebnis
        return ergebnis

    monkeypatch.setattr(kd, "_tourone_get", fake)
    # Ein neuer Fake heißt: ab hier gilt eine andere TourOne-Antwort. Der
    # Buchungs-Cache (10 min, Schlüssel nur die Kundennummer) überlebte sonst
    # den vorigen Test und der Fake käme gar nicht zum Zug.
    kd._buchungen_roh.cache_clear()


def test_vorgangsnummern_sortiert_kommende_nach_vorn(monkeypatch):
    """Das erste Element ist die Reise, die der Kunde meint. Stumpf nach vonDat
    absteigend stünde die am weitesten entfernte oben — der Fehler, den
    kundendaten.select ausdrücklich vermeidet."""
    import kundendaten as kd

    _adresse(
        monkeypatch,
        {
            "buchungen": [
                {"vorgang": "alt", "vonDat": "2020-01-01", "bisDat": "2020-01-15"},
                {"vorgang": "fern", "vonDat": "2099-08-01", "bisDat": "2099-08-15"},
                {"vorgang": "naechste", "vonDat": "2099-01-01", "bisDat": "2099-01-15"},
            ]
        },
    )
    assert kd.vorgangsnummern("472325") == ["naechste", "fern", "alt"]


def test_vorgangsnummern_trennt_ausfall_von_leer(monkeypatch):
    import kundendaten as kd

    _adresse(monkeypatch, RuntimeError("boom"))
    assert kd.vorgangsnummern("472325") is None, "Ausfall darf nicht [] sein"
    _adresse(monkeypatch, {"buchungen": []})
    assert kd.vorgangsnummern("472325") == []
    _adresse(monkeypatch, [])  # unbekannte Kundennummer
    assert kd.vorgangsnummern("472325") == []


def test_vorgangsnummern_loggt_keine_kundennummer(monkeypatch, capsys):
    """Die requests-Exception trägt die volle URL inklusive kundennummer."""
    import kundendaten as kd

    _adresse(
        monkeypatch,
        RuntimeError("404 for url: https://api.tourone.de/get/adresse?kundennummer=472325"),
    )
    kd.vorgangsnummern("472325")
    assert "472325" not in capsys.readouterr().out


# --- Bindung im Agenten -------------------------------------------------------


def _gebundene_tools(monkeypatch, endpoint="/", **kwargs) -> list[str]:
    """Die Namen der Tools, die call_stream wirklich bindet."""
    namen = []

    class _Executor:
        def stream(self, _state, stream_mode="values"):
            yield {"messages": []}

    def spion(model, tools):
        namen.extend(t.name for t in tools)
        return _Executor()

    monkeypatch.setattr(agent, "create_react_agent", spion)
    list(
        agent.call_stream(
            [{"role": "user", "content": "Was packe ich ein?"}], endpoint, **kwargs
        )
    )
    return namen


def test_ohne_login_kein_reiseinfo_tool(monkeypatch):
    """Ohne Buchung gibt es keine Reiseinfos — und ein anonymer Besucher könnte
    sonst raten, zu welchem Land eine fremde Buchungsnummer gehört."""
    assert "reiseinfo_tool" not in _gebundene_tools(monkeypatch)


def test_kunde_und_agentur_bekommen_das_tool(monkeypatch):
    assert "reiseinfo_tool" in _gebundene_tools(monkeypatch, kunden_id="472207")
    assert "reiseinfo_tool" in _gebundene_tools(monkeypatch, agentur_id="12345")


def test_offene_reise_kommt_aus_der_url_nicht_vom_modell(monkeypatch):
    """Welche Buchung gemeint ist, bindet der Server aus der URL — das Modell
    muss sie weder nennen noch vorher nachschlagen."""
    gebaut = {}

    def spion(seiten_vorgang="", kunden_id="", agentur_id=""):
        gebaut.update(
            seiten_vorgang=seiten_vorgang, kunden_id=kunden_id, agentur_id=agentur_id
        )
        return agent.termine_tool  # irgendein Tool-Objekt, hier egal

    monkeypatch.setattr(agent, "make_reiseinfo_tool", spion)
    _gebundene_tools(
        monkeypatch,
        endpoint="https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise?VRRVORGANG=226291",
        kunden_id="472207",
    )
    assert gebaut == {
        "seiten_vorgang": "226291",
        "kunden_id": "472207",
        "agentur_id": "",
    }


# --- Quelle „auto“: Reiseunterlagen → Reisebestätigung → Textbausteine -------

import os

import unterlagen

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "unterlagen")
_HOST = "https://unterlagen.chamaeleon-reisen.de/"


def _dok(dok_id, name, datei):
    return {"id": str(dok_id), "name": name, "beschreibung": "", "link": _HOST + datei}


def mit_unterlagen(monkeypatch, eintraege, status="OK", **felder):
    """Buchung mit ``unterlagen``; Downloads lesen die synthetischen Fixture-PDFs."""
    eigene(monkeypatch, VORGANG)
    calls = fake_tourone(
        monkeypatch,
        buchung={"reiseCode": "CNZHA_NEU", "status": status, "unterlagen": eintraege, **felder},
    )

    def laden(link):
        with open(os.path.join(_FIXTURES, link.rsplit("/", 1)[1]), "rb") as f:
            return f.read()

    monkeypatch.setattr(unterlagen, "_laden", laden)
    return calls


def test_auto_nimmt_die_reiseunterlagen(monkeypatch):
    calls = mit_unterlagen(monkeypatch, [
        _dok(1, "Reisebestätigung.pdf", "reisebestaetigung_alt.pdf"),
        _dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf"),
        _dok(3, "Visum Ausfüllhilfen.pdf", "visum_ausfuellhilfen.pdf"),
    ])
    text = ab.reiseinfo_tool_base(kunden_id="472325")
    assert "reiseunterlagen_neu.pdf" in text
    assert "dokument:visum-ausfuellhilfen" in text
    # Kein Umweg über die Textbausteine, wenn die Unterlagen da sind.
    assert not any(c["path"] == "/get/reise" for c in calls)


def test_auto_ohne_ulas_nimmt_die_reisebestaetigung(monkeypatch):
    mit_unterlagen(monkeypatch, [_dok(1, "Reisebestätigung.pdf", "reisebestaetigung_alt.pdf")])
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="reiseverlauf", tag=3)
    assert "reisebestaetigung_alt.pdf" in text and "Tag 3" in text


def test_ohne_pdfs_bleibt_es_bei_den_textbausteinen(monkeypatch):
    mit_unterlagen(monkeypatch, [])
    assert "Trinkgeld: 5 €" in ab.reiseinfo_tool_base(kunden_id="472325")


def test_textbausteine_auf_wunsch_trotz_unterlagen(monkeypatch):
    mit_unterlagen(monkeypatch, [_dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf")])
    text = ab.reiseinfo_tool_base(kunden_id="472325", quelle="textbausteine")
    assert "Trinkgeld: 5 €" in text


def test_storniert_liest_keine_unterlagen(monkeypatch):
    mit_unterlagen(
        monkeypatch, [_dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf")], status="XX"
    )
    assert "reiseunterlagen_neu.pdf" not in ab.reiseinfo_tool_base(kunden_id="472325")


def test_dokument_slug_und_teilnehmerdaten(monkeypatch):
    mit_unterlagen(monkeypatch, [
        _dok(3, "Visum Ausfüllhilfen.pdf", "visum_ausfuellhilfen.pdf"),
        _dok(4, "Teilnehmerdaten.pdf", "gibt_es_nicht.pdf"),
    ])
    hilfe = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:visum-ausfuellhilfen")
    assert "visum_ausfuellhilfen.pdf" in hilfe
    tn = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:teilnehmerdaten")
    assert tn == unterlagen.TEILNEHMERDATEN_TEXT
    fehlt = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:rechnung")
    assert "dokument:visum-ausfuellhilfen" in fehlt


def test_dokument_reiseunterlagen_ist_die_uebersicht_nicht_fehlt(monkeypatch):
    """Gemessen 2026-10-02: Gemini fragte dokument:reiseunterlagen, bekam „gibt
    es nicht“ und sagte dem Kunden, die Reiseunterlagen fehlten."""
    mit_unterlagen(monkeypatch, [
        _dok(1, "Reisebestätigung.pdf", "reisebestaetigung_alt.pdf"),
        _dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf"),
    ])
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:reiseunterlagen")
    assert "reiseunterlagen_neu.pdf" in text and "Inhalt (Details" in text
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:reisebestaetigung")
    assert "reisebestaetigung_alt.pdf" in text and "Inhalt (Details" in text


def test_dokument_reiseunterlagen_ungegliedert_nur_link(monkeypatch):
    """Unbekannte Vorlage: Link statt des ganzen PDF-Texts."""
    mit_unterlagen(monkeypatch, [_dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf")])
    monkeypatch.setattr(unterlagen, "gliedern", lambda inhalt: None)
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:reiseunterlagen")
    assert "konnte aber nicht gegliedert werden" in text
    assert "Inhalt von" not in text


def test_download_ausfall_nennt_den_link(monkeypatch):
    mit_unterlagen(monkeypatch, [_dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf")])

    def kaputt(link):
        raise TimeoutError

    monkeypatch.setattr(unterlagen, "_laden", kaputt)
    # Mit Abschnitt: Hinweis mit Link. Ohne: die Textbausteine wie vor den
    # Unterlagen, nicht nur ein Link für jede Packfrage.
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="reiseverlauf", tag=3)
    assert _HOST + "reiseunterlagen_neu.pdf" in text
    text = ab.reiseinfo_tool_base(kunden_id="472325")
    assert "# Reisehinweise" in text and _HOST not in text


def test_einreisebestimmungen_nennen_die_aktuelle_quelle(monkeypatch):
    mit_unterlagen(monkeypatch, [
        _dok(5, "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf", "einreisebestimmungen.pdf"),
    ])
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:einreisebestimmungen")
    assert "Stand bei Buchung" in text
    assert "visum.de" in text  # Fixture-Buchung ohne tripurl


def test_einreisebestimmungen_mit_tripurl_bis_reiseende(monkeypatch):
    url = "https://travel-details.eu/de?tid=TEST-TEST-TEST"
    mit_unterlagen(
        monkeypatch,
        [_dok(5, "Einreisebestimmungen Zeitpunkt der Reiseanmeldung.pdf", "einreisebestimmungen.pdf")],
        tripurl=url,
        bisDat="2099-05-15 00:00:00",
    )
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:einreisebestimmungen")
    assert f"Aktuelle Einreise-, Visa- und Impfbestimmungen: {url}" in text
    assert "visum.de" not in text


def test_dokument_slug_gilt_auch_bei_quelle_textbausteine(monkeypatch):
    calls = mit_unterlagen(monkeypatch, [_dok(3, "Visum Ausfüllhilfen.pdf", "visum_ausfuellhilfen.pdf")])
    text = ab.reiseinfo_tool_base(
        kunden_id="472325", quelle="textbausteine", abschnitt="dokument:visum-ausfuellhilfen"
    )
    assert "visum_ausfuellhilfen.pdf" in text
    assert not any(c["path"] == "/get/reise" for c in calls)


def test_dokument_slug_bei_buchungsausfall_fehlertext_ohne_zweiten_abruf(monkeypatch):
    """Ausfall ist nicht „das Dokument gibt es nicht“ — und kein Umweg über die
    Textbausteine, die dieselbe Buchung nochmal holen würden."""
    eigene(monkeypatch, VORGANG)
    calls = fake_tourone(monkeypatch, fehler=RuntimeError("timeout"))
    text = ab.reiseinfo_tool_base(kunden_id="472325", abschnitt="dokument:rechnung")
    assert text == ab.REISEINFO_FEHLER_TEXT
    assert len([c for c in calls if c["path"] == "/get/buchung"]) == 1


def test_unbekannte_quelle_und_kaputter_tag_normalisiert(monkeypatch):
    """quelle="dokumente" (gemessen vom Modell gesendet) zählt als auto, ein
    nicht-numerischer tag als 0 = alle Tage — kein Tool-Fehler."""
    mit_unterlagen(monkeypatch, [_dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf")])
    text = ab.reiseinfo_tool_base(
        kunden_id="472325", quelle="dokumente", abschnitt="reiseverlauf", tag="drei"
    )
    assert "reiseunterlagen_neu.pdf" in text
    assert "Tag 1 · Di 06.10.2026" in text and "Tag 12 · Sa 17.10.2026" in text


def test_tool_wrapper_nimmt_none_als_standard(monkeypatch):
    gerufen = {}

    def spion(vorgangsnummer, seiten_vorgang, kunden_id, agentur_id, **kw):
        gerufen.update(vorgangsnummer=vorgangsnummer, kunden_id=kunden_id, **kw)
        return "ok"

    monkeypatch.setattr(agent, "reiseinfo_tool_base", spion)
    tool = agent.make_reiseinfo_tool(kunden_id="472325")
    assert tool.invoke({"vorgangsnummer": None, "quelle": None, "abschnitt": None, "tag": None}) == "ok"
    assert gerufen == {
        "vorgangsnummer": "", "kunden_id": "472325", "quelle": "auto", "abschnitt": "", "tag": 0,
    }


@pytest.mark.parametrize("agt_nr, mit_dokumenten", [("12345", True), ("99999", False)])
def test_agentur_dokumente_nur_mit_passender_agtnr(monkeypatch, agt_nr, mit_dokumenten):
    """G3 auch auf dem PDF-Weg: /get/buchung kennt keinen Agenturfilter. Weicht
    agtNr ab, keine Dokumente — die Textbausteine bleiben erreichbar."""
    import agenturdaten as ad

    mit_unterlagen(
        monkeypatch, [_dok(2, "Reiseunterlagen.pdf", "reiseunterlagen_neu.pdf")], agtNr=agt_nr
    )
    monkeypatch.setattr(ad, "_agentur_get", lambda path, params: {"0": {"roh": True}})
    monkeypatch.setattr(ad, "_normalise_row", lambda row, agentur_id: {"vorgang": VORGANG})
    text = ab.reiseinfo_tool_base(VORGANG, agentur_id="12345")
    assert (_HOST + "reiseunterlagen_neu.pdf" in text) is mit_dokumenten
    if not mit_dokumenten:
        assert "# Reisehinweise" in text
