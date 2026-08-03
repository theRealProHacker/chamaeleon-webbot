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
    """Mit DDMMYY wäre diese Auswahl für JEDE Agentur immer leer."""
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
