import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
import faq_sync
from faq_sync import (
    LAENDER,
    _mit_a,
    land_zeilen,
    laender,
    parse_infos,
    parse_laender_csv,
    parse_md,
    render,
    render_beide,
)


def _seite(n, ids=None):
    ids = ids or [f"CHA-T-FAQ-{i}" for i in range(n)]
    lis = "".join(
        f'<li tos-template="text_akkordeon" tos-txtnr="{t}">'
        f'<h4 class="uk-accordion-title">Frage {i}?</h4>'
        f'<div class="uk-accordion-content"><p>Ab 20 €, siehe <a href="/Club">Club</a>.</p></div></li>'
        for i, t in enumerate(ids)
    )
    return f"<h3>Unterwegs</h3><ul>{lis}</ul>".encode("cp1252")


def test_parse_infos():
    rows = parse_infos(_seite(20))
    assert len(rows) == 20
    assert rows[0] == {
        "quelle": "website",
        "extern_id": "CHA-T-FAQ-0",
        "kategorie": "Unterwegs",
        "frage": "Frage 0?",
        "antwort": "Ab 20 €, siehe [Club](/Club).",
        "position": 1000,
    }


def test_parse_infos_schuetzt_vor_fehlerseiten():
    with pytest.raises(ValueError, match="nur 19"):
        parse_infos(_seite(19))
    with pytest.raises(ValueError, match="doppelte"):
        parse_infos(_seite(20, ids=["X"] * 20))


def test_allgemein_md_rundlauf():
    # Der Import nach Supabase darf von allgemein.md nichts verlieren.
    with open("faqs/allgemein.md", encoding="utf-8") as f:
        md = f.read()
    assert render(parse_md(md)) == _mit_a(md)


def test_render_intern_vor_website():
    rows = parse_infos(_seite(20)) + parse_md("## Kataloge\n\n**F: Wo?**\nA: Hier.")
    assert render(rows).startswith("## Kataloge\n\n**F: Wo?**\nA: Hier.\n\n## Unterwegs")


def _zeile(quelle, kategorie, frage, position):
    return {"quelle": quelle, "kategorie": kategorie, "frage": frage, "antwort": "Ja.", "position": position}


def test_render_trennt_quellen():
    # Gleicher Kategoriename in intern und website: zwei Bloecke, nicht einer.
    rows = [_zeile("intern", "Unterwegs", "A?", 0), _zeile("website", "Unterwegs", "B?", 1000)]
    assert render(rows) == "## Unterwegs\n\n**F: A?**\nA: Ja.\n\n## Unterwegs\n\n**F: B?**\nA: Ja."


def test_neue_frage_landet_in_ihrer_kategorie():
    # Der Editor vergibt hoechste Position + 1; die Frage gehoert trotzdem in ihren Block.
    rows = [
        _zeile("intern", "Kataloge", "A?", 0),
        _zeile("intern", "Reisearten", "B?", 1),
        _zeile("intern", "Kataloge", "Neu?", 2),
    ]
    assert render(rows) == (
        "## Kataloge\n\n**F: A?**\nA: Ja.\n\n**F: Neu?**\nA: Ja.\n\n## Reisearten\n\n**F: B?**\nA: Ja."
    )


def test_laender_csv_rundlauf():
    # Der Import nach Supabase darf von den Laender-CSVs nichts verlieren.
    daten, _ = parse_laender_csv()
    assert set(daten) == set(LAENDER)
    faqs, neu = laender(land_zeilen(daten))
    # Reihenfolge zaehlt: Tool-Beschreibung und Prompt zeigen sie so.
    assert list(faqs) == list(LAENDER)
    assert [list(neu[land].items()) for land in daten] == [list(f.items()) for f in daten.values()]


def test_laender_ohne_frage_bleiben():
    # Frage war "Visum?": die blendet O7 jetzt aus, siehe den Test darunter.
    faqs, daten = laender(land_zeilen({"Namibia": {"Strom?": "Ja."}}))
    assert faqs["Namibia"] == "# Namibia\n\n## Strom?\n\nJa."
    assert faqs["USA"] == "# USA" and daten["USA"] == {}


