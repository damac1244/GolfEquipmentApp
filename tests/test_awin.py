"""
Tests for the Awin adapter.

No API key needed: a stub client serves a feed list and a product feed shaped
the way Awin's documentation describes, so the whole path — list, pick the
advertiser, download, decompress, map columns — runs offline.

Two things matter more than the rest and have their own tests. The link has to
be `aw_deep_link`, because the entire reason this adapter exists is that every
other row on the site links to a Google search page. And a used club must not
be recorded as new, because that is the one error that would put a worn club
at the top of the table pretending to be boxed.

    python tests/test_awin.py
    pytest tests/
"""

from __future__ import annotations

import gzip
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

from app.adapters.awin import (  # noqa: E402
    AwinAdapter, decompress, normalize_condition, pick, to_float,
)

FEED_LIST = (
    "Advertiser ID,Advertiser Name,Primary Region,Membership Status,Feed ID,"
    "Feed Name,Language,Vertical,Last Imported,URL\n"
    "95935,2nd Swing Golf,US,joined,12345,Default,en,retail,2026-10-05,"
    "https://productdata.awin.com/datafeed/download/apikey/K/fid/12345/\n"
    "11111,Some Other Shop,US,not joined,99999,Default,en,retail,2026-10-05,"
    "https://productdata.awin.com/datafeed/download/apikey/K/fid/99999/\n"
)

PRODUCT_FEED = (
    "aw_deep_link,product_name,search_price,merchant_name,merchant_image_url,"
    "delivery_cost,currency,condition,in_stock,brand_name,merchant_product_id,"
    "rrp_price\n"
    "https://awin1.com/cread.php?p=tsr3,Titleist TSR3 Driver 9 Stiff RH,"
    "399.99,2nd Swing Golf,https://img.example/tsr3.jpg,0.00,USD,Used - Very Good,"
    "1,Titleist,SKU-1,649.99\n"
    "https://awin1.com/cread.php?p=p790,TaylorMade P790 Irons 4-PW Stiff,"
    "899.00,2nd Swing Golf,https://img.example/p790.jpg,12.95,USD,New,1,"
    "TaylorMade,SKU-2,\n"
    # junk rows a real feed always contains
    ",No Link Here,99.00,2nd Swing Golf,,0,USD,New,1,X,SKU-3,\n"
    "https://awin1.com/cread.php?p=x,,49.00,2nd Swing Golf,,0,USD,New,1,X,SKU-4,\n"
    "https://awin1.com/cread.php?p=y,Zero Price Club,0.00,2nd Swing Golf,,0,USD,"
    "New,1,X,SKU-5,\n"
)


class _Response:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class StubClient:
    """Serves the list, then gzipped feeds — the shapes Awin actually sends."""

    def __init__(self, list_csv=FEED_LIST, feed_csv=PRODUCT_FEED):
        self.list_csv = list_csv
        self.feed_csv = feed_csv
        self.requested: list[str] = []

    def get(self, url, *a, **kw):
        # The first request of a run is always the feed list, whatever URL it
        # was reached by — the adapter reads the list, then the feeds it names.
        first = not self.requested
        self.requested.append(url)
        if first:
            return _Response(self.list_csv.encode())
        return _Response(gzip.compress(self.feed_csv.encode()))


def _adapter(client=None, advertisers="2nd Swing", feed_ids=""):
    from app import config
    from app.adapters import awin

    config.AWIN_API_KEY = "test-key"
    config.AWIN_FEED_LIST_URL = ""
    config.AWIN_ADVERTISERS = advertisers
    config.AWIN_FEED_IDS = feed_ids
    config.AWIN_DEFAULT_RATE = 0.05

    stub = client or StubClient()

    class _Ctx:
        def __enter__(self): return stub
        def __exit__(self, *a): return False

    awin.httpx.Client = lambda *a, **kw: _Ctx()   # type: ignore[assignment]
    return AwinAdapter(), stub


