import common as _

from agent_base import detect_recommendation_links, format_system_prompt


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

    def fake_call_stream(messages, endpoint, name, telefon, is_agentur):
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
