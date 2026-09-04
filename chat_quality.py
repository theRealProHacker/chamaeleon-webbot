"""LLM classification of chat quality, causes and countries — one run per month.

This is the only part of the dashboard that costs money, and the only part that
is not deterministic. What it produces per conversation:

    {"qualitaet": "ausgewichen", "ursache_id": "u04",
     "laender": ["Namibia"], "beleg": 3}

The counting unit is the CONVERSATION, not the message. That is a deliberate
change of axis (gate decision C2): a ranking of topics says what people talk
about, which is already known (trip info, booking, prices). What is not known is
where the bot falls down. Work follows from a cause list — a prompt fix, an FAQ
snippet, a tool. Nothing follows from a topic ranking. It also halves the output
side: July has 4.193 user messages but only 1.962 chats.

``beleg`` is the INDEX of the assistant message, never its text, so the payload
carries no transcript material (SC3).

Countries (A5): the URL does not determine the country — "someone can ask about
Namibia from the homepage". So the model reads the conversation, and the URL is
only a prior in the prompt. The vocabulary is closed, because free-form country
strings do not aggregate. A chat counts for EACH of its countries, so the
country sums exceed the chat count and SC1's sum invariant does not apply here.

Three signals go into the prompt, none of them as a lookup behind it:
  - the trip-page prior from ``travel_index`` (28,6 % of chats),
  - the country-landing-page prior from ``agent_base.all_countries`` (39,5 %),
  - a recall floor: substring hits against the country vocabulary, the same
    mechanism that has run in production in ``agent.py`` for months.

Determinism, and its two limits: ``temperature=0`` is not a guarantee on Gemini,
and BATCH COMPOSITION is part of the input — a resumed run that re-batches
differently gives a chat different neighbours. Batches are therefore cut
deterministically over sorted ``chat_db_id`` and the batch index is recorded in
the per-chat artifact.

Importing this module does no I/O and builds no model. Everything expensive is
behind a function call.
"""

import hashlib
import json
import os
import re
import time
from typing import Any, Iterable, Literal, Sequence

from pydantic import BaseModel, Field

MODEL_NAME = "gemini-2.5-flash"
PROMPT_VERSION = 3

# Rough characters-per-token for German prose through a Gemini tokenizer. Only
# used to cut batches; being 20 % off costs a round trip, not correctness.
CHARS_PER_TOKEN = 4.0

# Input-token budget per call. Measured: a whole month is ~398k tokens
# (1.593.692 characters of full transcript), so a month is ~7 calls, not 76.
# The first draft said "25 chats", which is ~5k tokens — most of the wall time
# of that design was latency from batches too small to be worth a round trip.
BATCH_TOKEN_BUDGET = 60_000
# Cap for a single outlier chat so one runaway conversation cannot blow a batch.
MAX_CHAT_TOKENS = 6_500

# Chats per call. Not a hard model limit — measured, the model happily answers
# 280 in one go, and separately it will answer 1 of 10. The failure is FLAKY,
# not size-dependent: a well-formed response that covers one conversation out of
# the batch, no error and no truncation flag. On the August 2026 run that
# silently left 788 of 1.734 chats unclassified. Smaller batches do not prevent
# it, they make retrying it cheap.
MAX_CHATS_PER_BATCH = 80

# Below this, splitting a short batch further is not worth the round trip.
MIN_RETRY_BATCH = 4
# Explicit in code, not in someone's head (Eng re-review).
MAX_CHATS_PER_RUN = 5_000
MAX_RETRIES_PER_BATCH = 3

# Minimum size for a cause to be shown at all, and the promotion threshold for
# `neu` (answered question in the design: k = 20).
MIN_CAUSE_SIZE = 20
# Same threshold for the topic axis. Kept as its own name rather than reusing
# MIN_CAUSE_SIZE: the two axes have different natural cardinalities (a handful
# of recurring topics against a long tail of failure causes) and will drift
# apart the first time either is tuned.
MIN_TOPIC_SIZE = 20
# A cause below k in this many consecutive months is retired (A4).
RETIRE_AFTER_MONTHS = 3

RUN_DIR = "data/quality_runs"

type Quality = Literal["beantwortet", "ausgewichen", "falsch", "abgebrochen"]

QUALITIES: tuple[str, ...] = ("beantwortet", "ausgewichen", "falsch", "abgebrochen")

# The two classes that carry v1. `falsch` asks the model to judge the factual
# correctness of an answer written by its own model family, so it is kept but
# never headlined as a number — only as a way into the conversations.
LOAD_BEARING_QUALITIES: tuple[str, ...] = ("ausgewichen", "abgebrochen")

NEW_CAUSE_ID = "neu"
# Same sentinel on the topic axis, deliberately the same string: both mean
# "did not fit the list", and the dashboard reports both the same way.
NEW_TOPIC_ID = "neu"


# ──────────────────────────────────────────
# Vocabulary and priors (A5)
# ──────────────────────────────────────────


