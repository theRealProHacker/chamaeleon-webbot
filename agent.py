
import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import locale
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage, AIMessage
import datetime
# from google.ai.generativelanguage_v1beta.types import Tool as GenAITool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType

from langgraph.prebuilt import create_react_agent

locale.setlocale(locale.LC_ALL, 'de_DE.UTF-8')

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not found in environment variables")


TOURONE_API_KEY = os.getenv("TOURONE_BEARER_TOKEN")
if not TOURONE_API_KEY:
    raise ValueError("TOURONE_API_KEY not found in environment variables")


# Parsed sitemap URLs
all_sites = []
with open("sitemap.txt", "r", encoding="utf-8") as f:
    sitemap = f.read()
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            all_sites.append(line)


website_tool_description=f"""
Tool für direkten Zugriff auf chamaeleon-reisen.de Webseiten. Der Kunde sieht jedoch nicht, dass du dieses Tool benutzt.

Verfügbare Seiten:
{sitemap}

Args:
    url_path: Der Pfad zur gewünschten Seite (z.B. "/Vision", "/Afrika/Namibia")
    
Returns:
    dict: Enthält 'main_content' und 'title'
""".strip()

@tool(description=website_tool_description)
def chamaeleon_website_tool(url_path: str) -> dict:
    base_url = "https://www.chamaeleon-reisen.de"
    full_url = base_url + url_path
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(full_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Header extrahieren
        # header = soup.find('header')
        # header_text = header.get_text(strip=True) if header else "Header nicht gefunden"
        
        # Footer extrahieren
        # footer = soup.find('footer')
        # footer_text = footer.get_text(strip=True) if footer else "Footer nicht gefunden"
        
        # Main Content extrahieren
        main = soup.find('main') or soup.find('div', class_='main') or soup.find('body')
        for tag in main.find_all(True):  # True gibt alle Tags zurück
            # Speichere nur die erlaubten Attribute (id und class)
            allowed_attrs = {k: v for k, v in tag.attrs.items() if k in ['id', 'class']}
            tag.attrs = allowed_attrs
        # main_text = main.get_text(strip=True) if main else "Main Content nicht gefunden"
        
        # Title extrahieren
        title = soup.find('title')
        title_text = title.get_text(strip=True) if title else "Titel nicht gefunden"
        
        return {
            'title': title_text,
            'main_content': str(main),
            'status': 'success'
        }
        
    except requests.RequestException as e:
        return {
            'url': full_url,
            'error': f"Fehler beim Abrufen der Seite: {str(e)}",
            'status': 'error'
        }
    except Exception as e:
        return {
            'url': full_url,
            'error': f"Unerwarteter Fehler: {str(e)}",
            'status': 'error'
        }

def make_recommend_trip(container: set[str]):
    @tool(description="Schlage eine oder mehrere Reise vor (z.B. recommend_trip('Nofretete')). ")
    def recommend_trip(trip_id: str|list[str]):
        try: 
            for id in trip_id:
                container.add(id)
        except TypeError:
            assert isinstance(trip_id, str)
            container.add(trip_id)

    return recommend_trip

def make_recommend_human_support(container: list[str]):
    @tool(description="Empfehle den menschlichen Kundenberater anzurufen. ")
    def recommend_human_support():
        container.append(True)

    return recommend_human_support


# model = ChatOpenAI(
#     model_name="gpt-4.1-2025-04-14",
#     temperature=0.3,
#     openai_api_key=OPENAI_API_KEY
# )

model = ChatGoogleGenerativeAI(model="gemini-2.5-flash-preview-04-17", google_api_key=GEMINI_API_KEY)

system_prompt = """
Du bist ein professioneller Kundenbetreuer für das deutsche Reiseunternehmen Chamäleon (https://chamaeleon-reisen.de) mit über 10 Jahren Erfahrung.
Du weißt fast alles über die Firma und kannst auf die interne Webseiten-API zugreifen.
Deine Hauptaufgabe ist es, Kund*innen in einem Chat freundlich, kompetent und im typischen Chamäleon‑Stil zu beraten und Reisen zu empfehlen.

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

Aktuelle Zeitangabe:
- Datum: {date}
- Uhrzeit: {time}
- Wochentag: {weekday}

Der Kunde befindet sich gerade auf folgender Webseite: {endpoint}

Wichtiger Hinweis:
Falls du eine Frage nicht sicher beantworten kannst oder die Antwort zu komplex ist, verweise bitte auf den menschlichen Kundenberater. 
Dieser ist telefonisch erreichbar:
- Mo–Fr: 09:00–18:00 Uhr
- Sa: 09:00–13:00 Uhr

Gebe so oft wie möglich Links zu den relevanten Seiten auf chamaeleon-reisen.de an, damit der Kunde die Informationen auch selbst nachlesen kann.
Du kannst dafür auch einfach die relativen URLs verwenden, z.B. "/Impressum".

Empfehle Reisen, indem du das entsprechende Tool benutzt, z.B. `recommend_trip("Nofretete")`. 
Mache dies auch, wenn die Reise von dir oder dem Kunden erwähnt wird. 

TODO: Ganz viele Beispiele und FAQs.

Halte deine Antworten möglichst präzise, kurz und hilfreich.
""".strip()

site_link_pattern = re.compile(r'\s(/[a-zA-Z\-\_]+)+')
assert all(site_link_pattern.match(url) for url in all_sites)
url_pattern = re.compile(r'https:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)')

def call(messages: list, endpoint: str):
    now = datetime.datetime.now()
    date = now.strftime("%d. %B %Y")
    time = now.strftime("%H:%M")
    weekday = now.strftime("%A")

    chat_history = [
        SystemMessage(content=system_prompt.format(
            date=date,
            time=time,
            weekday=weekday,
            endpoint=endpoint
        ))
    ]
    for msg in messages:
        if msg['role'] == 'user':
            chat_history.append(HumanMessage(content=msg['content']))
        elif msg['role'] == 'assistant':
            chat_history.append(AIMessage(content=msg['content']))

    recommendations = set[str]()
    
    agent_executor = create_react_agent(
        model,
        tools=[
            chamaeleon_website_tool,
            make_recommend_trip(recommendations)
        ],
    )
    
    response = agent_executor.invoke({"messages": chat_history})

    for message in response["messages"]:
        message.pretty_print()

    reply = response["messages"][-1].content

    # Make links:
    reply = site_link_pattern.sub(
        lambda match: f'<a href="{match.group(0)}" target="_blank">{match.group(0)}</a>', 
        reply
    )

    reply = url_pattern.sub(
        lambda match: f'<a href="{match.group(0)}" target="_blank">{match.group(0)}</a>', 
        reply
    )

    print({'reply': reply, 'recommendations': list(recommendations)})
    
    return {'reply': reply, 'recommendations': list(recommendations)}


