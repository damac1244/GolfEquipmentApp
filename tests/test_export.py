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

from export_prices import (  # noqa: E402
    club_key, club_name, flag_foreign_currencies, flag_quantity_outliers,
    group_into_clubs, median, variant_label,
)


def _offers(totals):
    return [{"total": t} for t in totals]


def _priced(pairs):
    """[(total, currency), ...] -> offer dicts, already through the first pass."""
    offers = [{"total": t, "currency": c, "suspect": False,
               "suspect_reason": None} for t, c in pairs]
    return offers


# --------------------------------------------------------------------------
# Currency mismatches. Comparing £899 against $1,099 is not a rounding error,
# it is arithmetic on different units — and it always flatters the foreign row.
# --------------------------------------------------------------------------

def test_single_currency_is_left_alone():
    offers = _priced([(899.00, "USD"), (999.00, "USD"), (1099.00, "USD")])
    assert flag_foreign_currencies(offers) == 0
    assert not any(o["suspect"] for o in offers)


def test_minority_currency_is_flagged():
    offers = _priced([(899.00, "GBP"), (1049.00, "USD"), (1099.00, "USD")])
    assert flag_foreign_currencies(offers) == 1
    assert offers[0]["suspect"] is True
    assert offers[0]["suspect_reason"] == "currency"
    assert not offers[1]["suspect"] and not offers[2]["suspect"]


def test_the_cheapest_row_does_not_win_by_being_foreign():
    """The bug this guard exists to stop: a pound price heading the table."""
    offers = _priced([(899.00, "GBP"), (1049.00, "USD"), (1099.00, "USD")])
    flag_foreign_currencies(offers)
    comparable = [o for o in offers if not o["suspect"]]
    assert min(o["total"] for o in comparable) == 1049.00


def test_missing_currency_is_not_treated_as_foreign():
    """Older rows predate the currency column; absence is not disagreement."""
    offers = [{"total": 899.00, "suspect": False, "suspect_reason": None},
              {"total": 999.00, "currency": "USD", "suspect": False,
               "suspect_reason": None}]
    assert flag_foreign_currencies(offers) == 0


def test_a_row_already_flagged_is_not_counted_twice():
    offers = _priced([(55.00, "GBP"), (999.00, "USD"), (1099.00, "USD")])
    offers[0]["suspect"] = True          # the quantity pass got there first
    offers[0]["suspect_reason"] = "quantity"
    assert flag_foreign_currencies(offers) == 0
    assert offers[0]["suspect_reason"] == "currency"


def test_an_even_split_keeps_the_cheaper_currency():
    """
    With two of each there is no majority. Break towards the currency holding
    the cheapest offer, so the headline price stays a price someone can pay.
    """
    offers = _priced([(1049.00, "USD"), (1099.00, "USD"),
                      (1199.00, "CAD"), (1249.00, "CAD")])
    flag_foreign_currencies(offers)
    comparable = [o for o in offers if not o["suspect"]]
    assert {o["currency"] for o in comparable} == {"USD"}


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


# --------------------------------------------------------------------------
# Grouping fitting variants into one club.
#
# Sellers describe the same club at different levels of detail. The matcher is
# right to keep a 9° stiff apart from a 12° ladies -- one price for both would
# be a lie -- but that left one club spread over ten rows, its sellers split
# between them, and not one row worth reading. Identity stays strict; the
# presentation groups.
# --------------------------------------------------------------------------

def _variant(model="elyte", type_="driver", brand="Callaway", label="",
             best=400.0, spread=0.0, retailers=1, year=None, offers=None,
             new_from=None, used_from=None, vid=1):
    return {
        "id": vid, "brand": brand, "type": type_, "model": model, "year": year,
        "label": label, "best": best, "spread": spread,
        "spread_pct": round(spread / (best + spread) * 100, 1) if best + spread else 0.0,
        "retailers": retailers, "image": None, "currency": "USD",
        "new_from": new_from if new_from is not None else best,
        "used_from": used_from, "suspect_count": 0, "is_low": False,
        "lowest_90d": None,
        "offers": offers if offers is not None else [
            # Distinct shops per variant unless a test passes its own offers:
            # overlapping names would quietly test the wrong thing.
            {"retailer": "Shop %d-%d" % (vid, i), "total": best + i,
             "condition": "new", "suspect": False, "currency": "USD"}
            for i in range(retailers)
        ],
    }


def test_fitting_specs_do_not_start_a_new_club():
    a = _variant(label="9°, Stiff, RH")
    b = _variant(label="12°, Ladies, RH")
    assert club_key(a) == club_key(b)


def test_a_different_model_is_a_different_club():
    assert club_key(_variant(model="elyte")) != club_key(_variant(model="paradym"))


