"""Tests for Kunden-Modus (kundendaten.py + kunden_modus prompt block).

Pure logic is tested directly; TourOne calls are monkeypatched — no live
requests here. Deliberately never imports ``app`` (importing it triggers
live Supabase reads).
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import kundendaten as kd

# --- fixtures ----------------------------------------------------------------

ZUKUNFT_VON = "2099-01-01 00:00:00"
ZUKUNFT_BIS = "2099-01-15 00:00:00"
VERGANGEN_VON = "2020-01-01 00:00:00"
VERGANGEN_BIS = "2020-01-15 00:00:00"

FLUG = {
    "id": 4711,
    "pnrFileKey": "GEHEIMPNR",
    "vonCo3Code": "FRA",
    "nachCo3Code": "WDH",
    "flugnr": "4Y123",
    "airline": "4Y",
    "status": "OK",
    "abflug": "2099-01-01 10:20:00",
    "ankunft": "2099-01-01 18:30:00",
    "rang": 1,
    "sitzplatz": "12A",
}


def eingebettete_buchung(vorgang="126001", von=ZUKUNFT_VON, bis=ZUKUNFT_BIS):
    return {"vorgang": vorgang, "vonDat": von, "bisDat": bis, "reiseCode": "NAWDH"}


def adresse_mit(buchungen):
    return {"kundennummer": 999999999, "buchungen": buchungen}


def volle_buchung(status="OK", flugdaten=None, titel="Namibia-Reise"):
    return {
        "status": status,
        "beschreibungen": [{"titel": titel}],
        "flugdaten": [FLUG] if flugdaten is None else flugdaten,
    }


def fake_tourone(monkeypatch, handlers):
    """Patch kd._tourone_get; ``handlers`` maps path → result | Exception |
    callable(params). Records every call for structural assertions."""
    calls = []

    def fake(path, params, timeout=20):
        calls.append({"path": path, "params": dict(params), "timeout": timeout})
        result = handlers[path]
        if callable(result) and not isinstance(result, Exception):
            result = result(params)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(kd, "_tourone_get", fake)
    return calls


# --- parse_kunden_id ----------------------------------------------------------


def test_parse_akzeptiert_string_und_int():
    assert kd.parse_kunden_id("999999999") == "999999999"
    assert kd.parse_kunden_id("  42abc_X-1  ") == "42abc_X-1"
    assert kd.parse_kunden_id(999999999) == "999999999"


def test_parse_verwirft_andere_typen():
    # bool ist int-Subklasse: JSON true darf nicht zu "True" werden.
    assert kd.parse_kunden_id(True) == ""
    assert kd.parse_kunden_id(False) == ""
    assert kd.parse_kunden_id(None) == ""
    assert kd.parse_kunden_id(1.5) == ""
    assert kd.parse_kunden_id(["1"]) == ""
    assert kd.parse_kunden_id({"id": "1"}) == ""


def test_parse_allowlist():
    assert kd.parse_kunden_id("abc/def") == ""
    assert kd.parse_kunden_id("1?x=2") == ""
    assert kd.parse_kunden_id("a" * 33) == ""
    assert kd.parse_kunden_id("a" * 32) == "a" * 32
    assert kd.parse_kunden_id("") == ""
    assert kd.parse_kunden_id("   ") == ""


# --- fetch_fluege_text ---------------------------------------------------------


def test_unbekannte_id_eigener_text(monkeypatch):
    # Kontrakt: unbekannte ID → [] mit HTTP 200, nie Fehlerstatus.
    fake_tourone(monkeypatch, {"/get/adresse": []})
    assert kd.fetch_fluege_text("000000001") == kd.UNBEKANNT_TEXT


def test_api_fehler_hop1(monkeypatch):
    fake_tourone(monkeypatch, {"/get/adresse": RuntimeError("boom")})
    assert kd.fetch_fluege_text("999999999") == kd.FEHLER_TEXT


def test_keine_buchungen(monkeypatch):
    fake_tourone(monkeypatch, {"/get/adresse": adresse_mit([])})
    assert kd.fetch_fluege_text("999999999") == kd.KEINE_FLUEGE_TEXT


def test_nur_vergangene_buchungen_kein_hop2(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {"/get/adresse": adresse_mit([eingebettete_buchung(von=VERGANGEN_VON, bis=VERGANGEN_BIS)])},
    )
    assert kd.fetch_fluege_text("999999999") == kd.KEINE_FLUEGE_TEXT
    assert len(calls) == 1  # vergangene Buchung löst keinen /get/buchung-Call aus


def test_happy_path_whitelist(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(),
        },
    )
    text = kd.fetch_fluege_text("999999999")
    assert "4Y123" in text
    assert "FRA" in text and "WDH" in text
    assert "Namibia-Reise" in text
    assert "01.01.2099, 10:20 Uhr" in text
    # Whitelist: PNR, Sitzplatz, interne IDs, Roh-kunden_id erreichen nie den Text.
    assert "GEHEIMPNR" not in text
    assert "12A" not in text
    assert "4711" not in text
    assert "999999999" not in text
    # Strukturell: nur GET-Pfade, überall das enge Chat-Timeout.
    assert all(c["path"].startswith("/get/") for c in calls)
    assert all(c["timeout"] == kd.TIMEOUT for c in calls)


def test_stornierte_buchung_wird_uebersprungen(monkeypatch):
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(status="XX"),
        },
    )
    assert kd.fetch_fluege_text("999999999") == kd.KEINE_FLUEGE_TEXT


def test_noch_keine_flugdaten(monkeypatch):
    # Kommende Buchung, aber Flüge noch nicht eingebucht → Empty-Text, kein Fehler.
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": volle_buchung(flugdaten=[]),
        },
    )
    assert kd.fetch_fluege_text("999999999") == kd.KEINE_FLUEGE_TEXT


def test_api_fehler_hop2(monkeypatch):
    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit([eingebettete_buchung()]),
            "/get/buchung": RuntimeError("timeout"),
        },
    )
    assert kd.fetch_fluege_text("999999999") == kd.FEHLER_TEXT


def test_teilerfolg_zeigt_fluege(monkeypatch):
    # Erster Hop-2-Call scheitert, zweiter liefert Flüge → Flüge gewinnen.
    zustand = {"n": 0}

    def buchung_handler(params):
        zustand["n"] += 1
        if zustand["n"] == 1:
            raise RuntimeError("timeout")
        return volle_buchung()

    fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit(
                [eingebettete_buchung("126001"), eingebettete_buchung("126002")]
            ),
            "/get/buchung": buchung_handler,
        },
    )
    text = kd.fetch_fluege_text("999999999")
    assert "4Y123" in text


def test_hop2_cap(monkeypatch):
    calls = fake_tourone(
        monkeypatch,
        {
            "/get/adresse": adresse_mit(
                [eingebettete_buchung(str(i)) for i in range(10)]
            ),
            "/get/buchung": volle_buchung(),
        },
    )
    kd.fetch_fluege_text("999999999")
    assert len(calls) == 1 + kd.MAX_BUCHUNGEN


# --- make_fluege_tool -----------------------------------------------------------


def test_tool_hat_keine_parameter_und_closure(monkeypatch):
    gesehen = []
    fake_tourone(
        monkeypatch,
        {"/get/adresse": lambda params: gesehen.append(params) or []},
    )
    fluege_tool = kd.make_fluege_tool("999999999")
    # Kein Parameter: das Modell kann nie wählen, wessen Daten geholt werden.
    assert fluege_tool.args == {}
    result = fluege_tool.invoke({})
    assert result == kd.UNBEKANNT_TEXT
    assert gesehen[0]["kundennummer"] == "999999999"


# --- filter_new_tool_calls -------------------------------------------------------


def test_dedup_tool_calls():
    seen = set()
    erste = kd.filter_new_tool_calls([{"id": "a", "name": "x"}], seen)
    assert [tc["id"] for tc in erste] == ["a"]
    # stream_mode="values" liefert historische Calls erneut — gefiltert.
    zweite = kd.filter_new_tool_calls(
        [{"id": "a", "name": "x"}, {"id": "b", "name": "y"}], seen
    )
    assert [tc["id"] for tc in zweite] == ["b"]


def test_dedup_ohne_id_passiert_durch():
    seen = set()
    assert len(kd.filter_new_tool_calls([{"name": "x"}, {"name": "x"}], seen)) == 2
    assert seen == set()


# --- kunden_modus prompt block ---------------------------------------------------

FESTE_ZEIT = {"date": "01. Januar 2099", "time": "12:00", "weekday": "Montag"}


def test_prompt_ohne_kunden_id_unveraendert(monkeypatch):
    monkeypatch.setattr(agent_base, "get_current_time_info", lambda: FESTE_ZEIT)
    basis = agent_base.format_system_prompt("/", [])
    explizit = agent_base.format_system_prompt("/", [], is_kunde=False)
    assert basis == explizit
    assert "Kunden-Modus" not in basis
    assert "kunden_fluege_tool" not in basis


def test_prompt_mit_kunden_modus_block(monkeypatch):
    monkeypatch.setattr(agent_base, "get_current_time_info", lambda: FESTE_ZEIT)
    prompt = agent_base.format_system_prompt("/", [], is_kunde=True)
    assert "Kunden-Modus" in prompt
    assert "kunden_fluege_tool" in prompt
    # Überschreibt die allgemeine Flüge-Regel und routet vergangene Flüge.
    assert "Abweichend von der allgemeinen Flüge-Regel" in prompt
    assert "Vergangene Flüge" in prompt
    # Nur ein Flag erreicht den Prompt — nie die rohe ID (Signatur nimmt keine an).
    assert "999999999" not in prompt
