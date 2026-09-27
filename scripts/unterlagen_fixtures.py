"""Synthetische Fixture-PDFs für tests/test_unterlagen.py (und später die Evals).

Nachgebaut ist nur die STRUKTUR echter Chamäleon-Unterlagen (Überschriften,
Reihenfolge, Tageseinträge beider Template-Generationen, Personenköpfe der
Einreisebestimmungen). Alle Personen-, Buchungs- und Kontaktdaten sind
erfunden: Gäste Musterfrau/Beispielmann/Probst, Vorgänge 9000xx, Kunde 8000xx,
Telefonnummern mit Nullen. Kein Text stammt aus einer echten Buchung.

Braucht reportlab (nur lokal, nicht in _requirements.txt):

    pip install reportlab
    python scripts/unterlagen_fixtures.py

Die PDFs werden mit ``invariant=1`` geschrieben, ein erneuter Lauf erzeugt
dieselben Bytes.
"""

import os
import textwrap

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ZIEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "fixtures",
    "unterlagen",
)

# Seitenumbruch im Quelltext: eine Zeile, die nur aus "\f" besteht.
SEITE = "\f"

REISEUNTERLAGEN_NEU = """\
Reiseunterlagen
NAMIBIA
WÜSTENWIND
12 Tage Erlebnis-Reise
Erika Musterfrau | Theo Beispielmann
Dienstag, 06.10.2026 bis Samstag, 17.10.2026
Erlebnisberater*in Paula Platzhalter
Telefon +49 30 0000000-000
erlebnisberatung@example.invalid
Kundennummer 800001
Vorgang 900001
\f
HIGHLIGHTS
Sonnenaufgang auf den Dünen der Namib
Pirschfahrten im Etosha-Nationalpark
Abendessen unter dem Sternenhimmel
WÜSTENWIND
12 TAGE ERLEBNIS-REISE
NAMIBIA
DEINE CHAMÄLEON-REISELEITUNG:
Herr Jonas Fiktiv +264 00 000 0000
Di 06.10.2026 Abflug nach Namibia
Heute beginnt dein Abenteuer. Am Abend fliegst du über Nacht in Richtung Windhoek und kannst
an Bord schon ein wenig von der Weite des südlichen Afrikas träumen.
Mi 07.10.2026 Willkommen in Windhoek
Nach der Landung empfängt dich deine Reiseleitung am Flughafen und begleitet dich in die
Hauptstadt. Bei einer kleinen Stadtrundfahrt siehst du die Christuskirche und den alten Bahnhof.
Du übernachtest im Gästehaus Kameldorn, einem familiär geführten Haus mit schattigem Garten
und kleinem Pool am Rand der Innenstadt.
Das Abendessen genießt du gemeinsam mit deiner Gruppe in einem landestypischen Restaurant.
Die Fahrstrecke umfasst ca. 45 km.
Do 08.10.2026 In die Namib
Über den Spreetshoogte-Pass geht es hinunter in die Wüste. Unterwegs legst du Fotostopps an
den roten Hängen ein und erreichst am Nachmittag deine Unterkunft am Rand der Dünen.
Die Dünenblick Lodge liegt einsam in einem weiten Tal, ihre Chalets aus Naturstein bieten freie
Sicht auf die Berge. Am Abend sitzt du am Feuer und hörst die Stille der Wüste.
Frühstück und Abendessen sind inklusive.
Die Fahrstrecke umfasst ca. 320 km.
Fr 09.10.2026 Die Dünen am Sossusvlei
Noch vor Sonnenaufgang brichst du auf, um die Dünen im ersten Licht zu erleben. Wer mag,
steigt auf eine der hohen Dünen, danach wanderst du durch das Deadvlei mit seinen alten Bäumen.
Du übernachtest in derselben Unterkunft wie am Vortag.
Frühstück und Abendessen sind inklusive.
Sa 10.10.2026 An die Küste nach Swakopmund
Durch den Kuiseb-Canyon und über den Wendekreis des Steinbocks erreichst du den Atlantik.
In Swakopmund bummelst du an der Promenade entlang und probierst frischen Fisch.
Das Hotel Seebrise liegt nur wenige Schritte vom Strand entfernt und hat eine ruhige Terrasse.
Das Frühstück ist inklusive.
Die Fahrstrecke umfasst ca. 350 km.
So 11.10.2026 Zum Etosha-Nationalpark
Heute geht es ins Landesinnere. Am Nachmittag erreichst du den Rand des Nationalparks, wo du
in der Buschfeuer Lodge mit eigenem Wasserloch übernachtest.
Frühstück und Abendessen sind inklusive.
Die Fahrstrecke umfasst ca. 420 km.
Mo 12.10.2026 Auf Pirsch in Etosha
Den ganzen Tag bist du im offenen Safarifahrzeug unterwegs und hältst Ausschau nach Elefanten,
Giraffen und Löwen an den Wasserlöchern der weiten Salzpfanne.
Du übernachtest in derselben Unterkunft wie am Vortag.
Frühstück und Abendessen sind inklusive.
Di 13.10.2026 Rückfahrt nach Windhoek und Weiterflug
Nach dem Frühstück fährst du zurück nach Windhoek. Am Abend fliegst du weiter nach Kapstadt
oder zurück nach Deutschland.
Das Frühstück ist inklusive.
LEISTUNGEN BEI CHAMÄLEON
- Linienflug mit einer renommierten Fluggesellschaft ab/bis Deutschland
- Rail&Fly in der 2. Klasse inklusive ICE-Nutzung
- 7 Übernachtungen in Lodges, in einem Gästehaus und in einem Hotel
- Täglich Frühstück, 6 x Abendessen
- 2 Pirschfahrten im Etosha-Nationalpark
- Deutsch sprechende einheimische Reiseleitung
HINWEISE ZU DEN LEISTUNGEN UND ZUR REISE
- Falls einzelne der genannten Unterkünfte nicht verfügbar sind, wird eine möglichst
gleichwertige Alternative gebucht.
- Bei Buchung mit Anreise in Eigenregie sind Linienflug und Transfers nicht enthalten.
HINWEISE ZU UNSEREN EMPFEHLUNGEN
- Die empfohlenen Aktivitäten buchst und bezahlst du vor Ort bei deiner Reiseleitung.
KAPSTADT
NACHTRÄUMEN
SÜDAFRIKA
Di 13.10.2026 Flug nach Kapstadt
Am Abend landest du in Kapstadt und wirst zu deinem Hotel an der Waterfront gebracht.
Mi 14.10.2026 Tafelberg und Kap der Guten Hoffnung
Mit der Seilbahn fährst du auf den Tafelberg, danach geht es entlang der Küste bis zum Kap.
Das Hotel Hafenlicht liegt direkt an der Waterfront, viele Restaurants erreichst du zu Fuß.
Das Frühstück ist inklusive.
Do 15.10.2026 Tag zur freien Verfügung
Heute hast du Zeit für eigene Entdeckungen, etwa für einen Besuch im Botanischen Garten.
Fr 16.10.2026 Abschied von Kapstadt
Am Nachmittag bringt dich ein Transfer zum Flughafen, am Abend fliegst du nach Hause.
Sa 17.10.2026 Wieder zu Hause
Am Vormittag landest du in Deutschland.
LEISTUNGEN BEI CHAMÄLEON
- 3 Übernachtungen im Hotel Hafenlicht
- Transfers Flughafen - Hotel - Flughafen
\f
WICHTIGE REISEHINWEISE
WAS MUSS MIT? WER HOLT MICH AB?
Bitte sieh deine Reiseunterlagen sorgfältig durch und melde dich bei Unklarheiten umgehend bei
uns. Achte besonders auf die richtige Schreibweise deiner Namen im Flugplan.
Zusätzliche Reiseunterlagen
Voucher für gebuchte Zusatzprogramme erhältst du vor Ort von unserer Partneragentur, sofern sie
diesen Unterlagen nicht beiliegen.
Abholung am Zielflughafen
Am Flughafen Windhoek erwartet dich deine Reiseleitung mit einem Schild mit der Aufschrift
»Chamäleon«. Befestige bitte den Kofferanhänger an deinem Gepäck, damit man dich leichter erkennt.
Kontaktdaten vor Ort:
Beispiel Safaris Namibia
Tel.: +264-(0)61-000000 / E-Mail: kontakt@example.invalid
Frau Lena Erfunden (Deutsch sprechend), Mobil (für Notfälle): +264-(0)81-000-0000
Notfalltelefonnummer
In dringenden Fällen erreichst du uns rund um die Uhr unter der Notfallnummer +49-(0)170-0000000
und per WhatsApp.
ICH BIN DANN MAL WEG...
Check-in am Flughafen: Wir empfehlen, drei Stunden vor dem Abflug am Schalter zu sein.
CHECKLISTE
DOKUMENTE
Reisepass, noch mindestens sechs Monate über das Reiseende hinaus gültig
Flugplan und Reiseunterlagen
APOTHEKE
Sonnenschutz mit hohem Lichtschutzfaktor
KLEIDUNG
warme Jacke für die kühlen Nächte in der Wüste
INFORMATIONEN LINIENFLUG
Gepäckbestimmungen für Flüge nach Namibia mit einer Beispiel-Fluggesellschaft
Es gilt eine Freigepäckmenge von einem Gepäckstück bis 23 kg in der Economy Class.
INFORMATIONEN INLANDS- UND
REGIONALFLÜGE
Gepäckbestimmungen für den Regionalflug nach Kapstadt
Es gilt eine Freigepäckmenge von 20 kg, das Handgepäck darf höchstens 7 kg wiegen.
EU-HANDGEPÄCKREGELUNG.
Flüssigkeiten im Handgepäck nur in Behältern bis 100 ml in einem durchsichtigen Beutel.
\f
ALLGEMEINE REISEINFORMATIONEN
Reisen in kleinen Gruppen
Unsere Reisen finden in kleinen Gruppen statt, damit du nah an Land und Leuten bist und nicht im
Strom großer Reisegruppen untergehst.
REISEINFORMATIONEN WÜSTENWIND
Fahrzeuge
Du reist in einem klimatisierten Kleinbus mit großen Fenstern, in Etosha zusätzlich im offenen
Safarifahrzeug. Die Sitzplätze wechseln täglich, damit jede und jeder einmal vorne sitzt.
Geld und Kreditkarten
Die Landeswährung ist der Namibia-Dollar. Kreditkarten werden in Lodges und größeren Geschäften
fast überall akzeptiert, für Trinkgelder solltest du etwas Bargeld dabeihaben.
Strom
In den Lodges in der Wüste gibt es Strom aus Solaranlagen, der abends zeitweise abgeschaltet wird.
Einen Adapter für dreipolige Steckdosen solltest du auf jeden Fall mitnehmen.
WIE HAT ES DIR GEFALLEN?
Wir freuen uns auf deine Rückmeldung nach der Reise.
NOTIZEN
NOTIZEN
"""