def country_vocabulary() -> list[str]:
    """The closed country vocabulary: ``agent_base.laender_faqs`` (73 countries).

    Explicitly NOT ``faqs/laender.json``. That file has 31 entries, is cut off
    alphabetically per continent (Europe ends at "Island", Africa at "Mosambik"),
    contains neither Namibia nor Tansania nor Südafrika — the three biggest
    destinations — lists combination strings like "Argentinien/Chile" as a single
    country, and is loaded by no Python module at all. A closed vocabulary
    without Namibia would have dropped Namibia chats onto `ohne Land` or, worse,
    onto Botswana.
    """
    import agent_base

    return sorted(agent_base.laender_faqs)


def _travel_index_peek(url_path: str) -> str | None:
    """Country for a trip URL, WITHOUT triggering an index build.

    Deliberately reads ``travel_index._index`` rather than calling
    ``get_reisecodes()``, which runs ``ensure_built()``: shortly after a deploy
    that would park the whole classification run behind a synchronous, minutes
    long index rebuild. No prior beats a blocking prior.
    """
    import travel_index

    entry = travel_index._index.get(travel_index._url_key(url_path))
    if not entry:
        return None
    land = entry.get("land")
    return land if isinstance(land, str) and land.strip() else None


def _landing_page_peek(url_path: str) -> str | None:
    """Country for a ``/<Kontinent>/<Land>`` landing page, from the sitemap."""
    import agent_base

    parts = [p for p in url_path.split("?")[0].split("#")[0].split("/") if p]
    if len(parts) != 2:
        return None
    return agent_base.all_countries.get(parts[1])


def url_prior(urls: Iterable[str], vocabulary: Sequence[str]) -> list[str]:
    """Countries suggested by the pages a chat happened on.

    Covers a measured 68 % of chats between the two sources, and covers nothing
    at all for the rest — which is exactly why it is a prior in the prompt and
    not the assignment itself.
    """
    found: list[str] = []
    allowed = set(vocabulary)
    for url in urls:
        path = url.split("?")[0].split("#")[0]
        for candidate in (_travel_index_peek(path), _landing_page_peek(path)):
            if candidate and candidate in allowed and candidate not in found:
                found.append(candidate)
    return found


def mentioned_countries(messages: Sequence[Any], vocabulary: Sequence[str]) -> list[str]:
    """Recall floor: countries named literally in the conversation.

    Same substring match that ``agent.py`` has used in production for months to
    decide which country FAQs to inject. Free, deterministic, and handed to the
    model as "im Text erwähnt: …" — the model still decides on contradictions
    and context, it just does not have to find the mention itself.
    """
    found: list[str] = []
    for country in vocabulary:
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and country in content:
                found.append(country)
                break
    return found


