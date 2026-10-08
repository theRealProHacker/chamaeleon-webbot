"""Live-Eval der FAQ-Antworten: allgemeine FAQs und Laender-FAQs.

Wiederhergestellt 2026-09-25 aus 975d24a^ (tests/test_faq.py, Sommer 2025).
Geloescht worden war die Datei am 2026-07-04 als "drifted": die Fragen-Liste
passte nicht mehr zu den CSVs, und eine Pruefung beim Import liess die ganze
Sammlung abstuerzen. Seitdem pruefte nichts mehr, ob Leon Laender-FAQ-Fragen
richtig beantwortet.

Die Fragen und Schluesselwoerter sind die von damals, woertlich. Gestrichen
sind nur die 41 Fragen, die es in den FAQs nicht mehr gibt (ueberwiegend
Visum — absichtlich entfernt, siehe faqs/allgemein.md und das visum.de-Tool),
und zwei allgemeine Fragen tragen ihren neuen Wortlaut. Schluesselwoerter
pruefen die ANTWORT, nicht den FAQ-Text: "airline" darf fehlen, wo die FAQ
"Lufthansa" schreibt.

Die Laender-Fragen tragen das Land im Text ("Ägypten: …"). Damit haengt heute
die Laender-FAQ automatisch im Prompt (agent.py, Laender-Erkennung) — genau
der Weg, den B1 streichen wuerde. Die Suite ist deshalb die Kontrolle fuer
B1: beantwortet Leon diese Fragen ohne das Einhaengen genauso gut?

NIE Teil der Standardsuite — jede Frage ist ein Gemini-Aufruf. Blockweise:

    RUN_FAQ_EVAL=1 pytest tests/test_faq.py -q -k allgemein
    RUN_FAQ_EVAL=1 pytest tests/test_faq.py -q -k "land and Tansania"

Ab ~25 Aufrufen am Stueck kippt Gemini in die leere Antwort (TODOS,
2026-09-18): die Laender-Fragen also nach Laendern geteilt fahren, nie alle
auf einmal. Der alte Runner wiederholte jede Frage, bis sie dreimal in Folge
bestand; das misst nichts und sprengt die Aufrufgrenze, er ist nicht
zurueckgekommen.
"""

import os
import re

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

from agent import call
from agent_base import general_faq_data, laender_faq_data
from test_agentur_faq import keyword_matches

RUN = os.getenv("RUN_FAQ_EVAL") == "1"
live = pytest.mark.skipif(not RUN, reason="live FAQ-Eval - RUN_FAQ_EVAL=1 setzen")

AMP = r"(?:&amp;|&)"