def test_visumfragen_sieht_gemini_nicht():
    # O7: Einreise und Visum nur aus Passolution. Die Daten behalten sie.
    faqs, daten = laender(land_zeilen({"Ägypten": {
        "E-Visum oder Visa on arrival?": "Visa on arrival.", "Strom?": "230 V."}}))
    assert faqs["Ägypten"] == "# Ägypten\n\n## Strom?\n\n230 V."
    assert "E-Visum oder Visa on arrival?" in daten["Ägypten"]


def test_render_beide_ohne_laender():
    rows = parse_md("## Kataloge\n\n**F: Wo?**\nA: Hier.") + land_zeilen({"Namibia": {"Visum?": "Ja."}})
    assert render_beide(rows) == ("## Kataloge\n\n**F: Wo?**\nA: Hier.",) * 2


def test_zahlungslink_faq_nur_fuer_kunden():
    rows = parse_md("## Bezahlung\n\n**F: Wo finde ich den Zahlungslink?**\nA: Auf der Rechnung.")
    kunden, agentur = render_beide([dict(r, quelle="intern") for r in rows])
    assert "Zahlungslink" in kunden
    assert "Zahlungslink" not in agentur


class _Abfrage:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self.rows})


def _load(monkeypatch, rows):
    monkeypatch.setattr(faq_sync, "_supabase", lambda: type("S", (), {"table": lambda self, t: _Abfrage(rows)})())
    for name in ("allgemeine_faqs", "allgemeine_faqs_agentur", "general_faq_data", "laender_faqs", "laender_faq_data"):
        monkeypatch.setattr(agent_base, name, getattr(agent_base, name))
    assert faq_sync.load()


def test_load_ohne_land_zeilen_behaelt_laender(monkeypatch):
    # Vor dem Import: website/intern kommen an, die Laender-FAQs bleiben stehen.
    vorher = agent_base.laender_faqs
    _load(monkeypatch, parse_md("## Kataloge\n\n**F: Neu?**\nA: Ja."))
    assert agent_base.allgemeine_faqs == "## Kataloge\n\n**F: Neu?**\nA: Ja."
    assert agent_base.general_faq_data == {"Neu?": "Ja."}
    assert agent_base.laender_faqs is vorher


def test_load_meldet_unbekannte_laender(monkeypatch):
    rows = parse_md("## K\n\n**F: A?**\nA: B.") + land_zeilen({"Namibia ": {"Visum?": "Ja."}})
    _load(monkeypatch, rows)
    assert faq_sync.status["load"]["unbekannte_laender"] == ["Namibia "]
    assert agent_base.laender_faqs["Namibia "].startswith("# Namibia ")


def test_laender_csv_gruppen_verschlucken_nichts():
    # Bis 2026-10 setzte "Chile/Bolivien/Peru" Chile und Peru zurueck und
    # versteckte so 10 Fragen; eine Gruppen-Kopfzeile ergaenzt nur.
    daten, _ = parse_laender_csv()
    assert daten["Argentinien"].items() <= daten["Chile"].items()
    assert daten["Bolivien"].items() <= daten["Peru"].items()
    assert "Wie anspruchsvoll sind die Wanderungen ?" in daten["Albanien"]


def test_laender_csv_rest_landet_ausgeblendet():
    # Alles mit Text kommt nach Supabase; was keine ganze Frage-Antwort-Zeile ist,
    # ausgeblendet. Visum bleibt mit Warnung draussen.
    _, versteckt = parse_laender_csv()
    texte = {(land, frage) for land, frage, _ in versteckt}
    assert ("Island", "Wie funktioniert die Fahrt mit dem Flybus?") in texte
    assert ("Lettland", "Kombireise mit Estland und Litauen, siehe Estland") in texte
    assert not any(f.startswith(("Nr.", "Reisenspezifische")) for _, f, _ in versteckt)
    visum = [a for _, f, a in versteckt if "visum" in f.lower()]
    assert len(visum) == 2 and all(a == faq_sync.VISUM_HINWEIS for a in visum)