# ──────────────────────────────────────────
# Transcript rendering
# ──────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(content: Any) -> str:
    """Message content as plain text. Bot replies are HTML (agent_base)."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    else:
        return ""
    return _TAG_RE.sub(" ", text).strip()


# A prepared conversation is a plain dict, not a class: this codebase keeps
# per-instance state in dicts with module functions taking them first.
#
#   {"chat_db_id": str, "urls": [str], "turns": [(role, text)],
#    "assistant_indices": [int]}


def user_message_count(chat: dict) -> int:
    return sum(1 for role, _ in chat["turns"] if role == "user")


def turns_as_messages(chat: dict) -> list[dict]:
    """Turns in the ``{role, content}`` shape the recall floor expects."""
    return [{"role": role, "content": text} for role, text in chat["turns"]]


def render_chat(chat: dict) -> str:
    lines = []
    for i, (role, text) in enumerate(chat["turns"]):
        speaker = "NUTZER" if role == "user" else "BOT"
        lines.append(f"[{i}] {speaker}: {text}")
    return "\n".join(lines)


def token_estimate(chat: dict) -> int:
    return min(int(len(render_chat(chat)) / CHARS_PER_TOKEN) + 40, MAX_CHAT_TOKENS)


def prepare_chat(row: Any) -> dict | None:
    """Turn a raw chat row into a prepared chat dict, or None if nothing to judge."""
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    turns: list[tuple[str, str]] = []
    assistant_indices: list[int] = []
    urls: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            # recommendation_previews and friends are UI payload, not dialogue.
            continue
        url = message.get("url")
        if isinstance(url, str) and url.strip() and url not in urls:
            urls.append(url)
        text = _plain(message.get("content"))
        if not text:
            continue
        if role == "assistant":
            assistant_indices.append(len(turns))
        turns.append((role, text))
    if not any(role == "user" for role, _ in turns):
        return None
    return {
        "chat_db_id": str(row.get("id") or ""),
        "urls": urls,
        "turns": turns,
        "assistant_indices": assistant_indices,
    }


def batch_chats(chats: Sequence[dict], budget: int = BATCH_TOKEN_BUDGET) -> list[list[dict]]:
    """Cut chats into batches under a token budget, deterministically.

    Sorted by ``chat_db_id`` first: batch composition is part of the model input,
    so a resumed run must rebuild the same batches or the same chat gets
    different neighbours and a different answer.
    """
    ordered = sorted(chats, key=lambda c: c["chat_db_id"])
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for chat in ordered:
        tokens = token_estimate(chat)
        over_input = current and current_tokens + tokens > budget
        over_output = len(current) >= MAX_CHATS_PER_BATCH
        if over_input or over_output:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(chat)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


class IncompleteBatch(RuntimeError):
    """The model answered, but not about every conversation in the batch."""


def classify_with_completeness(
    chats: Sequence[dict],
    causes: Sequence[dict],
    vocabulary: Sequence[str],
    topics: Sequence[dict] = (),
) -> tuple[list[dict], str]:
    """Classify a batch, insisting on a verdict for every chat in it.

    A short answer is treated as a FAILED call, not as a partial result, because
    that is what it is: the same batch re-asked usually comes back complete.
    Accepting it instead is how a month ends up 45 % unclassified with a green
    log — the counts then look like a quieter month rather than a broken run.

    Order: ask, retry the whole batch, then split what is still missing. Only
    when a single conversation still gets no verdict after all of that does it
    end up in ``unmapped``, which is then a real finding rather than noise.
    """
    phash = ""
    verdicts: list[dict] = []

    def attempt() -> list[dict]:
        nonlocal phash
        result, phash = classify_batch(chats, causes, vocabulary, topics)
        answered = {v["chat_db_id"] for v in result}
        if len(answered) < len(chats):
            raise IncompleteBatch(
                f"{len(answered)} of {len(chats)} conversations answered"
            )
        return result

    try:
        return _with_retries(attempt, f"batch of {len(chats)}"), phash
    except IncompleteBatch:
        pass
    except Exception as exc:  # noqa: BLE001
        if len(chats) <= MIN_RETRY_BATCH:
            raise
        print(f"[chat-quality] batch of {len(chats)} failed ({exc}); splitting")

    # Keep whatever the last attempt did answer, then split the rest.
    try:
        verdicts, phash = classify_batch(chats, causes, vocabulary, topics)
    except Exception:  # noqa: BLE001
        verdicts = []
    answered = {v["chat_db_id"] for v in verdicts}
    missing = [c for c in chats if c["chat_db_id"] not in answered]
    if not missing:
        return verdicts, phash
    if len(chats) <= MIN_RETRY_BATCH:
        print(f"[chat-quality] giving up on {len(missing)} chats in a batch of {len(chats)}")
        return verdicts, phash

    half = max(MIN_RETRY_BATCH, (len(missing) + 1) // 2)
    print(f"[chat-quality] batch of {len(chats)} short by {len(missing)}; splitting")
    for start in range(0, len(missing), half):
        chunk = missing[start : start + half]
        try:
            extra, _ = classify_with_completeness(chunk, causes, vocabulary, topics)
            verdicts.extend(extra)
        except Exception as exc:  # noqa: BLE001
            print(f"[chat-quality] sub-batch of {len(chunk)} failed for good: {exc}")
    return verdicts, phash


# ──────────────────────────────────────────
# Model access
# ──────────────────────────────────────────


def _model():
    """Own model instance for classification — NOT the ``agent.py`` singleton.

    That one runs at ``temperature=0.1`` because it is writing to customers. A
    classifier that changes its mind between runs cannot support a month-over-
    month comparison, so this one is pinned at 0 with thinking off.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    from agent_base import GEMINI_API_KEY

    return ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=GEMINI_API_KEY,
        temperature=0,
        thinking_budget=0,
    )


def _with_retries(action, what: str, attempts: int = MAX_RETRIES_PER_BATCH):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 — the caller decides what a failure means
            last = exc
            print(f"[chat-quality] {what} failed (attempt {attempt + 1}/{attempts}): {exc}")
            if attempt < attempts - 1:
                time.sleep(2**attempt)
    raise last or RuntimeError(f"{what} failed")


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ──────────────────────────────────────────
# Stage 1 — cause taxonomy (T16)
# ──────────────────────────────────────────


class Cause(BaseModel):
    ursache_id: str = Field(description="Stabile ID, Format u01, u02, …")
    label: str = Field(description="Kurzes Label, max 6 Wörter, paraphrasiert")
    definition: str = Field(description="Ein Satz, der die Ursache abgrenzt")


class Taxonomy(BaseModel):
    ursachen: list[Cause]


class Topic(BaseModel):
    thema_id: str = Field(description="Stabile ID, Format t01, t02, …")
    label: str = Field(description="Kurzes Label, max 4 Wörter, paraphrasiert")
    definition: str = Field(description="Ein Satz, der das Thema abgrenzt")


class TopicTaxonomy(BaseModel):
    themen: list[Topic]


TAXONOMY_PROMPT = """Du analysierst Gespräche zwischen Nutzern und dem Chatbot des \
Reiseveranstalters Chamäleon Reisen.

Deine Aufgabe: Finde die wiederkehrenden URSACHEN dafür, dass der Bot eine Frage \
NICHT gut beantwortet hat. Nicht die Themen der Fragen — die Ursachen der \
schlechten Antworten.

Beispiele für die Art von Ursache, die gesucht ist:
- der Bot verweist auf das Reisebüro, statt die Frage zu beantworten
- der Bot kennt die Preise einer Reise nicht
- der Bot beantwortet eine Frage zu Visa-Bestimmungen unvollständig

Regeln:
- {min_causes} bis {max_causes} Ursachen.
- Jede Ursache muss in mindestens {min_size} der Gespräche vorkommen — was \
seltener ist, gehört nicht in die Liste.
- IDs strikt fortlaufend: u01, u02, u03, …
- Labels sind paraphrasiert und enthalten KEINE wörtlichen Zitate, keine Namen, \
keine Buchungsnummern und keine Ortsangaben aus den Gesprächen.
- Definition: genau ein Satz, der die Ursache von den anderen abgrenzt.

Hier sind {n} Gespräche:

{transcripts}"""


