"""Verschraenkte Messung alt/neu fuer W3 (docs/kundenfeedback-2026-09-plan.md).

Jeder Fall wird im selben Prozess zweimal unmittelbar nacheinander gestellt:
einmal mit dem ALTEN `system_prompt_template` (vor W3), einmal mit dem NEUEN,
die Reihenfolge je Fall alternierend. Innerhalb des Paares ist damit alles
konstant ausser dem Prompt — Zeit, Last, Modell-Serving, Webseiteninhalt und
alle anderen Commits. Zwei getrennte Kampagnen haetten das vermengt (D20).

Kein Eingriff in die drei Suiten (Review R2): die Fall-Listen und die
Einzelfunktionen werden importiert und direkt gerufen.

    python tests/eval_paare.py <block> [--teil 2/3]    ein Prozess, ein Stueck
    python tests/eval_paare.py kampagne                alle Bloecke, geteilt
    python tests/eval_paare.py bericht <protokoll.jsonl>

Bloecke: filter airline nichtangeboten ungefragt fachwissen erfinden flug
agentur meinchamaeleon.

Jeder Fall kostet zwei Aufrufe plus Wiederholungen bei Leerantworten (R1). Ab
~25 Aufrufen am Stueck kippt Gemini in die leere Antwort (TODOS, 2026-09-18);
deshalb faehrt ein Prozess hoechstens FAELLE_JE_PROZESS Faelle, und zwischen
zwei Prozessen liegt EVAL_PAUSE_S (Review R12).
"""

import argparse
import datetime
import functools
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent
import agent_base

REPO = Path(__file__).resolve().parent.parent

# Eltern-Commit von W3-Commit 1: der letzte Stand mit der alten Vorlage
# (Review R6). Fest eingetragen und nicht "HEAD~4", damit ein weiterer Commit
# auf main die Messung nicht still auf eine andere Vorlage schiebt.
ALT_COMMIT = "a84f3f14018075849c3d310ec50d331c3cce8adf"

FAELLE_JE_PROZESS = 11
EVAL_PAUSE_S = float(os.getenv("EVAL_PAUSE_S", "60"))

PROTOKOLL_DIR = REPO / "data" / "eval_paare"

BLOECKE = (
    "filter", "airline", "nichtangeboten", "ungefragt", "fachwissen",
    "erfinden", "flug", "agentur", "meinchamaeleon",
)

# McNemar nur, wo n ihn traegt (Review R10).
MIT_P_WERT = ("airline", "agentur")


# --- Die alte Vorlage aus Git (Review R6) ----------------------------------

_VORLAGE_ANFANG = 'system_prompt_template = f"""'
_VORLAGE_ENDE = '""".strip()'


def schneide_vorlage(quelltext: str) -> str:
    """Der Rumpf des f-Strings `system_prompt_template` aus einem Quelltext."""
    anfang = quelltext.index(_VORLAGE_ANFANG) + len(_VORLAGE_ANFANG)
    ende = quelltext.index(_VORLAGE_ENDE, anfang)
    return quelltext[anfang:ende]


def vorlage_aus_quelle(quelltext: str, namensraum: dict) -> str:
    """Die Vorlage so, wie der Import sie baut: f-String im Modulnamensraum.

    Im Namensraum von `agent_base` ausgewertet, nicht in einer zweiten
    Modulkopie: die haette Sitemap und FAQs neu geladen und alten Code aus
    W2/W5/W7 mitgebracht. So unterscheiden sich alt und neu NUR im Text der
    Vorlage — `{allgemeine_faqs}` ist auf beiden Seiten der heutige Stand.
    """
    return eval('f"""' + schneide_vorlage(quelltext) + '"""', namensraum).strip()


