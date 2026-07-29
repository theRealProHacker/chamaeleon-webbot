"""Kunden-Modus: Buchungen des eingeloggten Kunden aus TourOne.

Wird nur aktiv, wenn eine ``kunden_id`` gesetzt ist. Die ID ist serverseitig verifiziert (``kunden_auth`` prüft die
MeinChamäleon-Session einmal über ss.php und bindet sie an die session_id — der
Body-Wert wird ignoriert). Zusätzlich bleiben alle Schutzmechanismen strukturell:
das Tool hat KEINEN ID-Parameter (Closure — der Selektor wählt nur unter den
EIGENEN Buchungen des Kunden, nie wessen), nur GET-Zugriffe, und die Antwort
enthält ausschließlich whitelisted Felder.

``buchungen_tool(auswahl, anzahl, details)`` — Closure auf kunden_id:
  └─ GET /get/adresse?kundennummer=…                    (Hop 1, timeout=8)
       ├─ Liste ([])  → unbekannte ID → UNBEKANNT_TEXT
       └─ Objekt → buchungen[] → _select(auswahl, anzahl):
            auswahl "alle"       → alle, neueste zuerst
            auswahl "kommende"   → bisDat >= heute, nächste zuerst
            auswahl "vergangene" → bisDat <  heute, neueste zuerst
            anzahl N>0           → nur die ersten N der Auswahl
            ├─ details=false → grobe Liste (Titel, Zeitraum, Buchungsnr.),
            │                  NUR Hop 1, kein Hop 2
            └─ details=true  → je Buchung (gedeckelt auf MAX_DETAIL):
                 GET /get/buchung?vorgangsNummer=…       (Hop 2)
                 → Whitelist → Status, Reisende, Zahlstand, Flüge

Whitelist — nur diese Felder erreichen jemals das Modell/den Kunden:
  Grobe Liste: Titel, vonDat, bisDat, vorgang. Der Titel kommt aus
  beschreibungen.titel (nur Hop 2 gefüllt), sonst über den Reise-Index aus dem
  reiseCode, sonst der reiseCode selbst — siehe ``_titel_aus_code``.
  Detail zusätzlich: status, persAdult/persChild/persBaby, die sechs
  FLUG_FELDER sowie der Zahlstand (preis, anzahlungBetrag/-Dat, restBetrag,
  schlussZahlungDat, eingangBetrag).
Bewusst DRAUSSEN: Mitreisende-PII (teilnehmerliste), Notfallkontakt
(adrNotfallKontakt), interne Notizen (chroniken), Provision/Agentur/Berater,
Steuer-/Währungs-Details (*Cy, steuer*), pnrFileKey/interne IDs.

Vollständige Doku der API-Datenfelder in ``docs/kundendaten-datenzugriff.md``:
was der Endpunkt liefert, was wir davon nutzen und vor allem, was davon an
Gemini geht. Die maßgebliche Grenze ist die Modell-Grenze — den ganzen
Datensatz zu holen ist okay (bleibt serverseitig); minimal bleiben muss, was
im Gemini-Request landet. Änderungen hier gegen diese Grenze prüfen.
"""

import datetime
import re
from typing import Literal

import pytz
from langchain_core.tools import tool

# Bewusster Import der privaten TourOne-Plumbing-Funktion: es soll genau eine
# Implementierung geben, und die lebt in travel_index (Entscheidung 2A).
from travel_index import _tourone_get, get_titel_for_code

# Der 20s-Default von _tourone_get ist für Index-Builds; mitten im Chat muss
# die Wartezeit pro Request enger begrenzt sein (Entscheidung 5A).
TIMEOUT = 8

# Detailansicht: die Kette ist 1 + N Requests à TIMEOUT, also muss N begrenzt
# sein. anzahl/auswahl grenzen normal schon ein; MAX_DETAIL ist die harte
# Obergrenze für "alle, details=true".
MAX_DETAIL = 5

