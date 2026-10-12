"""Supabase-Attrappe fuer den FAQ-Editor, im Speicher und mit Filtern.

Bildet nach, was sql/faq.sql tut: ids aus der Identity, der Trigger schreibt
bei jedem UPDATE die alte Zeile nach faq_history und setzt updated_at. Fuer
Tests, die den ganzen Weg Route -> faq_sync gehen, und fuer die lokale Seite:

    python tests/faq_attrappe.py        http://dev:dev@127.0.0.1:8766/admin/faq

Die echte Tabelle wird dabei nie angefasst. Was die Attrappe nicht belegt:
dass PostgREST den Filter auf updated_at genauso trifft.
"""

import datetime
import itertools
import json

_uhr = itertools.count()


def _jetzt() -> str:
    # Streng steigend, auch wenn zwei Schreibvorgaenge in dieselbe Mikrosekunde fallen.
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(microseconds=next(_uhr))
    return t.isoformat()


def attrappe(faq: list[dict], historie: list[dict] = ()) -> dict:
    """{"faq": [...], "faq_history": [...], "table": name -> Abfrage}; die Listen sind der Stand."""
    db = {"faq": [dict(r) for r in faq], "faq_history": [dict(h) for h in historie]}

    def table(name):
        q = {"op": "select", "wo": [], "sort": [], "limit": None, "daten": None}

        def treffer():
            return [r for r in db[name] if all(test(r) for test in q["wo"])]

        def execute():
            if q["op"] == "insert":
                neu = [
                    {"id": max((r["id"] for r in db[name]), default=0) + 1 + i, "aktiv": True,
                     "ausgeblendet": False, "updated_at": _jetzt()} | d
                    for i, d in enumerate(q["daten"] if isinstance(q["daten"], list) else [q["daten"]])
                ]
                db[name] += neu
                rows = neu
            elif q["op"] == "update":
                rows = treffer()
                for r in rows:
                    db["faq_history"].append({
                        "id": max((h["id"] for h in db["faq_history"]), default=0) + 1, "faq_id": r["id"],
                        "operation": "UPDATE", "alt": dict(r), "geaendert_am": _jetzt(),
                    })
                    r |= q["daten"] | {"updated_at": _jetzt()}
            else:
                rows = treffer()
                for feld, desc in reversed(q["sort"]):
                    rows = sorted(rows, key=lambda r: r[feld], reverse=desc)
                rows = rows[: q["limit"]]
            return type("R", (), {"data": [dict(r) for r in rows]})

        def setze(**kw):
            q.update(kw)
            return abfrage

        abfrage = type("Abfrage", (), {
            "select": lambda self, *a: self,
            "insert": lambda self, d: setze(op="insert", daten=d),
            "update": lambda self, d: setze(op="update", daten=d),
            "eq": lambda self, k, v: setze(wo=q["wo"] + [lambda r: r[k] == v]),
            "order": lambda self, k, desc=False: setze(sort=q["sort"] + [(k, desc)]),
            "limit": lambda self, n: setze(limit=n),
            "execute": lambda self: execute(),
        })()
        return abfrage

    db["table"] = table
    return db


def einsetzen(db: dict, laden=lambda: True) -> None:
    """faq_sync arbeitet ab jetzt gegen db; load() wird ersetzt (kein agent_base, kein Netz)."""
    import faq_sync

    faq_sync._supabase = lambda: type("S", (), {"table": staticmethod(db["table"])})
    faq_sync.load = laden


def beispiel() -> dict:
    """Der committete Snapshot plus das, was er nicht enthaelt: ausgeblendete
    CSV-Reste (auch die Zeile unter "Afrika"), eine geloeschte Frage, ein Verlauf."""
    import faq_sync

    with open(faq_sync.SNAPSHOT, encoding="utf-8") as f:
        zeilen = json.load(f)
    _, versteckt = faq_sync.parse_laender_csv()
    zeilen += [
        {"quelle": "land", "extern_id": None, "kategorie": land, "frage": frage, "antwort": antwort,
         "position": faq_sync.LAND_POSITION + 500 + i, "ausgeblendet": True}
        for i, (land, frage, antwort) in enumerate(versteckt)
    ]
    zeilen.append({
        "quelle": "intern", "extern_id": None, "kategorie": "Buchen und Reservieren", "position": 900, "aktiv": False,
        "frage": "Gibt es einen Frühbucherrabatt?",
        "antwort": "Nein, bei Chamäleon gibt es keinen Frühbucherrabatt. Die Preise gelten für das ganze Reisejahr.",
    })
    faq = [
        {"aktiv": True, "ausgeblendet": False, "updated_at": "2026-10-08T01:52:02.559247+00:00"} | r | {"id": i}
        for i, r in enumerate(zeilen, 1)
    ]
    gutschein = next(r for r in faq if r["quelle"] == "intern" and "utschein" in r["frage"])
    historie = [
        {"id": i, "faq_id": gutschein["id"], "operation": "UPDATE", "geaendert_am": am, "alt": gutschein | {"antwort": antwort}}
        for i, (am, antwort) in enumerate([
            ("2026-06-03T09:40:00+00:00", "Bitte schicke uns den Gutschein per Post an Chamäleon Reisen, Pannierstraße 58, 12047 Berlin."),
            ("2026-10-07T14:12:00+00:00", "Bitte sende uns per Mail die Gutscheinnummer an erlebnisberatung@chamaeleon-reisen.de. Wir rechnen den Gutschein dann deiner Buchung an."),
        ], 1)
    ]
    return attrappe(faq, historie)


if __name__ == "__main__":
    import os
    import sys

    wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, wurzel)
    os.chdir(wurzel)  # faqs/… und static/… sind relativ zum Repo
    from flask import Flask

    import dashboard

    # FAQ_LADEN_SCHEITERT=1: Schreiben klappt, das Laden danach nicht ("Jetzt laden").
    einsetzen(beispiel(), laden=lambda: not os.environ.get("FAQ_LADEN_SCHEITERT"))
    dashboard.API_USERNAME = dashboard.API_PASSWORD = "dev"
    app = Flask(__name__, static_folder=None, root_path=wurzel)
    for pfad, view, methoden, _ in dashboard.routes:
        if pfad.startswith("/admin/faq") and pfad != "/admin/faq-sync":
            app.add_url_rule(pfad, view_func=view, methods=methoden)
    app.run("127.0.0.1", int(os.environ.get("PORT_LOKAL", 8766)))