def alte_vorlage(commit: str = ALT_COMMIT) -> str:
    quelltext = subprocess.run(
        ["git", "show", f"{commit}:agent_base.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return vorlage_aus_quelle(quelltext, vars(agent_base))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- Vorlage umschalten ----------------------------------------------------

_FESTE_ZEIT = {"date": "24. September 2026", "time": "12:00", "weekday": "Thursday"}


def rendere(vorlage: str) -> str:
    """`format_system_prompt` fuer einen festen Fall unter ``vorlage``.

    Mit fester Uhrzeit, sonst unterschiede eine Minutengrenze zwischen den
    beiden Aufrufen zwei gleiche Vorlagen.
    """
    alt_vorlage, alt_zeit = agent_base.system_prompt_template, agent_base.get_current_time_info
    agent_base.system_prompt_template = vorlage
    agent_base.get_current_time_info = lambda: dict(_FESTE_ZEIT)
    try:
        return agent_base.format_system_prompt("/", [])
    finally:
        agent_base.system_prompt_template = alt_vorlage
        agent_base.get_current_time_info = alt_zeit


def tausch_nachweis(alt: str, neu: str) -> dict:
    """Vorbedingung (Review R5): beide Vorlagen rendern verschieden.

    Waeren sie gleich, laege b = c = 0 und saehe aus wie "W3 wirkt nicht".
    Das ist kein Ergebnis, sondern ein kaputter Tausch — harter Abbruch vor
    dem ersten Modellaufruf.
    """
    prompt_alt, prompt_neu = rendere(alt), rendere(neu)
    if prompt_alt == prompt_neu:
        raise SystemExit("Tausch-Nachweis: alte und neue Vorlage rendern gleich — Abbruch")
    return {"prompt_alt_sha256": sha256(prompt_alt), "prompt_neu_sha256": sha256(prompt_neu)}


# --- Mitschnitt: Tool-Aufrufe und Antworten (Review R7) --------------------
#
# agent.call verwirft die tool_call-Events. call loest `call_stream` zur
# Laufzeit im Modul agent auf — ein Wrapper dort sieht also jeden Aufruf, auch
# den aus den Testmodulen, die `call` per `from agent import call` gebunden
# haben.

_mitschnitt = {"tools": 0, "antworten": []}


def _zaehlender_stream(original):
    @functools.wraps(original)
    def stream(*args, **kwargs):
        for event in original(*args, **kwargs):
            if event["type"] == "tool_call":
                _mitschnitt["tools"] += 1
            elif event["type"] == "response":
                _mitschnitt["antworten"].append(event["data"]["reply"])
            yield event

    stream.eval_paare_original = original
    return stream


def mitschnitt_einschalten() -> None:
    if not hasattr(agent.call_stream, "eval_paare_original"):
        agent.call_stream = _zaehlender_stream(agent.call_stream)


# --- Eine Seite eines Paares -----------------------------------------------

_LAENGE = re.compile(r"^\d+ Saetze \(hoechstens \d+\)$")


def befund_art(befunde: list[str], text: str) -> str:
    """Laenge / Link / Inhalt — fuer die c-Liste und den D13-Ausloeser (R3).

    "Laenge" nur, wenn die Satzzahl der EINZIGE Befund ist: dann misst der Fall
    die Laenge und nicht das, wofuer er da ist.
    """
    if befunde and all(_LAENGE.match(b) for b in befunde):
        return "Laenge"
    if re.search(r"missing|kein(?:en)? Link|MeinCham|https?:|\\\.", text):
        return "Link"
    return "Inhalt"


def fahre_seite(fall: dict, vorlage: str) -> dict:
    """Ein Fall unter einer Vorlage; ein Ausgang von vier (Review R8).

    gruen, rot (AssertionError), leer (Fallback-Text auch nach einer
    Wiederholung, R1), fehler (Skip, Netz-/TourOne-Ausnahme, fehlende
    Umgebung). Leer schlaegt jeden anderen Ausgang: eine Fallback-Antwort
    besteht manche Pruefung ("nennt nicht den Vertrieb") zufaellig.
    """
    alt = agent_base.system_prompt_template
    agent_base.system_prompt_template = vorlage
    try:
        for versuch in (1, 2):
            _mitschnitt["tools"], _mitschnitt["antworten"] = 0, []
            start, zeit = time.monotonic(), datetime.datetime.now(datetime.timezone.utc)
            befunde: list[str] = []
            try:
                fall["lauf"]()
                ausgang = "gruen"
            except AssertionError as fehler:
                ausgang = "rot"
                befunde = fehler.args[0] if fehler.args and isinstance(fehler.args[0], list) else [str(fehler)[:400]]
            except (pytest.skip.Exception, Exception) as fehler:  # noqa: BLE001
                ausgang = "fehler"
                befunde = [f"{type(fehler).__name__}: {str(fehler)[:300]}"]
            leer = any(agent.EMPTY_ANSWER_FALLBACK in a for a in _mitschnitt["antworten"])
            if not leer:
                break
        if leer:
            ausgang = "leer"
    finally:
        agent_base.system_prompt_template = alt
    return {
        "ausgang": ausgang,
        "befund_art": befund_art(befunde, " ".join(befunde)) if ausgang == "rot" else "",
        "befunde": befunde,
        "leer_wiederholt": versuch == 2,
        "tool_aufrufe": _mitschnitt["tools"],
        "dauer_s": round(time.monotonic() - start, 2),
        "zeit": zeit.isoformat(timespec="seconds"),
        "vorlage_sha256": sha256(vorlage),
    }


# --- Faelle ----------------------------------------------------------------
#
# Ein Fall ist ein Dict: id, block, `vorbereiten` (holt alle Seiten VOR dem
# ersten Modellaufruf, R9) und `lauf` (wirft AssertionError, wenn rot).
# `vorbereiten` darf pytest.skip werfen — dann faellt der Fall ganz heraus
# (Airline-Land ohne Airline) — oder etwas anderes, dann sind beide Seiten
# Fehler: eine kaputte Ableitung ist nicht die Schuld des Prompts.


def _neuer_fall(block: str, roh: dict, bauen=None) -> dict:
    """Ein Fall der neuen Suite: `call` + `pruefe` direkt (R9).

    ``bauen`` liefert das Fall-Dict erst beim Vorbereiten — fuer `airline`, wo
    schon die Frage von der Seite abhaengt.
    """
    import test_kundenfeedback_eval as kf

    aufgeloest: dict = {}

    def vorbereiten():
        quelle = bauen() if bauen else roh
        aufgeloest.clear()
        aufgeloest.update({k: kf._wert(v) for k, v in quelle.items()})

    def lauf():
        reply = agent.call(kf._nachrichten(aufgeloest), aufgeloest["endpoint"])
        befunde = kf.pruefe(aufgeloest, reply)
        assert not befunde, befunde

    return {"id": roh["id"], "block": block, "vorbereiten": vorbereiten, "lauf": lauf}


def _airline_fall(land: str) -> dict:
    """Nachbau von `test_airline` mit denselben Modul-Helfern."""
    import test_kundenfeedback_eval as kf

    fid = f"airline-{land}"

    def bauen():
        pfad, airline = kf.reise_mit_airline(land)
        if not airline:
            pytest.skip(f"{land}: keine Airline in den Leistungen")
        return {
            "id": fid,
            "frage": "Mit welcher Airline fliegt man nach "
            f"{kf.all_countries.get(land, land).replace('-', ' und ')}?",
            "endpoint": pfad,
            "muss": [airline],
            "darf_nicht": ["Rechnung", "Unterlagenlink", "Reiseunterlagen"],
        }

    return _neuer_fall("airline", {"id": fid}, bauen)


# Vier von zehn (Plan, Tabelle "Umfang"): der Block prueft EINEN Prompt-Satz.
# Ruanda ist Punkt 13 selbst, Nigeria ein Kandidat aus dem Plan, Turkmenistan
# und Paraguay stehen in keinem Prompt-Beispiel.
NICHTANGEBOTEN_AUSWAHL = ("Ruanda", "Nigeria", "Turkmenistan", "Paraguay")


def _nichtangeboten_fall(land: str) -> dict:
    import test_kundenfeedback_eval as kf

    return _neuer_fall(
        "nichtangeboten",
        {
            "id": f"nichtangeboten-{land}",
            "frage": f"Habt ihr eine Reise nach {land}?",
            "endpoint": "/",
            "muss_eines": kf._ABSAGE,
            "darf_nicht": [rf"und\s+{land}", rf"nach\s+{land}\s+f(ü|ue)hrt", "kombinier"],
        },
    )


def _funktion_fall(block: str, fid: str, fn, *args) -> dict:
    return {"id": fid, "block": block, "vorbereiten": lambda: None,
            "lauf": functools.partial(fn, *args)}


def _agentur_faelle() -> list[dict]:
    """22 aus DONE + 8 Einzelfunktionen + 2 Deko; eine davon nur mit Nummer.

    Der Plan nennt 33 — gezaehlt sind es 32 (R8 korrigiert auf 31 + 1).
    """
    import test_agentur_faq as af

    faelle = [
        _funktion_fall("agentur", cid, af.test_agentur_answer, q, muss, nicht)
        for cid, q, muss, nicht in af.DONE
    ]
    einzeln = [
        af.test_provision_bleibt_beim_vertrieb,
        af.test_fluganfrage_fragt_nach_der_reise,
        af.test_fluganfrage_mit_reise_nennt_die_durchwahl,
        af.test_termine_nennt_die_durchwahl,
        af.test_ansprechpartner_fragt_zurueck,
        af.test_gast_bucht_selbst_geht_nicht_zum_vertrieb,
        af.test_unbekannte_reise_erfindet_keine_durchwahl,
    ]
    if os.getenv("AGENTUR_TEST_NUMMER"):
        einzeln.append(af.test_buchungsstatus_mit_verifizierter_agentur)
    faelle += [_funktion_fall("agentur", fn.__name__, fn) for fn in einzeln]
    faelle += [
        _funktion_fall("agentur", f"deko-{fid}", af.test_agentur_deko_price_points_to_page, q)
        for fid, q in (("stoffbanner", "wie teuer sind stoffbanner?"),
                       ("leuchtdisplays", "wie teuer sind die leuchtdisplays?"))
    ]
    return faelle


def _meinchamaeleon_faelle() -> list[dict]:
    import test_meinchamaeleon_faq as mc

    faelle = [
        _funktion_fall("meinchamaeleon", cid, mc.test_meinchamaeleon_provides_url, q, kw)
        for cid, q, kw in mc.URL_CASES
    ]
    faelle.append(_funktion_fall(
        "meinchamaeleon", "koffergroesse",
        mc.test_koffergroesse_verweist_auf_die_fluggesellschaft,
    ))
    faelle += [
        _funktion_fall("meinchamaeleon", f"unterlagen-{fid}",
                       mc.test_reiseunterlagen_link_ist_vollstaendig, ep, kid)
        for fid, ep, kid in (("mit-vrrvorgang", mc.ENDPOINT, mc.KUNDEN_ID),
                             ("ohne-vrrvorgang-testkunde", mc.UEBERSICHT_ENDPOINT, mc.TESTKUNDE))
    ]
    return faelle


def faelle(block: str) -> list[dict]:
    """Alle Faelle eines Blocks, in fester Reihenfolge, ohne Netz."""
    import test_kundenfeedback_eval as kf

    listen = {"filter": kf.FILTER, "ungefragt": kf.UNGEFRAGT, "fachwissen": kf.FACHWISSEN,
              "erfinden": kf.ERFINDEN, "flug": kf.FLUG}
    if block in listen:
        return [_neuer_fall(block, roh) for roh in listen[block]]
    if block == "airline":
        return [_airline_fall(land) for land in kf.AIRLINE_LAENDER]
    if block == "nichtangeboten":
        return [_nichtangeboten_fall(land) for land in NICHTANGEBOTEN_AUSWAHL
                if land in kf.NICHT_ANGEBOTEN]
    if block == "agentur":
        return _agentur_faelle()
    if block == "meinchamaeleon":
        return _meinchamaeleon_faelle()
    raise ValueError(f"unbekannter Block {block!r}, gueltig: {BLOECKE}")


def teile(anzahl: int) -> int:
    return max(1, math.ceil(anzahl / FAELLE_JE_PROZESS))


def stueck(liste: list, teil: int, von: int) -> list:
    """Das ``teil``-te von ``von`` zusammenhaengenden Stuecken (1-basiert)."""
    groesse = math.ceil(len(liste) / von)
    return liste[(teil - 1) * groesse : teil * groesse]


# --- Ein Prozess -----------------------------------------------------------


def _schreibe(pfad: Path, zeile: dict) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("a", encoding="utf-8") as datei:
        datei.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def fahre_block(block: str, teil: int, von: int, protokoll: Path, prozess: str) -> None:
    alt, neu = alte_vorlage(), agent_base.system_prompt_template
    kopf = {
        "typ": "kopf", "block": block, "teil": f"{teil}/{von}", "prozess": prozess,
        "alt_commit": ALT_COMMIT, "neu_commit": _git("rev-parse", "HEAD"),
        "neu_agent_base_geaendert": bool(_git("status", "--porcelain", "agent_base.py")),
        "vorlage_alt_sha256": sha256(alt), "vorlage_neu_sha256": sha256(neu),
        **tausch_nachweis(alt, neu),
        "zeit": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    _schreibe(protokoll, kopf)
    print(f"# {block} {teil}/{von} — Prozess {prozess}", flush=True)
    mitschnitt_einschalten()

    alle = faelle(block)
    auswahl = [(alle.index(f), f) for f in stueck(alle, teil, von)]

    # Erst alle Seiten, dann der erste Modellaufruf (R9): ein Abruf mitten im
    # Paar stuende sonst auf einer Seite und nicht auf der anderen.
    bereit = []
    for index, fall in auswahl:
        try:
            fall["vorbereiten"]()
            bereit.append((index, fall, None))
        except pytest.skip.Exception as grund:
            _schreibe(protokoll, {"typ": "uebersprungen", "block": block, "fall": fall["id"],
                                  "grund": str(grund)[:200], "prozess": prozess})
        except Exception as fehler:  # noqa: BLE001
            bereit.append((index, fall, f"{type(fehler).__name__}: {str(fehler)[:300]}"))

    for index, fall, vorfehler in bereit:
        # Alternierend ueber den Index im GANZEN Block, damit auch geteilte
        # Bloecke je Fall dieselbe Reihenfolge haben wie ungeteilte.
        seiten = [("alt", alt), ("neu", neu)] if index % 2 == 0 else [("neu", neu), ("alt", alt)]
        for seite, vorlage in seiten:
            if vorfehler:
                ergebnis = {"ausgang": "fehler", "befund_art": "", "befunde": [vorfehler],
                            "leer_wiederholt": False, "tool_aufrufe": 0, "dauer_s": 0.0,
                            "zeit": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                            "vorlage_sha256": sha256(vorlage)}
            else:
                ergebnis = fahre_seite(fall, vorlage)
            _schreibe(protokoll, {"typ": "seite", "block": block, "fall": fall["id"],
                                  "seite": seite, "prozess": prozess, **ergebnis})
            print(f"  {fall['id']:<50} {seite} {ergebnis['ausgang']:<6} "
                  f"tools={ergebnis['tool_aufrufe']} {ergebnis['dauer_s']}s "
                  f"{'; '.join(ergebnis['befunde'])[:160]}", flush=True)


# --- Auswertung (Review R3/R10) -------------------------------------------


def mcnemar_p(b: int, c: int) -> float:
    """Exakter McNemar, zweiseitig: Binomialtest der diskordanten Paare."""
    n = b + c
    if n == 0:
        return 1.0
    kleiner = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(kleiner + 1)) / 2**n)


def auswerten(zeilen: list[dict]) -> dict:
    """Je Block: b, c, beide gruen/rot, ausgefallen, leer, Fehler, Dauer, Tools."""
    seiten: dict[tuple[str, str], dict] = {}
    for z in zeilen:
        if z.get("typ") == "seite":
            seiten.setdefault((z["block"], z["fall"]), {})[z["seite"]] = z
    bloecke: dict[str, dict] = {}
    for (block, fall), paar in seiten.items():
        e = bloecke.setdefault(block, {
            "faelle": 0, "b": 0, "c": 0, "beide_gruen": 0, "beide_rot": 0,
            "ausgefallen": 0, "leer_alt": 0, "leer_neu": 0, "fehler_alt": 0,
            "fehler_neu": 0, "wiederholt_alt": 0, "wiederholt_neu": 0,
            "c_faelle": [], "b_faelle": [], "rot_neu": [], "dauer_alt": [],
            "dauer_neu": [], "tools_alt": [], "tools_neu": [],
        })
        e["faelle"] += 1
        for seite in ("alt", "neu"):
            z = paar.get(seite)
            if not z:
                continue
            e[f"leer_{seite}"] += z["ausgang"] == "leer"
            e[f"fehler_{seite}"] += z["ausgang"] == "fehler"
            e[f"wiederholt_{seite}"] += bool(z.get("leer_wiederholt"))
            if z["ausgang"] in ("gruen", "rot"):
                e[f"dauer_{seite}"].append(z["dauer_s"])
                e[f"tools_{seite}"].append(z["tool_aufrufe"])
        a, n = paar.get("alt", {}).get("ausgang"), paar.get("neu", {}).get("ausgang")
        if n == "rot":
            e["rot_neu"].append((fall, paar["neu"]["befund_art"]))
        # Paare mit einer Leer- oder Fehlerseite fallen aus b/c (R8).
        if a not in ("gruen", "rot") or n not in ("gruen", "rot"):
            e["ausgefallen"] += 1
        elif a == "rot" and n == "gruen":
            e["b"] += 1
            e["b_faelle"].append(fall)
        elif a == "gruen" and n == "rot":
            e["c"] += 1
            e["c_faelle"].append((fall, paar["neu"]["befund_art"], paar["neu"]["befunde"]))
        elif a == n == "gruen":
            e["beide_gruen"] += 1
        else:
            e["beide_rot"] += 1
    for block, e in bloecke.items():
        e["p"] = mcnemar_p(e["b"], e["c"]) if block in MIT_P_WERT else None
    return bloecke


def d13_ausloeser(bloecke: dict) -> bool:
    """Welle 3 Punkt 4 (R3): mind. 2 filter-Faelle neu rot, Laenge nicht mitgezaehlt.

    Die zweite Haelfte ("Tool-Aufrufe steigen spuerbar") bleibt ein Urteil —
    die Zahlen dafuer stehen in der Tabelle.
    """
    rot = [f for f, art in bloecke.get("filter", {}).get("rot_neu", []) if art != "Laenge"]
    return len(rot) >= 2


def _median(werte: list[float]) -> str:
    return f"{statistics.median(werte):.1f}" if werte else "–"


def _mittel(werte: list[int]) -> str:
    return f"{statistics.mean(werte):.1f}" if werte else "–"


def bericht(zeilen: list[dict]) -> str:
    bloecke = auswerten(zeilen)
    kopf = [z for z in zeilen if z.get("typ") == "kopf"]
    uebersprungen = [z for z in zeilen if z.get("typ") == "uebersprungen"]
    aus = []
    if kopf:
        k = kopf[0]
        aus.append(f"Alt: `{k['alt_commit'][:7]}` (Vorlage `{k['vorlage_alt_sha256'][:12]}`, "
                   f"Prompt `{k['prompt_alt_sha256'][:12]}`) · Neu: `{k['neu_commit'][:7]}` "
                   f"(Vorlage `{k['vorlage_neu_sha256'][:12]}`, Prompt `{k['prompt_neu_sha256'][:12]}`)"
                   f" · {len(kopf)} Prozesse, {kopf[0]['zeit']} bis {kopf[-1]['zeit']}\n")
    aus.append("| Block | Fälle | b (neu besser) | c (neu schlechter) | beide grün | beide rot "
               "| ausgefallen | leer alt/neu | Fehler alt/neu | Median s alt/neu | Tools Ø alt/neu | p |")
    aus.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for block in BLOECKE:
        if block not in bloecke:
            continue
        e = bloecke[block]
        p = f"{e['p']:.3f}" if e["p"] is not None else ""
        aus.append(
            f"| `{block}` | {e['faelle']} | {e['b']} | {e['c']} | {e['beide_gruen']} | {e['beide_rot']} "
            f"| {e['ausgefallen']} | {e['leer_alt']}/{e['leer_neu']} | {e['fehler_alt']}/{e['fehler_neu']} "
            f"| {_median(e['dauer_alt'])}/{_median(e['dauer_neu'])} "
            f"| {_mittel(e['tools_alt'])}/{_mittel(e['tools_neu'])} | {p} |"
        )
    aus.append("")
    for block in BLOECKE:
        e = bloecke.get(block)
        if not e:
            continue
        if e["c_faelle"]:
            aus.append(f"**c-Fälle `{block}`** (alt grün, neu rot):")
            aus += [f"- `{f}` — {art}: {'; '.join(b)[:220]}" for f, art, b in e["c_faelle"]]
        if e["b_faelle"]:
            aus.append(f"b-Fälle `{block}`: " + ", ".join(f"`{f}`" for f in e["b_faelle"]))
    aus.append("")
    aus.append(f"D13-Auslöser (≥ 2 `filter`-Fälle neu rot ohne reine Satzzahl): "
               f"{'JA' if d13_ausloeser(bloecke) else 'nein'} — neu rot: "
               + ", ".join(f"`{f}` ({a})" for f, a in bloecke.get("filter", {}).get("rot_neu", [])))
    if uebersprungen:
        aus.append("Übersprungen: " + ", ".join(f"`{z['fall']}`" for z in uebersprungen))
    return "\n".join(aus)


# --- Kampagne --------------------------------------------------------------


def _anker_gruen() -> bool:
    """Die festen Wahrheiten ohne Modell — Vorbedingung vor jedem Block."""
    lauf = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_kundenfeedback_eval.py", "-q", "-k", "anker"],
        cwd=REPO, env={**os.environ, "RUN_KUNDENFEEDBACK_EVAL": "1"},
    )
    return lauf.returncode == 0