TOPIC_TAXONOMY_PROMPT = """Du analysierst Gespräche zwischen Nutzern und dem Chatbot des \
Reiseveranstalters Chamäleon Reisen.

Deine Aufgabe: Finde die wiederkehrenden THEMEN, über die die Nutzer sprechen. \
Nicht die Fehler des Bots — das ANLIEGEN des Nutzers.

Beispiele für die Art von Thema, die gesucht ist:
- Preise und Kosten einer Reise
- Visum und Einreise
- Impfungen und Gesundheit
- Flüge und Anreise
- Klima und Reisezeit

Regeln:
- {min_topics} bis {max_topics} Themen.
- Jedes Thema muss in mindestens {min_size} der Gespräche vorkommen — was \
seltener ist, gehört nicht in die Liste.
- Die Themen müssen sich gegenseitig ausschließen: jedes Gespräch soll in genau \
eines fallen. Bei mehreren Anliegen zählt das, worum es hauptsächlich geht.
- KEIN Thema, das ein Reiseland benennt. Länder werden getrennt erfasst; \
„Namibia" ist kein Thema, „Klima und Reisezeit" ist eines.
- IDs strikt fortlaufend: t01, t02, t03, …
- Labels sind paraphrasiert und enthalten KEINE wörtlichen Zitate, keine Namen, \
keine Buchungsnummern und keine Ortsangaben aus den Gesprächen.
- Definition: genau ein Satz, der das Thema von den anderen abgrenzt.

Hier sind {n} Gespräche:

{transcripts}"""


def build_topic_taxonomy(
    chats: Sequence[dict],
    min_topics: int = 8,
    max_topics: int = 14,
) -> tuple[list[dict], str]:
    """Derive the topic taxonomy from a sample. Returns (topics, prompt_hash).

    Same sample and same continuity rules as the causes, one separate call: the
    two axes answer different questions and a single prompt asked to do both
    reliably collapsed one into the other.
    """
    transcripts = "\n\n---\n\n".join(
        f"### Gespräch {i}\n{render_chat(chat)}" for i, chat in enumerate(chats)
    )
    prompt = TOPIC_TAXONOMY_PROMPT.format(
        min_topics=min_topics,
        max_topics=max_topics,
        min_size=MIN_TOPIC_SIZE,
        n=len(chats),
        transcripts=transcripts,
    )
    model = _model().with_structured_output(TopicTaxonomy)
    result: TopicTaxonomy = _with_retries(lambda: model.invoke(prompt), "topic-taxonomy")  # type: ignore
    topics = [
        {
            "thema_id": t.thema_id,
            "label": sanitize_label(t.label),
            "definition": sanitize_label(t.definition),
        }
        for t in result.themen
    ]
    return topics, prompt_hash(prompt)


def build_taxonomy(
    chats: Sequence[dict],
    min_causes: int = 10,
    max_causes: int = 20,
) -> tuple[list[dict], str]:
    """Derive the cause taxonomy from a sample. Returns (causes, prompt_hash).

    The sample must be stratified over segments AND over the months it comes
    from: a purely chronological seed knows nothing about MeinChamäleon or
    Agentur, which only went live in July and late July/August 2026.
    """
    transcripts = "\n\n---\n\n".join(
        f"### Gespräch {i}\n{render_chat(chat)}" for i, chat in enumerate(chats)
    )
    prompt = TAXONOMY_PROMPT.format(
        min_causes=min_causes,
        max_causes=max_causes,
        min_size=MIN_CAUSE_SIZE,
        n=len(chats),
        transcripts=transcripts,
    )
    model = _model().with_structured_output(Taxonomy)
    result: Taxonomy = _with_retries(lambda: model.invoke(prompt), "taxonomy")  # type: ignore
    causes = [
        {
            "ursache_id": c.ursache_id,
            "label": sanitize_label(c.label),
            "definition": sanitize_label(c.definition),
        }
        for c in result.ursachen
    ]
    return causes, prompt_hash(prompt)


def sanitize_label(text: str) -> str:
    """Strip markup characters from model-written text before it is stored.

    The chain here is longer than it looks: user text -> Gemini -> label AND
    one-sentence definition -> persisted in `month_stats` -> rendered in the
    dashboard. The frontend renders these with `textContent`, which is the real
    defence; this is the second one, at the write boundary, so a future renderer
    that reaches for `innerHTML` cannot turn a stored label into markup.

    Not HTML-escaping: these strings are shown as text, and an escaped label
    would read as `&lt;` to a human. Angle brackets simply have no business in a
    six-word German label.
    """
    return text.replace("<", "").replace(">", "").strip()


