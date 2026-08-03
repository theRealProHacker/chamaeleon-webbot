"""Agentur-Modus Buchungsdaten — die Buchungen EINER Agentur aus TourOne.

Gegenstück zu :mod:`kundendaten`, aber ein anderer Datenpfad: ein einziger Hop
liefert hier alles.

    GET /get/buchungLeistungenListe?agenturNummer=<agtNr>   (Hop 1, timeout=8)
      └─ buchungLeistungen.{ACTION, KUNDE, TEILNEHMERS[], LEISTUNGEN[]}

``details`` ist deshalb nur eine RENDERTIEFE über derselben Antwort und kostet
keinen zusätzlichen Request — anders als im Kundenpfad, wo details=true einen
Hop 2 je Buchung auslöst.

Zwei Wächter, beide nicht optional (Plan §8):

* **G2** — ``agenturNummer`` weglassen liefert HTTP 200 mit den Buchungen des
  GESAMTEN Mandanten (223.588 Stück). Deshalb geht jeder Request durch
  :func:`_agentur_get`, das an der Transportgrenze prüft. ``requests`` lässt
  ``None``-wertige Params still weg — genau der Fall, der katastrophal ist, und
  er entsteht durch Bibliotheksverhalten, nicht durch ein ``if`` von uns.
* **G3** — jede Zeile wird gegen die gebundene Agenturnummer nachgeprüft.
  Verglichen wird ``ACTION.AgenturNummer``, NICHT ``mandantAgtNr`` (das ist
  Chamäleon selbst) und nicht ``MeldeAgenturNummer`` (ein Dritter). Beide Seiten
  werden zu ``str`` normalisiert: der JSON-Typ ist nirgends garantiert, und
  dieselbe Antwort mischt Typen (``vorgangsNummer`` str, ``vorgangsId`` int).
  Ein naives ``!=`` würde bei int-Ankunft JEDE Zeile JEDER Agentur verwerfen und
  der Bot meldete „keine Buchungen" — genau das Versagen, das §7 verhindern soll.

Die Sammelregeln über ``LEISTUNGEN[]`` sind gemessen, nicht geraten — 15
Agenturen, 486 Buchungen, 2131 Einträge (docs/agentur-modus-plan.md §3.3). Die
wichtigste: **``VonDatum``/``BisDatum`` sind ``DDMMYY``** und damit lexikalisch
unsortierbar. Wer sie in :func:`kundendaten.select` steckt, vergleicht
``"030326" >= "2026-08-02"`` — immer falsch, jede Reise landet in „vergangen",
``auswahl="kommende"`` liefert leer, und ``fmt_datum`` zeigt dem Nutzer die
Rohziffern. Es wird ausschließlich ``leistungVonDat``/``leistungBisDat``
gelesen (``YYYY-MM-DD HH:MM:SS``).
"""

from typing import Literal

from langchain_core.tools import tool

import kundendaten
from kundendaten import fmt_datum, fmt_euro, select, zeit_marker
from travel_index import _tourone_get, get_titel_for_code

TIMEOUT = 8

# Ab wie vielen Zeilen ``details=true`` verweigert wird. In ZEILEN, nicht
# Requests: mit nur einem Hop ist die Promptgröße die einzige Schranke, die die
# Streichung von Hop 2 überlebt hat. Eine voll gerenderte Zeile misst ~250
# Zeichen gegen ~90 für eine grobe — bei den beobachteten 191 Buchungen der
# Unterschied zwischen ~4k und ~12k Tokens, und die eine gefragte Buchung
# ertrinkt unter 190 anderen.
DETAIL_ROW_CAP = 25

# Die Anforderung, die die gebuchte REISE bezeichnet (gemessen: erster Eintrag
# bei 422/486 Buchungen, längste Zeitspanne bei 391/486). Alle anderen Codes
# sind Zusatzleistungen: T=Transfer, F=Flug, V=Versicherung, RF=Rail&Fly,
# FO=Regenwald-Spende, X=Storno/Gebühren, …
REISE_ANFORDERUNG = "P"

