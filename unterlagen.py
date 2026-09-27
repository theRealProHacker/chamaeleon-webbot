"""Buchungsunterlagen (PDFs aus ``/get/buchung`` → ``unterlagen``) für Leon.

Reine Funktionen plus zwei Caches, keine Klassen. Die Buchung selbst holt der
Aufrufer (kundendaten/agenturdaten bzw. reiseinfo_tool); hier kommt nur das
fertige Buchungs-Dict an.

``dokumente_zeilen(buchung, heute)`` — Dokumente-Block im Detail-Block:
  ├─ storniert (XX)          → nichts
  ├─ Dokumentarten in fester Reihenfolge, gleichnamige nach int(id) nummeriert
  ├─ Teilnehmerdaten         → nur Name, KEIN Direktlink (Passnummern,
  │                             Geburtsdaten aller Mitreisenden)
  ├─ keine Reiseunterlagen   → Zeile „Schlussunterlagen noch nicht
  │                             bereitgestellt“ plus Tage bis Abreise
  └─ tripurl                 → nur solange bisDat >= heute

``reiseunterlagen(eintrag, …)`` / ``dokument(eintrag)`` — Inhalt für das Modell:
  eintrag ── lesen ── text(id, link)          [ttl 24 h, 256 Einträge]
                        ├─ _laden(link)       nur https://unterlagen.chamaeleon-reisen.de,
                        │                     Timeout 8 s, fremder Host → None
                        └─ _extrahieren       pypdf im gevent-Threadpool
              └─ gliedern(text)               Hauptüberschriften-Whitelist,
                                              Tageseinträge, Unterüberschriften

Modellgrenze: Teilnehmerdaten.pdf wird nie geladen. Einreisebestimmungen.pdf
nennt je Reisenden Name und Geburtsdatum; ans Modell geht je Staatsangehörigkeit
genau ein Block, beschriftet nur mit der Nationalität.
"""

import datetime
import io
import re
from collections import Counter
from urllib.parse import urlparse

import gevent
import requests
from cachetools.func import ttl_cache
from gevent import monkey
from pypdf import PdfReader

# Modul-Import statt ``from kundendaten import …``: kundendaten importiert
# seinerseits dieses Modul (Dokumente-Block im Detail-Block), und nur der
# spät gebundene Attributzugriff übersteht den Kreis-Import.
import kundendaten

# Gemessen 2026-09-27: alle 118 geprüften Links tragen genau diesen Host. Ein
# anderer ist ein Datenfehler bei TourOne, den wir sehen wollen, kein Grund,
# vom Server aus fremde Adressen abzurufen (Entscheidung A2/D3).
ERLAUBTER_HOST = "unterlagen.chamaeleon-reisen.de"
TIMEOUT = 8

# Unter 200 Zeichen ist ein PDF für uns Bild, Scan oder verschlüsselt — dann
# lieber ehrlich „nicht lesbar“ als aus Fetzen raten (A3).
MIN_ZEICHEN = 200

_gemeldete_hosts: set = set()  # einmal je Host je Prozess
_gemeldete_gliederung: set = set()  # einmal je Dokument-id je Prozess

# Dokumentarten in Anzeigereihenfolge: (Art, Namensstamm). Die Art ist
# zugleich der Schlüssel für ``dokument:<art>``. Zuordnung über den Namen,
# normalisiert (klein, Umlaute ausgeschrieben, Rest zu „-“).
_ARTEN = (
    ("reiseunterlagen", "reiseunterlagen"),
    ("reisebestaetigung", "reisebestaetigung"),
    ("flugplan", "flugplan"),
    ("aktueller-flugplan", "aktueller-flugplan"),
    ("wichtige-reisehinweise", "wichtige-reisehinweise"),
    ("rechnung", "rechnung"),
    ("reiseanmeldung", "reiseanmeldung"),
    ("visum-ausfuellhilfen", "visum-ausfuellhilfe"),
    ("visa-dokumente", "visa-dokument"),
    ("einreisebestimmungen", "einreisebestimmung"),
    ("teilnehmerdaten", "teilnehmerdaten"),
)
_RANG = {art: i for i, (art, _) in enumerate(_ARTEN)}

