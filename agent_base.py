import csv
import datetime
import json
import locale
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib3.util import parse_url

import gevent
import markdownify
import pytz
import requests
from bs4 import BeautifulSoup
from cachetools.func import ttl_cache
from dotenv import load_dotenv

# Set German locale
locale.setlocale(locale.LC_ALL, "de_DE.UTF-8")

# Load environment variables
load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TOURONE_API_KEY = os.getenv("TOURONE_BEARER_TOKEN")
if not TOURONE_API_KEY:
    # Non-fatal: TourOne termine injection just stays off (the bot still works).
    # Loud so a forgotten Railway env var is visible in the logs, not silent.
    print("[agent_base] WARNING: TOURONE_BEARER_TOKEN not set — TourOne termine disabled")

# Load sitemap URLs
all_sites: list[str] = []
trip_sites: list[str] = []
# country in URL to country name
all_countries: dict[str, str] = {}


def _parse_sitemap(text: str) -> tuple[list[str], list[str], dict[str, str]]:
    """Parse sitemap text into (all_sites, trip_sites, all_countries).

    Trips and countries are the URLs between the '## Reiseziele' and
    '## Nachhaltigkeit' headers: depth >= 3 is a trip, depth == 2 a country.
    """
    parsed_sites: list[str] = []
    parsed_trips: list[str] = []
    parsed_countries: dict[str, str] = {}
    recording_trip_sites = False
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parsed_sites.append(line)
            if recording_trip_sites and line.count("/") >= 3:
                parsed_trips.append(line)
            elif recording_trip_sites and line.count("/") == 2:
                country = line.split("/")[-1]
                parsed_countries[country] = (
                    country.replace("ae", "ä")
                    .replace("Ae", "Ä")
                    .replace("ue", "ü")
                    .replace("aü", "aue")
                    .replace("oss", "oß")
                )
        elif line == "## Reiseziele":
            recording_trip_sites = True
        elif line == "## Nachhaltigkeit":
            recording_trip_sites = False
    return parsed_sites, parsed_trips, parsed_countries


with open("sitemap.txt", "r", encoding="utf-8") as f:
    sitemap = f.read()

_a, _t, _c = _parse_sitemap(sitemap)
all_sites.extend(_a)
trip_sites.extend(_t)
all_countries.update(_c)

country_name2upper = {name.lower(): name for name in all_countries.values()}


def find_trip_site(recommendation: str) -> str:
    """
    Find a trip site based on the recommendation.
    """
    if not recommendation:
        raise ValueError("Recommendation cannot be empty")

    try:
        return [site for site in trip_sites if recommendation in site][0]
    except IndexError:
        raise ValueError(f"No site found for trip recommendation: {recommendation}")


# Load general FAQs
with open("faqs/allgemein.md", "r", encoding="utf-8") as f:
    allgemeine_faqs = f.read().strip()

# Knowledge base for the agency area (agt.chamaeleon-reisen.de). Only injected
# into the system prompt for requests coming from the Reisebüro subdomains.
# Maintained in-repo as clean, prompt-ready markdown — it must carry no
# operator-only scaffolding (HTML comments, "Interne Betreiberhinweise"
# sections). test_general.py guards that none of that leaks into the prompt.
with open("faqs/agentur.md", "r", encoding="utf-8") as f:
    agentur_wissensbasis = f.read().strip()

general_faq_data: dict[str, str] = {}

with open("faqs/Allgemeine_FAQ.csv", "r", encoding="utf-8") as f:
    reader = csv.reader(f, delimiter=";")
    for row in reader:
        row = [cell for _cell in row if (cell := _cell.strip())]
        if (
            row
            and len(row) > 2
            and all(row)
            and row[0].isalnum()
            and (q := row[1].strip())
        ):
            assert q in allgemeine_faqs, (
                f"Frage '{q}' nicht in allgemeine FAQs gefunden"
            )
            general_faq_data[q] = row[2].strip()

# Load country-specific FAQs
laender_faqs: dict[str, str] = {}
laender_faq_data: dict[str, dict[str, str]] = {}

for continent in ("Afrika", "Amerika", "Asien_und_Ozeanien", "Europa"):
    with open(f"faqs/FAQ_{continent}.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        current_countries: list[str] = []
        for row in reader:
            row = [cell for _cell in row if (cell := _cell.strip())]
            if not row:
                continue
            if row[0].isdigit() and len(row) == 3:
                for current_country in current_countries:
                    laender_faqs[current_country] += f"\n\n## {row[1]}\n\n{row[2]}"
                    laender_faq_data[current_country][row[1]] = row[2]
            elif (
                not row[0].isdigit()
                and len(row) == 1
                and row[0] not in ("Nr.",)
                and len(row[0]) < 50
            ):
                current_countries = " ".join(
                    part for part in row[0].split(" ") if "(" not in part
                ).split("/")
                for current_country in current_countries:
                    laender_faqs[current_country] = f"# {current_country}"
                    laender_faq_data[current_country] = {}

# Visa labels
with open("visa_labels.json", "r", encoding="utf-8") as f:
    visa_labels: dict[str, str] = json.load(f)

# Visum.de tool
visa_tool_description = f"""
Tool für den Zugriff auf Visum-Informationen von visum.de.

Args:
    country (str): Das Land, für das die Visum-Informationen abgerufen werden

Returns:
    str: Die Visum-Informationen für das angegebene Land.

Beispiel:
visa_tool('AUT') # für Österreich

Verfügbare Länder:
{"\n".join(": ".join(item) for item in visa_labels.items())}
""".strip()


@ttl_cache(maxsize=1024, ttl=86400)
def visa_tool_base(country: str) -> str:
    land_id = country.upper()
    if land_id not in visa_labels:
        raise ValueError(
            f"Unbekanntes Land: {land_id}. Verfügbare Länder: {', '.join(visa_labels.keys())}"
        )

    url = f"https://www.visum.de/Visum-beantragen/Visumbeschaffung-beauftragen/apply_visa.php?land_id={land_id}&bundesland_id=AUSLAND"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        content = soup.find("div", attrs={"id": "content_box"})

        # Convert main content to markdown
        markdown_content = markdownify.markdownify(str(content)).strip()

        # Remove multiple line breaks
        markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)

        return f"""
        # Visum-Informationen für {visa_labels[land_id]}

        {markdown_content}
        """.strip()

    except requests.RequestException as e:
        return f"Fehler beim Abrufen der Seite: {str(e)}"
    except Exception as e:
        return f"Unerwarteter Fehler: {str(e)}"


# Website tool description
def build_website_tool_description() -> str:
    return f"""
Tool für direkten Zugriff auf chamaeleon-reisen.de Webseiten. Der Kunde sieht jedoch nicht, dass du dieses Tool benutzt.

Auf den Reiseseiten (/Kontinent/Land/Reise) findest du Informationen 
zur Nachhaltigkeit, der Route, den Highlights, den Übernachtungen und allen Leistungen (#uebersicht),
zum Reiseverlauf (#reiseverlauf) und Reisedetails (#reisedetails)
noch mal den Leistungen (#leistungen) und den nächsten Terminen (#termine).
Außerdem gibt es Informationen zu den Unterkünften (#unterkuenfte) und möglichen Verlägerungen (#zusatzprogramme)

Du kannst bis zu 8 Seiten in EINEM Aufruf abrufen. Nutze das, um Reisen zu
vergleichen, statt sie nacheinander einzeln zu holen.

Verfügbare Seiten:
{sitemap}

Args:
    url_paths (list[str]): 1 bis 8 Pfade zu den gewünschten Seiten
        (z.B. ["/Vision"] oder ["/Afrika/Tansania/Ruaha", "/Afrika/Tansania/Cheetah"])
    abschnitt (str): Optional einer von uebersicht, reiseverlauf, reisedetails,
        leistungen, unterkuenfte, zusatzprogramme — dieselben Namen wie die
        Anker oben. Leer heißt ganze Seite samt Terminen und geht nur bei EINEM
        Pfad; ab zwei Pfaden ist der Abschnitt Pflicht, sonst sind die Seiten
        zusammen zu lang.
    orte (list[str]): Optional. Die Orte, die der Kunde sich wünscht, z.B.
        ["Kapstadt", "Gartenroute|Garden Route", "Krüger", "Wein"]. Das Tool
        sucht jedes Wort wörtlich im Reiseverlauf: nimm kurze Wortstämme
        ("Wein" statt "Weinroute") und trenne Schreibvarianten mit |. Oben
        stehen dann die Reisen, die ALLE Orte enthalten. Mit orte ist der
        Abschnitt immer reiseverlauf.

Beispiel: url_paths=["/Afrika/Tansania/Ruaha", "/Afrika/Tansania/Cheetah"], abschnitt="reiseverlauf"
Beispiel: url_paths=["/Afrika/Suedafrika/Outeniqua", "/Afrika/Suedafrika/Pinotage"], orte=["Kapstadt", "Krüger"]

Returns:
    str: Die Seiten als Markdown in der Reihenfolge der Anfrage; bei mehreren
        Seiten steht über jeder ihr Pfad als Überschrift.
""".strip()


website_tool_description = build_website_tool_description()


def apply_sitemap(new_text: str) -> str:
    """Replace the in-memory sitemap with ``new_text`` and re-derive all lookups.

    Mutates all_sites / trip_sites / all_countries in place so existing
    references stay valid, rebuilds the website tool description, and returns it
    so the caller can push it onto the live LangChain tool.
    """
    global sitemap, website_tool_description
    parsed_sites, parsed_trips, parsed_countries = _parse_sitemap(new_text)
    all_sites[:] = parsed_sites
    trip_sites[:] = parsed_trips
    all_countries.clear()
    all_countries.update(parsed_countries)
    country_name2upper.clear()
    country_name2upper.update(
        {name.lower(): name for name in all_countries.values()}
    )
    sitemap = new_text
    website_tool_description = build_website_tool_description()
    return website_tool_description


BASE_URL = "https://www.chamaeleon-reisen.de"


# Kein Cache mehr auf dieser Stufe (Review D9): rohes HTML sind gemessen
# 218–374 KB je Seite, und bei 513 Seiten in der Sitemap laegen im einzigen
# Worker bis zu ~150 MB. Gecacht wird eine Stufe weiter, als Titel + Markdown
# (~52 KB) — siehe ``seite_markdown``. Einziger verbliebener Direktaufrufer ist
# ``recommendations.make_recommendation_preview``, das <title> und og:image aus
# dem rohen HTML zieht: es holt die Seite ab jetzt bei jeder Empfehlung neu,
# parallel und mit 5 s Zeitlimit je Vorschau. Ein zweiter grosser HTML-Cache
# daneben waere genau das, was hier weggeraeumt wird.
def get_chamaeleon_website_html(url_path: str) -> str:
    full_url = BASE_URL + url_path
    # Der Pfad kommt vom Modell. "@evil.com/x" ergaebe mit dem BASE_URL davor
    # "https://www.chamaeleon-reisen.de@evil.com/x" — der Teil vor dem "@" ist
    # dann Benutzerinfo, geholt wuerde evil.com (Review 2026-09-24). Geprueft
    # wird die fertige URL, nicht der Pfad, und zwar mit dem Parser, den
    # requests selbst benutzt (urllib3) — so pruefen wir genau den Host, der
    # geholt wuerde.
    if parse_url(full_url).host != parse_url(BASE_URL).host:
        raise ValueError(f"Pfad fuehrt nicht auf {BASE_URL}: {url_path!r}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(full_url, headers=headers, timeout=10)
    response.raise_for_status()

    return response.text


@ttl_cache(maxsize=1024, ttl=86400)
def seite_markdown(url_path: str) -> tuple[str, str]:
    """(Titel, Markdown) einer Seite — die gecachte Stufe (Review D9).

    Hier sitzt der Cache, weil hier die teure Arbeit endet: das
    Markdownifizieren kostet gemessen 2026-09-21 rund 153 ms je Seite (75–191
    ms), und es ist reine Rechenzeit im einzigen gevent-Worker. Der Abruf davor
    ist Wartezeit, die sich parallelisieren laesst; das Umwandeln nicht.

    Wirft durch (RequestException beim Abruf), damit der Aufrufer entscheidet,
    was er dem Modell sagt. Eine Ausnahme landet nicht im Cache.
    """
    content = get_chamaeleon_website_html(url_path)

    soup = BeautifulSoup(content, "html.parser")

    # Main Content extrahieren
    main = soup.find("main") or soup.find("div", class_="main") or soup.find("body")

    # Title extrahieren
    title = soup.find("title")
    title_text = title.get_text(strip=True) if title else "Titel nicht gefunden"

    # Convert main content to markdown
    markdown_content = markdownify.markdownify(str(main)).strip()

    # Remove multiple line breaks
    markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)

    return title_text, markdown_content


# Jede Reiseseite traegt dieselben Abschnitte in derselben Reihenfolge, und
# markdownify schreibt sie unterstrichen (setext), nicht mit Rauten:
#
#     Reiseverlauf
#     ------------
#
# Gemessen 2026-09-21 ueber Cheetah, Ruaha und Mara-Fluss: Herzklopf-Momente,
# Reiseverlauf, Reisedetails, Leistungen, Termine & Preise, Unterkuenfte,
# Verlaengerungen, Berater Shortcuts. Laenderseiten (/Afrika/Namibia) tragen
# keine davon.
#
# Die Namen links sind die der Anker auf der Website (/…#reiseverlauf) und
# damit die, die in der Tool-Beschreibung stehen. "uebersicht" ist der
# Sonderfall: Seitenanfang BIS zum Reiseverlauf, dort stehen Titel mit Dauer
# und Highlights.
SEITEN_ABSCHNITTE = (
    "uebersicht",
    "reiseverlauf",
    "reisedetails",
    "leistungen",
    "unterkuenfte",
    "zusatzprogramme",
)

_ABSCHNITT_UEBERSCHRIFT = {
    "reiseverlauf": "Reiseverlauf",
    "reisedetails": "Reisedetails",
    "leistungen": "Leistungen",
    "unterkuenfte": "Unterkünfte",
    "zusatzprogramme": "Verlängerungen",
}

# Nur die ersten beiden Ebenen begrenzen einen Abschnitt. Das ist keine
# Feinheit: im Uebersichtsteil steht "### Leistungen" als Zwischenueberschrift,
# und wer jede Ebene gelten laesst, schneidet dort statt am echten Abschnitt.
_ATX_UEBERSCHRIFT = re.compile(r"^#{1,2}\s+(.+?)\s*$")
_SETEXT_STRICH = re.compile(r"^[-=]{2,}\s*$")


