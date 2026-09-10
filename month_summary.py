"""Der KI-Kurzreport: ein Gemini-Aufruf je Monat ueber das FERTIGE Aggregat.

Was hier hineingeht, ist ausschliesslich das, was der Monatsreport ohnehin
zeigt — Chatzahlen, Hilfe-Klassen, Ursachen, Themen, Laender, Wochentage und
Stunden —, jeweils fuer den Monat und seinen Vormonat. Keine Transkripte: das
war die Entscheidung im Design (A5), und sie haelt den Kurzreport auf derselben
DSGVO-Flaeche wie den Rest von ``month_stats``. Der Text kann deshalb nichts
zitieren, was nicht schon als Zahl in der Tabelle steht.

Der Text ist versionierte Prosa, keine Wahrheit: die Zahlen bleiben die Karten
darueber. Schlaegt der Aufruf fehl, bleibt der vorherige Kurzreport stehen —
kein Teiltext (A5).

Importieren macht kein I/O. Alles Teure steht hinter einem Funktionsaufruf.
"""

import time
from datetime import timedelta
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field

import month_aggregate
from month_aggregate import (
    DAY_NAME,
    MonthAggregate,
    MonthKey,
    QualityAggregate,
    avg_messages,
    avg_user_messages,
    dialog_samples,
    median_duration_dialog,
    month_label,
    select,
    vector_total,
    weekday_occurrences,
    weekday_vectors,
)

PROMPT_VERSION = 1
MODEL_NAME = "gemini-2.5-flash"

# Wie viele Eintraege je Rangliste in den Prompt gehen. Der Report zeigt 15
# Ursachen und je 10 Themen und Laender; mehr Zeilen machen den Text nicht
# besser, nur die Versuchung groesser, eine Randbewegung zur Erkenntnis zu
# erklaeren.
TOP_N = 10

# Prompt v1 des Kunden (2026-09-10), unveraendert bis auf den Datenblock und
# die Formatanweisung fuer die strukturierte Antwort.
PROMPT = """Erstelle einen kurzen KI-Monatsreport zu den Chatverläufen des Chamäleon-Reisen-Chatbots LEON.

Analysiere den ausgewählten Monat und vergleiche ihn immer mit dem direkten Vormonat.

Fasse die Daten schriftlich und in kurzen, sehr leicht verständlichen Sätzen zusammen. Wiederhole nicht einfach Tabellenwerte, sondern beschreibe die wichtigsten Entwicklungen, Auffälligkeiten und Erkenntnisse.

Berücksichtige dabei die folgenden Bereiche aus dem Dashboard:

- Chataktivität: Wie hat sich die Nutzung gegenüber dem Vormonat entwickelt? Gibt es Auffälligkeiten bei Gesprächsverlauf, Nachrichtenmenge oder Gesprächsdauer?
- Qualität der Antworten: Hat LEON häufiger oder seltener vollständig geholfen? Wo zeigen sich Verbesserungen oder Verschlechterungen?
- Wo der Bot danebenliegt: Welche Ursachen treten besonders häufig auf? Welche Probleme haben gegenüber dem Vormonat zu- oder abgenommen?
- Gesprächsthemen: Welche Themen beschäftigen die Nutzer besonders? Welche Themen gewinnen oder verlieren gegenüber dem Vormonat an Bedeutung?
- Reiseziele: Welche Destinationen stehen besonders im Fokus? Welche Reiseziele werden im Vergleich zum Vormonat häufiger oder seltener nachgefragt?
- Wochentage und Uhrzeiten: Gibt es erkennbare Veränderungen darin, wann Nutzer besonders häufig mit LEON chatten?
- Auffälligkeiten: Nenne besondere Entwicklungen, neue Fragestellungen, wiederkehrende Probleme oder ungewöhnliche Veränderungen.

Der Vergleich zum Vormonat ist besonders wichtig. Hebe deshalb nicht nur den aktuellen Stand hervor, sondern beschreibe vor allem, was sich verändert hat.

Nutze Formulierungen wie „häufiger als im Vormonat“, „deutlich zurückgegangen“, „weitgehend stabil“ oder „stärker in den Fokus gerückt“.

Schreibe nur Erkenntnisse, die sich tatsächlich aus den vorhandenen Daten ableiten lassen. Keine Vermutungen oder erfundenen Zusammenhänge. Steht zu einem Bereich „keine Vergleichsdaten“, dann vergleiche diesen Bereich nicht mit dem Vormonat.

Ausgabeformat:

Monatsentwicklung — 2–3 kurze Stichpunkte zur allgemeinen Entwicklung gegenüber dem Vormonat.

Themen & Reiseziele — 3–4 kurze Stichpunkte zu wichtigen Themen, Destinationen und Veränderungen.

Qualität & Probleme — 2–3 kurze Stichpunkte dazu, wo LEON gut funktioniert und wo Probleme auftreten.

Auffälligkeiten — 1–3 besonders relevante Erkenntnisse des Monats.

Insgesamt maximal 10–12 kurze Stichpunkte. Keine Einleitung und kein Fazit. Jeder Stichpunkt ist ein oder zwei ganze Sätze ohne Aufzählungszeichen und ohne Markdown. Bezeichnungen aus den Daten stehen in deutschen Anführungszeichen („…“).

Daten aus dem Dashboard:

{daten}
"""


