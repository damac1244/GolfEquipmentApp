"""
Tests for the shopping-search adapter.

No API key needed: a stub provider returns realistically messy rows, so the
whole path — provider response, price parsing, junk filtering, matching into
products — is exercised offline. Run the same way as the matcher tests:

    python tests/test_shopping.py
    pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

from app.adapters.shopping import (  # noqa: E402
    ShoppingAdapter, looks_like_golf, parse_price, parse_shipping,
)


# --------------------------------------------------------------------------
# Price parsing — a wrong number here is worse than no number
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("$549.99", 549.99),
    ("$1,299.00", 1299.0),
    ("$1,299", 1299.0),
    ("From $299.00", 299.0),
    ("549.99", 549.99),
    (549.99, 549.99),
    (1299, 1299.0),
    ("US$429.95", 429.95),
    ("", None),
    (None, None),
    ("Price unavailable", None),
    (0, None),
    ("$0.00", None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_unparseable_price_is_none_not_zero():
    """A zero would sort to the top and advertise a free driver."""
    assert parse_price("call for price") is None
    assert parse_price("--") is None


@pytest.mark.parametrize("raw,expected", [
    ("Free delivery", 0.0),
    ("Free shipping", 0.0),
    ("$5.99 delivery", 5.99),
    ("$12.95 shipping", 12.95),
    (0, 0.0),
    (7.5, 7.5),
    (None, None),
    ("", None),
])
def test_parse_shipping(raw, expected):
    assert parse_shipping(raw) == expected


def test_unknown_shipping_is_none_not_free():
    """None and 0.0 mean different things when ranking on total price."""
    assert parse_shipping("Delivery by Tuesday") is None
    assert parse_shipping("Free delivery") == 0.0


# --------------------------------------------------------------------------
# Junk filtering
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,keep", [
    ("TaylorMade Qi35 Driver 10.5 Stiff", True),
    ("Mizuno JPX925 Hot Metal Irons 5-PW", True),
    ("Titleist Pro V1 Golf Balls - 1 Dozen", True),
    ("Scotty Cameron Phantom 11 Putter 34\"", True),
    ("TaylorMade Qi35 Driver Headcover", False),
    ("Golf Towel with Carabiner", False),
    ("TaylorMade Golf Hat Adjustable", False),
    ("", False),
])
def test_looks_like_golf(title, keep):
    assert looks_like_golf(title) is keep


def test_headcover_is_rejected_despite_matching_club_name():
    """A "Qi35 Driver Headcover" names a driver but isn't one."""
    assert looks_like_golf("TaylorMade Qi35 Driver Headcover") is False


# --------------------------------------------------------------------------
# Full path, with a stub provider
# --------------------------------------------------------------------------

class StubProvider:
    """Returns the kind of mess a real shopping API returns."""

    id = "stub"
    calls: list[str] = []

    def is_configured(self) -> bool:
        return True

    def search(self, client, query, limit):
        StubProvider.calls.append(query)
        return [
            {"title": "TaylorMade Qi35 Driver - 10.5° / Stiff / Right Hand",
             "merchant": "Retailer A", "price": "$549.99", "old_price": "$629.99",
             "url": "https://a.example/qi35", "image": None, "shipping": "Free delivery"},
            {"title": "TaylorMade Qi35 Driver (Right Hand, 10.5 Degree, Stiff Flex)",
             "merchant": "Retailer B", "price": "$489.00", "old_price": None,
             "url": "https://b.example/qi35", "image": None, "shipping": "$9.95 delivery"},
            {"title": "2025 TaylorMade Qi35 Men's Driver 10.5* S-Flex RH - Used Excellent",
             "merchant": "Marketplace", "price": "$379.00", "old_price": None,
             "url": "https://c.example/qi35", "image": None, "shipping": "Free delivery"},
            # junk that must be dropped
            {"title": "TaylorMade Qi35 Driver Headcover", "merchant": "Retailer A",
             "price": "$34.99", "url": "https://a.example/hc", "shipping": None},
            # unusable rows
            {"title": "TaylorMade Qi35 Driver", "merchant": "Retailer C",
             "price": "call us", "url": "https://c.example/x", "shipping": None},
            {"title": "", "merchant": "Retailer D", "price": "$1.00",
             "url": "https://d.example/x", "shipping": None},
        ]


def _stub_adapter(tmp_seed: Path) -> ShoppingAdapter:
    from app import config
    from app.adapters import shopping

    a = ShoppingAdapter()
    a.provider = StubProvider()
    a.seed_queries = lambda: ["TaylorMade Qi35 driver"]  # type: ignore[method-assign]
    # No cache writes during tests.
    shopping.cache_get = lambda p, q: None   # type: ignore[assignment]
    shopping.cache_put = lambda p, q, r: None  # type: ignore[assignment]
    return a


def test_fetch_keeps_real_clubs_and_drops_junk(tmp_path=None):
    a = _stub_adapter(Path("."))
    offers = a.fetch()
    titles = [o.title for o in offers]

    assert len(offers) == 3, f"expected 3 usable offers, got {len(offers)}: {titles}"
    assert not any("Headcover" in t for t in titles)
    assert all(o.price > 0 for o in offers)


def test_shipping_is_carried_through():
    a = _stub_adapter(Path("."))
    by_retailer = {o.retailer: o for o in a.fetch()}
    assert by_retailer["Retailer A"].shipping == 0.0
    assert by_retailer["Retailer B"].shipping == 9.95


def test_sale_price_recorded_only_when_higher_than_current():
    a = _stub_adapter(Path("."))
    by_retailer = {o.retailer: o for o in a.fetch()}
    assert by_retailer["Retailer A"].original_price == 629.99
    assert by_retailer["Retailer B"].original_price is None


def test_offers_are_marked_as_earning_nothing():
    """
    These rows pay no commission. If that ever silently becomes 0.0 or a
    number, you'd lose the ability to tell which half of the table earns.
    """
    a = _stub_adapter(Path("."))
    assert all(o.commission_rate is None for o in a.fetch())


def test_external_ids_are_stable_across_runs():
    """Shopping APIs reshuffle their own ids; ours must not move, or every
    ingest duplicates the whole catalogue."""
    a = _stub_adapter(Path("."))
    first = sorted(o.external_id for o in a.fetch())
    second = sorted(o.external_id for o in a.fetch())
    assert first == second
    assert len(set(first)) == len(first)


def test_shopping_offers_match_into_one_product():
    """
    The point of the whole exercise: three differently-worded listings of the
    same driver from three merchants collapse into one comparable product.
    """
    from app.matching import ProductIndex, parse

    a = _stub_adapter(Path("."))
    index = ProductIndex()
    keys = {index.assign(parse(o.title, brand=o.brand)) for o in a.fetch()}
    assert len(keys) == 1, f"expected 1 product, got {len(keys)}: {keys}"


def test_used_listing_keeps_its_condition():
    """Condition rides on the offer, so new and used sit in one table."""
    from app.matching import parse

    a = _stub_adapter(Path("."))
    conds = {o.retailer: parse(o.title).condition for o in a.fetch()}
    assert conds["Marketplace"] == "used"
    assert conds["Retailer A"] == "new"


def test_unconfigured_adapter_explains_itself():
    from app.adapters.base import AdapterError

    a = ShoppingAdapter()
    a.provider = None
    assert a.is_configured() is False
    try:
        a.fetch()
    except AdapterError as exc:
        assert "GOLF_SHOPPING_PROVIDER" in str(exc)
    else:
        raise AssertionError("expected AdapterError")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

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
