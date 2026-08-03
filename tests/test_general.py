import common as _

from agent_base import (
    PAGE_CONTENT_MAX_CHARS,
    detect_recommendation_links,
    markdownify_page_html,
)


def test_recommendation_detection():
    text = "/Afrika/Namibia/Etosha#termine"

    assert detect_recommendation_links(text) == {"/Afrika/Namibia/Etosha#termine"}


def test_agentur_request_detection():
    import app

    with app.app.test_request_context(
        headers={"Origin": "https://agt.chamaeleon-reisen.de"}
    ):
        assert app.is_agentur_request("/Agentur")

    with app.app.test_request_context(
        headers={"Referer": "https://agt.chamdev.tourone.de/Agentur/Buchungen"}
    ):
        assert app.is_agentur_request("/Agentur/Buchungen")

    with app.app.test_request_context(
        headers={"Origin": "https://www.chamaeleon-reisen.de"}
    ):
        assert not app.is_agentur_request("/Afrika/Namibia")


def test_agentur_detection_current_url_fallback():
    """Without Origin/Referer headers, the current_url payload field decides."""
    import app

    with app.app.test_request_context():
        assert app.is_agentur_request("https://agt.chamaeleon-reisen.de/Agentur")

    # Second agentur host via Origin (only Referer was covered above)
    with app.app.test_request_context(
        headers={"Origin": "https://agt.chamdev.tourone.de"}
    ):
        assert app.is_agentur_request("/")

    # No headers, plain endpoint: not an agentur request
    with app.app.test_request_context():
        assert not app.is_agentur_request("/")


def test_chat_stream_threads_agentur_flag(monkeypatch):
    """chat_stream computes the flag in request context and passes it through."""
    import queue

    import app

    calls = []

    def fake_call_stream(
        messages,
        endpoint,
        name,
        telefon,
        is_agentur,
        page_content="",
        kunden_id="",
        agentur_id="",
    ):
        calls.append(is_agentur)
        yield {"type": "response", "data": {"reply": "Hallo!", "recommendations": []}}

    monkeypatch.setattr(app, "call_stream", fake_call_stream)
    monkeypatch.setattr(app, "log_queue", queue.Queue())  # keep tests off the DB

    client = app.app.test_client()
    payload = {
        "session_id": "test-agentur-flag",
        "messages": [{"role": "user", "content": "Hallo"}],
        "current_url": "/Agentur",
    }

    resp = client.post(
        "/chat/stream",
        json=payload,
        headers={"Origin": "https://agt.chamaeleon-reisen.de"},
    )
    assert resp.status_code == 200
    assert "Hallo!" in resp.get_data(as_text=True)

    resp = client.post("/chat/stream", json=payload)
    assert resp.status_code == 200

    assert calls == [True, False]


def test_markdownify_page_html():
    """Client-sent page HTML becomes capped markdown; never raises."""
    md = markdownify_page_html(
        "<main><h1>Buchungen</h1><script>evil()</script><p>Buchung 4711 Namibia</p></main>"
    )
    assert "Buchungen" in md
    assert "Buchung 4711 Namibia" in md
    assert "evil" not in md

    # Hard cap on the markdown that enters the prompt
    md = markdownify_page_html("<p>" + "wort " * 10_000 + "</p>")
    assert len(md) <= PAGE_CONTENT_MAX_CHARS

    # Garbage in, empty string out — never an exception
    assert markdownify_page_html("") == ""
    assert markdownify_page_html("   ") == ""
    assert markdownify_page_html(None) == ""
    assert markdownify_page_html({"a": 1}) == ""
    assert isinstance(markdownify_page_html("<div><p>kaputt"), str)


def test_chat_stream_threads_page_content(monkeypatch):
    """chat_stream converts page_html to markdown for agentur requests only."""
    import queue

    import app

    received = []

    def fake_call_stream(
        messages,
        endpoint,
        name,
        telefon,
        is_agentur,
        page_content="",
        kunden_id="",
        agentur_id="",
    ):
        received.append(page_content)
        yield {"type": "response", "data": {"reply": "Hallo!", "recommendations": []}}

    monkeypatch.setattr(app, "call_stream", fake_call_stream)
    monkeypatch.setattr(app, "log_queue", queue.Queue())  # keep tests off the DB

    client = app.app.test_client()
    payload = {
        "session_id": "test-page-content",
        "messages": [{"role": "user", "content": "Hallo"}],
        "current_url": "/Agentur/Buchungen",
        "page_html": "<main><h1>Buchungen</h1><p>Buchung 4711 Namibia</p></main>",
    }

    resp = client.post(
        "/chat/stream",
        json=payload,
        headers={"Origin": "https://agt.chamdev.tourone.de"},
    )
    assert resp.status_code == 200
    assert "Buchungen" in received[0]
    assert "Buchung 4711 Namibia" in received[0]

    # Same payload without agentur signals: content must be dropped
    payload["current_url"] = "/Afrika/Namibia"
    resp = client.post("/chat/stream", json=payload)
    assert resp.status_code == 200

    # Non-string page_html must not break the request
    payload["current_url"] = "/Agentur/Buchungen"
    payload["page_html"] = {"a": 1}
    resp = client.post(
        "/chat/stream",
        json=payload,
        headers={"Origin": "https://agt.chamdev.tourone.de"},
    )
    assert resp.status_code == 200

    assert received == [received[0], "", ""]