# Über ``abschnitt="dokument:<slug>"`` erreichbar (Design, Zweiter Commit).
# ULAS und BEST sind bewusst keine Slugs: sie laufen über quelle/abschnitt.
# Teilnehmerdaten auch nicht: die werden nie gelesen.
DOKUMENT_SLUGS = (
    "flugplan",
    "aktueller-flugplan",
    "wichtige-reisehinweise",
    "rechnung",
    "reiseanmeldung",
    "visum-ausfuellhilfen",
    "visa-dokumente",
    "einreisebestimmungen",
    "anschreiben",
)

TEILNEHMERDATEN_TEXT = (
    "Die Teilnehmerdaten (Passnummern, Geburtsdaten und Adressen aller "
    "Mitreisenden) lese ich nicht. Du findest sie in MeinChamäleon im Bereich "
    "„Unterlagen“ deiner Reise."
)
OHNE_LINK_TEXT = "„{name}“ ist in deinen Unterlagen gelistet, aber ohne Link."
NICHT_ABRUFBAR_TEXT = (
    "„{name}“ ist gerade nicht abrufbar. Bitte versuche es später noch einmal "
    "oder öffne das Dokument direkt: {link}"
)
NUR_LINK_TEXT = "„{name}“ kann ich nicht selbst öffnen, du findest es hier: {link}"
NICHT_LESBAR_TEXT = (
    "„{name}“ ist nicht lesbar (das PDF enthält keinen Text). Du findest es "
    "hier: {link}"
)
NICHT_GEGLIEDERT_TEXT = (
    "„{name}“ liegt vor, konnte aber nicht gegliedert werden. Du findest es "
    "hier: {link}"
)


# --- Dokumente-Block (erster Commit) -----------------------------------------


def _normname(name: object) -> str:
    """``Visum Ausfüllhilfen.pdf`` → ``visum-ausfuellhilfen``."""
    n = str(name or "").strip()
    if n.lower().endswith(".pdf"):
        n = n[:-4]
    n = n.lower().translate(
        {ord("ä"): "ae", ord("ö"): "oe", ord("ü"): "ue", ord("ß"): "ss"}
    )
    return re.sub(r"[^a-z0-9]+", "-", n).strip("-")


def dokument_art(name: object) -> str:
    """Art aus dem Dateinamen (``_ARTEN``, dazu ``anschreiben``); "" sonst."""
    n = _normname(name)
    for art, stamm in _ARTEN:
        if n.startswith(stamm):
            return art
    return "anschreiben" if n.startswith("anschreiben") else ""


def _anzeigename(name: object) -> str:
    n = str(name or "").strip()
    return n[:-4] if n.lower().endswith(".pdf") else n


def _id_schluessel(eintrag: dict) -> tuple:
    """Sortierschlüssel nach int(id); nicht-numerische ids ans Ende."""
    roh = str(eintrag.get("id") or "")
    return (int(roh), "") if roh.isdigit() else (float("inf"), roh)


def _eintraege(unterlagen: object) -> list:
    return [e for e in unterlagen or [] if isinstance(e, dict)]


def _sicherer_link(link: object) -> str:
    """Der Link, wenn er gefahrlos in einem Markdown-Link stehen kann; sonst ""."""
    if not isinstance(link, str):
        return ""
    link = link.strip()
    if not link.startswith("https://") or re.search(r"[\s()<>\[\]]", link):
        return ""
    return link


def _tage_bis(von: str, heute: str) -> int | None:
    try:
        return (
            datetime.date.fromisoformat(von[:10])
            - datetime.date.fromisoformat(heute[:10])
        ).days
    except ValueError:
        return None