# --------------------------------------------------------------------------
# The two that matter
# --------------------------------------------------------------------------

def test_the_link_is_the_tracked_deep_link():
    """
    Every other source on the site links to a Google search page. This adapter
    exists to produce links that go to the seller's own product page and earn
    on the way.
    """
    a, _ = _adapter()
    offers = a.fetch()
    assert offers, "expected offers from the stub feed"
    for o in offers:
        assert o.url.startswith("https://awin1.com/cread.php"), o.url


def test_a_used_club_is_not_recorded_as_new():
    a, _ = _adapter()
    by_title = {o.title: o for o in a.fetch()}
    tsr3 = by_title["Titleist TSR3 Driver 9 Stiff RH"]
    p790 = by_title["TaylorMade P790 Irons 4-PW Stiff"]
    assert tsr3.condition == "used", "\"Used - Very Good\" must not become new"
    assert p790.condition == "new"


# --------------------------------------------------------------------------
# Reaching the feed list
# --------------------------------------------------------------------------

def test_a_pasted_list_url_is_used_exactly_as_given():
    """
    Awin prints a Feed List Download URL in the account, key included. It has
    served that from more than one host, so a URL copied from the account beats
    one we assemble from documentation.
    """
    from app import config
    a, stub = _adapter()
    config.AWIN_API_KEY = None
    config.AWIN_FEED_LIST_URL = "https://ui.awin.com/whatever/apikey/XYZ"
    a.fetch()
    assert stub.requested[0] == "https://ui.awin.com/whatever/apikey/XYZ"
    config.AWIN_FEED_LIST_URL = ""


def test_a_bare_key_still_works():
    from app import config
    a, stub = _adapter()
    config.AWIN_FEED_LIST_URL = ""
    config.AWIN_API_KEY = "KEY123"
    a.fetch()
    assert stub.requested[0].endswith("/list/apikey/KEY123")


def test_configured_means_either_one():
    from app import config
    a, _ = _adapter()
    config.AWIN_API_KEY = None
    config.AWIN_FEED_LIST_URL = ""
    assert not a.is_configured()
    config.AWIN_FEED_LIST_URL = "https://ui.awin.com/x"
    assert a.is_configured()
    config.AWIN_FEED_LIST_URL = ""


# --------------------------------------------------------------------------
# Choosing feeds
# --------------------------------------------------------------------------

def test_only_joined_advertisers_are_downloaded():
    """The list includes programmes you could apply to, not just yours."""
    a, stub = _adapter()
    a.fetch()
    downloads = [u for u in stub.requested if "/download/" in u]
    assert len(downloads) == 1
    assert "12345" in downloads[0] and "99999" not in downloads[0]


def test_the_advertiser_name_matches_loosely():
    """"2nd Swing" has to find "2nd Swing Golf"."""
    a, stub = _adapter(advertisers="2nd swing")
    assert a.fetch()


def test_an_explicit_feed_id_wins_over_the_name():
    a, stub = _adapter(advertisers="nothing matches this", feed_ids="12345")
    assert a.fetch()


def test_no_matching_feed_says_so_clearly():
    from app.adapters.base import AdapterError
    a, _ = _adapter(advertisers="Nonexistent Shop")
    try:
        a.fetch()
    except AdapterError as exc:
        assert "no feeds matching" in str(exc).lower()
    else:
        raise AssertionError("a silent empty result would look like a dead feed")


# --------------------------------------------------------------------------
# Name matching.
#
# The live account returned 907 feeds and matched none of them. A plain
# substring test on "2nd Swing" misses "2ndSwing.com", and checking membership
# status before the name turned every unfamiliar status value into a silent
# zero result out of nine hundred rows.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("advertiser", [
    "2nd Swing Golf", "2ndSwing.com", "2nd-Swing", "2ND SWING GOLF US",
    "2nd  Swing  Golf",
])
def test_the_advertiser_is_found_however_it_is_punctuated(advertiser):
    rows = FEED_LIST.replace("2nd Swing Golf", advertiser)
    a, _ = _adapter(client=StubClient(list_csv=rows))
    assert a.fetch(), f"did not match {advertiser!r}"