# Grobe Liste braucht keinen Hop 2 (nur Hop-1-Daten), ist also billig; diese
# Anzeige-Grenze verhindert nur einen Riesen-Dump bei Vielbuchern.
OVERVIEW_CAP = 25

# Nur diese Felder aus flugdaten erreichen jemals das Modell/den Kunden.
# pnrFileKey (PNR/Buchungsreferenz) und interne IDs bleiben bewusst draußen.
FLUG_FELDER = ("flugnr", "airline", "vonCo3Code", "nachCo3Code", "abflug", "ankunft")

UNBEKANNT_TEXT = (
    "Zu dieser Anmeldung konnte ich keine Kundendaten finden. "
    "Bitte melde dich in MeinChamäleon neu an oder wende dich an deinen "
    "Erlebnisberater."
)
KEINE_BUCHUNGEN_TEXT = (
    "Zu deinem Konto finde ich aktuell keine Buchungen. Falls du gerade erst "
    "gebucht hast, kann es einen Moment dauern — sonst hilft dir dein "
    "Erlebnisberater gern weiter."
)
FEHLER_TEXT = (
    "Deine Buchungsdaten sind gerade nicht abrufbar. Bitte versuche es später "
    "noch einmal oder wende dich an deinen Erlebnisberater."
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


def _fmt_euro(value: object) -> str:
    """``4099.5`` → ``4.099,50 €`` (deutsche Notation); "" für Nicht-Zahlen."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


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


def _titel_aus_code(*codes: object) -> str:
    """Erster reiseCode, den der Reise-Index zu einem echten Titel auflöst.

    Hop 1 liefert pro Buchung ein LEERES ``beschreibungen``, der echte Titel steht
    nur in Hop 2. Ohne diesen Lookup zeigte die grobe Liste deshalb den internen
    Code (``COSAN_NEU`` statt ``San Agustín``) — die Detailansicht war schon immer
    richtig, weil sie Hop 2 ohnehin holt.

    Der Index ist In-Memory und wird nur gepeekt (siehe ``get_titel_for_code``),
    kostet hier also keinen Request. Kennt er den Code nicht, bleibt es beim Code:
    lieber unaufgelöst als der Titel einer anderen Reise.
    """
    for code in codes:
        if isinstance(code, str) and code:
            titel = get_titel_for_code(code)
            if titel:
                return titel
    return ""


def _buchung_titel(buchung: dict, fallback: str) -> str:
    for beschreibung in buchung.get("beschreibungen") or []:
        if isinstance(beschreibung, dict) and beschreibung.get("titel"):
            return beschreibung["titel"]
    return fallback


def _zeit_marker(von: str, bis: str, heute: str) -> str:
    """kommend / läuft gerade / vergangen aus den Datumsstrings (billig)."""
    von, bis = von[:10], bis[:10]
    if bis and bis < heute:
        return "vergangen"
    if von and von > heute:
        return "kommend"
    if von and bis:
        return "läuft gerade"
    return ""


def _personen_text(buchung: dict) -> str:
    """``2 (2 Erwachsene)`` aus persAdult/persChild/persBaby; "" wenn leer."""
    a = buchung.get("persAdult") or 0
    k = buchung.get("persChild") or 0
    b = buchung.get("persBaby") or 0
    teile = []
    if a:
        teile.append("1 Erwachsener" if a == 1 else f"{a} Erwachsene")
    if k:
        teile.append("1 Kind" if k == 1 else f"{k} Kinder")
    if b:
        teile.append("1 Kleinkind" if b == 1 else f"{b} Kleinkinder")
    if not teile:
        return ""
    gesamt = buchung.get("personen") or (a + k + b)
    return f"{gesamt} ({', '.join(teile)})"


def _zahlstand_zeilen(buchung: dict) -> list[str]:
    """Whitelisted Zahlstand-Zeilen — nur die kundenrelevanten Beträge/Termine."""
    zeilen = []
    preis = _fmt_euro(buchung.get("preis"))
    if preis:
        zeilen.append(f"- Gesamtpreis: {preis}")
    anzahlung = _fmt_euro(buchung.get("anzahlungBetrag"))
    if anzahlung:
        anz_dat = str(buchung.get("anzahlungDat") or "")
        zeilen.append(
            f"- Anzahlung: {anzahlung}"
            + (f" (fällig {_fmt_datum(anz_dat)})" if anz_dat else "")
        )
    rest = _fmt_euro(buchung.get("restBetrag"))
    if rest:
        schluss_dat = str(buchung.get("schlussZahlungDat") or "")
        zeilen.append(
            f"- Offener Betrag: {rest}"
            + (f" (fällig {_fmt_datum(schluss_dat)})" if schluss_dat else "")
        )
    eingang = _fmt_euro(buchung.get("eingangBetrag"))
    if eingang:
        zeilen.append(f"- Bereits eingegangen: {eingang}")
    return zeilen


def _select(buchungen: list, auswahl: str, anzahl: int, heute: str) -> list:
    """Filter + Sortierung des Selektors. Ungültige auswahl → wie "alle"."""
    if auswahl == "kommende":
        sel = [b for b in buchungen if str(b.get("bisDat") or "")[:10] >= heute]
        sel.sort(key=lambda b: str(b.get("vonDat") or ""))  # nächste zuerst
    elif auswahl == "vergangene":
        sel = [b for b in buchungen if str(b.get("bisDat") or "")[:10] < heute]
        sel.sort(key=lambda b: str(b.get("vonDat") or ""), reverse=True)  # neueste zuerst
    else:  # "alle"
        sel = sorted(buchungen, key=lambda b: str(b.get("vonDat") or ""), reverse=True)
    if isinstance(anzahl, int) and anzahl > 0:
        sel = sel[:anzahl]
    return sel


def _overview_zeile(b: dict, heute: str) -> str:
    """Eine grobe Zeile pro Buchung — nur aus den Hop-1-Daten."""
    code = b.get("reiseCode")
    titel = _buchung_titel(b, _titel_aus_code(code) or code or "deine Reise")
    von, bis = str(b.get("vonDat") or ""), str(b.get("bisDat") or "")
    teile = [f'„{titel}"']
    if von:
        teile.append(f"({_fmt_datum(von)}" + (f" – {_fmt_datum(bis)})" if bis else ")"))
    teile.append(f"Buchungsnummer {b.get('vorgang')}")
    marker = _zeit_marker(von, bis, heute)
    if marker:
        teile.append(marker)
    return "- " + " · ".join(teile)


def _detail_block(emb: dict, buchung: dict, heute: str) -> str:
    """Ein Detail-Block pro Buchung — whitelisted, deutscher Text."""
    # Hop 2 hat den autoritativen Titel; Index und Code sind nur Notnagel, falls
    # beschreibungen auch dort leer ist.
    codes = (emb.get("reiseCode"), buchung.get("reiseCode"))
    titel = _buchung_titel(
        buchung, _titel_aus_code(*codes) or codes[0] or codes[1] or "deine Reise"
    )
    von = str(emb.get("vonDat") or buchung.get("vonDat") or "")
    bis = str(emb.get("bisDat") or buchung.get("bisDat") or "")
    kopf = f'Reise „{titel}"'
    if von and bis:
        kopf += f" ({_fmt_datum(von)} – {_fmt_datum(bis)})"
    zeilen = [kopf + ":", f"- Buchungsnummer: {buchung.get('vorgang') or emb.get('vorgang')}"]
    # status XX = storniert; dann keine Zahlstand-/Flugdaten zeigen.
    if buchung.get("status") != "OK":
        zeilen.append("- Status: storniert")
        return "\n".join(zeilen)
    zeilen.append("- Status: gebucht")
    pers = _personen_text(buchung)
    if pers:
        zeilen.append(f"- Reisende: {pers}")
    zeilen.extend(_zahlstand_zeilen(buchung))
    fluege = [f for f in buchung.get("flugdaten") or [] if isinstance(f, dict)]
    if fluege:
        fluege.sort(key=lambda f: (f.get("rang") or 0, str(f.get("abflug") or "")))
        zeilen.extend(_flug_zeile(f) for f in fluege)
    else:
        zeilen.append("- Flüge: noch nicht eingebucht (oft erst kurz vor Abreise)")
    return "\n".join(zeilen)


def fetch_buchungen_text(
    kunden_id: str, auswahl: str = "alle", anzahl: int = 0, details: bool = False
) -> str:
    """Hole und formatiere die (ausgewählten) Buchungen des Kunden. Wirft nie."""
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

    alle = [
        b
        for b in adresse.get("buchungen") or []
        if isinstance(b, dict) and b.get("vorgang")
    ]
    if not alle:
        return KEINE_BUCHUNGEN_TEXT

    heute = _heute()
    ausgewaehlt = _select(alle, auswahl, anzahl, heute)
    if not ausgewaehlt:
        return f'In der Auswahl „{auswahl}" finde ich keine Buchung.'

    if not details:
        zeilen = [_overview_zeile(b, heute) for b in ausgewaehlt[:OVERVIEW_CAP]]
        text = "Deine Buchungen:\n" + "\n".join(zeilen)
        if len(ausgewaehlt) > OVERVIEW_CAP:
            text += f"\n… und {len(ausgewaehlt) - OVERVIEW_CAP} weitere"
        return text

    bloecke: list[str] = []
    fehler_gesehen = False
    for eingebettet in ausgewaehlt[:MAX_DETAIL]:
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
        if not isinstance(buchung, dict):
            continue
        bloecke.append(_detail_block(eingebettet, buchung, heute))

    if not bloecke:
        return FEHLER_TEXT if fehler_gesehen else KEINE_BUCHUNGEN_TEXT
    text = "Deine Buchungen im Detail:\n\n" + "\n\n".join(bloecke)
    if len(ausgewaehlt) > MAX_DETAIL:
        text += (
            f"\n\n… und {len(ausgewaehlt) - MAX_DETAIL} weitere "
            "(grenze mit auswahl/anzahl ein)"
        )
    return text


def make_buchungen_tool(kunden_id: str):
    """Build the per-request bookings tool bound to this customer by closure.

    The tool takes selector params (auswahl/anzahl/details) but NEVER the
    kunden_id: the model can pick WHICH of the customer's own bookings and at
    what detail — but never WHOSE data is fetched, so prompt injection cannot
    cross customers.
    """

    @tool
    def buchungen_tool(
        auswahl: Literal["alle", "kommende", "vergangene"] = "alle",
        anzahl: int = 0,
        details: bool = False,
    ) -> str:
        """Ruft die Buchungen des eingeloggten Kunden aus dem Buchungssystem ab.

        Nur verwenden, wenn der Kunde nach seinen EIGENEN Buchungen/Reisen fragt
        — z.B. „Was habe ich gebucht?", „Wann geht mein Flug?", „Wie viel muss
        ich noch zahlen?", „Wie ist meine Buchungsnummer?".

        auswahl: „alle" (Standard), „kommende" (laufende + zukünftige) oder
          „vergangene".
        anzahl: 0 = alle der Auswahl; sonst nur die N relevantesten (bei
          „kommende" die nächsten, bei „vergangene"/„alle" die neuesten).
          Beispiele: nächste Reise → auswahl=„kommende", anzahl=1; die letzten
          beiden → auswahl=„vergangene", anzahl=2.
        details: false = grobe Liste (Titel, Zeitraum, Buchungsnummer). true =
          Detailansicht je Buchung (Status, Reisende, Zahlstand, Flüge). Erst
          die grobe Liste holen, dann bei Bedarf mit details=true nachfassen.
        """
        return fetch_buchungen_text(kunden_id, auswahl, anzahl, details)

    return buchungen_tool


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