def dokumente_zeilen(buchung: dict, heute: str) -> list[str]:
    """Die Dokumente-Zeilen für den Detail-Block einer Buchung.

    Gleichnamige Einträge sind verschiedene Dokumente (gemessen: BEST- und
    ULAS-Anschreiben, zwei Teilnehmerdaten-Versionen) und werden deshalb nicht
    dedupliziert, sondern mit ``beschreibung`` oder laufender Nummer in
    id-Reihenfolge unterschieden (aufsteigende id = ältere Version zuerst).
    """
    if kundendaten.ist_storniert(buchung.get("status")):
        return []
    eintraege = [e for e in _eintraege(buchung.get("unterlagen")) if e.get("name") or e.get("link")]
    eintraege.sort(
        key=lambda e: (
            _RANG.get(dokument_art(e.get("name")), len(_RANG)),
            _normname(e.get("name")),
            _id_schluessel(e),
        )
    )
    anzahl = Counter(_normname(e.get("name")) for e in eintraege)
    laufend: Counter = Counter()

    zeilen = []
    for e in eintraege:
        schluessel = _normname(e.get("name"))
        label = _anzeigename(e.get("name")) or "Dokument"
        if e.get("name") and anzahl[schluessel] > 1:
            laufend[schluessel] += 1
            zusatz = str(e.get("beschreibung") or "").strip() or str(laufend[schluessel])
            label = f"{label} ({zusatz})"
        link = _sicherer_link(e.get("link"))
        if dokument_art(e.get("name")) == "teilnehmerdaten":
            # Kein Direktlink: jede Antwort liegt im Chat-Log, und dieses PDF
            # trägt Passnummern und Geburtsdaten aller Mitreisenden.
            zeilen.append(f"  - {label} (nur in MeinChamäleon im Bereich „Unterlagen“ der Reise)")
        elif link:
            zeilen.append(f"  - [{label}]({link})")
        else:
            zeilen.append(f"  - {label} (ohne Link)")
    if zeilen:
        zeilen.insert(0, "- Dokumente:")

    # Nach der Reise ist „noch nicht bereitgestellt“ keine Auskunft mehr, sondern
    # eine falsche Fährte (alte Buchungen vor Einführung der ``unterlagen``).
    vorbei = str(buchung.get("bisDat") or "")[:10] < heute if buchung.get("bisDat") else False
    if not vorbei and not any(
        dokument_art(e.get("name")) == "reiseunterlagen" for e in eintraege
    ):
        von = str(buchung.get("vonDat") or "")
        tage = _tage_bis(von, heute)
        if tage is not None and tage >= 0:
            wann = "heute" if tage == 0 else ("morgen" if tage == 1 else f"in {tage} Tagen")
            zeilen.append(
                "- Schlussunterlagen (Reiseunterlagen) noch nicht bereitgestellt; "
                f"Reisebeginn {kundendaten.fmt_datum(von)}, {wann}"
            )
        else:
            zeilen.append("- Schlussunterlagen (Reiseunterlagen) noch nicht bereitgestellt")

    # Die TripURL meldet nach der Reise nur noch „Reisezeitraum liegt in der
    # Vergangenheit“ — dann lieber gar nicht nennen.
    tripurl = _sicherer_link(buchung.get("tripurl"))
    if tripurl and str(buchung.get("bisDat") or "")[:10] >= heute:
        zeilen.append(
            f"- Aktuelle Einreise-, Visa- und Impfbestimmungen für diese Reise: {tripurl}"
        )
    return zeilen


# --- Auswahl der Quelle (zweiter Commit) -------------------------------------


def _neueste(eintraege: list) -> dict | None:
    return max(eintraege, key=_id_schluessel) if eintraege else None


def quelle_auto(unterlagen: object) -> dict | None:
    """Reiseunterlagen, sonst Reisebestätigung (je die höchste id); sonst None."""
    for art in ("reiseunterlagen", "reisebestaetigung"):
        treffer = _neueste(
            [e for e in _eintraege(unterlagen) if dokument_art(e.get("name")) == art]
        )
        if treffer:
            return treffer
    return None


def dokumente_nach_slug(unterlagen: object) -> dict:
    """``{slug: eintrag}`` für DOKUMENT_SLUGS; je Slug gewinnt die höchste id."""
    ergebnis = {}
    for slug in DOKUMENT_SLUGS:
        treffer = _neueste(
            [e for e in _eintraege(unterlagen) if dokument_art(e.get("name")) == slug]
        )
        if treffer:
            ergebnis[slug] = treffer
    return ergebnis


# --- Download und Text -------------------------------------------------------