# Ältere Generation: Tage ohne Wochentag, Hotel als eigene Zeile mit URL, keine
# WICHTIGEN REISEHINWEISE (Reisebestätigung), BERATUNG am Ende und ein
# Reisename, der in der Überschrift umbricht.
REISEBESTAETIGUNG_ALT = """\
Reisebestätigung
AUSTRALIEN
ROTE ERDE
10 Tage Erlebnis-Reise
Erika Musterfrau | Theo Beispielmann
Mittwoch, 03.03.2027 bis Freitag, 12.03.2027
Erlebnisberater*in Paula Platzhalter
Telefon +49 30 0000000-000
Vorgang 900002
\f
HIGHLIGHTS
Sonnenuntergang am Uluru
Wanderung durch den Kings Canyon
ROTE ERDE
10 TAGE ERLEBNIS-REISE
AUSTRALIEN
03.03.2027 Los geht's!
Es ist ein langer Weg nach Down Under, aber er lohnt sich. Heute fliegst du los.
04.03.2027 Ankunft in Alice Springs
Nach der Landung bringt dich ein Transfer in dein Hotel im Zentrum von Alice Springs.

Hotel Wüstenrose
Das Hotel Wüstenrose liegt ruhig am Rand der Innenstadt, von der Terrasse blickst du auf die
MacDonnell Ranges. Die Zimmer sind schlicht und gemütlich eingerichtet.
https://example.invalid/wuestenrose
Die Fahrstrecke beträgt ca. 15 km.
05.03.2027 Die MacDonnell Ranges
Ein Tagesausflug führt dich zu Schluchten und Wasserlöchern in den Bergen westlich der Stadt.
Du übernachtest in derselben Unterkunft wie am Vortag.
Die eingeschlossene Verpflegung besteht aus einem Frühstück.
06.03.2027 Kings Canyon
Früh am Morgen wanderst du auf dem Rim Walk rund um den Canyon und genießt den Blick.

Canyon Camp Beispiel
Das Camp liegt in der Nähe des Canyons, du übernachtest in festen Zelten mit eigenem Bad und
kannst abends den Sternenhimmel beobachten.
https://example.invalid/canyoncamp
Die eingeschlossene Verpflegung besteht aus einem Frühstück und einem Abendessen.
07.03.2027 Zum Uluru
Am Nachmittag erreichst du den Uluru und erlebst den Sonnenuntergang am großen Felsen.
08.03.2027 Uluru und Kata Tjuta
Du wanderst am Morgen durch das Tal der Winde und besuchst am Nachmittag das Kulturzentrum.
09.03.2027 Flug nach Sydney
Ein Inlandsflug bringt dich an die Küste. Am Abend spazierst du zum Opernhaus.
10.03.2027 Sydney
Eine Hafenrundfahrt zeigt dir die Stadt vom Wasser aus.
11.03.2027 Abschied
Am Abend fliegst du zurück nach Deutschland.
12.03.2027 Willkommen zu Hause!
Am Nachmittag landest du in Deutschland.
LEISTUNGEN BEI CHAMÄLEON
- Linienflug ab/bis Deutschland
- 8 Übernachtungen in Hotels und einem Zeltcamp
- Deutsch sprechende Reiseleitung
HINWEISE ZU DEN LEISTUNGEN UND ZUR REISE
- Falls einzelne der genannten Unterkünfte nicht verfügbar sind, wird eine möglichst
gleichwertige Alternative gebucht.
CHECKLISTE
DOKUMENTE
Reisepass und elektronische Einreisegenehmigung
KLEIDUNG
Fliegennetz für den Kopf, feste Wanderschuhe
\f
ALLGEMEINE REISEINFORMATIONEN
Reisen in kleinen Gruppen
Unsere Reisen finden in kleinen Gruppen statt, damit du nah an Land und Leuten bist und nicht im
Strom großer Reisegruppen untergehst.
REISEINFORMATIONEN ROTE
ERDE
Fahrzeuge
Im roten Zentrum reist du in einem geländegängigen Kleinbus, in Sydney zu Fuß und mit der Fähre.
Nebenkosten vor Ort
Für Getränke und nicht eingeschlossene Mahlzeiten solltest du etwa 40 Euro am Tag einplanen.
INFORMATIONEN LINIENFLUG
Gepäckbestimmungen für Flüge nach Australien mit einer Beispiel-Fluggesellschaft
Es gilt eine Freigepäckmenge von einem Gepäckstück bis 30 kg in der Economy Class.
BERATUNG
REISEMEDIZIN UND TROPENINSTITUTE
Lass dich vor der Reise reisemedizinisch beraten.
NOTIZEN
"""

