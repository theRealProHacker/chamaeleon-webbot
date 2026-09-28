from sys import path

import pytest

path.append(".")


@pytest.fixture(autouse=True)
def _caches_leeren():
    """Leert die TourOne-Caches vor jedem Test.

    ``ttl_cache`` hält Werte 10 Minuten (Texte einen Tag) — über Testgrenzen
    hinweg käme sonst die Antwort des vorigen Fakes zurück, oder ein Test ohne
    Fake bekäme einen Treffer, den er live nie hätte. Aktiv über tests/conftest.py.
    """
    import kundendaten

    kundendaten._buchungen_roh.cache_clear()
    kundendaten._buchung_roh.cache_clear()
    try:
        import unterlagen
    except ImportError:
        pass
    else:
        if hasattr(unterlagen.text, "cache_clear"):
            unterlagen.text.cache_clear()