def _laden(link: str) -> bytes | None:
    """PDF-Bytes vom Unterlagen-Host; ``None`` bei fremdem Host. WIRFT bei Ausfall.

    Redirects werden nicht verfolgt: sonst hebelte ein Umweg über einen
    anderen Host die Allowlist aus. Ein Ausfall (Timeout, HTTP-Fehler) wird
    nicht gefangen, damit ``text`` ihn nicht 24 h lang cacht.
    """
    teile = urlparse(link)
    if teile.scheme != "https" or teile.hostname != ERLAUBTER_HOST:
        host = teile.hostname or "?"
        if host not in _gemeldete_hosts:
            _gemeldete_hosts.add(host)
            print(f"[unterlagen] fremder Host {host!r} — Dokument nur verlinkt")
        return None
    antwort = requests.get(link, timeout=TIMEOUT, allow_redirects=False)
    if antwort.status_code != 200:
        raise requests.HTTPError(f"HTTP {antwort.status_code}")
    return antwort.content


def _extrahieren(daten: bytes) -> str:
    """pypdf im Standardmodus; ``extraction_mode="layout"`` hat offene
    Wortspaltungs-Fehler. Ein kaputtes PDF ist „nicht lesbar“, kein Ausfall."""
    try:
        reader = PdfReader(io.BytesIO(daten))
        return "\n".join(seite.extract_text() or "" for seite in reader.pages)
    except Exception as e:
        print(f"[unterlagen] pypdf fehlgeschlagen: {type(e).__name__}")
        return ""


def _im_threadpool(funktion, *args):
    """Rechenarbeit im echten OS-Thread, wenn gevent den Prozess gepatcht hat.

    Unter gunicorn ``-k gevent`` gibt es nur einen Worker; 0,2 bis 0,4 s
    pypdf hielten sonst jeden laufenden Chat an (P1/D7). Ohne Patch (Tests,
    lokaler Flask) läuft es direkt.
    """
    if monkey.is_module_patched("threading"):
        return gevent.get_hub().threadpool.apply(funktion, args)
    return funktion(*args)


# Schlüssel ist die Dokument-id (sie wechselt mit jeder neuen Version) plus
# Link. 256 Einträge à rund 80k Zeichen sind rund 20 MB (A1/D4).
@ttl_cache(maxsize=256, ttl=86400)
def text(dok_id: str, link: str) -> str | None:
    """Extrahierter Text eines Dokuments — gecacht. WIRFT bei Ausfall.

    ``None``: Link nicht auf dem Unterlagen-Host, nicht geladen.
    ``""``: weniger als MIN_ZEICHEN Text, also nicht lesbar.
    """
    daten = _laden(link)
    if daten is None:
        return None
    roh = _im_threadpool(_extrahieren, daten)
    return roh if len(roh.strip()) >= MIN_ZEICHEN else ""


def lesen(eintrag: dict) -> tuple[str | None, str]:
    """``(text, "")`` oder ``(None, Hinweis mit Link)`` für einen Eintrag."""
    name = _anzeigename(eintrag.get("name")) or "Dokument"
    link = eintrag.get("link") or ""
    if dokument_art(eintrag.get("name")) == "teilnehmerdaten":
        return None, TEILNEHMERDATEN_TEXT
    if not eintrag.get("name") or not link:
        return None, OHNE_LINK_TEXT.format(name=name)
    try:
        inhalt = text(str(eintrag.get("id") or ""), link)
    except Exception as e:
        print(f"[unterlagen] Download fehlgeschlagen: {type(e).__name__}")
        return None, NICHT_ABRUFBAR_TEXT.format(name=name, link=link)
    if inhalt is None:
        return None, NUR_LINK_TEXT.format(name=name, link=link)
    if not inhalt:
        return None, NICHT_LESBAR_TEXT.format(name=name, link=link)
    return inhalt, ""


# --- Gliederung --------------------------------------------------------------

