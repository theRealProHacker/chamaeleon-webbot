import json
import os
import re
import sys
import time
from contextlib import redirect_stdout
from io import StringIO

import common as _

from agent import call
from agent_base import general_faq_data, laender_faq_data


def safe_print(*args, **kwargs):
    """Print function that safely handles Unicode characters on Windows."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # Fallback: encode problematic characters as ASCII with replacement
        safe_args = []
        for arg in args:
            if isinstance(arg, str):
                safe_args.append(arg.encode("ascii", "replace").decode("ascii"))
            else:
                safe_args.append(str(arg).encode("ascii", "replace").decode("ascii"))
        print(*safe_args, **kwargs)


# --- Keywords for "Pass" Condition ---
# Each question maps to a list of keywords. ALL keywords must be present (case-insensitive)
# in the AI agent's response for the test to pass.
# Keywords can be either:
# - Plain text strings (matched as case-insensitive substrings)
# - Regular expressions (automatically detected by presence of regex special characters)

AMP = r"(?:&amp;|&)"

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
        "rücktrittsversicherung",
        "urlaubsgarantie",
        # Add more specific types if critical, e.g., "reise-krankenversicherung"
    ],
    "Versicherungsangebot": [
        "https://www.chamaeleon-reisen.de/daten/pdfs/hansemerkur_versicherung.pdf"
    ],
    "Versicherungsbedingungen": [
        re.escape(
            "https://m.hmrv.de/documents/168711/897094/vb-rks+2021+%28t-d%29.pdf/01f6f36a-f275-41ee-8ad9-6944207b6fcd"
        )
    ],
    "Ab wann kann ich meine Rail & Fly Tickets einbuchen?": [
        "10 wochen",
        "anreise",
        "digital",
        "reiseunterlagen",
    ],
    "Kann ich mit dem Rail&Fly 1 Tag früher anreisen?": ["ja", "datum", "anpassen"],
    "Kann ich mit dem Rail&Fly 1 Tag später abreisen?": ["ja", "datum", "anpassen"],
    "Brauche ich ein Visum/Impfungen für meine Reise?": [
        "reiseanmeldung",
        "mein chamäleon",
        "einreisebestimmungen",
        "visainformationen",
        "länderinfos",
        "reiseziel",
    ],
    "Wie ist der Altersdurchschnitt auf unseren Reisen?": ["50-60 jahre"],
    "Wo finde ich die Provisionsabrechnung?": [
        "agenturbereich",
        "hochgeladen",
        "https://agt.chamaeleon-reisen.de/agentur/buchungen",
    ],
    "Können Kinder mitreisen?": ["ab ((zwölf)|12) jahren", "geeignet"],
    "Erhalte ich die Reiseunterlagen auch noch per Post?": [
        "digitaler form",
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
    "Wo finde ich meine Flugzeiten?": ["rechnung", "unterlagenlink", "mein chamäleon"],
    "Wo finde ich meine gebuchten Sitzplätze?": [
        "rechnung",
        "unterlagenlink",
        "mein chamäleon",
        "neben den flugzeiten",
        "gebucht sind",
    ],
    "Wie löse ich einen Reisegutschein ein?": [
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

assert set(EXPECTED_KEYWORDS_GENERAL_FAQ.keys()) == set(general_faq_data.keys())

# Hand-crafted keywords for country-specific FAQ questions
# Each question maps to carefully selected keywords that test the AI's specific knowledge
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
    "Botswana: Benötige ich Malaria Prophylaxe? Fragen zu Impfungen": [
        "bcrt",
        "reisepraxen",
        "coupon",
    ],
    "Botswana: Ist eine Stromversorgung in den Lodges durchgängig garantiert?": [
        "durchgängig",
        "generator",
        "nacht",
    ],
    "Botswana: Gibt es WLAN vor Ort?": ["hauptbereich", "gomoti", "kein wlan"],
    "Botswana: Gibt es einen Fön in den Unterkünften?": [
        "nicht alle",
        "strom begrenzt",
        "mitbringen",
    ],
    "Botswana: Bieten die Unterkünfte einen Wäscheservice?": [
        "wäscheservice",
        "zeit",
        "ausreicht",
    ],
    "Botswana: Kann ich das Leitungswasser trinken oder zum Zähnputzen nehmen?": [
        "nicht geeignet",
        "glaskaraffen",
        "trinkwasser",
    ],
    "Botswana: Brauche ich einen Adapter für die Steckdose?": [
        "speziellen stecker",
        "weltstecker",
        "funktioniert nicht",
    ],
    # === AMERIKA ===
    # Argentinien/Chile
    "Argentinien/Chile: Wie lange fliegt man nach Argentinien?": [
        "direktverbindung",
        "lufthansa",
        "13 stunden",
    ],
    "Argentinien/Chile: Wann ist die beste Reisezeit?": [
        "patagonien",
        "november",
        "februar",
    ],
    "Argentinien/Chile: Welche Landeswährung hat Argentinien bzw. Chile?": [
        "argentinischer peso",
        "chilenischer peso",
        "währung",
    ],
    "Argentinien/Chile: Muss vor Abreise Geld getauscht werden?": [
        "nein",
        "euro",
        "usd",
    ],
    "Argentinien/Chile: Kann bei der Aerolinas Argentinas schon vorher Gepäck dazugebucht werden, da die Freigepäckmenge ja nur 15KG beeinhaltet.": [
        "check-in",
        "23kg",
        "überschritten",
    ],
    "Argentinien/Chile: Wieviele Reiseleiter gibt es auf der Reise?": [
        "6 reiseleiter",
        "flug",
        "grenzübertritt",
    ],
    "Argentinien/Chile: Mit welcher Airline wird bei der Reise Patagonia geflogen.": [
        "latam",
        "aerolinas argentinas",
        "airline",
    ],
    # Brasilien
    "Brasilien: Wann ist die Trockenzeit im Amazonas?": [
        "juli",
        "september",
        "sommermonate",
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
        "juni-september",
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
    # Chile/Bolivien/Peru
    "Chile/Bolivien/Peru: Mit welcher Airline wird bei der Reise Altiplano geflogen?": [
        "iberia",
        "klm",
        "air france",
    ],
    "Chile/Bolivien/Peru: Welche Impfungen brauche ich für die Reise Altiplano?": [
        "keine impfungen",
        "gelbfieberimpfung",
        "galápagos",
    ],
    "Chile/Bolivien/Peru: Wie viel Gepäck ist auf den Inlandsflügen inbegriffen?": [
        "23 kg",
        "latam",
        "boliviana",
    ],
    # Costa Rica
    "Costa Rica: Kann man überall rauchen?": [
        "rauchergesetz",
        "ausgewiesene bereiche",
        "ernst",
    ],
    "Costa Rica: Wie schwer sind die Wanderungen?": [
        "unterschiedlich",
        "konkrete auskünfte",
        "anrufen",
    ],
    "Costa Rica: Braucht man eine gute Kondition, um alle Touren mitzumachen?": [
        "normale kondition",
        "nein",
        "reicht aus",
    ],
    "Costa Rica: In welcher Höhe ist man maximal unterwegs?": [
        "unterschiedlich",
        "je nach reise",
        "höhe",
    ],
    "Costa Rica: Welche Stromadapter brauche ich?": [
        "welt-steckdosen.de",
        "schauen",
        "adapter",
    ],
    "Costa Rica: Wie lange dauert der Flug?": ["frankfurt", "san josé", "12h"],
    'Costa Rica: Kann ich auch mal "aussetzen" mit den Touren/Ausflügen?': [
        "hotel bleiben",
        "bus",
        "warten",
    ],
    "Costa Rica: Muss ich Moskitonetze mitbringen?": [
        "nicht notwendig",
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
        "strandspaziergang",
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
        "euro tauschen",
        "kreditkarte",
        "us-dollar",
    ],
    "Ecuador: Ist man auf der Ecuador-Reise in Malariagebieten unterwegs?": [
        "nein",
        "außerhalb",
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
        "angepasst",
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
        "flüge anpassen",
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
        "migracioncolombia",
    ],
    "Kolumbien: Was ist die beste Reisezeit für Kolumbien?": [
        "ganzjährig",
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
        "winter mild",
    ],
    "Australien: Visum?": ["e-visum", "eigenständig", "1 monat"],
    "Australien: Aktivitätslevel?": ["einfach", "bequem", "level"],
    "Australien: Optionale Aktivitäten?": ["opernbesuch", "sydney", "bridge walk"],
    "Australien: Eigenanreise?": ["möglich", "alternative", "geprüft"],
    "Australien: Gepäckbestimmungen?": ["30kg", "40kg", "emirates"],
    "Australien: Essenspräferenzen / Allergien ?": [
        "ohne probleme",
        "umsetzbar",
        "allergien",
    ],
    "Australien: Reiseleitungen ?": ["drei verschiedene", "melbourne", "queensland"],
    # Armenien
    "Armenien: Wird ein Visum benötigt?": ["kein visum", "deutsche", "österreichische"],
    "Armenien: Gibt es eine optionale Aktivität?": [
        "kulinarischer rundgang",
        "jerewan",
        "4 personen",
    ],
    "Armenien: Wie werden die Grenzübergänge erfolgen?": [
        "landweg",
        "kilometer",
        "gepäck",
    ],
    "Armenien: Gibt es eine besondere Kleidervorschrift?": [
        "religiöse stätten",
        "bedeckte kleidung",
        "tuch",
    ],
    # Aserbaidschan
    "Aserbaidschan: Wird ein Visum benötigt?": ["ja", "e-visum", "3 werktage"],
    # Bhutan
    "Bhutan: Visum?": ["indien visum", "one-year-visum", "agentur"],
    "Bhutan: Airline ?": ["lufthansa", "delhi", "fliegen"],
    # China
    "China: Fluggesellschaft?": ["lufthansa", "airline", "fliegen"],
    "China: Abflughafen?": ["münchen", "frankfurt", "abflug"],
    "China: Mitnahme von Drohnen nach China": ["drohne", "registrierung", "städte"],
    "China: Adapter für Steckdosen?": ["gleiche steckdosen", "adapter", "uns"],
    "China: Geld wechseln?": ["bargeld", "reiseleitung", "tauschbar"],
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
        "120ml",
        "brennbar",
    ],
    "China: Visum?": ["30 tage", "kein visum", "dach"],
    "China: Flusskreuzfahrt besonderheiten? (CNYAN)": [
        "drei schiffe",
        "kein pool",
        "bord",
    ],
    # Georgien
    "Georgien: Wird ein Visum benötigt?": ["kein visum", "deutsche", "österreichische"],
    # Indien
    "Indien: Benötigen wir ein Visum?": ["one-year", "120 tagen", "visadienst"],
    "Indien: unterschied zwischen INRAJ und INTAJ": ["ähnlich", "4 tage", "wüste"],
    "Indien: Ist eine Eigenanreise möglich?": ["nein", "eigenanreise", "möglich"],
    "Indien: Geldtauschen?": ["vor ort", "tauschen", "empfehlen"],
    # Japan
    "Japan: Airline ?": ["direktflüge", "lufthansa", "airline"],
    "Japan: Bestuhlung ?": ["3-3-3", "2-3-2", "bestuhlung"],
    "Japan: Eigenanreise?": ["möglich", "transfers", "teuer"],
    "Japan: Sitzplatzreservierung ?": ["standardsitzplatz", "65€", "beinfreiheit"],
    "Japan: Höhere Buchungsklassen?": ["premium", "business", "kalkuliert"],
    "Japan: Geld wechseln?": ["flughafen", "kreditkarte", "währungswechsel"],
    "Japan: JPKYO: Kann man Wanderung auf Pilgerweg aussetzen?": ["ja", "bus", "cafe"],
    # Jordanien
    "Jordanien: Muss ich mich um ein Visum kümmern?": [
        "nein",
        "gruppenvisum",
        "arrival",
    ],
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
    # Laos
    "Laos: Visum?": ["visum benötigt", "eigenständig", "beantragt"],
    # Malaysia
    "Malaysia (MYBOR): Was kostet die Business Class?": [
        "2800 euro",
        "business class",
        "vorkalkuliert",
    ],
    "Malaysia (MYBOR): Was kostet die Premium Economy?": [
        "1400 euro",
        "premium economy",
        "vorkalkuliert",
    ],
    "Malaysia (MYBOR): Ist eine Sitzplatzreservierung möglich?": [
        "economy",
        "keine",
        "premium",
    ],
    "Malaysia (MYBOR): Was kostet eine Sitzplatzreservierung?": [
        "kostenfrei",
        "premium",
        "business",
    ],
    "Malaysia (MYBOR): Wird ein Visum oder elektronische Einreisebestimmung benötigt?": [
        "arrival card",
        "einreisebestimmungen",
        "ausgefüllt",
    ],
    "Malaysia (MYBOR): Mit welcher Airline wird geflogen?": [
        "singapur airlines",
        "malaysian airlines",
        "singapur",
    ],
    "Malaysia (MYBOR): Sind andere Abflughäfen möglich?": [
        "frankfurt",
        "langkawi",
        "kombinieren",
    ],
    "Malaysia (MYBOR): Gibt es die Möglichkeit ein Stop over zu machen/ die Reise zu unterbrechen?": [
        "stopover",
        "singapur",
        "nachträumen",
    ],
    # === EUROPA ===
    # Albanien
    "Albanien: Wie anspruchsvoll sind die Wanderungen ?": [
        "2,5 stunden",
        "llogara",
        "ebene wege",
    ],
    # Azoren
    "Azoren: Wie anstrengend ist die Reise?": [
        "kein spezielles",
        "gut zu fuß",
        "spaziergänge",
    ],
    "Azoren: Benötige ich ein Visum für Portugal?": [
        "nein",
        "ausweisdokument",
        "ausreichend",
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
        "gelaufene km",
        "tag",
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
        "relativ klein",
        "amerikanischen",
        "vergleichen",
    ],
    "Frankreich: Wie groß sind die Betten?": ["1,40 m", "1.90m", "überdecke"],
    "Frankreich: FRPRO: Wann ist die Lavendelblüte?": ["juni", "august", "region"],
    "Frankreich: FRPRO: muss man an der E-Bike Tour durch die Camargue teilnehmen?": [
        "fahrradtour",
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
        "zielgruppe",
        "hauptsächlich",
    ],
    "Island: Wann ist die beste Reisezeit für Island?": [
        "wandern",
        "wale",
        "nordlichter",
    ],
    "Island: Wird es in Island richtig kalt?": ["weder kalt", "wechselhaft", "moment"],
    "Island: Ist diese Reise eine aktive Wanderreise?": [
        "nein",
        "viel unterwegs",
        "wanderungen",
    ],
    "Island: Wieviele Reiseleiter gibt es auf dieser Reise?": [
        "1 reiseleiter",
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
    # Nordmazedonien/Albanien/Montenegro
    "Nordmazedonien/Albanien/Montenegro: Muss ich vorher Euro in Landeswährung tauschen?": [
        "nicht notwendig",
        "denar",
        "lek",
    ],
    "Nordmazedonien/Albanien/Montenegro: Welche Reisezeit ist am besten?": [
        "juni",
        "september",
        "badetemperaturen",
    ],
    "Nordmazedonien/Albanien/Montenegro: Für wen ist die Reise geeignet?": [
        "potpourri",
        "natur",
        "kultur",
    ],
    "Nordmazedonien/Albanien/Montenegro: Welche Reisedokumente brauche ich?": [
        "personalausweis",
        "reisepass",
        "6 monate",
    ],
    "Nordmazedonien/Albanien/Montenegro: Kann die Reise auch ohne Flug gebucht werden?": [
        "eigenanreise",
        "lufthansa",
        "nonstop",
    ],
    "Nordmazedonien/Albanien/Montenegro: Werden viele Serpentinen gefahren?": [
        "serpentinen",
        "reisetabletten",
        "kaugummis",
    ],
    "Nordmazedonien/Albanien/Montenegro: Ist die Reise anstrengend?": [
        "keine wanderungen",
        "kopfsteinpflaster",
        "treppen",
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
    "Norwegen: Aufgabegepäck bei LH ?": ["23kg", "32kg", "business"],
    "Norwegen: Im Hotel Senja teilen sich die Gäste ein Apartment (NUR BEI NOLOF)": [
        "wohnbereich",
        "schlafzimmer",
        "schlüssel",
    ],
    # Portugal
    "Portugal: Benötige ich ein Visum für Portugal?": [
        "nein",
        "ausweisdokument",
        "ausreichend",
    ],
    "Portugal: Haben die Unterkünfte Duschmittel?": [
        "kleine proben",
        "zusätzlich",
        "mitnehmen",
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
        "fährt",
        "stopps",
    ],
    "Schottland: Wie lange fährt man so circa täglich?": [
        "4-5 stunden",
        "täglich",
        "unterschiedlich",
    ],
}


def test_progress_file_name(name: str) -> str:
    return f"tests/test_faq_progress_{name}.json"


def test_results_file_name(name: str) -> str:
    return f"tests/test_faq_results_{name}.json"


def load_progress(name: str):
    """Load previous test progress if it exists."""
    filename = test_progress_file_name(name)
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    # Initialize progress tracking for all questions
    return None


def save_progress(name: str, progress):
    """Save current test progress."""
    filename = test_progress_file_name(name)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=4, ensure_ascii=False)


def load_results(name: str):
    """Load previous test results if they exist."""
    filename = test_results_file_name(name)
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_results(name: str, results):
    """Save detailed test results."""
    filename = test_results_file_name(name)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)


def is_regex_pattern(pattern):
    """Check if a string is intended to be a regular expression pattern."""
    # Consider it a regex if it contains regex special characters
    regex_chars = r"[\[\](){}.*+?^$|\\]"
    return bool(re.search(regex_chars, pattern))


def keyword_matches(keyword, text):
    """Check if keyword matches in text, supporting both plain text and regex."""
    if is_regex_pattern(keyword):
        try:
            # Use regex matching (case-insensitive)
            return bool(re.search(keyword, text, re.IGNORECASE))
        except re.error:
            # If regex is invalid, fall back to plain text matching
            return keyword.lower() in text.lower()
    else:
        # Plain text matching (case-insensitive)
        return keyword.lower() in text.lower()


def test_single_question(question, keywords):
    """Test a single question and return the result."""
    safe_print(f"\nTesting Question: '{question}'")

    with redirect_stdout(StringIO()):
        ai_response = call([{"role": "user", "content": question}], "/")

    missing_keywords = []
    for keyword in keywords:
        if not keyword_matches(keyword, ai_response):
            missing_keywords.append(keyword)

    passed = not missing_keywords

    if passed:
        safe_print(f"  Result: PASS")
    else:
        safe_print(f"  Result: FAIL")
        safe_print(f"    Expected Keywords: {keywords}")
        safe_print(f"    Missing Keywords : {missing_keywords}")
        safe_print(f"    Received Response: '{ai_response}'")

    return {
        "question": question,
        "expected_keywords": keywords,
        "ai_response": ai_response,
        "passed": passed,
        "missing_keywords": missing_keywords,
        "timestamp": time.time(),
    }


test_keywords_lookup = {
    "general": EXPECTED_KEYWORDS_GENERAL_FAQ,
    "country": COUNTRY_EXPECTED_KEYWORDS,
}


def run_tests(name: str):
    keywords = test_keywords_lookup[name]

    # progress
    progress = load_progress(name) or {
        question: {
            "passes": 0,
            "total_attempts": 0,
            "completed": False,
            "last_attempt_passed": False,
        }
        for question in keywords
    }

    # results
    all_results = load_results(name)

    print(f"--- Starting Multi-Pass Country FAQ Test Run ---")
    print(f"Total Country Questions: {len(keywords)}")

    # Show current progress
    completed_questions = sum(1 for p in progress.values() if p["completed"])
    print(f"Already Completed: {completed_questions}")
    print(f"Remaining: {len(keywords) - completed_questions}")
    print("-" * 60)

    # Phase 1: Test all questions until each passes at least once
    print("\n=== PHASE 1: Testing until all country questions pass at least once ===")

    questions_to_test = [q for q, p in progress.items() if not p["last_attempt_passed"]]

    while questions_to_test:
        for question in questions_to_test[
            :
        ]:  # Create a copy to modify during iteration
            if progress[question]["completed"]:
                questions_to_test.remove(question)
                continue

            kws = keywords[question]
            result = test_single_question(question, kws)
            all_results.append(result)

            # Update progress
            progress[question]["total_attempts"] += 1
            progress[question]["last_attempt_passed"] = result["passed"]

            if result["passed"]:
                progress[question]["passes"] += 1
                questions_to_test.remove(question)
                print(f"  ✓ Question passed! Moving to Phase 2 for this question.")
            else:
                print(f"  ✗ Question failed. Will retry in next round.")

            # Save progress after each test
            save_progress(name, progress)
            save_results(name, all_results)

            print("-" * 60)
            time.sleep(10)

        if questions_to_test:
            print(f"\nRetrying {len(questions_to_test)} remaining questions...")

    print(
        "\n[SUCCESS] PHASE 1 COMPLETE: All country questions have passed at least once!"
    )

    # Phase 2: Test questions until they pass 3 times consecutively
    print("\n=== PHASE 2: Testing for consistency (3 consecutive passes) ===")

    questions_needing_consistency = [
        q for q, p in progress.items() if not p["completed"] and p["passes"] < 3
    ]

    while questions_needing_consistency:
        for question in questions_needing_consistency[:]:
            if progress[question]["completed"]:
                questions_needing_consistency.remove(question)
                continue

            keywords = COUNTRY_EXPECTED_KEYWORDS[question]
            current_passes = progress[question]["passes"]

            print(
                f"\nTesting for consistency ({current_passes}/3 passes): '{question}'"
            )

            result = test_single_question(question, keywords)
            all_results.append(result)

            # Update progress
            progress[question]["total_attempts"] += 1

            if result["passed"]:
                progress[question]["passes"] += 1
                progress[question]["last_attempt_passed"] = True

                if progress[question]["passes"] >= 3:
                    progress[question]["completed"] = True
                    questions_needing_consistency.remove(question)
                    print(f"  🎯 Question COMPLETED! (3 consecutive passes achieved)")
                else:
                    print(f"  ✓ Pass {progress[question]['passes']}/3")
            else:
                # Reset passes on failure
                progress[question]["passes"] = 0
                progress[question]["last_attempt_passed"] = False
                print(f"  ✗ Failed! Resetting pass counter to 0/3")

            # Save progress after each test
            save_progress(name, progress)
            save_results(name, all_results)

            print("-" * 60)
            time.sleep(10)

    # Final summary
    print("\n" + "=" * 60)
    print("🏆 ALL COUNTRY FAQ TESTS COMPLETE!")
    print("=" * 60)

    total_questions = len(COUNTRY_EXPECTED_KEYWORDS)
    completed_questions = sum(1 for p in progress.values() if p["completed"])
    total_attempts = sum(p["total_attempts"] for p in progress.values())

    print(f"Total Country Questions: {total_questions}")
    print(f"Successfully Completed: {completed_questions}")
    print(f"Total Test Attempts: {total_attempts}")

    # Show summary by question
    print(f"\n--- Country Question Summary ---")
    for question, prog in progress.items():
        status = (
            "✅ COMPLETED" if prog["completed"] else f"🔄 {prog['passes']}/3 passes"
        )
        print(f"{status} - {prog['total_attempts']} attempts: '{question[:50]}...'")


def run_country_tests():
    run_tests("country")


def run_general_tests():
    run_tests("general")


def main():
    """Main function to choose which tests to run."""

    # Check for command-line arguments
    if len(sys.argv) > 1:
        try:
            choice = sys.argv[1].strip()

            if choice == "0":
                safe_print("Exiting...")
                return
            elif choice == "1":
                safe_print("Starting General FAQ Tests...")
                run_general_tests()
                return
            elif choice == "2":
                safe_print("Starting Country FAQ Tests...")
                run_country_tests()
                return
            elif choice == "3":
                safe_print("Starting General FAQ Tests...")
                run_general_tests()
                safe_print("\nStarting Country FAQ Tests...")
                run_country_tests()
                return
            else:
                safe_print(f"Invalid argument: {choice}")
                safe_print(
                    "Valid options are: 0 (exit), 1 (general), 2 (country), 3 (both)"
                )
                return
        except Exception as e:
            safe_print(
                f"Error processing command-line argument: {str(e).encode('ascii', 'replace').decode('ascii')}"
            )
            return

    # Interactive mode if no command-line arguments
    safe_print("FAQ Testing Suite")
    safe_print("================")
    safe_print("1. Run general FAQ tests")
    safe_print("2. Run country-specific FAQ tests")
    safe_print("3. Run both test suites")
    safe_print("0. Exit")
    safe_print("\nYou can also run directly with: python test_faq.py [1|2|3]")

    while True:
        choice = input("\nPlease choose an option (0-3): ").strip()

        if choice == "0":
            safe_print("Exiting...")
            break
        elif choice == "1":
            safe_print("\nStarting General FAQ Tests...")
            run_general_tests()
            break
        elif choice == "2":
            safe_print("\nStarting Country FAQ Tests...")
            run_country_tests()
            break
        elif choice == "3":
            safe_print("\nStarting General FAQ Tests...")
            run_general_tests()
            safe_print("\nStarting Country FAQ Tests...")
            run_country_tests()
            break
        else:
            safe_print("Invalid choice. Please enter 0, 1, 2, or 3.")


if __name__ == "__main__":
    main()
