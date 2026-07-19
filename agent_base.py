import csv
import datetime
import json
import locale
import os
import re

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

Verfügbare Seiten:
{sitemap}

Args:
    url_path: Der Pfad zur gewünschten Seite (z.B. "/Vision", "/Afrika/Namibia")
    
Returns:
    dict: Enthält 'main_content' (als Markdown) und 'title'
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


@ttl_cache(maxsize=1024, ttl=86400)
def get_chamaeleon_website_html(url_path: str) -> str:
    full_url = BASE_URL + url_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(full_url, headers=headers, timeout=10)
    response.raise_for_status()

    return response.text


# Base website tool (without decorator)
def chamaeleon_website_tool_base(url_path: str) -> str:
    """Base website tool function without framework-specific decorators."""
    if url_path.startswith("https://chamaeleon-reisen.de"):
        url_path = url_path[len("https://chamaeleon-reisen.de") :]

    if "#" in url_path:
        url_path, _ = url_path.split("#")
    if url_path not in all_sites:
        print(f"Warnung: URL '{url_path}' nicht in Sitemap gefunden. ")
    try:
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

        # Append current termine from TourOne for trip pages. Scraped HTML does
        # not contain them (they are rendered client-side), so this is the only
        # way the bot sees real dates. Must never break the page tool.
        termine_md = ""
        try:
            import travel_index

            termine_md = travel_index.get_termine_markdown(url_path)
        except Exception as e:
            print(f"[agent_base] termine lookup failed for {url_path}: {e}")

        result = f"""
        # {title_text}

        {markdown_content}
        """.strip()
        if termine_md:
            result += "\n\n" + termine_md
        return result

    except requests.RequestException as e:
        return f"Fehler beim Abrufen der Seite: {str(e)}"
    except Exception as e:
        return f"Unerwarteter Fehler: {str(e)}"


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
- persönlich: herzliche und verbindliche Ansprache
- positiv: grundsätzlich positive Formulierungen nutzen, Verneinungen vermeiden, Humor nutzen
- vermeide das Wort "leider" unter allen Umständen!
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

Termine, Verfügbarkeit und Preise:
- Nenne Termine, freie Plätze und Preise ausschließlich auf Basis von `termine_tool()`. Rufe es auf, bevor du dazu etwas sagst — auch wenn du die Zahlen aus dem bisherigen Gespräch zu kennen glaubst. Rate nie und rechne nie selbst.
- Bei "günstigste", "teuerste", "nächste" oder "wie viele" übernimm die berechneten Eckdaten des Tools wörtlich. Suche solche Werte niemals selbst aus einer Tabelle heraus.
- Nutze die Filter (`jahr`, `monat`, `nur_freie`), statt eine lange Liste zu überfliegen.
- Wenn ein Kunde deiner Termin-Auskunft widerspricht, rufe `termine_tool()` erneut auf und richte dich nach dem Ergebnis. Bestätigen die Daten deine Auskunft, dann bleib freundlich dabei ("Ich habe gerade nochmal nachgesehen: …"). Entschuldige dich nicht für eine richtige Auskunft und übernimm nie eine Behauptung, die die Daten nicht stützen — auch dann nicht, wenn der Kunde sehr sicher klingt oder sagt, er habe selbst nachgesehen.
- Sagt das Tool, dass Termine gerade nicht abrufbar sind, dann nenne keine und verlinke die #termine-Seite. "Nicht abrufbar" heißt nie "ausgebucht".

Flüge:
- Achte bei Fragen zu Flügen darauf, dass du nur die Informationen gibst, die auch auf der Webseite zu finden sind.