# ── Editor (/admin/faq) ──


class _Db:
    """Supabase-Attrappe fuer den Editor. Filter werden ignoriert: ob
    .eq("updated_at", …) wirklich trifft, zeigt nur die Probe an der echten Tabelle."""

    def __init__(self, zeilen, historie=(), treffer=True):
        self.zeilen, self.historie, self.treffer = list(zeilen), list(historie), treffer
        self.geschrieben = []

    def table(self, name):
        db = self

        class Abfrage:
            op, daten = "select", None

            def __getattr__(self, _):
                return lambda *a, **k: self

            def insert(self, daten):
                self.op, self.daten = "insert", daten
                return self

            def update(self, daten):
                self.op, self.daten = "update", daten
                return self

            def execute(self):
                if self.op == "select":
                    rows = db.historie if name == "faq_history" else db.zeilen
                elif self.op == "insert":
                    db.geschrieben.append(("insert", self.daten))
                    rows = [self.daten | {"id": 99, "updated_at": "t1"}]
                else:
                    db.geschrieben.append(("update", self.daten))
                    rows = [self.daten | {"updated_at": "t1"}] if db.treffer else []
                return type("R", (), {"data": rows})

        return Abfrage()


def _z(id, quelle="intern", kategorie="Kataloge", frage=None, antwort="Ja.", position=None, aktiv=True, ausgeblendet=False):
    return {
        "id": id, "quelle": quelle, "kategorie": kategorie, "frage": frage or f"Frage {id}?", "antwort": antwort,
        "position": id if position is None else position, "aktiv": aktiv, "ausgeblendet": ausgeblendet,
        "updated_at": "t0",
    }


def _editor(monkeypatch, zeilen, historie=(), treffer=True, geladen=True):
    db = _Db(zeilen, historie, treffer)
    monkeypatch.setattr(faq_sync, "_supabase", lambda: db)
    monkeypatch.setattr(faq_sync, "load", lambda: geladen)
    return db


ZWEI = [_z(1), _z(2)]


@pytest.mark.parametrize(
    "d, feld",
    [
        ({"quelle": "land", "kategorie": "Atlantis", "frage": "Neu?", "antwort": "Ja."}, "kategorie"),
        ({"quelle": "intern", "kategorie": "", "frage": "Neu?", "antwort": "Ja."}, "kategorie"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "  ", "antwort": "Ja."}, "frage"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "Neu?", "antwort": ""}, "antwort"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "Neu\nzwei?", "antwort": "Ja."}, "frage"),
        ({"quelle": "intern", "kategorie": "Kat\naloge", "frage": "Neu?", "antwort": "Ja."}, "kategorie"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "x" * 301, "antwort": "Ja."}, "frage"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "Neu?", "antwort": "x" * 4001}, "antwort"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "Frage 1?", "antwort": "Ja."}, "frage"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": "Web?", "antwort": "Ja."}, "frage"),
        ({"quelle": "intern", "kategorie": "Kataloge", "frage": 7, "antwort": "Ja."}, "frage"),
        ({"quelle": "website", "kategorie": "Kataloge", "frage": "Neu?", "antwort": "Ja."}, None),
        ({"kategorie": "Kataloge", "frage": "Neu?", "antwort": "Ja."}, None),
    ],
)
def test_pruefen_lehnt_ab(monkeypatch, d, feld):
    db = _editor(monkeypatch, ZWEI + [_z(3, "website", "Unterwegs", "Web?")])
    ergebnis = faq_sync.anlegen(d)
    assert ergebnis["status"] == 400 and ergebnis["feld"] == feld and ergebnis["error"]
    assert db.geschrieben == []


