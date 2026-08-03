"""agenturdaten — die Wächter G2/G3 und die gemessenen Sammelregeln.

Alle Fixtures sind ERFUNDEN. Ein echter ss.php- oder TourOne-Dump darf hier nie
landen: eine Agentursession trägt Passwort-Hash, Salt, IBAN, USt-IdNr und ein
Klartextpasswort.
"""

import common as _  # noqa: F401

import agenturdaten


def _leistung(anf="P", bez="Rundreise Namibia Etosha", von="2027-05-01 00:00:00",
              bis="2027-05-14 00:00:00", status="OK", code="NAETO"):
    return {
        "Anforderung": anf,
        "Leistung": code,
        "LeistungsBezeichnung": bez,
        # DDMMYY — genau die Falle. Wird nie gelesen, steht hier, damit ein
        # Regress darüber stolpert.
        "VonDatum": "010527",
        "BisDatum": "140527",
        "leistungVonDat": von,
        "leistungBisDat": bis,
        "LeistungsStatus": status,
    }


def _row(agt="12345", vorgang="4711", leistungen=None, kunde="Familie Muster",
         teilnehmer=("Anna Muster", "Ben Muster"), provision="120.00"):
    return {
        "vorgangsNummer": vorgang,
        "buchungLeistungen": {
            "ACTION": {
                "AgenturNummer": agt,
                "VorgangsNummer": vorgang,
                "GesamtPreis": "4200.00",
                "AgenturCommission": provision,
            },
            "KUNDE": {
                "VornameTitel": kunde,
                "StrasseHausNummer": "Musterweg 1",
                "Postleitzahl": "12345",
                "Ort": "Musterstadt",
                "TelefonNummer": "030 000000",
            },
            "TEILNEHMERS": [{"Name": n} for n in teilnehmer],
            "LEISTUNGEN": leistungen if leistungen is not None else [_leistung()],
        },
    }


def _page(rows):
    page = {str(i): r for i, r in enumerate(rows)}
    page["anzahl"] = len(rows)
    return page


# --- G2 -----------------------------------------------------------------------


def test_g2_leere_agenturnummer_geht_nie_raus(monkeypatch):
    """Ohne agenturNummer liefert die API 223.588 Buchungen bei HTTP 200."""
    gerufen = []
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", lambda *a, **k: gerufen.append(a) or {}
    )

    for kaputt in ("", "   ", None, 12345):
        try:
            agenturdaten._agentur_get(
                "/get/buchungLeistungenListe", {"agenturNummer": kaputt}
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"{kaputt!r} hätte abgelehnt werden müssen")

    # Der entscheidende Teil: der Transport wurde NIE erreicht.
    assert gerufen == []


def test_g2_fehlende_agenturnummer_erzeugt_keinen_request(monkeypatch):
    gerufen = []
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", lambda *a, **k: gerufen.append(a) or {}
    )
    try:
        agenturdaten._agentur_get("/get/buchungLeistungenListe", {})
    except ValueError:
        pass
    assert gerufen == []


