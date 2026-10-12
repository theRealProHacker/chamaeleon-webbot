"""Alle allgemeinen FAQs als Frage-Antwort-Zeilen in der Supabase-Tabelle ``faq``.

Drei Quellen, eine Tabelle: ``website`` (die Akkordeons auf /Infos, per Sync),
``intern`` (vormals faqs/allgemein.md) und ``land`` (vormals faqs/FAQ_*.csv,
``kategorie`` = genau ein Land); intern und land einmal importiert, danach im
Supabase Table Editor gepflegt. DDL: sql/faq.sql. Ueberschneiden sich beide,
blendet der Owner eine Zeile per ``ausgeblendet`` aus; der Sync aendert das nie.

    python faq_sync.py import [--replace]   allgemein.md -> intern-Zeilen
    python faq_sync.py import-laender [--replace]   FAQ_*.csv -> land-Zeilen (Rest ausgeblendet)
    python faq_sync.py sync [--force]       /Infos -> website-Zeilen
    python faq_sync.py export               aktive Zeilen -> faqs/snapshot.json

--replace bricht ohne --pflege-verwerfen immer ab: es verwirft die Pflege aus
dem Editor unter /admin/faq (Funktionen unten ab editor_zeilen).

Laufzeit: agent_base rendert beim Import den committeten Snapshot (Grundstand,
auch fuer Tests und Evals); der Serverstart und der naechtliche Sync laden per
``load()`` aus Supabase. Scheitert das, bleibt der aktuelle Stand im Speicher.
"""

import csv
import datetime
import json
import re
import sys
import threading

import markdownify
import requests
from bs4 import BeautifulSoup

from sitemap_sync import _HEADERS, BASE_URL

TABLE = "faq"
SNAPSHOT = "faqs/snapshot.json"
FELDER = ("quelle", "extern_id", "kategorie", "frage", "antwort", "position")
# intern zuerst (0..), Website dahinter: der bisherige Prompt-Text bleibt vorne unverändert.
WEBSITE_POSITION = 1000
LAND_POSITION = 2000
KONTINENTE = ("Afrika", "Amerika", "Asien_und_Ozeanien", "Europa")
# Alle Reiseländer, auch die ohne eine einzige Frage: Sie bleiben im
# country_faq_tool, in der Länder-Erkennung und im Vokabular von
# chat_quality.country_vocabulary(). Ein Land kommt per land-Zeile dazu,
# verschwindet aber nur hier. Reihenfolge wie früher aus den CSVs.
LAENDER = (
    "Ägypten", "Botswana", "Kap Verde", "Kenia", "Madagaskar", "Marokko",
    "Mosambik", "Namibia", "Simbabwe", "Südafrika", "Tansania", "Uganda",
    "Mauritius", "Argentinien", "Chile", "Belize", "Bolivien", "Brasilien",
    "Peru", "Costa Rica", "Ecuador", "Guatemala", "Kanada", "Kolumbien", "Kuba",
    "Mexiko", "Nicaragua", "Panama", "USA", "Australien", "Armenien",
    "Aserbaidschan", "Bhutan", "China", "Georgien", "Indien", "Japan",
    "Jordanien", "Kambodscha", "Kirgisistan", "Laos", "Malaysia", "Mongolei",
    "Nepal", "Neuseeland", "Oman", "Saudi-Arabien", "Sri Lanka", "Thailand",
    "Usbekistan", "Vietnam", "Indonesien", "Albanien", "Azoren", "Estland",
    "Finnland", "Frankreich", "Griechenland", "Großbritannien", "Irland",
    "Island", "Italien", "Kroatien", "Lettland", "Litauen", "Nordmazedonien",
    "Montenegro", "Norwegen", "Portugal", "Rumänien", "Schottland", "Schweden",
    "Spanien",
)
MIN_FRAGEN = 20

status: dict = {}  # letzter load/sync, fuer /admin
_lock = threading.Lock()


