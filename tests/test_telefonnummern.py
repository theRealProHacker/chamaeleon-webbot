"""Eine Telefonnummer aus einem Tool muss Ziffer für Ziffer beim Kunden ankommen.

Live 2026-10-02 (Buchung 199095): reiseinfo_tool lieferte
"Reiseleitung: Ramy Faraag, Tel. +96897081877", Gemini schrieb in 2 von 6
Läufen "+968970818877" (eine 8 zu viel). Der Code dazwischen ändert keine
Ziffer; es ist ein Kopierfehler des Modells. Der Test stubbt nur
create_react_agent, es braucht weder Modell noch TourOne.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import agent
from agent import telefonnummern_angleichen

TOOL = "Reiseleitung: Ramy Faraag, Tel. +96897081877\n- leistungen ..."
PROMPT = "Zentrale: +49 30 347 996 0. Erlebnisberater*in: Team Amerika, +49 30 347996-228"
QUELLEN = TOOL + "\n" + PROMPT


def test_zusaetzliche_ziffer_wird_auf_die_quelle_zurueckgesetzt():
    reply = 'Tel. <a href="tel:+968970818877">+968970818877</a>'
    assert telefonnummern_angleichen(reply, QUELLEN) == (
        'Tel. <a href="tel:+96897081877">+96897081877</a>'
    )


def test_fehlende_oder_vertauschte_ziffer_wird_korrigiert():
    assert telefonnummern_angleichen("+9689708877", QUELLEN) == "+96897081877"
    assert telefonnummern_angleichen("+96897081878", QUELLEN) == "+96897081877"


def test_richtige_nummer_bleibt_auch_anders_gruppiert():
    for reply in ("+96897081877", "+968 9708 1877", "+49 30 347996-228", "+49303479960"):
        assert telefonnummern_angleichen(reply, QUELLEN) == reply


def test_fremde_und_kurze_zahlen_bleiben_unberuehrt():
    for reply in (
        "+12345678901",  # weit weg von jeder Quelle
        "Vorgang 199095",  # Buchungsnummer, zu kurz für eine Telefonnummer
        "vom 12.03.2026 bis 14.03.2026 für 2.499 €",
    ):
        assert telefonnummern_angleichen(reply, QUELLEN) == reply


def test_ohne_eindeutige_quelle_wird_nichts_geraten():
    zwei = "+49 30 1234567 und +49 30 1234569"
    assert telefonnummern_angleichen("+49 30 1234568", zwei) == "+49 30 1234568"


class _Executor:
    def __init__(self, messages):
        self.messages = messages

    def stream(self, _state, stream_mode="values"):
        yield {"messages": self.messages}


def test_call_stream_gibt_die_nummer_aus_dem_tool_weiter(monkeypatch):
    messages = [
        SystemMessage(content=PROMPT),
        HumanMessage(content="Wer ist mein Reiseleiter?"),
        AIMessage(content="", tool_calls=[{"name": "reiseinfo_tool", "args": {}, "id": "1"}]),
        ToolMessage(content=TOOL, tool_call_id="1"),
        AIMessage(content="Dein Reiseleiter ist Ramy Faraag, Tel. +968970818877."),
    ]
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: _Executor(messages))
    events = list(agent.call_stream([{"role": "user", "content": "Wer ist mein Reiseleiter?"}], "/"))
    reply = [e for e in events if e["type"] == "response"][0]["data"]["reply"]
    assert "+96897081877" in reply
    assert "+968970818877" not in reply