def test_fetch_ohne_agentur_id_meldet_fehler_nicht_leer(monkeypatch):
    """Ein G2-Abbruch darf nie als „keine Buchungen" beim Nutzer ankommen."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([_row()]))
    text = agenturdaten.fetch_buchungen_text("")
    assert text == agenturdaten.FEHLER_TEXT
    assert "keine Buchungen" not in text


# --- G3 -----------------------------------------------------------------------


def test_g3_akzeptiert_int_typisierte_agenturnummer(monkeypatch):
    """Der JSON-Typ ist nirgends zugesichert; dieselbe Antwort mischt Typen.

    Ohne Normalisierung verwirft ein naives != JEDE Zeile JEDER Agentur, und der
    Bot meldet „keine Buchungen" — genau das Versagen, das §7 verhindern soll.
    Eine erfundene Fixture fängt das nur, wenn sie den Typ absichtlich dreht.
    """
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", lambda *a, **k: _page([_row(agt=12345)])
    )
    text = agenturdaten.fetch_buchungen_text("12345")
    assert "4711" in text
    assert text != agenturdaten.FEHLER_TEXT


def test_g3_verwirft_fremde_agentur(monkeypatch):
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", lambda *a, **k: _page([_row(agt="99999")])
    )
    text = agenturdaten.fetch_buchungen_text("12345")
    # Zeilen rein, null Zeilen raus ist eine Bug-Signatur, kein Leerzustand.
    assert text == agenturdaten.G3_FEHLER_TEXT
    assert "keine Buchungen" not in text


def test_g3_vergleicht_action_nicht_meldeagentur(monkeypatch):
    """MeldeAgenturNummer ist ein Dritter — nie die Vergleichsquelle."""
    row = _row(agt="12345")
    row["buchungLeistungen"]["LEISTUNGEN"][0]["MeldeAgenturNummer"] = "77777"
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([row]))
    assert "4711" in agenturdaten.fetch_buchungen_text("12345")


# --- gemessene Sammelregeln (§3.3) --------------------------------------------


def test_datum_kommt_aus_leistungvondat_nicht_ddmmyy(monkeypatch):
    """VonDatum ist DDMMYY; landet es im Text, ist die Sortierung tot."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([_row()]))
    text = agenturdaten.fetch_buchungen_text("12345")
    assert "01.05.2027" in text and "14.05.2027" in text
    assert "010527" not in text and "140527" not in text


def test_kommende_auswahl_findet_zukuenftige_reise(monkeypatch):
    """Mit DDMMYY wäre diese Auswahl für JEDE Agentur immer leer.

    Uhr festgenagelt: die Fixture-Reise liegt im Mai 2027, und ohne das hier
    schlüge der Test ab dem 15.05.2027 fehl — aus einem Grund, der nichts mit
    dem zu tun hat, was er prüft.
    """
    monkeypatch.setattr(agenturdaten, "heute_berlin", lambda: "2026-08-03")
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([_row()]))
    text = agenturdaten.fetch_buchungen_text("12345", auswahl="kommende")
    assert "4711" in text
    assert "finde ich keine Buchung" not in text


def test_datumsspanne_stammt_vom_p_eintrag(monkeypatch):
    """Eine Versicherung mit reisefremden Daten darf das Fenster nicht weiten."""
    leistungen = [
        _leistung(),
        _leistung(
            anf="V",
            bez="Chamäleon-Premiumschutz",
            von="2026-01-02 00:00:00",
            bis="2028-12-31 00:00:00",
            code="VERS",
        ),
    ]
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        lambda *a, **k: _page([_row(leistungen=leistungen)]),
    )
    text = agenturdaten.fetch_buchungen_text("12345")
    assert "01.05.2027" in text and "14.05.2027" in text
    assert "02.01.2026" not in text and "31.12.2028" not in text


def test_titel_faellt_bei_generischer_bezeichnung_auf_den_index(monkeypatch):
    """„Erlebnis-Reise" ist eine Produktlinie, kein Reisetitel (86/436)."""
    monkeypatch.setattr(agenturdaten, "get_titel_for_code", lambda c: {
        "NASOS": "Namibia Sossusvlei Rundreise"
    }.get(c, ""))
    leistungen = [_leistung(bez="Erlebnis-Reise", code="NASOS")]
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        lambda *a, **k: _page([_row(leistungen=leistungen)]),
    )
    text = agenturdaten.fetch_buchungen_text("12345")
    assert "Namibia Sossusvlei Rundreise" in text


def test_titel_bleibt_stehen_wenn_der_index_nichts_weiss(monkeypatch):
    monkeypatch.setattr(agenturdaten, "get_titel_for_code", lambda c: "")
    leistungen = [_leistung(bez="Erlebnis-Reise", code="UNBEKANNT")]
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        lambda *a, **k: _page([_row(leistungen=leistungen)]),
    )
    assert "Erlebnis-Reise" in agenturdaten.fetch_buchungen_text("12345")


def test_status_folgt_dem_p_eintrag_nicht_allen(monkeypatch):
    """Reise storniert, Restposten offen → storniert (6/436 sonst falsch)."""
    leistungen = [
        _leistung(status="XX"),
        _leistung(anf="X", bez="Storno/Reiseabsage", status="OK", code="STORNO"),
    ]
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        lambda *a, **k: _page([_row(leistungen=leistungen)]),
    )
    assert "storniert" in agenturdaten.fetch_buchungen_text("12345")


