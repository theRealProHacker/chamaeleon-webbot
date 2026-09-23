"""seiten_abschnitt — schneidet einen Abschnitt aus dem Seiten-Markdown.

Ohne Netz und ohne Modell. Das Markdown unten ist der Aufbau einer echten
Reiseseite, gezogen 2026-09-21 ueber /Afrika/Tansania/Cheetah: die Abschnitte
stehen als unterstrichene Ueberschriften (setext) da, dazwischen liegen
Zwischenueberschriften der dritten Ebene — darunter ein "### Leistungen" im
Uebersichtsteil, das nicht mit dem echten Leistungsabschnitt verwechselt
werden darf.

    pytest tests/test_seiten_abschnitt.py -v
"""

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import agent_base
from agent_base import SEITEN_ABSCHNITTE, seiten_abschnitt


# Wortgetreuer Aufbau einer Reiseseite, gekuerzt. "### Leistungen" und die
# Benefit-Bar "Unterkuenfte" ohne Unterstreichung sind echte Fallstricke der
# Seite, keine erfundenen Sonderfaelle.
REISESEITE = """# Cheetah - 14 Tage Erlebnisreise | Tansania | Chamäleon

Übersicht
Reiseverlauf
Reisedetails
Leistungen
Termine
Unterkünfte
Verlängerungen

Herzklopf-Momente
-----------------

### Übernachtungen Höhepunkte

Hatari Lodge, Serengeti.

### Leistungen

Flug mit Discover Airlines.

Reiseverlauf
------------

### 9. Tag - Herzliches Willkommen auf Sansibar

Weiterflug nach Sansibar.

Reisedetails
------------

Mindestteilnehmerzahl fuer die Ballonfahrt: 6.

Leistungen
----------

* Linienflug mit Discover Airlines
* Erlebnisreise mit hoechstens 6 Gaesten

---

Tansania | Cheetah

Termine & Preise
----------------

nur verfuegbare Termine anzeigen

![Icon Haus](/data/pic/benefitbar/CHA_Benefit-Bar--Unterkuenfte.svg)

Unterkünfte
zum Verlieben

Unterkünfte
-----------

- Hatari Lodge - Junior Suite

Verlängerungen
--------------

* Sansibar: Baden im Indischen Ozean

Berater Shortcuts
-----------------

[Inlands- & Regionalflüge](/start/report.loadpdf.php?tpl=x)

## Termine – Eckdaten (berechnet, wörtlich übernehmen)

Naechster Termin: 12.01.2027
"""

# Laenderseite: traegt keine der Abschnittsueberschriften.
LAENDERSEITE = """# Namibia Urlaub | Chamäleon

Namibia Urlaub  pure Faszination
--------------------------------

Wueste, Duenen, Weite.

13 Reisen
---------

Sossusvlei 14 Tage, Etosha 19 Tage.
"""


def test_jeder_abschnitt_schneidet_seinen_block():
    ausschnitte = {
        name: seiten_abschnitt(REISESEITE, name) for name in SEITEN_ABSCHNITTE
    }
    for name, text in ausschnitte.items():
        assert text, f"{name} kam leer zurueck"

    assert "Herzklopf-Momente" in ausschnitte["uebersicht"]
    assert "Sansibar" in ausschnitte["reiseverlauf"]
    assert "Ballonfahrt" in ausschnitte["reisedetails"]
    assert "Discover Airlines" in ausschnitte["leistungen"]
    assert "Hatari Lodge - Junior Suite" in ausschnitte["unterkuenfte"]
    assert "Indischen Ozean" in ausschnitte["zusatzprogramme"]


def test_abschnitt_endet_vor_der_naechsten_ueberschrift():
    verlauf = seiten_abschnitt(REISESEITE, "reiseverlauf")
    assert verlauf.startswith("Reiseverlauf")
    assert "Sansibar" in verlauf
    # Der naechste Abschnitt darf nicht mit durchrutschen, auch seine
    # Unterstreichung nicht.
    assert "Reisedetails" not in verlauf
    assert "Ballonfahrt" not in verlauf


def test_uebersicht_ist_der_seitenanfang_bis_zum_reiseverlauf():
    uebersicht = seiten_abschnitt(REISESEITE, "uebersicht")
    assert uebersicht.startswith("# Cheetah - 14 Tage Erlebnisreise")
    assert "Herzklopf-Momente" in uebersicht
    # Genau dafuer ist der Abschnitt da: Dauer und Highlights ohne die
    # 13.000 Zeichen Reiseverlauf.
    assert "9. Tag" not in uebersicht