# Hauptüberschriften, stabil über alle geprüften Reisen und beide Template-
# Generationen (PDF-Analyse 2026-09-27). Schlüssel "" = generischer
# Chamäleon-Text (FAQ-Wissen), der nie ans Modell geht.
_HAUPT = tuple(
    (schluessel, re.compile(muster))
    for schluessel, muster in (
        ("highlights", r"HIGHLIGHTS|HÖHEPUNKTE"),
        ("reiseverlauf", r"DEINE CHAMÄLEON-REISELEITUNG:?"),
        ("leistungen", r"(?:ZUSATZ)?LEISTUNGE?N? BEI CHAMÄLEON"),
        (
            "hinweise",
            r"HINWEISE ZU DEN LEISTUNGEN UND ZUR REISE|HINWEISE ZU UNSEREN "
            r"EMPFEHLUNGEN|HINWEISE ZU DEINER REISE",
        ),
        ("wichtige-reisehinweise", r"WICHTIGE REISEHINWEISE"),
        ("checkliste", r"CHECKLISTE"),
        ("fluege", r"INFORMATIONEN LINIENFLUG|INFORMATIONEN INLANDS- UND REGIONALFLÜGE"),
        ("", r"ALLGEMEINE REISEINFORMATIONEN"),
        ("reiseinformationen", r"REISEINFORMATIONEN [A-ZÄÖÜ][A-ZÄÖÜ &'.-]*"),
        (
            "",
            r"ICH BIN DANN MAL WEG\.*|EU-HANDGEPÄCKREGELUNG\.?|BERATUNG|"
            r"WIE HAT ES DIR GEFALLEN\?|NOTIZEN",
        ),
    )
)
# „MUI NE / NACHTRÄUMEN / VIETNAM“: Titelblock eines Anschlussprogramms, danach
# folgen dessen Tage und eigene Leistungen.
_ANSCHLUSS = "NACHTRÄUMEN"
# Beide Template-Generationen: neu mit Wochentag, alt ohne.
_TAG = re.compile(r"^(?:(Mo|Di|Mi|Do|Fr|Sa|So)\s+)?(\d\d\.\d\d\.\d{4})\s+(\S.*)$")
# Tage nur im Reiseverlauf erkennen — ein Datum am Zeilenanfang in den
# Reiseinformationen ist kein Tag.
_TAG_KONTEXT = ("deckblatt", "highlights", "reiseverlauf", "tag")
_UNTER_MIT_ABSCHNITT = ("reiseinformationen", "wichtige-reisehinweise")
_GROSS = re.compile(r"[A-ZÄÖÜ]")


def _zeilen(roh: str) -> list[str]:
    """``\\f`` zu ``\\n``, Leerraum normalisieren, Leerzeilen weg."""
    zeilen = (re.sub(r"\s+", " ", z).strip() for z in roh.replace("\f", "\n").split("\n"))
    return [z for z in zeilen if z]


def _schluessel(zeile: str) -> str | None:
    for schluessel, muster in _HAUPT:
        if muster.fullmatch(zeile):
            return schluessel
    return None


def _hauptueberschrift(zeile: str, naechste: str) -> tuple | None:
    """``(schluessel, titel, verbrauchte_zeilen)`` oder None.

    Umbruch-Toleranz: „INFORMATIONEN INLANDS- UND“ / „REGIONALFLÜGE“ und
    „REISEINFORMATIONEN MACHU“ / „PICCHU“. Die Folgezeile wird nur angehängt,
    wenn sie kurz und durchgehend groß ist — Unterüberschriften sind gemischt.
    """
    if naechste and len(naechste) <= 30 and naechste == naechste.upper() and _GROSS.search(naechste):
        verbunden = f"{zeile} {naechste}"
        schluessel = _schluessel(verbunden)
        if schluessel is not None:
            return schluessel, verbunden, 2
    schluessel = _schluessel(zeile)
    return (schluessel, zeile, 1) if schluessel is not None else None


def _unterueberschriften(zeilen: list[str]) -> list[str]:
    """Kurze Zeile ohne Ziffer und Satzzeichen, gefolgt von einer langen."""
    unter = []
    for i, zeile in enumerate(zeilen[:-1]):
        if (
            3 <= len(zeile) <= 45
            and zeile[0].isupper()
            and not re.search(r"[\d.,:;!?()»«\"„“•]", zeile)
            and len(zeilen[i + 1]) > 45
        ):
            unter.append(zeile)
    return unter