def test_dublette_nur_im_selben_schluesselraum(monkeypatch):
    # Dieselbe Frage in einem anderen Land, oder neben einer ausgeblendeten Zeile, ist erlaubt.
    zeilen = ZWEI + [_z(3, "land", "Namibia", "Geld?"), _z(4, frage="Alt?", ausgeblendet=True)]
    _editor(monkeypatch, zeilen)
    assert faq_sync.anlegen({"quelle": "land", "kategorie": "Kenia", "frage": "Geld?", "antwort": "Ja."})["ok"]
    assert faq_sync.anlegen({"quelle": "intern", "kategorie": "Kataloge", "frage": "Alt?", "antwort": "Ja."})["ok"]
    assert faq_sync.anlegen({"quelle": "land", "kategorie": "Namibia", "frage": "Geld?", "antwort": "Ja."})["feld"] == "frage"


def test_position_zaehlt_geloeschte_mit(monkeypatch):
    db = _editor(monkeypatch, [_z(1), _z(2, position=7, aktiv=False), _z(3, "land", "Namibia", position=2005)])
    ergebnis = faq_sync.anlegen({"quelle": "intern", "kategorie": "Neu", "frage": " Neu? ", "antwort": "Ja."})
    assert ergebnis["ok"] and ergebnis["historie_id"] is None
    assert db.geschrieben == [("insert", {
        "quelle": "intern", "kategorie": "Neu", "frage": "Neu?", "antwort": "Ja.",
        "ausgeblendet": False, "aktiv": True, "extern_id": None, "position": 8,
    })]
    faq_sync.anlegen({"quelle": "land", "kategorie": "Italien", "frage": "Neu?", "antwort": "Ja."})
    assert db.geschrieben[-1][1]["position"] == 2006


def test_leere_antwort_nur_ausgeblendet(monkeypatch):
    db = _editor(monkeypatch, ZWEI + [_z(3, "land", "Namibia", "Notiz", antwort="", ausgeblendet=True)])
    # Bearbeiten geht, solange die Zeile ausgeblendet bleibt; einblenden erst mit Antwort.
    assert faq_sync.aendern(3, {"frage": "Notiz neu"}, "t0")["ok"]
    assert faq_sync.aendern(3, {"ausgeblendet": False}, "t0")["feld"] == "antwort"
    assert faq_sync.aendern(1, {"antwort": "", "ausgeblendet": True}, "t0")["feld"] == "antwort"
    assert db.geschrieben == [("update", {"frage": "Notiz neu"})]


def test_aendern_grenzen(monkeypatch):
    zeilen = ZWEI + [_z(3, "website", "Unterwegs", "Web?"), _z(4, aktiv=False), _z(5, "land", "Afrika", "Visum?")]
    db = _editor(monkeypatch, zeilen)
    assert faq_sync.aendern(3, {"antwort": "Neu."}, "t0")["status"] == 400  # website-Zeile
    assert faq_sync.aendern(1, {"quelle": "land"}, "t0")["status"] == 400  # quelle aendern
    assert faq_sync.aendern(4, {"antwort": "Neu."}, "t0")["status"] == 400  # geloescht
    assert faq_sync.aendern(8, {"antwort": "Neu."}, "t0")["status"] == 404
    assert faq_sync.aendern(1, {"antwort": "Neu."}, None)["status"] == 400  # ohne Stand
    assert db.geschrieben == []
    # Land ausserhalb von LAENDER (R6): bearbeitbar, solange die Kategorie bleibt.
    assert faq_sync.aendern(5, {"antwort": "Neu."}, "t0")["ok"]
    assert faq_sync.aendern(5, {"kategorie": "Namibia"}, "t0")["ok"]
    assert faq_sync.aendern(5, {"kategorie": "Europa"}, "t0")["feld"] == "kategorie"


