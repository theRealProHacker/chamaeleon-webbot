"""Tool-Historie: serverseitig speichern und wiedereinfügen, nie ans Frontend.

Test-first geschrieben, vor der Implementierung: das Modul ``tool_history`` (Store nach dem
session_binding-Muster) und die agent.py-Erweiterungen (Erfassen nach
Stream-Ende, Wiedereinfügen in convert_messages_to_langchain).

Kein Live-Modell, kein TourOne, und bewusst nie ``import app`` (das zöge beim
Import Live-Supabase-Reads nach sich). Der kompilierte Graph wird wie in
test_empty_reply.py durch einen Stub ersetzt — anders als dort reicht der Stub
die Eingabe durch, denn genau die (stream_mode="values"-Verhalten) braucht die
Erfassungslogik, um den Zuwachs zu bestimmen.

Festgepinnte API:

    tool_history.new_store(ttl)                          # dict, kein Objekt
    tool_history.save(store, session_id, identitaet, turn_index, verlauf)
    tool_history.load(store, session_id, identitaet) -> dict[int, list]
    tool_history.TTL / MAX_ERGEBNIS_ZEICHEN / MAX_TOOL_TURNS / MAX_SESSIONS
    tool_history._store                                  # Instanz für agent.py

    verlauf-Einträge (nur JSON-artige Werte, keine LangChain-Objekte):
      {"typ": "ai_tool_calls", "tool_calls": [{"name", "args", "id"}]}
      {"typ": "tool", "tool_call_id": ..., "name": ..., "content": ...}

    agent.convert_messages_to_langchain(messages, gespeicherte_turns=None)
    agent.call_stream(..., session_id="")                # "" = Feature aus
"""

import common as _  # noqa: F401  (adds repo root to sys.path)

import time

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

import agent
import tool_history


# --- Bausteine ----------------------------------------------------------------


def _verlauf(call_id="call-1", name="reiseinfo_tool", args=None, content="Reise: Namibia, 03.05."):
    """Ein Ein-Tool-Turn im Speicherformat des Plans."""
    return [
        {
            "typ": "ai_tool_calls",
            "tool_calls": [{"name": name, "args": args or {}, "id": call_id}],
        },
        {"typ": "tool", "tool_call_id": call_id, "name": name, "content": content},
    ]


def _ai_mit_toolcall(call_id, name="reiseinfo_tool", args=None):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}
        ],
    )


def _toolmsg(call_id, content, name="reiseinfo_tool"):
    return ToolMessage(content=content, tool_call_id=call_id, name=name)


class _Executor:
    """Ersetzt den kompilierten Graph.

    Reicht — wie stream_mode="values" — die komplette Eingabe wieder mit aus
    und hängt pro Lauf die konfigurierten Extras an. Merkt sich jede gesehene
    Eingabe, damit Tests prüfen können, was das "Modell" an History bekam.
    """

    def __init__(self, extras_pro_lauf):
        self.extras_pro_lauf = list(extras_pro_lauf)
        self.laeufe = 0
        self.gesehene_eingaben = []

    def stream(self, state, stream_mode="values"):
        eingabe = list(state["messages"])
        self.gesehene_eingaben.append(eingabe)
        extras = self.extras_pro_lauf[min(self.laeufe, len(self.extras_pro_lauf) - 1)]
        self.laeufe += 1
        yield {"messages": eingabe + list(extras)}


def _frischer_store(monkeypatch):
    store = tool_history.new_store(lambda: tool_history.TTL)
    monkeypatch.setattr(tool_history, "_store", store)
    return store


def _stub_executor(monkeypatch, extras_pro_lauf):
    executor = _Executor(extras_pro_lauf)
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    return executor


MSGS_ZWEI_TURNS = [
    {"role": "user", "content": "Wann geht meine Reise los?"},
    {"role": "assistant", "content": "<p>Am 3. Mai.</p>"},
    {"role": "user", "content": "Und wie teuer ist sie?"},
]


