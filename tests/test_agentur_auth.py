"""agentur_auth + session_binding.

Alle ss.php-Dumps hier sind ERFUNDEN. Ein echter Agentur-Dump trägt
Passwort-Hash, Salt, IBAN, USt-IdNr und ein Klartextpasswort in SESSION_AGTBEZ
und darf niemals als Fixture committet werden.
"""

import common as _  # noqa: F401

import agentur_auth as aa
import session_binding

VALID_SID = "abcdef0123456789abcdef"
AGT_ORIGIN = "https://agt.chamdev.tourone.de"


def _dump(*paare):
    zeilen = ["<pre>Array", "("]
    zeilen += [f"    [{k}] => {v}" for k, v in paare]
    zeilen += [")", "</pre>"]
    return "\n".join(zeilen)


class _Resp:
    def __init__(self, body="", status=200):
        self.content = body.encode("ISO-8859-1")
        self.status_code = status


# --- extract_agenturnr --------------------------------------------------------


def test_extrahiert_die_agenturnummer():
    assert aa.extract_agenturnr(_dump(("SESSION_AGTNR", "12345"))) == "12345"


def test_decoy_keys_binden_nicht_die_falsche_agentur():
    """Vier weitere Keys ENDEN auf AGTNR — ein Substring-Match nähme den falschen."""
    body = _dump(
        ("SESSION_AGTALTAGTNR", "11111"),
        ("SESSION_AGTNEUAGTNR", "22222"),
        ("SESSION_AGTCRSTOMAAGTNR", "33333"),
        ("SESSION_AGTCRSMERLINAGTNR", "44444"),
        ("SESSION_AGTNR", "12345"),
    )
    assert aa.extract_agenturnr(body) == "12345"


def test_ohne_key_leer():
    assert aa.extract_agenturnr(_dump(("SESSION_ADRKUNDENNR", "999"))) == ""
    assert aa.extract_agenturnr("") == ""


def test_mehrfachtreffer_wird_abgelehnt():
    """Ein untergeschobener zweiter Key ist Mehrdeutigkeit, kein Münzwurf."""
    body = _dump(("SESSION_AGTNR", "12345"), ("SESSION_AGTNR", "99999"))
    assert aa.extract_agenturnr(body) == ""


def test_verschachtelter_key_zaehlt_nicht():
    """print_r rückt Verschachteltes tiefer ein als vier Leerzeichen."""
    body = "<pre>Array\n(\n    [CART] => Array\n        (\n            [SESSION_AGTNR] => 99999\n        )\n)\n</pre>"
    assert aa.extract_agenturnr(body) == ""


def test_nullen_und_muell_werden_verworfen():
    for wert in ("0", "00", "000", "Array", "", "abc", "12-34", "1/2", "../x"):
        assert aa.extract_agenturnr(_dump(("SESSION_AGTNR", wert))) == "", wert


def test_kurze_nummer_ist_gueltig():
    """Kein Längen-Minimum (Owner 2026-08-02) — es ist eine URL-Sicherheitsprüfung."""
    assert aa.extract_agenturnr(_dump(("SESSION_AGTNR", "7"))) == "7"


# --- verify -------------------------------------------------------------------


def test_verify_faellt_bei_kaputtem_token_ohne_request_aus(monkeypatch):
    gerufen = []
    monkeypatch.setattr(aa.requests, "get", lambda *a, **k: gerufen.append(1))
    for token in ("", "kurz", None, 12345, "gültig" * 40, "abc\n" + "d" * 20):
        assert aa.verify_agentur_session(token, origin=AGT_ORIGIN) is None
    assert gerufen == []


def test_verify_folgt_niemals_redirects(monkeypatch):
    """Ein 3xx würde den passwortgleichen Token an das Ziel weiterreichen."""
    gesehen = {}

    def fake_get(url, **kw):
        gesehen.update(kw)
        return _Resp(_dump(("SESSION_AGTNR", "12345")))

    monkeypatch.setattr(aa.requests, "get", fake_get)
    assert aa.verify_agentur_session(VALID_SID, origin=AGT_ORIGIN) == "12345"
    assert gesehen["allow_redirects"] is False