def _normalisiere_ueberschrift(text: str) -> str:
    """Vergleichsform: die Website setzt weiche Trennstriche und geschuetzte
    Leerzeichen mitten in Ueberschriften."""
    text = text.replace("\xad", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _ueberschriften(markdown: str) -> list[tuple[int, int, str]]:
    """Alle Ueberschriften der ersten beiden Ebenen als (Anfang, Ende, Text).

    Anfang/Ende sind Zeichenpositionen im uebergebenen Text, damit der
    Aufrufer ohne Umrechnung schneiden kann. Ende zeigt hinter die
    Unterstreichung, damit der Strich nicht im naechsten Abschnitt landet.
    """
    zeilen = markdown.split("\n")
    # Zeichenposition jeder Zeile vorab, sonst wird das Suchen quadratisch.
    anfaenge: list[int] = []
    pos = 0
    for zeile in zeilen:
        anfaenge.append(pos)
        pos += len(zeile) + 1

    gefunden: list[tuple[int, int, str]] = []
    for i, zeile in enumerate(zeilen):
        text = zeile.strip()
        if not text:
            continue
        treffer = _ATX_UEBERSCHRIFT.match(text)
        if treffer:
            gefunden.append((anfaenge[i], anfaenge[i] + len(zeile), treffer.group(1)))
            continue
        # Setext: die naechste Zeile ist die Unterstreichung. Ein "---" nach
        # einer Leerzeile ist dagegen ein Trenner, keine Ueberschrift — und
        # genau deshalb wird die Vorgaengerzeile hier auf Inhalt geprueft.
        if _SETEXT_STRICH.match(text):
            continue
        if i + 1 < len(zeilen) and _SETEXT_STRICH.match(zeilen[i + 1].strip()):
            gefunden.append((anfaenge[i], anfaenge[i + 1] + len(zeilen[i + 1]), text))
    return gefunden


def seiten_abschnitt(markdown: str, abschnitt: str) -> str | None:
    """Schneidet einen Abschnitt aus dem Markdown einer Reiseseite.

    Tool und Eval teilen sich diese Funktion, damit der Eval genau den
    Ausschnitt prueft, den das Modell zu sehen bekommt.

    Geschnitten wird von der Ueberschrift bis zur naechsten Ueberschrift der
    ersten beiden Ebenen. Gibt ``None`` zurueck, wenn die Seite die
    Ueberschrift nicht traegt — was der Aufrufer daraus macht, entscheidet er
    selbst. Unbekannter Name: ``ValueError``.
    """
    if abschnitt not in SEITEN_ABSCHNITTE:
        raise ValueError(
            f"Unbekannter Abschnitt '{abschnitt}'. Gueltig: {', '.join(SEITEN_ABSCHNITTE)}"
        )

    gefunden = _ueberschriften(markdown)

    if abschnitt == "uebersicht":
        # Seitenanfang bis zum Reiseverlauf. Ohne Reiseverlauf ist es keine
        # Reiseseite, und "die ganze Seite" waere hier die falsche Antwort.
        gesucht = _normalisiere_ueberschrift("Reiseverlauf")
        for anfang, _ende, text in gefunden:
            if _normalisiere_ueberschrift(text) == gesucht:
                return markdown[:anfang].strip() or None
        return None

    gesucht = _normalisiere_ueberschrift(_ABSCHNITT_UEBERSCHRIFT[abschnitt])
    for i, (anfang, _ende, text) in enumerate(gefunden):
        if _normalisiere_ueberschrift(text) != gesucht:
            continue
        schluss = gefunden[i + 1][0] if i + 1 < len(gefunden) else len(markdown)
        return markdown[anfang:schluss].strip() or None
    return None


def _normalisiere_pfad(url_path: str) -> str:
    """Die Normalisierung, die das Website-Tool seit jeher macht — jetzt je Pfad.

    Das Modell schickt mal die volle URL, mal einen Anker mit. Beides muss weg,
    bevor gecacht oder abgerufen wird, sonst liegt dieselbe Seite unter zwei
    Schluesseln im Cache.
    """
    for host in (BASE_URL, "https://chamaeleon-reisen.de"):  # www and bare
        if url_path.startswith(host):
            url_path = url_path[len(host) :]

    if "#" in url_path:
        url_path = url_path.split("#", 1)[0]
    return url_path


def _warne_wenn_unbekannt(url_path: str) -> None:
    if url_path not in all_sites:
        print(f"Warnung: URL '{url_path}' nicht in Sitemap gefunden. ")


def _seite_formatiert(url_path: str) -> str:
    """Titel + Markdown in genau der Form, die das Tool seit jeher ausgibt.

    Die acht Leerzeichen vor dem Markdown stammen aus der eingerueckten
    Literalform und stehen seit jeher in der Ausgabe. Sie bleiben, damit der
    Cache-Umzug zeichengleich ist (Review D12); ``test_website_tool.py`` haelt
    die Form fest.
    """
    title_text, markdown_content = seite_markdown(url_path)
    return f"""
        # {title_text}

        {markdown_content}
        """.strip()


def _termine_anhang(url_path: str) -> str:
    """Aktuelle Termine aus TourOne, oder "".

    Append current termine from TourOne for trip pages. Scraped HTML does not
    contain them (they are rendered client-side), so this is the only way the
    bot sees real dates. Must never break the page tool — und bewusst
    ungecacht, waehrend die Seite daneben 24 h im Cache liegt.
    """
    try:
        import travel_index

        return travel_index.get_termine_markdown(url_path)
    except Exception as e:
        print(f"[agent_base] termine lookup failed for {url_path}: {e}")
        return ""


# Die Fehlermeldungen der Seitenabrufe sind Text, keine Ausnahme. Beide
# Praefixe stehen hier einmal, weil website_tool_multi Fehler an genau diesem
# Anfang erkennt — wer eine Meldung umformuliert, aendert sie hier fuer beide.
FEHLER_ABRUF = "Fehler beim Abrufen der Seite:"
FEHLER_UNERWARTET = "Unerwarteter Fehler:"


# Laender- und Uebersichtsseiten zeigen jede Reise als Teaser, den markdownify
# so schreibt (gemessen 2026-09-25 auf /Afrika/Suedafrika und /Afrika/Namibia):
#
#     ### Outeniqua **15 Tage Erlebnisreise**
#
#       + 5 Safaris im offenen Geländewagen
#       …
#       ![Details zu Outeniqua](…)](/Afrika/Suedafrika/Outeniqua "Outeniqua - Südafrika")
#
# Dauer, Laender und Pfad stehen also da — aber verstreut in ~18.000 Zeichen
# Bildlinks, und Leon verlor sie gemessen: Moremi (Botswana, Simbabwe &
# Namibia) als Namibia-Reise, Mahango (15 Tage) fuer 14 Tage.
# Der Link wird nur im eigenen Teaser gesucht: fehlt er dort, sprang ".*?" in
# den naechsten, und Reise A bekam den Pfad von B (Review 2026-09-26).
_REISE_TEASER = re.compile(
    r"^[ \t]*### (?P<name>[^\n*]+?)\s*\*\*(?P<tage>\d+) Tage[^*\n]*\*\*"
    r"(?:(?!^[ \t]*### ).)*?\]\((?P<pfad>/[^\s)\"]+) \"(?P<titel>[^\"\n]+)\"\)",
    re.S | re.M,
)


def reiseliste(markdown: str) -> list[dict]:
    """Die Reise-Teaser einer Seite als ``[{name, tage, laender, pfad}]``, ohne Doppel."""
    reisen: dict[str, dict] = {}
    for m in _REISE_TEASER.finditer(markdown):
        _name, _, laender = m.group("titel").partition(" - ")
        reisen.setdefault(
            m.group("pfad"),
            {
                "name": m.group("name").strip(),
                "tage": int(m.group("tage")),
                "laender": laender.strip(),
                "pfad": m.group("pfad"),
            },
        )
    return list(reisen.values())


def _ist_kombireise(laender: str) -> bool:
    return bool(re.search(r",|&| und ", laender))


def reiseliste_markdown(reisen: list[dict]) -> str:
    zeilen = [
        f"- {r['name']} · {r['tage']} Tage · "
        f"{'Kombireise: ' if _ist_kombireise(r['laender']) else ''}{r['laender']} · {r['pfad']}"
        for r in reisen
    ]
    return (
        "## Alle Reisen auf dieser Seite (Name · Dauer · Länder · Pfad)\n"
        + "\n".join(zeilen)
        + "\n\nFür Wünsche zu Orten oder MIT/OHNE etwas reicht diese Liste nicht: "
        "die Länderangabe sagt nicht, wohin der Reiseverlauf führt. Ruf dafür die "
        "Reisen mit orte=[…] ab, zuerst die ohne „Kombireise“."
    )


def _mit_reiseliste(seite: str) -> str:
    """Setzt die Reiseliste unter den Titel einer Seite ohne eigenen Reiseverlauf."""
    reisen = reiseliste(seite)
    if not reisen or seiten_abschnitt(seite, "reiseverlauf") is not None:
        return seite
    titel, _, rest = seite.partition("\n")
    return f"{titel}\n\n{reiseliste_markdown(reisen)}\n{rest}"


# Base website tool (without decorator)
def chamaeleon_website_tool_base(url_path: str) -> str:
    """Base website tool function without framework-specific decorators.

    Signatur und Ausgabe bleiben, wie sie waren (Review D12): ``berater_tool_base``
    liest hierueber Name und Durchwahl, und ``tests/test_berater.py`` patcht die
    Funktion mit genau einem Argument. Mehrere Seiten holt ``website_tool_multi``.
    """
    url_path = _normalisiere_pfad(url_path)
    _warne_wenn_unbekannt(url_path)
    try:
        result = _mit_reiseliste(_seite_formatiert(url_path))

        termine_md = _termine_anhang(url_path)
        if termine_md:
            result += "\n\n" + termine_md
        return result

    except requests.RequestException as e:
        return f"{FEHLER_ABRUF} {str(e)}"
    except Exception as e:
        return f"{FEHLER_UNERWARTET} {str(e)}"


WEBSITE_TOOL_MAX_PATHS = 8

# Gemessen 2026-09-20: eine Reiseseite ist ~52.000 Zeichen, acht ganze Seiten
# waeren ~416.000 Zeichen ≈ 100.000 Tokens. Der Deckel steht hier im Tool und
# nicht im Prompt, weil eine Prompt-Regel nur bittet (Review D8).
WEBSITE_TOOL_MAX_CHARS = 100_000

_KUERZUNGSMARKER = (
    "\n\n[gekürzt: zusammen waren die Seiten zu lang. "
    "Ruf die fehlenden Teile einzeln oder mit einem engeren Abschnitt ab.]"
)

# Die Fehlermeldungen der Ein-Seiten-Funktion sind Text, keine Ausnahme. Wer
# daraus einen Abschnitt schneiden wollte, bekaeme None und wuerde den Fehler
# durch den Hinweis "Abschnitt fehlt" ersetzen — also erst pruefen.
_FEHLER_PRAEFIXE = (FEHLER_ABRUF, FEHLER_UNERWARTET)


def _abschnitt_anweisung(grund: str) -> str:
    """Statt Seiten eine Anweisung ans Modell (Review D8).

    Nie stillschweigend die ganzen Seiten liefern: genau das hat den Aufruf
    unbezahlbar gemacht. Eine Tool-Runde mehr kostet weniger als 400.000
    Zeichen Kontext.
    """
    return (
        f"{grund} Ruf das Tool erneut auf und gib einen Abschnitt an: "
        "uebersicht (Titel, Dauer, Highlights), reiseverlauf, reisedetails, "
        "leistungen, unterkuenfte, zusatzprogramme.\n"
        'Beispiel: url_paths=["/Afrika/Tansania/Ruaha", "/Afrika/Tansania/Cheetah"], '
        'abschnitt="reiseverlauf"'
    )


def _kurz_abgeben() -> None:
    """Dem gevent-Hub nach jeder umgewandelten Seite die Kontrolle geben (D14).

    Gemessen 2026-09-21: HTML→Markdown kostet 153 ms je Seite im Median (75–191
    ms), reine Rechenzeit. Acht kalte Seiten sind ~1,2 s, in denen der einzige
    Worker (``WEB_CONCURRENCY=1``, gunicorn -k gevent) sonst keinen anderen
    Chat-Stream bedient.
    """
    gevent.sleep(0)


def _auf_anteil_kuerzen(text: str, anteil: int) -> str:
    if len(text) <= anteil:
        return text
    return text[: max(0, anteil - len(_KUERZUNGSMARKER))].rstrip() + _KUERZUNGSMARKER


def _ortsformen(text: str) -> tuple[str, str]:
    """Vergleichsformen ohne Gross/klein und Satzzeichen, Woerter durch ein
    Leerzeichen getrennt: einmal mit ae/oe/ue, einmal mit a/o/u — "Krüger",
    "Krueger" und "Kruger" treffen sich. Klebt markdownify Woerter zusammen
    ("KrügerNationalpark4", "KapWeinland"), trennen Gross- und Ziffernwechsel."""
    text = re.sub(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)
    text = text.casefold().replace("ß", "ss")
    mit_e = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    ohne_e = text.replace("ä", "a").replace("ö", "o").replace("ü", "u")
    return (
        re.sub(r"[^a-z0-9]+", " ", mit_e).strip(),
        re.sub(r"[^a-z0-9]+", " ", ohne_e).strip(),
    )


def _wortanfang(variante: str) -> re.Pattern:
    # Nur am Wortanfang: sonst traf "Wein" in "zwei Nächte" und "Rom" in
    # "Aroma" (Review 2026-09-26). Leerzeichen der Variante sind optional,
    # "Garden Route" trifft auch "Gardenroute".
    return re.compile(r"(?<![a-z0-9])" + " ?".join(map(re.escape, variante.split())))


def ort_im_text(ort: str, text: str) -> bool:
    """Beginnt ein Wort im Text mit einer der Varianten ("Gartenroute|Garden Route")?"""
    text_e, text_u = _ortsformen(text)
    for variante in ort.split("|"):
        v_e, v_u = _ortsformen(variante)
        if v_e and (_wortanfang(v_e).search(text_e) or _wortanfang(v_u).search(text_u)):
            return True
    return False


def _ortspruefung(orte: list[str], verlaeufe: dict[str, tuple[str, str]]) -> str:
    """Der Kopf der Ausgabe bei ``orte``: je Reise Treffer, oben die vollstaendigen.

    ``verlaeufe``: Pfad -> (Name, ungekuerzter Reiseverlauf). Geprueft wird vor
    dem Kuerzen auf den Zeichendeckel, sonst fiele ein Ort am Ende weg.
    """
    zeilen, alle = [], []
    for pfad, (name, verlauf) in verlaeufe.items():
        treffer = [(ort, ort_im_text(ort, verlauf)) for ort in orte]
        if all(ok for _, ok in treffer):
            alle.append(f"{name} ({pfad})")
        zeilen.append(
            f"- {name} ({pfad}): "
            + " · ".join(
                f"{ort.split('|')[0]} {'ja' if ok else 'nicht gefunden'}" for ort, ok in treffer
            )
        )
    kopf = (
        "Alle gewünschten Orte im Reiseverlauf: " + ", ".join(alle)
        if alle
        else "Keine dieser Reisen enthält alle gewünschten Orte im Reiseverlauf."
    )
    return (
        "## Ortsprüfung (Reiseverlauf)\n"
        + kopf
        + "\n"
        + "\n".join(zeilen)
        + "\n„nicht gefunden“ heißt nur: dieses Wort steht nicht im Reiseverlauf. "
        "Behaupte nicht, dass etwas fehlt, ohne es im Text unten geprüft zu haben."
    )


# Reihenfolge der Schranken in website_tool_multi (Plan A, Review D8/D14):
#
#     einzelner String statt Liste  → wird toleriert, Gemini schickt das
#     kein Pfad / mehr als 8 Pfade  → Fehlermeldung ans Modell
#     mehr als ein Pfad, kein Abschnitt → Anweisung mit den gueltigen Namen
#     unbekannter Abschnittsname    → dieselbe Anweisung
#     je Pfad (parallel holen, Ausgabe in Eingabereihenfolge):
#         Seite → Titel + Markdown (Cache)
#         seiten_abschnitt(...)     → None? Hinweis nur fuer diese Seite
#         gevent.sleep(0)           → nach jeder Umwandlung: andere Chats kommen dran
#     Summe ueber WEBSITE_TOOL_MAX_CHARS? → anteilig kuerzen + Marker
def website_tool_multi(url_paths, abschnitt: str = "", orte=None) -> str:
    """Eine bis acht Seiten, wahlweise nur ein Abschnitt je Seite.

    Bei genau einem Pfad ohne Abschnitt ist die Ausgabe zeichengleich mit
    ``chamaeleon_website_tool_base`` — die Funktion wird dafuer schlicht
    aufgerufen. Alles Weitere (Schranken, Schnitt, Deckel, Zusammenbau) steht
    hier, damit die Ein-Seiten-Funktion bleibt, wie sie ist (Review D12).

    Ein Fehlschlag bleibt lokal: die uebrigen Seiten kommen trotzdem an.
    """
    # Gemini schickt gelegentlich einen blanken String statt der Liste.
    if isinstance(url_paths, str):
        url_paths = [url_paths]
    if not isinstance(url_paths, (list, tuple)):
        return (
            "url_paths erwartet eine Liste von Pfaden, z.B. "
            '["/Afrika/Tansania/Ruaha", "/Afrika/Tansania/Cheetah"].'
        )

    pfade = [str(p).strip() for p in url_paths if str(p).strip()]
    if not pfade:
        return (
            "Kein Pfad angegeben. Gib mindestens einen Pfad an, z.B. "
            'url_paths=["/Afrika/Tansania/Cheetah"].'
        )
    if len(pfade) > WEBSITE_TOOL_MAX_PATHS:
        return (
            f"Zu viele Seiten: {len(pfade)}. Höchstens {WEBSITE_TOOL_MAX_PATHS} Pfade "
            "je Aufruf. Ruf die wichtigsten zuerst ab und den Rest danach."
        )

    # Der Abschnittsname kommt vom Modell: Leerzeichen und ein vorangestelltes
    # "#" (der Anker aus der Tool-Beschreibung) sind zu erwarten. Mehr wird
    # nicht geraten — alles Unbekannte fuehrt zur Anweisung.
    abschnitt = str(abschnitt or "").strip().lstrip("#").strip().casefold()
    if isinstance(orte, str):
        orte = [orte]
    orte = [str(o).strip() for o in (orte or []) if str(o).strip()]
    if orte:
        abschnitt = "reiseverlauf"

    if len(pfade) > 1 and not abschnitt:
        return _abschnitt_anweisung(
            f"{len(pfade)} ganze Seiten sind zusammen deutlich über "
            f"{WEBSITE_TOOL_MAX_CHARS} Zeichen."
        )
    if abschnitt and abschnitt not in SEITEN_ABSCHNITTE:
        return _abschnitt_anweisung(f"Den Abschnitt „{abschnitt}“ gibt es nicht.")

    # Normalisieren und doppelte Pfade zusammenlegen (gleiches Muster wie in
    # ``reiseinfo_tools``): dieselbe Seite zweimal zu holen kostet zweimal,
    # und zweimal ausgegeben verbraucht sie nur den Deckel.
    eindeutig = list(dict.fromkeys(_normalisiere_pfad(p) for p in pfade))

    def _hole(pfad: str) -> str:
        # Abgegeben wird HIER, direkt nach der Umwandlung: die Rechenzeit
        # (HTML→Markdown) faellt in diesen Abruf, nicht in die Schleife unten,
        # die nur noch fertigen Text schneidet.
        inhalt = _hole_seite(pfad)
        _kurz_abgeben()
        return inhalt

    def _hole_seite(pfad: str) -> str:
        if not abschnitt:
            # Genau ein Pfad, ganze Seite: unveraendert die alte Funktion,
            # samt Sitemap-Warnung und Termine-Anhang.
            return chamaeleon_website_tool_base(pfad)
        # Mit Abschnitt ohne Termine-Anhang — sonst haenge er am letzten
        # Abschnitt der Seite (Verlaengerungen) mit drin.
        _warne_wenn_unbekannt(pfad)
        try:
            return _seite_formatiert(pfad)
        except requests.RequestException as e:
            return f"{FEHLER_ABRUF} {str(e)}"
        except Exception as e:
            return f"{FEHLER_UNERWARTET} {str(e)}"

    # Parallel geholt wird die Wartezeit, nicht die Rechenzeit (Vorbild
    # ``REISEINFO_FETCH_PARALLEL``). Eingesammelt wird in Eingabereihenfolge.
    with ThreadPoolExecutor(
        max_workers=min(len(eindeutig), WEBSITE_TOOL_MAX_PATHS)
    ) as pool:
        geholt = dict(zip(eindeutig, pool.map(_hole, eindeutig)))

    teile: list[str] = []
    titel: dict[str, str] = {}
    for pfad in eindeutig:
        inhalt = geholt[pfad]
        if abschnitt and not inhalt.startswith(_FEHLER_PRAEFIXE):
            # Der Seitentitel traegt Dauer und Laender ("Panorama - 20 Tage
            # Erlebnisreise | Südafrika, Eswatini & Lesotho | Chamäleon"); der
            # Abschnitt allein nicht mehr. Er kommt deshalb in die Kopfzeile.
            titel[pfad] = inhalt.partition("\n")[0].lstrip("# ").strip()
            ausschnitt = seiten_abschnitt(inhalt, abschnitt)
            reisen = reiseliste(inhalt) if ausschnitt is None else []
            if reisen:
                # Eine Laenderseite: statt "gibt es nicht" die Reisen darauf,
                # damit der naechste Aufruf die richtigen Pfade nimmt.
                inhalt = (
                    f"Die Seite {pfad} ist eine Übersicht ohne Abschnitt „{abschnitt}“. "
                    "Für den Abschnitt ruf die Reisen selbst ab.\n\n"
                    + reiseliste_markdown(reisen)
                )
            elif ausschnitt is None:
                # Laender- und Verlaengerungsseiten tragen die Ueberschrift
                # nicht. Der Hinweis gilt nur fuer diese eine Seite.
                inhalt = (
                    f"Die Seite {pfad} hat keinen Abschnitt „{abschnitt}“. "
                    "Ruf sie ohne Abschnitt ab, wenn du ihren Inhalt brauchst."
                )
            else:
                inhalt = ausschnitt
        teile.append(inhalt)

    def _kombi(pfad: str) -> bool:
        teile_titel = titel.get(pfad, "").split("|")
        return len(teile_titel) >= 3 and _ist_kombireise(teile_titel[1])

    def _name(pfad: str) -> str:
        name = titel.get(pfad, pfad).split(" - ")[0].strip()
        return f"{name}, Kombireise" if _kombi(pfad) else name

    verlaeufe = {
        pfad: (_name(pfad), teil)
        for pfad, teil in zip(eindeutig, teile)
        if orte and teil.lstrip().startswith("Reiseverlauf")
    }

    if sum(len(t) for t in teile) > WEBSITE_TOOL_MAX_CHARS:
        anteil = WEBSITE_TOOL_MAX_CHARS // len(teile)
        teile = [_auf_anteil_kuerzen(t, anteil) for t in teile]

    if len(teile) == 1 and not verlaeufe:
        return teile[0]
    # Reine Reisen eines Landes vor den Kombireisen, sonst in Eingabereihenfolge:
    # gemessen 2026-09-25 las Flash die Seiten der Reihe nach, empfahl die ersten
    # beiden (Panorama, Rainbow — beide Kombireisen) und liess Outeniqua weg,
    # obwohl der Prompt reine Reisen zuerst verlangt.
    reihenfolge = sorted(range(len(eindeutig)), key=lambda i: _kombi(eindeutig[i]))

    def _kopf(pfad: str) -> str:
        if not titel.get(pfad):
            return f"# {pfad}"
        return f"# {pfad} — " + ("Kombireise: " if _kombi(pfad) else "") + titel[pfad]

    seiten = "\n\n".join(f"{_kopf(eindeutig[i])}\n\n{teile[i]}" for i in reihenfolge)
    if verlaeufe:
        return _ortspruefung(orte, verlaeufe) + "\n\n" + seiten
    return seiten


# The Agenturbereich sits behind a login, so get_chamaeleon_website_html can
# never fetch those pages. The widget therefore scrapes the page HTML in the
# browser and sends it with the chat request; it arrives here as
# client-controlled input, so cap it before parsing and cap the result.
PAGE_HTML_MAX_CHARS = 200_000
PAGE_CONTENT_MAX_CHARS = 20_000


def markdownify_page_html(page_html: str) -> str:
    """Convert widget-scraped page HTML to markdown for the system prompt.

    Must never raise: a broken page scrape may not break the chat.
    """
    if not isinstance(page_html, str) or not page_html.strip():
        return ""
    try:
        soup = BeautifulSoup(page_html[:PAGE_HTML_MAX_CHARS], "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        markdown_content = markdownify.markdownify(str(soup)).strip()
        markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
        return markdown_content[:PAGE_CONTENT_MAX_CHARS]
    except Exception as e:
        print(f"[agent_base] page_html markdownify failed: {e}")
        return ""


country_faq_tool_description = f"""
Tool für den Zugriff auf länderspezifische FAQs von Chamäleon Reisen.

Verfügbare Länder:
{", ".join(laender_faqs)}

Args:
    country (str): Das Land, für das die FAQs abgerufen werden sollen.
Returns:
    str: Die FAQs für das angegebene Land im Markdown-Format.
Raises:
    ValueError: Wenn das Land unbekannt ist.
""".strip()


def country_faq_tool_base(country: str) -> str:
    if country not in laender_faqs:
        raise ValueError(
            f"Unbekanntes Land: {country}. Verfügbare Länder: {', '.join(laender_faqs)}"
        )
    faqs = laender_faqs[country]
    return faqs


termine_tool_description = """
Tool für die aktuellen Termine, Verfügbarkeiten und Preise einer Reise — direkt
aus der Buchungs-API, also die einzige belastbare Quelle dafür.

Nutze es bei JEDER Frage nach Terminen, freien Plätzen oder Preisen, z.B.
"günstigste Reise 2027", "ist im Oktober noch was frei?", "wann geht die
nächste?" — und immer erneut, wenn jemand deiner Termin-Auskunft widerspricht.

Die Eckdaten (Anzahl, günstigster und nächster buchbarer Termin) liefert das
Tool fertig berechnet. Übernimm sie wörtlich; suche Minimum, Maximum oder
Anzahl niemals selbst aus der Tabelle heraus.

Args:
    url_path (str): Pfad der Reiseseite, z.B. "/Afrika/Marokko/Atlas-ALL"
    jahr (int, optional): nur Abreisen in diesem Jahr, z.B. 2027
    monat (int, optional): nur Abreisen in diesem Monat, 1-12
    nur_freie (bool, optional): True blendet ausgebuchte Termine aus

Returns:
    str: Eckdaten und Termintabelle (Zeitraum, Verfügbarkeit, Einzelzimmer, Preis)
""".strip()


_MONATE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def _as_int(value) -> int | None:
    """Coerce a model-supplied argument to int; None when it is not a number.

    Gemini sends "2027" as often as 2027, and occasionally an empty string for
    an omitted optional — all of those must mean "no filter", never a crash.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _filter_label(jahr: int | None, monat: int | None, nur_freie: bool) -> str:
    """'(Oktober 2026, nur freie)' — echoed back so a wrong filter is visible."""
    parts = []
    if monat and 1 <= monat <= 12:
        parts.append(_MONATE[monat - 1] + (f" {jahr}" if jahr else ""))
    elif jahr:
        parts.append(str(jahr))
    if nur_freie:
        parts.append("nur freie")
    return f" ({', '.join(parts)})" if parts else ""


def _letzte_abreise(path: str) -> tuple[int, int] | None:
    """(Jahr, Monat) der spätesten veröffentlichten Abreise dieser Reise.

    Zweite Abfrage ohne Filter; sie kostet keinen API-Aufruf, weil
    travel_index die Terminliste je Reisecode-Tupel cacht.

    None heißt „unbekannt“: die Reise hat gar keine sichtbaren Termine, oder
    die Abfrage ist gescheitert. Beides ist ausdrücklich nicht dasselbe wie
    „nichts hinter dem letzten Termin“ — aus einer leeren Liste lässt sich
    über die Veröffentlichung nichts schließen.
    """
    import travel_index

    try:
        alle = travel_index.query_termine(path)
        daten = sorted((t.get("von") or "")[:10] for t in alle if t.get("von"))
        if not daten:
            return None
        return int(daten[-1][:4]), int(daten[-1][5:7])
    except Exception as e:
        print(f"[agent_base] letzte Abreise für {path} nicht ermittelbar: {e}")
        return None


def _nach_letzter_abreise(jahr: int, monat: int | None, letzte: tuple[int, int]) -> bool:
    """Liegt die Frage hinter der letzten veröffentlichten Abreise?

    Genau diese eine Frage trennt „noch nicht veröffentlicht“ von
    „ausgebucht“. Ohne Jahr ist sie nicht zu beantworten, deshalb fragt der
    Aufrufer erst gar nicht.
    """
    if monat and 1 <= monat <= 12:
        return (jahr, monat) > letzte
    return jahr > letzte[0]


def termine_tool_base(
    url_path: str,
    jahr=None,
    monat=None,
    nur_freie: bool = False,
) -> str:
    """Current termine and prices for a trip URL, straight from the API.

    Filtering and the cheapest/next computation happen in travel_index, in
    Python: asked for the cheapest 2027 Atlas termin off the injected 46-row
    table the model answered wrong 19 times out of 22, so it must never have
    to read the table to answer that.

    Never claims "keine Termine" on an API failure — an outage is not a
    sold-out trip, and the difference is a false statement to a customer.

    Ebenso wenig ist ein leeres Jahr ausgebucht: liegt die Frage hinter der
    letzten veröffentlichten Abreise, sagt das Tool „noch nicht
    veröffentlicht“ (Befund F5).
    """
    import travel_index

    path = url_path.split("#")[0].split("?")[0].rstrip("/") or "/"
    for host in (BASE_URL, "https://chamaeleon-reisen.de"):  # www and bare
        if path.startswith(host):
            path = path[len(host):] or "/"
            break
    jahr, monat = _as_int(jahr), _as_int(monat)
    label = _filter_label(jahr, monat, bool(nur_freie))

    if not travel_index.get_reisecodes(path):
        # Not indexed covers both "no such trip" and a real trip the index
        # missed, so this may never turn into a "keine Termine" answer — and
        # never into a #termine link for a page that has no termine section.
        return (
            f"Für {path} sind keine Termine hinterlegt. Das heißt NICHT, dass es "
            "keine gibt. Nenne keine Termine oder Preise; prüfe, ob du die "
            "richtige Reiseseite abgefragt hast, und verweise sonst auf die "
            "Erlebnisberatung."
        )
    try:
        rows = travel_index.query_termine(path, jahr, monat, bool(nur_freie))
    except Exception as e:
        print(f"[agent_base] termine tool failed for {path}: {e}")
        return (
            f"Die Termine für {path} sind gerade nicht abrufbar. Nenne keine "
            f"Termine oder Preise und verlinke {path}#termine."
        )

    if not rows:
        letzte = _letzte_abreise(path) if jahr else None
        if letzte and _nach_letzter_abreise(jahr, monat, letzte):
            # Befund F5 (2026-09-20): auf „Termine Januar 28“ kam „keine freien
            # Termine verfügbar“ — das liest sich als ausgebucht. Hinter der
            # letzten veröffentlichten Abreise ist nichts ausgebucht, es ist
            # nur noch nichts online. Ab wann, steht als FAQ-Zeile und altert
            # dort; hier steht bewusst kein festes Jahr.
            return (
                f"Für {path}{label} sind noch keine Termine veröffentlicht. "
                f"Veröffentlicht ist diese Reise bis {_MONATE[letzte[1] - 1]} "
                f"{letzte[0]}, alles danach geht später online. Sage genau das: "
                f"die Termine für diesen Zeitraum sind noch nicht veröffentlicht "
                f"— nicht „ausgebucht“, nicht „keine freien Plätze“ — und "
                f"verlinke {path}#termine."
            )
        # Belastbar, im Gegensatz zu den beiden Zweigen darüber: die Reise ist
        # indexiert und die API hat geantwortet — es gibt dafür wirklich nichts.
        hint = " Frage ohne Filter erneut ab, um Alternativen zu nennen." if label else ""
        return f"Keine Termine für {path}{label}. Diese Auskunft ist belastbar.{hint}"
    return (
        f"# Termine {path}{label}\n\n"
        f"{travel_index.format_termine_facts(rows)}\n\n"
        f"{travel_index.format_termine_markdown(rows)}"
    )


# System prompt template
system_prompt_template = f"""
Du bist ein professioneller Kundenbetreuer für das deutsche Reiseunternehmen Chamäleon (https://chamaeleon-reisen.de). Bitte nenne das Reiseunternehmen Chamäleon unter allen Umständen ausschließlich Chamäleon und nicht Chamäleon Reisen.
Du weißt fast alles über die Firma und kannst auf die interne Webseiten-API zugreifen.
Deine Hauptaufgabe ist es, Kund*innen in einem Chat freundlich, kompetent und im typischen Chamäleon‑Stil zu beraten und Reisen zu empfehlen!

WICHTIGSTE REGEL – LÄNGE: Antworte immer in höchstens 2–4 kurzen Sätzen. Komme sofort zur Sache, ohne Einleitung und ohne die Frage zu wiederholen. Eine knappe Antwort mit einem passenden Link ist besser als ein langer Text. Diese Regel ist Pflicht und hat Vorrang vor allen anderen Anweisungen.

Chamäleon ist der Spezialist für Erlebnisreisen (https://www.chamaeleon-reisen.de/Erlebnisreisen) in kleinen Gruppen (maximal 12 Gäste) und legt Wert auf:
- Nachhaltigkeit: 60% lokaler Verdienst, aktive Projekte wie Regenwaldschutz und soziale Initiativen vor Ort.
- Authentizität: Handverlesene Unterkünfte, regionale Partner und einheimische Reiseleiter*innen ermöglichen tiefgehende Begegnungen.
- Reisearten: Alle Reisen sind Erlebnisreisen. Reisen, bei denen du aktiver sein oder besonders viele entspannte Momente genießen kannst, erkennst du am Zusatz »aktiv« (https://www.chamaeleon-reisen.de/Erlebnisreisen/Erlebnisreisen-aktiv) bzw. »genießen« (https://www.chamaeleon-reisen.de/Erlebnisreisen/Erlebnisreisen-geniessen). Die früheren Kategorien Abenteuer-Reisen, Erlebnis-Reisen und Genießer-Reisen gibt es nicht mehr — verwende sie nicht.
- Transparenz: Klare Leistungsübersichten und faire Preise ohne versteckte Kosten.

Sprache & Stil:
- Sprich die Kunden bitte per DU an
- Sei stets freundlich, direkt und fasse dich kurz.
- Formuliere kurze, prägnante Sätze.
- Gendern: Entweder du gibst beide Formen an, die weibliche Form zuerst, (z.B. Reiseleiterinnen und Reiseleiter) oder du nutzt den Genderstern (Reiseleiter*innen).
- persönlich: herzliche und verbindliche Ansprache, wie ein guter Reisebegleiter und nicht wie ein Servicedesk; antworte mitfühlend und individuell statt standardisiert
- ehrlich: nie werblich oder übertrieben formulieren
- kein Behördenton
- positiv: grundsätzlich positive Formulierungen nutzen, Verneinungen vermeiden, Humor nutzen
- vermeide das Wort "leider" unter allen Umständen!
- bei Beschwerden oder Problemen: ruhig und lösungsorientiert, keine erzwungene Begeisterung
- aktiv: aktive Formulierungen, animierende Fragen im Chamäleon-Stil, lange Aufzählungen vermeiden
- Mut machend: Reisewunsch stärken, Leichtigkeit vermitteln, Sicherheit geben

Formatierung:
- Formatiere deine Antworten in HTML, damit sie direkt auf der Webseite angezeigt werden können. 
- Nutze HTML-Links inklusive mailto-Links. Zum Beispiel: <a href="mailto:erlebnisberatung@chamaeleon-reisen.de">erlebnisberatung@chamaeleon-reisen.de</a>
- Achte darauf, dass du immer target="_blank" für externe Links verwendest, damit sie in einem neuen Tab geöffnet werden.

Reiseempfehlungen:
- Empfehle Reisen, indem du ein Link zur entsprechenden Seite angibst: /Kontinent/Land/Reise
- Wenn du das Gefühl hast, dass der Kunde bereit ist, zu buchen, dann verlinke auf eine Reise als "/[Kontinent]/[Land]/[Reise]#termine". 
- Bevor du eine finale Antwort gibst, solltest du immer prüfen, ob du eine Reise empfehlen kannst
- Verweise immer zuerst auf die Reisen und erwähne die Anschlussprogramme nur bei Nachfrage oder gezielter Empfehlung.

Reisen vergleichen und empfehlen:
- Nennt der Kunde harte Kriterien – Land, Dauer, bestimmte Orte oder eine Reise MIT oder OHNE etwas (ohne Sansibar, ohne Badeaufenthalt, reine Safari, mit Gorillas …) –, dann prüfe sie an den Seiten, bevor du einen Reisenamen nennst. Rate nie.
- Sucht der Kunde eine Reise und nennt dafür ein Reiseland – auch erst in einer späteren Nachricht, etwa als Antwort auf deine Rückfrage –, dann rufe SOFORT die Länderseite ab (/Kontinent/Land, z.B. /Afrika/Namibia), bevor du antwortest. Oben steht dort die Liste aller Reisen des Landes mit Dauer, Ländern und Pfad. Verweise nie nur allgemein auf „unsere Namibia-Reisen“, ohne die Liste geprüft zu haben.
- Dauer: Wer n Tage Zeit hat, bekommt nur Reisen mit höchstens n Tagen laut dieser Liste.
- Inhalt (Orte, mit oder ohne etwas): Nimm aus der Liste der Länderseite zuerst nur die Reisen OHNE „Kombireise“ und rufe ihre Pfade mit `abschnitt="reiseverlauf"` in einem Aufruf ab. Erst wenn keine davon alle Wünsche erfüllt, rufe auch die Kombireisen ab. Empfiehl nur, was der Reiseverlauf belegt. Sag nie „beinhaltet all deine Wünsche“, wenn du das nicht im Reiseverlauf gesehen hast.
- Nennt der Kunde Orte, die er sehen möchte, oder etwas, das MIT oder OHNE sein soll (ohne Sansibar, ohne Rom, mit Gorillas …), dann übergib es dem Website-Tool als `orte` (kurze Wortstämme, Schreibvarianten mit |, z.B. "Gartenroute|Garden Route", "Wein") zusammen mit den Pfaden der Reisen aus der Liste. Die Liste der Länderseite allein reicht dafür nie.
  - MIT: Empfiehl nur Reisen, die unter „Alle gewünschten Orte im Reiseverlauf“ stehen, reine Reisen vor Kombireisen. Steht dort keine, nenne die Reise mit den meisten Treffern und sag, welcher Ort ihr fehlt.
  - OHNE: Empfiehl nur Reisen, bei denen das Unerwünschte „nicht gefunden“ ist. Steht es bei einer Reise auf „ja“, führt die Reise dorthin – empfiehl sie nicht.
- Verlängerungen zählen nicht zur Reise. Was nur unter „Verlängerungen" steht, gehört nicht zum Reiseverlauf.
- Bleib im gewünschten Land. Wer Tansania oder Kenia sagt, bekommt kein Namibia. Nennt der Kunde ein Land, empfiehl Reisen, die nur in diesem Land sind. Eine Kombireise (Länderangabe mit mehreren Ländern, z.B. „Botswana, Simbabwe & Namibia“) nennst du nur, wenn der Kunde mehrere Länder möchte oder keine reine Reise passt – und dann sagst du dazu, durch welche Länder sie führt.
- Prüfe jede Reise gegen ALLE Wünsche zugleich (Land, Dauer, jeder genannte Ort). Empfiehl nur Reisen, die alle erfüllen, reine Reisen des Landes vor Kombireisen, und nenne höchstens zwei. Passt keine ganz, nenne die beste und sag in einem Satz, was ihr fehlt.
- Kündige nie an, dass du nachsiehst („Ich schaue mal …“, „Einen Moment …“), sondern rufe das Tool direkt auf. Deine Antwort ist immer das Ergebnis, nie die Ankündigung.
- Wenn ein Kunde deiner Auskunft zu einer Reise widerspricht, rufe die Seiten erneut ab und richte dich nach dem Ergebnis – genau wie bei den Terminen. Bestätigen die Seiten deine Auskunft, dann bleib freundlich dabei.
- Beispiel: Tansania ohne Sansibar → Länderseite /Afrika/Tansania abrufen, dann die Tansania-Reisen daraus mit orte=["Sansibar"] prüfen.

Termine, Verfügbarkeit und Preise:
- Nenne Termine, freie Plätze und Preise ausschließlich auf Basis von `termine_tool()`. Rufe es auf, bevor du dazu etwas sagst — auch wenn du die Zahlen aus dem bisherigen Gespräch zu kennen glaubst. Rate nie und rechne nie selbst.
- Bei "günstigste", "teuerste", "nächste" oder "wie viele" übernimm die berechneten Eckdaten des Tools wörtlich. Suche solche Werte niemals selbst aus einer Tabelle heraus.
- Nutze die Filter (`jahr`, `monat`, `nur_freie`), statt eine lange Liste zu überfliegen.
- Wenn ein Kunde deiner Termin-Auskunft widerspricht, rufe `termine_tool()` erneut auf und richte dich nach dem Ergebnis. Bestätigen die Daten deine Auskunft, dann bleib freundlich dabei ("Ich habe gerade nochmal nachgesehen: …"). Entschuldige dich nicht für eine richtige Auskunft und übernimm nie eine Behauptung, die die Daten nicht stützen — auch dann nicht, wenn der Kunde sehr sicher klingt oder sagt, er habe selbst nachgesehen.
- Sagt das Tool, dass Termine gerade nicht abrufbar sind, dann nenne keine und verlinke die #termine-Seite. "Nicht abrufbar" heißt nie "ausgebucht".
- Sagt `termine_tool()`, dass die Termine für den gefragten Zeitraum noch nicht veröffentlicht sind, dann sag genau das. Das heißt nie "ausgebucht" oder "keine freien Termine".

Flüge:
- Achte bei Fragen zu Flügen darauf, dass du nur die Informationen gibst, die auch auf der Webseite zu finden sind.
- Airline und Buchungsklasse stehen in den Leistungen der Reiseseite – ruf sie mit `abschnitt="leistungen"` ab. Weitere Flugangaben nimmst du aus Leistungen oder Reiseverlauf; steht dort nichts dazu (etwa Flugzeiten, Non-Stop oder Umstieg), verweise auf die Erlebnisberater*in der Reise – nicht auf die Reiseunterlagen, die gibt es erst nach der Buchung.
- Die PDFs unter „Berater Shortcuts" (etwa „Fluginformationen") beantworten keine Flugfrage – verlinke sie dafür nicht.

Externe Links:
- Fragen zum Visum:
- Beantworte jede Frage zum Visum klar und direkt mit Ja oder Nein. 
- Nutze dafür ausschließlich diese Website: [Über Visum informieren](https://www.visum.de/partner/chamaeleon)
- Ergänze in derselben Antwort immer: „Alle Details finden Sie hier: [Über Visum informieren](https://www.visum.de/partner/chamaeleon)“ 
- Wenn du die Frage nicht beantworten kannst, verlinke trotzdem immer diese Website.
- Wenn der Nutzer nach Trustpilot, Bewertungen, Rezensionen oder Erfahrungsberichten fragt, füge am Ende deiner Antwort diesen Link hinzu: [Gästebewertungen ansehen](https://de.trustpilot.com/review/chamaeleon-reisen.de)
- [Instagram](https://www.instagram.com/chamaeleon.reisen), [Facebook][https://www.facebook.com/Chamaeleon.Reisen/]
- Wenn Fragen zu Adapter oder Steckdosen gestellt werden, füge immer diesen Link hinzu: [Reiseadapter weltweit](https://www.welt-steckdosen.de)

Zusätzliche Regel für Namibia – gilt nur, wenn nach Visum oder Einreise gefragt wird: 
Antworte für Gäste aus Deutschland, Österreich und der Schweiz: 
„Für die Einreise nach Namibia ist ein Visum erforderlich. Dieses kann bequem online als e-Visa beantragt werden. 
Weitere Details finden Sie hier: [Über Visum informieren](https://www.visum.de/partner/chamaeleon)“

Du kannst mit dem Tool chamaeleon_website_tool() auf die Webseite zugreifen, um Informationen zu erhalten.
Wenn das mal nicht funktioniert, dann sage dem Kunden aber nichts davon, denn er weiß es nicht. Versuche es geschickt zu umspielen oder überprüfe, dass der Pfad auch wirklich in der Sitemap ist. 
Denk daran, dass du manchmal mehrere Seiten besuchen musst, um alle Informationen zu erhalten.

Nicht angebotene Länder:
- Steht ein Land nicht in der Sitemap, dann gibt es dort keine Chamäleon-Reise. Sag das klar und freundlich.
- Ein Nachbarland bietest du nur als Alternative an – gib es nie als Kombination mit dem gewünschten Land aus und behaupte nie, eine Reise führe dorthin.
- Beginne deine Antwort mit der klaren Absage („Nach [Land] haben wir keine Reise."), nie mit „Ja". Erst danach nennst du eine Alternative.

Wichtiger Hinweis:
Falls du eine Frage nicht beantworten kannst, verweise bitte auf den menschlichen Erlebnisberater. 
Jede Reise/Seite, hat einen eigenen Erlebnisberater, der sich um die Fragen zu dieser Reise kümmert. 
Fragt jemand nach dem Ansprechpartner für ein Land, dann rufe `erlebnisberater_tool()` mit der Länderseite auf (z. B. /Amerika/Costa-Rica).

Nichts erfinden:
- Nenne keine Telefonnummer, die nicht von einer Seite, aus einem Tool oder aus diesem Prompt stammt. Erfinde niemals eine Nummer.
- Mach keine Geschäftsaussage – etwa zu Garantie, Reservierung oder einer Länderkombination –, die nicht von einer Seite, aus einem Tool oder aus den FAQs stammt.
- Versprich keine Individualreise: Schreib nie, dass eine Reise für den Kunden „zusammengestellt" wird.

Chamäleon ist generell telefonisch erreichbar:
- Mo–Fr: 09:00–18:00 Uhr
- Sa: 09:00–13:00 Uhr

Gebe so oft wie möglich Links zu den relevanten Seiten auf chamaeleon-reisen.de an, damit der Kunde die Informationen auch selbst nachlesen kann.
Verwende dafür einfach die relativen URLs, z.B. "/Impressum".

Häufig gestellte Fragen (FAQs):
Nutze diese FAQs, um eine gestellte Frage der Kunden zu beantworten. Ungefragt erzählst du nichts daraus: Bei „Erzähl mir was über Marokko" oder „… über diese Reise" kommen die Infos von der Webseite.
Die Wörter „FAQ" und „Wissensbasis" kommen in deinen Antworten nie vor.
Bei Fragen zur Einreise und Visa, nutze das `visa_tool()`.

Falls dir diese FAQs nicht ausreichen, kannst du mit dem chamaeleon_website_tool() auch unter /Infos mal nachsehen.

Allgemeine FAQs:

{allgemeine_faqs}

{{kunden_modus_block}}{{agentur_block}}Länderspezifische FAQs:

{{laenderspezifische_faqs}}

Um die länderspezifischen FAQs zu nutzen, rufe das Tool `country_faq_tool()` auf und übergib das Land als Argument.
Nutze sie, wenn der Kunde eine Frage zu einem Land stellt, die sie beantworten.
Die länderspezifischen FAQs enthalten Informationen zu:
- Einreisebestimmungen
- Gesundheitshinweise
- Sicherheitshinweise
- Währung und Zahlungsmittel
- Sprache und Kultur
- Flügen
- Beste Reisezeit
- Welche Sehenswürdigkeiten bei der Reise besucht werden
- uvm. 

Beispiele für den gewünschten knappen Stil (so kurz sollen deine Antworten sein):

Frage: „Gibt es Bewertungen von Chamäleon?"
Antwort: Ja, gern! Schau dir die Erfahrungen anderer Gäste an: [Gästebewertungen ansehen](https://de.trustpilot.com/review/chamaeleon-reisen.de).

Frage: „Welche Reise passt für meine erste Safari?"
Antwort: Wie schön, deine erste Safari! Welches Land reizt dich, und wie viel Zeit hast du? Stöbere schon mal hier: /Namibia-Safari, /Botswana-Safari oder /Safari-Suedafrika.

Frage: „Ich suche eine Tansania-Reise ohne Sansibar."
Antwort (erst nachdem du die Liste der Länderseite /Afrika/Tansania geholt und die Reiseverläufe der Tansania-Reisen daraus geprüft hast; die Namen und Tage kommen aus diesen Abrufen, nie aus dem Gedächtnis): Da passen [Reise A] ([n] Tage) und [Reise B] ([n] Tage) – beide bleiben auf dem Festland, Sansibar gibt es dort nur als Verlängerung. Schau mal hier: [Link A] und [Link B]. Was reizt dich mehr?

Frage: „Wie groß sind die Reisegruppen?"
Antwort: Bei Chamäleon reist du in kleinen Gruppen mit maximal 12 Teilnehmenden – persönlich und intensiv. Magst du wissen, welche Reise dazu am besten passt?

Wichtigste Hinweise. Diese müssen unbedingt beachtet werden:
- Antworte in höchstens 2–4 kurzen Sätzen. Das ist Pflicht, keine Empfehlung.
- Fasse dich knapp: keine langen Aufzählungen, keine einleitenden Floskeln, keine Wiederholung der Frage – komme direkt zur Antwort.
- Vermeide das Wort "leider", weil es negativ klingt und andeutet, dass etwas nicht funktioniert hat.

Aktuelle Zeitangabe:
- Datum: {{date}}
- Uhrzeit: {{time}}
- Wochentag: {{weekday}}

Der Kunde befindet sich gerade auf folgender Webseite: {{endpoint}}. Gehe davon aus, dass sich Fragen auf diese Seite beziehen.

{{page_content_block}}{{kundenberater_name}}
{{kundenberater_telefon}}
""".strip()

# URL patterns for link processing
site_link_pattern = re.compile(r"(?:/[a-zA-Z0-9\-\_]+)*")
assert all(site_link_pattern.match(url) for url in all_sites)
url_pattern = re.compile(
    r"\s(https:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*))"
)

def detect_recommendation_links(reply: str) -> set[str]:
    """
    Detect and return a set of recommendation links from the reply.
    """
    links = set()
    for match in site_link_pattern.finditer(reply):
        link = match.group(0)
        if link in trip_sites:
            # check if the link is to #termine
            _, end = match.span(0)
            if reply[end : end + 8] == "#termine":
                link += "#termine"
            links.add(link)
    return links


def get_current_time_info() -> dict:
    """Get current date, time, and weekday formatted for German locale."""

    now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))
    return {
        "date": now.strftime("%d. %B %Y"),
        "time": now.strftime("%H:%M"),
        "weekday": now.strftime("%A"),
    }


# Booking number in the MeinChamäleon trip URL. It is prefilled into the
# Kunden-Modus link block so the model never receives a placeholder. The value
# comes from the client-sent current_url and can be echoed into the rendered
# reply, so restrict it to a safe id charset — anything else counts as "no
# booking number" and the four trip links are omitted from the prompt.
_vrrvorgang_pattern = re.compile(r"[?&]VRRVORGANG=([A-Za-z0-9_-]{1,64})", re.IGNORECASE)


def _vrrvorgang_from_url(url: str) -> str:
    """Return the current page's VRRVORGANG booking number, or '' if absent."""
    match = _vrrvorgang_pattern.search(url or "")
    return match.group(1) if match else ""


# Die Berater-Box der Website, in den beiden Formen, in denen sie vorkommt.
# Gemessen 2026-09-18 ueber 25 zufaellige indizierte Reise-URLs: 23 Treffer,
# die zwei Fehlschlaege (Sossusvlei-ALL, Erongo-ALL) fuehren gar keinen Berater.
#
#   Reisedetailseite (~50k Zeichen, Box bei 84-99 % Tiefe):
#       ![Mira Feldmann](/data/thumbs/.../PASSBILD900.jpg)
#
#       Erlebnisberater\*in
#
#       Mira Feldmann
#
#       [+49 30 347996-901](tel:+49 30 347996-901)
#
#   Laender- und -ALL-Uebersicht (~15-22k Zeichen, Box bei 4-6 % Tiefe):
#       **Jonas Wehrle**
#       Erlebnisberaterin
#
#       Ich bin fuer dich da.
#
#       [+49 30 347996-902](tel:+49 30 347996-902 "Anruf starten")
#
# Die Nummer wird genommen, wie sie dasteht. In denselben 25 Seiten kamen drei
# Schreibweisen vor (+49 30 347996-901, +49 30-347996-903, 030347996905) — wer
# hier normalisiert, erfindet eine Nummer, die so auf der Seite nicht steht.
#
# Dritte Form, Laenderseiten (gemessen 2026-09-25, /Amerika/Costa-Rica,
# /Afrika/Namibia): statt einer Person ein Team; der Name der Teamleitung steht
# nur im Bild-alt. Genommen wird, was als Text dasteht (Owner): "Team Amerika".
#
#       **Team Amerika**
#
#       Ich bin für dich da.
#
#       [+49 30 347996-228](tel:+49 30 347996-228 "Anruf starten")
#
# Sie steht zuletzt: eine Reiseseite mit benannter Berater*in gewinnt.
_BERATER_PATTERNS = (
    re.compile(
        r"Erlebnisberater\\\*in\s*\n+\s*(?P<name>[^\n\[\]]+?)\s*\n+\s*"
        r"\[\s*(?P<tel>[+0-9][0-9 \-]{5,})\s*\]\(tel:"
    ),
    re.compile(
        r"\*\*(?P<name>[^*\n]+?)\*\*\s*\n+\s*Erlebnisberater(?:in)?\b"
        r"[\s\S]{0,120}?\[\s*(?P<tel>[+0-9][0-9 \-]{5,})\s*\]\(tel:"
    ),
    re.compile(
        r"\*\*(?P<name>Team[ \xa0][^*\n]+?)\*\*\s*\n+\s*Ich bin f(?:ü|ue)r dich da\.?"
        r"\s*\n+\s*\[\s*(?P<tel>[+0-9][0-9 \-]{5,})\s*\]\(tel:"
    ),
)


@ttl_cache(maxsize=1024, ttl=86400)
def berater_von_seite(url_path: str) -> tuple[str, str]:
    """(Name, Telefon) der Erlebnisberater*in von der Reiseseite, sonst ("", "").

    Gecacht wie die Seite selbst (24 h): der HTML-Cache erspart den Abruf, nicht
    das Markdownifizieren der 50k-Zeichen-Seite — ohne diesen Dekorator kostete
    der Promptbau gemessen 85 ms je Nachricht statt 0.

    Ruft die Seite ab und parst sie — ohne Schranke. Wer das auf JEDER
    Chatnachricht aufruft, schraenkt vorher selbst ein (siehe
    ``format_system_prompt``, das ``travel_index.is_reise_url`` davorsetzt);
    das Tool dagegen wird gezielt aufgerufen und darf auch eine nicht
    indizierte Reiseseite lesen.

    Gibt ("", "") zurueck, sobald irgendetwas fehlt: kein Teiltreffer, keine
    geratene Nummer. Wirft nie — der Promptbau darf hieran nicht scheitern.
    """
    try:
        markdown = chamaeleon_website_tool_base(url_path)
    except Exception as e:
        print(f"[agent_base] berater page lookup failed for {url_path}: {e}")
        return "", ""

    for pattern in _BERATER_PATTERNS:
        match = pattern.search(markdown)
        if match:
            name = match.group("name").replace("\xa0", " ").strip()
            telefon = match.group("tel").strip()
            if name and telefon:
                return name, telefon
    return "", ""


def _ist_reise_url(url_path: str) -> bool:
    """travel_index.is_reise_url, aber ohne je eine Ausnahme durchzulassen."""
    try:
        import travel_index

        return travel_index.is_reise_url(url_path)
    except Exception as e:
        print(f"[agent_base] reise-url check failed for {url_path}: {e}")
        return False


def _berater_aus_index(url_path: str) -> dict[str, str]:
    """travel_index.get_berater, aber ohne je eine Ausnahme durchzulassen."""
    try:
        import travel_index

        return travel_index.get_berater(url_path)
    except Exception as e:
        print(f"[agent_base] berater lookup failed for {url_path}: {e}")
        return {}


berater_tool_description = """
Tool für die zuständige Erlebnisberater*in einer Reise — Name und Durchwahl,
direkt von der Reiseseite.

Nutze es, sobald jemand wissen will, wer für eine Reise zuständig ist oder an
wen er sich zu einer konkreten Reise wenden soll: "Wer ist mein
Ansprechpartner?", "an wen wende ich mich wegen meiner Fluganfrage?", Fragen
zum Buchungsstatus, zu Reservierungen und Optionen.

Du brauchst dafür die Reise. Kennst du sie noch nicht, frage zuerst danach,
statt zu raten.

Liefert das Tool nichts, dann hat diese Seite keine Erlebnisberater*in
hinterlegt. Nenne dann die Zentrale (+49 30 347 996 0). Erfinde niemals einen
Namen oder eine Durchwahl.

Args:
    url_path (str): Pfad der Reiseseite, z.B. "/Afrika/Namibia/Etosha"
"""


def berater_tool_base(url_path: str) -> str:
    """Erlebnisberater*in einer Reise als fertiger Satz, oder eine Leermeldung.

    Der Agenturbereich ist login-geschuetzt und ueber das Website-Tool nicht
    erreichbar — ein Abruf liefe nur in den Timeout. Deshalb hier abgewiesen,
    statt das Modell warten zu lassen.
    """
    if url_path.lstrip("/").lower().startswith("agentur"):
        return (
            "Der Agenturbereich hat keine Erlebnisberater*in. "
            "Frage nach der Reise, um die es geht."
        )

    name, telefon = berater_von_seite(url_path)
    if name and telefon and name.startswith("Team"):
        return f"Ansprechpartner für dieses Land: {name}, Telefon {telefon}"
    if name and telefon:
        return f"Erlebnisberater*in dieser Reise: {name}, Telefon {telefon}"
    return (
        "Für diese Seite ist keine Erlebnisberater*in hinterlegt. "
        "Nenne die Zentrale: +49 30 347 996 0"
    )


# Dasselbe Zeichenset wie in ``_vrrvorgang_pattern``, nur als ganzer Wert: die
# Nummer kann jetzt auch aus TourOne statt aus der URL kommen, landet aber
# genauso in einem Link in der gerenderten Antwort.
_VRRVORGANG_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _trip_links_block(reise_vorgang: str, reise_label: str) -> str:
    """Die vier MeinChamäleon-Links zur gemeinten Reise; "" ohne Buchungsnummer.

    WELCHE Reise gemeint ist, löst der Aufrufer auf (``agent.py``): entweder die
    Nummer aus der geöffneten Seite oder die nächste offene Buchung des Kunden.
    Hier kommt nur noch ein fertiger String an — der Prompt-Bau bleibt dadurch
    ohne Netz und sieht die kunden_id weiterhin nie.

    ``reise_label`` ist genau dann gesetzt, wenn die Reise aus den Buchungen
    stammt statt aus der URL. Dann benennt die Überschrift sie (Ziel, Datum),
    damit Leon sagen kann, worauf er sich bezieht — bei mehreren offenen Reisen
    ist das sonst geraten.

    Die Nummer wird wie in ``_vrrvorgang_from_url`` auf ein sicheres Zeichenset
    geprüft: sie landet in einem Link, der in der gerenderten Antwort steht.
    Alles andere zählt als „keine Nummer", dann bleiben die Übersichts-Links.
    """
    if not _VRRVORGANG_SAFE.match(reise_vorgang or ""):
        return ""
    reise_url = (
        "https://www.chamaeleon-reisen.de/MeinChamaeleon/Reise"
        f"?VRRVORGANG={reise_vorgang}"
    )
    kopf = (
        f"Zur nächsten Reise ({reise_label}):"
        if reise_label
        else "Zur aktuell geöffneten Reise:"
    )
    return (
        f"{kopf}\n"
        f"- [Reisedaten]({reise_url}#reisedaten)"
        " — die Eckdaten deiner Buchung\n"
        f"- [Reiseverlauf]({reise_url}#reiseverlauf)"
        " — die Unterkünfte und der Ablauf deiner Reise\n"
        f"- [Gäste]({reise_url}#gaeste)"
        " — die Passdaten der Reisenden einsehen und ergänzen\n"
        f"- [Reiseunterlagen]({reise_url}#unterlagen)"
        " — deine Reiseunterlagen mit Flugplan, Rechnung (inklusive "
        "Zahlungslink), Rail&Fly-Tickets und Visumausfüllhilfe; "
        "bereitgestellt spätestens zwei Wochen vor Reisebeginn\n"
    )


def format_system_prompt(
    endpoint: str,
    countries: list[str],
    kundenberater_name: str = "",
    kundenberater_telefon: str = "",
    is_agentur: bool = False,
    page_content: str = "",
    is_kunde: bool = False,
    has_agentur_daten: bool = False,
    reise_vorgang: str = "",
    reise_label: str = "",
) -> str:
    """Format the system prompt with current time information and endpoint.

    ``reise_vorgang``/``reise_label``: die schon aufgelöste Reise für die
    MeinChamäleon-Links (siehe ``_trip_links_block``). Beide sind Strings und
    kommen fertig vom Aufrufer — diese Funktion holt nichts nach.
    """
    # Erlebnisberater, in dieser Rangfolge: bei einer Reise gewinnt die Angabe
    # von der Reiseseite selbst, dann die vom einbettenden Widget, zuletzt der
    # TourOne-Index.
    #
    # Warum die Seite oben steht: der Index liefert das Feld seit jeher leer.
    # Gemessen 2026-09-18 ueber fetch_all_travels — 670 von 670 Reisen tragen
    # ein berater-Dict mit den richtigen Schluesseln und in allen 670 sind
    # vorname, nachname, telefon und email leer. Die Seite traegt es dagegen in
    # zwei festen Formen (siehe berater_von_seite): 23 von 25 stichprobenartig
    # geprueften Reise-URLs. Der Index bleibt in der Kette, damit es von selbst
    # wirkt, falls TourOne das Feld je befuellt.
    #
    # is_reise_url peekt in den Index und baut ihn nie. Die Schranke steht hier
    # und nicht in berater_von_seite, weil sie zu DIESEM Aufrufer gehoert: er
    # laeuft auf jeder Chatnachricht. Ohne sie liefe jeder Agentur-Request in
    # den Abruf-Timeout — der Agenturbereich ist login-geschuetzt und ueber das
    # Website-Tool gar nicht erreichbar.
    seite_name, seite_telefon = (
        berater_von_seite(endpoint) if _ist_reise_url(endpoint) else ("", "")
    )
    # Die Team-Box ("**Team Amerika** / Ich bin für dich da.") ist nur der
    # Rueckfall fuer Seiten ohne benannte Person: nennt das Widget eine
    # Berater*in, gewinnt sie. Telefon mit, damit Name und Nummer zusammenpassen.
    if seite_name.startswith("Team") and kundenberater_name:
        seite_name, seite_telefon = "", ""
    index_berater = _berater_aus_index(endpoint)
    kundenberater_name = (
        seite_name or kundenberater_name or index_berater.get("name", "")
    )
    kundenberater_telefon = (
        seite_telefon or kundenberater_telefon or index_berater.get("telefon", "")
    )

    time_info = get_current_time_info()

    # Kunden-Modus (eingeloggter MeinChamäleon-Kunde): nur ein Flag erreicht
    # den Prompt — die kunden_id selbst bleibt in der Tool-Closure (agent.py)
    # und darf hier nie auftauchen.
    kunden_modus_block = ""
    if is_kunde:
        # Trip-specific MeinChamäleon links carry the booking number, so the
        # model only ever receives ready-to-use links and never holds a
        # placeholder. Welche Buchung gemeint ist, steht schon fest, wenn wir
        # hier ankommen (aufgelöst in agent.py: Nummer aus der geöffneten Seite,
        # sonst die nächste offene Reise des Kunden). Ohne Nummer bleiben die
        # vier Reise-Links weg — wie bisher.
        trip_links_block = _trip_links_block(reise_vorgang, reise_label)
        kunden_modus_block = (
            "Kunden-Modus:\n"
            "Der Kunde ist in MeinChamäleon eingeloggt. Du hast über das "
            "buchungen_tool NUR LESENDEN Zugriff auf seine Buchungen (Reisedaten, "
            "Status, Zahlstand, Flüge) — darüber hinaus kannst du nichts: keine "
            "Buchungen anlegen, keine Änderungen, keine Stornierungen und keine "
            "Abfragen außerhalb der Tools.\n"
            "- Rufe buchungen_tool nur auf, wenn der Kunde nach seinen eigenen "
            "Buchungen, Reisen, Flügen oder seinem Zahlstand fragt. Hol dir "
            "zuerst die grobe Liste (details=false) und fasse dann bei Bedarf mit "
            "details=true nach; grenze mit auswahl/anzahl ein (z. B. die nächste "
            "Reise: auswahl=kommende, anzahl=1).\n"
            "- Fragen zur Vorbereitung der gebuchten Reise — Packliste, Gepäck "
            "und Handgepäck, Trinkgeld, Bargeld und Bezahlen, Klima, Strom und "
            "Adapter, Gesundheit, Abholung am Zielflughafen — beantwortest du "
            "IMMER mit dem reiseinfo_tool, nicht aus den allgemeinen FAQs und "
            "nicht mit einem Verweis auf die Reiseunterlagen.\n"
            "- Ruf das reiseinfo_tool dafür OHNE Argument auf: es nimmt von "
            "selbst die gerade geöffnete Reise, sonst die nächste des Kunden. "
            "Du brauchst dafür kein buchungen_tool und keine Buchungsnummer — "
            "frag den Kunden nie danach und frag auch nicht, welche Reise er "
            "meint.\n"
            "- Abweichend von der allgemeinen Flüge-Regel darfst du die per Tool "
            "abgerufenen Buchungsdaten dieses Kunden — Flüge wie Zahlstand, "
            "vergangene wie kommende — nennen.\n"
            "- Die Rechnung als Dokument, die Reiseunterlagen und die Passdaten "
            "der Teilnehmer kannst du NICHT abrufen oder ändern; zeige dem Kunden "
            "dafür den passenden MeinChamäleon-Link (siehe unten), auf dem er sie "
            "selbst einsehen oder ergänzen kann. Nur für echte Vorgänge wie "
            "Umbuchung oder Stornierung, für die es keine solche Seite gibt, "
            "verweise an den Erlebnisberater.\n"
            "Alle allgemeinen Funktionen (Reisekatalog, Termine, FAQs, Visum) "
            "stehen weiterhin zur Verfügung.\n\n"
            "Links in MeinChamäleon:\n"
            "Diese Seiten kannst du selbst NICHT öffnen — rufe das "
            "chamaeleon_website_tool NICHT für MeinChamäleon-Seiten auf. "
            "Verwende ausschließlich die hier genannten Links und baue keine "
            "eigenen MeinChamäleon-URLs.\n"
            "Gib einen MeinChamäleon-Link IMMER vollständig und wörtlich so "
            "wieder, wie er hier steht — immer mit https://www.chamaeleon-reisen.de "
            "davor; die Reise-Links zusätzlich mit Buchungsnummer und Anker "
            "(#unterlagen, #gaeste, #reiseverlauf, #reisedaten). Kürze einen "
            "Reise-Link nie auf /MeinChamaeleon und "
            "mache nie einen relativen Pfad daraus: die allgemeine Regel, "
            "relative URLs zu verwenden, gilt für MeinChamäleon-Links NICHT.\n"
            "Passt die Frage des Kunden zu einer dieser Seiten, gib IMMER den "
            "passenden Link als fertigen Link zum Anklicken an — nenne den "
            "Bereich nie nur beim Namen (also nicht bloß „in den "
            "Reiseunterlagen“, sondern immer der echte Link dorthin). Was der "
            "Kunde auf jeder Seite findet oder selbst erledigen kann, steht in "
            "der Beschreibung hinter dem jeweiligen Link.\n"
            "Das gilt auch, wenn der Kunde die Seite nicht beim Namen nennt: "
            "Verweist deine Antwort auf ein Dokument oder einen Wert, der laut "
            "Beschreibung auf einer dieser Seiten liegt (z. B. die Rechnung mit "
            "dem Zahlungslink in den Reiseunterlagen), gib den Link zu dieser "
            "Seite immer mit an.\n"
            "Immer verfügbar:\n"
            "- [Mein Chamäleon Übersicht](https://www.chamaeleon-reisen.de/MeinChamaeleon)"
            " — Überblick über deine Reisen sowie deine Clubstufe und ihre Vorteile\n"
            "- [Stornierte Reisen](https://www.chamaeleon-reisen.de/MeinChamaeleon/Stornierte-Reisen)"
            " — deine bereits stornierten Reisen\n"
            "- [Meine Daten](https://www.chamaeleon-reisen.de/MeinChamaeleon/Daten)"
            " — deine hinterlegte E-Mail-Adresse und persönlichen Daten prüfen "
            "oder ändern (auch dein Login läuft über diese Daten)\n"
            "- [Facebook-Gruppe für Gleichgesinnte](https://www.facebook.com/groups/617243488669698)\n"
            "- [Regenwald schützen](https://www.rainforest-foundation.com/spenden/)\n"
            f"{trip_links_block}"
            "\n"
            "Verhalten & Tonalität in MeinChamäleon:\n"
            "Du sprichst mit einem eingeloggten Gast, der bereits gebucht hat.\n"
            "- Wecke Vorfreude auf die Reise: ein Bild von Landschaft, "
            "Erlebnis oder Begegnung, wo es passt.\n"
            "- Bei Stornierung, Umbuchung, Datenänderung oder Passwort: ruhig "
            "und lösungsorientiert, keine Vorfreude.\n\n"
        )

    agentur_block = ""
    if is_agentur:
        agentur_block = (
            "Agenturbereich:\n"
            "Diese Konversation findet im Agenturbereich für Reisebüros statt "
            "(agt.chamaeleon-reisen.de). Du sprichst hier mit Reiseprofis "
            "(Reisebüros, Expedient*innen, mobilen Reiseberater*innen), nicht mit "
            "Endkund*innen. Nutze zusätzlich die folgende Wissensbasis für den "
            "Agenturbereich. Schritt-für-Schritt-Anleitungen aus dieser "
            "Wissensbasis darfst du abweichend von der Längenregel vollständig "
            "wiedergeben:\n\n"
            f"{agentur_wissensbasis}\n\n"
        )

    # Buchungsdaten der Agentur: nur bei serverseitig verifizierter Bindung.
    # Die Agenturnummer selbst erreicht den Prompt NIE — sie bleibt in der
    # Tool-Closure (agent.py), gleiche Regel wie für die kunden_id.
    if has_agentur_daten:
        agentur_block += (
            "Buchungen dieser Agentur:\n"
            "Diese Agentur ist angemeldet und verifiziert. Mit dem Tool "
            "buchungen_agentur_tool kannst du IHRE eigenen Buchungen abrufen — "
            "und ausschließlich diese. Nutze es, sobald nach konkreten "
            "Buchungen, Reisenden, Terminen, Preisen oder der Provision zu "
            "einer Buchung gefragt wird.\n"
            "- Arbeite in zwei Schritten: erst die grobe Liste (details=false), "
            "dann gezielt mit auswahl/anzahl eingegrenzt und details=true "
            "nachfassen. Der Zahlstand — Anzahlung, offener Betrag, bereits "
            "eingegangen — und die Flugdaten stehen ausschließlich in der "
            "Detailansicht.\n"
            "- Steht am Ende der Antwort, dass zu einzelnen Buchungen keine "
            "Zahlungs- und Flugdaten geladen werden konnten, dann gib das so "
            "weiter. Diese Buchungen sind nicht unbezahlt und haben nicht "
            "keine Flüge — die Angaben fehlen gerade.\n"
            "- Erfinde nie eine Buchung, eine Buchungsnummer, einen Namen oder "
            "einen Betrag. Was das Tool nicht liefert, weißt du nicht.\n"
            "- Die Agenturnummer steht in der ersten Zeile der Tool-Antwort. "
            "Nur von dort nehmen — eine Buchungsnummer ist NICHT die "
            "Agenturnummer, und geraten wird nie.\n"
            "- Wenn das Tool meldet, die Daten seien gerade nicht abrufbar, "
            "sage genau das. Sage NIE, es gebe keine Buchungen — das ist etwas "
            "völlig anderes und der Reiseprofi würde daraus schließen, die "
            "Buchung existiere nicht.\n"
            "- Die Mitreisenden sind hier bewusst enthalten: die Agentur hat "
            "die Buchung selbst angelegt und kennt sie bereits.\n\n"
            # D8: Die Provision zerfällt in zwei Fragen, die verschieden
            # beantwortet werden. Der Betrag ist eine Tatsache über bereits
            # geleistete Arbeit, die der Reiseprofi ohnehin auf seiner eigenen
            # Buchungsseite sieht. Die Konditionen sind Vertragsthema.
            "Provision:\n"
            "- Die Provisionshöhe zu einer KONKRETEN Buchung ist eine Tatsache "
            "und steht in den Buchungsdaten — nenne sie, wenn danach gefragt "
            "wird. Die Agentur sieht dieselbe Zahl auf ihrer eigenen "
            "Buchungsübersicht.\n"
            "- Ebenso beantwortbar: die eigene Agenturnummer und Summen, die "
            "sich aus den eigenen Buchungen ergeben.\n"
            "- NICHT beantworten, sondern an das Vertriebsteam verweisen: wie "
            "ein Provisionssatz zustande kommt, individuelle Konditionen, "
            "Anhebung, Verkettung, Rückvergütung, Cashback, Streit über eine "
            "Provisionsabrechnung und alles Vertragliche.\n"
            "- Bankdaten (IBAN, BIC, Kontoinhaber) nennst du nie.\n\n"
        )

    # Agentur pages are behind a login, so chamaeleon_website_tool cannot
    # fetch them; the widget sends the page content instead (already
    # markdownified and capped by markdownify_page_html).
    page_content_block = ""
    if is_agentur and page_content:
        page_content_block = (
            "Inhalt der aktuellen Seite:\n"
            "Der Inhalt der Seite, auf der sich der Kunde gerade befindet, liegt "
            "dir hier bereits als Markdown vor. Beziehe dich bei Fragen zur "
            "aktuellen Seite direkt auf diesen Inhalt. Rufe für die aktuelle Seite "
            "NICHT das chamaeleon_website_tool auf – der Agenturbereich ist darüber "
            "nicht erreichbar. Für öffentliche Seiten auf www.chamaeleon-reisen.de "
            "kannst du das Tool weiterhin nutzen.\n\n"
            "--- Seiteninhalt Anfang ---\n"
            f"{page_content}\n"
            "--- Seiteninhalt Ende ---\n\n"
        )

    # Die Nur-auf-Frage-Regel steht vor UND nach den Eintraegen (B1, Owner
    # 2026-09-25): ohne sie leerte Leon bei "Infos über Marokko" die Marokko-FAQ
    # aus (Ramadan). Die Injektion selbst bleibt — ohne sie fielen die
    # Laender-Faelle aus test_faq.py von 119/135 auf 41/135.
    nur_auf_frage = (
        "WICHTIG: Nutze einen dieser Einträge NUR, wenn der Kunde ausdrücklich "
        "nach genau diesem Thema fragt (z. B. Ramadan, Geld wechseln, Steckdosen, "
        "Leitungswasser). Fragt er allgemein nach dem Land oder einer Reise "
        "(„Erzähl mir was über [Land]“, „Was gibt es zu dieser Reise zu wissen?“), "
        "erwähnst du keinen dieser Einträge – die Antwort kommt dann von der "
        "Reise- oder Länderseite."
    )
    laenderspezifische_faqs = ""
    if countries:
        laenderspezifische_faqs += (
            "Diese Länder wurden im Chatverlauf erkannt und hier sind ihre FAQs, "
            "auf die du auch durch das country_faq_tool hättest zugreifen können.\n"
            f"{nur_auf_frage}\n\n"
        )

    for country in countries:
        laenderspezifische_faqs += laender_faqs[country] + "\n\n"

    if countries:
        laenderspezifische_faqs += f"{nur_auf_frage}\n\n"

    return system_prompt_template.format(
        **time_info,
        endpoint=endpoint,
        kunden_modus_block=kunden_modus_block,
        agentur_block=agentur_block,
        page_content_block=page_content_block,
        laenderspezifische_faqs=laenderspezifische_faqs,
        # Uebersichtsseiten (-ALL) tragen oft nur die Team-Box (berater_von_seite,
        # dritte Form): dann ist das Team zustaendig, nicht "der Erlebnisberater".
        kundenberater_name=(
            (
                "Für diese Seite ist die Erlebnisberatung " + kundenberater_name + " zuständig. "
                if kundenberater_name.startswith("Team")
                else "Bei dieser Reise heißt der Erlebnisberater " + kundenberater_name + ". "
            )
        )
        if kundenberater_name
        else "",
        # Der Verweis-Satz (Owner 2026-09-25): sonst schrieb Leon die FAQ-Antwort
        # "klärt die Erlebnisberater*in deiner Wunschreise" wortgleich ab, obwohl
        # Name und Durchwahl hier im Prompt stehen.
        kundenberater_telefon=(
            (
                "Die Telefonnummer ist " + kundenberater_telefon + ". "
                if kundenberater_name.startswith("Team")
                else "Die Telefonnummer des Erlebnisberaters ist " + kundenberater_telefon + ". "
            )
            + (
                "Verweist du auf die Erlebnisberatung oder die Erlebnisberater*in, "
                "nenne immer " + kundenberater_name + " mit dieser Telefonnummer – "
                "nie nur allgemein „die Erlebnisberater*in deiner Wunschreise“. "
                if kundenberater_name
                else ""
            )
        )
        if kundenberater_telefon
        else "",
    )


# --- „Wichtige Informationen": Reiseinfo-Textbausteine zu einer Buchung -------
#
# Rekonstruiert den Block „Wichtige Informationen" der MeinChamäleon-Reiseseite
# (Reisehinweise, Checkliste, Inlands-/Regionalflüge, Allgemeine
# Reiseinformationen) allein aus der TourOne-API — ohne die eingeloggte Seite zu
# scrapen. Der Kunde nennt eine Buchung „Reise"; Einstieg ist deshalb die
# Vorgangsnummer.
#
# Kette (alle GET, siehe https://api.tourone.de/api/doc):
#   /get/buchung?vorgangsNummer=…   → reiseCode
#   /get/reise?reisecode=…          → zusatzfelder (INFO-Codes) + land2 (ISO)
#   /get/textbaustein?textcode=…    → je Baustein Titel + HTML-Text
#
# Zwei Quellen für die Codes, bewusst getrennt:
#  1. AUTORITATIV, pro Reise gepflegt (reise.zusatzfelder): die INFO-Bausteine
#     der „Allgemeinen Reiseinformationen". Ihre WERTE sind die Textcodes
#     (INFOALLLAND→INFO-ALLE, INFOLAND→INFO-<land>, INFOREISE→INFO-<reise>, …).
#     Nur Werte übernehmen, die wie ein Code aussehen — manche Felder tragen
#     Müll (Leerzeichen, Fließtext).
#  2. KONVENTION, aus dem Ziel-ISO (reise.land2.iso2Liste, bei Mehrländer-
#     reisen kommagetrennt): HIN-<iso>, CHECK-<iso>, FLUG-<iso>-INL. Existiert
#     kein solcher Baustein, hat das Land den Block schlicht nicht (kein Fehler).
#
# Bekannte Lücke: der internationale Block „Informationen Linienflug" (z.B.
# FLUG-MY) wird vom MeinChamäleon-Backend editorial aus der Flugroute gesetzt
# und liegt in KEINEM erreichbaren API-Feld — er fehlt daher bewusst.
#
# Verifiziert 2026-08-05 über 14 Länder (NP, UG, JO, MN, TZ, CA, PE, NA, VN, MA,
# TH, ES, OM, EG) sowie gegen die IDKOM-Seite (6/7 Codes, nur FLUG-MY fehlt).

# Ein zusatzfeld-Wert zählt nur als Textcode, wenn er dem Schema folgt (GROSS +
# Bindestrich, z.B. INFO-IDKOM). Sonst wären Leerzeichen oder Fließfeld-Reste
# ("PASSS-ES-Landinfo", " ") gültige Codes.
_TEXTBAUSTEIN_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*-[A-Z0-9-]+$")

# reise.zusatzfelder-Schlüssel, deren WERT ein INFO-Textcode ist (Block
# „Allgemeine Reiseinformationen"). Reihenfolge = Anzeige-Reihenfolge.
_REISEINFO_KEYS = (
    "INFOALLLAND",
    "INFOLAND",
    "INFOLAND2",
    "INFOLAND3",
    "INFOLAND4",
    "INFOREISE",
    "INFOZUSATZ",
    "INFOZUSATZ2",
    "INFOZUSATZ3",
    "INFOZUSATZ4",
    "INFOZUSATZ5",
    "INFOAIRLINE",
    "INFOAIRLINE2",
    "INFOAIRLINE3",
)

# Wie kundendaten.py: der 20s-Default von _tourone_get ist für Index-Builds; hier
# muss die Wartezeit pro Request enger sein.
#
# Die Zahlen sind gegen die 30-Sekunden-Frist des Widgets gerechnet, nicht
# geraten: Das Widget bekommt bis zum finalen response-Event kein Byte, der
# ganze Turn (Modelllauf INKLUSIVE Tool-Aufrufe) muss also darunter bleiben.
# Bei einer Mehrländerreise mit voll gepflegten Zusatzfeldern entstehen bis zu
# 20 Baustein-Requests; mit PARALLEL=8 wären das drei Wellen, macht im
# Timeout-Worst-Case 2*8s (Buchung, Reise) + 3*8s = 40s — allein das Tool reißt
# die Frist. Deshalb: kürzeres Timeout je Request und genug Parallelität, dass
# eine Welle reicht. Worst Case jetzt 5s + 5s + 5s = 15s.
REISEINFO_TIMEOUT = 5
REISEINFO_FETCH_PARALLEL = 20


def _reise_code_for_vorgang(vorgangsnummer: str) -> str | None:
    """reiseCode der Buchung, oder None wenn die Buchung fehlt/leer ist."""
    import travel_index

    buchung = travel_index._tourone_get(
        "/get/buchung", {"vorgangsNummer": vorgangsnummer}, timeout=REISEINFO_TIMEOUT
    )
    if not isinstance(buchung, dict):
        return None
    code = buchung.get("reiseCode")
    return code if isinstance(code, str) and code else None


def _reiseinfo_zusatzfeld_wert(zusatzfelder: dict, key: str) -> str | None:
    """``zusatzfelder[key].value`` — die Felder sind {label, value}-Dicts."""
    feld = zusatzfelder.get(key)
    if isinstance(feld, dict):
        wert = feld.get("value")
        return wert if isinstance(wert, str) else None
    return None


def _reiseinfo_ziel_isos(reise: dict) -> list[str]:
    """Ziel-ISO(s) aus land2 — kommagetrennt bei Mehrländerreisen (CN,HK)."""
    land2 = reise.get("land2") or {}
    roh = land2.get("iso2Liste") or land2.get("code") or ""
    isos = [t.strip().upper() for t in str(roh).split(",")]
    # Nur echte ISO2 — der code kann auch ein Kürzel wie "CUH" sein.
    return [iso for iso in isos if re.fullmatch(r"[A-Z]{2}", iso)]


def _reiseinfo_kandidaten(reise: dict) -> dict[str, list[str]]:
    """Kandidaten-Textcodes je Block. Existenz wird erst danach geprüft.

    Reihenfolge und Blockschnitt spiegeln die MeinChamäleon-Reiseseite. Der
    internationale „Informationen Linienflug"-Block fehlt bewusst (siehe oben):
    sein Code steht in keinem API-Feld.
    """
    zf = reise.get("zusatzfelder") or {}
    isos = _reiseinfo_ziel_isos(reise)

    info: list[str] = []
    for key in _REISEINFO_KEYS:
        wert = _reiseinfo_zusatzfeld_wert(zf, key)
        if wert and _TEXTBAUSTEIN_CODE_RE.match(wert.strip()):
            info.append(wert.strip())

    return {
        "Reisehinweise": [f"HIN-{iso}" for iso in isos],
        "Checkliste": [f"CHECK-{iso}" for iso in isos],
        "Informationen Inlands- und Regionalflüge": [
            f"FLUG-{iso}-INL" for iso in isos
        ],
        "Allgemeine Reiseinformationen": info,
    }


def _ist_nicht_gefunden(fehler: Exception) -> bool:
    """Heißt dieser Fehler „gibt es nicht" — oder heißt er „gerade kaputt"?

    Praktisch nur ein Sicherheitsnetz: gemessen 2026-08-11 antwortet
    ``/get/textbaustein`` für einen FEHLENDEN Code mit HTTP 200 und einer leeren
    Liste (geprüft mit FLUG-MN-INL und einem Fantasiecode), nicht mit einem
    Fehler — den Fall fängt schon der ``isinstance``-Guard unten ab. Sollte
    TourOne doch einmal 404 liefern, heißt auch das „gibt es nicht".
    """
    antwort = getattr(fehler, "response", None)
    return getattr(antwort, "status_code", None) == 404


def _lade_textbaustein(code: str) -> dict | None:
    """Einen Textbaustein holen. None = gibt es nicht; wirft bei Ausfall.

    Die beiden Fälle auseinanderzuhalten ist der ganze Zweck: „diesen Block hat
    das Land nicht" kommt als 200 mit leerer Liste, ein Timeout oder eine 500
    ist ein Ausfall. Beides als „gibt es nicht" zu lesen, machte aus einer
    Störung die Aussage „zu dieser Reise ist nichts hinterlegt" — und der Kunde
    schließt daraus, er müsse nichts vorbereiten. Genau diese Verwechslung fand
    das Review 2026-08-10.
    """
    import travel_index

    try:
        baustein = travel_index._tourone_get(
            "/get/textbaustein", {"textcode": code}, timeout=REISEINFO_TIMEOUT
        )
    except Exception as e:
        if _ist_nicht_gefunden(e):
            return None
        raise
    if not isinstance(baustein, dict):
        return None
    text = (baustein.get("text") or "").strip()
    if not text:
        return None
    return {
        "code": code,
        "titel": baustein.get("bezeichnung") or code,
        "html": text,
    }


# Dritter Ausgang neben „da" und „gibt es nicht": „gerade nicht abrufbar".
_FEHLER = object()


def _lade_textbaustein_sicher(code: str):
    """``_lade_textbaustein``, aber ein Ausfall reißt die Nebenläufigkeit nicht.

    Eine Exception aus ``pool.map`` würde beim Auslesen der Ergebnisse fliegen
    und alle übrigen Bausteine mitnehmen — auch die längst geladenen.
    """
    try:
        return _lade_textbaustein(code)
    except Exception as e:
        print(f"[agent_base] textbaustein {code} nicht abrufbar: {type(e).__name__}")
        return _FEHLER

def reiseinfo_tools(vorgangsnummer: str) -> dict:
    """„Wichtige Informationen" zu einer Buchung (Vorgangsnummer).

    Rückgabe::

        {
          "vorgang": "226177",
          "reiseCode": "CNZHA_NEU",
          "isos": ["CN", "HK"],
          "bloecke": {
            "Reisehinweise": [{code, titel, html}, ...],
            "Checkliste": [...],
            "Informationen Inlands- und Regionalflüge": [...],
            "Allgemeine Reiseinformationen": [...],
          },
        }

    Leere Blöcke werden weggelassen. Fehlt die Buchung oder ihre Reise, ist
    ``bloecke`` leer (kein Wurf, außer der API-Aufruf selbst schlägt fehl).
    """
    import travel_index

    reise_code = _reise_code_for_vorgang(vorgangsnummer)
    if not reise_code:
        return {"vorgang": vorgangsnummer, "reiseCode": None, "isos": [], "bloecke": {}}

    reise = travel_index._tourone_get(
        "/get/reise", {"reisecode": reise_code}, timeout=REISEINFO_TIMEOUT
    )
    if not isinstance(reise, dict):
        return {
            "vorgang": vorgangsnummer,
            "reiseCode": reise_code,
            "isos": [],
            "bloecke": {},
        }

    kandidaten = _reiseinfo_kandidaten(reise)

    # Alle Codes nebenläufig auflösen (ein Request je Kandidat); Reihenfolge je
    # Block bleibt erhalten. Doppelte Codes nur einmal anfragen — dieselbe
    # INFO-Nummer kann in mehreren zusatzfeldern stehen.
    alle_codes = list(dict.fromkeys(c for codes in kandidaten.values() for c in codes))
    with ThreadPoolExecutor(max_workers=REISEINFO_FETCH_PARALLEL) as pool:
        aufgeloest = dict(zip(alle_codes, pool.map(_lade_textbaustein_sicher, alle_codes)))

    # Ein Ausfall EINZELNER Bausteine darf die übrigen nicht kosten, aber er
    # muss sichtbar bleiben: sonst liest sich eine lückenhafte Antwort wie eine
    # vollständige. Gleiche Regel wie im Buchungs-Tool.
    unvollstaendig = any(wert is _FEHLER for wert in aufgeloest.values())

    bloecke: dict[str, list[dict]] = {}
    for block, codes in kandidaten.items():
        treffer = [
            aufgeloest[c]
            for c in codes
            if aufgeloest.get(c) and aufgeloest[c] is not _FEHLER
        ]
        if treffer:
            bloecke[block] = treffer

    return {
        "vorgang": vorgangsnummer,
        "reiseCode": reise_code,
        "isos": _reiseinfo_ziel_isos(reise),
        "bloecke": bloecke,
        "unvollstaendig": unvollstaendig,
    }


reise_info_tools_description = """
Tool für die „Wichtigen Informationen“ zu EINER gebuchten Reise — genau die
Textbausteine, die der Kunde in MeinChamäleon unter seiner Reise findet:

- Reisehinweise: was mitmuss, welche Unterlagen es gibt, wer am Zielflughafen
  abholt
- Checkliste: die Packliste zum Reiseland (Dokumente, Kleidung, Apotheke)
- Informationen Inlands- und Regionalflüge: Freigepäck, Handgepäck, was im
  Reiseland nicht ins Flugzeug darf
- Allgemeine Reiseinformationen: Land für Land Devisen und Zoll, Geld und
  Bezahlen, Trinkgeld, Klima, Strom und Adapter, Gesundheit, Sicherheit,
  Kommunikation, Verpflegung, Unterkünfte

Nutze es bei JEDER Frage zur Vorbereitung einer gebuchten Reise, z.B. „Was muss
ich einpacken?“, „Wie viel Gepäck darf ich mitnehmen?“, „Wie viel Trinkgeld ist
üblich?“, „Brauche ich einen Adapter?“, „Wie viel Bargeld nehme ich mit?“, „Wer
holt mich am Flughafen ab?“, „Wie ist das Klima im Reiseland?“.

Diese Bausteine sind für die gebuchte Reise die verbindliche Quelle: Sie sind
pro Reise und Zielland gepflegt und gehen allgemeinen FAQs vor. Nicht zuständig
ist das Tool für Visum und Einreise (dafür visa_tool), für Termine und Preise
(dafür termine_tool) und für die Daten der Buchung selbst — Flüge, Zahlstand,
Reisende — die stehen in buchungen_tool bzw. buchungen_agentur_tool.

Ruf es OHNE Argument auf — dann nimmt es von selbst die Reise, um die es geht
(die aktuell geöffnete, sonst die nächste des Kunden). Du brauchst dafür weder
eine Buchungsnummer noch einen vorherigen Aufruf von buchungen_tool, und du
fragst den Kunden nie nach seiner Buchungsnummer. Nur wenn er ausdrücklich eine
ANDERE als seine nächste Reise meint, gib deren Buchungsnummer mit — aber rate
nie eine.

Die Antwort enthält nur die Blöcke, die es zu dieser Reise wirklich gibt. Gib
daraus wieder, was zur Frage passt — nicht den ganzen Text — und erfinde nichts
dazu.

Args:
    vorgangsnummer (str, optional): nur für eine andere als die nächste Reise,
        z.B. "226177". Leer lassen heißt: die Reise, um die es gerade geht.

Returns:
    str: Die vorhandenen Blöcke als Markdown, je Baustein mit Überschrift.
""".strip()

# Die Nummer kommt aus der Modellantwort und geht in einen authentifizierten
# API-Aufruf; alles außer Ziffern/Buchstaben hat darin nichts zu suchen.
_VORGANGSNUMMER_RE = re.compile(r"^[A-Za-z0-9-]{1,20}$")

REISEINFO_UNBEKANNT_TEXT = (
    "Zu dieser Buchungsnummer finde ich keine Reise. Bitte prüfe die Nummer."
)
REISEINFO_OHNE_BUCHUNG_TEXT = (
    "Für welche Buchung? Nenne die Buchungsnummer der Reise."
)
REISEINFO_LEER_TEXT = (
    "Zu dieser Reise sind aktuell keine „Wichtigen Informationen“ hinterlegt. "
    "Dein Erlebnisberater hilft dir gern weiter."
)
REISEINFO_FEHLER_TEXT = (
    "Die Reiseinformationen sind gerade nicht abrufbar. Bitte versuche es später "
    "noch einmal."
)

# Deckel auf das, was ins Modell geht — wie PAGE_CONTENT_MAX_CHARS für die
# gescrapte Seite. Gemessen sind 36.000 Zeichen (~10k Token) für eine
# Mehrländerreise; MAX_TOKENS ist eine der gemessenen Ursachen leerer Antworten,
# und der Retry-Pfad in agent.py läuft den Graphen samt Tool-Abrufen komplett
# neu. Der Deckel liegt über allem, was bisher gemessen wurde, und greift nur,
# wenn eine Reise aus dem Rahmen fällt.
REISEINFO_MAX_CHARS = 60_000


def _kuerzen(text: str) -> str:
    """Bei Überlänge hart abschneiden — und das sagen, nicht stillschweigend."""
    if len(text) <= REISEINFO_MAX_CHARS:
        return text
    print(f"[agent_base] reiseinfo gekürzt: {len(text)} > {REISEINFO_MAX_CHARS}")
    return (
        text[:REISEINFO_MAX_CHARS]
        + "\n\n(Gekürzt — es gibt zu dieser Reise noch mehr Informationen.)"
    )


def _reiseinfo_markdown(baustein: dict) -> str:
    """Ein Textbaustein als Markdown — die API liefert ihn als HTML.

    Darf nie werfen: der Text ist redaktionell gepflegt, und kaputtes HTML in
    EINEM Baustein darf die Antwort nicht kosten (gleiche Regel wie in
    ``markdownify_page_html``).
    """
    try:
        markdown_content = markdownify.markdownify(baustein["html"]).strip()
    except Exception as e:
        print(
            f"[agent_base] textbaustein {baustein.get('code')} "
            f"markdownify failed: {type(e).__name__}"
        )
        return ""
    return re.sub(r"\n{3,}", "\n\n", markdown_content)


def _eigene_vorgangsnummern(kunden_id: str, agentur_id: str) -> list[str] | None:
    """Die Buchungen, über die dieser Anfragende Auskunft bekommen darf.

    ``None`` heißt Ausfall (nicht „keine Buchungen“). Ohne Bindung — also ohne
    kunden_id und ohne agentur_id — gibt es nichts: das Tool ist dann gar nicht
    erst gebunden.
    """
    if kunden_id:
        import kundendaten

        return kundendaten.vorgangsnummern(kunden_id)
    if agentur_id:
        import agenturdaten

        return agenturdaten.vorgangsnummern(agentur_id)
    return []


def reiseinfo_vorgang(
    vorgangsnummer: str,
    seiten_vorgang: str = "",
    kunden_id: str = "",
    agentur_id: str = "",
) -> tuple[str, str]:
    """Welche Buchung ist gemeint? Rückgabe ``(nummer, fehler)``.

    Reihenfolge: Modellangabe (nur wenn sie dem Anfragenden gehört) > die offene
    Seite > die nächste eigene Reise. ``fehler`` ist "" oder einer der
    REISEINFO_*_TEXT und hat dann Vorrang vor der Nummer.

    Die Modellangabe wird geprüft, nicht geglaubt. Ungeprüft war sie ein
    Enumerations-Orakel: durchnummerierte Buchungsnummern verrieten, ob eine
    fremde Buchung existiert und zu welchem Land sie gehört, und die Antwort
    rahmte sie als „deine Reise“. Die Prüfliste kostet nichts extra — die
    Auflösung der nächsten Reise braucht sie ohnehin.

    ``seiten_vorgang`` kommt aus der URL der aufgerufenen Seite und damit vom
    Server, nicht aus der Modellantwort; er wird trotzdem mitgeprüft, weil die
    Seite im Kunden-Modus zur Anmeldung passen muss.

    Das Modell soll das Tool ohne Argument aufrufen können: die Nummer selbst
    nachzuschlagen kostete einen zweiten Tool-Aufruf, und in der Lücke dazwischen
    kündigte Gemini dem Kunden gemessen an, es schaue „gleich nach“, und beendete
    den Zug (2026-08-10, 1 von 15 Läufen). Diese Auflösung passiert deshalb hier
    und nicht im Modell.
    """
    eigene = _eigene_vorgangsnummern(kunden_id, agentur_id)
    if eigene is None:
        return "", REISEINFO_FEHLER_TEXT

    for kandidat in (str(vorgangsnummer or "").strip(), str(seiten_vorgang or "")):
        if kandidat and kandidat in eigene:
            return kandidat, ""

    # Die nächste eigene Reise. Die Liste ist wie ``auswahl="alle"`` sortiert,
    # das erste Element ist also die kommende Reise (sonst die zuletzt gereiste).
    #
    # Für die MeinChamäleon-Links im Prompt gilt bewusst etwas anderes:
    # ``kundendaten.naechste_offene_reise`` nimmt NUR offene Reisen und gibt
    # sonst nichts zurück. Hier ist die zuletzt gereiste richtig (ihre Bausteine
    # sind besser als nichts), in einem Link auf „deine Reise" wäre sie falsch.
    if eigene:
        return eigene[0], ""
    return "", REISEINFO_OHNE_BUCHUNG_TEXT


def reiseinfo_tool_base(
    vorgangsnummer: str = "",
    seiten_vorgang: str = "",
    kunden_id: str = "",
    agentur_id: str = "",
) -> str:
    """„Wichtige Informationen“ zu einer Buchung als Markdown. Wirft nie.

    Ohne ``vorgangsnummer`` wird die gemeinte Buchung selbst aufgelöst (siehe
    ``reiseinfo_vorgang``).

    Trennt die Fälle, die für den Kunden verschieden sind: gar keine Buchung,
    unbekannte Buchungsnummer, Reise ohne hinterlegte Bausteine und Ausfall der
    API. Ein Ausfall darf nie als „dazu gibt es nichts“ beim Kunden ankommen —
    er würde daraus schließen, dass er nichts vorbereiten muss.
    """
    nummer, fehler = reiseinfo_vorgang(
        vorgangsnummer, seiten_vorgang, kunden_id, agentur_id
    )
    if fehler:
        return fehler
    if not _VORGANGSNUMMER_RE.match(nummer):
        return REISEINFO_UNBEKANNT_TEXT

    try:
        daten = reiseinfo_tools(nummer)
    except Exception as e:
        # Nur der Fehlertyp: die requests-Exception trägt die volle Request-URL
        # und damit die Vorgangsnummer. Sie identifiziert eine Buchung und
        # gehört so wenig ins Log wie eine Kundennummer.
        print(f"[agent_base] reiseinfo lookup failed: {type(e).__name__}")
        return REISEINFO_FEHLER_TEXT

    if not daten.get("reiseCode"):
        return REISEINFO_UNBEKANNT_TEXT
    bloecke = daten.get("bloecke") or {}
    if not bloecke:
        # Nichts da UND es hat gekracht: das ist ein Ausfall, keine Reise ohne
        # Informationen. Der Unterschied entscheidet, ob der Kunde glaubt, er
        # müsse nichts vorbereiten.
        return REISEINFO_FEHLER_TEXT if daten.get("unvollstaendig") else REISEINFO_LEER_TEXT

    teile = [f"Wichtige Informationen zu deiner Reise (Buchung {nummer}):"]
    for block, bausteine in bloecke.items():
        teile.append(f"# {block}")
        for baustein in bausteine:
            teile.append(f"## {baustein['titel']}\n\n{_reiseinfo_markdown(baustein)}")
    if daten.get("unvollstaendig"):
        # Teilerfolg ehrlich benennen, statt eine lückenhafte Antwort als
        # vollständig auszugeben — gleiche Regel wie im Buchungs-Tool.
        teile.append(
            "(Einzelne Abschnitte konnte ich gerade nicht laden. Sie fehlen "
            "hier, sie sind nicht leer.)"
        )
    return _kuerzen("\n\n".join(teile))