# ──────────────────────────────────────────
# Stage 2 — per-conversation classification (T07 + T15)
# ──────────────────────────────────────────


class ChatVerdict(BaseModel):
    idx: int = Field(description="Nummer des Gesprächs aus der Eingabe")
    qualitaet: str = Field(description="beantwortet | ausgewichen | falsch | abgebrochen")
    ursache_id: str = Field(description="ID aus der Taxonomie, oder 'neu'. Bei 'beantwortet': ''")
    thema_id: str = Field(default="", description="Themen-ID aus der Themenliste, oder 'neu'")
    laender: list[str] = Field(default_factory=list, description="Länder aus dem Vokabular")
    beleg: int = Field(default=-1, description="Nummer der belegenden BOT-Nachricht, sonst -1")


class BatchVerdict(BaseModel):
    gespraeche: list[ChatVerdict]


CLASSIFY_PROMPT = """Du bewertest Gespräche zwischen Nutzern und dem Chatbot des \
Reiseveranstalters Chamäleon Reisen. Pro Gespräch gibst du GENAU EINE Bewertung ab.

## qualitaet — wähle einen Wert

- `beantwortet`: der Bot hat die Fragen des Nutzers inhaltlich beantwortet.
- `ausgewichen`: der Bot hat die Frage erkennbar nicht beantwortet, sondern \
umgangen, auf eine andere Stelle verwiesen oder allgemein geantwortet.
- `falsch`: die Antwort war inhaltlich falsch.
- `abgebrochen`: der Nutzer hat erkennbar aufgegeben — er stellt dieselbe Frage \
erneut, äußert Unzufriedenheit oder bricht mitten in einem offenen Anliegen ab.

WICHTIG zu `abgebrochen`: schließe das NICHT daraus, dass das Gespräch endet oder \
dass die letzte Nachricht vom Bot kommt. Der Bot beendet jede Antwort mit einer \
Rückfrage, deshalb sieht praktisch jedes Gespräch strukturell abgebrochen aus, \
und über die Hälfte der Gespräche besteht aus genau einer Nutzernachricht. Ein \
Gespräch mit nur einer Nutzernachricht ist NIE `abgebrochen` — dort gibt es \
keinen Beleg dafür. Verlange einen positiven Beleg im Nutzertext.

Achte ebenso darauf, dass Ausweichen hier sprachlich unauffällig aussieht: dem \
Bot ist per Systemprompt verboten, „leider" zu sagen oder zu verneinen. Ein \
freundlicher, flüssiger Absatz, der die Frage nicht beantwortet, ist \
`ausgewichen`.

## ursache_id

Bei `beantwortet`: leerer String. Sonst die passende ID aus dieser Taxonomie:

{taxonomy}

Passt keine: `neu`.

## thema_id

Worum es dem Nutzer geht — für JEDES Gespräch, auch für ein gut beantwortetes. \
Genau eine ID aus dieser Liste:

{topics}

Bei mehreren Anliegen: das hauptsächliche. Passt keins: `neu`. Ein Reiseland ist \
kein Thema — das steht getrennt unter `laender`.

## laender

Die im Gespräch tatsächlich besprochenen Reiseländer, AUSSCHLIESSLICH aus diesem \
Vokabular (exakte Schreibweise übernehmen):

{vocabulary}

Kein Land besprochen: leere Liste. Mehrere Länder (Kombireise, Vergleich): alle \
nennen. Nimm ein Land NICHT auf, nur weil die Seite dazu gehört — es muss im \
Gespräch vorkommen.

## beleg

Die Nummer in eckigen Klammern der BOT-Nachricht, die deine Bewertung belegt. \
Bei `beantwortet` oder wenn es keine gibt: -1. Gib NIE Text als Beleg zurück.

## Gespräche

{chats}"""


def _format_taxonomy(causes: Sequence[dict]) -> str:
    if not causes:
        return "(noch keine Taxonomie — vergib für jedes nicht beantwortete Gespräch `neu`)"
    return "\n".join(f"- {c['ursache_id']}: {c['label']} — {c['definition']}" for c in causes)


def _format_topics(topics: Sequence[dict]) -> str:
    if not topics:
        return "(noch keine Themenliste — vergib für jedes Gespräch `neu`)"
    return "\n".join(f"- {t['thema_id']}: {t['label']} — {t['definition']}" for t in topics)


def _format_chat_block(idx: int, chat: dict, vocabulary: Sequence[str]) -> str:
    priors = url_prior(chat["urls"], vocabulary)
    mentioned = mentioned_countries(turns_as_messages(chat), vocabulary)
    hints = []
    if priors:
        hints.append(f"Seitenkontext deutet auf: {', '.join(priors)}")
    if mentioned:
        hints.append(f"im Text erwähnt: {', '.join(mentioned)}")
    hint_line = ("\n(" + " · ".join(hints) + ")") if hints else ""
    return f"### Gespräch {idx}{hint_line}\n{render_chat(chat)}"