def _supabase():
    from db_logging import supabase  # erst bei Bedarf: agent_base importiert dieses Modul

    return supabase


def parse_infos(html: bytes | str) -> list[dict]:
    """/Infos-HTML -> website-Zeilen in Seitenreihenfolge."""
    # Seite sagt ISO-8859-1, schreibt das Euro-Zeichen aber als cp1252-Byte 0x80.
    soup = BeautifulSoup(html, "html.parser", from_encoding="cp1252")
    items = soup.select("li[tos-template=text_akkordeon]")
    ids = [li.get("tos-txtnr") for li in items]
    # Schutz gegen Fehler-/Relaunch-Seiten (Soft-404 liefert HTTP 200).
    if len(items) < MIN_FRAGEN:
        raise ValueError(f"nur {len(items)} FAQs auf /Infos, nichts geschrieben")
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("fehlende oder doppelte tos-txtnr auf /Infos")
    rows = []
    for i, li in enumerate(items):
        h3 = li.find_previous("h3")
        rows.append(
            {
                "quelle": "website",
                "extern_id": ids[i],
                "kategorie": h3.get_text(" ", strip=True) if h3 else "Allgemein",
                "frage": li.select_one("h4.uk-accordion-title").get_text(" ", strip=True),
                "antwort": markdownify.markdownify(
                    str(li.select_one(".uk-accordion-content")), strip=["img"]
                ).strip(),
                "position": WEBSITE_POSITION + i,
            }
        )
    return rows


def parse_md(text: str) -> list[dict]:
    """allgemein.md (## Kategorie, **F: …**, A: …) -> intern-Zeilen."""
    rows, kategorie = [], None
    for block in re.split(r"\n\n(?=\*\*F: |## )", text.strip()):
        if block.startswith("## "):
            kategorie = block[3:].strip()
            continue
        m = re.match(r"\*\*F: (.+?)\*\*\n(.+)", block, re.S)
        if not (m and kategorie):
            raise ValueError(f"unerwarteter Block in allgemein.md: {block[:60]!r}")
        rows.append(
            {
                "quelle": "intern",
                "extern_id": None,
                "kategorie": kategorie,
                "frage": m[1],
                "antwort": m[2].removeprefix("A: "),
                "position": len(rows),
            }
        )
    return rows


def render(rows: list[dict]) -> str:
    """Zeilen -> Prompt-Block im Format von allgemein.md."""
    # Je (quelle, kategorie) ein Block, in der Reihenfolge des ersten Auftretens:
    # eine neue Frage (hoechste Position + 1) landet so in ihrer Kategorie, und
    # eine interne Kategorie mit Website-Namen rutscht nicht in den Website-Block.
    gruppen: dict[tuple, list[str]] = {}
    for r in sorted(rows, key=lambda r: r["position"]):
        gruppen.setdefault((r["quelle"], r["kategorie"]), [f"## {r['kategorie']}"]).append(
            f"**F: {r['frage']}**\nA: {r['antwort']}"
        )
    return "\n\n".join(teil for gruppe in gruppen.values() for teil in gruppe)


def render_beide(rows: list[dict]) -> tuple[str, str]:
    """(website + intern, nur intern) fuer Endkunden- und Agentur-Prompt."""
    # Laender-FAQs (quelle land) gehoeren nie in den allgemeinen Block.
    rows = [r for r in rows if r["quelle"] in ("website", "intern")]
    return render(rows), render([r for r in rows if r["quelle"] == "intern"])


def fragen_antworten(rows: list[dict]) -> dict[str, str]:
    """{Frage: Antwort} der allgemeinen FAQs (website + intern)."""
    return {r["frage"]: r["antwort"] for r in rows if r["quelle"] in ("website", "intern")}


