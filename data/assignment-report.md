# Assignment — Klassifikation Juli und August 2026

Rohergebnis des ersten bezahlten Laufs. Kein UI, keine Interpretation:
die Liste ist da, um zu entscheiden, ob die Achse trägt.

## 2026-07

- Chats im Monat: 1962
- klassifiziert: 1962 (unmapped: 0)
- Laufzeit: 630s · status: ok

### Qualität

| Klasse | gesamt | allgemein | meinchamaeleon | agentur |
| --- | ---: | ---: | ---: | ---: |
| beantwortet | 1220 | 947 | 198 | 75 |
| ausgewichen | 673 | 436 | 217 | 20 |
| abgebrochen | 57 | 39 | 16 | 2 |
| falsch | 12 | 7 | 2 | 3 |

### Ursachen (roh, ungefiltert)

| ID | Label | gesamt | allgemein | meinchamaeleon | agentur |
| --- | --- | ---: | ---: | ---: | ---: |
| u01 | Bot verweist auf Reisebüro/Erlebnisberatung | 209 | 157 | 49 | 3 |
| u02 | Bot kennt Flugdetails nicht | 99 | 74 | 24 | 1 |
| u04 | Bot hat keinen Zugriff auf persönliche Buchungsdaten | 88 | 23 | 61 | 4 |
| u09 | Bot kennt spezifische Reisedetails nicht | 83 | 71 | 11 | 1 |
| u07 | Bot kann technische Probleme nicht lösen | 63 | 26 | 31 | 6 |
| u05 | Bot kennt Preise/Verfügbarkeiten nicht | 58 | 55 | 2 | 1 |
| u11 | Bot versteht Kontext nicht | 53 | 43 | 7 | 3 |
| u08 | Bot kann keine Änderungen an Buchungen vornehmen | 37 | 13 | 20 | 4 |
| u03 | Bot kennt Visabestimmungen nicht vollständig | 26 | 11 | 14 | 1 |
| u06 | Bot kennt Reiseunterlagen-Details nicht | 18 | 3 | 15 | 0 |
| neu | (neu) | 5 | 5 | 0 | 0 |
| u10 | Bot gibt falsche Kontaktinformationen | 3 | 1 | 1 | 1 |

### Länder (Rangfolge, keine exakten Zahlen)

- Namibia: 193
- Südafrika: 96
- Tansania: 94
- Botswana: 84
- Simbabwe: 41
- Costa Rica: 36
- China: 33
- Vietnam: 33
- Indien: 31
- Marokko: 30
- Japan: 28
- Peru: 22
- Indonesien: 19
- Sri Lanka: 17
- Usbekistan: 15

## 2026-08

- Chats im Monat: 1734
- klassifiziert: 1734 (unmapped: 0)
- Laufzeit: 14906s · status: ok

### Qualität

| Klasse | gesamt | allgemein | meinchamaeleon | agentur |
| --- | ---: | ---: | ---: | ---: |
| beantwortet | 998 | 753 | 204 | 41 |
| ausgewichen | 665 | 387 | 249 | 29 |
| abgebrochen | 60 | 39 | 20 | 1 |
| falsch | 11 | 9 | 2 | 0 |

### Ursachen (roh, ungefiltert)

| ID | Label | gesamt | allgemein | meinchamaeleon | agentur |
| --- | --- | ---: | ---: | ---: | ---: |
| u01 | Bot verweist auf Reisebüro/Erlebnisberatung | 203 | 140 | 55 | 8 |
| u02 | Bot kennt Flugdetails nicht | 94 | 56 | 31 | 7 |
| u07 | Bot kann technische Probleme nicht lösen | 82 | 23 | 56 | 3 |
| u09 | Bot kennt spezifische Reisedetails nicht | 80 | 64 | 12 | 4 |
| u04 | Bot hat keinen Zugriff auf persönliche Buchungsdaten | 79 | 17 | 59 | 3 |
| u05 | Bot kennt Preise/Verfügbarkeiten nicht | 67 | 64 | 2 | 1 |
| u08 | Bot kann keine Änderungen an Buchungen vornehmen | 40 | 11 | 25 | 4 |
| u11 | Bot versteht Kontext nicht | 38 | 31 | 7 | 0 |
| u03 | Bot kennt Visabestimmungen nicht vollständig | 22 | 9 | 13 | 0 |
| neu | (neu) | 19 | 17 | 2 | 0 |
| u06 | Bot kennt Reiseunterlagen-Details nicht | 12 | 3 | 9 | 0 |

