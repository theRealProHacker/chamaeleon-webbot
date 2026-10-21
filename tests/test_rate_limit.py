"""Unit: Passolution-Kontingent je Person (rate_limit.passolution_*). Offline."""

import pytest

import common as _  # noqa: F401  (adds repo root to sys.path)

import rate_limit as rl


@pytest.fixture(autouse=True)
def frischer_zaehler():
    rl._passolution_speicher.reset()
    yield
    rl._passolution_speicher.reset()


def test_30_erlaubt_das_31_nicht_andere_person_schon():
    for _ in range(30):
        assert rl.passolution_erlaubt("1.2.3.4")
        rl.passolution_gezaehlt("1.2.3.4")
    assert not rl.passolution_erlaubt("1.2.3.4")
    assert rl.passolution_erlaubt("5.6.7.8")
    assert rl.passolution_erlaubt("kunde:4711")


def test_erlaubt_zaehlt_nicht():
    for _ in range(40):
        assert rl.passolution_erlaubt("1.2.3.4")


@pytest.mark.parametrize(
    "client_ip, kunden_id, agentur_id, erwartet",
    [
        ("1.2.3.4", "", "", "1.2.3.4"),
        ("1.2.3.4", "4711", "", "kunde:4711"),
        ("1.2.3.4", "", "A1", ""),
        ("", "4711", "", ""),
        ("127.0.0.1", "", "", ""),
        ("::1", "", "", ""),
    ],
)
def test_schluessel(client_ip, kunden_id, agentur_id, erwartet):
    assert rl.passolution_schluessel(client_ip, kunden_id, agentur_id) == erwartet


def test_ausgenommen_immer_erlaubt_und_nie_gezaehlt():
    for ip in ("", "127.0.0.1"):
        schluessel = rl.passolution_schluessel(ip)
        for _ in range(31):
            rl.passolution_gezaehlt(schluessel)
        assert rl.passolution_erlaubt(schluessel)
