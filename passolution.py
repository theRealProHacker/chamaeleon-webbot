"""Einreisebestimmungen aus Passolution (Design docs/designs/passolution-einreise.md).

Modulfunktionen plus Caches, keine Klassen. Bisher nur die Zielländer der
geöffneten Reiseseite; der API-Abruf folgt, sobald echte Antworten als Fixture
vorliegen (Schritt 0).
"""

import re

from cachetools.func import ttl_cache

import agent_base

# Reiseseiten tragen ihre Zielländer im HTML (gemessen 2026-10-21):
# data-passolution-covid-info='{"laendercodes": "BW, NA, ZW", "laendercode": "BNZ"}'.
# Nur der Plural: der Singular kann ein Pseudo-Code wie "BNZ" sein.
_LAENDERCODES = re.compile(r'"laendercodes":\s*"([A-Za-z ,]+)"')


@ttl_cache(maxsize=512, ttl=86400)
def laender_der_seite(url_path: str) -> list[str]:
    """ISO2-Zielländer der Reiseseite ``url_path``; ``[]`` ohne Feld (Länderseite).

    WIRFT bei Ausfall (RequestException, ValueError für fremde Hosts) — so
    landen Fehler nie im Cache, und ein kurzer 503 der Website versteckt die
    Länder nicht einen Tag lang. Eigener kleiner Cache statt ``seite_markdown``:
    dort wird nur Markdown gecacht, und das Feld steht nur im rohen HTML.
    """
    treffer = _LAENDERCODES.search(agent_base.get_chamaeleon_website_html(url_path))
    if not treffer:
        return []
    laender = []
    for code in treffer.group(1).split(","):
        code = code.strip().upper()
        if re.fullmatch(r"[A-Z]{2}", code) and code not in laender:
            laender.append(code)
    return laender
