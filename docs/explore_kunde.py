"""Enumerate the TourOne data surface for a customer (values redacted).

Regenerates the field list in ``kundendaten-datenzugriff.md``. Prints only the
*shape* (keys + value types) of ``/get/adresse`` and each linked
``/get/buchung`` — never real values — so it is safe to run and paste.

    python docs/explore_kunde.py            # default: reference customer 999999999
    python docs/explore_kunde.py 999999999

Needs TOURONE_BEARER_TOKEN in the environment / .env (see agent_base.py).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from travel_index import _tourone_get  # noqa: E402


def shape(obj, depth=0, maxdepth=6):
    """Keys + value types, values redacted. Lists of dicts show the field union."""
    if depth > maxdepth:
        return "…"
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1, maxdepth) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return "[] (empty)"
        if all(isinstance(x, dict) for x in obj):
            merged = {}
            for x in obj:
                for k, v in x.items():
                    merged.setdefault(k, shape(v, depth + 1, maxdepth))
            return [f"list[{len(obj)}] of:", merged]
        return [f"list[{len(obj)}] of {type(obj[0]).__name__}"]
    return type(obj).__name__


def main(kid: str) -> None:
    print("=" * 70)
    print(f"GET /get/adresse?kundennummer={kid}")
    print("=" * 70)
    adr = _tourone_get("/get/adresse", {"kundennummer": kid}, timeout=15)
    print("Top-level type:", type(adr).__name__)
    print(json.dumps(shape(adr), indent=2, ensure_ascii=False))

    if not isinstance(adr, dict):
        print("\n(empty list = unknown customer — try 999999999)")
        return

    buchungen = adr.get("buchungen") or []
    print(f"\n{len(buchungen)} buchung(en) referenced.")
    for i, b in enumerate(buchungen[:5]):
        vg = b.get("vorgang")
        if not vg:
            continue
        print("=" * 70)
        print(f"GET /get/buchung?vorgangsNummer={vg}  (buchung {i})")
        print("=" * 70)
        try:
            buch = _tourone_get("/get/buchung", {"vorgangsNummer": vg}, timeout=15)
        except Exception as e:
            print("  ERROR:", e)
            continue
        print(json.dumps(shape(buch), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "999999999")