def test_aendern_kategorie_ans_ende(monkeypatch):
    db = _editor(monkeypatch, ZWEI, historie=[{"id": 41}])
    ergebnis = faq_sync.aendern(1, {"kategorie": "Reisearten", "frage": "Frage 1?"}, "t0")
    assert db.geschrieben == [("update", {"kategorie": "Reisearten", "position": 3})]
    assert ergebnis == {"ok": True, "zeile": {"kategorie": "Reisearten", "position": 3, "updated_at": "t1"},
                        "geladen": True, "historie_id": 41}
    # Nichts geaendert: kein Schreiben, kein Eintrag in der Historie.
    assert faq_sync.aendern(2, {"frage": "Frage 2?"}, "t0")["historie_id"] is None
    assert len(db.geschrieben) == 1


def test_aendern_konflikt(monkeypatch):
    _editor(monkeypatch, ZWEI, treffer=False)
    assert faq_sync.aendern(1, {"antwort": "Neu."}, "veraltet")["status"] == 409


def test_schreiben_ok_laden_scheitert(monkeypatch):
    _editor(monkeypatch, ZWEI, geladen=False)
    ergebnis = faq_sync.aendern(1, {"antwort": "Neu."}, "t0")
    assert ergebnis["ok"] and ergebnis["geladen"] is False


def test_loeschen_setzt_aktiv(monkeypatch):
    db = _editor(monkeypatch, ZWEI)
    assert faq_sync.loeschen(1)["ok"]
    assert db.geschrieben == [("update", {"aktiv": False})]


def test_letzte_interne_zeile_bleibt(monkeypatch):
    zeilen = [_z(1), _z(2, ausgeblendet=True), _z(3, aktiv=False), _z(4, "website", "Unterwegs")]
    db = _editor(monkeypatch, zeilen, historie=[{"id": 9, "faq_id": 1, "alt": _z(1, ausgeblendet=True)}])
    assert faq_sync.loeschen(1)["status"] == 400
    assert faq_sync.aendern(1, {"ausgeblendet": True}, "t0")["status"] == 400
    assert faq_sync.fassung_zurueck(1, 9, "t0")["status"] == 400  # test_fassung_zurueck_letzte_interne
    assert db.geschrieben == []


def test_wiederherstellen(monkeypatch):
    db = _editor(monkeypatch, [_z(1), _z(2, aktiv=False), _z(3, frage="Frage 1?", aktiv=False)])
    assert faq_sync.wiederherstellen(2)["ok"]
    assert db.geschrieben == [("update", {"aktiv": True})]
    assert faq_sync.wiederherstellen(3)["feld"] == "frage"  # waere eine Dublette


def test_fassung_zurueck_fremde_historie(monkeypatch):
    db = _editor(monkeypatch, ZWEI, historie=[{"id": 9, "faq_id": 2, "alt": _z(2, antwort="Alt.")}])
    assert faq_sync.fassung_zurueck(1, 9, "t0")["status"] == 404
    assert db.geschrieben == []


def test_fassung_zurueck_holt_position(monkeypatch):
    # Nach einem versehentlichen Kategoriewechsel: alte Kategorie UND alter Platz,
    # aber nie aktiv, quelle oder id aus der Historie.
    alt = _z(1, kategorie="Kataloge", position=0) | {"aktiv": False, "quelle": "land", "id": 5}
    db = _editor(monkeypatch, [_z(1, kategorie="Reisearten", position=3), _z(2)], historie=[{"id": 9, "faq_id": 1, "alt": alt}])
    assert faq_sync.fassung_zurueck(1, 9, "t0")["ok"]
    assert db.geschrieben == [("update", {"kategorie": "Kataloge", "position": 0})]


def test_editor_zeilen_website_nur_sichtbare(monkeypatch):
    zeilen = [_z(1), _z(2, aktiv=False), _z(3, "land", "Namibia", ausgeblendet=True), _z(4, "website", "Unterwegs"),
              _z(5, "website", "Unterwegs", aktiv=False), _z(6, "website", "Unterwegs", ausgeblendet=True)]
    _editor(monkeypatch, zeilen)
    ergebnis = faq_sync.editor_zeilen()
    assert [r["id"] for r in ergebnis["zeilen"]] == [1, 2, 3, 4] and ergebnis["laender"] == LAENDER


