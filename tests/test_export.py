"""
Tests for the quantity-outlier guard.

This guard exists because of a real bug found in live data: single irons sitting
inside iron-set listings, producing "savings" of $1,825 that did not exist. For
a price comparison site, a confidently wrong number is worse than no number.

Every case below uses prices actually observed in a production run.

    python tests/test_export.py
    pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover
    class _Mark:
        @staticmethod
        def parametrize(argnames, argvalues):
            names = [n.strip() for n in argnames.split(",")]

            def deco(fn):
                fn._params = (names, argvalues)
                return fn
            return deco

    class _Shim:
        mark = _Mark()
    pytest = _Shim()  # type: ignore[assignment]

from export_prices import flag_quantity_outliers, median  # noqa: E402


def _offers(totals):
    return [{"total": t} for t in totals]


def test_median():
    assert median([1, 2, 3]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert median([5]) == 5


# --------------------------------------------------------------------------
# Must flag: observed mismatches from the live run
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label,totals", [
    ("T150, 6 sellers, a $170 single among sets",
     [170.00, 1093.99, 1399.99, 1439.00, 1499.99, 1995.00]),
    ("P790, 11 sellers, a $189 single among sets",
     [189.99, 569.99, 600.00, 730.99, 895.00, 999.99,
      1091.99, 1099.99, 1199.99, 1308.99, 1399.99]),
    ("T150 Black Vapor, only 2 sellers", [285.00, 1995.99]),
    ("Srixon ZXi5 G2, only 3 sellers", [214.29, 900.00, 1714.24]),
    ("Mizuno JPX925 Fit, only 2 sellers", [137.50, 1056.99]),
    ("Mizuno Pro 243, only 2 sellers", [55.99, 787.99]),
])
def test_flags_obvious_mismatches(label, totals):
    offers = _offers(totals)
    assert flag_quantity_outliers(offers) > 0, f"should have flagged: {label}"
    assert offers[0]["suspect"] is True, "the cheapest is the odd one out"


# --------------------------------------------------------------------------
# Must NOT flag: real bargains are the whole point of the site
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label,totals", [
    ("a real 42% used saving", [379.00, 649.99]),
    ("a normal 31% spread", [379, 489, 499, 515, 529, 549.99]),
    ("used vs new, wide but legitimate", [299.00, 520.00, 649.99]),
    ("two similar set prices", [1093.99, 1199.99]),
    ("balls, tight pricing", [44.99, 47.99, 49.99, 52.99]),
])
def test_leaves_genuine_bargains_alone(label, totals):
    offers = _offers(totals)
    assert flag_quantity_outliers(offers) == 0, (
        f"wrongly flagged a real deal: {label} — an over-eager filter hides "
        f"exactly the savings this site exists to surface"
    )


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label,totals", [
    ("Pro V1x: a bulk case among dozens",
     [27.99, 44.99, 47.99, 49.99, 52.99, 54.99, 54.99, 56.99, 59.99, 591.44]),
    ("a pallet of balls", [39.99, 42.99, 44.99, 47.99, 1299.00]),
])
def test_flags_expensive_outliers_too(label, totals):
    """
    The first version of this guard only looked downward. A $591 case of golf
    balls sitting among $50 dozens then inflated the spread from the top.
    """
    offers = _offers(totals)
    assert flag_quantity_outliers(offers) > 0, f"should have flagged: {label}"
    assert offers[-1]["suspect"] is True, "the dearest is the odd one out"


def test_new_versus_used_is_not_flagged_as_an_outlier():
    """A new club at 3x a worn used one is a real comparison, not a mismatch."""
    offers = _offers([299.00, 420.00, 520.00, 649.99])
    assert flag_quantity_outliers(offers) == 0


def test_single_offer_is_never_flagged():
    offers = _offers([499.00])
    assert flag_quantity_outliers(offers) == 0
    assert offers[0]["suspect"] is False


def test_empty_list():
    assert flag_quantity_outliers([]) == 0


def test_never_flags_every_offer():
    """If everything looks suspect, the threshold is wrong, not the data."""
    offers = _offers([10.00, 10.50, 11.00])
    flag_quantity_outliers(offers)
    assert not all(o["suspect"] for o in offers)


def test_every_offer_gets_an_explicit_suspect_key():
    """The front end must never have to guess at a missing field."""
    offers = _offers([100.00, 120.00, 900.00])
    flag_quantity_outliers(offers)
    assert all("suspect" in o for o in offers)
    assert all(isinstance(o["suspect"], bool) for o in offers)


def test_flagging_shrinks_the_headline_spread():
    """The point of the exercise: the advertised saving becomes honest."""
    totals = [170.00, 1093.99, 1399.99, 1439.00, 1499.99, 1995.00]
    offers = _offers(totals)
    flag_quantity_outliers(offers)
    comparable = [o["total"] for o in offers if not o["suspect"]]
    before = max(totals) - min(totals)
    after = max(comparable) - min(comparable)
    assert before > 1800 and after < 1000, f"{before:.0f} -> {after:.0f}"


def _run_standalone() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    failures: list[str] = []
    for name, fn in tests:
        params = getattr(fn, "_params", None)
        cases = ([dict(zip(params[0], v if isinstance(v, tuple) else (v,)))
                  for v in params[1]] if params else [{}])
        for kwargs in cases:
            label = f"{name}{tuple(kwargs.values())[:1] if kwargs else ''}"
            try:
                fn(**kwargs)
                passed += 1
            except AssertionError as exc:
                failed += 1
                failures.append(f"FAIL {label}\n      {exc}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                failures.append(f"ERROR {label}\n      {type(exc).__name__}: {exc}")
    for line in failures:
        print(line)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