# Je Frage die Schluesselwoerter, die ALLE in der Antwort stehen muessen
# (ohne Gross-/Kleinschreibung; mit Regex-Zeichen als Regex gelesen).
EXPECTED_KEYWORDS_GENERAL_FAQ = {
    "Wie kann ich meine Reise bezahlen?": [
        "überweisung",
        "kreditkarte",
        "mastercard",
        "visa",
    ],
    "Wo finde ich den Zahlungslink für die Kreditkartenzahlung?": [
        "rechnung",
        "mail",
        "zugesandt",
    ],
    "Wie hoch ist die Anzahlung?": [
        "20%",
        "reisepreises",
        "restzahlung",
        "4 wochen",
        "reiseantritt",
    ],
    "Wann erhalte ich meine Reiseunterlagen?": [
        "zwei wochen",
        "reisebeginn",
        "flugplan",
        "reisedetails",
        f"rail{AMP}fly-gutscheincodes",
    ],
    "Welche Versicherungen bieten Sie an?": [
        "hansemerkur",
        "reiseversicherung",
        "premiumschutz",
        "basisschutz",
        "rücktritt",
        "urlaubsgarantie",
        # Add more specific types if critical, e.g., "reise-krankenversicherung"
    ],
    "Versicherungsangebot": [
        "https://www.chamaeleon-reisen.de/daten/pdfs/hansemerkur_versicherung.pdf"
    ],
    "Versicherungsbedingungen": [
        # Seit dem FAQ-Sync verlinkt Leon die aktuellen AVB von /Infos.
        re.escape("https://secure-pro.hmrv.de/rda-web/servlet/dokumentadapter")
    ],
    "Ab wann kann ich meine Rail & Fly Tickets einbuchen?": [
        "10 wochen",
        "anreise",
        "digital",
        "reiseunterlagen",
    ],
    "Kann ich mit dem Rail&Fly 1 Tag früher anreisen?": ["ja", "datum", "anpassen"],
    "Kann ich mit dem Rail&Fly 1 Tag später abreisen?": ["ja", "datum", "anpassen"],
    "Wie ist der Altersdurchschnitt auf unseren Reisen?": ["50-60 jahre"],
    "Wo finde ich die Provisionsabrechnung?": [
        "agenturbereich",
        "hochgeladen",
        "https://agt.chamaeleon-reisen.de/agentur/buchungen",
    ],
    "Können Kinder mitreisen?": ["ab ((zwölf)|12) jahren", "geeignet"],
    "Erhalte ich die Reiseunterlagen auch noch per Post?": [
        "digital",
        "bestätigungsunterlagen",
        "post",
    ],
    "Mein gewünschter Termin ist online nicht mehr sichtbar": [
        "ausgebucht",
        "geschlossen",
        "mail",
        "erlebnisberatung@chamaeleon-reisen.de",
    ],
    "Ich möchte Sitzplätze reservieren, wie kann ich das tun?": [
        "mail",
        "erlebnisberatung@chamaeleon-reisen.de",
        "vorgangsnummer",
    ],
    "Wann erhalte ich meine Flugtickets?": [
        "flugtickets .* nicht mehr",
        "flugplan",
        "reiseunterlagen",
    ],
    "Wo finde ich die Flugzeiten meiner gebuchten Reise?": ["rechnung", "unterlagenlink", "mein chamäleon"],
    "Wo finde ich die Sitzplätze meiner gebuchten Reise?": [
        "rechnung",
        "unterlagenlink",
        "mein chamäleon",
        "neben den flugzeiten",
        "gebucht sind",
    ],
    "Wie löse ich einen Reisegutschein ein?": [
        "schritt 2",
        "mail",
        "gutscheinnummer",
        "vorgang",
        "rechnen",
    ],
    "Wie hoch ist die maximale Teilnehmerzahl auf den Reisen?": ["(12)|(zwölf)"],
    "Ich muss meine Reise stornieren wie mache ich das?": [
        "e-mail",
        "vorgangsnummer",
        "erlebnisberatung@chamaeleon-reisen.de",
    ],
}

