"""Live-Eval zum Kundenfeedback September 2026 (docs/kundenfeedback-2026-09-plan.md).

Aufbau wie tests/test_agentur_faq.py: eine echte Frage an Leon, die Antwort vom
Live-Modell, und dann beide Richtungen — was drinstehen muss und was nie
drinstehen darf. `keyword_matches` und `telefonnummern` kommen von dort, damit
es sie nur einmal gibt.

NIE Teil der Standardsuite — jeder Fall ist ein Gemini-Aufruf. Manuell:

    RUN_KUNDENFEEDBACK_EVAL=1 pytest tests/test_kundenfeedback_eval.py -q -s -k filter

Die Bloecke entsprechen den Ursachen aus dem Plan und laufen einzeln ueber `-k`:

    anker           die festen Wahrheiten, ohne Modell — vor jedem Block (D16)
    filter          Land, Dauer und genannte Orte pruefen statt raten (D1, F8-F10)
    airline         die Airline steht in den Leistungen der Seite (D2)
    nichtangeboten  Land nicht im Programm: klare Absage (D3)
    ungefragt       FAQ-Inhalte nur auf Frage (D4, F11)
    fachwissen      Garantie, Reservierung, Erlebnisberater (D5, F2, F4, F5)
    erfinden        keine Nummer und keine Aussage aus dem Nichts (F1, F7)
    flug            Flugfragen nicht mit dem Gepaeck-PDF beantworten (F6a)

Gemessen 2026-09-18 (test_agentur_faq): ab ~30 Aufrufen am Stueck kippt Gemini
in die leere Antwort. Deshalb blockweise laufen lassen, nie die ganze Datei.
Ein roter Fall heisst zuerst "nochmal einzeln laufen lassen".

`-s` gehoert dazu: die Quotenzeilen (siehe EVAL_N) schreibt der Test auf stdout,
und pytest schluckt stdout bei bestandenen Tests.

Drei Dinge unterscheiden diese Datei von der Agentur-Suite:

**Erwartungen kommen aus den Seiten, nicht aus dem Kopf.** Welche
Tansania-Reise ohne Sansibar auskommt und mit welcher Airline man nach Marokko
fliegt, steht auf der Webseite und aendert sich mit dem Programm. Der Test
liest es zur Laufzeit ueber `agent_base.seiten_abschnitt` — dieselbe Funktion,
mit der das Website-Tool dem Modell den Ausschnitt zeigt.

**Ankerwerte gegen den Zirkelschluss (Review D16).** Weil Tool und Eval sich
diese Funktion teilen, macht ein Fehler darin beide gleich falsch. Darum ein
paar fest eingetragene Wahrheiten mit Datum, geprueft ohne Modell: Ruaha ohne
Sansibar, Cheetah mit, Kasbah mit Discover Airlines, Etosha 19 Tage, Gorilla
nennt Ruanda nur als Grenze. Vor einem Block erst `-k anker` laufen lassen.
Eine leere ODER vollstaendige Erwartungsmenge ist ein Fehlschlag, kein Skip:
"keine Tansania-Reise ohne Sansibar" und "alle ohne" heissen beide, dass die
Ableitung kaputt ist, nicht dass das Programm sich geaendert hat.

**Schwesterfaelle gegen Auswendiglernen (Review D15).** Jeder Kernfall hat eine
Umformulierung mit anderem Land oder Wortlaut, die in keinem Prompt-Beispiel
steht (`-schwester` im Fallnamen). Wird ein Fall gruen, weil eine Prompt-Regel
genau diesen Wortlaut nennt, bleibt der Schwesterfall rot und zeigt es.

Laufzeit, gemessen 2026-09-23 (waehrend sechs Agenten parallel live pruefen):
der erste Seitenabruf im Prozess baut den travel_index neu und kostet allein
rund drei Minuten; eine Reiseseite danach 9 s. Der Block `filter` leitet seine
Erwartung aus allen 36 Tansania-Seiten ab. Das ist der Preis dafuer, dass die
Erwartung aus dem Programm kommt und nicht aus einer Liste im Test.
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from html import unescape

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

from agent import call
from agent_base import (
    all_countries,
    chamaeleon_website_tool_base,
    seiten_abschnitt,
    trip_sites,
)
from test_agentur_faq import keyword_matches, nennt_durchwahl, telefonnummern

RUN = os.getenv("RUN_KUNDENFEEDBACK_EVAL") == "1"

pytestmark = pytest.mark.skipif(
    not RUN, reason="live Kundenfeedback-Eval - RUN_KUNDENFEEDBACK_EVAL=1 setzen"
)

# Wiederholungen je Fall. Ein einzelner Lauf ist auch bei temperature=0.1 noch
# Rauschen; fuer die Ausgangs- und die Schlussmessung faehrt der Orchestrator
# EVAL_N=3 und vergleicht Quoten statt rot/gruen.
EVAL_N = max(1, int(os.getenv("EVAL_N", "1")))

L = re.escape  # Links und URLs woertlich vergleichen, nicht als Regex


# --- Seiten lesen ----------------------------------------------------------

_SEITEN_PARALLEL = 6

# chamaeleon_website_tool_base wirft bei einem fehlgeschlagenen Abruf NICHT,
# sondern gibt den Fehler als Seiteninhalt zurueck. Auf so einem String liefert
# seiten_abschnitt dann None — "Abruf kaputt" saehe damit exakt aus wie "Seite
# hat keinen Reiseverlauf" bzw. "Sansibar kommt nicht vor", und der Eval leitete
# still eine falsche Erwartung ab. Also: ein Ausfall ist ein Fehler, kein
# Datenpunkt, und faerbt den Block rot statt ihn zu ueberspringen (Review D16).
_ABRUF_FEHLER = ("Fehler beim Abrufen der Seite:", "Unerwarteter Fehler:")

# Gemessen 2026-09-23: unter paralleler Last antwortet die Website in 9 s, und
# das requests-Timeout von 10 s in get_chamaeleon_website_html wird dabei
# reproduzierbar gerissen. Ein solcher Ausfall ist eine Aussage ueber das Netz,
# nicht ueber das Reiseprogramm — deshalb zwei Wiederholungen. Bleibt es dabei,
# wird es zum Fehler.
_ABRUF_VERSUCHE = 3
_ABRUF_PAUSE_S = 2.0


class AbrufFehlgeschlagen(RuntimeError):
    """Eine Seite kam nicht — jede Erwartung daraus waere geraten."""


@lru_cache(maxsize=None)
def seite(url_path: str) -> str:
    """Das Markdown einer Seite, genau wie das Website-Tool es liefert."""
    letzter = ""
    for versuch in range(_ABRUF_VERSUCHE):
        markdown = chamaeleon_website_tool_base(url_path)
        if not markdown.startswith(_ABRUF_FEHLER):
            return markdown
        letzter = markdown
        if versuch + 1 < _ABRUF_VERSUCHE:
            time.sleep(_ABRUF_PAUSE_S)
    raise AbrufFehlgeschlagen(f"{url_path}: {letzter[:160]}")


def abschnitt(url_path: str, name: str) -> str | None:
    """Ein Abschnitt einer Seite; ``None``, wenn die Seite ihn nicht traegt."""
    return seiten_abschnitt(seite(url_path), name)


def _hole_parallel(pfade: list[str]) -> list[str]:
    """Alle Seiten parallel; ein einziger Ausfall reisst die Ableitung mit."""
    with ThreadPoolExecutor(max_workers=_SEITEN_PARALLEL) as pool:
        return list(pool.map(seite, pfade))


def land_seiten(land: str) -> list[str]:
    """Alle Reise-URLs eines Landes aus der Sitemap, in fester Reihenfolge."""
    return sorted(s for s in trip_sites if s.split("/")[2] == land)


@lru_cache(maxsize=None)
def reiseverlaeufe(land: str) -> tuple[tuple[str, str], ...]:
    """``((URL, Reiseverlauf), …)`` aller echten Reisen eines Landes.

    Gefiltert wird auf "hat einen Reiseverlauf", nicht auf den Namen: die
    Verlaengerungs- und Vorprogrammseiten (`Nachtraeumen-…`, `Vorfreuen-…`)
    tragen keinen, und genau das macht sie zu Nicht-Reisen. Ueber den Namen zu
    filtern hiesse, eine Namenskonvention als Programmwissen auszugeben — und
    `/Afrika/Namibia/Etosha-ALL` zeigt, dass die Konvention nicht traegt: das
    ist die Laenderuebersicht, keine Reise, und faellt hier von selbst heraus.
    """
    pfade = land_seiten(land)
    gefunden = []
    for pfad, markdown in zip(pfade, _hole_parallel(pfade)):
        verlauf = seiten_abschnitt(markdown, "reiseverlauf")
        if verlauf:
            gefunden.append((pfad, verlauf))
    assert gefunden, f"keine einzige Reiseseite mit Reiseverlauf fuer {land}"
    return tuple(gefunden)


def reisename(url_path: str) -> str:
    """Der Name, unter dem Leon eine Reise nennt: das letzte Pfadstueck.

    Ohne die Jahres- und Varianten-Suffixe, die auf der Seite nirgends stehen —
    `Cheetah-NEU` heisst im Text `Cheetah`.
    """
    return re.sub(r"-(?:ALL|NEU|Neu|\d{2,4})$", "", url_path.rsplit("/", 1)[-1])


def name_muster(url_path: str) -> str:
    """Der Reisename als Suchmuster.

    Zwischen den Wortteilen schreibt mal die Website einen Bindestrich und mal
    das Modell ein Leerzeichen ("Mara-Fluss" / "Mara Fluss").
    """
    return r"[-\s]".join(re.escape(teil) for teil in reisename(url_path).split("-"))


def fremde_kombilaender(land: str) -> list[str]:
    """Link-Muster der Kombi-Laender, in die eine Empfehlung abrutscht (F10).

    Wer Namibia sagt, meint nicht Botswana-Namibia — Moremi war genau dieser
    Fehler. Geprueft wird der Link und nicht das Wort: eine Namibia-Reise darf
    Botswana im Verlauf erwaehnen, sie darf nur keine Botswana-Reise sein.
    Die Liste kommt aus der Sitemap, nicht aus dem Kopf.
    """
    muster = set()
    for pfad in trip_sites:
        schluessel = pfad.split("/")[2]
        if schluessel != land and land in schluessel:
            muster.add(L("/".join(pfad.split("/")[:3]) + "/"))
    return sorted(muster)


def trennt_sauber(mit: list, ohne: list, was: str) -> None:
    """Review D16: eine leere ODER vollstaendige Menge ist ein Fehlschlag.

    Findet die Ableitung das Merkmal auf keiner oder auf jeder Seite, dann sagt
    sie nichts ueber das Programm, sondern ueber sich selbst — der Schnitt oder
    die Suche ist kaputt. Beides ist rot, nie ein Skip.
    """
    assert mit and ohne, (
        f"{was}: die Erwartungsmenge trennt nicht (mit={sorted(mit)}, "
        f"ohne={sorted(ohne)}) — Ableitung pruefen, nicht den Fall gruen biegen"
    )


# --- Merkmale aus den Seiten ----------------------------------------------


@lru_cache(maxsize=None)
def nach_merkmal(land: str, merkmal: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(ohne, mit)`` als URL-Listen: nennt der REISEVERLAUF das Merkmal?

    Die Verlaengerungen zaehlen nicht zur Reise, deshalb der Reiseverlauf und
    nicht die ganze Seite. Genau daran ist Leon im Feedback gescheitert: alle
    Tansania-Reisen nennen Sansibar irgendwo, nur ein Teil im Reiseverlauf.
    """
    ohne, mit = [], []
    for pfad, verlauf in reiseverlaeufe(land):
        (mit if merkmal.lower() in verlauf.lower() else ohne).append(pfad)
    trennt_sauber(mit, ohne, f"{land} ohne {merkmal}")
    return tuple(ohne), tuple(mit)