def kampagne(protokoll: Path, bloecke: tuple[str, ...]) -> None:
    prozess = 0
    for block in bloecke:
        if not _anker_gruen():
            raise SystemExit(f"anker rot vor Block {block} — Ableitung pruefen, Abbruch")
        von = teile(len(faelle(block)))
        for teil in range(1, von + 1):
            if prozess:
                print(f"# Pause {EVAL_PAUSE_S:.0f} s", flush=True)
                time.sleep(EVAL_PAUSE_S)
            prozess += 1
            subprocess.run(
                [sys.executable, __file__, block, "--teil", f"{teil}/{von}",
                 "--protokoll", str(protokoll), "--prozess", str(prozess)],
                cwd=REPO, check=False,
            )
    print(bericht(_lies(protokoll)))


def _lies(pfad: Path) -> list[dict]:
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("was", help="Block, 'kampagne' oder 'bericht'")
    parser.add_argument("datei", nargs="?", help="Protokoll fuer 'bericht'")
    parser.add_argument("--teil", default="1/1")
    parser.add_argument("--protokoll")
    parser.add_argument("--prozess", default="1")
    parser.add_argument("--bloecke", default=",".join(BLOECKE))
    args = parser.parse_args(argv)

    if args.was == "bericht":
        print(bericht(_lies(Path(args.datei))))
        return
    protokoll = Path(args.protokoll) if args.protokoll else (
        PROTOKOLL_DIR / f"{datetime.date.today().isoformat()}.jsonl"
    )
    if args.was == "kampagne":
        kampagne(protokoll, tuple(args.bloecke.split(",")))
        return
    teil, von = (int(x) for x in args.teil.split("/"))
    fahre_block(args.was, teil, von, protokoll, args.prozess)


if __name__ == "__main__":
    main()