# --- 1. Store-Einheit ----------------------------------------------------------


def test_save_load_roundtrip():
    store = tool_history.new_store(60)
    verlauf = _verlauf()
    tool_history.save(store, "s1", "", 1, verlauf)
    geladen = tool_history.load(store, "s1", "")
    assert list(geladen) == [1]
    assert geladen[1] == verlauf


def test_load_unbekannte_session_ist_leer():
    store = tool_history.new_store(60)
    assert tool_history.load(store, "gibtsnicht", "") == {}


def test_ttl_abgelaufen_heisst_weg():
    store = tool_history.new_store(60)
    tool_history.save(store, "s1", "", 1, _verlauf())
    # Konvention aus test_kunden_auth: den Eintrag direkt zurückdatieren statt
    # die Uhr zu patchen.
    store["sessions"]["s1"]["expiry"] = time.time() - 1
    assert tool_history.load(store, "s1", "") == {}
    assert "s1" not in store["sessions"]


def test_lru_kappe_verdraengt_die_aelteste_session(monkeypatch):
    monkeypatch.setattr(tool_history, "MAX_SESSIONS", 2)
    store = tool_history.new_store(60)
    tool_history.save(store, "s1", "", 1, _verlauf("a"))
    tool_history.save(store, "s2", "", 1, _verlauf("b"))
    tool_history.save(store, "s3", "", 1, _verlauf("c"))
    assert tool_history.load(store, "s1", "") == {}
    assert tool_history.load(store, "s2", "") != {}
    assert tool_history.load(store, "s3", "") != {}


def test_turn_kappe_haelt_nur_die_juengsten_turns(monkeypatch):
    monkeypatch.setattr(tool_history, "MAX_TOOL_TURNS", 2)
    store = tool_history.new_store(60)
    for turn in (1, 2, 3):
        tool_history.save(store, "s1", "", turn, _verlauf(f"call-{turn}"))
    assert sorted(tool_history.load(store, "s1", "")) == [2, 3]


def test_lange_tool_ergebnisse_werden_gekappt(monkeypatch):
    monkeypatch.setattr(tool_history, "MAX_ERGEBNIS_ZEICHEN", 50)
    store = tool_history.new_store(60)
    lang = "x" * 200
    tool_history.save(store, "s1", "", 1, _verlauf(content=lang))
    (gespeichert,) = [
        e for e in tool_history.load(store, "s1", "")[1] if e["typ"] == "tool"
    ]
    assert gespeichert["content"].startswith("x" * 50)
    assert "[gekürzt]" in gespeichert["content"]
    assert gespeichert["content"] == "x" * 50 + tool_history.KUERZUNGS_MARKER


def test_leerer_verlauf_loescht_den_alten_turn():
    """Retry/Edit derselben Frage: ein Lauf ohne Tools räumt seinen Index ab."""
    store = tool_history.new_store(60)
    tool_history.save(store, "s1", "", 2, _verlauf())
    tool_history.save(store, "s1", "", 2, [])
    assert 2 not in tool_history.load(store, "s1", "")


def test_identitaets_mismatch_verwirft_und_loescht():
    """Geteilter Browser, gleiche session_id, andere Identität:
    Tool-Daten von Kunde A dürfen nie im Chat von Kunde B auftauchen."""
    store = tool_history.new_store(60)
    tool_history.save(store, "s1", "kunde:111", 1, _verlauf())
    assert tool_history.load(store, "s1", "kunde:222") == {}
    # verworfen heißt gelöscht — auch die richtige Identität sieht nichts mehr
    assert tool_history.load(store, "s1", "kunde:111") == {}


# --- 2. Wiedereinfügen (convert_messages_to_langchain) --------------------------


def test_ohne_gespeicherte_turns_bleibt_alles_wie_heute():
    ergebnis = agent.convert_messages_to_langchain(MSGS_ZWEI_TURNS)
    assert [type(m) for m in ergebnis] == [HumanMessage, AIMessage, HumanMessage]
    assert ergebnis[1].content == "<p>Am 3. Mai.</p>"