# ──────────────────────────────────────────
# Eingabe: das Aggregat als Text
# ──────────────────────────────────────────


def _fmt(n: int | float | None) -> str:
    if n is None:
        return "–"
    if isinstance(n, float):
        return f"{n:.1f}".replace(".", ",")
    return f"{n:,}".replace(",", ".")


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d} min"


def activity(agg: MonthAggregate | None) -> dict[str, Any] | None:
    """Die Zahlen der Kachel „Der Monat in Zahlen“, ueber alle Segmente."""
    if agg is None:
        return None
    return {
        "chats": vector_total(agg["total_chats"]),
        "user_messages": avg_user_messages(agg),
        "messages": avg_messages(agg),
        "duration": median_duration_dialog(agg),
        "dialog_samples": dialog_samples(agg),
        "segments": {
            name: agg["total_chats"][index]
            for index, name in enumerate(("Allgemeine Webseite", "MeinChamäleon", "Agentur"))
        },
    }


def weekday_profile(agg: MonthAggregate | None, key: MonthKey) -> list[tuple[str, float]]:
    """Chats je Vorkommen des Wochentags — so vergleicht sich ein Monat mit
    fuenf Montagen mit einem, der vier hat."""
    if agg is None:
        return []
    occurrences = weekday_occurrences(key)
    vectors = weekday_vectors(agg, key)
    return [
        (DAY_NAME[weekday], (vector_total(vector) / occurrences[weekday]) if occurrences[weekday] else 0.0)
        for weekday, vector in enumerate(vectors)
    ]


def hour_profile(agg: MonthAggregate | None) -> list[tuple[int, int]]:
    """Die drei staerksten Stunden, Berliner Zeit."""
    if agg is None:
        return []
    ranked = sorted(
        ((hour, vector_total(vector)) for hour, vector in enumerate(agg["hourly"])),
        key=lambda hv: -hv[1],
    )
    return ranked[:3]


def ranked_axis(
    current: Sequence[dict],
    id_key: str,
    previous: dict[str, int] | None,
) -> list[dict[str, Any]]:
    """Eine Rangliste mit Vormonatsspalte. ``previous`` None heisst: nicht
    vergleichbar (Taxonomiewechsel oder kein Vormonat), nicht null."""
    rows = []
    for entry in current[:TOP_N]:
        rows.append(
            {
                "label": entry.get("label") or entry.get(id_key),
                "count": entry.get("count"),
                "before": None if previous is None else previous.get(entry[id_key], 0),
            }
        )
    return rows


def hilfe_counts(hilfe: dict[str, Any] | None) -> dict[str, int] | None:
    if not hilfe or hilfe.get("status") != "ok":
        return None
    return {k["label"]: k["count"] for k in hilfe["klassen"]}


