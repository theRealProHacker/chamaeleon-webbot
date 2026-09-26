"""Ankündigung ohne Tool-Aufruf bekommt genau einen Anstoß.

Gemessen 2026-09-25 (Katharinas Mail, Punkt 10): "Super, Namibia ist eine
fantastische Wahl! Ich schaue mal, welche Reisen für euch passen." — und dann
nichts, kein Tool-Aufruf. Wie in ``test_empty_reply.py`` wird nur
create_react_agent gestubbt, kein Modell- und kein Netzaufruf.

    pytest tests/test_ankuendigung.py -v
"""

import pytest

import agent

ANKUENDIGUNG = "Super, Namibia ist eine fantastische Wahl! Ich schaue mal, welche Reisen passen."
ANTWORT = "Für 14 Tage passt Sossusvlei."


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.response_metadata = {}
        self.usage_metadata = {}
        self.id = None


class _Executor:
    """Je Lauf: (Content der letzten Nachricht, ob in diesem Lauf ein Tool lief)."""

    def __init__(self, laeufe):
        self.laeufe = list(laeufe)
        self.eingaben = []

    def stream(self, state, stream_mode="values"):
        self.eingaben.append(state["messages"])
        content, mit_tool = self.laeufe[min(len(self.eingaben) - 1, len(self.laeufe) - 1)]
        tool = [{"name": "chamaeleon_website_tool", "args": {}, "id": "1"}] if mit_tool else []
        yield {"messages": [_Msg("", tool_calls=tool), _Msg(content)]}


def _run(monkeypatch, laeufe):
    executor = _Executor(laeufe)
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **kw: executor)
    events = list(agent.call_stream([{"role": "user", "content": "namibia"}], "/"))
    reply = [e for e in events if e["type"] == "response"][0]["data"]["reply"]
    return executor, reply


def test_ankuendigung_ohne_tool_wird_angestossen(monkeypatch):
    executor, reply = _run(monkeypatch, [(ANKUENDIGUNG, False), (ANTWORT, True)])
    assert len(executor.eingaben) == 2
    assert executor.eingaben[1][-1].content == agent._ANSTOSS
    assert "Sossusvlei" in reply


def test_nur_ein_anstoss(monkeypatch):
    executor, reply = _run(monkeypatch, [(ANKUENDIGUNG, False), (ANKUENDIGUNG, False)])
    assert len(executor.eingaben) == 2
    assert "schaue mal" in reply


def test_ankuendigung_mit_tool_aufruf_bleibt(monkeypatch):
    executor, _ = _run(monkeypatch, [("Ich schaue gern weiter, wenn du magst: Sossusvlei.", True)])
    assert len(executor.eingaben) == 1


def test_normale_antwort_laeuft_einmal(monkeypatch):
    executor, _ = _run(monkeypatch, [(ANTWORT, False)])
    assert len(executor.eingaben) == 1


@pytest.mark.parametrize(
    "text, erwartet",
    [
        ("Ich schaue mal, welche Reisen passen.", True),
        ("Einen Moment, ich sehe nach.", True),
        ("Da prüfe ich kurz die Termine.", True),
        ("Für 14 Tage passt Sossusvlei.", False),
        ("Schau mal hier: Sossusvlei.", False),
        ("Super, Namibia ist eine tolle Wahl! Ich schaue mal, welche Reisen passen.", True),
        ("<p>Ich sehe kurz nach. Einen Moment bitte!</p>", True),
        # Rueckfragen und Antworten mit Ergebnis bleiben (Review 2026-09-26)
        ("Nenn mir bitte deine Buchungsnummer, dann prüfe ich das gern für dich.", False),
        ("Hast du einen Moment Zeit für ein Telefonat mit Noelle?", False),
        ("Ich suche dir gern etwas raus: Da passen Outeniqua (15 Tage) und Pinotage.", False),
    ],
)
def test_kuendigt_nur_an(text, erwartet):
    assert agent.kuendigt_nur_an(text) is erwartet