# LeistungsBezeichnungen, die eine Produktlinie benennen statt einer Reise.
# Gemessen auf 86 von 436 Buchungen mit P-Eintrag — ohne den Index-Fallback
# hießen sie alle „Erlebnis-Reise".
_GENERISCHE_TITEL = {
    "Erlebnis-Reise",
    "Genießer-Reise",
    "Landaufenthalt",
    "Reise",
}

KEINE_BUCHUNGEN_TEXT = (
    "Zu dieser Agentur finde ich aktuell keine Buchungen im System."
)
FEHLER_TEXT = (
    "Die Buchungsdaten sind gerade nicht abrufbar. Bitte versuche es später "
    "noch einmal."
)
# Zeilen rein, null Zeilen raus ist eine Bug-Signatur, keine leere Agentur — die
# vierte Fehlerbedingung (Plan §7). Sie als Leerzustand zu rendern ist der Weg,
# auf dem sich ein Typwechsel oder eine Feldumbenennung monatelang versteckt.
G3_FEHLER_TEXT = FEHLER_TEXT


def _agentur_get(path: str, params: dict):
    """Authentifizierter GET, der ohne belegte ``agenturNummer`` gar nicht erst rausgeht.

    G2 an der Transportgrenze statt als Assert oben in der Funktion: ``requests``
    verwirft ``None``-wertige Params still, sodass ein durchgerutschtes ``None``
    den ungefilterten Mandanten-Abruf auslöst. Hier ist es unmöglich, weil kein
    Request ohne die Prüfung gebaut werden kann.
    """
    agt = params.get("agenturNummer")
    if not isinstance(agt, str) or not agt.strip():
        raise ValueError("agenturNummer fehlt oder ist leer — Abruf verweigert (G2)")
    return _tourone_get(path, params, timeout=TIMEOUT)


def _rows(page: object) -> list[dict]:
    """``{"0": {...}, "1": {...}, "anzahl": N}`` → Liste. Objekt, kein Array."""
    if isinstance(page, dict):
        return [v for k, v in page.items() if k.isdigit() and isinstance(v, dict)]
    if isinstance(page, list):
        return [r for r in page if isinstance(r, dict)]
    return []


def _leistungen(bl: dict) -> list[dict]:
    return [e for e in (bl.get("LEISTUNGEN") or []) if isinstance(e, dict)]


def _reise_eintrag(leistungen: list[dict]) -> dict | None:
    """Der P-Eintrag (die gebuchte Reise), oder None bei 50/486 Buchungen."""
    for e in leistungen:
        if e.get("Anforderung") == REISE_ANFORDERUNG:
            return e
    return None


def _tag(value: object) -> str:
    """``leistungVonDat`` → ``YYYY-MM-DD``; alles andere → "".

    Liest NUR die ISO-Felder. ``VonDatum`` ist ``DDMMYY`` und darf hier nie
    ankommen — siehe Modul-Docstring.
    """
    return value[:10] if isinstance(value, str) and len(value) >= 10 else ""