def test_verify_faellt_geschlossen_aus(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("netz")

    monkeypatch.setattr(aa.requests, "get", boom)
    assert aa.verify_agentur_session(VALID_SID, origin=AGT_ORIGIN) is None

    monkeypatch.setattr(aa.requests, "get", lambda *a, **k: _Resp("", 500))
    assert aa.verify_agentur_session(VALID_SID, origin=AGT_ORIGIN) is None


# --- authenticate: die Reihenfolge IST die Sicherheitseigenschaft --------------


def test_fehlgeschlagener_reauth_loest_die_alte_bindung(monkeypatch):
    """Geteilter Agentur-Arbeitsplatz: sonst erbt der Nächste die Vor-Agentur."""
    aa.bind("shared-sid", "11111")
    assert aa.resolve("shared-sid") == "11111"

    monkeypatch.setattr(aa, "verify_agentur_session", lambda *a, **k: None)
    authed, sid = aa.authenticate({"session_id": "shared-sid", "phpsessid": VALID_SID}, origin=AGT_ORIGIN)
    assert authed is False and sid == "shared-sid"
    assert aa.resolve("shared-sid") is None


def test_authenticate_ohne_session_id_meldet_none():
    assert aa.authenticate({}) == (False, None)
    assert aa.authenticate({"session_id": 123}) == (False, None)
    assert aa.authenticate({"session_id": {"a": 1}}) == (False, None)


def test_inflight_wird_auch_bei_exception_abgeraeumt(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(aa, "verify_agentur_session", boom)
    try:
        aa.authenticate({"session_id": "leak-sid", "phpsessid": VALID_SID}, origin=AGT_ORIGIN)
    except RuntimeError:
        pass
    assert "leak-sid" not in aa._inflight


def test_langsamer_auth_kann_neueren_nicht_ueberschreiben():
    """Newest auth wins — Latenz-Reihenfolge, kein Angreifer nötig."""
    alt = aa.begin_auth("race-sid")
    neu = aa.begin_auth("race-sid")
    assert aa.commit_auth("race-sid", "11111", alt) is False
    assert aa.resolve("race-sid") is None
    assert aa.commit_auth("race-sid", "22222", neu) is True
    assert aa.resolve("race-sid") == "22222"


def test_unbind_cancelt_den_laufenden_auth():
    gen = aa.begin_auth("cancel-sid")
    aa.unbind("cancel-sid")
    assert aa.commit_auth("cancel-sid", "12345", gen) is False
    assert aa.resolve("cancel-sid") is None


def test_agentur_und_kunden_bindungen_sind_getrennt():
    """Zwei Stores, eine session_id — die Modi dürfen sich nie kreuzen."""
    import kunden_auth as ka

    ka.bind("gleiche-sid", "999999")
    aa.bind("gleiche-sid", "12345")
    assert ka.resolve("gleiche-sid") == "999999"
    assert aa.resolve("gleiche-sid") == "12345"
    aa.unbind("gleiche-sid")
    assert ka.resolve("gleiche-sid") == "999999"
    assert aa.resolve("gleiche-sid") is None


# --- session_binding ----------------------------------------------------------


def test_ttl_wird_beim_binden_gelesen_nicht_beim_anlegen():
    """Sonst friert der Store den Importwert ein und die Konstante lügt."""
    ttl = [1000]
    store = session_binding.new_store(lambda: ttl[0])
    session_binding.bind(store, "s", "id-1")
    erste = store["bindings"]["s"][1]
    ttl[0] = 50_000
    session_binding.bind(store, "s", "id-1")
    assert store["bindings"]["s"][1] > erste + 40_000


def test_ttl_darf_auch_eine_zahl_sein():
    store = session_binding.new_store(60)
    session_binding.bind(store, "s", "id-1")
    assert session_binding.resolve(store, "s") == "id-1"


def test_abgelaufene_bindung_wird_beim_lesen_entfernt():
    import time

    store = session_binding.new_store(0)
    store["bindings"]["alt"] = ("id-1", time.time() - 1)
    assert session_binding.resolve(store, "alt") is None
    assert "alt" not in store["bindings"]


def test_die_dicts_werden_nie_ersetzt_nur_mutiert():
    """Genau das macht die Modul-Aliase in kunden_auth/agentur_auth sicher."""
    store = session_binding.new_store(60)
    bindings, inflight = store["bindings"], store["inflight"]
    gen = session_binding.begin(store, "s")
    session_binding.commit(store, "s", "id-1", gen)
    session_binding.unbind(store, "s")
    session_binding.bind(store, "s", "id-2")
    assert store["bindings"] is bindings
    assert store["inflight"] is inflight


def test_modul_aliase_sehen_dieselben_daten():
    aa.bind("alias-sid", "12345")
    assert aa._bindings["alias-sid"][0] == "12345"
    assert aa._store["bindings"] is aa._bindings

    import kunden_auth as ka

    ka.bind("alias-sid-k", "999999")
    assert ka._bindings["alias-sid-k"][0] == "999999"
    assert ka._store["bindings"] is ka._bindings


def test_leere_eingaben_sind_no_ops():
    store = session_binding.new_store(60)
    session_binding.bind(store, "", "id-1")
    session_binding.bind(store, "s", "")
    session_binding.unbind(store, "")
    assert store["bindings"] == {}
    assert session_binding.begin(store, "") == 0
    assert session_binding.commit(store, "", "id", 1) is False
    assert session_binding.resolve(store, "") is None


# --- app-Verdrahtung ----------------------------------------------------------


def test_view_name_beschattet_das_modul_nicht():
    """`def agentur_auth():` auf Modulebene → AttributeError → 500 auf JEDEM
    Agentur-Chat. Die Suite importiert app sonst nie, also fängt das nichts —
    außer diesem Test.
    """
    import app

    # Das Modul-Binding muss das MODUL sein, keine View-Funktion.
    assert app.agentur_auth is aa
    assert callable(app.agentur_auth.resolve)
    # Genau der Aufruf, der in chat_stream passiert.
    assert app.agentur_auth.resolve("nicht-gebunden") is None


def test_route_und_endpoint_sind_verdrahtet():
    import app
    import rate_limit

    regeln = {r.rule: r.endpoint for r in app.app.url_map.iter_rules()}
    assert regeln.get("/agentur/auth") == "agentur_auth"
    # Der 429-Pfad muss die Bindung über genau diesen Endpoint-Namen finden.
    assert "agentur_auth" in rate_limit.AUTH_ENDPOINTS
    assert rate_limit.AUTH_ENDPOINTS["agentur_auth"] == "agentur_auth"


def test_rate_limit_429_loest_die_agentur_bindung():
    """flask-limiter lehnt in before_request ab — der View-Body läuft nie."""
    import json

    import app

    aa.bind("limit-sid", "12345")
    client = app.app.test_client()
    with app.app.test_request_context(
        "/agentur/auth",
        method="POST",
        data=json.dumps({"session_id": "limit-sid"}),
        content_type="application/json",
    ):
        import rate_limit

        rate_limit._unbind_rate_limited_session("agentur_auth")
    assert aa.resolve("limit-sid") is None
    del client


# --- Origin → ss.php (agt ist ein ANDERER Host als www) -----------------------


def test_agt_origins_haben_eigene_ss_php():
    assert aa.ss_url_for_origin("https://agt.chamaeleon-reisen.de") == (
        "https://agt.chamaeleon-reisen.de/ss.php"
    )
    assert aa.ss_url_for_origin("https://agt.chamdev.tourone.de") == (
        "https://agt.chamdev.tourone.de/ss.php"
    )


def test_unbekannter_origin_faellt_nicht_auf_produktion_zurueck():
    """Der Kundenpfad fällt auf www zurück; hier wäre das ein Replay gegen einen
    Host, der den Token nie ausgestellt hat."""
    for o in ("https://evil.example.com", "https://www.chamaeleon-reisen.de",
              "", None, 42, "http://agt.chamaeleon-reisen.de"):
        assert aa.ss_url_for_origin(o) == "", o


def test_unbekannter_origin_erzeugt_keinen_request(monkeypatch):
    gerufen = []
    monkeypatch.setattr(aa.requests, "get", lambda *a, **k: gerufen.append(1))
    assert aa.verify_agentur_session(VALID_SID, origin="https://evil.example.com") is None
    assert gerufen == []


def test_kunden_tabelle_bleibt_ohne_agt_hosts():
    """Das Fehlen ist dort selbst eine Kontrolle — die Tabellen bleiben getrennt."""
    import kunden_auth as ka

    assert not any("agt." in o for o in ka.SS_URLS)


def test_tool_output_traegt_die_agenturnummer(monkeypatch):
    """Sonst rät das Modell — gemessen: es nannte die erste Buchungsnummer."""
    import agenturdaten

    monkeypatch.setattr(
        agenturdaten, "fetch_buchungen_text", lambda *a, **k: "Buchungen dieser Agentur:\n- ..."
    )
    t = agenturdaten.make_buchungen_agentur_tool("54321")
    out = t.invoke({})
    assert out.startswith("Agenturnummer: 54321")


def test_die_bindung_ist_waehrend_ss_php_schon_geloest(monkeypatch):
    """begin_auth muss VOR ss.php laufen, nicht erst danach.

    Nur den Endzustand zu prüfen (test_fehlgeschlagener_reauth_loest_die_alte_
    bindung) reicht nicht: begin_auth in den finally-Block zu verschieben lässt
    jenen Test grün und öffnet trotzdem ein Fenster von TIMEOUT Sekunden, in dem
    resolve() noch die VORIGE Agentur liefert. Am geteilten Counter ist genau das
    die Lücke — der Nächste bekommt die Buchungen des Vorigen, solange dessen
    ss.php-Aufruf läuft. Gegenstück zu test_kunden_auth.test_binding_is_cleared_
    before_ss_php_is_called.
    """
    gesehen = {}

    def spy(phpsessid, user_agent="", origin=""):
        gesehen["waehrend"] = aa.resolve("clear-first-agt")
        return "22222"

    monkeypatch.setattr(aa, "verify_agentur_session", spy)
    aa.bind("clear-first-agt", "11111")
    aa.authenticate(
        {"session_id": "clear-first-agt", "phpsessid": VALID_SID}, origin=AGT_ORIGIN
    )

    assert gesehen["waehrend"] is None, "Vor-Agentur war während ss.php noch gebunden"
    assert aa.resolve("clear-first-agt") == "22222"
    aa.unbind("clear-first-agt")