def build_input(
    key: MonthKey,
    agg: MonthAggregate | None,
    quality: QualityAggregate,
    taxonomy: Sequence[Any],
    topics: Sequence[Any],
    previous_agg: MonthAggregate | None,
    previous_quality: QualityAggregate | None,
) -> dict[str, Any]:
    """Alles, was in den Prompt geht, als Zahlen. Rein, testbar, ohne I/O.

    ``previous_quality`` ist None, wenn der Vormonat nicht vergleichbar ist —
    dieselbe Regel wie in den Delta-Spalten des Reports: ueber eine
    Taxonomiegrenze hinweg gibt es keinen Vergleich.
    """
    previous_key = month_aggregate.month_key(
        month_aggregate.month_bounds(key)[0] - timedelta(days=1)
    )
    hilfe = month_aggregate.hilfe_for(quality, taxonomy)
    hilfe_before = (
        month_aggregate.hilfe_for(previous_quality, taxonomy) if previous_quality else None
    )
    referral = month_aggregate.referral_cause_id(taxonomy)
    causes = [
        c
        for c in month_aggregate.top_causes(quality, taxonomy)
        if c.get("count") is not None and c.get("ursache_id") != referral
    ]

    def previous_counts(axis: str) -> dict[str, int] | None:
        if previous_quality is None:
            return None
        return {k: vector_total(v) for k, v in (previous_quality.get(axis) or {}).items()}

    countries = [
        c
        for c in month_aggregate.top_countries(quality, limit=TOP_N + 1)
        if c["land"] != month_aggregate.NO_COUNTRY
    ]
    return {
        "month": key,
        "label": month_label(key),
        "previous_label": month_label(previous_key),
        "activity": activity(agg),
        "activity_before": activity(previous_agg),
        "classified": vector_total(quality.get("classified_chats") or [0, 0, 0]),
        "hilfe": hilfe_counts(hilfe),
        "hilfe_before": hilfe_counts(hilfe_before),
        "uebergaben": hilfe.get("uebergaben"),
        "uebergaben_before": (hilfe_before or {}).get("uebergaben"),
        "causes": ranked_axis(causes, "ursache_id", previous_counts("ursachen")),
        "causes_new": vector_total((quality.get("ursachen") or {}).get("neu") or [0, 0, 0]),
        "topics": ranked_axis(
            month_aggregate.top_topics(quality, topics), "thema_id", previous_counts("themen")
        ),
        "topics_new": vector_total((quality.get("themen") or {}).get("neu") or [0, 0, 0]),
        "countries": ranked_axis(countries, "land", previous_counts("laender")),
        "weekdays": weekday_profile(agg, key),
        "weekdays_before": weekday_profile(previous_agg, previous_key),
        "hours": hour_profile(agg),
        "hours_before": hour_profile(previous_agg),
    }


def _activity_lines(label: str, a: dict[str, Any] | None) -> list[str]:
    if a is None:
        return [f"{label}: keine Vergleichsdaten"]
    parts = [
        f"{_fmt(a['chats'])} Gespräche",
        f"{_fmt(a['user_messages'])} Nutzernachrichten je Gespräch",
        f"{_fmt(a['messages'])} Nachrichten je Gespräch",
        f"Gesprächsdauer (Median, nur Gespräche mit Rückfrage, {_fmt(a['dialog_samples'])} Stück): {_duration(a['duration'])}",
    ]
    segments = ", ".join(f"{name} {_fmt(n)}" for name, n in a["segments"].items() if n)
    if segments:
        parts.append(f"davon {segments}")
    return [f"{label}: " + "; ".join(parts)]


def _table(title: str, rows: Iterable[dict[str, Any]], comparable: bool) -> list[str]:
    lines = [f"{title} (Gespräche, aktueller Monat → Vormonat):"]
    any_row = False
    for row in rows:
        any_row = True
        before = "keine Vergleichsdaten" if not comparable else _fmt(row["before"])
        lines.append(f"- {row['label']}: {_fmt(row['count'])} → {before}")
    if not any_row:
        lines.append("- keine Daten")
    return lines