def test_ohne_p_eintrag_faellt_status_auf_alle_zurueck(monkeypatch):
    leistungen = [_leistung(anf="L", status="XX"), _leistung(anf="T", status="XX")]
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        lambda *a, **k: _page([_row(leistungen=leistungen)]),
    )
    assert "storniert" in agenturdaten.fetch_buchungen_text("12345")


# --- Whitelist ----------------------------------------------------------------


def test_endkunden_pii_erreicht_den_text_nie(monkeypatch):
    """Straße, PLZ, Ort und Telefon kommen ungefragt mit — und bleiben draußen."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([_row()]))
    for details in (False, True):
        text = agenturdaten.fetch_buchungen_text("12345", details=details)
        assert "Musterweg" not in text
        assert "12345 " not in text.replace("Buchungsnummer", "")
        assert "Musterstadt" not in text
        assert "030 000000" not in text


def test_details_zeigt_mitreisende_und_provision(monkeypatch):
    """D9: Mitreisende sind für Agenturen bewusst DRIN (anders als im Kundenpfad)."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([_row()]))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anna Muster" in text and "Ben Muster" in text
    assert "Provision" in text


def test_details_ueber_dem_cap_verweigert_statt_zu_kuerzen(monkeypatch):
    rows = [_row(vorgang=str(1000 + i)) for i in range(agenturdaten.DETAIL_ROW_CAP + 5)]
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page(rows))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert str(len(rows)) in text
    assert "anzahl" in text  # sagt, wie man eingrenzt
    assert "Anna Muster" not in text  # nichts still gekürzt, gar nichts gerendert


# --- Fehlerpfade --------------------------------------------------------------


def test_http_fehler_wird_nie_zu_keine_buchungen(monkeypatch):
    """~1–2% der eingeloggten Agenturen scheitern hier hart und dauerhaft."""

    def boom(*a, **k):
        raise RuntimeError("500")

    monkeypatch.setattr(agenturdaten, "_tourone_get", boom)
    text = agenturdaten.fetch_buchungen_text("12345")
    assert text == agenturdaten.FEHLER_TEXT
    assert "keine Buchungen" not in text


def test_leere_antwort_ist_eine_leere_agentur(monkeypatch):
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: {"anzahl": 0})
    assert agenturdaten.fetch_buchungen_text("12345") == agenturdaten.KEINE_BUCHUNGEN_TEXT


def test_tool_closure_nimmt_keine_agenturnummer():
    """Das Modell darf wählen WELCHE Buchungen, nie WESSEN."""
    t = agenturdaten.make_buchungen_agentur_tool("12345")
    assert "agentur_id" not in t.args
    assert "agenturNummer" not in t.args
    assert set(t.args) <= {"auswahl", "anzahl", "details"}


# --- Hop 2: Zahlstand, Flüge, und G3 ein zweites Mal ---------------------------
#
# ``/get/buchung`` kennt keinen Agenturfilter — es ist über die bloße
# vorgangsNummer adressiert. Dass sie aus einer G3-geprüften Hop-1-Zeile stammt,
# ist die einzige Bindung an die Agentur, deshalb wird sie an der Rückgabe
# unabhängig nachgeprüft.