def _euro(value: object) -> str:
    """Betragsstring der Agentur-API → deutsches Euro-Format; "" wenn keiner.

    Alle Preisfelder kommen hier als STRING an, nicht als Zahl (gemessen §3.3) —
    ``fmt_euro`` lehnt Strings zu Recht ab (im Kundenpfad schützt genau das vor
    einem print_r-„Array"), also wird hier konvertiert statt dort gelockert.
    Format ist maschinell: ``4200.00``, ``4200``, ``0``, auch negativ
    (``-100.50`` bei Ermäßigungen), gelegentlich leer.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return fmt_euro(value)
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return fmt_euro(float(value.strip()))
    except ValueError:
        return ""


def _titel(reise: dict | None) -> str:
    """Reisetitel nach der gemessenen Kette (§3.3, Regel 3).

    Bezeichnung → Index über den Reisecode → generische Bezeichnung → Notnagel.
    Gemessen 303 / 116 / 6 / 11 von 436. Ohne den Index-Schritt trügen 133
    Buchungen (27%) „Erlebnis-Reise" oder gar nichts.
    """
    if reise is None:
        return "diese Reise"
    bez = reise.get("LeistungsBezeichnung")
    bez = bez.strip() if isinstance(bez, str) else ""
    if bez and bez not in _GENERISCHE_TITEL:
        return bez
    # get_titel_for_code ist ein nicht-blockierender Blick in eine bereits
    # gebaute Map (travel_index.py:794-810) und kostet keinen Request.
    code = reise.get("Leistung")
    titel = get_titel_for_code(code) if isinstance(code, str) else ""
    return titel or bez or "diese Reise"


def _normalise_row(row: dict, agentur_id: str) -> dict | None:
    """Eine Hop-1-Zeile → flaches Dict, oder ``None`` wenn G3 sie verwirft.

    Die Datums-, Titel- und Statusregeln stammen aus der Messung in
    docs/agentur-modus-plan.md §3.3.
    """
    bl = row.get("buchungLeistungen")
    if not isinstance(bl, dict):
        return None
    action = bl.get("ACTION") if isinstance(bl.get("ACTION"), dict) else {}

    # G3 — beide Seiten als str. Der JSON-Typ ist nirgends zugesichert.
    zeilen_agt = action.get("AgenturNummer")
    if str(zeilen_agt).strip() != str(agentur_id).strip():
        return None

    leistungen = _leistungen(bl)
    reise = _reise_eintrag(leistungen)

    # Regel 1: die Spanne des P-Eintrags; ohne P-Eintrag min/max über alles.
    # NICHT pauschal min/max — Versicherungen, Regenwald-Spenden und Gutscheine
    # tragen Daten ohne Reisebedeutung und weiten das Fenster falsch (41/436).
    if reise is not None:
        von, bis = _tag(reise.get("leistungVonDat")), _tag(reise.get("leistungBisDat"))
    else:
        vons = [d for d in (_tag(e.get("leistungVonDat")) for e in leistungen) if d]
        biss = [d for d in (_tag(e.get("leistungBisDat")) for e in leistungen) if d]
        von, bis = (min(vons) if vons else ""), (max(biss) if biss else "")

    # Regel 4: der Status des P-Eintrags gewinnt. Ein „alle XX"-Test meldete 6
    # von 436 stornierten Reisen als aktiv (Reise storniert, Restposten offen).
    if reise is not None:
        storniert = reise.get("LeistungsStatus") == "XX"
    else:
        stati = [e.get("LeistungsStatus") for e in leistungen]
        storniert = bool(stati) and all(s == "XX" for s in stati)

    kunde = bl.get("KUNDE") if isinstance(bl.get("KUNDE"), dict) else {}
    teilnehmer = [t for t in (bl.get("TEILNEHMERS") or []) if isinstance(t, dict)]

    return {
        "vorgang": row.get("vorgangsNummer") or action.get("VorgangsNummer") or "",
        "titel": _titel(reise),
        "vonDat": von,
        "bisDat": bis,
        "storniert": storniert,
        # Whitelist (§6): der NAME des Bestellers, nie Straße/PLZ/Ort/Telefon.
        "kunde": (kunde.get("VornameTitel") or "").strip(),
        "provision": action.get("AgenturCommission"),
        "gesamtpreis": action.get("GesamtPreis"),
        # D9: Mitreisende sind für Agenturen bewusst DRIN — anders als im
        # Kundenpfad (kundendaten.py sagt „Bewusst DRAUSSEN: Mitreisende-PII").
        # Die Asymmetrie ist gewollt und darf nicht in eine Richtung
        # vereinheitlicht werden: die Agentur hat die Buchung selbst angelegt.
        "teilnehmer": [
            (t.get("Name") or "").strip() for t in teilnehmer if t.get("Name")
        ],
        "leistungen": [
            {
                "bezeichnung": (e.get("LeistungsBezeichnung") or "").strip(),
                "von": _tag(e.get("leistungVonDat")),
                "bis": _tag(e.get("leistungBisDat")),
                "storniert": e.get("LeistungsStatus") == "XX",
            }
            for e in leistungen
        ],
    }


def _overview_zeile(b: dict, heute: str, ist_naechste: bool = False) -> str:
    """Eine grobe Zeile je Buchung — Titel, Zeitraum, Nummer, Besteller."""
    teile = [f'„{b["titel"]}"']
    von, bis = b["vonDat"], b["bisDat"]
    if von:
        teile.append(f"({fmt_datum(von)}" + (f" – {fmt_datum(bis)})" if bis else ")"))
    teile.append(f"Buchungsnummer {b['vorgang']}")
    if b["kunde"]:
        teile.append(b["kunde"])
    if b["storniert"]:
        teile.append("storniert")
    else:
        marker = zeit_marker(von, bis, heute)
        if ist_naechste and marker == "kommend":
            marker = "nächste Reise"
        if marker:
            teile.append(marker)
    return "- " + " · ".join(teile)


def _detail_block(b: dict, heute: str) -> str:
    """Ein Detailblock je Buchung — aus DERSELBEN Hop-1-Antwort, kein Abruf."""
    kopf = f'Buchung {b["vorgang"]} — „{b["titel"]}"'
    if b["vonDat"] and b["bisDat"]:
        kopf += f" ({fmt_datum(b['vonDat'])} – {fmt_datum(b['bisDat'])})"
    zeilen = [kopf + ":"]
    zeilen.append("- Status: " + ("storniert" if b["storniert"] else "gebucht"))
    if b["kunde"]:
        zeilen.append(f"- Besteller: {b['kunde']}")
    if b["teilnehmer"]:
        zeilen.append(f"- Reisende: {', '.join(b['teilnehmer'])}")
    preis = _euro(b["gesamtpreis"])
    if preis:
        zeilen.append(f"- Gesamtpreis: {preis}")
    provision = _euro(b["provision"])
    if provision:
        zeilen.append(f"- Provision: {provision}")
    posten = [
        "  · "
        + p["bezeichnung"]
        + (f" ({fmt_datum(p['von'])} – {fmt_datum(p['bis'])})" if p["von"] else "")
        + (" — storniert" if p["storniert"] else "")
        for p in b["leistungen"]
        if p["bezeichnung"]
    ]
    if posten:
        zeilen.append("- Leistungen:")
        zeilen.extend(posten)
    return "\n".join(zeilen)


def fetch_buchungen_text(
    agentur_id: str, auswahl: str = "alle", anzahl: int = 0, details: bool = False
) -> str:
    """Hole und formatiere die Buchungen dieser Agentur. Wirft nie."""
    try:
        page = _agentur_get(
            "/get/buchungLeistungenListe", {"agenturNummer": agentur_id}
        )
    except Exception as e:
        # ~1–2% der eingeloggten Agenturen scheitern hier hart und dauerhaft
        # (§3). Das MUSS als „gerade nicht abrufbar" ankommen, nie als „keine
        # Buchungen" — sonst schließt der Reiseprofi, es gäbe sie nicht.
        print(f"[agenturdaten] buchungLeistungenListe failed: {type(e).__name__}")
        return FEHLER_TEXT

    roh = _rows(page)
    if not roh:
        return KEINE_BUCHUNGEN_TEXT

    alle = [n for n in (_normalise_row(r, agentur_id) for r in roh) if n]
    if not alle:
        # Zeilen rein, null Zeilen raus: Bug-Signatur, kein Leerzustand.
        print(
            f"[agenturdaten] G3 verwarf ALLE {len(roh)} Zeilen — "
            "AgenturNummer-Abgleich fehlgeschlagen"
        )
        return G3_FEHLER_TEXT

    heute = kundendaten._heute()
    ausgewaehlt = select(alle, auswahl, anzahl, heute)
    if not ausgewaehlt:
        return f'In der Auswahl „{auswahl}" finde ich keine Buchung.'

    if details and len(ausgewaehlt) > DETAIL_ROW_CAP:
        # Verweigerung, auf die der Nutzer reagieren kann — keine stille Kürzung.
        return (
            f"Die Auswahl umfasst {len(ausgewaehlt)} Buchungen; die Detailansicht "
            f"zeige ich bis {DETAIL_ROW_CAP}. Grenze bitte mit auswahl "
            '(„kommende"/„vergangene") oder anzahl weiter ein.'
        )

    if not details:
        naechste = next(
            (
                i
                for i, b in enumerate(ausgewaehlt)
                if not b["storniert"]
                and zeit_marker(b["vonDat"], b["bisDat"], heute) == "kommend"
            ),
            None,
        )
        zeilen = [
            _overview_zeile(b, heute, ist_naechste=(i == naechste))
            for i, b in enumerate(ausgewaehlt)
        ]
        return "Buchungen dieser Agentur:\n" + "\n".join(zeilen)

    return "Buchungen dieser Agentur im Detail:\n\n" + "\n\n".join(
        _detail_block(b, heute) for b in ausgewaehlt
    )


def make_buchungen_agentur_tool(agentur_id: str):
    """Baue das Buchungs-Tool, per Closure an genau diese Agentur gebunden.

    Das Tool nimmt Selektor-Parameter, aber NIE die Agenturnummer: das Modell
    darf wählen, WELCHE der eigenen Buchungen und in welcher Tiefe — nie, WESSEN
    Daten geholt werden. Prompt Injection kann so keine Agenturgrenze
    überschreiten.
    """

    @tool
    def buchungen_agentur_tool(
        auswahl: Literal["alle", "kommende", "vergangene"] = "alle",
        anzahl: int = 0,
        details: bool = False,
    ) -> str:
        """Ruft die Buchungen der eingeloggten Agentur aus dem Buchungssystem ab.

        Nur verwenden, wenn nach Buchungen/Reisen DIESER Agentur gefragt wird —
        z.B. „Welche Buchungen haben wir?", „Was hat Familie Müller gebucht?",
        „Wie hoch ist unsere Provision?", „Wer reist bei Vorgang 4711 mit?".

        auswahl: „alle" (Standard), „kommende" (laufende + zukünftige) oder
          „vergangene".
        anzahl: 0 = alle der Auswahl; sonst nur die N relevantesten (bei
          „alle"/„kommende" die zeitlich nächsten, bei „vergangene" die
          neuesten). Welche die nächste Reise ist, musst du NICHT aus der
          Reihenfolge erschließen — genau diese Zeile ist markiert.
        details: false = grobe Liste (Titel, Zeitraum, Buchungsnummer,
          Besteller). true = Detailansicht (Reisende, Gesamtpreis, Provision,
          Einzelleistungen). Beide stammen aus DEMSELBEN Abruf, details=true
          kostet also keinen zusätzlichen Request — aber es macht die Ausgabe
          lang. Hol immer erst die grobe Liste und fasse dann mit
          auswahl/anzahl eingegrenzt und details=true nach.
        Stornierte Buchungen sind mit dabei und in beiden Ansichten als
          „storniert" gekennzeichnet.
        Die Antwort beginnt mit der Agenturnummer der eingeloggten Agentur —
          das ist die EINZIGE Quelle dafür. Leite sie nie aus einer
          Buchungsnummer ab.
        """
        # Die Agenturnummer steht bewusst NICHT im System-Prompt (gleiche Regel
        # wie für die kunden_id, agent_base.py:703-705). Ohne sie hier hat das
        # Modell aber keinen belegten Wert und erfindet einen: gemessen
        # 2026-08-02 antwortete es auf „Wie lautet unsere Agenturnummer?" mit
        # der ersten BUCHUNGSNUMMER aus der Liste. Falsch und selbstsicher ist
        # schlechter als die Verweigerung — und §6a sagt ohnehin, dass eine
        # verifizierte Session die Antwort auf genau diese Frage IST.
        # Über den Tool-Output ist sie belegt statt geraten, und sie erreicht
        # nur die Agentur, der sie ohnehin gehört.
        return f"Agenturnummer: {agentur_id}\n\n" + fetch_buchungen_text(
            agentur_id, auswahl, anzahl, details
        )

    return buchungen_agentur_tool