def test_zwischenueberschrift_dritter_ebene_ist_kein_abschnitt():
    """Im Uebersichtsteil steht "### Leistungen". Wer jede Ebene gelten
    laesst, liefert hier drei Zeilen statt der echten Leistungen."""
    leistungen = seiten_abschnitt(REISESEITE, "leistungen")
    assert leistungen.startswith("Leistungen\n----------")
    assert "Erlebnisreise mit hoechstens 6 Gaesten" in leistungen
    assert "Flug mit Discover Airlines." not in leistungen


def test_benefit_bar_ohne_unterstreichung_ist_keine_ueberschrift():
    """Vor dem echten Abschnitt steht "Unterkuenfte" als Bildunterschrift,
    ohne Strich darunter."""
    unterkuenfte = seiten_abschnitt(REISESEITE, "unterkuenfte")
    assert unterkuenfte.startswith("Unterkünfte\n-----------")
    assert "zum Verlieben" not in unterkuenfte


def test_trennstrich_nach_leerzeile_beendet_den_abschnitt_nicht():
    """markdownify setzt "---" auch als Trenner. Der steht mitten in den
    Leistungen und darf dort nichts abschneiden."""
    leistungen = seiten_abschnitt(REISESEITE, "leistungen")
    assert "Erlebnisreise mit hoechstens 6 Gaesten" in leistungen
    assert "Termine & Preise" not in leistungen


def test_letzter_abschnitt_endet_an_der_naechsten_ueberschrift_jeder_art():
    """Nach den Verlaengerungen kommen "Berater Shortcuts" (unterstrichen)
    und der angehaengte Termine-Block (mit Rauten). Beide sind Grenzen."""
    zusatz = seiten_abschnitt(REISESEITE, "zusatzprogramme")
    assert "Indischen Ozean" in zusatz
    assert "Berater Shortcuts" not in zusatz
    assert "Naechster Termin" not in zusatz


def test_fehlende_ueberschrift_gibt_none():
    ohne_verlaengerungen = REISESEITE.split("Verlängerungen\n--------------")[0]
    assert seiten_abschnitt(ohne_verlaengerungen, "zusatzprogramme") is None


def test_laenderseite_ohne_bekannte_ueberschrift_gibt_ueberall_none():
    for name in SEITEN_ABSCHNITTE:
        assert seiten_abschnitt(LAENDERSEITE, name) is None, name


def test_seite_ganz_ohne_ueberschriften_gibt_none():
    for name in SEITEN_ABSCHNITTE:
        assert seiten_abschnitt("Nur Fliesstext, keine Ueberschrift.", name) is None


def test_unbekannter_name_wirft():
    with pytest.raises(ValueError) as fehler:
        seiten_abschnitt(REISESEITE, "termine")
    # Die Meldung nennt die gueltigen Namen — sie landet beim Modell.
    assert "reiseverlauf" in str(fehler.value)

    with pytest.raises(ValueError):
        seiten_abschnitt(REISESEITE, "")


def test_weicher_trennstrich_in_der_ueberschrift_stoert_nicht():
    """Die Website setzt weiche Trennstriche in lange Woerter — auf der
    Cheetah-Seite steht "Gesundheits\xadbestimmungen"."""
    seite = REISESEITE.replace(
        "Verlängerungen\n--------------", "Verlän\xadgerungen\n--------------"
    )
    zusatz = seiten_abschnitt(seite, "zusatzprogramme")
    assert zusatz is not None
    assert "Indischen Ozean" in zusatz


def test_randleerzeichen_in_der_ueberschrift_stoeren_nicht():
    """markdownify laesst geschuetzte und normale Leerzeichen am Zeilenende
    stehen (gesehen: "Unterkünfte  ")."""
    seite = REISESEITE.replace(
        "Unterkünfte\n-----------", "Unterkünfte \xa0\n-----------"
    )
    unterkuenfte = seiten_abschnitt(seite, "unterkuenfte")
    assert unterkuenfte is not None
    assert "Hatari Lodge - Junior Suite" in unterkuenfte


def test_ueberschriften_finder_nimmt_nur_die_ersten_beiden_ebenen():
    ebenen = [text for _a, _e, text in agent_base._ueberschriften(REISESEITE)]
    assert "Leistungen" in ebenen
    assert "Übernachtungen Höhepunkte" not in ebenen
    assert "9. Tag - Herzliches Willkommen auf Sansibar" not in ebenen
