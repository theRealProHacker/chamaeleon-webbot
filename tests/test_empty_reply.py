"""Eine leere Modellantwort darf nie als leere Blase beim Kunden landen.

Gemessen in prod: 26 von 1343 Assistant-Turns kamen leer zurück (1,9 %). Das
Widget rendert daraus eine leere Blase, speichert sie als Verlauf und schickt
sie beim nächsten Mal mit — das Modell beantwortet dann die vorige Frage, die
Antworten laufen also versetzt weiter.

Der Test stubbt nur create_react_agent, es ist also weder ein Modell- noch ein
TourOne-Aufruf nötig.
"""

import agent


class _Msg:
    def __init__(self, content, finish_reason=None, tool_calls=None, msg_id=None):
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.response_metadata = {"finish_reason": finish_reason} if finish_reason else {}
        self.usage_metadata = {"input_tokens": 1200, "output_tokens": 0}
        self.id = msg_id


class _Executor:
    """Ersetzt den kompilierten Graph und zählt die Läufe.

    ``contents`` liefert je Lauf den Content der letzten Nachricht.
    """

    def __init__(self, contents):
        self.contents = list(contents)
        self.runs = 0

    def stream(self, _state, stream_mode="values"):
        content = self.contents[min(self.runs, len(self.contents) - 1)]
        self.runs += 1
        yield {"messages": [_Msg("egal"), _Msg(content)]}


def _run(monkeypatch, contents):
    executor = _Executor(contents)
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    messages = [{"role": "user", "content": "Wann findet meine nächste Reise statt?"}]
    events = list(agent.call_stream(messages, "/"))
    return executor, events


def _reply(events):
    responses = [e for e in events if e.get("type") == "response"]
    assert len(responses) == 1, f"genau ein response-Event erwartet, {events}"
    return responses[0]["data"]["reply"]


def test_leere_antwort_wird_wiederholt(monkeypatch):
    executor, events = _run(monkeypatch, ["", "Deine nächste Reise ist die Éire-Reise."])
    assert executor.runs == 2, "leere Antwort muss einen zweiten Versuch auslösen"
    assert "Éire" in _reply(events)


def test_dritter_versuch_rettet_die_antwort_noch(monkeypatch):
    executor, events = _run(monkeypatch, ["", "", "Deine nächste Reise ist die Éire-Reise."])
    assert executor.runs == 3, "nach zwei leeren Läufen muss ein dritter folgen"
    assert "Éire" in _reply(events)


def test_drei_leere_antworten_ergeben_klartext_statt_leerer_blase(monkeypatch):
    executor, events = _run(monkeypatch, ["", "", ""])
    assert executor.runs == 3, "nach dem dritten leeren Lauf wird nicht weiter versucht"
    reply = _reply(events)
    assert reply.strip(), "der Kunde darf nie eine leere Blase bekommen"
    assert "keine Antwort gelungen" in reply


def test_nur_whitespace_zaehlt_als_leer(monkeypatch):
    executor, events = _run(monkeypatch, ["   \n\t ", "Alles klar!"])
    assert executor.runs == 2
    assert "Alles klar!" in _reply(events)


def test_gute_antwort_laeuft_genau_einmal(monkeypatch):
    executor, events = _run(monkeypatch, ["Deine nächste Reise ist die Éire-Reise."])
    assert executor.runs == 1, "eine gute Antwort darf keinen zweiten Modellaufruf kosten"
    assert "Éire" in _reply(events)


def test_leere_antwort_meldet_finish_reason(monkeypatch, capsys):
    """Die bestehende Leer-Diagnose muss weiter feuern."""
    _run(monkeypatch, ["", "Alles klar!"])
    assert "leere Modellantwort" in capsys.readouterr().out


# --- Auffälliger finish_reason irgendwo im Stream -----------------------------
#
# Ein MALFORMED_FUNCTION_CALL, nach dem der Graph sich fängt, ist genau der
# Fall, den die Leer-Diagnose oben NICHT sieht: der Kunde bekommt eine gute
# Antwort, das Signal wäre weg.


class _StreamExecutor:
    """Stubbt den Graph mit vorgegebenen Events.

    ``batches`` ist eine Liste von Message-Listen — je eine pro Event. Mit
    ``stream_mode="values"`` gibt LangGraph bei jedem Event die vollständige
    Historie erneut heraus, die Batches enthalten dieselben Objekte also
    absichtlich mehrfach.
    """

    def __init__(self, batches):
        self.batches = batches
        self.runs = 0

    def stream(self, _state, stream_mode="values"):
        self.runs += 1
        for batch in self.batches:
            yield {"messages": list(batch)}


def _run_stream(monkeypatch, batches):
    executor = _StreamExecutor(batches)
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    messages = [{"role": "user", "content": "Wann findet meine nächste Reise statt?"}]
    events = list(agent.call_stream(messages, "/"))
    return executor, events


def _vorfall_zeilen(capsys):
    return [
        zeile
        for zeile in capsys.readouterr().out.splitlines()
        if "auffälliger finish_reason" in zeile
    ]