def test_iron_generations_stay_separate_clubs():
    """2019 and 2025 P790s are different clubs and different price tiers."""
    a = _variant(model="p790", type_="iron_set", year=2019)
    b = _variant(model="p790", type_="iron_set", year=2025)
    assert club_key(a) != club_key(b)
    assert "2019" in club_name(a) and "2025" in club_name(b)


def test_a_year_in_a_driver_title_does_not_split_the_club():
    """Only irons carry the generation. Elsewhere a year is just wording."""
    a = _variant(model="qi35", type_="driver", year=2025)
    b = _variant(model="qi35", type_="driver", year=None)
    assert club_key(a) == club_key(b)
    assert "2025" not in club_name(a)


def test_club_name_reads_like_a_club():
    assert club_name(_variant(brand="Callaway", model="elyte", type_="driver")) \
        == "Callaway Elyte Driver"


# --------------------------------------------------------------------------
# The number the old layout could never show
# --------------------------------------------------------------------------

def test_sellers_are_pooled_across_variants():
    """
    The whole point. Four sellers on one loft and three on another is seven
    sellers for that club, not two separate rows of "a few".
    """
    a = _variant(label="9°, Stiff, RH", retailers=4, best=400.0, vid=1)
    b = _variant(label="10.5°, Regular, RH", retailers=3, best=420.0, vid=2)
    club = group_into_clubs([a, b])[0]
    assert club["variant_count"] == 2
    assert club["sellers"] == 7
    assert club["best"] == 400.0


def test_the_same_seller_on_two_variants_is_counted_once():
    shared = [{"retailer": "Same Shop", "total": 400.0, "condition": "new",
               "suspect": False, "currency": "USD"}]
    a = _variant(label="9°", offers=shared, best=400.0, vid=1)
    b = _variant(label="10.5°", offers=list(shared), best=400.0, vid=2)
    assert group_into_clubs([a, b])[0]["sellers"] == 1


def test_the_advertised_saving_is_never_measured_across_variants():
    """
    The fake-saving bug in a new costume. A cheap 12° ladies next to a dear 9°
    tour head is not a deal, it is two different clubs. The number we show has
    to be a gap someone can actually act on: the biggest spread WITHIN one
    fitting.
    """
    cheap = _variant(label="12°, Ladies, RH", best=200.0, spread=10.0, vid=1)
    dear = _variant(label="9°, Tour, RH", best=900.0, spread=50.0, vid=2)
    club = group_into_clubs([cheap, dear])[0]
    assert club["spread"] == 50.0, "must be a within-variant gap, not 900 - 200"
    assert club["best"] == 200.0


def test_labelled_variants_come_before_the_unstated_pile():
    unstated = _variant(label="", best=300.0, vid=1)
    stated = _variant(label="10.5°, Stiff, RH", best=500.0, vid=2)
    club = group_into_clubs([unstated, stated])[0]
    assert club["variants"][0]["label"] == "10.5°, Stiff, RH"
    assert club["variants"][-1]["label"] == ""


def test_a_club_with_one_variant_still_works():
    club = group_into_clubs([_variant(label="9°, Stiff, RH", retailers=3)])[0]
    assert club["variant_count"] == 1
    assert club["sellers"] == 3


def test_suspect_offers_do_not_inflate_the_seller_count():
    offers = [
        {"retailer": "Real", "total": 400.0, "condition": "new",
         "suspect": False, "currency": "USD"},
        {"retailer": "Mismatch", "total": 40.0, "condition": "new",
         "suspect": True, "currency": "USD"},
    ]
    club = group_into_clubs([_variant(offers=offers, best=400.0)])[0]
    assert club["sellers"] == 1
    assert club["best"] == 400.0, "a flagged row must not become the headline price"


# --------------------------------------------------------------------------
# Variant labels
# --------------------------------------------------------------------------

def _row(**kw):
    base = {"set_composition": None, "loft": None, "bounce": None,
            "length": None, "flex": None, "dexterity": None}
    base.update(kw)
    return base


def test_variant_label_reads_in_the_order_people_say_it():
    assert variant_label(_row(set_composition="4-PW", flex="S", dexterity="RH")) \
        == "4-PW, Stiff, RH"
    assert variant_label(_row(loft=10.5, flex="R", dexterity="RH")) \
        == "10.5°, Regular, RH"


def test_a_whole_number_loft_loses_its_decimal():
    assert variant_label(_row(loft=9.0)) == "9°"


def test_no_stated_spec_gives_an_empty_label():
    """Not a failure — half of all listings never say. The page says so."""
    assert variant_label(_row()) == ""


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

