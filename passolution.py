"""Einreisebestimmungen aus Passolution (Design docs/designs/passolution-einreise.md).

Modulfunktionen plus Caches und eine Tool-Factory, keine Klassen. Ein Abruf
(``GET /content/all/text``) liefert alle Kombinationen aus Zielland und
Staatsangehörigkeit auf einmal, je Kombination ein Record mit den Abschnitten
entry, visa, transit_visa, health (gemessen 2026-10-22, Fixtures unter
tests/fixtures/passolution/).

Woher Länder und Staatsangehörigkeiten kommen:
- Website: Land aus der Frage, sonst aus der geöffneten Reiseseite;
  Staatsangehörigkeit nennt der Gast (sonst NATIONALITAET_FEHLT).
- Kunden-Modus: TripURL der Buchung (``/travel-details/{tid}``), sonst
  Länder aus ``/get/reise`` und Staatsangehörigkeiten aus dem Einreise-PDF
  (Owner 2026-10-22). ``/get/adresse`` bleibt draußen (O5).

An Passolution gehen nur ISO2-Codes und die Sprache. Ins Log nur Statuscode
und Länderzahl, nie Inhalt, IP oder Staatsangehörigkeit.
"""

import os
import re
from urllib.parse import urlparse

import requests
from cachetools.func import ttl_cache
from langchain_core.tools import tool

import agent_base
import kundendaten
import rate_limit
import travel_index
import unterlagen

API_BASE = "https://api.passolution.eu/api/v2"
TIMEOUT = 8
SPRACHE = "de"
# Gemessen 2026-10-22: rund 7.600 Zeichen je Land und Staatsangehörigkeit
# (ohne final_provisions), Botswana-Namibia-Simbabwe für DACH nach dem
# Zusammenlegen gleicher Abschnitte rund 25.000. Der Deckel liegt darüber und
# greift nur bei langen Mehrländerreisen; gekürzt wird je Land, damit das
# letzte Land nicht ganz wegfällt.
MAX_ZEICHEN = 30_000
# Passolution: höchstens 50 Kombinationen Land × Staatsangehörigkeit je Abruf.
MAX_KOMBINATIONEN = 50

# Reihenfolge der Antwort: das Gefragteste zuerst. corona ist leer, und
# final_provisions ist für jedes Land derselbe Textbaustein (1.659 Zeichen).
_ABSCHNITTE = (
    ("visa", "Visum"),
    ("entry", "Einreise und Reisedokumente"),
    ("transit_visa", "Transit"),
    ("health", "Gesundheit und Impfungen"),
)

_ISO2 = re.compile(r"[A-Z]{2}")
_TID = re.compile(r"[?&]tid=([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b")
_LANDNAME = re.compile(r"Reiseziel\s+(.+?)\s*/")
_UNGUELTIG = re.compile(r"Invalid Country Codes:\s*([A-Za-z, ]+)")

NATIONALITAET_FEHLT = (
    "Für die Einreisebestimmungen brauche ich die Staatsangehörigkeit der "
    "Reisenden. Frag den Gast in einem Satz danach und ruf einreise_tool dann "
    "mit nationalitaeten erneut auf (ISO2, etwa DE, AT, CH)."
)
LAND_FEHLT = (
    "Für welches Reiseland? Auf dieser Seite ist kein Reiseziel hinterlegt. "
    "Nenne das Land, sobald du es kennst, als ISO2-Code in laender (etwa CO "
    "für Kolumbien); sonst frag den Gast danach."
)
LIMIT_SATZ = " Für weitere Länder hilft dir deine Erlebnisberater*in."

