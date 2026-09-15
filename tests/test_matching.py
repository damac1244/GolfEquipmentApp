"""
Tests for the matcher.

These are the tests that matter. If matching breaks, the app silently shows one
product per retailer instead of one product with five prices, and nothing else
in the codebase will notice.

Two failure modes, tested separately:
  - under-merging: the same club appears as several products (test_variants)
  - over-merging: different clubs collapse into one (test_must_not_merge)

Over-merging is the worse bug. It shows a shopper a price for a club that
isn't the one they're looking at.

Run with pytest, or directly with no dependencies at all:

    pytest tests/                  # normal
    python tests/test_matching.py  # no pytest installed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover - fallback for bare environments
    class _Mark:
        @staticmethod
        def parametrize(argnames, argvalues):
            names = [n.strip() for n in argnames.split(",")]

            def decorator(fn):
                fn._params = (names, argvalues)
                return fn

            return decorator

    class _PytestShim:
        mark = _Mark()

    pytest = _PytestShim()  # type: ignore[assignment]

from app.matching import ProductIndex, normalize_gtin, parse  # noqa: E402


# --------------------------------------------------------------------------
# The core case: one club, six retailers, six title styles
# --------------------------------------------------------------------------

QI35_VARIANTS = [
    "TaylorMade Qi35 Driver - 10.5° / Stiff / Right Hand",
    "TaylorMade Qi35 Driver (Right Hand, 10.5 Degree, Stiff Flex)",
    "TaylorMade Qi35 Driver 10.5 Stiff RH",
    "2025 TaylorMade Qi35 Men's Driver 10.5* S-Flex RH - NEW!",
    "TaylorMade Golf Qi35 Driver 10.5 Degree Stiff Right Handed",
    "TaylorMade Qi35 Driver w/ Ventus Blue 10.5 Stiff RH",
]


def test_variants_collapse_to_one_product():
    index = ProductIndex()
    keys = {index.assign(parse(t)) for t in QI35_VARIANTS}
    assert len(keys) == 1, f"expected 1 product, got {len(keys)}: {keys}"


def test_variants_parse_consistently():
    parsed = [parse(t) for t in QI35_VARIANTS]
    assert {p.brand for p in parsed} == {"TaylorMade"}
    assert {p.club_type for p in parsed} == {"driver"}
    assert {p.loft for p in parsed} == {10.5}
    assert {p.flex for p in parsed} == {"S"}
    assert {p.dexterity for p in parsed} == {"RH"}
    assert {p.model for p in parsed} == {"qi35"}


# --------------------------------------------------------------------------
# Over-merging guards
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,why", [
    ("TaylorMade Qi35 Driver 10.5 Stiff RH",
     "TaylorMade Qi35 Driver 9 Stiff RH", "different loft"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH",
     "TaylorMade Qi35 Driver 10.5 Regular RH", "different flex"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH",
     "TaylorMade Qi35 Driver 10.5 Stiff LH", "different dexterity"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH",
     "TaylorMade Qi35 Max Driver 10.5 Stiff RH", "different model"),
    ("Titleist Pro V1 Golf Balls",
     "Titleist Pro V1x Golf Balls", "Pro V1 vs Pro V1x are not the same ball"),
    ("Mizuno JPX925 Hot Metal Irons 5-PW Regular RH",
     "Mizuno JPX925 Forged Irons 5-PW Regular RH", "different iron model"),
    ("TaylorMade P790 Irons 4-PW Stiff RH",
     "TaylorMade P790 Irons 5-PW Stiff RH", "different set composition"),
    ("Vokey SM10 Wedge 56 RH",
     "Vokey SM10 Wedge 60 RH", "different wedge loft"),
    # Regression: a blanket "drop 1-2 digit tokens" rule in extract_model ate
    # putter model numbers and merged every Phantom into one product.
    ("Scotty Cameron Phantom 5 Putter 34\"",
     "Scotty Cameron Phantom 11 Putter 34\"", "different putter model number"),
    ("PING i230 Irons 4-PW Regular RH",
     "PING i530 Irons 4-PW Regular RH", "different iron model number"),
])
def test_must_not_merge(a, b, why):
    index = ProductIndex()
    ka, kb = index.assign(parse(a)), index.assign(parse(b))
    assert ka != kb, f"wrongly merged ({why}): {a!r} + {b!r}"


# --------------------------------------------------------------------------
# Attribute extraction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("PING G440 Max Driver 10.5 Stiff RH", 10.5),
    ("Callaway Elyte Driver 9° Regular", 9.0),
    ("2025 Cobra DS-Adapt Max Driver 10.5* S-Flex", 10.5),
    ("Vokey SM10 Wedge 56.10 S Grind", 56.0),
    ("Titleist Pro V1 Golf Balls", None),
])
def test_loft(title, expected):
    assert parse(title).loft == expected


@pytest.mark.parametrize("title,expected", [
    ("TaylorMade Qi35 Driver X-Stiff", "X"),
    ("TaylorMade Qi35 Driver Stiff", "S"),
    ("TaylorMade Qi35 Driver Regular Flex", "R"),
    ("TaylorMade Qi35 Driver Senior", "A"),
    ("TaylorMade Qi35 Ladies Driver", "L"),
    ("2025 TaylorMade Qi35 Men's Driver 10.5* S-Flex RH", "S"),
])
def test_flex(title, expected):
    assert parse(title).flex == expected


def test_xstiff_is_not_read_as_stiff():
    """Longest-alias-first matching. Getting this wrong merges X into S."""
    assert parse("TaylorMade Qi35 Driver 9 X-Stiff RH").flex == "X"


@pytest.mark.parametrize("title,expected", [
    ("PING G440 Driver Left Handed", "LH"),
    ("PING G440 Driver (LH)", "LH"),
    ("PING G440 Driver Right Hand", "RH"),
    ("PING G440 Driver RH", "RH"),
])
def test_dexterity(title, expected):
    assert parse(title).dexterity == expected


@pytest.mark.parametrize("title,expected", [
    ("TaylorMade Qi35 Driver 10.5 Stiff", "driver"),
    ("Mizuno JPX925 Irons 5-PW", "iron_set"),
    ("Vokey SM10 Wedge 56", "wedge"),
    ("Scotty Cameron Phantom 11 Putter 34\"", "putter"),
    ("Titleist Pro V1 Golf Balls", "golf_balls"),
    ("Callaway Elyte 3 Wood 15 Stiff", "fairway_wood"),
    ("PING G440 Hybrid 19 Regular", "hybrid"),
])
def test_club_type(title, expected):
    assert parse(title).club_type == expected


@pytest.mark.parametrize("title,expected", [
    ("Titleist Scotty Cameron Phantom 11 Putter", "Scotty Cameron"),
    ("Titleist Vokey SM10 Wedge 56", "Vokey"),
    ("Taylor Made Qi35 Driver", "TaylorMade"),
    ("TAYLORMADE GOLF Qi35 Driver", "TaylorMade"),
    ("Callaway Golf Elyte Driver", "Callaway"),
])
def test_brand_longest_alias_wins(title, expected):
    """"Titleist Scotty Cameron" is a Scotty Cameron, not a Titleist."""
    assert parse(title).brand == expected


def test_set_composition():
    assert parse("TaylorMade P790 Irons 4-PW Stiff RH").set_composition == "4-PW"
    assert parse("Mizuno JPX925 Irons 5-PW Regular").set_composition == "5-PW"


def test_condition_is_detected_but_not_part_of_identity():
    """
    Open-box and new are the same product at different prices. They must share
    a key so they appear in one compare table, with condition on the row.
    """
    new = parse("TaylorMade Qi35 Driver 10.5 Stiff RH")
    box = parse("TaylorMade Qi35 Driver 10.5 Stiff RH - Open Box")
    assert new.condition == "new"
    assert box.condition == "open_box"
    assert new.match_key == box.match_key


def test_shaft_naming_does_not_split_a_product():
    """The stock-shaft inconsistency -- see SHAFT_IN_KEY in matching.py."""
    bare = parse("TaylorMade Qi35 Driver 10.5 Stiff RH")
    shafted = parse("TaylorMade Qi35 Driver w/ Ventus Blue 10.5 Stiff RH")
    assert shafted.shaft == "Ventus Blue"
    assert bare.match_key == shafted.match_key


@pytest.mark.parametrize("title,grade", [
    ("TaylorMade Qi35 Driver 10.5 Stiff RH - Used Excellent", "Excellent"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH - Very Good", "Very Good"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH (Good)", "Good"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH - Mint", "Mint"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH Like New", "Like New"),
    ("TaylorMade Qi35 Driver 10.5 Stiff RH", None),
])
def test_grade_extraction(title, grade):
    assert parse(title).grade == grade


def test_very_good_is_not_read_as_good():
    """Longest-first again. Collapsing these misprices half the used market."""
    assert parse("Ping G440 Driver 10.5 Stiff RH Very Good").grade == "Very Good"


def test_grade_does_not_split_a_product():
    """
    The whole premise: a new listing and three differently-graded used ones
    are ONE product with four rows, not four products.
    """
    index = ProductIndex()
    titles = [
        "TaylorMade Qi35 Driver 10.5 Stiff RH",
        "TaylorMade Qi35 Driver 10.5 Stiff RH - Used Excellent",
        "TaylorMade Qi35 Driver 10.5 Stiff RH - Very Good",
        "TaylorMade Qi35 Driver 10.5 Stiff RH - Used Good",
    ]
    keys = {index.assign(parse(t)) for t in titles}
    assert len(keys) == 1, f"expected 1 product, got {len(keys)}: {keys}"


def test_grade_words_leave_the_model_string():
    p = parse("TaylorMade Qi35 Driver 10.5 Stiff RH - Used Excellent")
    assert p.model == "qi35"
    assert p.condition == "used"
    assert p.grade == "Excellent"


def test_unknown_shaft_after_w_slash_does_not_split_a_product():
    """
    Regression: "Denali" wasn't in SHAFT_BRANDS, so it stayed in the model
    string and split the Titleist GT2 into two products. The shaft list will
    never be complete, so "w/ <anything>" is handled generically.
    """
    bare = parse("Titleist GT2 Driver 10 Regular RH")
    shafted = parse("Titleist GT2 Driver w/ Denali 10 Regular RH")
    assert bare.match_key == shafted.match_key
    assert shafted.shaft is not None


def test_unlisted_shaft_is_still_captured():
    p = parse("Callaway Elyte Driver w/ Graphite Design Tour AD 9 Stiff RH")
    assert p.shaft is not None
    assert p.model == "elyte"


def test_model_year_does_not_split_a_product():
    a = parse("TaylorMade Qi35 Driver 10.5 Stiff RH")
    b = parse("2025 TaylorMade Qi35 Driver 10.5 Stiff RH")
    assert a.match_key == b.match_key


# --------------------------------------------------------------------------
# GTIN
# --------------------------------------------------------------------------

def test_gtin_normalization_pads_upc_to_13():
    assert normalize_gtin("012345678905") == "0012345678905"
    assert normalize_gtin("0012345678905") == "0012345678905"
    assert normalize_gtin("012-345-678-905") == "0012345678905"
    assert normalize_gtin("nonsense") is None
    assert normalize_gtin(None) is None


def test_gtin_beats_title_parsing():
    """Two differently-worded listings with one GTIN are one product."""
    index = ProductIndex()
    a = index.assign(parse("TaylorMade Qi35 Driver 10.5 S RH", gtin="012345678905"))
    b = index.assign(parse("TM Qi35 Dr 10.5 Stiff Right", gtin="0012345678905"))
    assert a == b


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------

def test_display_name_is_rebuilt_cleanly():
    name = parse("2025 TaylorMade Qi35 Men's Driver 10.5* S-Flex RH - NEW!").display_name
    assert "TaylorMade" in name
    assert "Qi35" in name
    assert "Driver" in name
    assert "10.5°" in name
    assert "Stiff" in name
    assert "NEW!" not in name


def test_display_name_omits_shaft():
    name = parse("TaylorMade Qi35 Driver w/ Ventus Blue 10.5 Stiff RH").display_name
    assert "Ventus" not in name


# --------------------------------------------------------------------------
# Standalone runner, for environments without pytest
# --------------------------------------------------------------------------

def _run_standalone() -> int:
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed = failed = 0
    failures: list[str] = []

    for name, fn in tests:
        params = getattr(fn, "_params", None)
        cases = (
            [dict(zip(params[0], v if isinstance(v, tuple) else (v,)))
             for v in params[1]]
            if params else [{}]
        )
        for kwargs in cases:
            label = f"{name}{tuple(kwargs.values()) if kwargs else ''}"
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