def _detail(agt="12345", vorgang="4711", status="OK", fluege=None, **extra):
    """Erfundene Hop-2-Antwort. Trägt bewusst auch Felder, die DRAUSSEN bleiben."""
    d = {
        "agtNr": agt,
        # Der Mandant (Chamäleon selbst). Steht hier, damit ein Regress, der ihn
        # statt agtNr vergleicht, an test_g3_hop2_akzeptiert_mandant_nicht scheitert.
        "mandantAgtNr": "99",
        "vorgang": vorgang,
        "status": status,
        "beschreibungen": [{"titel": "Namibia — Zauber der Weite"}],
        "preis": 4200.0,
        "anzahlungBetrag": 840.0,
        "anzahlungDat": "2027-01-15 00:00:00",
        "restBetrag": 3360.0,
        "schlussZahlungDat": "2027-03-20 00:00:00",
        "eingangBetrag": 840.0,
        "personen": 2,
        "persAdult": 2,
        "flugdaten": [
            {
                "rang": 1,
                "flugnr": "LH576",
                "airline": "LH",
                "vonCo3Code": "FRA",
                "nachCo3Code": "WDH",
                "abflug": "2027-05-01 20:15:00",
                "ankunft": "2027-05-02 06:30:00",
            }
        ]
        if fluege is None
        else fluege,
        # Bewusst DRAUSSEN (Plan §6) — dürfen im gerenderten Text nie auftauchen.
        "pnrFileKey": "PNRGEHEIM",
        "bookNotiz": "interne Notiz Kunde nervt",
        "chroniken": [{"text": "interner Chronikeintrag"}],
        "adrNotfallKontakt": "Notfall Oma 0170",
        "adrEmail": "privat@example.invalid",
        "teilnehmerliste": [
            {"name": "Anna Muster", "gebDat": "1980-03-04", "email": "anna@example.invalid"}
        ],
    }
    d.update(extra)
    return d


def _api(rows, detail=None, gerufen=None):
    """Fake für BEIDE Hops; unterschieden wird am Pfad, wie in der echten API."""

    def call(path, params=None, **k):
        if gerufen is not None:
            gerufen.append((path, dict(params or {})))
        if path == "/get/buchung":
            d = detail(params["vorgangsNummer"]) if callable(detail) else detail
            if isinstance(d, Exception):
                raise d
            return d
        return _page(rows)

    return call


def test_grobe_liste_loest_keinen_hop2_aus(monkeypatch):
    """details=false ist der billige Pfad und muss es bleiben."""
    gerufen = []
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", _api([_row()], _detail(), gerufen)
    )
    agenturdaten.fetch_buchungen_text("12345", details=False)
    assert [p for p, _ in gerufen] == ["/get/buchungLeistungenListe"]


def test_details_holt_genau_einen_hop2_je_buchung(monkeypatch):
    gerufen = []
    rows = [_row(vorgang=str(4711 + i)) for i in range(3)]
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        _api(rows, lambda v: _detail(vorgang=v), gerufen),
    )
    agenturdaten.fetch_buchungen_text("12345", details=True)
    hop2 = [params["vorgangsNummer"] for p, params in gerufen if p == "/get/buchung"]
    assert sorted(hop2) == ["4711", "4712", "4713"]


def test_details_zeigt_den_zahlstand(monkeypatch):
    """Der Grund, dass es Hop 2 gibt: „ist die Buchung bezahlt?"."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail()))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anzahlung" in text
    assert "Offener Betrag" in text
    assert "Bereits eingegangen" in text
    assert "3.360,00" in text


def test_gesamtpreis_steht_genau_einmal(monkeypatch):
    """Hop 1 (GesamtPreis) und Hop 2 (preis) sind dieselbe Zahl.

    Zwei „Gesamtpreis"-Zeilen sind für das Modell zwei Tatsachen.
    """
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail()))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert text.count("- Gesamtpreis:") == 1


def test_details_zeigt_fluege(monkeypatch):
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail()))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "LH576" in text and "FRA" in text and "WDH" in text


def test_ohne_flugdaten_sagt_noch_nicht_eingebucht(monkeypatch):
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", _api([_row()], _detail(fluege=[]))
    )
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "noch nicht eingebucht" in text


def test_hop2_titel_schlaegt_den_hop1_titel(monkeypatch):
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail()))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Zauber der Weite" in text


# --- G3 auf Hop 2 -------------------------------------------------------------


def test_g3_hop2_verwirft_fremde_buchung(monkeypatch):
    """Eine Detailantwort mit fremder agtNr darf keine Zahlen beisteuern."""
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", _api([_row()], _detail(agt="99999"))
    )
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anzahlung" not in text
    assert "LH576" not in text
    assert "Zauber der Weite" not in text
    # Der Hop-1-Teil bleibt stehen, und die Lücke wird benannt.
    assert "Anna Muster" in text
    assert "nicht leer" in text


def test_g3_hop2_akzeptiert_mandant_nicht(monkeypatch):
    """mandantAgtNr ist Chamäleon selbst — als Ausweis wäre sie wertlos."""
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        _api([_row()], _detail(agt="99999", mandantAgtNr="12345")),
    )
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anzahlung" not in text


def test_g3_hop2_verwirft_fehlende_agtnr(monkeypatch):
    """Fail closed: ohne Nachweis kein Detail (z.B. nach einer Feldumbenennung)."""
    d = _detail()
    del d["agtNr"]
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], d))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anzahlung" not in text
    assert "nicht leer" in text


def test_g3_hop2_normalisiert_typen(monkeypatch):
    """int-typisierte agtNr ist gültig — sonst verschwände jeder Zahlstand."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail(agt=12345)))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anzahlung" in text
    assert "nicht leer" not in text