TOOL_BESCHREIBUNG = """
Aktuelle Einreisebestimmungen von Passolution: Visum, Reisepass und
Reisedokumente, Einreiseformulare, Minderjährige, Transit und Impfpflicht,
je Reiseland und Staatsangehörigkeit, mit Stand-Datum je Abschnitt.

Nutze es bei JEDER Frage zu Einreise, Visum, Reisepass, Einreiseformular,
Transit, Minderjährigen oder Impfpflicht. Es ist die einzige Quelle dafür,
nicht visum.de, nicht die FAQs und nicht dein Gedächtnis.

Parameter (beide optional, ISO2-Codes durch Komma getrennt):
- laender: die Reiseländer, zum Beispiel CO für Kolumbien oder BW,NA,ZW. Leer
  lassen, wenn der Gast auf einer Reiseseite ist oder im Kunden-Modus nach
  seiner gebuchten Reise fragt: dann nimmt das Tool die Länder der Reise selbst,
  auch bei Mehrländerreisen. Ein Transitland, das der Gast nennt, gibst du mit.
- nationalitaeten: die Staatsangehörigkeiten der Reisenden, zum Beispiel DE
  oder DE,CH. Leer lassen, solange der Gast keine genannt hat; im Kunden-Modus
  kommen sie aus der Buchung.

Beispiel: Fragt ein Gast auf der Kolumbien-Reiseseite „Brauche ich als
Deutscher ein Visum?“, rufst du einreise_tool mit nationalitaeten DE auf und
lässt laender leer.
"""


def _codes(wert: object) -> tuple[list[str], list[str]]:
    """``"co, BW;na"`` → (gültige ISO2 in Reihenfolge, verworfene Rohwerte)."""
    if isinstance(wert, (list, tuple)):
        wert = ",".join(str(w) for w in wert)
    gueltig, verworfen = [], []
    for token in re.split(r"[,;/\s]+", str(wert or "").strip()):
        code = token.strip().upper()
        if not code:
            continue
        if _ISO2.fullmatch(code):
            if code not in gueltig:
                gueltig.append(code)
        else:
            verworfen.append(token.strip()[:20])
    return gueltig, verworfen


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('PASSOLUTION_API_KEY', '')}"}