VISUM_AUSFUELLHILFEN = """\
Fragen & Antworten zum Visum Namibia
(Stand 01.08.2026)
Für die Einreise nach Namibia benötigen deutsche Staatsangehörige ein Visum, das du vorab online
als elektronische Einreisegenehmigung beantragen kannst.
Muss ich das Visum selbst beantragen?
Ja. Das Visum beantragst du persönlich, mit dieser Anleitung ist das in wenigen Minuten erledigt.
Wann beantrage ich die elektronische Einreisegenehmigung?
Wir empfehlen die Beantragung mit Erhalt der Reiseunterlagen, etwa zwei bis drei Wochen vor Abflug.
Welche Adresse gebe ich im Antrag an?
Als Adresse in Namibia gibst du die erste Unterkunft an: Gästehaus Kameldorn, Windhoek.
Was kostet das Visum?
Die Gebühr beträgt für Erwachsene rund 80 Euro und wird online per Kreditkarte bezahlt.
"""

RECHNUNG = """\
Frau
Erika Musterfrau
Beispielweg 1
DE 00000 Musterstadt
Erlebnisberater*in
Paula Platzhalter
+49 30 0000000-000
Vorgang 900001
Reise Wüstenwind
06.10.2026 - 17.10.2026
Buchung vom 02.02.2026
Rechnung 900001
Kunde 800001
Berlin, 10.08.2026
Deine Rechnung
Gast Name Preis pro Person
1 Frau Musterfrau, Erika 3.000,00 €
2 Herr Beispielmann, Theo 3.000,00 €
Flug-Nr. Ab An Flugklasse / Sitz
XX0001 06.10.26 / 20:00 / Frankfurt/M. DE 07.10.26 / 07:00 / Windhoek NA EC
XX0002 13.10.26 / 18:00 / Windhoek NA 13.10.26 / 20:00 / Kapstadt ZA EC
XX0003 16.10.26 / 21:00 / Kapstadt ZA 17.10.26 / 08:00 / Frankfurt/M. DE EC
Hinweise: Alle Zeitangaben sind Ortszeiten.
Erlebnis-Reise Wüstenwind
Reisezeitraum 06.10.26 bis 17.10.26 2 x 3.000,00 € 6.000,00 €
Doppelzimmer
Gesamtbetrag 6.000,00 €
Anzahlung bereits erhalten 1.200,00 €
Restzahlung Vorgangsnr. 900001 bis zum 08.09.2026 4.800,00 €
"""