def laender(rows: list[dict]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """land-Zeilen -> (Land -> Markdown fuer Tool und Prompt, Land -> {Frage: Antwort})."""
    faqs = {land: f"# {land}" for land in LAENDER}
    daten: dict[str, dict[str, str]] = {land: {} for land in LAENDER}
    for r in sorted(rows, key=lambda r: r["position"]):
        if r["quelle"] != "land":
            continue
        land = r["kategorie"]
        faqs[land] = faqs.get(land, f"# {land}") + f"\n\n## {r['frage']}\n\n{r['antwort']}"
        daten.setdefault(land, {})[r["frage"]] = r["antwort"]
    return faqs, daten


# Bewusst ausgeblendete Zeile: Visum-Fragen sind seit Sept. 2026 absichtlich
# nicht in den Laender-FAQs (visum.de-Tool und allgemeine FAQs decken sie ab).
VISUM_HINWEIS = "Nicht einblenden: Visum-Fragen sind seit Sept. 2026 bewusst nicht in den Länder-FAQs (visum.de-Tool)."


def parse_laender_csv() -> tuple[dict[str, dict[str, str]], list[tuple[str, str, str]]]:
    """faqs/FAQ_*.csv -> ({Land: {Frage: Antwort}}, [(Land, Frage, Antwort)] ausgeblendet).

    Eine Kopfzeile wie "Chile/Bolivien/Peru" gilt fuer mehrere Laender und
    ergaenzt deren Fragen. (Bis 2026-10 setzte sie sie zurueck und versteckte so
    Chile 7, Peru 3 und Albanien 1 Frage.) Zweite Liste: alles andere mit Text,
    das keine vollstaendige Frage-Antwort-Zeile ist (Frage ohne Antwort, Notiz),
    damit es in Supabase liegt, ohne dass der Bot es sieht.
    """
    daten: dict[str, dict[str, str]] = {}
    versteckt: list[tuple[str, str, str]] = []
    for kontinent in KONTINENTE:
        with open(f"faqs/FAQ_{kontinent}.csv", "r", encoding="utf-8") as f:
            aktuelle: list[str] = [kontinent]
            for row in csv.reader(f, delimiter=";"):
                row = [cell for _cell in row if (cell := _cell.strip())]
                if not row:
                    continue
                if row[0].isdigit() and len(row) == 3:
                    for land in aktuelle:
                        daten[land][row[1]] = row[2]
                elif row[0].isdigit():
                    for land in aktuelle:
                        for text in row[1:]:  # Nummer ohne Text: leere Vorlage
                            versteckt.append(
                                (land, text, VISUM_HINWEIS if "visum" in text.lower() else "")
                            )
                elif row[0].startswith("Reisenspezifische Fragen") or row[0] == "Nr.":
                    continue  # Zwischenueberschrift der Vorlage
                elif len(row) == 1 and len(row[0]) < 50:
                    aktuelle = " ".join(
                        part for part in row[0].split(" ") if "(" not in part
                    ).split("/")
                    for land in aktuelle:
                        daten.setdefault(land, {})
                else:
                    for land in aktuelle:
                        text = " ".join(row)
                        versteckt.append(
                            (land, text, VISUM_HINWEIS if "visum" in text.lower() else "")
                        )
    return daten, versteckt


def land_zeilen(daten: dict[str, dict[str, str]]) -> list[dict]:
    """{Land: {Frage: Antwort}} -> land-Zeilen, eine je Land und Frage."""
    paare = [(land, f, a) for land, fragen in daten.items() for f, a in fragen.items()]
    return [
        {
            "quelle": "land",
            "extern_id": None,
            "kategorie": land,
            "frage": f,
            "antwort": a,
            "position": LAND_POSITION + i,
        }
        for i, (land, f, a) in enumerate(paare)
    ]


def _mit_a(md: str) -> str:
    # ponytail: zwei Katalog-Antworten in allgemein.md haben kein "A: "; render setzt es immer.
    return re.sub(r"(\*\*F: .+?\*\*\n)(?!A: )", r"\1A: ", md.strip())


def _jetzt() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def load() -> bool:
    """Aktive Zeilen aus Supabase in den Prompt. Fehler: alter Stand bleibt."""
    import agent_base

    try:
        rows = (
            _supabase().table(TABLE).select(",".join(FELDER))
            .eq("aktiv", True).eq("ausgeblendet", False).order("position").order("id").execute().data
        )
        if not any(r["quelle"] == "intern" for r in rows):
            raise ValueError("keine aktiven intern-Zeilen")
    except Exception as e:
        status["load"] = {"ok": False, "grund": f"{e}; alter Stand bleibt", "zeit": _jetzt()}
        print(f"[faq-sync] load failed, keeping current FAQs: {e}")
        return False
    agent_base.allgemeine_faqs, agent_base.allgemeine_faqs_agentur = render_beide(rows)
    agent_base.general_faq_data = fragen_antworten(rows)
    land = sum(r["quelle"] == "land" for r in rows)
    status["load"] = {"ok": True, "zeilen": len(rows), "land": land, "zeit": _jetzt()}
    # Ohne land-Zeilen (z.B. vor dem Import) bleiben die Laender-FAQs, wie sie sind;
    # website und intern kommen trotzdem an.
    if land:
        agent_base.laender_faqs, agent_base.laender_faq_data = laender(rows)
        # Tippfehler im Table Editor ("Namibia ", "namibia") wuerden still ein neues
        # Land anlegen, das die Erkennung nie trifft: sichtbar machen.
        if neu := sorted({r["kategorie"] for r in rows if r["quelle"] == "land"} - set(LAENDER)):
            status["load"]["unbekannte_laender"] = neu
    else:
        status["load"]["grund"] = "keine land-Zeilen; Laender-FAQs bleiben, wie sie sind"
    return True


def sync(force: bool = False) -> dict:
    """/Infos -> website-Zeilen (Upsert auf extern_id), dann load().

    Schreibt nichts, wenn die Seite kaputt aussieht oder (ohne force) weniger
    als 80 % der zuletzt aktiven Website-Fragen liefert.
    """
    with _lock:
        try:
            r = requests.get(BASE_URL + "/Infos", headers=_HEADERS, timeout=20)
            r.raise_for_status()
            neu = [n | {"aktiv": True} for n in parse_infos(r.content)]
            db = _supabase().table(TABLE)
            alt = {
                a["extern_id"]: a
                for a in db.select(",".join(FELDER) + ",aktiv").eq("quelle", "website").execute().data
            }
            vorher = sum(a["aktiv"] for a in alt.values())
            if not force and len(neu) < 0.8 * vorher:
                raise ValueError(f"nur {len(neu)} von {vorher} Fragen, nichts geschrieben (force erzwingt)")
            # Nur echte Aenderungen schreiben, sonst rauscht faq_history.
            schreiben = [n for n in neu if alt.get(n["extern_id"]) != n]
            if schreiben:
                db.upsert(schreiben, on_conflict="extern_id").execute()
            ids = {n["extern_id"] for n in neu}
            weg = [e for e, a in alt.items() if a["aktiv"] and e not in ids]
            if weg:
                db.update({"aktiv": False}).in_("extern_id", weg).execute()
            neu_zahl = sum(n["extern_id"] not in alt for n in neu)
            ergebnis = {
                "ok": True,
                "fragen": len(neu),
                "neu": neu_zahl,
                "geaendert": len(schreiben) - neu_zahl,
                "deaktiviert": len(weg),
            }
        except Exception as e:
            ergebnis = {"ok": False, "grund": str(e)}
            print(f"[faq-sync] sync failed: {e}")
        ergebnis["zeit"] = _jetzt()
        status["sync"] = ergebnis
        # Innerhalb der Sperre: Schreiben und Laden haben genau eine Reihenfolge.
        if ergebnis["ok"]:
            load()
    return ergebnis


# ── Editor unter /admin/faq (Plan: docs/designs/admin-faq-editor.md) ──
# Jede schreibende Funktion nimmt _lock, liest die Zeilen frisch, prueft,
# schreibt und laedt. Ergebnis ist ein dict fuer die Route: {ok, zeile, geladen,
# historie_id} oder {error, feld, status} mit einem Satz fuer die Seite.

HISTORY = "faq_history"
EDITOR_FELDER = "id,quelle,kategorie,frage,antwort,position,aktiv,ausgeblendet,updated_at"
INHALT = ("kategorie", "frage", "antwort", "ausgeblendet")
MAX_FRAGE = 300
MAX_ANTWORT = 4000  # jede intern-Antwort steht in jedem Chat-Prompt


def _fehler(satz: str, feld: str | None = None, status: int = 400) -> dict:
    return {"error": satz, "feld": feld, "status": status}


def _sichtbar(r: dict) -> bool:
    """Leon kennt die Zeile (dieselbe Bedingung wie in load())."""
    return r["aktiv"] and not r["ausgeblendet"]


def _alle() -> list[dict]:
    return _supabase().table(TABLE).select(EDITOR_FELDER).order("position").order("id").execute().data


def _naechste_position(alle: list[dict], quelle: str) -> int:
    # Hoechste Position der Quelle + 1, geloeschte und ausgeblendete mitgezaehlt:
    # sonst teilt sich eine wiederhergestellte Zeile die Position mit einer neuen.
    start = LAND_POSITION if quelle == "land" else 0
    return max((r["position"] for r in alle if r["quelle"] == quelle), default=start - 1) + 1


def _neu(d: dict, alt: dict) -> dict:
    """alt mit den Inhaltsfeldern aus d; Text getrimmt."""
    neu = dict(alt)
    for feld in INHALT:
        if feld in d:
            neu[feld] = d[feld].strip() if isinstance(d[feld], str) else d[feld]
    return neu


def _pruefen(neu: dict, alt: dict | None, alle: list[dict]) -> dict | None:
    """Fehler-dict mit deutschem Satz oder None. alt ist None beim Anlegen."""
    if neu["quelle"] not in ("intern", "land"):
        return _fehler("Hier lassen sich nur interne Fragen und Länder-Fragen bearbeiten.")
    if not isinstance(neu["ausgeblendet"], bool):
        return _fehler("Ausblenden muss ja oder nein sein.", "ausgeblendet")
    land = neu["quelle"] == "land"
    for feld, name in (("kategorie", "ein Land" if land else "eine Kategorie"), ("frage", "eine Frage")):
        wert = neu[feld]
        if not isinstance(wert, str) or not wert:
            return _fehler(f"Bitte {name} angeben.", feld)
        # Ein Zeilenumbruch zerbraeche "**F: …**" und "## …" im Prompt.
        if "\n" in wert or "\r" in wert:
            return _fehler("Bitte ohne Zeilenumbruch schreiben.", feld)
    if len(neu["frage"]) > MAX_FRAGE:
        return _fehler(f"Die Frage ist zu lang (höchstens {MAX_FRAGE} Zeichen).", "frage")
    if not isinstance(neu["antwort"], str):
        return _fehler("Bitte eine Antwort schreiben.", "antwort")
    if len(neu["antwort"]) > MAX_ANTWORT:
        return _fehler(f"Die Antwort ist zu lang (höchstens {MAX_ANTWORT} Zeichen).", "antwort")
    # Leer nur, solange die Zeile ausgeblendet ist und bleibt (importierte Notizen).
    leer_ok = neu["ausgeblendet"] and (alt is None or alt["ausgeblendet"])
    if not neu["antwort"] and not leer_ok:
        return _fehler("Bitte eine Antwort schreiben. Ohne Antwort bleibt die Frage ausgeblendet.", "antwort")
    # Vorhandene Zeilen mit anderem Wert (Tippfehler aus dem Table Editor) bleiben bearbeitbar.
    if land and (alt is None or neu["kategorie"] != alt["kategorie"]) and neu["kategorie"] not in LAENDER:
        return _fehler("Dieses Land gibt es nicht. Bitte eines aus der Liste wählen.", "kategorie")
    andere = [r for r in alle if _sichtbar(r) and r["id"] != neu.get("id")]
    if _sichtbar(neu):
        # Schluesselraum von general_faq_data bzw. laender_faq_data: die zweite
        # Zeile mit derselben Frage wuerde die erste still ersetzen.
        raum = (
            [r for r in andere if r["quelle"] == "land" and r["kategorie"] == neu["kategorie"]]
            if land
            else [r for r in andere if r["quelle"] in ("intern", "website")]
        )
        if any(r["frage"] == neu["frage"] for r in raum):
            return _fehler("Diese Frage gibt es schon. Bitte die vorhandene Frage ändern.", "frage")
    elif alt and alt["quelle"] == "intern" and _sichtbar(alt) and not any(r["quelle"] == "intern" for r in andere):
        # Ohne sichtbare intern-Zeile scheitert load() und Leon bliebe unbemerkt alt.
        return _fehler("Das ist die letzte sichtbare allgemeine Frage. Leon braucht mindestens eine.")
    return None


def _fertig(zeile: dict, historie_id: int | None) -> dict:
    return {"ok": True, "zeile": zeile, "geladen": load(), "historie_id": historie_id}


def _schreiben(id: int, neu_aus, updated_at: str | None = None, geloescht_ok: bool = False) -> dict:
    """Gemeinsamer Weg aller Updates. neu_aus(alt, alle) -> Zielzustand oder Fehler-dict."""
    with _lock:
        alle = _alle()
        alt = next((r for r in alle if r["id"] == id), None)
        if not alt:
            return _fehler("Diese Frage gibt es nicht mehr.", status=404)
        if not alt["aktiv"] and not geloescht_ok:
            # Sonst verschoebe jede Aenderung das "Geloescht am" (updated_at).
            return _fehler("Diese Frage ist gelöscht. Bitte erst wiederherstellen.")
        neu = neu_aus(alt, alle)
        if fehler := neu if "error" in neu else _pruefen(neu, alt, alle):
            return fehler
        patch = {k: neu[k] for k in (*INHALT, "aktiv", "position") if neu[k] != alt[k]}
        if not patch:
            return {"ok": True, "zeile": alt, "geladen": True, "historie_id": None}
        abfrage = _supabase().table(TABLE).update(patch).eq("id", id)
        if updated_at is not None:
            abfrage = abfrage.eq("updated_at", updated_at)
        daten = abfrage.execute().data
        if not daten:
            return _fehler("Diese Frage wurde inzwischen geändert.", status=409)
        # Der Eintrag, den der Trigger fuer genau dieses Update angelegt hat.
        historie = (
            _supabase().table(HISTORY).select("id").eq("faq_id", id)
            .order("id", desc=True).limit(1).execute().data
        )
        return _fertig(daten[0], historie[0]["id"] if historie else None)


def _stand_fehlt(updated_at) -> dict | None:
    if not isinstance(updated_at, str) or not updated_at:
        return _fehler("Bitte die Seite neu laden und noch einmal speichern.")
    return None


def editor_zeilen() -> dict:
    """Alles, was die Seite zeigt: intern und land ganz, website nur, was Leon kennt."""
    zeilen = [r for r in _alle() if r["quelle"] != "website" or _sichtbar(r)]
    return {"zeilen": zeilen, "laender": LAENDER}


def anlegen(d: dict) -> dict:
    with _lock:
        alle = _alle()
        leer = {"quelle": d.get("quelle"), "kategorie": "", "frage": "", "antwort": "", "ausgeblendet": False, "aktiv": True}
        neu = _neu(d, leer)
        if fehler := _pruefen(neu, None, alle):
            return fehler
        neu |= {"extern_id": None, "position": _naechste_position(alle, neu["quelle"])}
        return _fertig(_supabase().table(TABLE).insert(neu).execute().data[0], None)


def aendern(id: int, d: dict, updated_at: str) -> dict:
    """Inhalt aendern, auch Ausblenden. quelle aendert sich nie."""

    def neu_aus(alt, alle):
        if d.get("quelle", alt["quelle"]) != alt["quelle"]:
            return _fehler("Eine Frage kann nicht zwischen Allgemein und Länder wechseln.")
        neu = _neu(d, alt)
        if neu["kategorie"] != alt["kategorie"]:
            neu["position"] = _naechste_position(alle, alt["quelle"])  # ans Ende der Zielkategorie
        return neu

    return _stand_fehlt(updated_at) or _schreiben(id, neu_aus, updated_at)


def loeschen(id: int) -> dict:
    """Loeschen = aktiv false: umkehrbar, die frueheren Fassungen bleiben an der Frage."""
    return _schreiben(id, lambda alt, alle: alt | {"aktiv": False}, geloescht_ok=True)


def wiederherstellen(id: int) -> dict:
    return _schreiben(id, lambda alt, alle: alt | {"aktiv": True}, geloescht_ok=True)


def fassung_zurueck(id: int, historie_id: int, updated_at: str) -> dict:
    """Inhalt einer frueheren Fassung zurueckholen; nie aktiv, quelle oder id."""

    def neu_aus(alt, alle):
        eintrag = _supabase().table(HISTORY).select("faq_id,alt").eq("id", historie_id).execute().data
        if not eintrag or eintrag[0]["faq_id"] != id:
            return _fehler("Diese frühere Fassung gehört nicht zu dieser Frage.", status=404)
        frueher = eintrag[0]["alt"]
        neu = alt | {feld: frueher[feld] for feld in INHALT}
        if neu["kategorie"] != alt["kategorie"]:
            neu["position"] = frueher["position"]  # der alte Platz ist frei: neue bekommen Maximum + 1
        return neu

    return _stand_fehlt(updated_at) or _schreiben(id, neu_aus, updated_at)


def verlauf(id: int) -> dict:
    """Fruehere Fassungen einer Frage, neueste zuerst."""
    zeile = _supabase().table(TABLE).select(EDITOR_FELDER).eq("id", id).execute().data
    if not zeile:
        return _fehler("Diese Frage gibt es nicht mehr.", status=404)
    eintraege = (
        _supabase().table(HISTORY).select("id,alt,geaendert_am").eq("faq_id", id)
        .order("id", desc=True).execute().data
    )
    fassungen, danach = [], zeile[0]
    for e in eintraege:
        # Loeschen, Wiederherstellen und Umsortieren aendern den Inhalt nicht:
        # solche Eintraege waeren inhaltsgleiche "Fassungen".
        if any(e["alt"][feld] != danach[feld] for feld in INHALT):
            fassungen.append({"id": e["id"], "geaendert_am": e["geaendert_am"]} | {f: e["alt"][f] for f in INHALT})
        danach = e["alt"]
    return {"ok": True, "fassungen": fassungen}


def laden() -> dict:
    """load() unter der Sperre, fuer "Jetzt laden" nach einem gescheiterten Laden."""
    with _lock:
        return {"ok": True, "geladen": load(), "stand": status.get("load")}


PFLEGE_VERWERFEN = (
    "Abbruch: --replace löscht alle Zeilen dieser Quelle und legt sie mit neuen ids an.\n"
    "Alles, was unter /admin/faq gepflegt wurde, geht verloren, und die früheren\n"
    "Fassungen hängen danach an keiner Frage mehr. Wenn das gewollt ist:\n"
    "zusätzlich --pflege-verwerfen angeben."
)


def replace_gesperrt(argv: list[str]) -> bool:
    return "--replace" in argv and "--pflege-verwerfen" not in argv


def import_md(replace: bool = False) -> int:
    """Einmalig: faqs/allgemein.md -> intern-Zeilen, mit Rundlauf-Pruefung."""
    with open("faqs/allgemein.md", encoding="utf-8") as f:
        md = f.read()
    rows = parse_md(md)
    if render(rows) != _mit_a(md):
        raise SystemExit("Rundlauf weicht ab: render(parse(allgemein.md)) != allgemein.md")
    db = _supabase().table(TABLE)
    if db.select("id").eq("quelle", "intern").limit(1).execute().data:
        if not replace:
            raise SystemExit("intern-Zeilen gibt es schon; --replace ersetzt sie")
        db.delete().eq("quelle", "intern").execute()  # alte Zeilen landen in faq_history
    db.insert(rows).execute()
    return len(rows)


def import_laender(replace: bool = False) -> int:
    """Einmalig: faqs/FAQ_*.csv -> land-Zeilen, alles mit Text.

    Vollstaendige Paare aktiv, der Rest (Fragen ohne Antwort, Notizen) mit
    ausgeblendet=true: liegt in Supabase, der Bot sieht ihn nicht.
    """
    daten, versteckt = parse_laender_csv()
    rows = land_zeilen(daten)
    if set(daten) != set(LAENDER) or laender(rows)[1] != daten:
        raise SystemExit("Rundlauf weicht ab: laender(land_zeilen(csv)) != csv oder LAENDER")
    rest = [
        {
            "quelle": "land",
            "extern_id": None,
            "kategorie": land,
            "frage": frage,
            "antwort": antwort,
            "position": LAND_POSITION + len(rows) + i,
            "ausgeblendet": True,
        }
        for i, (land, frage, antwort) in enumerate(versteckt)
    ]
    db = _supabase().table(TABLE)
    if db.select("id").eq("quelle", "land").limit(1).execute().data:
        if not replace:
            raise SystemExit("land-Zeilen gibt es schon; --replace ersetzt sie")
        db.delete().eq("quelle", "land").execute()  # alte Zeilen landen in faq_history
    db.insert([r | {"ausgeblendet": False} for r in rows] + rest).execute()
    # Gegenprobe gegen das, was load() wirklich liest, und gegen die ausgeblendeten.
    zurueck = (
        db.select(",".join(FELDER)).eq("quelle", "land")
        .eq("aktiv", True).eq("ausgeblendet", False).order("position").execute().data
    )
    aus = db.select("id").eq("quelle", "land").eq("ausgeblendet", True).execute().data
    if laender(zurueck)[1] != daten or len(aus) != len(rest):
        raise SystemExit(
            "land-Zeilen in Supabase weichen vom CSV ab und sind schon aktiv: "
            "sofort mit --replace wiederholen, vorher kein Push 2"
        )
    for r in rest:
        print(f"[ausgeblendet] {r['kategorie']}: {r['frage']}" + (f"  ({r['antwort']})" if r["antwort"] else ""))
    return len(rows) + len(rest)


def export() -> int:
    """Aktive Zeilen -> faqs/snapshot.json (Grundstand beim Import von agent_base)."""
    rows = (
        _supabase().table(TABLE).select(",".join(FELDER))
        .eq("aktiv", True).eq("ausgeblendet", False).order("position").order("id").execute().data
    )
    # Ohne land-Zeilen hiesse der Snapshot: 73 Laender ohne eine Frage.
    if not any(r["quelle"] == "land" for r in rows):
        raise SystemExit("keine land-Zeilen in Supabase; erst import-laender, Snapshot unveraendert")
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return len(rows)


if __name__ == "__main__":
    befehl = sys.argv[1] if len(sys.argv) > 1 else ""
    if replace_gesperrt(sys.argv):
        sys.exit(PFLEGE_VERWERFEN)
    if befehl == "import":
        print(f"{import_md('--replace' in sys.argv)} intern-Zeilen importiert")
    elif befehl == "import-laender":
        print(f"{import_laender('--replace' in sys.argv)} land-Zeilen importiert")
    elif befehl == "sync":
        print(sync(force="--force" in sys.argv))
    elif befehl == "export":
        print(f"{export()} Zeilen nach {SNAPSHOT}")
    else:
        sys.exit(__doc__)
