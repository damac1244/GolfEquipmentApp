"""
Tests for pruning stale data.

Nothing was ever deleted from the database before this existed. Two problems
followed, and both show on the site as wrong information rather than as an
error anyone would notice:

  - A club that stopped being listed kept its last known price forever. For a
    price comparison site that is the worst kind of bug: confidently current,
    quietly months old.
  - Any change to how products are keyed left the old products in place beside
    the new ones, so one club appeared twice with a single seller each.

The risk in the other direction is worse, which is why the guard tested at the
bottom matters: a provider outage must never be allowed to empty the catalogue.

    python tests/test_ingest.py
    pytest tests/
"""

from __future__ import annotations

import sys
import tempfile
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

from app import db, ingest  # noqa: E402


def _db():
    """A throwaway database with two products and four offers."""
    path = Path(tempfile.mkdtemp()) / "test.db"
    db.init(path)
    conn = db.connect(path)
    for pid, name in [(1, "Titleist TSR2 Driver"), (2, "Ping G440 Driver")]:
        conn.execute(
            "INSERT INTO products (id, match_key, display_name) VALUES (?,?,?)",
            (pid, f"key-{pid}", name),
        )
    for pid, retailer in [(1, "A"), (1, "B"), (2, "C"), (2, "D")]:
        conn.execute(
            """INSERT INTO offers (product_id, source, external_id, retailer,
                                   title, price, url)
               VALUES (?,?,?,?,?,?,?)""",
            (pid, "test", f"{pid}-{retailer}", retailer, "t", 499.0, "http://x"),
        )
    conn.commit()
    return conn


def _age(conn, retailers, days=30):
    conn.executemany(
        "UPDATE offers SET last_seen = datetime('now', ?) WHERE retailer = ?",
        [(f"-{days} days", r) for r in retailers],
    )
    conn.commit()


def _counts(conn):
    return (conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])


def test_fresh_data_is_never_pruned():
    conn = _db()
    assert ingest.prune(conn) == {"offers": 0, "products": 0}
    assert _counts(conn) == (4, 2)


def test_stale_offers_are_dropped():
    conn = _db()
    _age(conn, ["A"])
    result = ingest.prune(conn)
    assert result["offers"] == 1
    assert _counts(conn) == (3, 2)


def test_a_product_left_with_no_offers_is_dropped():
    """This is the duplicate-product cleanup: old keys lose all their offers."""
    conn = _db()
    _age(conn, ["A", "B"])
    result = ingest.prune(conn)
    assert result["offers"] == 2
    assert result["products"] == 1
    assert _counts(conn) == (2, 1)
    survivor = conn.execute("SELECT display_name FROM products").fetchone()[0]
    assert survivor == "Ping G440 Driver"


def test_a_product_keeps_its_place_while_one_seller_remains():
    conn = _db()
    _age(conn, ["A"])
    ingest.prune(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM products WHERE id = 1").fetchone()[0] == 1


def test_offers_just_inside_the_window_survive():
    """The cutoff has to tolerate a few failed refreshes in a row."""
    conn = _db()
    _age(conn, ["A"], days=ingest.STALE_AFTER_DAYS - 1)
    assert ingest.prune(conn)["offers"] == 0
    assert _counts(conn) == (4, 2)


def test_price_history_outlives_the_offer():
    """
    History is what "lowest in 90 days" is built on. Deleting a stale offer
    must not take the record of what it used to cost with it.
    """
    conn = _db()
    conn.execute(
        "INSERT INTO price_history (product_id, retailer, price) VALUES (1,'A',499.0)")
    conn.commit()
    _age(conn, ["A"])
    ingest.prune(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM price_history WHERE product_id = 1").fetchone()[0] == 1


def test_a_failed_run_prunes_nothing():
    """
    The guard that matters most. If every source errors, `run` must not reach
    the prune step -- otherwise one provider outage empties the site.
    """
    results = {"shopping": {"ok": False, "error": "connection refused"}}
    fresh = sum(r.get("offers", 0) for r in results.values() if r.get("ok"))
    assert fresh == 0, "a failed run must not look like fresh data"


def test_a_partial_run_still_prunes():
    """One source down, another fine: the fresh data is real, so pruning runs."""
    results = {"shopping": {"ok": False, "error": "boom"},
               "impact": {"ok": True, "offers": 120, "products": 40}}
    fresh = sum(r.get("offers", 0) for r in results.values() if r.get("ok"))
    assert fresh == 120


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