# --- termine tool -------------------------------------------------------------


def test_as_int_coerces_model_supplied_strings():
    # Gemini sends "2027" as readily as 2027, and "" for an omitted optional.
    from agent_base import _as_int

    assert _as_int("2027") == 2027 and _as_int(2027) == 2027
    assert _as_int("") is None and _as_int(None) is None and _as_int("Herbst") is None
    assert _as_int(True) is None  # a bool is a filter mix-up, not a year


def test_filter_label_echoes_the_applied_filter():
    from agent_base import _filter_label

    assert _filter_label(2026, 10, True) == " (Oktober 2026, nur freie)"
    assert _filter_label(2027, None, False) == " (2027)"
    assert _filter_label(None, None, False) == ""


def test_termine_tool_unindexed_url_never_claims_no_termine(monkeypatch):
    import agent_base
    import travel_index

    monkeypatch.setattr(travel_index, "get_reisecodes", lambda url: [])
    out = agent_base.termine_tool_base("/Impressum")
    assert "Das heißt NICHT, dass es keine gibt" in out
    assert "#termine" not in out  # no termine anchor for a page without one


def test_termine_tool_api_failure_is_not_a_sold_out_claim(monkeypatch):
    import agent_base
    import travel_index

    monkeypatch.setattr(travel_index, "get_reisecodes", lambda url: ["A"])

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(travel_index, "query_termine", boom)
    out = agent_base.termine_tool_base("/Afrika/Marokko/Atlas-ALL")
    assert "nicht abrufbar" in out
    assert "ausgebucht" not in out and "Keine Termine" not in out


def test_termine_tool_strips_fragment_and_host(monkeypatch):
    import agent_base
    import travel_index

    seen = []
    monkeypatch.setattr(travel_index, "get_reisecodes", lambda url: seen.append(url) or ["A"])
    monkeypatch.setattr(travel_index, "query_termine", lambda *a, **k: [])
    agent_base.termine_tool_base("https://www.chamaeleon-reisen.de/Afrika/Marokko/Atlas-ALL#termine")
    assert seen == ["/Afrika/Marokko/Atlas-ALL"]


def _fake_stream(sink):
    """call_stream-Ersatz, der (kunden_id, agentur_id) mitschreibt."""

    def fake(messages, endpoint, name, telefon, is_agentur,
             page_content="", kunden_id="", agentur_id=""):
        sink.append((kunden_id, agentur_id))
        yield {"type": "response", "data": {"reply": "Hallo!", "recommendations": []}}

    return fake


def test_agentur_id_kommt_aus_der_bindung_nicht_aus_dem_body(monkeypatch):
    """Die Agenturnummer ist serverseitig abgeleitet — der Body wird ignoriert.

    Das ist DIE Sicherheitseigenschaft des Modus, und sie war ungeprüft: ersetzt
    man den resolve()-Aufruf durch data.get("agentur_id"), wird die Agentur frei
    wählbar und die ganze Suite bleibt trotzdem grün. is_agentur allein schaltet
    ebenfalls nichts frei — es ist nur ein Header-Spiegel.
    """
    import queue

    import agentur_auth
    import app

    gesehen = []
    monkeypatch.setattr(app, "call_stream", _fake_stream(gesehen))
    monkeypatch.setattr(app, "log_queue", queue.Queue())

    client = app.app.test_client()
    agt_header = {"Origin": "https://agt.chamaeleon-reisen.de"}
    payload = {
        "session_id": "agt-sid",
        "messages": [{"role": "user", "content": "Welche Buchungen haben wir?"}],
        "current_url": "/Agentur",
        # Behauptung aus dem Body — muss folgenlos bleiben.
        "agentur_id": "99999",
    }

    client.post("/chat/stream", json=payload, headers=agt_header)   # keine Bindung
    agentur_auth.bind("agt-sid", "12345")
    client.post("/chat/stream", json=payload, headers=agt_header)   # gebunden
    agentur_auth.unbind("agt-sid")

    assert [a for _, a in gesehen] == ["", "12345"]


def test_kunden_und_agentur_modus_schliessen_sich_aus(monkeypatch):
    """Beide Bindungen auf einer session_id: es gilt genau die des Modus.

    Ohne den Guard bekäme ein Agentur-Counter zusätzlich das Kunden-Tool — zwei
    Identitäten in einer Anfrage. Die getrennten Stores (test_agentur_auth) sind
    die halbe Miete; erzwungen wird die Exklusivität aber hier in chat_stream.
    """
    import queue

    import agentur_auth
    import app
    import kunden_auth

    gesehen = []
    monkeypatch.setattr(app, "call_stream", _fake_stream(gesehen))
    monkeypatch.setattr(app, "log_queue", queue.Queue())

    kunden_auth.bind("beide-sid", "999999")
    agentur_auth.bind("beide-sid", "12345")

    client = app.app.test_client()
    payload = {"session_id": "beide-sid", "messages": [{"role": "user", "content": "hi"}]}

    client.post(
        "/chat/stream",
        json={**payload, "current_url": "/Agentur"},
        headers={"Origin": "https://agt.chamaeleon-reisen.de"},
    )
    client.post("/chat/stream", json={**payload, "current_url": "/Afrika/Namibia"})

    kunden_auth.unbind("beide-sid")
    agentur_auth.unbind("beide-sid")

    assert gesehen == [("", "12345"), ("999999", "")]