def ohne_merkmal(land: str, merkmal: str) -> dict:
    """Erwartungsfelder fuer "eine Reise OHNE X"."""
    return empfiehl_nur(*nach_merkmal(land, merkmal))


def empfiehl_nur(ohne: tuple[str, ...], mit: tuple[str, ...]) -> dict:
    """Erwartungsfelder: eine der passenden Reisen, keine der unpassenden.

    Gegen die unpassenden wird auf den LINK geprueft, nicht auf den Namen: eine
    Mara-Fluss-Empfehlung darf den Ngorongoro-Krater im Verlauf erwaehnen, nur
    die Ngorongoro-REISE darf sie nicht sein.
    """
    return {
        "muss_eines": [L(p) for p in ohne] + [name_muster(p) for p in ohne],
        "darf_nicht": [L(p) for p in mit],
    }


_DAUER = re.compile(r"-\s*(\d{1,2})(?:\s*oder\s*(\d{1,2}))?\s*Tage")


@lru_cache(maxsize=None)
def dauern(land: str) -> tuple[tuple[str, int], ...]:
    """``((URL, Tage), …)`` aus den Seitentiteln eines Landes.

    Der Titel traegt die Dauer in fester Form ("Etosha - 19 Tage
    Erlebnisreise"). Steht dort eine Spanne ("14 oder 15 Tage"), zaehlt die
    kuerzere — danach fragt, wer sagt, er habe nur 14 Tage Zeit.
    """
    gefunden = []
    for pfad, _verlauf in reiseverlaeufe(land):
        zeilen = (abschnitt(pfad, "uebersicht") or "").splitlines()
        treffer = _DAUER.search(zeilen[0] if zeilen else "")
        if treffer:
            gefunden.append((pfad, min(int(t) for t in treffer.groups() if t)))
    assert gefunden, f"keine Dauer aus den Seitentiteln von {land} gelesen"
    return tuple(gefunden)


def passt_in(land: str, tage: int) -> dict:
    """Erwartungsfelder fuer ein Zeitbudget: kurz genug ja, zu lang nein."""
    passend = tuple(p for p, t in dauern(land) if t <= tage)
    zu_lang = tuple(p for p, t in dauern(land) if t > tage)
    trennt_sauber(list(zu_lang), list(passend), f"{land} in {tage} Tagen")
    return empfiehl_nur(passend, zu_lang)