COUNTRY_EXPECTED_KEYWORDS = {
    # === AFRIKA ===
    # Ägypten
    "Ägypten: Kann ich ab/bis Hamburg fliegen?": ["egypt air", "hamburg", "nein"],
    "Ägypten: E-Visum oder Visa on arrival?": [
        "visa on arrival",
        "kairo",
        "mitarbeiter",
    ],
    "Ägypten: Kann ich von Hurghada ohne Umstieg in Kairo zurück nach Deutschland fliegen?": [
        "nein",
        "ticket",
        "direktverbindung",
    ],
    "Ägypten: Wird das neue Ägyptische Museum (GEM) auf der Reise besucht?": [
        "tutanchamun",
        "museum",
        "eröffnet",
    ],
    "Ägypten: Kann eine Verlängerung auch in einem anderen Hotel gemacht werden": [
        "ja",
        "andere hotels",
        "anfragen",
    ],
    "Ägypten: Kann ich eine Vorübernachtung im ersten Hotel dazubuchen?": [
        "ja",
        "vorübernachtung",
        "anfragen",
    ],
    "Ägypten: Gibt es starke Einschränkungen durch Ramadan?": [
        "nein",
        "respektiere",
        "religion",
    ],
    # Botswana
    "Botswana: Wie kann ich in Botswana bezahlen?": [
        "pula",
        "kreditkarte",
        "geldautomat",
    ],
    "Botswana: Kann ich mit US-Dollar bezahlen?": [
        "unterkünfte",
        "außerhalb",
        "nicht gerne",
    ],
    "Botswana: Brauche ich ein Visum für Botswana?": [
        "kein visum",
        "deutsche",
        "österreichische",
    ],
    "Botswana: Ist eine Dreierbelegung für Reisen nach Namibia möglich?": [
        "doppel",
        "einzelzimmer",
        "bieten",
    ],
    "Botswana: Welche Busse / Fahrzeuge werden vor Ort eingesetzt?": [
        "mercedes sprinter",
        "12-16 sitzer",
        "packtransport",
    ],
    "Botswana: Gibt es Toiletten auf den Fahrtwegen/Strecken?": [
        "natur",
        "restaurants",
        "tankstellen",
    ],
    "Botswana: Kann ich auch Mückenspray vor Ort kaufen?": [
        "supermarkt",
        "reiseleitung",
        "mückenmittel",
    ],
    "Botswana: Ist eine Stromversorgung in den Lodges durchgängig garantiert?": [
        "durchgängig",
        "generator",
        "nacht",
    ],
    "Botswana: Gibt es WLAN vor Ort?": ["hauptbereich", "gomoti", "kein wlan"],
    "Botswana: Gibt es einen Fön in den Unterkünften?": [
        "nicht alle",
        "strom.{0,40}begrenzt",
        "mit(zu)?bringen",
    ],
    "Botswana: Bieten die Unterkünfte einen Wäscheservice?": [
        "wäscheservice",
        "zeit",
        "ausreicht",
    ],
    "Botswana: Kann ich das Leitungswasser trinken oder zum Zähnputzen nehmen?": [
        "nicht.{0,20}geeignet",
        "glaskaraffen",
        "trinkwasser",
    ],
    "Botswana: Brauche ich einen Adapter für die Steckdose?": [
        "speziellen (stecker|adapter)",
        "weltstecker",
        "funktioniert.{0,10}nicht",
    ],
    # === AMERIKA ===
    # Brasilien
    "Brasilien: Wann ist die Trockenzeit im Amazonas?": [
        "juli",
        "september",
    ],
    "Brasilien: Welche Reisestecker muss man für Brasilien mitnehmen?": [
        "typ n",
        "reisestecker",
        "brasilien",
    ],
    "Brasilien: Muss man in Deutschland schon Euro in die Landeswährung tauschen?": [
        "nein",
        "euro",
        "bargeld",
    ],
    "Brasilien: Wann ist die beste Zeit um Jaguare zu beobachten?": [
        "pantanal",
        "juni ?(-|bis) ?september",
        "jaguare",
    ],
    "Brasilien: Bieten Sie diese Reise auch im Februar an?": [
        "nein",
        "regenzeit",
        "april",
    ],
    "Brasilien: Mit welcher Airline wird bei Pantanal Reise geflogen?": [
        "latam",
        "langstrecke",
        "brasilien",
    ],
    # Costa Rica
    "Costa Rica: Kann man überall rauchen?": [
        "rauchergesetz",
        "ausgewiesenen? bereiche",
        "ernst",
    ],
    "Costa Rica: Wie schwer sind die Wanderungen?": [
        "unterschiedlich",
        "konkrete auskünfte",
        "(anrufen|ruf|tel:)",
    ],
    "Costa Rica: Braucht man eine gute Kondition, um alle Touren mitzumachen?": [
        "normale kondition",
        r"reicht\b.*\baus",
    ],
    "Costa Rica: In welcher Höhe ist man maximal unterwegs?": [
        "unterschiedlich",
        "je nach reise",
        "höhe",
    ],
    "Costa Rica: Welche Stromadapter brauche ich?": [
        "welt-steckdosen.de",
        "schau",
        "adapter",
    ],
    "Costa Rica: Wie lange dauert der Flug?": ["frankfurt", "san josé", "12 ?(h|stunden)"],
    'Costa Rica: Kann ich auch mal "aussetzen" mit den Touren/Ausflügen?': [
        "hotel bleiben",
        "bus",
        "wart(en|est)",
    ],
    "Costa Rica: Muss ich Moskitonetze mitbringen?": [
        "nicht (notwendig|nötig|mitbringen)",
        "vorkehrungen",
        "unterkünfte",
    ],
    "Costa Rica: Schuhwerk": ["feste schuhe", "profilsohle", "eingetragen"],
    "Costa Rica: Welche Zahlungsmittel und Währungen sind empfohlen?": [
        "kreditkarte",
        "us-dollar",
        "costa rica colón",
    ],
    "Costa Rica: Welche Flüge sind nach Costa Rica vorgesehen?": [
        "lufthansa",
        "direktflüge",
        "tagflug",
    ],
    "Costa Rica: Wie wird der Transfer auf der Tortuguero-Reise (CRTOR) vom Tango Mar zum Flughafen gestaltet?": [
        "fähre",
        "golf von nicoya",
        "transferbus",
    ],
    "Costa Rica: Kann man im Pazifik baden?": [
        "starke strömungen",
        "pools",
        "strandspazierg",
    ],
    "Costa Rica: CRMIR: kann man, obwohl die Reise in Panama endet, treotzdem ein Anschlussprogramm in Costa Rica buchen?": [
        "panama city",
        "san josé",
        "anschlussprogramm",
    ],
    # Ecuador
    "Ecuador: Wann ist die beste Reisezeit für Ecuador?": [
        "ganzjähriges reiseziel",
        "regen",
        "trockenzeit",
    ],
    "Ecuador: Welche Impfungen brauche ich für Ecuador?": [
        "gelbfieberimpfung",
        "unter 60",
        "europa",
    ],
    "Ecuador: Welche Währung brauche ich für Ecuador?": [
        "us-dollar",
        "keine eigene",
        "landeswährung",
    ],
    "Ecuador: Muss ich vor der Reise Euro in Landeswährung tauschen?": [
        "euro.{0,20}tauschen",
        "kreditkarte",
        "us-dollar",
    ],
    "Ecuador: Ist man auf der Ecuador-Reise in Malariagebieten unterwegs?": [
        "nein",
        "(außerhalb|nicht in malariagebieten)",
        "insektenschutz",
    ],
    "Ecuador: Sind die Wanderungen auf der Reise anstrengend?": [
        "leicht",
        "mittelschwer",
        "trittsicherheit",
    ],
    "Ecuador: Wie viel Gepäck ist bei den Inlandsflügen nach und von Galápagos inbegriffen?": [
        "23 kg",
        "premium economy",
        "business class",
    ],
    "Ecuador: Habe ich auf den Galápagos-Inseln Zeit zum Tauchen oder Schnorcheln?": [
        "schnorcheln",
        "masken",
        "neoprenanzüge",
    ],
    # Kanada
    "Kanada: Brauche ich für Kanada ein Visum?": [
        "eta",
        "elektronische reisegenehmigung",
        "1-3 tage",
    ],
    "Kanada: CAROC: Sind die Wanderungen anstrengend?": [
        "mittelmäßig",
        "fit",
        "ausfallen lassen",
    ],
    "Kanada: CAQUE: Ist die Reise anstrengend?": [
        "nicht besonders",
        "durchschnittlich",
        "(angepasst|passt .{0,40}an)",
    ],
    "Kanada: CAQUE: Müssen die optionalen Aktivitäten vorab angemeldet werden": [
        "nein",
        "vor ort",
        "bezahlung",
    ],
    "Kanada: CAQUE: Gibt es für die Reise ein Anschlussprogramm?": [
        "nein",
        "kein anschlussprogramm",
        "bieten",
    ],
    "Kanada: CAQUE: Kann man früher anreisen und schon ein paar Tage in Toronto verbringen?": [
        "ja",
        "flüge.{0,20}anpassen",
        "toronto",
    ],
    "Kanada: CAQUE: Kann man später abreisen und noch ein paar Tage in Québec City oder in Montreal verbringen?": [
        "ja",
        "québec city",
        "montreal",
    ],
    "Kanada: CAQUE: Wann ist die beste Reisezeit?": [
        "indian summer",
        "september",
        "oktober",
    ],
    # Kolumbien
    "Kolumbien: Ist für Kolumbien eine Gelbfieber-Impfung verpflichtend?": [
        "nicht verpflichtend",
        "dringend empfohlen",
        "gelbfieber",
    ],
    "Kolumbien: Brauche ich für Kolumbien ein Visum?": [
        "kein visum",
        "online-formular",
        r"visum\.de/partner/chamaeleon",
    ],
    "Kolumbien: Was ist die beste Reisezeit für Kolumbien?": [
        "(ganzjährig|ganze jahr)",
        "trockenzeiten",
        "regenzeiten",
    ],
    # === ASIEN ===
    # Australien
    "Australien: Fluggesellschaft?": ["emirates", "qantas", "langstrecke"],
    "Australien: Besonderheiten Flug?": ["economy", "business class", "zubringer"],
    "Australien: Beste Reisezeit?": [
        "ganze jahr",
        "jahreszeiten entgegengesetzt",
        "winter.{0,15}mild",
    ],
    "Australien: Aktivitätslevel?": ["einfach", "bequem", "level"],
    "Australien: Optionale Aktivitäten?": ["opernbesuch", "sydney", "bridge walk"],
    "Australien: Eigenanreise?": ["möglich", "alternative", "(geprüft|prüfen)"],
    "Australien: Gepäckbestimmungen?": ["30 ?kg", "40 ?kg", "emirates"],
    "Australien: Essenspräferenzen / Allergien ?": [
        "ohne probleme",
        "umsetzbar",
        "allergien",
    ],
    "Australien: Reiseleitungen ?": ["drei verschiedene", "melbourne", "queensland"],
    # Armenien
    "Armenien: Gibt es eine optionale Aktivität?": [
        "kulinarische[nr]? rundgang",
        "jerewan",
        "4 personen",
    ],
    "Armenien: Wie werden die Grenzübergänge erfolgen?": [
        "landweg",
        "kilometer",
        "gepäck",
    ],
    "Armenien: Gibt es eine besondere Kleidervorschrift?": [
        "religiöse[nr]? stätten",
        "bedeckte(re)? kleidung",
        "tuch",
    ],
    # Bhutan
    "Bhutan: Airline ?": ["lufthansa", "delhi", "fliegen"],
    # China
    "China: Fluggesellschaft?": ["lufthansa", "airline", "fliegen"],
    "China: Abflughafen?": ["münchen", "frankfurt", "abflug"],
    "China: Mitnahme von Drohnen nach China": ["drohne", "registrierung", "städte"],
    "China: Adapter für Steckdosen?": ["gleichen? steckdosen", "adapter", "uns"],
    "China: Geld wechseln?": ["bargeld", "reiseleit(ung|er)", "tausch(bar|en)"],
    "China: Aktivitätslevel?": ["grundfitness", "gehstrecken", "lang"],
    "China: Bestuhlung vom Flugzeug?": ["3-3-3", "2-3-2", "bestuhlung"],
    "China: Kommunikation:": ["wlan", "vpn", "wechat"],
    "China: relevante Personenbezogene Daten:": [
        "passkopie",
        "körpergewicht",
        "floßfahrten",
    ],
    "China: Hinweise Kosmetik?": ["duschgel", "shampoo", "unterkünfte"],
    "China: Flüssigkeiten auf Inlandsflug und Zugfahrten?": [
        "keine flüssigkeiten",
        "120 ?ml",
        "brennbar",
    ],
    "China: Flusskreuzfahrt besonderheiten? (CNYAN)": [
        "drei .{0,25}schiffe",
        "kein(en)? pool",
        "bord",
    ],
    # Indien
    "Indien: unterschied zwischen INRAJ und INTAJ": ["ähnlich", "(4|vier) tage", "wüste"],
    "Indien: Ist eine Eigenanreise möglich?": ["nein", "eigenanreise", "möglich"],
    "Indien: Geldtauschen?": ["vor ort", "tauschen", "empfehlen"],
    # Japan
    "Japan: Airline ?": ["direktflüge", "lufthansa", "airline"],
    "Japan: Bestuhlung ?": ["3-3-3", "2-3-2", "bestuhlung"],
    "Japan: Eigenanreise?": ["möglich", "transfers", "teuer"],
    "Japan: Sitzplatzreservierung ?": ["standardsitzplatz", "65 ?€", "beinfreiheit"],
    "Japan: Höhere Buchungsklassen?": ["(premium|px)", "(business|bx)", "kalkuliert"],
    "Japan: Geld wechseln?": ["flughafen", "kreditkarte", "währungswechsel"],
    "Japan: JPKYO: Kann man Wanderung auf Pilgerweg aussetzen?": ["ja", "bus", "caf(e|é)"],
    # Jordanien
    "Jordanien: Brauche ich für das Visum ein Passfoto oder ähnliches?": [
        "nein",
        "reisepass",
        "reiseunterlagen",
    ],
    "Jordanien: Findet die Reise statt, bzw. gibt es Sicherheitsbedenken?": [
        "sicherheit",
        "partner",
        "kontakt",
    ],
    "Jordanien: Mit welcher Airline wird geflogen?": [
        "lufthansa",
        "austrian airlines",
        "wien",
    ],
    # === EUROPA ===
    # Azoren
    "Azoren: Wie anstrengend ist die Reise?": [
        "kein spezielles",
        "gut zu fuß",
        "spaziergänge",
    ],
    "Azoren: Wie lange ist der Flug auf die Azoren?": [
        "frankfurt",
        "5 stunden",
        "flug",
    ],
    "Azoren: Wann ist die beste Zeit um Wale zu beobachten auf den Azoren?": [
        "ganzjährig",
        "april",
        "oktober",
    ],
    # Estland
    "Estland: Alle Einzelzimmer sind ausgebucht, aber ich würde gerne ein Einzelzimmer buchen, was nun?": [
        "kontaktformular",
        "einzelzimmer",
        "anfragen",
    ],
    "Estland: Baltikum: Wie viel läuft man auf der Reise?": [
        "10-12",
        "(km|kilometer)",
        "(tag|täglich)",
    ],
    "Estland: Baltikum: Wie anstrengend sind die Wanderungen?": [
        "2-3 km",
        "moorlandschaften",
        "trittsicherheit",
    ],
    "Estland: Soomaa: Werden meine Schuhe bei der Moorwanderung dreckig?": [
        "schneeschuhe",
        "moorschuhe",
        "schmutzig",
    ],
    # Finnland
    "Finnland: Verlänegungen möglich ?": ["nein", "einmal", "woche"],
    "Finnland: wechseln wir das hotel": ["nein", "standortreise", "hotel"],
    "Finnland: benötigen wir besondere Wärmebekleidung": [
        "wärmebekleidung",
        "anzug",
        "handschuhe",
    ],
    "Finnland: was gibt es zu essen": ["deftig", "herzhaft", "fleisch"],
    "Finnland: Nebenkosten vor Ort": ["200-300 euro", "person", "nebenkosten"],
    # Frankreich
    "Frankreich: Wie groß sind die Zimmer?": [
        "klein",
        "(amerikanisch|usa)",
        "(vergleichen|kleiner als)",
    ],
    "Frankreich: Wie groß sind die Betten?": ["1,40 m", "1[.,]90 ?m", "überdecke"],
    "Frankreich: FRPRO: Wann ist die Lavendelblüte?": ["juni", "august", "region"],
    "Frankreich: FRPRO: muss man an der E-Bike Tour durch die Camargue teilnehmen?": [
        "(fahrradtour|e-bike)",
        "aigues-mortes",
        "alternative",
    ],
    # Griechenland
    "Griechenland: Gibt es Wanderungen auf dieser Reise?": [
        "palamidi",
        "reisebus",
        "stadtbesichtigungen",
    ],
    # Island
    "Island: Wann kann man am bsten Nordlichter beobachten?": [
        "oktober",
        "märz",
        "nordlichter",
    ],
    "Island: Was passiert, wenn ein Vulkan ausbricht?": [
        "normal",
        "sehenswert",
        "entspannt",
    ],
    "Island: Welche Zielgruppe bereist Island?": [
        "naturinteressierte",
        "hauptsächlich",
    ],
    "Island: Wann ist die beste Reisezeit für Island?": [
        "wandern",
        "wale",
        "nordlichter",
    ],
    "Island: Wird es in Island richtig kalt?": ["weder (richtig )?kalt", "wechselhaft", "(moment|mehrmals am tag)"],
    "Island: Ist diese Reise eine aktive Wanderreise?": [
        "nein",
        "viel unterwegs",
        "wanderungen",
    ],
    "Island: Wieviele Reiseleiter gibt es auf dieser Reise?": [
        "(1|ein) reiseleiter",
        "fahrer",
        "gleichzeitig",
    ],
    "Island: Warum kommen wir am 1.Tag erst so spät in Reykjavik an?": [
        "flugkontingente",
        "lufthansa",
        "frühere",
    ],
    # Kroatien
    "Kroatien: Mit welcher Fluggesellschaft wird geflogen?": [
        "lufthansa",
        "geflogen",
        "fluggesellschaft",
    ],
    # Norwegen
    "Norwegen: wo sind die Voucher?": [
        "gruppentransfer",
        "reiseleitung",
        "unterschiedlich",
    ],
    "Norwegen: Sitzplatzreservierung vorab möglich": ["25 €", "xl-sitzplatz", "45 €"],
    "Norwegen: Aktivitätslevel": ["einfach", "aktivität", "level"],
    "Norwegen: Tipps zur Kleidung": ["zwiebellook", "schlafmaske", "sonnenbrille"],
    "Norwegen: Nebenkosten vor Ort": ["300", "400 €", "woche"],
    "Norwegen: Aufgabegepäck bei LH ?": ["23 ?kg", "32 ?kg", "business"],
    "Norwegen: Im Hotel Senja teilen sich die Gäste ein Apartment (NUR BEI NOLOF)": [
        "wohnbereich",
        "schlafzimmer",
        "(schlüssel|abschließ|verschließ)",
    ],
    # Portugal
    "Portugal: Haben die Unterkünfte Duschmittel?": [
        "kleine .{0,25}proben",
        "zusätzlich",
        "mit(zu)?nehmen",
    ],
    "Portugal: Muss ich gut zu Fuß sein?": [
        "grundfitness",
        "stadtbesichtigungen",
        "treppen",
    ],
    # Rumänien
    "Rumänien: gibt es im Kloster Strom": ["ja", "gästeräume", "strom"],
    # Schottland
    "Schottland: Ist die Tour anstrengend, muss man gut zu Fuß sein?": [
        "nicht anstrengend",
        "f(ä|a)hr(t|st)",
        "stopps",
    ],
    "Schottland: Wie lange fährt man so circa täglich?": [
        "4-5 stunden",
        "täglich",
        "unterschiedlich",
    ],
}


