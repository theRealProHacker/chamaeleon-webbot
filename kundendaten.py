"""Kunden-Modus: Flugdaten des eingeloggten Kunden aus TourOne.

Wird nur aktiv, wenn der Widget-Request eine ``kunden_id`` mitschickt (der
eingeloggte MeinChamäleon-Kunde). Die ID ist client-asserted und unverifiziert
(akzeptiertes MVP-Risiko, siehe TODOS.md); deshalb sind alle Schutzmechanismen
strukturell: das Tool hat KEINEN ID-Parameter (Closure), nur GET-Zugriffe,
und die Antwort enthält ausschließlich whitelisted Flugfelder.

Entscheidungsbaum des Tools (Kontrakt live verifiziert 2026-07-13; unbekannte
IDs kommen als ``[]`` mit HTTP 200 zurück, nie als Fehlerstatus):

    make_fluege_tool(kunden_id)          # Closure — Tool hat KEINE Parameter
      └─ GET /get/adresse?kundennummer=…             (Hop 1, timeout=8)
           ├─ Liste ([])  → unbekannte ID → UNBEKANNT_TEXT
           └─ Objekt      → buchungen[] → kommende behalten (bisDat >= heute)
                └─ je Buchung: GET /get/buchung?vorgangsNummer=…   (Hop 2)
                     ├─ status != "OK" → überspringen (XX = storniert)
                     └─ flugdaten[] → Whitelist → deutscher Text
                (keine kommende Buchung / noch nichts eingebucht
                 → KEINE_FLUEGE_TEXT; Request-Fehler → FEHLER_TEXT)

``flugdaten`` füllt TourOne erst, wenn die Flüge eingebucht sind (kurz vor
Abreise) — ein leeres Array bei einer kommenden Reise ist der legitime
"keine Flüge hinterlegt"-Fall, kein Fehler.

Vollständige Doku der API-Datenfelder in ``docs/kundendaten-datenzugriff.md``:
was der Endpunkt liefert, was wir davon nutzen und vor allem, was davon an
Gemini geht. Die maßgebliche Grenze ist die Modell-Grenze — den ganzen
Datensatz zu holen ist okay (bleibt serverseitig); minimal bleiben muss, was
im Gemini-Request landet. Änderungen hier gegen diese Grenze prüfen.
"""

import datetime
import re

import pytz
from langchain_core.tools import tool

# Bewusster Import der privaten TourOne-Plumbing-Funktion: es soll genau eine
# Implementierung geben, und die lebt in travel_index (Entscheidung 2A).
from travel_index import _tourone_get

# Der 20s-Default von _tourone_get ist für Index-Builds; mitten im Chat muss
# die Wartezeit pro Request enger begrenzt sein (Entscheidung 5A).
TIMEOUT = 8

# Obergrenze der Hop-2-Calls: die Kette ist 1 + N Requests à TIMEOUT, also
# muss N begrenzt sein. Mehr als ein paar kommende Buchungen hat kein Kunde.
MAX_BUCHUNGEN = 3

# Nur diese Felder aus flugdaten erreichen jemals das Modell/den Kunden.
# pnrFileKey (PNR/Buchungsreferenz) und interne IDs bleiben bewusst draußen.
FLUG_FELDER = ("flugnr", "airline", "vonCo3Code", "nachCo3Code", "abflug", "ankunft")

UNBEKANNT_TEXT = (
    "Zu dieser Anmeldung konnte ich keine Kundendaten finden. "
    "Bitte melde dich in MeinChamäleon neu an oder wende dich an deinen "
    "Erlebnisberater."
)
KEINE_FLUEGE_TEXT = (
    "Zu deinem Konto sind aktuell keine Flüge hinterlegt. Flugdaten werden "
    "oft erst kurz vor der Abreise eingebucht — dein Erlebnisberater hilft "
    "dir gern weiter."
)
FEHLER_TEXT = (
    "Die Flugdaten sind gerade nicht abrufbar. Bitte versuche es später noch "
    "einmal oder wende dich an deinen Erlebnisberater."
)

