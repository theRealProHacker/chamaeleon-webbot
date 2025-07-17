import json
import os
import re
import markdownify
import pytz
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from dotenv import load_dotenv
import locale
import datetime
from functools import cache

# Set German locale
locale.setlocale(locale.LC_ALL, 'de_DE.UTF-8')

# Load environment variables
load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# if not OPENAI_API_KEY:
#     raise ValueError("OPENAI_API_KEY not found in environment variables")

TOURONE_API_KEY = os.getenv("TOURONE_BEARER_TOKEN")
if not TOURONE_API_KEY:
    raise ValueError("TOURONE_API_KEY not found in environment variables")

# Load sitemap URLs
all_sites: list[str] = []
trip_sites: list[str] = []
with open("sitemap.txt", "r", encoding="utf-8") as f:
    sitemap = f.read()
    recording_trip_sites = False
    for line in sitemap.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            all_sites.append(line)
            if recording_trip_sites and line.count('/') >= 3:
                trip_sites.append(line)
        elif line == "## Reiseziele":
            recording_trip_sites = True
        elif line == "## Nachhaltigkeit":
            recording_trip_sites = False

# print(len(all_sites), "total URLs found in sitemap")
# print(len(trip_sites), "trip URLs found in sitemap")

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

# Load country-specific FAQs
with open("faqs/laender.json", "r", encoding="utf-8") as f:
    laender_faqs = json.load(f)

faq_continents = list(laender_faqs.keys())
faq_countries = {continent: list(laender_faqs[continent].keys()) for continent in faq_continents}

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
{'\n'.join(': '.join(item) for item in visa_labels.items())}
""".strip()

@cache
def visa_tool_base(country: str) -> str:
    land_id = country.upper()
    if land_id not in visa_labels:
        raise ValueError(f"Unbekanntes Land: {land_id}. Verfügbare Länder: {', '.join(visa_labels.keys())}")

    url = f"https://www.visum.de/Visum-beantragen/Visumbeschaffung-beauftragen/apply_visa.php?land_id={land_id}&bundesland_id=AUSLAND"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        content = soup.find('div', attrs={'id': 'content_box'})
            
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
website_tool_description = f"""
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

BASE_URL = "https://www.chamaeleon-reisen.de"

@cache
def get_chamaeleon_website_html(url_path: str) -> str:
    full_url = BASE_URL + url_path
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = requests.get(full_url, headers=headers, timeout=10)
    response.raise_for_status()

    return response.text

# Base website tool (without decorator)
def chamaeleon_website_tool_base(url_path: str) -> str:
    """Base website tool function without framework-specific decorators."""
    try:
        content = get_chamaeleon_website_html(url_path)
        
        soup = BeautifulSoup(content, 'html.parser')
        
        # Main Content extrahieren
        main = soup.find('main') or soup.find('div', class_='main') or soup.find('body')
        
        # Title extrahieren
        title = soup.find('title')
        title_text = title.get_text(strip=True) if title else "Titel nicht gefunden"
        
        # Convert main content to markdown
        markdown_content = markdownify.markdownify(str(main)).strip()

        # Remove multiple line breaks
        markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)

        return f"""
        # {title_text}

        {markdown_content}
        """.strip()
        
    except requests.RequestException as e:
        return f"Fehler beim Abrufen der Seite: {str(e)}"
    except Exception as e:
        return f"Unerwarteter Fehler: {str(e)}"
    
country_faq_tool_description = f"""
Tool für den Zugriff auf länderspezifische FAQs von Chamäleon Reisen.
Verfügbare Kontinente: {', '.join(faq_continents)}
Verfügbare Länder:
{', '.join([f"{continent}: {', '.join(faq_countries[continent])}" for continent in faq_continents])}
Args:
    continent (str): Der Kontinent, für den die FAQs abgerufen werden sollen.
    country (str): Das Land, für das die FAQs abgerufen werden sollen.
Returns:
    str: Die FAQs für das angegebene Land im Markdown-Format.
Raises:
    ValueError: Wenn der Kontinent oder das Land unbekannt ist.
""".strip()