def test_an_unfamiliar_membership_status_does_not_silently_drop_the_feed():
    """
    Status wording varies by account and region. A name match is the strong
    signal; an odd status is worth a warning and one wasted request, not a
    zero result nobody can explain.
    """
    rows = FEED_LIST.replace(",joined,", ",Programme Approved,")
    a, _ = _adapter(client=StubClient(list_csv=rows))
    assert a.fetch()


def test_a_miss_names_the_closest_advertisers():
    from app.adapters.base import AdapterError
    rows = FEED_LIST.replace("2nd Swing Golf", "2nd Swing Golf Europe")
    a, _ = _adapter(client=StubClient(list_csv=rows), advertisers="2nd Swong")
    try:
        a.fetch()
    except AdapterError as exc:
        assert "closest names" in str(exc).lower(), str(exc)
        assert "2nd Swing Golf Europe" in str(exc)
    else:
        raise AssertionError("expected a miss")


def test_a_miss_with_nothing_similar_says_the_programme_may_not_be_listed_yet():
    from app.adapters.base import AdapterError
    a, _ = _adapter(advertisers="Completely Different Shop")
    try:
        a.fetch()
    except AdapterError as exc:
        assert "not on this account" in str(exc).lower(), str(exc)
    else:
        raise AssertionError("expected a miss")


# --------------------------------------------------------------------------
# Rows a real feed contains
# --------------------------------------------------------------------------

def test_unusable_rows_are_dropped():
    """No link, no title, or no price — all three appear in every feed."""
    a, _ = _adapter()
    offers = a.fetch()
    assert len(offers) == 2, [o.title for o in offers]


def test_prices_shipping_and_rrp_are_read():
    a, _ = _adapter()
    by_title = {o.title: o for o in a.fetch()}
    tsr3 = by_title["Titleist TSR3 Driver 9 Stiff RH"]
    p790 = by_title["TaylorMade P790 Irons 4-PW Stiff"]
    assert tsr3.price == 399.99
    assert tsr3.original_price == 649.99
    assert p790.shipping == 12.95
    assert p790.original_price is None, "no rrp in the row means no fake discount"


def test_rows_are_marked_as_earning():
    """The export uses this to tell a paying link from a Google one."""
    a, _ = _adapter()
    assert all(o.commission_rate for o in a.fetch())


def test_ids_are_unique_per_retailer_and_product():
    a, _ = _adapter()
    ids = [o.external_id for o in a.fetch()]
    assert len(ids) == len(set(ids))


def test_limit_is_respected():
    a, _ = _adapter()
    assert len(a.fetch(limit=1)) == 1


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("New", "new"), ("Brand New", "new"),
    ("Used", "used"), ("Pre-Owned", "used"), ("Used - Very Good", "used"),
    ("Refurbished", "refurbished"), ("Open Box", "open_box"),
    # An advertiser's own grade wording. Used is the safe reading: calling a
    # worn club new is the damaging direction.
    ("Very Good", "used"), ("Excellent", "used"),
    (None, None), ("", None),
])
def test_condition_wording(raw, expected):
    assert normalize_condition(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("129.99", 129.99), ("£129.99", 129.99), ("1,299.00", 1299.0),
    ("", None), (None, None), ("0.00", 0.0 or None),
])
def test_price_parsing(raw, expected):
    assert to_float(raw) == expected


def test_a_column_is_found_whatever_its_case():
    assert pick({"Product_Name": "Qi35"}, ("product_name",)) == "Qi35"


@pytest.mark.parametrize("body", [
    gzip.compress(b"a,b\n1,2\n"),
    b"a,b\n1,2\n",
])
def test_feeds_decompress_however_they_arrive(body):
    assert decompress(body).startswith("a,b")


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