def test_malformed_call_wird_geloggt_obwohl_die_antwort_gut_ist(monkeypatch, capsys):
    kaputt = _Msg("", finish_reason="MALFORMED_FUNCTION_CALL", msg_id="m1")
    gut = _Msg("Deine nächste Reise ist die Éire-Reise.", finish_reason="STOP", msg_id="m2")
    executor, events = _run_stream(monkeypatch, [[kaputt], [kaputt, gut]])

    assert executor.runs == 1, "die Antwort war gut, es darf keinen Retry geben"
    assert "Éire" in _reply(events)
    zeilen = _vorfall_zeilen(capsys)
    assert len(zeilen) == 1, f"genau eine Vorfallzeile erwartet, {zeilen}"
    assert "MALFORMED_FUNCTION_CALL" in zeilen[0]


def test_vorfall_wird_nicht_pro_event_wiederholt(monkeypatch, capsys):
    kaputt = _Msg("", finish_reason="MALFORMED_FUNCTION_CALL", msg_id="m1")
    gut = _Msg("Alles klar!", finish_reason="STOP", msg_id="m2")
    _run_stream(
        monkeypatch,
        [[kaputt], [kaputt, gut], [kaputt, gut], [kaputt, gut]],
    )
    assert len(_vorfall_zeilen(capsys)) == 1, "stream_mode=values darf nicht vervielfachen"


def test_vorfall_ohne_id_wird_ebenfalls_nur_einmal_geloggt(monkeypatch, capsys):
    kaputt = _Msg("", finish_reason="MALFORMED_FUNCTION_CALL")
    gut = _Msg("Alles klar!", finish_reason="STOP")
    _run_stream(monkeypatch, [[kaputt], [kaputt, gut], [kaputt, gut]])
    assert len(_vorfall_zeilen(capsys)) == 1


def test_normaler_turn_loggt_nichts(monkeypatch, capsys):
    gut = _Msg("Deine nächste Reise ist die Éire-Reise.", finish_reason="STOP", msg_id="m1")
    tool_zug = _Msg(
        "",
        finish_reason="STOP",
        tool_calls=[{"name": "termine_tool", "args": {"url_path": "/irland"}, "id": "t1"}],
        msg_id="m0",
    )
    _run_stream(monkeypatch, [[tool_zug], [tool_zug, gut]])
    assert capsys.readouterr().out == "", "ein guter Turn darf das Log nicht anfassen"


def test_max_tokens_zaehlt_auch_als_vorfall(monkeypatch, capsys):
    abgeschnitten = _Msg("Teilantwort", finish_reason="MAX_TOKENS", msg_id="m1")
    _run_stream(monkeypatch, [[abgeschnitten]])
    zeilen = _vorfall_zeilen(capsys)
    assert len(zeilen) == 1 and "MAX_TOKENS" in zeilen[0]


def test_log_nennt_toolname_und_gebundene_tools(monkeypatch, capsys):
    kaputt = _Msg(
        "",
        finish_reason="MALFORMED_FUNCTION_CALL",
        tool_calls=[{"name": "termine_tool", "args": {"url_path": "/irland"}, "id": "t1"}],
        msg_id="m1",
    )
    gut = _Msg("Alles klar!", finish_reason="STOP", msg_id="m2")
    _run_stream(monkeypatch, [[kaputt], [kaputt, gut]])
    zeile = _vorfall_zeilen(capsys)[0]
    assert "termine_tool" in zeile, "der Toolname ist der Hebel zur Ursache"
    assert "visa_tool" in zeile, "die gebundenen Tools müssen im Log stehen"
    # Zählerstand beim ERSTEN Auftauchen des Vorfalls, nicht am Turn-Ende.
    assert "nachrichten=1" in zeile


def test_log_enthaelt_keine_kundendaten(monkeypatch, capsys):
    kaputt = _Msg(
        "Ihre Buchung 4711 nach Namibia",
        finish_reason="MALFORMED_FUNCTION_CALL",
        tool_calls=[{"name": "buchungen_tool", "args": {"kunden_id": "4711"}, "id": "t1"}],
        msg_id="m1",
    )
    _run_stream(monkeypatch, [[kaputt]])
    ausgabe = capsys.readouterr().out
    assert "4711" not in ausgabe, "keine Kundennummer im Log"
    assert "Namibia" not in ausgabe, "kein Nachrichtentext im Log"
    assert "kunden_id" not in ausgabe, "keine Tool-Argumente im Log"


def test_jeder_versuch_meldet_seinen_eigenen_vorfall(monkeypatch, capsys):
    """Zwei leere Läufe mit kaputtem Call ergeben zwei Vorfallzeilen."""
    executor = _StreamExecutor([])

    def _stream(_state, stream_mode="values"):
        executor.runs += 1
        # Jeder Lauf erzeugt frische Objekte — wie in echt.
        kaputt = _Msg("", finish_reason="MALFORMED_FUNCTION_CALL", msg_id=None)
        letzte = _Msg("" if executor.runs == 1 else "Alles klar!", finish_reason="STOP")
        yield {"messages": [kaputt, letzte]}

    executor.stream = _stream
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    events = list(agent.call_stream([{"role": "user", "content": "Hallo"}], "/"))

    assert executor.runs == 2
    zeilen = _vorfall_zeilen(capsys)
    assert len(zeilen) == 2, f"pro Versuch ein Vorfall, {zeilen}"
    assert "versuch=1/3" in zeilen[0] and "versuch=2/3" in zeilen[1]
    assert "Alles klar!" in _reply(events)
