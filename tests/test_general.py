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
    assert "Agenturbereich Chamäleon" in prompt
    assert "Expi-Ermäßigung" in prompt

    # The KB is clean, prompt-ready markdown: no operator scaffolding may leak
    # into the prompt. This guard fails CI if a future edit reintroduces any.
    assert "Interne Betreiberhinweise" not in prompt
    assert "TODO Betreiber" not in prompt
    assert "<!--" not in prompt

    # Language rule from the Reisebüro feedback: the non-word "Erklärungsvideo"
    # must never come back — use "Video-Tutorial".
    assert "Erklärungsvideo" not in prompt

    prompt = format_system_prompt("/", [])
    assert "Agenturbereich Chamäleon" not in prompt


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

    def fake_call_stream(
        messages, endpoint, name, telefon, is_agentur, page_content="", kunden_id=""
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

    def fake_call_stream(
        messages, endpoint, name, telefon, is_agentur, page_content="", kunden_id=""
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