# Drei Reisende, zwei Nationalitäten, je zwei Zielländer: ergibt entdoppelt
# genau zwei Blöcke (DE, AT). Die Nummer des zweiten Reisenden klebt wie im
# echten PDF am Seitenende an der Vorzeile.
_EINREISE_LAND = """\
Zielland: {land}
Reisedokumente
Die Einreise ist mit folgenden Reisedokumenten möglich:
Reisepass
Das Reisedokument muss {monate} Monate über die Aufenthaltsdauer hinaus gültig sein.
Visabestimmungen
{visum}
Trip-URL: https://travel-details.invalid/de?tid=XXXX-XXXX-XXXX
Gesundheitsbestimmungen
Folgende Impfungen sind bei der Einreise empfohlen:
- Hepatitis A
"""
EINREISEBESTIMMUNGEN = (
    """\
Herrn
Theo Beispielmann
Beispielweg 1
DE 00000 Musterstadt
Vorgang 900001
Reise Wüstenwind
06.10.2026 - 17.10.2026
Buchung vom 02.02.2026
Kunde 800001
Berlin, 03.02.2026
Einreisebestimmungen
1. Frau Musterfrau, Erika
Geburtsdatum: 01.01.1970, Staatsangehörigkeit: DE
"""
    + _EINREISE_LAND.format(land="Namibia", monate="6", visum="Deutsche Staatsangehörige benötigen ein Visum.")
    + _EINREISE_LAND.format(land="Südafrika", monate="1", visum="Deutsche Staatsangehörige benötigen kein Visum.")
    + """\
Die Weiterreise erfolgt innerhalb von 6 Stunden2. Herr Beispielmann, Theo
Geburtsdatum: 02.02.1972, Staatsangehörigkeit: DE
"""
    + _EINREISE_LAND.format(land="Namibia", monate="6", visum="Deutsche Staatsangehörige benötigen ein Visum.")
    + _EINREISE_LAND.format(land="Südafrika", monate="1", visum="Deutsche Staatsangehörige benötigen kein Visum.")
    + """\
3. Frau Probst, Mia
Geburtsdatum: 03.03.1980, Staatsangehörigkeit: AT
"""
    + _EINREISE_LAND.format(land="Namibia", monate="6", visum="Österreichische Staatsangehörige benötigen ein Visum.")
    + _EINREISE_LAND.format(land="Südafrika", monate="1", visum="Österreichische Staatsangehörige benötigen kein Visum.")
)