def classify_batch(
    chats: Sequence[dict],
    causes: Sequence[dict],
    vocabulary: Sequence[str],
    topics: Sequence[dict] = (),
) -> tuple[list[dict], str]:
    """Classify one batch. Returns (verdicts, prompt_hash).

    Partial results are kept: a batch where the model returns 12 of 15 verdicts
    yields those 12, and the missing three are marked ``unmapped`` by the caller
    rather than discarding a batch that was already paid for.
    """
    blocks = "\n\n---\n\n".join(
        _format_chat_block(i, chat, vocabulary) for i, chat in enumerate(chats)
    )
    prompt = CLASSIFY_PROMPT.format(
        taxonomy=_format_taxonomy(causes),
        topics=_format_topics(topics),
        vocabulary=", ".join(vocabulary),
        chats=blocks,
    )
    model = _model().with_structured_output(BatchVerdict)
    result: BatchVerdict = _with_retries(lambda: model.invoke(prompt), "classification batch")  # type: ignore

    allowed_causes = {c["ursache_id"] for c in causes} | {NEW_CAUSE_ID, ""}
    allowed_topics = {t["thema_id"] for t in topics} | {NEW_TOPIC_ID}
    allowed_countries = set(vocabulary)
    verdicts: list[dict] = []
    for verdict in result.gespraeche:
        if not 0 <= verdict.idx < len(chats):
            continue
        chat = chats[verdict.idx]
        quality = verdict.qualitaet if verdict.qualitaet in QUALITIES else "beantwortet"
        cause = verdict.ursache_id if verdict.ursache_id in allowed_causes else NEW_CAUSE_ID
        if quality == "beantwortet":
            cause = ""
        # One user message carries no evidence for "gave up" — the design's
        # measured confounder, enforced here rather than hoped for in the prompt.
        if quality == "abgebrochen" and user_message_count(chat) < 2:
            quality = "ausgewichen" if cause else "beantwortet"
        # Anders als ursache_id gilt das Thema fuer JEDES Gespraech, auch fuer
        # ein gut beantwortetes — sonst beschriebe die Achse nur die Ausfaelle.
        topic = verdict.thema_id if verdict.thema_id in allowed_topics else NEW_TOPIC_ID
        laender = [c for c in dict.fromkeys(verdict.laender) if c in allowed_countries]
        beleg = verdict.beleg if verdict.beleg in chat["assistant_indices"] else -1
        verdicts.append(
            {
                "idx": verdict.idx,
                "chat_db_id": chat["chat_db_id"],
                "qualitaet": quality,
                "ursache_id": cause,
                "thema_id": topic,
                "laender": laender,
                "beleg": beleg,
            }
        )
    return verdicts, prompt_hash(prompt)


# ──────────────────────────────────────────
# Run orchestration, checkpointing, artifacts
# ──────────────────────────────────────────


def new_run_result(run_id: str, month: str, taxonomy_version: int) -> dict:
    """Result of one classification run, as a plain dict.

    ``unmapped`` holds the chats the model never returned a verdict for. They
    are counted and reported, never silently dropped — a month that quietly
    classified 80 % of its chats would look like a month with fewer problems.
    """
    return {
        "run_id": run_id,
        "month": month,
        "model": MODEL_NAME,
        "prompt_version": PROMPT_VERSION,
        "taxonomy_version": taxonomy_version,
        "verdicts": [],
        "unmapped": [],
        "status": "ok",  # ok | partial | failed
        "started_at": time.time(),
        "finished_at": 0.0,
    }


def run_id_for(month: str, taxonomy_version: int) -> str:
    """Stable per (month, taxonomy) so a resumed run rejoins its own checkpoint."""
    return f"{month}-t{taxonomy_version}-p{PROMPT_VERSION}"


def _checkpoint_path(run_id: str) -> str:
    return os.path.join(RUN_DIR, f"{run_id}.jsonl")


def load_checkpoint(run_id: str) -> list[dict]:
    """Verdicts already paid for in an earlier attempt of this run."""
    path = _checkpoint_path(run_id)
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A run killed mid-write leaves one torn line. Everything before
                # it is still paid for and still valid.
                continue
    return rows