# --- Teilausfall --------------------------------------------------------------


def test_teilausfall_nennt_die_luecke_und_behaelt_den_rest(monkeypatch):
    """Eine kaputte Buchung darf weder die Antwort kosten noch stumm fehlen."""
    rows = [_row(vorgang="4711"), _row(vorgang="4712", kunde="Familie Zweite")]

    def detail(vorgang):
        if vorgang == "4712":
            raise RuntimeError("500")
        return _detail(vorgang=vorgang)

    monkeypatch.setattr(agenturdaten, "_tourone_get", _api(rows, detail))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "Anzahlung" in text  # die heile Buchung ist vollständig
    assert "Familie Zweite" in text  # die kaputte ist trotzdem da
    assert "nicht leer" in text  # und die Lücke ist benannt
    assert agenturdaten.KEINE_BUCHUNGEN_TEXT not in text


def test_ohne_hop2_keine_behauptung_ueber_fluege(monkeypatch):
    """„noch nicht eingebucht" wäre eine Aussage über ungesehene Daten."""

    def boom(vorgang):
        raise RuntimeError("timeout")

    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], boom))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "noch nicht eingebucht" not in text


def test_vollstaendige_details_ohne_hinweis(monkeypatch):
    """Der Teilausfall-Hinweis darf nicht bei jeder Antwort mitlaufen."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail()))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "nicht leer" not in text


# --- Storno und Status --------------------------------------------------------


def test_hop2_status_xx_storniert_auch_gegen_hop1(monkeypatch):
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", _api([_row()], _detail(status="XX"))
    )
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "- Status: storniert" in text
    # Zahlstand und Flüge einer stornierten Reise sind irreführend.
    assert "Offener Betrag" not in text
    assert "LH576" not in text


def test_fehlender_hop2_status_storniert_nicht(monkeypatch):
    """Ein fehlendes Feld darf keine gebuchte Reise stornieren."""
    d = _detail()
    del d["status"]
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], d))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    assert "- Status: gebucht" in text


# --- Whitelist auf Hop 2 ------------------------------------------------------


def test_hop2_pii_und_interna_erreichen_den_text_nie(monkeypatch):
    """Hop 2 trägt deutlich mehr als Hop 1 — die Allowlist muss auch hier greifen."""
    monkeypatch.setattr(agenturdaten, "_tourone_get", _api([_row()], _detail()))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)
    for verboten in (
        "PNRGEHEIM",
        "nervt",
        "Chronikeintrag",
        "Notfall Oma",
        "privat@example.invalid",
        "1980-03-04",
        "anna@example.invalid",
    ):
        assert verboten not in text, verboten


def test_cap_verweigert_bevor_hop2_feuert(monkeypatch):
    """Die Verweigerung muss billiger sein als die Arbeit, die sie ablehnt."""
    gerufen = []
    rows = [_row(vorgang=str(1000 + i)) for i in range(agenturdaten.DETAIL_ROW_CAP + 5)]
    monkeypatch.setattr(
        agenturdaten, "_tourone_get", _api(rows, _detail(), gerufen)
    )
    agenturdaten.fetch_buchungen_text("12345", details=True)
    assert [p for p, _ in gerufen] == ["/get/buchungLeistungenListe"]


# --- G3: die Prüfung ist PRO ZEILE, nicht pro Antwort -------------------------


def test_g3_verwirft_die_fremde_zeile_zwischen_eigenen(monkeypatch):
    """Gemischte Seite: eine fremde Zeile darf nicht mitfahren.

    Bis hierher war jede Testseite homogen — entweder nur eigene Zeilen oder eine
    einzelne fremde. Beides bleibt grün, wenn man die Prüfung von „pro Zeile" auf
    „pro Antwort" abschwächt (verwerfe nur, wenn KEINE Zeile passt). Genau dann
    reicht eine eigene Buchung, um beliebig viele fremde durchzuwinken.
    """
    monkeypatch.setattr(
        agenturdaten,
        "_tourone_get",
        lambda *a, **k: _page(
            [
                _row(agt="12345", vorgang="4711", kunde="Familie Muster"),
                _row(agt="99999", vorgang="9999", kunde="Fremde Agentur"),
            ]
        ),
    )
    text = agenturdaten.fetch_buchungen_text("12345")

    assert "4711" in text
    assert "9999" not in text
    assert "Fremde Agentur" not in text


def test_g3_verwirft_zeile_ohne_agenturnummer(monkeypatch):
    """Fail closed: ohne Nachweis keine Zeile — etwa nach einer Feldumbenennung.

    Hop 2 hat diesen Test längst (test_g3_hop2_verwirft_fehlende_agtnr); Hop 1
    hatte ihn nicht, obwohl dort dieselbe Umbenennung dieselbe Wirkung hätte.
    """
    row = _row()
    del row["buchungLeistungen"]["ACTION"]["AgenturNummer"]
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page([row]))

    assert agenturdaten.fetch_buchungen_text("12345") == agenturdaten.G3_FEHLER_TEXT


# --- Hop 2 landet bei SEINER Buchung ------------------------------------------


def test_hop2_detail_landet_bei_seiner_eigenen_buchung(monkeypatch):
    """Die zip()-Paarung hängt an der Reihenfolge von pool.map.

    Vertauschte Paarung wirft nichts und sieht plausibel aus: jede Buchung zeigt
    dann den Zahlstand ihrer Nachbarin. „Ist die Buchung bezahlt" ist die
    Kernfrage am Counter — eine Verwechslung ist eine falsche Geldaussage.
    """
    rows = [_row(vorgang="4711"), _row(vorgang="4712")]

    def detail(vorgang):
        return _detail(
            vorgang=vorgang,
            restBetrag=1111.0 if vorgang == "4711" else 2222.0,
        )

    monkeypatch.setattr(agenturdaten, "_tourone_get", _api(rows, detail))
    text = agenturdaten.fetch_buchungen_text("12345", details=True)

    block_4711, block_4712 = text.split("Buchung 4712")
    assert "1.111,00" in block_4711 and "2.222,00" not in block_4711
    assert "2.222,00" in block_4712 and "1.111,00" not in block_4712


# --- Mehrere P-Einträge = mehrere Reiseabschnitte ------------------------------


def test_zeitraum_spannt_ueber_alle_p_eintraege(monkeypatch):
    """Gemessen 2026-08-03: 25% der Buchungen tragen mehr als einen P-Eintrag,
    und bei 69% davon sind die Zeiträume verschieden.

    Nur den ersten zu nehmen rendert einen Abschnitt als die ganze Reise — der
    Fehler wirft nichts und sieht bloß nach einer kurzen Reise aus.
    """
    rows = [
        _row(
            leistungen=[
                _leistung(von="2027-05-01 00:00:00", bis="2027-05-14 00:00:00"),
                _leistung(von="2027-05-14 00:00:00", bis="2027-05-28 00:00:00",
                          bez="Verlängerung Sossusvlei"),
                # Zusatzleistung mit reisefremdem Datum: darf die Spanne NICHT weiten.
                _leistung(anf="V", bez="Versicherung", von="2026-11-02 00:00:00",
                          bis="2028-01-31 00:00:00"),
            ]
        )
    ]
    monkeypatch.setattr(agenturdaten, "_tourone_get", lambda *a, **k: _page(rows))
    text = agenturdaten.fetch_buchungen_text("12345")

    assert "01.05.2027 – 28.05.2027" in text
    assert "14.05.2027)" not in text   # nicht die Spanne des ersten Abschnitts
    assert "31.01.2028" not in text    # und nicht die der Versicherung
