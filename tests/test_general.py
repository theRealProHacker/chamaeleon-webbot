import common as _

from agent_base import (
    PAGE_CONTENT_MAX_CHARS,
    detect_recommendation_links,
    format_system_prompt,
    markdownify_page_html,
)


def test_recommendation_detection():
    text = "/Afrika/Namibia/Etosha#termine"

    assert detect_recommendation_links(text) == {"/Afrika/Namibia/Etosha#termine"}


def test_agentur_wissensbasis_injection():
    prompt = format_system_prompt("/", [], is_agentur=True)
    assert "Wissensbasis für Chatbot Leon" in prompt
    assert "Expi-Ermäßigung" in prompt

    # Operator-only scaffolding is stripped at load and never reaches the prompt
    assert "Interne Betreiberhinweise" not in prompt
    assert "TODO Betreiber" not in prompt
    assert "<!--" not in prompt

    prompt = format_system_prompt("/", [])
    assert "Wissensbasis für Chatbot Leon" not in prompt


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


def test_agentur_block_placement():
    """The agentur knowledge base sits between the general and country FAQs."""
    prompt = format_system_prompt("/", [], is_agentur=True)
    assert (
        prompt.index("Allgemeine FAQs:")
        < prompt.index("Agenturbereich:")
        < prompt.index("Länderspezifische FAQs:")
    )
    assert "{agentur_block}" not in prompt

    prompt = format_system_prompt("/", [])
    assert "Agenturbereich:" not in prompt
    assert "{agentur_block}" not in prompt
    assert "Länderspezifische FAQs:" in prompt


def test_chat_stream_threads_agentur_flag(monkeypatch):
    """chat_stream computes the flag in request context and passes it through."""
    import queue

    import app

    calls = []

    def fake_call_stream(messages, endpoint, name, telefon, is_agentur, page_content=""):
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


def test_page_content_injection():
    """Page content appears in the prompt only for agentur requests with content."""
    prompt = format_system_prompt(
        "/Agentur/Buchungen", [], is_agentur=True, page_content="Buchung 4711 Namibia"
    )
    assert "Buchung 4711 Namibia" in prompt
    assert "--- Seiteninhalt Anfang ---" in prompt
    assert (
        prompt.index("Der Kunde befindet sich gerade auf folgender Webseite")
        < prompt.index("Inhalt der aktuellen Seite:")
    )
    assert "{page_content_block}" not in prompt

    # No content, or not agentur: no block
    prompt = format_system_prompt("/Agentur/Buchungen", [], is_agentur=True)
    assert "Seiteninhalt" not in prompt

    prompt = format_system_prompt("/", [], page_content="Buchung 4711 Namibia")
    assert "Buchung 4711 Namibia" not in prompt
    assert "Seiteninhalt" not in prompt

    # www regression: prompt is free of the slot and the block
    prompt = format_system_prompt("/", [])
    assert "Seiteninhalt" not in prompt
    assert "{page_content_block}" not in prompt


def test_chat_stream_threads_page_content(monkeypatch):
    """chat_stream converts page_html to markdown for agentur requests only."""
    import queue

    import app

    received = []

    def fake_call_stream(messages, endpoint, name, telefon, is_agentur, page_content=""):
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