def render_input(data: dict[str, Any]) -> str:
    """Der Datenblock im Prompt. Klartext, weil das Modell ihn lesen soll und
    ein Mensch ihn im Testlauf pruefen muss."""
    lines: list[str] = []
    lines.append(f"Monat: {data['label']}. Vormonat: {data['previous_label']}.")
    lines.append("")
    lines.append("Chataktivität")
    lines += _activity_lines(data["label"], data["activity"])
    lines += _activity_lines(data["previous_label"], data["activity_before"])
    lines.append("")

    lines.append(f"Qualität der Antworten ({_fmt(data['classified'])} eingeordnete Gespräche)")
    if data["hilfe"]:
        lines.append(
            f"{data['label']}: "
            + "; ".join(f"{k} {_fmt(v)}" for k, v in data["hilfe"].items())
            + f"; davon an die Beratung übergeben {_fmt(data['uebergaben'])}"
        )
    else:
        lines.append(f"{data['label']}: keine Daten")
    if data["hilfe_before"]:
        lines.append(
            f"{data['previous_label']}: "
            + "; ".join(f"{k} {_fmt(v)}" for k, v in data["hilfe_before"].items())
            + f"; davon an die Beratung übergeben {_fmt(data['uebergaben_before'])}"
        )
    else:
        lines.append(f"{data['previous_label']}: keine Vergleichsdaten")
    lines.append("")

    comparable = any(r["before"] is not None for r in data["causes"]) or any(
        r["before"] is not None for r in data["topics"]
    )
    lines += _table("Wo der Bot danebenliegt — Ursachen", data["causes"], comparable)
    lines.append(f"- Ursache passt in keine bekannte Kategorie: {_fmt(data['causes_new'])}")
    lines.append("")
    lines += _table("Gesprächsthemen", data["topics"], comparable)
    lines.append(f"- Thema passt in keine bekannte Kategorie: {_fmt(data['topics_new'])}")
    lines.append("")
    lines += _table(
        "Reiseziele (ein Gespräch kann mehrere Länder nennen)",
        data["countries"],
        any(r["before"] is not None for r in data["countries"]),
    )
    lines.append("")

    lines.append("Wochentage (Gespräche je Wochentag im Schnitt, aktueller Monat → Vormonat)")
    before = dict(data["weekdays_before"])
    if data["weekdays"]:
        for day, value in data["weekdays"]:
            was = _fmt(before[day]) if day in before else "keine Vergleichsdaten"
            lines.append(f"- {day}: {_fmt(value)} → {was}")
    else:
        lines.append("- keine Daten")
    lines.append("")

    def hours(label: str, ranked: list[tuple[int, int]]) -> str:
        if not ranked:
            return f"{label}: keine Vergleichsdaten"
        return f"{label}: " + ", ".join(f"{h}–{h + 1} Uhr ({_fmt(n)})" for h, n in ranked)

    lines.append("Stärkste Stunden (Berliner Zeit, Gespräche)")
    lines.append(hours(data["label"], data["hours"]))
    lines.append(hours(data["previous_label"], data["hours_before"]))
    return "\n".join(lines)


# ──────────────────────────────────────────
# Modellaufruf
# ──────────────────────────────────────────


class Summary(BaseModel):
    monatsentwicklung: list[str] = Field(description="2–3 Stichpunkte")
    themen_reiseziele: list[str] = Field(description="3–4 Stichpunkte")
    qualitaet_probleme: list[str] = Field(description="2–3 Stichpunkte")
    auffaelligkeiten: list[str] = Field(description="1–3 Stichpunkte")


SECTIONS = (
    ("monatsentwicklung", "Monatsentwicklung"),
    ("themen_reiseziele", "Themen & Reiseziele"),
    ("qualitaet_probleme", "Qualität & Probleme"),
    ("auffaelligkeiten", "Auffälligkeiten"),
)


def _model():
    """Eigene Instanz, nicht der Klassifikator: der steht auf Denken-aus, und
    Prosa ueber zwei Monate Zahlen wird ohne Denken merklich schlechter. Die
    Temperatur bleibt 0 — derselbe Monat soll denselben Text bekommen."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    from agent_base import GEMINI_API_KEY

    return ChatGoogleGenerativeAI(
        model=MODEL_NAME, google_api_key=GEMINI_API_KEY, temperature=0
    )


def generate(data: dict[str, Any]) -> dict[str, Any]:
    """Ein Aufruf. Wirft, wenn das Modell nicht antwortet — der Aufrufer
    entscheidet, was ein Fehlschlag heisst (A5: der alte Text bleibt)."""
    import chat_quality

    prompt = PROMPT.format(daten=render_input(data))
    model = _model().with_structured_output(Summary)
    result: Summary = chat_quality._with_retries(  # type: ignore
        lambda: model.invoke(prompt), "kurzreport"
    )
    sections = []
    for field, title in SECTIONS:
        points = [
            chat_quality.sanitize_label(p).strip()
            for p in getattr(result, field) or []
        ]
        sections.append({"titel": title, "punkte": [p for p in points if p]})
    if not any(s["punkte"] for s in sections):
        raise RuntimeError("Kurzreport kam leer zurück")
    return {
        "abschnitte": sections,
        "model": MODEL_NAME,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": chat_quality.prompt_hash(prompt),
        "vormonat": data["previous_label"],
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