def spaeter(bauer, *args) -> dict:
    """Erwartungsfelder, die erst im Test Seiten abrufen.

    Beim Sammeln darf kein Byte ueber das Netz gehen — sonst kostet schon
    `--collect-only` Minuten, und ein Netzfehler faellt als Sammelfehler an
    statt als roter Fall.
    """
    return {
        "muss_eines": lambda: bauer(*args).get("muss_eines", []),
        "darf_nicht": lambda: bauer(*args).get("darf_nicht", []),
    }


def deckt_ab(land: str, orte: tuple[str, ...]) -> dict:
    """Erwartungsfelder fuer "diese Orte alle in einer Reise" (F8)."""
    passend = tuple(
        p
        for p, verlauf in reiseverlaeufe(land)
        if all(re.search(ort, verlauf, re.IGNORECASE) for ort in orte)
    )
    andere = tuple(p for p, _ in reiseverlaeufe(land) if p not in passend)
    trennt_sauber(list(passend), list(andere), f"{land} mit {orte}")
    # Die anderen Reisen stehen hier NICHT im darf_nicht: wer alle vier Orte
    # traegt, ist die Antwort, aber eine zweite Reise daneben zu nennen ist
    # kein Fehler. Der Fehler im Feedback war "haben wir nicht im Programm".
    return {"muss_eines": [L(p) for p in passend] + [name_muster(p) for p in passend]}


# Die Leistungen nennen den Flug in fester Form: "Linienflug mit Discover
# Airlines nach Marrakesch und zurueck (Buchungsklasse T)". Gemessen
# 2026-09-23 auf der Kasbah-Seite.
_AIRLINE = re.compile(
    r"Linienfl(?:ug|üge|uege)\b[^.\n]*?\bmit\s+(?:der\s+)?"
    r"([A-ZÄÖÜ][\w.&-]*(?:\s+[A-ZÄÖÜ][\w.&-]*){0,3}?)\s+"
    r"(?:nach|von|ab|in|zum|zur|und|\()"
)


def airline_der_seite(url_path: str) -> str:
    """Die Airline aus den Leistungen der Reiseseite; "" wenn keine dasteht."""
    leistungen = abschnitt(url_path, "leistungen")
    if not leistungen:
        return ""
    treffer = _AIRLINE.search(leistungen)
    return treffer.group(1).strip() if treffer else ""


# Verlaengerungs- und Vorprogrammseiten kommen als Kandidat nicht in Frage.
# Das ist eine Vorauswahl, WELCHE Seite ueberhaupt geholt wird — keine
# Erwartung: die Airline selbst kommt weiter von der Seite.
_KEINE_REISE = ("Nachtraeumen-", "Vorfreuen-")


def hauptseiten(land: str) -> list[str]:
    """Kandidaten fuer "eine Reise dieses Landes", schlichte Namen zuerst.

    Ein `-ALL`-Pfad ist mal eine Reise (`Baobab-ALL`) und mal die
    Laenderuebersicht ohne Leistungen (`Etosha-ALL`, `Atlas-ALL`). Gemessen
    2026-09-23: alphabetisch sortiert standen die `-ALL`-Seiten vorn, und fuenf
    der ersten dreizehn Laender fielen aus dem Airline-Block, weil nur sie
    probiert wurden. Die Reihenfolge entscheidet nur, WELCHE Seite geholt wird;
    die Airline kommt weiter von der Seite.
    """
    seiten = [
        s for s in land_seiten(land) if not s.rsplit("/", 1)[-1].startswith(_KEINE_REISE)
    ]
    return sorted(seiten, key=lambda s: (s.endswith(("-ALL", "-NEU", "-Neu")), s))


@lru_cache(maxsize=None)
def reise_mit_airline(land: str) -> tuple[str, str]:
    """``(URL, Airline)`` der ersten Reise eines Landes, die eine nennt.

    Hoechstens drei Seiten je Land, damit der Block nicht am Abrufen haengt.
    """
    for pfad in hauptseiten(land)[:3]:
        airline = airline_der_seite(pfad)
        if airline:
            return pfad, airline
    return "", ""


# Namen, die im Reiseverlauf neben einem Ort stehen. Die Stopwoerter sind die
# Woerter, die auf JEDER Seite im selben Satz stehen — sie wuerden den Fall
# gratis gruen machen.
_ORT_STOP = {
    "Chamäleon", "Deutschland", "Frankfurt", "Hotel", "Lodge", "Camp", "Gäste",
    "Reiseleitung", "Reiseleiter", "Reiseleiterin", "Ankunft", "Abreise",
    "Flughafen", "Abend", "Morgen", "Mittag", "Nachmittag", "Anschließend",
    "Danach", "Heute", "Später", "Willkommen", "Deine", "Deiner", "Deinem",
    "Unterwegs", "Unterkunft", "Frühstück", "Abendessen", "Mittagessen",
    "Übernachtung", "Weiter", "Zunächst", "Nachtsüber", "Erlebnisreise",
}


# Bildnamen und Linkziele stehen als Markdown im Reiseverlauf
# ("![AF-NA-ST-Windhoek2 | © …](/data/pic/…)") und haben im Fliesstext nichts
# verloren: gemessen 2026-09-23 war "AF-NA-ST-Windhoek2" der erste "Ortsname",
# den die Ableitung fand. Erst den Text freilegen, dann Namen suchen.
_BILD_ODER_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")


def fliesstext(markdown: str) -> str:
    """Das Markdown ohne Bilder und Linkziele."""
    return _BILD_ODER_LINK.sub(" ", markdown)


def reise_mit_ort(land: str, ort: str) -> str:
    """Die erste Reise eines Landes, deren Reiseverlauf ``ort`` nennt.

    Abgeleitet statt eingetragen, weil ein `-ALL`-Pfad beides sein kann:
    `/Afrika/Tansania/Baobab-ALL` ist eine Reise, `/Asien/Vietnam/Ao-Dai-ALL`
    ist es nicht (gemessen 2026-09-23: kein Reiseverlauf). Wer die Seite fest
    einträgt, misst irgendwann eine Uebersichtsseite.
    """
    for pfad, verlauf in reiseverlaeufe(land):
        if ort.lower() in verlauf.lower():
            return pfad
    raise AssertionError(f"keine {land}-Reise nennt {ort} im Reiseverlauf")


def orte_am_tag(url_path: str, ort: str) -> list[str]:
    """Namen aus dem Reiseverlauf, die im selben Absatz wie ``ort`` stehen.

    Absatz und nicht Satz: der Tag beginnt mit der Ankunft und nennt die
    Sehenswuerdigkeiten erst danach. Gemessen 2026-09-23 auf der Etosha-Seite —
    "Landung, wenn in Windhoek die Sonne aufgeht. Hier bilden Gestern und Heute
    eine beispiellose Symbiose: die Christuskirche, der Tintenpalast, der
    historische Bahnhof." Satzweise gesucht faende man davon nichts.

    Der zweite Buchstabe muss klein sein: markdownify klebt den Bildnamen an
    das Wort davor ("**Windhoek**AF-NA-ST-Windhoek2"), und ein Bildname ist
    kein Ort.

    Bewusst grosszuegig (irgendeiner davon reicht): die scharfe Seite dieses
    Falls ist das darf_nicht. Hier geht es nur darum, dass die Antwort
    ueberhaupt von der Seite kommt und nicht aus dem Weltwissen des Modells.
    """
    verlauf = fliesstext(abschnitt(url_path, "reiseverlauf") or "")
    gefunden = []
    for absatz in re.split(r"\n\s*\n", verlauf):
        if ort.lower() not in absatz.lower():
            continue
        for wort in re.findall(r"\b[A-ZÄÖÜ][a-zäöüß][^\W\d]{3,}\b", absatz):
            if wort not in _ORT_STOP and wort.lower() != ort.lower():
                gefunden.append(wort)
    assert gefunden, f"keine Namen zum {ort}-Tag auf {url_path} gefunden"
    return sorted(set(gefunden))