@ttl_cache(maxsize=512, ttl=86400)
def _abrufen(laender: tuple, nats: tuple) -> list:
    """Die Records für alle Kombinationen — gecacht 1 Tag. WIRFT bei Fehler.

    Muster ``kundendaten._buchung_roh``: ttl_cache merkt sich keine Exceptions,
    ein 503 oder Timeout landet also nie im Cache. Der Cache schützt das
    Monatskontingent (429), nicht nur die Antwortzeit (Owner 2026-10-21).
    Aufrufer übergeben sortierte Tupel, damit die Reihenfolge den Schlüssel
    nicht verdoppelt.
    """
    antwort = requests.get(
        f"{API_BASE}/content/all/text",
        params={"lang": SPRACHE, "countries": ",".join(laender), "nat": ",".join(nats)},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    antwort.raise_for_status()
    records = antwort.json().get("records")
    if not isinstance(records, list) or not records:
        # 204 oder leerer Umschlag: kein Inhalt ist hier ein Ausfall.
        raise ValueError("keine records")
    return records


def _stand(updated_at: object) -> str:
    """``2026-09-01T12:48:08.000000Z`` → ``01.09.2026``; sonst ""."""
    treffer = re.match(r"(\d{4})-(\d\d)-(\d\d)", str(updated_at or ""))
    return f"{treffer.group(3)}.{treffer.group(2)}.{treffer.group(1)}" if treffer else ""


def _landname(record: dict) -> str:
    treffer = _LANDNAME.search(str(record.get("title") or ""))
    return treffer.group(1) if treffer else ""


def _formatieren(records: list, laender: list[str], nats: list[str]) -> str:
    """Abschnitte je Land; gleiche Abschnitte mehrerer Staatsangehörigkeiten einmal.

    Für DACH sind Visum, Transit und Gesundheit meist wörtlich gleich, nur die
    Reisedokumente unterscheiden sich (gemessen Botswana: drei Fassungen). Jeder
    Abschnitt trägt sein eigenes Stand-Datum. Gekürzt wird je Land auf
    ``MAX_ZEICHEN / Länderzahl``, sichtbar.
    """
    je_land: dict[str, list[dict]] = {}
    for record in records:
        if isinstance(record, dict):
            je_land.setdefault(str(record.get("destination") or "").upper(), []).append(record)
    reihenfolge = [l for l in laender if l in je_land] + [l for l in je_land if l not in laender]
    deckel = MAX_ZEICHEN // max(len(reihenfolge), 1)

    teile = []
    for land in reihenfolge:
        gruppe = sorted(
            je_land[land],
            key=lambda r: nats.index(r.get("nationality")) if r.get("nationality") in nats else 99,
        )
        name = _landname(gruppe[0])
        zeilen = [f"## {name} ({land})" if name else f"## {land}"]
        for schluessel, titel in _ABSCHNITTE:
            fassungen: dict[str, list] = {}  # Inhalt → [Staatsangehörigkeiten, Stand]
            for record in gruppe:
                abschnitt = record.get(schluessel) or {}
                inhalt = str(abschnitt.get("content") or "").replace("\r\n", "\n").strip()
                if not inhalt:
                    continue
                eintrag = fassungen.setdefault(inhalt, [[], _stand(abschnitt.get("updated_at"))])
                eintrag[0].append(str(record.get("nationality") or "?"))
            for inhalt, (staaten, stand) in fassungen.items():
                kopf = f"### {titel} – für Staatsangehörige {', '.join(staaten)}"
                zeilen += ["", kopf + (f" (Stand {stand})" if stand else ""), inhalt]
        text = "\n".join(zeilen)
        if len(text) > deckel:
            text = text[:deckel] + "\n\n(Gekürzt — Passolution hat zu diesem Land noch mehr.)"
        teile.append(text)
    return "\n\n".join(teile)


def einreise_text(laender: list[str], nats: list[str], *, schluessel: str, fallback: str) -> str:
    """Prüfung → Rate-Limit → ``_abrufen`` → ``_formatieren``. Wirft nie.

    ``schluessel`` aus ``rate_limit.passolution_schluessel`` ("" = ohne Limit).
    Gezählt wird nur ein erfolgreicher Abruf, der nicht aus dem Cache kam; ein
    Cache-Treffer geht auch bei erschöpftem Kontingent durch, er kostet nichts.
    Jeder Fehler ergibt ``fallback``, außer unbekannten Ländercodes: die meldet
    das Tool dem Modell, damit es mit richtigen Codes neu aufruft.
    """
    # Höchstens 50 Kombinationen je Abruf; Länder gehen vor (es gibt selten
    # mehr als drei Staatsangehörigkeiten, aber Rundreisen mit vielen Ländern).
    if not laender or not nats:
        return fallback
    nats = nats[:MAX_KOMBINATIONEN]
    laender = laender[: max(MAX_KOMBINATIONEN // len(nats), 1)]
    schluessel_cache = _abrufen.cache_key(tuple(sorted(laender)), tuple(sorted(nats)))
    aus_cache = schluessel_cache in _abrufen.cache
    if not aus_cache and not rate_limit.passolution_erlaubt(schluessel):
        return fallback + LIMIT_SATZ
    try:
        records = _abrufen(tuple(sorted(laender)), tuple(sorted(nats)))
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        print(f"[passolution] status={status or type(e).__name__} laender={len(laender)}")
        if status == 400:
            try:
                ungueltig = _UNGUELTIG.search(e.response.json()["error"]["message"])
            except Exception:
                ungueltig = None
            if ungueltig:
                return (
                    f"Unbekannte Ländercodes: {ungueltig.group(1).strip()}. Ruf "
                    "einreise_tool mit gültigen ISO2-Codes erneut auf (etwa GB "
                    "für Großbritannien)."
                )
        return fallback
    if not aus_cache:
        rate_limit.passolution_gezaehlt(schluessel)
    return _formatieren(records, laender, nats)


# --- Woher die Länder kommen -------------------------------------------------

# Reiseseiten tragen ihre Zielländer im HTML (gemessen 2026-10-21):
# data-passolution-covid-info='{"laendercodes": "BW, NA, ZW", "laendercode": "BNZ"}'.
# Nur der Plural: der Singular kann ein Pseudo-Code wie "BNZ" sein.
_LAENDERCODES = re.compile(r'"laendercodes":\s*"([A-Za-z ,]+)"')


@ttl_cache(maxsize=512, ttl=86400)
def laender_der_seite(url_path: str) -> list[str]:
    """ISO2-Zielländer der Reiseseite ``url_path``; ``[]`` ohne Feld (Länderseite).

    WIRFT bei Ausfall (RequestException, ValueError für fremde Hosts) — so
    landen Fehler nie im Cache, und ein kurzer 503 der Website versteckt die
    Länder nicht einen Tag lang. Eigener kleiner Cache statt ``seite_markdown``:
    dort wird nur Markdown gecacht, und das Feld steht nur im rohen HTML.
    """
    treffer = _LAENDERCODES.search(agent_base.get_chamaeleon_website_html(url_path))
    if not treffer:
        return []
    laender = []
    for code in treffer.group(1).split(","):
        code = code.strip().upper()
        if re.fullmatch(r"[A-Z]{2}", code) and code not in laender:
            laender.append(code)
    return laender


def _seitenpfad(endpoint: str) -> str:
    """Pfad einer Website-Reiseseite (``/Kontinent/Land/Reise``); sonst "".

    Bewusst ohne den Reiseindex (``agent_base._ist_reise_url``): der ist vor
    dem ersten Bau und in Evals leer, und dann fehlte die Seite still.
    Länderseiten (zwei Teile) tragen kein ``laendercodes``.
    """
    pfad = urlparse(endpoint or "").path if "://" in (endpoint or "") else (endpoint or "").split("?")[0]
    teile = [t for t in pfad.split("/") if t]
    if len(teile) < 3 or teile[0].lower() == "meinchamaeleon":
        return ""
    return "/" + "/".join(teile)


@ttl_cache(maxsize=256, ttl=86400)
def _reise_laender(reise_code: str) -> list[str]:
    """``land2.iso2Liste`` aus ``/get/reise``. WIRFT bei Ausfall (nie gecacht)."""
    reise = travel_index._tourone_get("/get/reise", {"reisecode": reise_code}, timeout=TIMEOUT)
    return agent_base._reiseinfo_ziel_isos(reise) if isinstance(reise, dict) else []


@ttl_cache(maxsize=256, ttl=86400)
def _tripurl_daten(tid: str) -> dict | None:
    """``data`` von ``GET /travel-details/{tid}``; ``None`` nach Reiseende (400).

    Nach ``end_date`` ist die tid weg: 400 „No travel detail exists“ (gemessen
    2026-10-22). Das darf in den Cache, abgelaufen bleibt abgelaufen. Jeder
    andere Fehler WIRFT und wird nie gecacht.
    """
    antwort = requests.get(f"{API_BASE}/travel-details/{tid}", headers=_headers(), timeout=TIMEOUT)
    if antwort.status_code == 400:
        return None
    antwort.raise_for_status()
    daten = antwort.json().get("data")
    return daten if isinstance(daten, dict) else None


def _liste(werte: object) -> list[str]:
    return _codes(werte if isinstance(werte, list) else "")[0]


def ziel_und_nats_der_buchung(buchung: dict) -> tuple[list[str], list[str], str]:
    """``(laender, nats, tripurl)`` einer (geprüften, nicht stornierten) Buchung.

    Reihenfolge (Owner 2026-10-22): TripURL der Buchung, sonst Länder aus
    ``/get/reise`` und Staatsangehörigkeiten aus dem Einreise-PDF. Die TripURL
    nennt auch Zwischenstopps (gemessen: NZ plus SG), die gehen mit. Fällt eine
    Quelle aus, bleibt die nächste; ``tripurl`` ist der Link für den Gast, ""
    nach Reiseende.
    """
    tripurl = unterlagen.aktuelle_tripurl(buchung, kundendaten.heute_berlin())
    laender: list[str] = []
    nats: list[str] = []
    tid = _TID.search(tripurl or "")
    if tid:
        try:
            daten = _tripurl_daten(tid.group(1)) or {}
        except Exception as e:
            print(f"[passolution] tripurl status={getattr(getattr(e, 'response', None), 'status_code', None) or type(e).__name__}")
            daten = {}
        laender = _liste(daten.get("destinations"))
        nats = _liste(daten.get("nationalities"))
    if not laender and buchung.get("reiseCode"):
        try:
            laender = _reise_laender(str(buchung["reiseCode"]))
        except Exception as e:
            print(f"[passolution] reise lookup failed: {type(e).__name__}")
    if not nats:
        try:
            nats = unterlagen.nationalitaeten(buchung.get("unterlagen"))
        except Exception as e:
            print(f"[passolution] einreise-pdf failed: {type(e).__name__}")
    return laender, nats, tripurl


# --- Das Tool ----------------------------------------------------------------


def _fallback_website(seitenpfad: str) -> str:
    # Der Block heißt auf der Seite „Einreise- und Gesundheitsbestimmungen für
    # dein Reiseziel“ (Textbaustein PASS-Einleitung, gemessen 2026-10-22); einen
    # stabilen Anker gibt es nicht, also Pfad plus Überschrift.
    if seitenpfad:
        return (
            "Die Einreisebestimmungen kann ich gerade nicht abrufen. Sie stehen "
            f"auf der Reiseseite {seitenpfad} unter „Einreise- und "
            "Gesundheitsbestimmungen für dein Reiseziel“ (Staatsangehörigkeit "
            "wählen). Bei Fragen hilft dir die Erlebnisberater*in der Reise."
        )
    return (
        "Die Einreisebestimmungen kann ich gerade nicht abrufen. Jede Reiseseite "
        "zeigt sie unter „Einreise- und Gesundheitsbestimmungen für dein "
        "Reiseziel“ (Staatsangehörigkeit wählen); sonst hilft dir deine "
        "Erlebnisberater*in."
    )


def _fallback_kunde(reise_vorgang: str, tripurl: str) -> str:
    teile = ["Die aktuellen Einreisebestimmungen kann ich gerade nicht abrufen."]
    if tripurl:
        teile.append(f"Aktuelle Einreise-, Visa- und Impfbestimmungen deiner Reise: {tripurl}")
    teile.append(
        "Die Einreisebestimmungen zum Zeitpunkt der Reiseanmeldung (mit Stand-Datum) "
        "liegen in deinen Reiseunterlagen: https://www.chamaeleon-reisen.de/"
        f"MeinChamaeleon/Reise?VRRVORGANG={reise_vorgang}#unterlagen "
        '(reiseinfo_tool, abschnitt "dokument:einreisebestimmungen").'
    )
    return "\n".join(teile)


def _visa_dokumente(buchung: dict) -> list[str]:
    """Visum-Ausfüllhilfe und Visa-Dokumente der Buchung als Linkzeilen.

    Direkt im Ergebnis: ohne sie nannte Gemini die Ausfüllhilfe zur Namibia-
    Frage nicht, weil es buchungen_tool dafür nicht mehr aufrief (Eval
    2026-10-22).
    """
    zeilen = []
    for slug, name in (("visum-ausfuellhilfen", "Visum-Ausfüllhilfe"), ("visa-dokumente", "Visa-Dokumente")):
        eintrag = unterlagen.dokumente_nach_slug(buchung.get("unterlagen")).get(slug)
        link = unterlagen._sicherer_link(eintrag.get("link")) if eintrag else ""
        if link:
            # Als nackte URL mit „genau diesen Link“: als Markdown-Link
            # verlinkte Gemini den Namen 2 von 5 Mal auf #unterlagen (Eval
            # 2026-10-22).
            zeilen.append(
                f"{name} dieser Buchung: {link} — nenne sie, wenn ein Visum nötig "
                "ist, und verlinke genau diese URL."
            )
    return zeilen


def _weitere_reisen(kunden_id: str, reise_vorgang: str) -> bool:
    """Hat der Kunde außer ``reise_vorgang`` noch eine kommende, nicht stornierte Reise?"""
    try:
        heute = kundendaten.heute_berlin()
        for b in kundendaten.select(kundendaten._buchungen_roh(kunden_id) or [], "kommende", 0, heute):
            v = str(b.get("vorgang") or "")
            if v != reise_vorgang and not kundendaten.ist_storniert(kundendaten.buchungsstatus(v)):
                return True
    except Exception as e:
        print(f"[passolution] buchungen lookup failed: {type(e).__name__}")
    return False


def _gebuchte_reise(kunden_id: str, reise_vorgang: str) -> dict | None:
    """Die Buchung ``reise_vorgang``, nur wenn sie dem Kunden gehört und lebt.

    Die Nummer kann aus der URL stammen (``agent.reise_fuer_links`` prüft sie
    dort nicht): ungeprüft verriete sie Länder und Staatsangehörigkeiten einer
    fremden Buchung. Prüfung wie ``agent._dokumente_der_reise``.
    """
    if not kunden_id or not agent_base._VRRVORGANG_SAFE.match(reise_vorgang or ""):
        return None
    try:
        if reise_vorgang not in (kundendaten.vorgangsnummern(kunden_id) or []):
            return None
        buchung = kundendaten._buchung_roh(reise_vorgang)
    except Exception as e:
        print(f"[passolution] buchung lookup failed: {type(e).__name__}")
        return None
    if not isinstance(buchung, dict) or kundendaten.ist_storniert(buchung.get("status")):
        return None
    return buchung


def einreise_antwort(
    laender: str,
    nationalitaeten: str,
    *,
    endpoint: str,
    kunden_id: str = "",
    agentur_id: str = "",
    client_ip: str = "",
    reise_vorgang: str = "",
    reise_label: str = "",
) -> str:
    """Was ``einreise_tool`` dem Modell zurückgibt. Wirft nie."""
    land_codes, verworfen = _codes(laender)
    nat_codes, verworfen_nat = _codes(nationalitaeten)
    hinweise = []
    if verworfen or verworfen_nat:
        hinweise.append(
            f"Kein ISO2-Code, verworfen: {', '.join(verworfen + verworfen_nat)}."
        )

    # Kunden-Modus: Länder und Staatsangehörigkeiten aus der Buchung, soweit
    # das Modell keine nennt. Agentur-Modus: keine Auflösung (O6).
    buchung = _gebuchte_reise(kunden_id, reise_vorgang) if not agentur_id else None
    tripurl = ""
    if buchung is not None and (not land_codes or not nat_codes):
        b_laender, b_nats, tripurl = ziel_und_nats_der_buchung(buchung)
        land_codes = land_codes or b_laender
        nat_codes = nat_codes or b_nats
    elif buchung is not None:
        tripurl = unterlagen.aktuelle_tripurl(buchung, kundendaten.heute_berlin())

    seitenpfad = "" if kunden_id or agentur_id else _seitenpfad(endpoint)
    if seitenpfad:
        # Auch wenn das Modell Länder nennt: es liest sie aus der URL, und
        # /Afrika/Botswana-Namibia/Caprivi führt auch durch Simbabwe (Eval
        # 2026-10-22: laender BW,NA, Simbabwe fehlte). Sind seine Länder ein
        # Teil der Reise, gilt die ganze Reise; ein anderes Land bleibt allein.
        try:
            seite = laender_der_seite(seitenpfad)
        except Exception as e:
            print(f"[passolution] seite lookup failed: {type(e).__name__}")
            seite = []
        if seite and set(land_codes) <= set(seite):
            land_codes = seite
    if not land_codes:
        return " ".join(hinweise + [LAND_FEHLT])
    if not nat_codes:
        return " ".join(hinweise + [NATIONALITAET_FEHLT])

    fallback = (
        _fallback_kunde(reise_vorgang, tripurl) if buchung is not None else _fallback_website(seitenpfad)
    )
    text = einreise_text(
        land_codes,
        nat_codes,
        schluessel=rate_limit.passolution_schluessel(client_ip, kunden_id, agentur_id),
        fallback=fallback,
    )
    if text.startswith(fallback) or text.startswith("Unbekannte Ländercodes"):
        return "\n".join(hinweise + [text])

    # Die Hinweise stehen im Ergebnis und nicht nur im Prompt: im ersten
    # Eval-Lauf (2026-10-22) fehlte das Stand-Datum und bei Caprivi Simbabwe.
    kopf = [
        "Einreisebestimmungen laut Passolution. Nenne in der Antwort Passolution "
        "als Quelle und das Stand-Datum des Abschnitts, aus dem du zitierst "
        "(etwa „laut Passolution, Stand 01.09.2026“)."
    ]
    if len(land_codes) > 1:
        kopf.append(
            f"Die Reise führt durch {len(land_codes)} Länder ({', '.join(land_codes)}). "
            "Nenne jedes davon mit Namen und sag kurz, was dort gilt, auch für "
            "ein Land, in dem wenig zu beachten ist."
        )
    if buchung is not None:
        kopf.append(
            f"Für die gebuchte Reise {reise_label or reise_vorgang}"
            + "; Länder und Staatsangehörigkeiten stammen aus der Buchung."
        )
        if not agent_base._vrrvorgang_from_url(endpoint) and _weitere_reisen(kunden_id, reise_vorgang):
            kopf.append(
                "Der Gast hat noch weitere kommende Reisen. Nenne, für welche "
                "Reise diese Angaben gelten; ist unklar, welche er meint, frag kurz nach."
            )
    if tripurl:
        kopf.append(f"Persönlicher, laufend aktualisierter Link zu den Bestimmungen der Reise: {tripurl}")
    if buchung is not None:
        kopf += _visa_dokumente(buchung)
    return "\n".join(hinweise + kopf + ["", text])


def make_einreise_tool(
    endpoint: str,
    kunden_id: str = "",
    agentur_id: str = "",
    client_ip: str = "",
    reise_vorgang: str = "",
    reise_label: str = "",
):
    """Das ``einreise_tool`` dieses Requests, gebunden per Closure.

    IP, Kundennummer und Buchung kommen vom Server, nie vom Modell; die IP geht
    nirgends hin außer in den Limiter-Schlüssel.
    """

    # Bewusst lose getypt wie reiseinfo_tool: ein Wert, den die Validierung
    # ablehnt, käme als Tool-Fehler beim Gast an. Normalisiert wird oben.
    @tool(description=TOOL_BESCHREIBUNG)
    def einreise_tool(laender: str | None = "", nationalitaeten: str | None = "") -> str:
        """LangChain tool wrapper for the Passolution entry rules."""
        return einreise_antwort(
            laender or "",
            nationalitaeten or "",
            endpoint=endpoint,
            kunden_id=kunden_id,
            agentur_id=agentur_id,
            client_ip=client_ip,
            reise_vorgang=reise_vorgang,
            reise_label=reise_label,
        )

    return einreise_tool