### Länder (Rangfolge, keine exakten Zahlen)

- Namibia: 130
- Südafrika: 100
- Tansania: 92
- Botswana: 58
- Vietnam: 41
- China: 37
- Peru: 36
- Costa Rica: 32
- Simbabwe: 31
- Indien: 27
- Japan: 21
- Sri Lanka: 19
- Marokko: 19
- Madagaskar: 13
- Chile: 13

## Kontinuität der Ursachen

| ID | Label | 2026-07 | 2026-08 |
| --- | --- | ---: | ---: |
| neu | (neu) | 5 | 19 |
| u01 | Bot verweist auf Reisebüro/Erlebnisberatung | 209 | 203 |
| u02 | Bot kennt Flugdetails nicht | 99 | 94 |
| u03 | Bot kennt Visabestimmungen nicht vollständig | 26 | 22 |
| u04 | Bot hat keinen Zugriff auf persönliche Buchungsdaten | 88 | 79 |
| u05 | Bot kennt Preise/Verfügbarkeiten nicht | 58 | 67 |
| u06 | Bot kennt Reiseunterlagen-Details nicht | 18 | 12 |
| u07 | Bot kann technische Probleme nicht lösen | 63 | 82 |
| u08 | Bot kann keine Änderungen an Buchungen vornehmen | 37 | 40 |
| u09 | Bot kennt spezifische Reisedetails nicht | 83 | 80 |
| u10 | Bot gibt falsche Kontaktinformationen | 3 | 0 |
| u11 | Bot versteht Kontext nicht | 53 | 38 |

## Varianz der Länderachse (derselbe Monat zweimal gerechnet)

- verglichene Chats: 1962
- Übereinstimmung Qualität: 0.9776
- Übereinstimmung Ursache: 0.9587
- Übereinstimmung Länder: 0.9995

Daraus folgt, wie viele Ränge die Länderliste zeigen darf: im langen
Schwanz kippen die Plätze an einer Handvoll Chats.

## 30 Länderzuordnungen zum Nachprüfen (2026-07)

| chat_db_id | URL-Prior | LLM | übernommen | Fallback |
| --- | --- | --- | --- | --- |
| 00112afc | — | — | ohne Land | nein |
| 0972de7b | — | — | ohne Land | nein |
| 115f8a26 | — | — | ohne Land | nein |
| 1aad9b70 | — | — | ohne Land | nein |
| 23906514 | — | Peru | Peru | nein |
| 2caef22b | — | — | ohne Land | nein |
| 354957c5 | — | Botswana, Namibia | Botswana, Namibia | nein |
| 3dbd9883 | — | — | ohne Land | nein |
| 45ece381 | — | Thailand | Thailand | nein |
| 4e726e67 | — | Tansania | Tansania | nein |
| 556bb817 | — | Costa Rica | Costa Rica | nein |
| 5bf36b34 | — | — | ohne Land | nein |
| 6515624d | — | Kambodscha, Laos | Kambodscha, Laos | nein |
| 6bfc7249 | — | — | ohne Land | nein |
| 75c66f4a | — | — | ohne Land | nein |
| 7e91f800 | — | — | ohne Land | nein |
| 87bfc0de | — | — | ohne Land | nein |
| 8f3a5d44 | — | China | China | nein |
| 96e3759e | — | — | ohne Land | nein |
| 9fd26b92 | — | Frankreich | Frankreich | nein |
| a9a6fc52 | — | — | ohne Land | nein |
| b43df9e9 | — | Madagaskar | Madagaskar | nein |
| bdaf79e5 | — | Namibia | Namibia | nein |
| c7b27a39 | — | Marokko | Marokko | nein |
| cf9bc635 | — | Kenia, Mauritius, Sri Lanka | Kenia, Mauritius, Sri Lanka | nein |
| d6b32291 | — | — | ohne Land | nein |
| de7203c4 | — | — | ohne Land | nein |
| e6261378 | — | Oman | Oman | nein |
| ef17bf49 | — | Botswana, Namibia, Simbabwe, Südafrika | Botswana, Namibia, Simbabwe, Südafrika | nein |
| f6770f22 | — | Südafrika | Südafrika | nein |