# --- Antwort pruefen -------------------------------------------------------

# call() liefert HTML (agent.py rendert das Markdown). Blockenden werden zu
# Satzgrenzen, damit eine Aufzaehlung mit fuenf Punkten auch als fuenf Saetze
# zaehlt — sonst waere die laengste Antwort formal die kuerzeste.
_BLOCKENDE = re.compile(r"</(?:p|li|h[1-6]|div|tr|blockquote)>|<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_TRENNER = "\x00"


def klartext(html: str) -> str:
    """Die Antwort ohne Markup, Blockenden als Trenner."""
    text = _BLOCKENDE.sub(_TRENNER, html)
    text = _TAG.sub(" ", text)
    return re.sub(r"[ \t]+", " ", unescape(text)).strip()


# "z. B." und "ca." sind keine Satzenden. Die Liste ist bewusst kurz: sie deckt
# ab, was im Chamaeleon-Ton tatsaechlich vorkommt.
_ABKUERZUNG = re.compile(
    r"\b(?:z|B|ca|bzw|ggf|evtl|inkl|exkl|Nr|u|a|Mo|Di|Mi|Do|Fr|Sa|So|St|Dr|max|min|etc|usw)\.$"
)


def saetze(text: str) -> list[str]:
    """Die Saetze eines Klartexts."""
    gefunden: list[str] = []
    for block in text.split(_TRENNER):
        puffer = ""
        for teil in re.split(r"(?<=[.!?…])\s+", block.strip()):
            puffer = f"{puffer} {teil}".strip()
            if _ABKUERZUNG.search(puffer):
                continue
            if re.search(r"\w", puffer):
                gefunden.append(puffer)
            puffer = ""
        if re.search(r"\w", puffer):
            gefunden.append(puffer)
    return gefunden


# Der Prompt fordert 2-4 Saetze. Gemessen wird gegen 5: ein Satz Luft, damit der
# Querschnitts-Check die Ausreisser meldet und nicht jede Antwort.
MAX_SAETZE = 5

# Zwei Erwartungen, die in mehreren Bloecken vorkommen: "die Reise findet
# statt" und "frag den Erlebnisberater". Sie stehen hier, weil sie zu den
# Antwortregeln gehoeren und nicht zu einem einzelnen Fall.
_GARANTIE = [r"garantiert", r"findet[^.]{0,30}statt", r"jede Reise findet"]
_BERATER = ["Erlebnisberat"]

# Ein Jahrespaar ("2026 - 2027") sieht fuer die Nummernerkennung aus wie eine
# Rufnummer. Nur diese eine Form wird ausgenommen, damit eine erfundene
# Telefonnummer nicht durch eine grosszuegige Regel durchrutscht.
_JAHRESPAAR = re.compile(r"^(?:19|20)\d{2}(?:19|20)\d{2}$")

# Der Stamm aller Chamaeleon-Nummern. Was nicht darauf beginnt, stand in keiner
# Datei des Repos und auf keiner Seite — F7 war "030 - 833 93 93".
_STAMM = "347996"


def erfundene_nummern(text: str) -> set[str]:
    """Telefonnummern in der Antwort, die keine Chamaeleon-Nummer sein koennen."""
    return {
        nummer
        for nummer in telefonnummern(text)
        if not nummer.startswith(_STAMM)
        and len(nummer) >= 7
        and not _JAHRESPAAR.match(nummer)
    }


# W7 gibt dem Modell bei einem noch nicht veroeffentlichten Jahr eine
# Toolausgabe, die die verbotenen Woerter SELBST enthaelt — als Verbot (live
# gemessen an Uluru/2028, W7-Bericht 2026-09-23):
#
#     "... sind noch keine Termine veroeffentlicht. ... Sage genau das: die
#      Termine fuer diesen Zeitraum sind noch nicht veroeffentlicht — nicht
#      'ausgebucht', nicht 'keine freien Plaetze' ..."
#
# Zitiert Leon diese Belehrung brav mit, waere ein nacktes darf_nicht auf
# "ausgebucht" rot, obwohl die Antwort stimmt — und die Ausgangsmessung
# verzerrt. Geprueft wird deshalb die BEHAUPTUNG: das Wort ohne Verneinung
# davor. Beim Nachziehen bitte so lassen.
_VERNEINT = re.compile(r"(?:nicht|kein\w*)\W{0,4}$", re.IGNORECASE)


def behauptet(text: str, muster: str) -> bool:
    """True, wenn ``muster`` vorkommt, ohne dass eine Verneinung davorsteht."""
    for treffer in re.finditer(muster, text, re.IGNORECASE):
        if not _VERNEINT.search(text[max(0, treffer.start() - 26) : treffer.start()]):
            return True
    return False


def querschnitt(reply: str, max_saetze: int) -> list[str]:
    """Die Regeln, die fuer JEDE Antwort gelten (Review D7).

    Laenge, "leider" und die Woerter "FAQ"/"Wissensbasis" stehen in keinem
    einzelnen Fall, gelten aber ueberall — und genau deshalb faellt niemandem
    auf, wenn eine neue Prompt-Regel sie kippt. Hier faellt es auf.
    """
    text = klartext(reply)
    verletzt = []
    anzahl = len(saetze(text))
    if anzahl > max_saetze:
        verletzt.append(f"{anzahl} Saetze (hoechstens {max_saetze})")
    if re.search(r"\bleider\b", text, re.IGNORECASE):
        verletzt.append("sagt 'leider'")
    if re.search(r"\bFAQ|Wissensbasis", text, re.IGNORECASE):
        verletzt.append("nennt 'FAQ'/'Wissensbasis'")
    return verletzt


def _wert(x):
    """Ein Feld darf ein Callable sein: Erwartungen, die aus den Seiten kommen,
    sollen erst im Test Seiten abrufen und nicht schon beim Sammeln."""
    return x() if callable(x) else x


def _nachrichten(fall: dict) -> list[dict]:
    if "verlauf" in fall:
        return _wert(fall["verlauf"])
    return [{"role": "user", "content": _wert(fall["frage"])}]


def pruefe(fall: dict, reply: str) -> list[str]:
    """Alle Befunde eines Falls: muss, muss_eines, darf_nicht, Querschnitt."""
    befunde = []
    fehlend = [k for k in _wert(fall.get("muss", [])) if not keyword_matches(k, reply)]
    if fehlend:
        befunde.append(f"fehlt {fehlend}")
    eines = _wert(fall.get("muss_eines", []))
    if eines and not any(keyword_matches(k, reply) for k in eines):
        befunde.append(f"keines von {eines}")
    verboten = [
        k for k in _wert(fall.get("darf_nicht", [])) if keyword_matches(k, reply)
    ]
    if verboten:
        befunde.append(f"verboten {verboten}")
    klar = klartext(reply)
    behauptet_verboten = [
        k for k in _wert(fall.get("nie_behauptet", [])) if behauptet(klar, k)
    ]
    if behauptet_verboten:
        befunde.append(f"behauptet {behauptet_verboten}")
    if fall.get("muss_durchwahl") and not nennt_durchwahl(reply):
        befunde.append(f"keine Durchwahl (gefunden: {sorted(telefonnummern(reply))})")
    if fall.get("keine_nummern") and (erfunden := erfundene_nummern(reply)):
        befunde.append(f"erfundene Nummer {sorted(erfunden)}")
    befunde += querschnitt(reply, fall.get("max_saetze", MAX_SAETZE))
    return befunde


def fahre(fall: dict) -> None:
    """Ein Fall, EVAL_N mal.

    Bei EVAL_N=1 ist das Ergebnis rot oder gruen. Ab EVAL_N=2 meldet der Fall
    seine Quote und faellt nur, wenn er KEINEN Durchgang besteht: fuer die
    Messung ist "2 von 3" die Zahl, die zaehlt, und ein Abbruch beim ersten
    roten Durchgang haette sie nie erhoben.
    """
    if fall.get("braucht_index"):
        # Produktion baut den Reiseindex beim Start; erst damit steht die
        # Erlebnisberater*in der Reiseseite im Prompt. Ohne ihn misst der Fall
        # einen Zustand, den es live nie gibt (gemessen 2026-09-25: 1/5 ohne,
        # alle fuenf Antworten mit Name und Durchwahl mit Index).
        import travel_index

        travel_index.ensure_built()
    bestanden = 0
    letzte_befunde: list[str] = []
    letzte_antwort = ""
    laengen: list[int] = []
    for _ in range(EVAL_N):
        reply = call(_nachrichten(fall), _wert(fall["endpoint"]))
        laengen.append(len(saetze(klartext(reply))))
        befunde = pruefe(fall, reply)
        if befunde:
            letzte_befunde, letzte_antwort = befunde, reply
        else:
            bestanden += 1
    # Die Satzzahl gehoert in die Ausgangsmessung, auch wenn der Fall gruen ist.
    print(f"QUOTE {fall['id']} {bestanden}/{EVAL_N} saetze={laengen}")
    if EVAL_N == 1:
        assert not letzte_befunde, (
            f"{letzte_befunde}\n--- reply ---\n{letzte_antwort}"
        )
    else:
        assert bestanden, (
            f"0/{EVAL_N} — {letzte_befunde}\n--- reply ---\n{letzte_antwort}"
        )


def _params(faelle: list[dict]):
    return [pytest.param(fall, id=fall["id"]) for fall in faelle]


# === Block "anker" — die festen Wahrheiten, ohne Modell ====================
#
# Stand 2026-09-20/23. Werden sie bei einem Programmwechsel rot, ist das eine
# Information: nachziehen und das Datum erneuern, nicht die Pruefung lockern.

RUAHA = "/Afrika/Tansania/Ruaha"
CHEETAH = "/Afrika/Tansania/Cheetah"
KASBAH = "/Afrika/Marokko/Kasbah"
KASBAH_AIRLINE = "Discover Airlines"
ETOSHA = "/Afrika/Namibia/Etosha"
ETOSHA_TAGE = 19
GORILLA = "/Afrika/Uganda/Gorilla"
OUTENIQUA = "/Afrika/Suedafrika/Outeniqua-ALL"
ULURU = "/Ozeanien/Australien/Uluru"


def test_anker_sansibar_trennt_die_tansania_reisen():
    """Der Kern von Punkt 10: Sansibar im Reiseverlauf, nicht irgendwo."""
    ohne, mit = nach_merkmal("Tansania", "Sansibar")
    assert RUAHA in ohne, f"Ruaha nicht in {ohne}"
    assert CHEETAH in mit, f"Cheetah nicht in {mit}"


def test_anker_kasbah_fliegt_discover():
    """Punkt 12: die Airline steht in den Leistungen, nicht in einer FAQ."""
    assert airline_der_seite(KASBAH) == KASBAH_AIRLINE


def test_anker_etosha_dauert_19_tage():
    """F9: Etosha ist zu lang fuer 14 Tage — der Seitentitel sagt es."""
    zeilen = (abschnitt(ETOSHA, "uebersicht") or "").splitlines()
    treffer = _DAUER.search(zeilen[0] if zeilen else "")
    assert treffer, f"keine Dauer im Titel: {zeilen[:1]}"
    assert int(treffer.group(1)) == ETOSHA_TAGE, zeilen[0]


def test_anker_gorilla_nennt_ruanda_nur_als_grenze():
    """Punkt 13: "kombiniert Uganda und Ruanda" ist frei erfunden.

    Die Seite nennt Ruanda, aber nur als Grenze und als Zwischenstopp in
    Kigali — nie als Reiseziel.
    """
    assert "Ruanda" in seite(GORILLA), "Ruanda steht gar nicht mehr auf der Seite"
    verlauf = abschnitt(GORILLA, "reiseverlauf") or ""
    for satz in re.split(r"(?<=[.!?])\s+", verlauf):
        if "Ruanda" in satz:
            assert re.search(r"Grenz|Kigali", satz), (
                f"Ruanda im Reiseverlauf ohne Grenze oder Kigali: {satz!r}"
            )


def test_anker_ruanda_ist_kein_reiseland():
    """Die Grundlage des Blocks nichtangeboten — ohne Netz."""
    assert "Ruanda" in NICHT_ANGEBOTEN, f"Ruanda steht jetzt im Programm: {NICHT_ANGEBOTEN}"


# === Block "filter" — Land, Dauer und Orte pruefen (D1, F8-F10) ============

FILTER = [
    {
        "id": "filter-tansania-ohne-sansibar",
        "frage": "Ich suche eine Tansania-Reise ohne Sansibar. Welche passt?",
        "endpoint": "/Afrika/Tansania",
        **spaeter(ohne_merkmal, "Tansania", "Sansibar"),
    },
    {
        # Der zweite Zug ist der eigentliche Test: Leon hat die Seiten gelesen,
        # der Kunde widerspricht — und die Seite behaelt recht. Dieselbe Regel
        # wie bei den Terminen.
        "id": "filter-tansania-ohne-sansibar-widerspruch",
        "verlauf": lambda: [
            {"role": "user", "content": "Ich suche eine Tansania-Reise ohne Sansibar."},
            {
                "role": "assistant",
                "content": "Schau dir die "
                f"{reisename(nach_merkmal('Tansania', 'Sansibar')[0][0])}-Reise an.",
            },
            {"role": "user", "content": "Die ist doch auch mit Sansibar."},
        ],
        "endpoint": "/Afrika/Tansania",
        **spaeter(ohne_merkmal, "Tansania", "Sansibar"),
    },
    {
        # F8, woertlich aus Katharinas Mail.
        "id": "filter-suedafrika-kapstadt-gartenroute",
        "frage": "Wir wollen im Februar 2027 nach Kapstadt, die Gartenroute und "
        "die Weinroute fahren und zum Krüger mit Inlandflug. Was habt ihr da?",
        "endpoint": "/Afrika/Suedafrika",
        "muss_eines": lambda: deckt_ab(
            "Suedafrika", ("Kapstadt", "Garten[- ]?[Rr]oute|Garden Route", "Krüger")
        )["muss_eines"],
        "darf_nicht": ["nicht im Programm", "zusammenstell", "Individualreise"],
    },
    {
        # F9: 19 Tage als "perfekt fuer zweiwoechig" empfohlen.
        "id": "filter-namibia-14-tage",
        "frage": "Ich möchte nach Namibia, habe aber nur 14 Tage Zeit. "
        "Welche Reise passt?",
        "endpoint": "/Afrika/Namibia",
        **spaeter(passt_in, "Namibia", 14),
    },
    {
        # F10: Land und Dauer waren beide genannt, beide verfehlt.
        "id": "filter-namibia-teenager-14-tage",
        "verlauf": [
            {
                "role": "user",
                "content": "wir reisen mit zwei teenagern und haben 14 tage",
            },
            {"role": "assistant", "content": "Wohin soll es denn gehen?"},
            {"role": "user", "content": "namibia"},
        ],
        "endpoint": "/",
        "muss_eines": lambda: passt_in("Namibia", 14)["muss_eines"],
        "darf_nicht": lambda: passt_in("Namibia", 14)["darf_nicht"]
        + fremde_kombilaender("Namibia")
        + ["Moremi"],
    },
    # --- Schwesterfaelle: anderes Land, anderer Wortlaut ------------------
    {
        "id": "filter-peru-ohne-amazonas-schwester",
        "frage": "Gibt es eine Peru-Reise, bei der wir nicht in den Amazonas fahren?",
        "endpoint": "/Amerika/Peru",
        **spaeter(ohne_merkmal, "Peru", "Amazonas"),
    },
    {
        # 11 und nicht 10 Tage: gemessen 2026-09-24 ist die kuerzeste
        # Marokko-Reise der Sitemap Atlas mit 11 Tagen (Indigo 15, Marrakesch
        # 17). Bei 10 passte keine, die Erwartung trennte nicht und der Fall
        # war auf beiden Seiten "Fehler" statt einer Messung.
        "id": "filter-marokko-11-tage-schwester",
        "frage": "Wir haben nur 11 Tage Urlaub und würden gern nach Marokko.",
        "endpoint": "/Afrika/Marokko",
        **spaeter(passt_in, "Marokko", 11),
    },
    {
        "id": "filter-italien-ohne-rom-schwester",
        "frage": "Welche Italien-Reise kommt ohne Rom aus?",
        "endpoint": "/Europa/Italien",
        **spaeter(ohne_merkmal, "Italien", "Rom"),
    },
]


@pytest.mark.parametrize("fall", _params(FILTER))
def test_filter(fall):
    """Empfiehlt Leon nur, was der Reiseverlauf und die Dauer hergeben?"""
    fahre(fall)


# === Block "airline" — die Airline steht in den Leistungen (D2) ============

AIRLINE_LAENDER_SOLL = 20


def _airline_kandidaten(anzahl: int = 32) -> tuple[str, ...]:
    """Gleichmaessig ueber die sortierte Laenderliste verteilte Kandidaten.

    Verteilt statt alphabetisch von vorn, damit die Auswahl ueber alle
    Kontinente streut und sich niemand die passenden Laender aussucht. Die
    Liste kommt aus der Sitemap und kostet kein Netz — die Airline selbst
    liest der Test dann von der Seite.

    Warum 32 fuer 20 geforderte Laender: ein uebersprungenes Land kostet keinen
    Modellaufruf, nur einen Seitenabruf. Gemessen 2026-09-23 trugen 16 von 24
    Kandidaten eine erkennbare Airline (zwei Drittel) — 32 Kandidaten ergeben
    also rund 21 Laender und damit rund 21 Live-Aufrufe, unter der Grenze von
    ~25, ab der Gemini in die leere Antwort kippt.
    """
    laender = sorted({s.split("/")[2] for s in trip_sites if hauptseiten(s.split("/")[2])})
    if len(laender) <= anzahl:
        return tuple(laender)
    return tuple(laender[round(i * len(laender) / anzahl)] for i in range(anzahl))


AIRLINE_LAENDER = _airline_kandidaten()


@pytest.mark.parametrize("land", AIRLINE_LAENDER)
def test_airline(land):
    """Die Airline kommt von der Reiseseite, nicht aus der Unterlagen-FAQ.

    Leons Antwort im Feedback ("Rechnung über unseren Unterlagenlink") war
    woertlich die FAQ "Wo finde ich meine Flugzeiten?" — die gilt nur fuer
    bereits gebuchte Reisen.
    """
    pfad, airline = reise_mit_airline(land)
    if not airline:
        pytest.skip(f"{land}: keine Airline in den Leistungen")
    fahre(
        {
            "id": f"airline-{land}",
            # Kombi-Laender heissen in der Sitemap "Botswana-Namibia"; als Frage
            # gelesen wird daraus "Botswana und Namibia".
            "frage": "Mit welcher Airline fliegt man nach "
            f"{all_countries.get(land, land).replace('-', ' und ')}?",
            "endpoint": pfad,
            "muss": [airline],
            "darf_nicht": ["Rechnung", "Unterlagenlink", "Reiseunterlagen"],
        }
    )


def test_airline_deckt_genug_laender_ab():
    """Unter 20 Laendern bleibt der Block rot (Plan D2).

    Ein Block, der sich auf drei Laender herunterskippt, misst nichts mehr und
    saehe trotzdem gruen aus.
    """
    mit = [land for land in AIRLINE_LAENDER if reise_mit_airline(land)[1]]
    ohne = [land for land in AIRLINE_LAENDER if not reise_mit_airline(land)[1]]
    assert len(mit) >= AIRLINE_LAENDER_SOLL, (
        f"nur {len(mit)} Laender mit erkennbarer Airline "
        f"(uebersprungen: {ohne}) — Muster oder Seiten pruefen"
    )


# === Block "nichtangeboten" — klare Absage (D3) ============================

# Kandidaten aus dem Plan plus zwei, die in keinem Prompt-Beispiel stehen.
_KANDIDATEN = (
    "Ruanda", "Burundi", "Nigeria", "Afghanistan", "Nordkorea",
    "Venezuela", "Pakistan", "Ukraine", "Turkmenistan", "Paraguay",
)


def _nicht_angeboten() -> tuple[str, ...]:
    """Die Kandidaten, die wirklich in keiner Sitemap-Zeile stehen.

    Zur Laufzeit gefiltert: kaeme eines der Laender ins Programm, waere die
    "klare Absage" die falsche Erwartung.
    """
    bekannt = [s.lower() for s in list(all_countries) + list(all_countries.values())]
    return tuple(
        land for land in _KANDIDATEN if not any(land.lower() in s for s in bekannt)
    )


NICHT_ANGEBOTEN = _nicht_angeboten()

# "Wir haben dazu keine Reise" in den Formen, die im Chamaeleon-Ton vorkommen.
_ABSAGE = [
    r"(haben|bieten|gibt es)[^.]{0,40}(kein|nicht)",
    # "nicht im Programm", aber auch "nicht in unserem Reiseprogramm" und
    # "nicht unter unseren Reisezielen" (gemessen 2026-09-24, Nigeria und
    # Turkmenistan).
    r"(kein|nicht)[^.]{0,30}(Programm|Angebot|Reiseziel)",
    r"keine? Reisen? (nach|in)",
    r"(steht|stehen)[^.]{0,30}nicht",
    r"noch nicht im Programm",
    # "Afghanistan ist kein Reiseziel, das Chamäleon anbietet" — eine richtige
    # Absage, die keines der Muster oben traf (W3-Sichtpruefung und Kampagne
    # 2026-09-24, Afghanistan und Nigeria).
    r"\bkein\w*\s+(Reiseziel|Ziel|Reise)",
    # "bieten ... nicht an" in jeder Form: "bietet Chamäleon derzeit nicht an"
    # (Ruanda, 2026-09-24).
    r"(biete\w*|führ\w*|fuehr\w*)[^.]{0,40}nicht an",
]


@pytest.mark.parametrize("land", NICHT_ANGEBOTEN)
def test_nichtangeboten(land):
    """Kein Reiseland heisst: klar sagen, nie eine Kombination erfinden."""
    fahre(
        {
            "id": f"nichtangeboten-{land}",
            "frage": f"Habt ihr eine Reise nach {land}?",
            "endpoint": "/",
            "muss_eines": _ABSAGE,
            "darf_nicht": [
                rf"und\s+{land}",
                rf"nach\s+{land}\s+f(ü|ue)hrt",
                "kombinier",
            ],
        }
    )


# === Block "ungefragt" — FAQ-Inhalte nur auf Frage (D4, F11) ===============

UNGEFRAGT = [
    {
        # Punkt 5: Ramadan ist Marokko-FAQ Nr. 1 und stand ungefragt in der
        # Antwort auf "erzaehl mir was ueber Marokko".
        "id": "ungefragt-marokko-infos",
        "frage": "Gib mir ein paar Infos über Marokko und zu dieser Reise.",
        "endpoint": KASBAH,
        "darf_nicht": ["Ramadan", "Visum", "Impf"],
    },
    {
        # F11: gefragt war nach Sehenswuerdigkeiten, geantwortet wurde mit
        # Geldwechsel, Mueckenspray und der Namibia-Visumregel.
        # Der Erlebnisberater steht hier neben den Namen von der Seite, weil er
        # die zweite richtige Antwort ist: sagt der Windhoek-Tag nichts ueber
        # Sehenswuerdigkeiten, dann ist "frag den Erlebnisberater" richtig und
        # Erfinden falsch. Gemessen 2026-09-23 nennt Leon "Christuskirche, Alte
        # Feste, Handwerkermarkt" — nichts davon steht im Reiseverlauf.
        "id": "ungefragt-windhoek-sehenswuerdigkeiten",
        "frage": "Welche Sehenswürdigkeiten guckt man sich in Windhoek an?",
        "endpoint": ETOSHA,
        "muss_eines": lambda: orte_am_tag(ETOSHA, "Windhoek") + _BERATER,
        "darf_nicht": ["Visum", "Mückenspray", "Geldwechsel", r"\bGeld\b", "e-Visa"],
    },
    # --- Schwesterfaelle ---------------------------------------------------
    {
        "id": "ungefragt-vietnam-infos-schwester",
        "frage": "Erzähl mir etwas über Vietnam und diese Reise.",
        "endpoint": lambda: reise_mit_ort("Vietnam", "Hanoi"),
        "darf_nicht": ["Visum", "Impf", "Malaria", r"\bGeld\b"],
    },
    {
        "id": "ungefragt-hanoi-sehenswuerdigkeiten-schwester",
        "frage": "Was schaut man sich in Hanoi an?",
        "endpoint": lambda: reise_mit_ort("Vietnam", "Hanoi"),
        "muss_eines": lambda: orte_am_tag(reise_mit_ort("Vietnam", "Hanoi"), "Hanoi")
        + _BERATER,
        "darf_nicht": ["Visum", "Impf", "Mückenspray", r"\bGeld\b"],
    },
]


@pytest.mark.parametrize("fall", _params(UNGEFRAGT))
def test_ungefragt(fall):
    """Beantwortet Leon die Frage — oder leert er die Laender-FAQ aus?"""
    fahre(fall)


# === Block "fachwissen" — Garantie, Reservierung, Berater (D5, F2, F4, F5) ==


FACHWISSEN = [
    {
        # Punkt 2: "ab 2 Personen" steht in keiner Datei und auf keiner Seite.
        "id": "fachwissen-durchfuehrung-ab-wievielen",
        "frage": "Findet diese Reise auch statt, wenn nur wenige gebucht haben?",
        "endpoint": RUAHA,
        "muss_eines": _GARANTIE,
        "darf_nicht": ["ab 2 Personen", "Mindestteilnehmer", "abgesagt"],
    },
    {
        # F4, woertlich: ein Wort als ganze Frage.
        "id": "fachwissen-durchfuehrungsgarantie",
        "frage": "durchführungsgarantie",
        "endpoint": "/",
        "muss_eines": _GARANTIE,
        "darf_nicht": ["keine generelle", r"keine[^.]{0,20}Durchf(ü|ue)hrungsgarantie"],
    },
    {
        # Punkt 6: Leon hat die Reservierung bestritten, die auf der Seite steht.
        "id": "fachwissen-reservierung-7-tage",
        "frage": "Wie lange gilt eine Reservierung?",
        "endpoint": RUAHA,
        "muss": ["7 Tage"],
        "darf_nicht": ["nicht möglich", r"gibt es[^.]{0,20}nicht"],
    },
    {
        # Punkt 4: online reservieren, Uebertragung ueber den Erlebnisberater.
        "id": "fachwissen-buchung-ueber-reisebuero",
        "frage": "Ich möchte diese Reise über mein Reisebüro buchen. Geht das?",
        "endpoint": RUAHA,
        "muss": _BERATER,
        "darf_nicht": ["nicht möglich", r"gibt es[^.]{0,20}nicht"],
    },
    {
        # Punkt 7
        "id": "fachwissen-privatreise",
        "frage": "Können wir diese Reise als Privatreise buchen?",
        "endpoint": RUAHA,
        "muss": _BERATER,
        # Owner 2026-09-25: Privatreise und Just4You haben nichts miteinander
        # zu tun — Leon warf beides zusammen.
        "darf_nicht": ["Just4You"],
    },
    {
        # Punkt 11, korrigiert (Owner 2026-09-25): Just4You ist ein Angebot fuer
        # Mitarbeiter*innen von Reisebueros, keine Privatreise. Frueher verlangte
        # der Fall nur den Erlebnisberater und war mit der falschen Gleichsetzung
        # gruen.
        "id": "fachwissen-just4you",
        "frage": "Kann ich diese Reise als Just4You für uns allein buchen?",
        "endpoint": RUAHA,
        "muss": ["Reisebür", "Mitarbeiter"],
    },
    {
        # Wortgetreu aus dem Chat, den der Owner am 2026-09-25 zeigte: Leon
        # antwortete "was im Rahmen von Just4You möglich ist".
        "id": "fachwissen-private-rundreise-4-personen-schwester",
        "frage": "Ist es möglich ihreAngebotenurfür maximal 4 Personenals "
        "privateRundreise zu buchen",
        "endpoint": RUAHA,
        # "Ja, das klaert die Erlebnisberater*in …" klingt nach Zusage (Owner,
        # 2026-09-25); und auf einer Reiseseite ist die Berater*in bekannt —
        # ihre Durchwahl statt "Kontaktdaten findest du unter #termine". Die
        # Durchwahl ersetzt das Wort "Erlebnisberat": "klärt Jona Reimann unter
        # +49 30 347996-221" ist die bessere Antwort, nicht die schlechtere.
        "darf_nicht": ["Just4You", r"^\s*(<p>)?\s*Ja\b"],
        "muss_durchwahl": True,
        "braucht_index": True,
    },
    {
        # Punkt 9
        "id": "fachwissen-hatari-zusatztage",
        "frage": "Können wir Zusatztage in der Hatari Lodge dazubuchen?",
        "endpoint": CHEETAH,
        "muss": _BERATER,
        "darf_nicht": ["nicht möglich"],
    },
    {
        # F2: Termin nicht gefunden UND Garantie nicht genannt.
        "id": "fachwissen-outeniqua-30-01-2027",
        "frage": "findet die Tour Outeniqua mit Startdatum 30.1.27 definitiv statt?",
        "endpoint": OUTENIQUA,
        "muss_eines": _GARANTIE,
        "nie_behauptet": ["keine Termine", "liegen mir keine", "nicht abrufbar"],
    },
    {
        # F5: "keine freien Termine" klingt ausgebucht; 2028 ist ab Anfang 2027
        # online. "keine Termine" steht hier BEWUSST nicht im darf_nicht: die
        # richtige Antwort lautet "es sind noch keine Termine veroeffentlicht".
        "id": "fachwissen-termine-januar-2028",
        "frage": "Termine Januar 28",
        "endpoint": ULURU,
        "muss_eines": [
            r"noch nicht ver(ö|oe)ffentlicht",
            "noch nicht online",
            "noch nicht buchbar",
            "später online",
            "ab Anfang 2027",
        ],
        "nie_behauptet": ["ausgebucht", r"keine freien"],
    },
    # --- Schwesterfaelle ---------------------------------------------------
    {
        "id": "fachwissen-absage-bei-wenigen-schwester",
        "frage": "Wird eine Reise abgesagt, wenn sich zu wenige anmelden?",
        "endpoint": "/",
        "muss_eines": _GARANTIE,
        "darf_nicht": ["Mindestteilnehmer", "ab 2 Personen"],
    },
    {
        "id": "fachwissen-vormerken-schwester",
        "frage": "Wie lange könnt ihr mir einen Platz unverbindlich freihalten?",
        "endpoint": CHEETAH,
        "muss": ["7 Tage"],
    },
    {
        "id": "fachwissen-termine-2029-schwester",
        "frage": "Habt ihr schon Termine für 2029?",
        "endpoint": KASBAH,
        "muss_eines": [
            r"noch nicht ver(ö|oe)ffentlicht",
            "noch nicht online",
            "noch nicht buchbar",
            "später online",
        ],
        "nie_behauptet": ["ausgebucht", r"keine freien"],
    },
]


@pytest.mark.parametrize("fall", _params(FACHWISSEN))
def test_fachwissen(fall):
    """Weiss Leon, was Chamaeleon tatsaechlich anbietet?"""
    fahre(fall)


# === Block "erfinden" — keine Nummer aus dem Nichts (F1, F7) ===============


# Geprueft wird eine DURCHWAHL, nicht eine bestimmte Nummer von einer
# bestimmten Seite. Grund, gemessen 2026-09-23: die Laenderseite
# /Amerika/Costa-Rica traegt gar keine Erlebnisberater*in (12.891 Zeichen,
# erfolgreich abgerufen, kein einziges Vorkommen von "Erlebnisberater") — der
# Plan nimmt unter F1 an, sie tue es. `berater_tool_base` antwortet auf so eine
# Seite folgerichtig mit der Zentrale, und genau die Zentrale war im Feedback
# die beanstandete Antwort. Dieser Fall bleibt deshalb rot, bis jemand
# entscheidet, woher der Berater fuer ein LAND kommen soll; `nennt_durchwahl`
# (aus test_agentur_faq) trennt dabei sauber: Stammnummer plus Nebenstelle,
# aber nicht der Empfang (…0) und nicht der Vertrieb (…290).


ERFINDEN = [
    {
        # F1, woertlich mit den Tippfehlern aus dem Chat.
        "id": "erfinden-ansprechpartner-costarica",
        "frage": "wie erreiche ich den Ansprchpartener für die Reise nach Costrica?",
        "endpoint": "/",
        "muss_durchwahl": True,
        "keine_nummern": True,
    },
    {
        # F7: die Antwort nannte "030 - 833 93 93" — steht in keiner Datei.
        "id": "erfinden-passname",
        "frage": "In meinem Pass ist mein Vorname anders geschrieben als in der "
        "Buchung. An wen wende ich mich?",
        "endpoint": "/",
        "muss_eines": _BERATER,
        "keine_nummern": True,
    },
    # --- Schwesterfaelle ---------------------------------------------------
    {
        "id": "erfinden-ansprechpartner-namibia-schwester",
        "frage": "an wen wende ich mich bei fragen zur reise nach Nambia?",
        "endpoint": "/",
        "muss_durchwahl": True,
        "keine_nummern": True,
    },
    {
        "id": "erfinden-namensschreibweise-schwester",
        "frage": "Mein Nachname hat einen Bindestrich, im Ticket fehlt er. "
        "Wen frage ich?",
        "endpoint": "/",
        "muss_eines": _BERATER,
        "keine_nummern": True,
    },
]


@pytest.mark.parametrize("fall", _params(ERFINDEN))
def test_erfinden(fall):
    """Nummern nur aus Seite, Tool oder Prompt — nie erfunden."""
    fahre(fall)


# === Block "flug" — nicht das Gepaeck-PDF verlinken (F6a) ==================

# Die Unterlagen-FAQ gilt nur fuer bereits gebuchte Reisen. Im oeffentlichen
# Chat ist sie die falsche Antwort — gemessen 2026-09-23 antwortet Leon auf
# "Non-Stop?" woertlich "schau bitte in deinen Reiseunterlagen nach dem
# Flugplan", ohne die Reiseseite zu oeffnen.
_FLUG_VERBOTEN = [
    L("tpl=fluginformationen"),
    L("report.loadpdf"),
    "Gepäckbestimmung",
    "Reiseunterlagen",
]

FLUG = [
    {
        # F6: auf "Non-Stop?" kam der Link "Fluginformationen" — das PDF
        # enthaelt Gepaeckbestimmungen, keine Flugverbindung.
        "id": "flug-non-stop",
        "frage": "Handelt es sich um Non-Stop-Flüge?",
        "endpoint": KASBAH,
        "muss_eines": lambda: [airline_der_seite(KASBAH)] + _BERATER,
        "darf_nicht": _FLUG_VERBOTEN,
    },
    {
        "id": "flug-umstieg-schwester",
        "frage": "Fliegt man da direkt hin oder mit Umstieg?",
        "endpoint": GORILLA,
        "muss_eines": lambda: [airline_der_seite(GORILLA)] + _BERATER,
        "darf_nicht": _FLUG_VERBOTEN,
    },
]


@pytest.mark.parametrize("fall", _params(FLUG))
def test_flug(fall):
    """Flugangaben aus den Leistungen — oder sonst der Erlebnisberater."""
    fahre(fall)
