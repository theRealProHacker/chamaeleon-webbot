"""Alle allgemeinen FAQs als Frage-Antwort-Zeilen in der Supabase-Tabelle ``faq``.

Zwei Quellen, eine Tabelle: ``website`` (die Akkordeons auf /Infos, per Sync)
und ``intern`` (vormals faqs/allgemein.md, einmal importiert, danach im
Supabase Table Editor gepflegt). DDL: sql/faq.sql. Ueberschneiden sich beide,
blendet der Owner eine Zeile per ``ausgeblendet`` aus; der Sync aendert das nie.

    python faq_sync.py import [--replace]   allgemein.md -> intern-Zeilen
    python faq_sync.py sync [--force]       /Infos -> website-Zeilen
    python faq_sync.py export               aktive Zeilen -> faqs/snapshot.json

Laufzeit: agent_base rendert beim Import den committeten Snapshot (Grundstand,
auch fuer Tests und Evals); der Serverstart und der naechtliche Sync laden per
``load()`` aus Supabase. Scheitert das, bleibt der aktuelle Stand im Speicher.
"""

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
    teile, kategorie = [], None
    for r in sorted(rows, key=lambda r: r["position"]):
        if r["kategorie"] != kategorie:
            kategorie = r["kategorie"]
            teile.append(f"## {kategorie}")
        teile.append(f"**F: {r['frage']}**\nA: {r['antwort']}")
    return "\n\n".join(teile)


def render_beide(rows: list[dict]) -> tuple[str, str]:
    """(website + intern, nur intern) fuer Endkunden- und Agentur-Prompt."""
    # Laender-FAQs (quelle land) gehoeren nie in den allgemeinen Block.
    rows = [r for r in rows if r["quelle"] in ("website", "intern")]
    return render(rows), render([r for r in rows if r["quelle"] == "intern"])


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
            .eq("aktiv", True).eq("ausgeblendet", False).order("position").execute().data
        )
        if not any(r["quelle"] == "intern" for r in rows):
            raise ValueError("keine aktiven intern-Zeilen")
    except Exception as e:
        status["load"] = {"ok": False, "grund": f"{e}; alter Stand bleibt", "zeit": _jetzt()}
        print(f"[faq-sync] load failed, keeping current FAQs: {e}")
        return False
    agent_base.allgemeine_faqs, agent_base.allgemeine_faqs_agentur = render_beide(rows)
    status["load"] = {"ok": True, "zeilen": len(rows), "zeit": _jetzt()}
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
    if ergebnis["ok"]:
        load()
    return ergebnis


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


def export() -> int:
    """Aktive Zeilen -> faqs/snapshot.json (Grundstand beim Import von agent_base)."""
    rows = (
        _supabase().table(TABLE).select(",".join(FELDER))
        .eq("aktiv", True).eq("ausgeblendet", False).order("position").execute().data
    )
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return len(rows)


if __name__ == "__main__":
    befehl = sys.argv[1] if len(sys.argv) > 1 else ""
    if befehl == "import":
        print(f"{import_md('--replace' in sys.argv)} intern-Zeilen importiert")
    elif befehl == "sync":
        print(sync(force="--force" in sys.argv))
    elif befehl == "export":
        print(f"{export()} Zeilen nach {SNAPSHOT}")
    else:
        sys.exit(__doc__)