def _append_checkpoint(run_id: str, rows: Sequence[dict]) -> None:
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(_checkpoint_path(run_id), "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def classify_month(
    rows: Sequence[Any],
    causes: Sequence[dict],
    month: str,
    taxonomy_version: int = 0,
    resume: bool = True,
    max_chats: int = MAX_CHATS_PER_RUN,
    topics: Sequence[dict] = (),
) -> dict:
    """Classify every chat of one month. Resumable, checkpointed per batch.

    A batch is written to the checkpoint the moment it comes back, so an
    interrupted run never pays for the same batch twice.
    """
    vocabulary = country_vocabulary()
    run_id = run_id_for(month, taxonomy_version)
    result = new_run_result(run_id, month, taxonomy_version)

    prepared = [chat for row in rows if (chat := prepare_chat(row))]
    if len(prepared) > max_chats:
        print(
            f"[chat-quality] {month}: {len(prepared)} chats exceeds the per-run cap "
            f"of {max_chats}; classifying the first {max_chats} by id"
        )
        prepared = sorted(prepared, key=lambda c: c["chat_db_id"])[:max_chats]

    done: dict[str, dict] = {}
    if resume:
        for row in load_checkpoint(run_id):
            done[row["chat_db_id"]] = row
        if done:
            print(f"[chat-quality] {month}: resuming, {len(done)} chats already classified")

    batches = batch_chats(prepared)
    for batch_index, batch in enumerate(batches):
        todo = [chat for chat in batch if chat["chat_db_id"] not in done]
        if not todo:
            continue
        try:
            verdicts, phash = classify_with_completeness(
                todo, causes, vocabulary, topics
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[chat-quality] {month}: batch {batch_index} failed for good: {exc}")
            result["status"] = "partial"
            continue

        # Look chats up by id, not by the batch-local `idx`: verdicts merged
        # back from a re-asked sub-batch carry that sub-batch's indices.
        by_id = {chat["chat_db_id"]: chat for chat in todo}
        artifacts = []
        for verdict in verdicts:
            chat = by_id.get(verdict["chat_db_id"])
            if chat is None:
                continue
            priors = url_prior(chat["urls"], vocabulary)
            laender = verdict["laender"]
            # Fallback: no country from the model -> the prior stands. Neither ->
            # `ohne Land`, which is a reported bucket, not an omission.
            fallback_used = False
            if not laender and priors:
                laender = priors
                fallback_used = True
            artifacts.append(
                {
                    "chat_db_id": chat["chat_db_id"],
                    "run_id": run_id,
                    "batch_index": batch_index,
                    "model": MODEL_NAME,
                    "prompt_hash": phash,
                    "prompt_version": PROMPT_VERSION,
                    "taxonomy_version": taxonomy_version,
                    "qualitaet": verdict["qualitaet"],
                    "ursache_id": verdict["ursache_id"],
                    "thema_id": verdict.get("thema_id", NEW_TOPIC_ID),
                    "url_prior": priors,
                    "llm_laender": verdict["laender"],
                    "laender": laender,
                    "fallback_used": fallback_used,
                    "beleg": verdict["beleg"],
                    "user_message_count": user_message_count(chat),
                }
            )
        _append_checkpoint(run_id, artifacts)
        for artifact in artifacts:
            done[artifact["chat_db_id"]] = artifact

    result["verdicts"] = [done[c["chat_db_id"]] for c in prepared if c["chat_db_id"] in done]
    result["unmapped"] = [c["chat_db_id"] for c in prepared if c["chat_db_id"] not in done]
    if result["unmapped"] and result["status"] == "ok":
        result["status"] = "partial"
    result["finished_at"] = time.time()
    print(
        f"[chat-quality] {month}: {len(result['verdicts'])} classified, "
        f"{len(result['unmapped'])} unmapped, status={result['status']}, "
        f"{result['finished_at'] - result['started_at']:.0f}s"
    )
    return result


# ──────────────────────────────────────────
# Taxonomy continuity across months (A4)
# ──────────────────────────────────────────


def next_cause_id(causes: Sequence[dict]) -> str:
    """The next free ``uNN``. IDs are never reused — the comparison is over IDs."""
    used = {
        int(m.group(1))
        for c in causes
        if (m := re.fullmatch(r"u(\d+)", str(c.get("ursache_id", ""))))
    }
    return f"u{(max(used) + 1) if used else 1:02d}"


def promote_new_causes(
    causes: Sequence[dict],
    new_bucket: Sequence[dict],
    k: int = MIN_CAUSE_SIZE,
) -> list[dict]:
    """Promote `neu` groups that reached k into causes with fresh, stable IDs.

    ``new_bucket`` entries are ``{label, definition, count}`` as named by the
    naming step. Promotion happens for month N+1, never retroactively: a month
    is always classified against the taxonomy it was run with.
    """
    promoted = list(causes)
    for candidate in sorted(new_bucket, key=lambda c: -c.get("count", 0)):
        if candidate.get("count", 0) < k:
            continue
        promoted.append(
            {
                "ursache_id": next_cause_id(promoted),
                "label": candidate["label"],
                "definition": candidate["definition"],
                "promoted_from": NEW_CAUSE_ID,
            }
        )
    return promoted


def retire_causes(
    causes: Sequence[dict],
    history: dict[str, list[int]],
    protected: Iterable[str] = (),
    k: int = MIN_CAUSE_SIZE,
    months: int = RETIRE_AFTER_MONTHS,
) -> list[dict]:
    """Mark causes below k for `months` consecutive months as retired.

    A retired cause leaves the classification prompt but stays in the old
    aggregates, and its number afterwards is ``None``, not 0 — "no longer
    measured" is not "no longer happening". A cause with an open action from
    SC5 is never retired: retiring it would manufacture exactly the drop that
    SC6 reads as success.
    """
    protected_ids = set(protected)
    out: list[dict] = []
    for cause in causes:
        cause = dict(cause)
        cid = cause["ursache_id"]
        recent = history.get(cid, [])[-months:]
        if (
            cid not in protected_ids
            and len(recent) >= months
            and all(count < k for count in recent)
        ):
            cause["retired"] = True
        out.append(cause)
    return out


def active_causes(causes: Sequence[dict]) -> list[dict]:
    """Causes that still go into the classification prompt."""
    return [c for c in causes if not c.get("retired")]


def next_topic_id(topics: Sequence[dict]) -> str:
    """The next free ``tNN``. IDs are never reused — the comparison is over IDs."""
    used = {
        int(m.group(1))
        for t in topics
        if (m := re.fullmatch(r"t(\d+)", str(t.get("thema_id", ""))))
    }
    return f"t{(max(used) + 1) if used else 1:02d}"


def promote_new_topics(
    topics: Sequence[dict],
    new_bucket: Sequence[dict],
    k: int = MIN_TOPIC_SIZE,
) -> list[dict]:
    """Promote `neu` topic groups that reached k, with fresh stable IDs.

    Same rule as the causes (A4): promotion takes effect for month N+1, never
    retroactively, so a month is always read with the list it was run against.
    """
    promoted = list(topics)
    for candidate in sorted(new_bucket, key=lambda t: -t.get("count", 0)):
        if candidate.get("count", 0) < k:
            continue
        promoted.append(
            {
                "thema_id": next_topic_id(promoted),
                "label": candidate["label"],
                "definition": candidate["definition"],
                "promoted_from": NEW_TOPIC_ID,
            }
        )
    return promoted


def active_topics(topics: Sequence[dict]) -> list[dict]:
    """Topics that still go into the classification prompt."""
    return [t for t in topics if not t.get("retired")]


# ──────────────────────────────────────────
# FAQ coverage (T26)
# ──────────────────────────────────────────

# Words that appear in nearly every German sentence and in nearly every cause
# label, so a match on one of them says nothing.
_STOPWORDS = {
    "aber", "aus", "bei", "beim", "bot", "das", "dem", "den", "der", "des",
    "die", "ein", "eine", "einen", "einer", "für", "hat", "ich", "ist", "kann",
    "kein", "keine", "keinen", "mit", "nicht", "oder", "sich", "und", "vom",
    "von", "wie", "wird", "über", "zum", "zur", "chamäleon", "chamaeleon",
    "reise", "reisen", "gast", "gäste", "nutzer", "kunde", "kunden", "frage",
    "fragen", "antwort", "antworten", "information", "informationen",
    "spezifische", "spezifischen", "details", "genaue", "genauen",
}


def _content_words(text: str) -> set[str]:
    """Words from a label that could plausibly identify a topic."""
    words = re.findall(r"[A-Za-zÄÖÜäöüß]{5,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def faq_corpus() -> set[str]:
    """Every word used in an FAQ question, lowercased.

    Questions only, not answers: a cause is covered when somebody has already
    written a Q&A pair ABOUT it. An answer that mentions the word in passing is
    not a snippet the bot can be pointed at.
    """
    import agent_base

    questions = list(agent_base.general_faq_data)
    for country_questions in agent_base.laender_faq_data.values():
        questions.extend(country_questions)
    return set(re.findall(r"[a-zäöüß]{3,}", " ".join(questions).lower()))


# German compounds mean a cause word and an FAQ word rarely match exactly:
# "Reiseunterlagen" against "Unterlagen", "Visabestimmungen" against "Visa".
# A shared prefix of this length catches those. Six, not four: at four,
# "Verfügbarkeit" matches "verfügen" and "Zahlungsmodalitäten" matches
# "zahlen", which marks every cause covered and makes the marker useless.
_STEM_LENGTH = 6



def _covered(word: str, corpus: set[str]) -> bool:
    """Whether an FAQ question uses this word, or an obvious variant of it.

    Only two directions, both conservative:
      - the word appears inside a longer FAQ compound ("Visa" in
        "Visabeantragung"),
      - the two share a six-character prefix ("Reiseunterlagen" /
        "Reiseunterlage").

    Explicitly NOT "an FAQ word appears inside the cause word": that made
    "Reise" cover "Preise" and "meine" cover "allgemeine", which marked every
    cause as covered.
    """
    stem = word[:_STEM_LENGTH]
    for faq_word in corpus:
        if word in faq_word or faq_word.startswith(stem):
            return True
    return False


def mark_faq_gaps(causes: Sequence[dict], corpus: set[str] | None = None) -> list[dict]:
    """Flag causes that no FAQ entry addresses yet.

    This is the piece that turns the cause table from a list of complaints into
    a list of work: a cause with no snippet behind it is one somebody can go and
    write. Deliberately crude — a word overlap, not a semantic match — because
    the marker only has to be right often enough to point at the next thing to
    write, and a wrong mark costs a glance at the FAQ file.

    A cause whose label carries no distinctive word at all is NOT marked: "no
    evidence of coverage" and "evidence of no coverage" are different claims,
    and only the second one is worth showing.
    """
    words_in_faqs = corpus if corpus is not None else faq_corpus()
    marked: list[dict] = []
    for cause in causes:
        cause = dict(cause)
        # The LABEL only. A one-sentence definition carries enough incidental
        # vocabulary that every cause finds some word in the FAQ corpus.
        words = _content_words(cause.get("label", ""))
        cause["faq_gap"] = bool(words) and not any(
            _covered(word, words_in_faqs) for word in words
        )
        marked.append(cause)
    return marked