def test_verlauf_ohne_inhaltsgleiche(monkeypatch):
    # Neueste zuerst: Loeschen (9) und Wiederherstellen (8) aendern keinen Inhalt, 7 schon.
    jetzt = _z(1, antwort="Neu.")
    historie = [
        {"id": 9, "geaendert_am": "d3", "alt": jetzt | {"aktiv": False}},
        {"id": 8, "geaendert_am": "d2", "alt": jetzt},
        {"id": 7, "geaendert_am": "d1", "alt": _z(1, antwort="Alt.")},
    ]
    db = _editor(monkeypatch, [jetzt], historie=historie)
    assert faq_sync.verlauf(1)["fassungen"] == [
        {"id": 7, "geaendert_am": "d1", "kategorie": "Kataloge", "frage": "Frage 1?", "antwort": "Alt.", "ausgeblendet": False}
    ]
    db.zeilen = []
    assert faq_sync.verlauf(1)["status"] == 404


# ── Routen ──


@pytest.fixture
def editor_app(monkeypatch):
    """Nur die Editor-Routen, ohne ``import app`` (das gegen die echte Tabelle arbeitet)."""
    import base64

    from flask import Flask
    from flask_limiter import Limiter, RateLimitExceeded
    from flask_limiter.util import get_remote_address

    import dashboard
    import rate_limit

    db = _editor(monkeypatch, ZWEI + [_z(3, "website", "Unterwegs", "Web?")])
    app = Flask(__name__)
    limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[], storage_uri="memory://")
    app.register_error_handler(RateLimitExceeded, rate_limit._on_rate_limit)
    app.add_url_rule("/chat/stream", view_func=limiter.limit("1 per hour")(lambda: "ok"), methods=["POST"])
    for pfad, view, methoden, _ in dashboard.routes:
        if pfad.startswith("/admin/faq/"):
            limit = "1 per hour" if pfad.endswith("/laden") else "100 per hour"
            app.add_url_rule(pfad, view_func=limiter.limit(limit)(view), methods=methoden)
    kopf = base64.b64encode(f"{dashboard.API_USERNAME}:{dashboard.API_PASSWORD}".encode()).decode()
    return app.test_client(), {"Authorization": f"Basic {kopf}"}, db


def test_routen_ohne_auth(editor_app):
    client, _, _ = editor_app
    assert client.get("/admin/faq/zeilen").status_code == 401
    assert client.post("/admin/faq/zeilen/1/loeschen", json={}).status_code == 401


def test_routen_antworten(editor_app):
    client, auth, db = editor_app
    assert [r["id"] for r in client.get("/admin/faq/zeilen", headers=auth).get_json()["zeilen"]] == [1, 2, 3]
    ok = client.post("/admin/faq/zeilen/1", headers=auth, json={"antwort": "Neu.", "updated_at": "t0"})
    assert ok.status_code == 200 and ok.get_json()["geladen"] is True and "status" not in ok.get_json()
    leer = client.post("/admin/faq/zeilen", headers=auth, json={"quelle": "intern", "kategorie": "Kataloge", "frage": ""})
    assert leer.status_code == 400 and leer.get_json()["feld"] == "frage" and leer.get_json()["error"]
    assert client.post("/admin/faq/zeilen/3", headers=auth, json={"antwort": "x", "updated_at": "t0"}).status_code == 400
    assert client.post("/admin/faq/zeilen/8/loeschen", headers=auth, json={}).status_code == 404
    db.treffer = False
    assert client.post("/admin/faq/zeilen/2", headers=auth, json={"antwort": "x", "updated_at": "alt"}).status_code == 409


def test_routen_verlangen_json(editor_app):
    # Ein Formular von einer fremden Seite kann kein application/json senden.
    client, auth, db = editor_app
    assert client.post("/admin/faq/zeilen/1/loeschen", headers=auth).status_code == 400
    assert client.post("/admin/faq/zeilen/1/loeschen", headers=auth, data={"x": "1"}).status_code == 400
    assert client.post("/admin/faq/zeilen/1/loeschen", headers=auth, data="[]", content_type="application/json").status_code == 400
    assert db.geschrieben == []