def test_gespeicherter_turn_wird_vor_der_assistant_antwort_eingefuegt():
    verlauf = _verlauf(args={"vorgangsnummer": ""})
    ergebnis = agent.convert_messages_to_langchain(
        MSGS_ZWEI_TURNS, gespeicherte_turns={1: verlauf}
    )
    assert [type(m) for m in ergebnis] == [
        HumanMessage,
        AIMessage,   # tool_calls, Content leer
        ToolMessage,
        AIMessage,   # die eigentliche Antwort
        HumanMessage,
    ]
    ai_tc = ergebnis[1]
    assert ai_tc.tool_calls and ai_tc.tool_calls[0]["name"] == "reiseinfo_tool"
    assert ai_tc.tool_calls[0]["id"] == "call-1"
    assert not agent.text_aus_content(ai_tc.content).strip()
    assert ergebnis[2].tool_call_id == "call-1"
    assert ergebnis[2].content == "Reise: Namibia, 03.05."
    assert ergebnis[3].content == "<p>Am 3. Mai.</p>"


def test_mehrere_tool_schritte_bleiben_in_reihenfolge():
    verlauf = [
        {"typ": "ai_tool_calls", "tool_calls": [{"name": "visa_tool", "args": {"country": "Namibia"}, "id": "c1"}]},
        {"typ": "tool", "tool_call_id": "c1", "name": "visa_tool", "content": "Visum frei."},
        {"typ": "ai_tool_calls", "tool_calls": [{"name": "termine_tool", "args": {"url_path": "/x"}, "id": "c2"}]},
        {"typ": "tool", "tool_call_id": "c2", "name": "termine_tool", "content": "3 Termine."},
    ]
    ergebnis = agent.convert_messages_to_langchain(
        MSGS_ZWEI_TURNS, gespeicherte_turns={1: verlauf}
    )
    assert [type(m) for m in ergebnis] == [
        HumanMessage,
        AIMessage, ToolMessage, AIMessage, ToolMessage,
        AIMessage,
        HumanMessage,
    ]
    assert ergebnis[1].tool_calls[0]["id"] == "c1"
    assert ergebnis[3].tool_calls[0]["id"] == "c2"


def test_turn_ohne_assistant_antwort_wird_uebersprungen():
    """Randfall: kein Anker, kein Einfügen — niemals ein Fehler."""
    ergebnis = agent.convert_messages_to_langchain(
        MSGS_ZWEI_TURNS, gespeicherte_turns={2: _verlauf()}
    )
    assert [type(m) for m in ergebnis] == [HumanMessage, AIMessage, HumanMessage]


# --- 3. Ende-zu-Ende durch call_stream ------------------------------------------


def test_call_stream_speichert_den_tool_verlauf(monkeypatch):
    _frischer_store(monkeypatch)
    _stub_executor(
        monkeypatch,
        [[
            _ai_mit_toolcall("call-9", name="visa_tool", args={"country": "Namibia"}),
            _toolmsg("call-9", "Visum: bei Einreise, kostenlos.", name="visa_tool"),
            AIMessage(content="Du bekommst das Visum bei der Einreise."),
        ]],
    )
    events = list(agent.call_stream(MSGS_ZWEI_TURNS, "/", session_id="sess-1"))
    assert any(e["type"] == "response" for e in events)

    geladen = tool_history.load(tool_history._store, "sess-1", "")
    # zwei User-Nachrichten in der Eingabe → das war Turn 2
    assert list(geladen) == [2]
    typen = [e["typ"] for e in geladen[2]]
    assert typen == ["ai_tool_calls", "tool"]
    (tc,) = geladen[2][0]["tool_calls"]
    assert tc["name"] == "visa_tool"
    assert tc["args"] == {"country": "Namibia"}
    assert tc["id"] == "call-9"
    assert geladen[2][1]["tool_call_id"] == "call-9"
    assert "Visum" in geladen[2][1]["content"]