def _land_und_frage(schluessel: str) -> tuple[str, str]:
    land, frage = schluessel.split(": ", 1)
    return land, frage


def _pruefe(frage: str, schluesselwoerter: list[str]) -> None:
    reply = call([{"role": "user", "content": frage}], "/")
    fehlend = [k for k in schluesselwoerter if not keyword_matches(k, reply)]
    assert not fehlend, f"fehlt {fehlend}\n--- reply ---\n{reply}"


@live
@pytest.mark.parametrize(
    "frage", list(EXPECTED_KEYWORDS_GENERAL_FAQ), ids=lambda f: f"allgemein-{f[:40]}"
)
def test_allgemein(frage):
    _pruefe(frage, EXPECTED_KEYWORDS_GENERAL_FAQ[frage])


@live
@pytest.mark.parametrize(
    "frage", list(COUNTRY_EXPECTED_KEYWORDS), ids=lambda f: f"land-{f[:50]}"
)
def test_land(frage):
    _pruefe(frage, COUNTRY_EXPECTED_KEYWORDS[frage])


# --- Ohne Netz: die Fragen gibt es noch --------------------------------------
#
# Das war frueher eine Pruefung beim Import, die bei jedem FAQ-Umbau die ganze
# Sammlung abstuerzen liess — und am Ende zur Loeschung der Datei fuehrte.
# Jetzt ein normaler Test: er sagt, WELCHE Frage verschwunden ist.


def test_allgemeine_fragen_gibt_es_noch():
    weg = [f for f in EXPECTED_KEYWORDS_GENERAL_FAQ if f not in general_faq_data]
    assert not weg, f"nicht mehr in faqs/Allgemeine_FAQ.csv: {weg}"


def test_laender_fragen_gibt_es_noch():
    weg = [
        s
        for s in COUNTRY_EXPECTED_KEYWORDS
        if _land_und_frage(s)[1] not in laender_faq_data.get(_land_und_frage(s)[0], {})
    ]
    assert not weg, f"nicht mehr in den Laender-FAQs: {weg}"