# Weniger als 200 Zeichen Text: das Tool muss „nicht lesbar“ sagen.
FAST_LEER = "Seite 1\n"

DOKUMENTE = {
    "reiseunterlagen_neu.pdf": REISEUNTERLAGEN_NEU,
    "reisebestaetigung_alt.pdf": REISEBESTAETIGUNG_ALT,
    "visum_ausfuellhilfen.pdf": VISUM_AUSFUELLHILFEN,
    "rechnung.pdf": RECHNUNG,
    "einreisebestimmungen.pdf": EINREISEBESTIMMUNGEN,
    "fast_leer.pdf": FAST_LEER,
}


def schreiben(pfad: str, quelltext: str) -> None:
    """Zeile für Zeile auf A4; "\\f" beginnt eine neue Seite, lange Zeilen
    werden bei 95 Zeichen umbrochen (wie der Fließtext der echten PDFs)."""
    pdf = canvas.Canvas(pfad, pagesize=A4, invariant=1)
    pdf.setFont("Helvetica", 9)
    breite, hoehe = A4
    y = hoehe - 50
    for zeile in quelltext.split("\n"):
        if zeile == SEITE:
            pdf.showPage()
            pdf.setFont("Helvetica", 9)
            y = hoehe - 50
            continue
        for stueck in textwrap.wrap(zeile, 95) or [""]:
            if y < 50:
                pdf.showPage()
                pdf.setFont("Helvetica", 9)
                y = hoehe - 50
            pdf.drawString(50, y, stueck)
            y -= 12
    pdf.save()


def main() -> None:
    os.makedirs(ZIEL, exist_ok=True)
    for name, quelltext in DOKUMENTE.items():
        schreiben(os.path.join(ZIEL, name), quelltext)
        print(f"geschrieben: {name}")


if __name__ == "__main__":
    main()