def gliedern(roh: str) -> dict | None:
    """Text von Reiseunterlagen/Reisebestätigung → Abschnitte und Tage.

    Ergebnis::

        {"abschnitte": {schluessel: [{"titel", "text", "unter"}]},
         "tage": [{"wochentag", "datum", "titel", "text", "programm"}]}

    Deckblatt (Namen, Vorgang, Agentur) und generische Blöcke fallen weg.
    ``None``, wenn keine einzige bekannte Hauptüberschrift vorkommt — dann ist
    es ein unbekanntes Template, und raten wäre schlimmer als verlinken (F4).
    """
    zeilen = _zeilen(roh)
    teile = [{"schluessel": "deckblatt", "titel": "", "zeilen": []}]
    erkannt = False
    programm = ""
    i = 0
    while i < len(zeilen):
        zeile = zeilen[i]
        aktuell = teile[-1]
        naechste = zeilen[i + 1] if i + 1 < len(zeilen) else ""
        if zeile == _ANSCHLUSS:
            vorher = aktuell["zeilen"][-1] if aktuell["zeilen"] else ""
            if vorher and vorher == vorher.upper():
                aktuell["zeilen"].pop()
            programm = vorher if vorher == vorher.upper() else ""
            titel = f"Anschlussprogramm {programm}".strip()
            teile.append({"schluessel": "reiseverlauf", "titel": titel, "zeilen": []})
            i += 1
            continue
        treffer = _hauptueberschrift(zeile, naechste)
        if treffer:
            erkannt = True
            schluessel, titel, verbraucht = treffer
            teile.append({"schluessel": schluessel, "titel": titel, "zeilen": []})
            i += verbraucht
            continue
        tag = _TAG.match(zeile)
        if tag and aktuell["schluessel"] in _TAG_KONTEXT:
            teile.append(
                {
                    "schluessel": "tag",
                    "titel": tag.group(3),
                    "zeilen": [],
                    "wochentag": tag.group(1) or "",
                    "datum": tag.group(2),
                    "programm": programm,
                }
            )
            i += 1
            continue
        aktuell["zeilen"].append(zeile)
        i += 1
    if not erkannt:
        return None

    ergebnis: dict = {"abschnitte": {}, "tage": []}
    for teil in teile:
        schluessel = teil["schluessel"]
        if schluessel == "tag":
            ergebnis["tage"].append(
                {
                    "wochentag": teil["wochentag"],
                    "datum": teil["datum"],
                    "titel": teil["titel"],
                    "text": "\n".join(teil["zeilen"]),
                    "programm": teil["programm"],
                }
            )
        elif schluessel not in ("deckblatt", ""):
            ergebnis["abschnitte"].setdefault(schluessel, []).append(
                {
                    "titel": teil["titel"],
                    "text": "\n".join(teil["zeilen"]),
                    "unter": _unterueberschriften(teil["zeilen"])
                    if schluessel in _UNTER_MIT_ABSCHNITT
                    else [],
                }
            )
    return ergebnis


# --- Antworten fürs Modell ---------------------------------------------------

# Abschnitte, die ``abschnitt=…`` liefert, in Anzeigereihenfolge.
ABSCHNITTE = ("reiseverlauf", "leistungen", "hinweise", "checkliste", "reiseinformationen", "fluege")
_ABSCHNITT_NAMEN = {
    "leistungen": "Leistungen",
    "hinweise": "Hinweise zu Leistungen und Empfehlungen",
    "checkliste": "Checkliste (Dokumente, Apotheke, Kleidung …)",
    "reiseinformationen": "Reiseinformationen",
    "fluege": "Gepäck- und Fluginformationen",
}


def _tag_kopf(nummer: int, tag: dict) -> str:
    datum = f"{tag['wochentag']} {tag['datum']}".strip()
    kopf = f"Tag {nummer} · {datum} · {tag['titel']}"
    return kopf + (f" (Anschlussprogramm {tag['programm']})" if tag["programm"] else "")


def _reiseleitung(gl: dict) -> str:
    """Name (und Telefon) der Reiseleitung; mehrere Zeilen bei mehreren Personen."""
    for teil in gl["abschnitte"].get("reiseverlauf", []):
        if teil["titel"].startswith("DEINE CHAMÄLEON-REISELEITUNG") and teil["text"]:
            return teil["text"].replace("\n", " / ")
    return ""