def test_call_stream_speichert_unter_der_identitaet_des_laufs(monkeypatch):
    _frischer_store(monkeypatch)
    _stub_executor(
        monkeypatch,
        [[
            _ai_mit_toolcall("call-k"),
            _toolmsg("call-k", "Zahlstand: offen 500 €."),
            AIMessage(content="Offen sind noch 500 €."),
        ]],
    )
    list(
        agent.call_stream(
            [{"role": "user", "content": "Was ist noch offen?"}],
            "/",
            session_id="sess-k",
            kunden_id="4711",
        )
    )
    assert tool_history.load(tool_history._store, "sess-k", "kunde:4711") != {}
    # der anonyme Folgerequest derselben session_id sieht NICHTS
    assert tool_history.load(tool_history._store, "sess-k", "") == {}


def test_call_stream_fuegt_gespeicherte_turns_wieder_ein(monkeypatch):
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "sess-2", "", 1, _verlauf())
    executor = _stub_executor(monkeypatch, [[AIMessage(content="1.990 € pro Person.")]])
    list(agent.call_stream(MSGS_ZWEI_TURNS, "/", session_id="sess-2"))

    (eingabe,) = executor.gesehene_eingaben
    assert [type(m) for m in eingabe] == [
        SystemMessage,
        HumanMessage,
        AIMessage,   # tool_calls aus dem Store
        ToolMessage,
        AIMessage,   # damalige Antwort
        HumanMessage,
    ]
    assert eingabe[2].tool_calls[0]["id"] == "call-1"
    assert eingabe[3].tool_call_id == "call-1"


def test_retry_speichert_nur_den_letzten_versuch(monkeypatch):
    _frischer_store(monkeypatch)
    _stub_executor(
        monkeypatch,
        [
            [  # Versuch 1: Tool-Call A, aber leere Endantwort → Retry
                _ai_mit_toolcall("call-A", name="visa_tool"),
                _toolmsg("call-A", "Ergebnis A", name="visa_tool"),
                AIMessage(content=""),
            ],
            [  # Versuch 2: Tool-Call B, gute Antwort
                _ai_mit_toolcall("call-B", name="visa_tool"),
                _toolmsg("call-B", "Ergebnis B", name="visa_tool"),
                AIMessage(content="Hier die Antwort."),
            ],
        ],
    )
    list(
        agent.call_stream(
            [{"role": "user", "content": "Hallo"}], "/", session_id="sess-3"
        )
    )
    geladen = tool_history.load(tool_history._store, "sess-3", "")
    ids = [
        tc["id"]
        for eintrag in geladen.get(1, [])
        if eintrag["typ"] == "ai_tool_calls"
        for tc in eintrag["tool_calls"]
    ]
    assert ids == ["call-B"], f"nur der letzte Versuch zählt, gespeichert: {ids}"


def test_ohne_session_id_wird_nichts_gespeichert_und_nichts_eingefuegt(monkeypatch):
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "sess-4", "", 1, _verlauf())
    executor = _stub_executor(
        monkeypatch,
        [[
            _ai_mit_toolcall("call-neu"),
            _toolmsg("call-neu", "Ergebnis"),
            AIMessage(content="Antwort."),
        ]],
    )
    # session_id fehlt → exakt heutiges Verhalten, bitweise
    list(agent.call_stream(MSGS_ZWEI_TURNS, "/"))

    (eingabe,) = executor.gesehene_eingaben
    assert not any(isinstance(m, ToolMessage) for m in eingabe)
    assert set(store["sessions"]) == {"sess-4"}, "kein Eintrag für die leere session_id"


# --- 5. Nachträge aus dem Review ------------------------------------------------