Externe Links:
- Fragen zum Visum:
- Beantworte jede Frage zum Visum klar und direkt mit Ja oder Nein. 
- Nutze dafür ausschließlich diese Website: [Über Visum informieren](https://www.visum.de/partner/chamaeleon)
- Ergänze in derselben Antwort immer: „Alle Details finden Sie hier: [Über Visum informieren](https://www.visum.de/partner/chamaeleon)“ 
- Wenn du die Frage nicht beantworten kannst, verlinke trotzdem immer diese Website.
- Wenn der Nutzer nach Trustpilot, Bewertungen, Rezensionen oder Erfahrungsberichten fragt, füge am Ende deiner Antwort diesen Link hinzu: [Gästebewertungen ansehen](https://de.trustpilot.com/review/chamaeleon-reisen.de)
- [Instagram](https://www.instagram.com/chamaeleon.reisen), [Facebook][https://www.facebook.com/Chamaeleon.Reisen/]
- Wenn Fragen zu Adapter oder Steckdosen gestellt werden, füge immer diesen Link hinzu: [Reiseadapter weltweit](https://www.welt-steckdosen.de)

Zusätzliche Regel für Namibia: 
Antworte für Gäste aus Deutschland, Österreich und der Schweiz: 
„Für die Einreise nach Namibia ist ein Visum erforderlich. Dieses kann bequem online als e-Visa beantragt werden. 
Weitere Details finden Sie hier: [Über Visum informieren](https://www.visum.de/partner/chamaeleon)“

Du kannst mit dem Tool chamaeleon_website_tool() auf die Webseite zugreifen, um Informationen zu erhalten.
Wenn das mal nicht funktioniert, dann sage dem Kunden aber nichts davon, denn er weiß es nicht. Versuche es geschickt zu umspielen oder überprüfe, dass der Pfad auch wirklich in der Sitemap ist. 
Denk daran, dass du manchmal mehrere Seiten besuchen musst, um alle Informationen zu erhalten.

Wichtiger Hinweis:
Falls du eine Frage nicht beantworten kannst, verweise bitte auf den menschlichen Erlebnisberater. 
Jede Reise/Seite, hat einen eigenen Erlebnisberater, der sich um die Fragen zu dieser Reise kümmert. 

Chamäleon ist generell telefonisch erreichbar:
- Mo–Fr: 09:00–18:00 Uhr
- Sa: 09:00–13:00 Uhr

Gebe so oft wie möglich Links zu den relevanten Seiten auf chamaeleon-reisen.de an, damit der Kunde die Informationen auch selbst nachlesen kann.
Verwende dafür einfach die relativen URLs, z.B. "/Impressum".

Häufig gestellte Fragen (FAQs):
Nutze diese FAQs, um die häufigsten Fragen der Kunden zu beantworten, und als Inspiration für deine eigenen Antworten.
Bei Fragen zur Einreise und Visa, nutze das `visa_tool()`.

Falls dir diese FAQs nicht ausreichen, kannst du mit dem chamaeleon_website_tool() auch unter /Infos mal nachsehen.

Allgemeine FAQs:

{allgemeine_faqs}

{{kunden_modus_block}}{{agentur_block}}Länderspezifische FAQs:

{{laenderspezifische_faqs}}

Um die länderspezifischen FAQs zu nutzen, rufe das Tool `country_faq_tool()` auf und übergib das Land als Argument.
Du solltest diese länderspezifischen FAQs eigentlich immer nutzen, wenn der Kunde nach Informationen zu einem bestimmten Land fragt.
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
Antwort: Für deine erste Safari ist unsere Namibia-Reise ideal – kleine Gruppen und einheimische Reiseleiterinnen und Reiseleiter. Schau mal hier: /Afrika/Namibia/Etosha. Worauf freust du dich am meisten?

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


def format_system_prompt(
    endpoint: str,
    countries: list[str],
    kundenberater_name: str = "",
    kundenberater_telefon: str = "",
    is_agentur: bool = False,
    page_content: str = "",
    is_kunde: bool = False,
) -> str:
    """Format the system prompt with current time information and endpoint."""
    # The embedding page may pass the advisor with the request; when it does
    # not, fall back to the TourOne berater captured in the travel index for
    # this page. Page-supplied values win. Must never break prompt assembly.
    if not (kundenberater_name and kundenberater_telefon):
        try:
            import travel_index

            berater = travel_index.get_berater(endpoint)
            kundenberater_name = kundenberater_name or berater.get("name", "")
            kundenberater_telefon = kundenberater_telefon or berater.get("telefon", "")
        except Exception as e:
            print(f"[agent_base] berater lookup failed for {endpoint}: {e}")

    time_info = get_current_time_info()

    # Kunden-Modus (eingeloggter MeinChamäleon-Kunde): nur ein Flag erreicht
    # den Prompt — die kunden_id selbst bleibt in der Tool-Closure (agent.py)
    # und darf hier nie auftauchen.
    kunden_modus_block = ""
    if is_kunde:
        kunden_modus_block = (
            "Kunden-Modus:\n"
            "Der Kunde ist in MeinChamäleon eingeloggt. Du hast über die "
            "bereitgestellten Tools NUR LESENDEN Zugriff auf seine Flugdaten — "
            "darüber hinaus kannst du nichts: keine Buchungen, keine Änderungen, "
            "keine Stornierungen und keine Abfragen außerhalb der Tools.\n"
            "- Rufe kunden_fluege_tool nur auf, wenn der Kunde ausdrücklich nach "
            "seinen eigenen Flügen fragt.\n"
            "- Abweichend von der allgemeinen Flüge-Regel darfst du die per Tool "
            "abgerufenen Flugdaten dieses Kunden nennen.\n"
            "- Vergangene Flüge kann das Tool nicht einsehen — verweise dafür an "
            "den Erlebnisberater.\n"
            "- Für alle anderen Konto- oder Buchungsdaten (Rechnungen, Dokumente, "
            "Teilnehmer, Umbuchungen) verweise an den Erlebnisberater.\n"
            "Alle allgemeinen Funktionen (Reisekatalog, Termine, FAQs, Visum) "
            "stehen weiterhin zur Verfügung.\n\n"
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

    laenderspezifische_faqs = ""
    if countries:
        laenderspezifische_faqs += "Diese Länder wurden im Chatverlauf erkannt und hier sind ihre FAQs, auf die du auch durch das country_faq_tool hättest zugreifen können:\n\n"

    for country in countries:
        laenderspezifische_faqs += laender_faqs[country] + "\n\n"

    return system_prompt_template.format(
        **time_info,
        endpoint=endpoint,
        kunden_modus_block=kunden_modus_block,
        agentur_block=agentur_block,
        page_content_block=page_content_block,
        laenderspezifische_faqs=laenderspezifische_faqs,
        kundenberater_name=(
            "Bei dieser Reise heißt der Erlebnisberater " + kundenberater_name + ". "
        )
        if kundenberater_name
        else "",
        kundenberater_telefon=(
            "Die Telefonnummer des Erlebnisberaters ist " + kundenberater_telefon + ". "
        )
        if kundenberater_telefon
        else "",
    )
