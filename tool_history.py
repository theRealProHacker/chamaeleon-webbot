"""Tool-Historie: was das Modell in früheren Turns nachgeschlagen hat.

Das Widget schickt den Verlauf nur als Textpaare; Tool-Aufrufe und -Ergebnisse
vergangener Turns gingen bisher verloren, und bei „und wie teuer ist die?“ rief
das Modell das Tool erneut oder antwortete aus der Luft. Hier liegen sie
serverseitig pro session_id, agent.py fügt sie beim nächsten Request wieder vor
die Antwort ihres Turns ein. Nichts davon geht an den Browser.

Ein Store ist ein plain dict aus :func:`new_store`, nach dem Muster von
session_binding.py — keine Klasse, ein Lock für die Dict-Operationen. In-memory,
Single-Worker-Deploy (siehe rate_limit.py): Neustart = Historie weg, der Chat
läuft dann wie vorher ohne sie weiter.

Gespeichert werden nur JSON-artige Werte, nie LangChain-Objekte:

    {"typ": "ai_tool_calls", "tool_calls": [{"name", "args", "id"}]}
    {"typ": "tool", "tool_call_id": ..., "name": ..., "content": ...}

Ein ``ai_tool_calls``-Eintrag und alle seine ``tool``-Einträge bilden eine
Gruppe. Gekappt wird nur gruppenweise: Gemini lehnt einen function_call ohne
Antwort (und umgekehrt) ab.

Die Identität (``kunde:<id>`` / ``agentur:<id>`` / ``""``) ist ein Wächter,
kein Schlüsselteil: lädt eine andere Identität dieselbe session_id (geteilter
Browser), wird der Eintrag verworfen. Tool-Ergebnisse von Kunde A landen so nie
im Chat von Kunde B.
"""

import threading
import time
from collections import OrderedDict

# 2 h ab dem letzten Schreiben. Deutlich unter der Bindungs-TTL (12 h), weil
# hier Inhalte liegen (Zahlstand, Flüge), nicht nur eine Kundennummer.
TTL = 2 * 60 * 60

MAX_ERGEBNIS_ZEICHEN = 3000
MAX_TURN_EINTRAEGE = 8
MAX_TURN_ZEICHEN = 12000
MAX_TOOL_TURNS = 5
# session_id ist client-kontrolliert; ohne Kappe wäre der Store ein Speicher-DoS.
MAX_SESSIONS = 2000

# Im Folge-Turn ist das Replay das einzige, was das Modell vom Ergebnis noch
# weiß. Der Marker zeigt ihm deshalb den Weg zurück zum Tool.
KUERZUNGS_MARKER = "…[gekürzt] Bei Bedarf das Tool erneut aufrufen."


def new_store(ttl) -> dict:
    """Ein frischer Store; ``ttl`` in Sekunden, Zahl oder Funktion ohne Argumente."""
    return {
        "ttl": ttl,
        # session_id -> eintrag; die Reihenfolge ist die LRU-Ordnung.
        "sessions": OrderedDict(),
        "lock": threading.Lock(),
    }


def _expiry(store: dict) -> float:
    ttl = store["ttl"]
    return time.time() + (ttl() if callable(ttl) else ttl)


def _kuerzen(eintrag: dict) -> dict:
    if eintrag.get("typ") != "tool":
        return eintrag
    content = str(eintrag.get("content") or "")
    if len(content) > MAX_ERGEBNIS_ZEICHEN:
        content = content[:MAX_ERGEBNIS_ZEICHEN] + KUERZUNGS_MARKER
    return {**eintrag, "content": content}


def _gruppen(verlauf: list) -> list[list]:
    gruppen: list[list] = []
    for eintrag in verlauf:
        if eintrag.get("typ") == "ai_tool_calls" or not gruppen:
            gruppen.append([])
        gruppen[-1].append(eintrag)
    return gruppen


def _kappen(verlauf: list) -> list:
    """Einzelne Ergebnisse kürzen, dann älteste Gruppen streichen, bis der Turn passt."""
    gruppen = _gruppen([_kuerzen(e) for e in verlauf])

    def zu_gross() -> bool:
        eintraege = sum(len(g) for g in gruppen)
        zeichen = sum(len(e.get("content") or "") for g in gruppen for e in g)
        return eintraege > MAX_TURN_EINTRAEGE or zeichen > MAX_TURN_ZEICHEN

    while gruppen and zu_gross():
        gruppen.pop(0)
    return [e for g in gruppen for e in g]


def _normalisiert(text: str) -> str:
    return " ".join(str(text or "").split())[:200]


def save(
    store: dict,
    session_id: str,
    identitaet: str,
    turn_index: int,
    verlauf: list,
    fingerprint: str = "",
) -> None:
    """Den Tool-Verlauf eines Turns ablegen; ein leerer Verlauf löscht den Turn.

    ``fingerprint`` ist der Text der Nutzernachricht des Turns. :func:`load`
    gibt den Turn nur zurück, wenn derselbe Text wieder an dieser Stelle steht.
    """
    if not session_id:
        return
    verlauf = _kappen(verlauf)
    with store["lock"]:
        sessions = store["sessions"]
        eintrag = sessions.get(session_id)
        # Andere Identität oder abgelaufen: ersetzen, nie mergen — sonst lebten
        # Turns von Kunde A unter der Identität von Kunde B weiter.
        if (
            eintrag is None
            or eintrag["identitaet"] != identitaet
            or time.time() >= eintrag["expiry"]
        ):
            eintrag = {"identitaet": identitaet, "turns": {}, "fingerprints": {}}
            sessions[session_id] = eintrag
        if verlauf:
            eintrag["turns"][turn_index] = verlauf
            eintrag["fingerprints"][turn_index] = _normalisiert(fingerprint)
        else:
            eintrag["turns"].pop(turn_index, None)
            eintrag["fingerprints"].pop(turn_index, None)
        for alt in sorted(eintrag["turns"])[:-MAX_TOOL_TURNS]:
            del eintrag["turns"][alt]
            eintrag["fingerprints"].pop(alt, None)
        eintrag["expiry"] = _expiry(store)
        sessions.move_to_end(session_id)
        while len(sessions) > MAX_SESSIONS:
            sessions.popitem(last=False)


def load(
    store: dict,
    session_id: str,
    identitaet: str,
    fingerprints: dict[int, str] | None = None,
) -> dict[int, list]:
    """Gespeicherte Turns dieser Session, ``{turn_index: verlauf}``.

    Leer bei unbekannter, abgelaufener oder fremder Session; die beiden letzten
    werden dabei gelöscht. Mit ``fingerprints`` (Turn → Text der Nutzernachricht
    im aktuellen Verlauf) fallen Turns weg, deren Frage nicht mehr dieselbe ist.
    """
    if not session_id:
        return {}
    with store["lock"]:
        sessions = store["sessions"]
        eintrag = sessions.get(session_id)
        if eintrag is None:
            return {}
        if time.time() >= eintrag["expiry"] or eintrag["identitaet"] != identitaet:
            del sessions[session_id]
            return {}
        sessions.move_to_end(session_id)
        return {
            turn: list(verlauf)
            for turn, verlauf in eintrag["turns"].items()
            if _passt(eintrag["fingerprints"].get(turn, ""), fingerprints, turn)
        }


def _passt(gespeichert: str, fingerprints: dict[int, str] | None, turn: int) -> bool:
    # Ohne Fingerprint auf einer der beiden Seiten gibt es nichts zu vergleichen.
    if fingerprints is None or not gespeichert:
        return True
    return _normalisiert(fingerprints.get(turn, "")) == gespeichert


_store = new_store(lambda: TTL)