_KUNDEN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def parse_kunden_id(value: object) -> str:
    """Normalize the client-sent kunden_id; anything unexpected means absent.

    Accepts strings and JSON integers (the widget contract is a string, but a
    numeric ID must not silently disable the mode). ``bool`` subclasses
    ``int``, so JSON ``true`` must be rejected before the int branch; ``None``
    and every other type map to "". The allowlist kills path/query injection
    into the authenticated TourOne call.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not _KUNDEN_ID_PATTERN.match(value):
        return ""
    return value


def _heute() -> str:
    """Today in Berlin as ``YYYY-MM-DD`` (matches TourOne date strings)."""
    return datetime.datetime.now(pytz.timezone("Europe/Berlin")).strftime("%Y-%m-%d")


def _fmt_datum(value: str) -> str:
    """``2026-09-01 00:00:00`` → ``01.09.2026`` (fallback: raw value)."""
    try:
        return datetime.datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


def _fmt_zeitpunkt(value: str) -> str:
    """``2026-09-01 10:20:00`` → ``01.09.2026, 10:20 Uhr`` (fallback: raw)."""
    try:
        dt = datetime.datetime.strptime(value[:16], "%Y-%m-%d %H:%M")
        return dt.strftime("%d.%m.%Y, %H:%M Uhr")
    except ValueError:
        return value


def _flug_zeile(flug: dict) -> str:
    """One whitelisted flight segment as a German bullet line."""
    flugnr = flug.get("flugnr") or ""
    airline = flug.get("airline") or ""
    von = flug.get("vonCo3Code") or "?"
    nach = flug.get("nachCo3Code") or "?"
    kopf = f"Flug {flugnr}".strip() if flugnr else "Flug"
    if airline and not flugnr.startswith(airline):
        kopf += f" ({airline})"
    zeile = f"- {kopf}: {von} → {nach}"
    if flug.get("abflug"):
        zeile += f", Abflug {_fmt_zeitpunkt(flug['abflug'])}"
    if flug.get("ankunft"):
        zeile += f", Ankunft {_fmt_zeitpunkt(flug['ankunft'])}"
    return zeile


def _buchung_titel(buchung: dict, fallback: str) -> str:
    for beschreibung in buchung.get("beschreibungen") or []:
        if isinstance(beschreibung, dict) and beschreibung.get("titel"):
            return beschreibung["titel"]
    return fallback


def fetch_fluege_text(kunden_id: str) -> str:
    """Fetch and format the customer's upcoming flights. Never raises."""
    try:
        adresse = _tourone_get(
            "/get/adresse", {"kundennummer": kunden_id}, timeout=TIMEOUT
        )
    except Exception as e:
        print(f"[kundendaten] adresse lookup failed: {e}")
        return FEHLER_TEXT

    # Kontrakt: unbekannte ID → leere Liste, Treffer → Objekt (beides HTTP 200).
    if not isinstance(adresse, dict):
        return UNBEKANNT_TEXT

    heute = _heute()
    kommende = sorted(
        (
            b
            for b in adresse.get("buchungen") or []
            if isinstance(b, dict)
            and b.get("vorgang")
            and str(b.get("bisDat") or "")[:10] >= heute
        ),
        key=lambda b: str(b.get("vonDat") or ""),
    )

    abschnitte: list[str] = []
    fehler_gesehen = False
    for eingebettet in kommende[:MAX_BUCHUNGEN]:
        try:
            buchung = _tourone_get(
                "/get/buchung",
                {"vorgangsNummer": eingebettet["vorgang"]},
                timeout=TIMEOUT,
            )
        except Exception as e:
            print(f"[kundendaten] buchung lookup failed: {e}")
            fehler_gesehen = True
            continue
        # Nur flugdaten von /get/buchung sind je gefüllt; status XX = storniert.
        if not isinstance(buchung, dict) or buchung.get("status") != "OK":
            continue
        fluege = [f for f in buchung.get("flugdaten") or [] if isinstance(f, dict)]
        if not fluege:
            continue
        fluege.sort(key=lambda f: (f.get("rang") or 0, str(f.get("abflug") or "")))
        titel = _buchung_titel(buchung, eingebettet.get("reiseCode") or "deine Reise")
        kopf = f'Reise „{titel}"'
        von_dat = str(eingebettet.get("vonDat") or "")
        bis_dat = str(eingebettet.get("bisDat") or "")
        if von_dat and bis_dat:
            kopf += f" ({_fmt_datum(von_dat)} – {_fmt_datum(bis_dat)})"
        abschnitte.append(kopf + ":\n" + "\n".join(_flug_zeile(f) for f in fluege))

    if abschnitte:
        return "Kommende Flüge laut Buchungssystem:\n\n" + "\n\n".join(abschnitte)
    if fehler_gesehen:
        return FEHLER_TEXT
    return KEINE_FLUEGE_TEXT


def make_fluege_tool(kunden_id: str):
    """Build the per-request flights tool bound to this customer by closure.

    The tool deliberately takes NO parameters: the model can never choose
    whose data is fetched, so prompt injection cannot cross customers.
    """

    @tool
    def kunden_fluege_tool() -> str:
        """Ruft die kommenden Flüge des eingeloggten Kunden aus dem Buchungssystem ab.

        Nur verwenden, wenn der Kunde ausdrücklich nach seinen EIGENEN Flügen
        fragt (z.B. "Wann geht mein Flug?"). Vergangene Flüge kann dieses Tool
        nicht einsehen.
        """
        return fetch_fluege_text(kunden_id)

    return kunden_fluege_tool


def filter_new_tool_calls(tool_calls: list, seen_ids: set) -> list:
    """Return only tool calls whose id was not seen yet; updates ``seen_ids``.

    ``call_stream`` uses ``stream_mode="values"``, so every event re-yields
    the full message history including historical tool_calls — without this
    filter each call would be logged once per subsequent event. Calls without
    an id pass through (nothing to dedup on).
    """
    neue = []
    for tc in tool_calls:
        tc_id = tc.get("id") or ""
        if tc_id in seen_ids:
            continue
        if tc_id:
            seen_ids.add(tc_id)
        neue.append(tc)
    return neue