def _vorhanden(gl: dict) -> list[str]:
    return [a for a in ABSCHNITTE if (gl["tage"] if a == "reiseverlauf" else gl["abschnitte"].get(a))]


def uebersicht(gl: dict, slugs=()) -> str:
    """Inhaltsverzeichnis plus WICHTIGE REISEHINWEISE komplett (Abholung,
    Partnerkontakt, Notfallnummer — die häufigsten Fragen ohne zweiten Aufruf)."""
    zeilen = ["Inhalt (Details mit abschnitt=…):"]
    tage = gl["tage"]
    if tage:
        zeilen.append(
            f"- reiseverlauf: {len(tage)} Tage, {tage[0]['datum']} bis {tage[-1]['datum']} "
            "(tag=N für einen Tag, tag=0 für alle)"
        )
        reiseleitung = _reiseleitung(gl)
        if reiseleitung:
            zeilen.append(f"  Reiseleitung: {reiseleitung}")
    for schluessel in ABSCHNITTE[1:]:
        teile = gl["abschnitte"].get(schluessel)
        if not teile:
            continue
        unter = [u for t in teile for u in t["unter"]]
        zeile = f"- {schluessel}: {_ABSCHNITT_NAMEN[schluessel]}"
        zeilen.append(zeile + (f" ({', '.join(unter)})" if unter else ""))
    if slugs:
        zeilen.append(
            "- Weitere Dokumente dieser Buchung: "
            + ", ".join(f"dokument:{s}" for s in slugs)
        )
    for teil in gl["abschnitte"].get("wichtige-reisehinweise", []):
        zeilen += ["", teil["titel"], teil["text"]]
    return "\n".join(zeilen)


def abschnitt_text(gl: dict, abschnitt: str, tag: int = 0, *, deckel: int) -> str:
    """Ein Abschnitt; beim Reiseverlauf ein Tag (tag=N) oder alle (tag=0).

    Alle Tage über ``deckel`` Zeichen → Inhaltsverzeichnis der Tage statt
    Kürzung: es wird nie mitten im Tag abgeschnitten.
    """
    abschnitt = (abschnitt or "").strip().lower()
    if abschnitt == "reiseverlauf":
        tage = gl["tage"]
        if not tage:
            return "Dieses Dokument enthält keinen Tagesablauf."
        if tag and tag > 0:
            if tag > len(tage):
                return f"Tag {tag} gibt es nicht; die Reise hat {len(tage)} Tage."
            return f"{_tag_kopf(tag, tage[tag - 1])}\n{tage[tag - 1]['text']}"
        kopf = []
        reiseleitung = _reiseleitung(gl)
        if reiseleitung:
            kopf.append(f"Reiseleitung: {reiseleitung}")
        voll = "\n\n".join(
            kopf + [f"{_tag_kopf(n, t)}\n{t['text']}" for n, t in enumerate(tage, 1)]
        )
        if len(voll) <= deckel:
            return voll
        return "\n".join(
            kopf
            + ["Alle Tage zusammen sind zu lang. Einzeln abrufbar mit tag=N:"]
            + [f"- {_tag_kopf(n, t)}" for n, t in enumerate(tage, 1)]
        )
    teile = gl["abschnitte"].get(abschnitt) if abschnitt in ABSCHNITTE else None
    if not teile:
        return (
            f"Den Abschnitt „{abschnitt}“ gibt es in diesem Dokument nicht. "
            f"Vorhanden: {', '.join(_vorhanden(gl)) or 'keiner'}."
        )
    return "\n\n".join(f"{t['titel']}\n{t['text']}".strip() for t in teile)


# „1. Herr Nachname, Vorname“ / „Geburtsdatum: …, Staatsangehörigkeit: DE“.
# Die Nummer klebt am Seitenende manchmal am Vortext („6 Stunden2. Frau …“).
_PERSON = re.compile(r"\d{1,2}\.[ \t]+[^\n]*\n[ \t]*Geburtsdatum:([^\n]*)\n?")
_NATION = re.compile(r"Staatsangehörigkeit:\s*([A-Z]{2,3})\b")