def test_turn_kappe_streicht_ganze_gruppen_nie_einzelne_eintraege(monkeypatch):
    """Gemini lehnt function_call ohne function_response ab: gekappt wird paarweise."""
    monkeypatch.setattr(tool_history, "MAX_TURN_EINTRAEGE", 3)
    store = tool_history.new_store(60)
    verlauf = _verlauf("alt") + [
        {
            "typ": "ai_tool_calls",
            "tool_calls": [
                {"name": "visa_tool", "args": {}, "id": "neu-1"},
                {"name": "country_faq_tool", "args": {}, "id": "neu-2"},
            ],
        },
        {"typ": "tool", "tool_call_id": "neu-1", "name": "visa_tool", "content": "a"},
        {"typ": "tool", "tool_call_id": "neu-2", "name": "country_faq_tool", "content": "b"},
    ]
    tool_history.save(store, "s1", "", 1, verlauf)
    (gespeichert,) = tool_history.load(store, "s1", "").values()
    assert gespeichert == verlauf[2:]


def test_fingerprint_mismatch_laesst_den_turn_weg():
    store = tool_history.new_store(60)
    tool_history.save(store, "s1", "", 1, _verlauf(), fingerprint="Wann geht es los?")
    assert tool_history.load(store, "s1", "", fingerprints={1: "Wann  geht es\nlos?"})
    assert tool_history.load(store, "s1", "", fingerprints={1: "Was kostet das?"}) == {}


def test_save_bei_identitaetswechsel_ersetzt_statt_zu_mergen():
    store = tool_history.new_store(60)
    tool_history.save(store, "s1", "kunde:111", 1, _verlauf("a"))
    tool_history.save(store, "s1", "kunde:222", 2, _verlauf("b"))
    assert list(tool_history.load(store, "s1", "kunde:222")) == [2]


def test_termine_wird_paarweise_nicht_gespeichert(monkeypatch):
    """Verfügbarkeiten kippen; auch im parallelen Mischaufruf fällt nur termine weg."""
    _frischer_store(monkeypatch)
    _stub_executor(
        monkeypatch,
        [[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "termine_tool", "args": {"url_path": "/x"}, "id": "t1", "type": "tool_call"},
                    {"name": "visa_tool", "args": {"country": "Peru"}, "id": "v1", "type": "tool_call"},
                ],
            ),
            _toolmsg("t1", "3 Plätze frei.", name="termine_tool"),
            _toolmsg("v1", "Visumfrei.", name="visa_tool"),
            _ai_mit_toolcall("t2", name="termine_tool"),
            _toolmsg("t2", "Noch 2 Plätze.", name="termine_tool"),
            AIMessage(content="Visumfrei, und es gibt noch Plätze."),
        ]],
    )
    list(agent.call_stream([{"role": "user", "content": "Peru?"}], "/", session_id="s-t"))
    (verlauf,) = tool_history.load(tool_history._store, "s-t", "").values()
    assert [e["typ"] for e in verlauf] == ["ai_tool_calls", "tool"]
    assert [tc["id"] for tc in verlauf[0]["tool_calls"]] == ["v1"]
    assert verlauf[1]["tool_call_id"] == "v1"