def country_faq_tool_base(continent: str, country: str) -> str:
    if continent not in laender_faqs:
        raise ValueError(f"Unbekannter Kontinent: {continent}. Verfügbare Kontinente: {', '.join(faq_continents)}")
    if country not in laender_faqs[continent]:
        raise ValueError(f"Unbekanntes Land: {country} im Kontinent {continent}. Verfügbare Länder: {', '.join(faq_countries[continent])}")
    faqs = laender_faqs[continent][country]
    return faqs


# Base tool factory functions (without decorators)
def make_recommend_trip_base(container: set[str]):
    """Create a trip recommendation function that stores results in the given container."""
    def recommend_trip(trip_id: str|list[str]):
        try: 
            for id in trip_id:
                container.add(id)
        except TypeError:
            assert isinstance(trip_id, str)
            container.add(trip_id)
    return recommend_trip

def make_recommend_human_support_base(container: list[str]):
    """Create a human support recommendation function that stores results in the given container."""
    def recommend_human_support():
        container.append(True)
    return recommend_human_support

# System prompt template
system_prompt_template = f"""
Du bist ein professioneller Kundenbetreuer für das deutsche Reiseunternehmen Chamäleon (https://chamaeleon-reisen.de) mit über 10 Jahren Erfahrung.
Du weißt fast alles über die Firma und kannst auf die interne Webseiten-API zugreifen.
Deine Hauptaufgabe ist es, Kund*innen in einem Chat freundlich, kompetent und im typischen Chamäleon‑Stil zu beraten und Reisen zu empfehlen!

Spezialisiert auf Erlebnis- und Abenteuerreisen in kleinen Gruppen (maximal 12 Teilnehmende), legt Chamäleon Wert auf:
- Nachhaltigkeit: 60% lokaler Verdienst, aktive Projekte wie Regenwaldschutz und soziale Initiativen vor Ort.
- Authentizität: Handverlesene Unterkünfte, regionale Partner und einheimische Reiseleiter*innen ermöglichen tiefgehende Begegnungen.
- Vielfalt der Reisen: Vom Abenteuer in Namibia über Genießer-Touren in Italien bis zu Erlebnis-Tagen in Deutschland.
- Transparenz: Klare Leistungsübersichten und faire Preise ohne versteckte Kosten.

Sprache & Stil:
- Sprich die Kunden bitte per DU an
- Sei stets freundlich, direkt und fasse dich kurz.
- Verwende direkte, einladende Formulierungen und rhetorische Fragen.
- Formuliere kurze, prägnante Sätze.

Formatierung:
- Formatiere deine Antworten in HTML, damit sie direkt auf der Webseite angezeigt werden können. Vermeide Markdown-Formatierung, wie zum Beispiel **fett**, _kursiv_ oder * Listeneintrag 1 \n * Listeneintrag 2!
- Nutze HTML-Links inklusive mailto-Links. Zum Beispiel: <a href="mailto:erlebnisberatung@chamaeleon-reisen.de">erlebnisberatung@chamaeleon-reisen.de</a>
- Achte darauf, dass du immer target="_blank" für externe Links verwendest, damit sie in einem neuen Tab geöffnet werden.

Aktuelle Zeitangabe:
- Datum: {{date}}
- Uhrzeit: {{time}}
- Wochentag: {{weekday}}

Der Kunde befindet sich gerade auf folgender Webseite: {{endpoint}}. Gehe davon aus, dass sich Fragen auf diese Seite beziehen.

Du kannst mit dem Tool chamaeleon_website_tool() auf die Webseite zugreifen, um Informationen zu erhalten.
Denk daran, dass du manchmal mehrere Seiten besuchen musst, um alle Informationen zu erhalten.

Zum Beispiel:
TODO


Wichtiger Hinweis:
Falls du eine Frage nicht sicher beantworten kannst oder die Antwort zu komplex ist, verweise bitte auf den menschlichen Erlebnisberater. 
Jede Reise/Seite, hat einen eigenen Erlebnisberater, der sich um die Fragen zu dieser Reise kümmert. 
{{kundenberater_name}}
{{kundenberater_telefon}}

Die Chamäleon ist generell telefonisch erreichbar:
- Mo–Fr: 09:00–18:00 Uhr
- Sa: 09:00–13:00 Uhr

Gebe so oft wie möglich Links zu den relevanten Seiten auf chamaeleon-reisen.de an, damit der Kunde die Informationen auch selbst nachlesen kann.
Verwende dafür einfach die relativen URLs, z.B. "/Impressum".

Empfehle Reisen, indem du das entsprechende Tool benutzt, z.B. `recommend_trip("Nofretete")`. 
Mache dies immer, wenn die Reise von dir oder dem Kunden auf irgendeine Weise erwähnt wird. 
Bevor du eine finale Antwort gibst, solltest du immer prüfen, ob du eine Reise empfehlen kannst.

Häufig gestellte Fragen (FAQs):
Nutze diese FAQs, um die häufigsten Fragen der Kunden zu beantworten, und als Inspiration für deine eigenen Antworten.

Falls dir diese FAQs nicht ausreichen, kannst du mit dem chamaeleon_website_tool() auch unter /Infos mal nachsehen.

Allgemeine FAQs:

{allgemeine_faqs}

Länderspezifische FAQs:

Um die länderspezifischen FAQs zu nutzen, rufe das Tool `country_faq_tool()` auf und übergib den Kontinent und das Land als Argumente.
Du solltest diese länderspezifischen FAQs eigentlich immer nutzen, wenn der Kunde nach Informationen zu einem bestimmten Land fragt.

Wichtigste Hinweise:
Halte deine Antworten möglichst präzise, kurz und hilfreich. 
Versuche die Antworten auf 200 Zeichen zu beschränken, damit sie gut lesbar sind und auf der Webseite angezeigt werden können.
Empfehle Reisen, sooft wie möglich, indem du das `recommend_trip()`-Tool benutzt.
""".strip()