def _bereinigt(roh: str) -> str:
    # Sicherheitsnetz: eine Personenzeile, die das Muster verfehlt, bleibt
    # trotzdem draußen.
    return "\n".join(z for z in _zeilen(roh) if "Geburtsdatum:" not in z)


def einreise_bloecke(roh: str) -> str | None:
    """Einreisebestimmungen entdoppelt: je Staatsangehörigkeit ein Block.

    Das PDF wiederholt die Bestimmungen je Reisendem × Zielland, mit Name und
    Geburtsdatum im Kopf. Hier bleibt je Nationalität der Block des ersten
    Reisenden, beschriftet nur mit der Nationalität. Kopf mit Anschrift und
    Personenzeilen fallen weg. ``None``, wenn keine Personenzeile erkannt wird.
    """
    roh = re.sub(r"Trip-URL:\s*\S+", "", roh.replace("\f", "\n"))
    personen = list(_PERSON.finditer(roh))
    if not personen:
        return None
    kopf = roh[: personen[0].start()]
    stand = re.search(r"Berlin, (\d\d\.\d\d\.\d{4})", kopf) or re.search(
        r"Buchung vom (\d\d\.\d\d\.\d{4})", kopf
    )
    bloecke: dict = {}
    for k, person in enumerate(personen):
        nation = _NATION.search(person.group(1))
        nation = nation.group(1) if nation else "ohne Angabe"
        if nation in bloecke:
            continue
        ende = personen[k + 1].start() if k + 1 < len(personen) else len(roh)
        bloecke[nation] = _bereinigt(roh[person.end() : ende])
    zeilen = [
        "Einreisebestimmungen zum Zeitpunkt der Reiseanmeldung"
        + (f" (Stand bei Buchung, {stand.group(1)})" if stand else "")
    ]
    for nation, block in bloecke.items():
        zeilen += ["", f"Für Reisende mit Staatsangehörigkeit {nation}:", block]
    return "\n".join(zeilen)


def _nicht_gegliedert(eintrag: dict) -> str:
    dok_id = str(eintrag.get("id") or "")
    if dok_id not in _gemeldete_gliederung:
        _gemeldete_gliederung.add(dok_id)
        print(f"[unterlagen] Dokument {dok_id} ({eintrag.get('name')!r}) nicht gegliedert")
    return NICHT_GEGLIEDERT_TEXT.format(
        name=_anzeigename(eintrag.get("name")), link=eintrag.get("link")
    )


def reiseunterlagen(eintrag: dict, abschnitt: str = "", tag: int = 0, *, deckel: int, slugs=()) -> str:
    """Antwort aus Reiseunterlagen/Reisebestätigung: ohne ``abschnitt`` die
    Übersicht, sonst der Abschnitt. Fehler immer mit Link, nie „nicht vorhanden“."""
    inhalt, hinweis = lesen(eintrag)
    if inhalt is None:
        return hinweis
    gl = gliedern(inhalt)
    if gl is None:
        return _nicht_gegliedert(eintrag)
    kopf = f"Quelle: {_anzeigename(eintrag.get('name'))} ({eintrag.get('link')})"
    if not abschnitt:
        return f"{kopf}\n{uebersicht(gl, slugs)}"
    return f"{kopf}\n{abschnitt_text(gl, abschnitt, tag, deckel=deckel)}"


def dokument(eintrag: dict) -> str:
    """Ein kleines Dokument ganz (``abschnitt="dokument:<slug>"``);
    Einreisebestimmungen entdoppelt, Teilnehmerdaten nie."""
    inhalt, hinweis = lesen(eintrag)
    if inhalt is None:
        return hinweis
    if dokument_art(eintrag.get("name")) == "einreisebestimmungen":
        inhalt = einreise_bloecke(inhalt)
        if inhalt is None:
            return _nicht_gegliedert(eintrag)
    else:
        inhalt = "\n".join(_zeilen(inhalt))
    return f"Inhalt von „{_anzeigename(eintrag.get('name'))}“ ({eintrag.get('link')}):\n{inhalt}"