def test_routen_supabase_weg(editor_app, monkeypatch):
    client, auth, _ = editor_app
    monkeypatch.setattr(faq_sync, "_supabase", lambda: 1 / 0)
    antwort = client.post("/admin/faq/zeilen/1/loeschen", headers=auth, json={})
    assert antwort.status_code == 502 and "noch einmal" in antwort.get_json()["error"]


def test_routen_limit_ist_429_json(editor_app):
    # Frueher 200 mit SSE-Rumpf: ein gebremstes Speichern saehe wie ein Erfolg aus.
    client, auth, _ = editor_app
    assert client.post("/admin/faq/laden", headers=auth, json={}).get_json()["geladen"] is True
    gebremst = client.post("/admin/faq/laden", headers=auth, json={})
    assert gebremst.status_code == 429 and gebremst.is_json and gebremst.get_json()["error"]


def test_chat_limit_bleibt_sse(editor_app):
    # Das Widget liest SSE: der Chat darf vom 429-JSON der anderen Routen nichts merken.
    client, _, _ = editor_app
    client.post("/chat/stream", json={})
    gebremst = client.post("/chat/stream", json={})
    assert gebremst.status_code == 200 and gebremst.mimetype == "text/event-stream"
    assert "rate_limited" in gebremst.get_data(as_text=True)


def test_replace_nur_mit_pflege_verwerfen():
    assert faq_sync.replace_gesperrt(["faq_sync.py", "import", "--replace"])
    assert not faq_sync.replace_gesperrt(["faq_sync.py", "import-laender", "--replace", "--pflege-verwerfen"])
    assert not faq_sync.replace_gesperrt(["faq_sync.py", "export"])


def test_zwei_reiter_der_zweite_bekommt_409(monkeypatch):
    # Der ganze Weg Route -> faq_sync gegen eine Attrappe mit Filtern und Trigger:
    # speichern, mit altem Stand speichern (409), mit neuem Stand speichern.
    from flask import Flask

    import dashboard
    from faq_attrappe import attrappe

    db = attrappe([_z(1), _z(2)])
    monkeypatch.setattr(faq_sync, "_supabase", lambda: type("S", (), {"table": staticmethod(db["table"])}))
    monkeypatch.setattr(faq_sync, "load", lambda: True)
    monkeypatch.setattr(dashboard, "API_USERNAME", "dev")
    monkeypatch.setattr(dashboard, "API_PASSWORD", "dev")
    app = Flask(__name__)
    for pfad, view, methoden, _ in dashboard.routes:
        if pfad.startswith("/admin/faq/"):
            app.add_url_rule(pfad, view_func=view, methods=methoden)
    client, auth = app.test_client(), {"Authorization": "Basic ZGV2OmRldg=="}

    def speichern(antwort, stand):
        return client.post("/admin/faq/zeilen/1", headers=auth, json={"antwort": antwort, "updated_at": stand})

    erster = speichern("Erste.", "t0")
    assert erster.status_code == 200
    assert speichern("Zweite.", "t0").status_code == 409
    neu = erster.get_json()
    assert speichern("Zweite.", neu["zeile"]["updated_at"]).status_code == 200
    assert db["faq"][0]["antwort"] == "Zweite."
    # "Rueckgaengig" des ersten Speicherns waere jetzt veraltet; mit frischem Stand holt es "Ja." zurueck.
    stand = client.get("/admin/faq/zeilen", headers=auth).get_json()["zeilen"][0]["updated_at"]
    zurueck = client.post(f"/admin/faq/zeilen/1/verlauf/{neu['historie_id']}", headers=auth, json={"updated_at": stand})
    assert zurueck.status_code == 200 and db["faq"][0]["antwort"] == "Ja."
    assert [f["antwort"] for f in client.get("/admin/faq/zeilen/1/verlauf", headers=auth).get_json()["fassungen"]] == [
        "Zweite.", "Erste.", "Ja."
    ]