# URL patterns for link processing
site_link_pattern = re.compile(r'(?:/[a-zA-Z\-\_]+)*')
assert all(site_link_pattern.match(url) for url in all_sites)
url_pattern = re.compile(r'\s(https:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*))')

# def process_links_in_reply(reply: str) -> str:
#     """Process and convert URLs in the reply to HTML links."""
#     # Make site links
#     reply = site_link_pattern.sub(
#         lambda match: f'<a href="{match.group(1)}" target="_blank">{match.group(1)}</a>', 
#         reply
#     )
    
#     # Make external URLs
#     reply = url_pattern.sub(
#         lambda match: f'<a href="{match.group(1)}" target="_blank">{match.group(1)}</a>', 
#         reply
#     )
    
#     return reply

def detect_recommendation_links(reply: str) -> set[str]:
    """
    Detect and return a set of recommendation links from the reply.
    """
    links = set()
    for match in site_link_pattern.finditer(reply):
        link = match.group(0)
        if link in trip_sites:
            links.add(link)
    return links

def get_current_time_info() -> dict:
    """Get current date, time, and weekday formatted for German locale."""

    now = datetime.datetime.now(pytz.timezone('Europe/Berlin'))
    return {
        'date': now.strftime("%d. %B %Y"),
        'time': now.strftime("%H:%M"),
        'weekday': now.strftime("%A")
    }

def format_system_prompt(endpoint: str, kundenberater_name: str = "", kundenberater_telefon: str = "") -> str:
    """Format the system prompt with current time information and endpoint."""
    time_info = get_current_time_info()
    return system_prompt_template.format(
        **time_info,
        endpoint=endpoint,
        kundenberater_name=('Bei dieser Reise heißt der Erlebnisberater ' + kundenberater_name + '. ') if kundenberater_name else '',
        kundenberater_telefon=('Die Telefonnummer des Erlebnisberaters ist ' + kundenberater_telefon + '. ') if kundenberater_telefon else ''
    )