def test_ergebnis_traegt_die_berliner_uhrzeit(monkeypatch):
    class _Uhr(agent.datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # 10:15 UTC im Sommer = 12:15 in Berlin
            return agent.datetime.datetime(2026, 7, 1, 10, 15, tzinfo=agent.pytz.utc).astimezone(tz)

    monkeypatch.setattr(agent.datetime, "datetime", _Uhr)
    _frischer_store(monkeypatch)
    _stub_executor(
        monkeypatch,
        [[
            _ai_mit_toolcall("v1", name="visa_tool"),
            _toolmsg("v1", "Visumfrei.", name="visa_tool"),
            AIMessage(content="Visumfrei."),
        ]],
    )
    list(agent.call_stream([{"role": "user", "content": "Peru?"}], "/", session_id="s-z"))
    (verlauf,) = tool_history.load(tool_history._store, "s-z", "").values()
    assert verlauf[1]["content"] == "[Stand 12:15] Visumfrei."


def test_begruessung_zaehlt_nicht_als_turn(monkeypatch):
    """Prod-Form: das Widget schickt seine Begrüßung als erste Assistant-Nachricht."""
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "s-g", "", 1, _verlauf(name="visa_tool"), fingerprint="Visum Peru?")
    executor = _stub_executor(
        monkeypatch,
        [[_ai_mit_toolcall("v2", name="visa_tool"), _toolmsg("v2", "x", name="visa_tool"), AIMessage(content="Ja.")]],
    )
    msgs = [
        {"role": "assistant", "content": "<p>Hallo Anna! Ich bin Leon.</p>"},
        {"role": "user", "content": "Visum Peru?"},
        {"role": "assistant", "content": "<p>Visumfrei.</p>"},
        {"role": "user", "content": "Und Bolivien?"},
    ]
    list(agent.call_stream(msgs, "/", session_id="s-g"))
    (eingabe,) = executor.gesehene_eingaben
    assert [type(m) for m in eingabe] == [
        SystemMessage, HumanMessage, AIMessage, ToolMessage, AIMessage, HumanMessage,
    ]
    assert sorted(tool_history.load(store, "s-g", "")) == [1, 2]


def test_replay_wird_nicht_erneut_gespeichert_und_nicht_gemeldet(monkeypatch):
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "s-r", "", 1, _verlauf("alt", name="visa_tool"))
    _stub_executor(monkeypatch, [[AIMessage(content="1.990 €.")]])
    events = list(agent.call_stream(MSGS_ZWEI_TURNS, "/", session_id="s-r"))
    assert not [e for e in events if e["type"] == "tool_call"]
    assert list(tool_history.load(store, "s-r", "")) == [1]


def test_fail_open_bei_abgelehnter_replay_form(monkeypatch):
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "s-f", "", 1, _verlauf(name="visa_tool"))

    class _Lehnt:
        gesehene_eingaben = []

        def stream(self, state, stream_mode="values"):
            eingabe = list(state["messages"])
            self.gesehene_eingaben.append(eingabe)
            if any(isinstance(m, ToolMessage) for m in eingabe):
                raise RuntimeError("400 INVALID_ARGUMENT: function response parts")
            yield {"messages": eingabe + [AIMessage(content="Antwort ohne Replay.")]}

    executor = _Lehnt()
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    events = list(agent.call_stream(MSGS_ZWEI_TURNS, "/", session_id="s-f"))
    assert [e["type"] for e in events] == ["response"]
    assert len(executor.gesehene_eingaben) == 2


def test_kein_fail_open_bei_transientem_fehler(monkeypatch):
    """429/5xx würden im zweiten Lauf nur jeden Tool-Aufruf doppelt ausführen."""
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "s-5", "", 1, _verlauf(name="visa_tool"))

    class _Faellt:
        laeufe = 0

        def stream(self, state, stream_mode="values"):
            self.laeufe += 1
            raise RuntimeError("503 Service Unavailable")
            yield

    executor = _Faellt()
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    events = list(agent.call_stream(MSGS_ZWEI_TURNS, "/", session_id="s-5"))
    assert [e["type"] for e in events] == ["error"]
    assert executor.laeufe == 1


def test_schalter_aus_heisst_verhalten_wie_heute(monkeypatch):
    monkeypatch.setattr(agent, "TOOL_HISTORY_ENABLED", False)
    store = _frischer_store(monkeypatch)
    tool_history.save(store, "s-a", "", 1, _verlauf(name="visa_tool"))
    executor = _stub_executor(
        monkeypatch,
        [[_ai_mit_toolcall("n", name="visa_tool"), _toolmsg("n", "x", name="visa_tool"), AIMessage(content="Ja.")]],
    )
    list(agent.call_stream(MSGS_ZWEI_TURNS, "/", session_id="s-a"))
    (eingabe,) = executor.gesehene_eingaben
    assert not any(isinstance(m, ToolMessage) for m in eingabe)
    assert list(tool_history.load(store, "s-a", "")) == [1]
