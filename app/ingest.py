"""
Ingest: pull every configured source, match, and write to SQLite.

    python -m app.ingest                 # all configured sources
    python -m app.ingest --source impact # one source
    python -m app.ingest --probe impact  # dump one raw item and exit
    python -m app.ingest --reset         # drop the db first

Run it on a schedule. Daily is enough for new-gear pricing; retailers change
prices in campaign cycles, not by the minute. Every run also writes one
price_history row per product per retailer, which is what backs the
"lowest in 90 days" badge.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone

from . import db
from .adapters import ADAPTERS_BY_ID, AdapterError, configured_adapters
from .adapters.base import Adapter, RawOffer
from .matching import ProductIndex, parse

log = logging.getLogger("ingest")


def ingest_offers(conn: sqlite3.Connection, offers: list[RawOffer]) -> tuple[int, int]:
    """
    Match a batch of raw offers into products and upsert both.

    Returns (offers written, products touched).

    The index is seeded from products already in the database so a second run
    attaches new offers to existing products instead of creating duplicates.
    """
    index = ProductIndex()
    key_to_id: dict[str, int] = {}

    for row in conn.execute("SELECT id, match_key, display_name, brand, club_type, "
                            "model, loft, flex, dexterity, shaft, set_composition "
                            "FROM products"):
        existing = parse(row["display_name"], brand=row["brand"])
        existing.match_key = row["match_key"]
        existing.brand = row["brand"]
        existing.club_type = row["club_type"]
        existing.model = row["model"] or ""
        existing.loft = row["loft"]
        existing.flex = row["flex"]
        existing.dexterity = row["dexterity"]
        existing.shaft = row["shaft"]
        existing.set_composition = row["set_composition"]
        existing.tokens = set(existing.model.split())
        index.assign(existing)
        key_to_id[row["match_key"]] = row["id"]

    written = 0
    touched: set[int] = set()

    for offer in offers:
        parsed = parse(
            offer.title,
            brand=offer.brand,
            category=offer.category,
            condition=offer.condition,
            gtin=offer.gtin,
            mpn=offer.mpn,
        )
        # Skip rows the parser can't place -- apparel, accessories, junk. Better
        # a smaller accurate catalog than a big one full of mystery rows.
        if not parsed.brand or not parsed.club_type:
            continue

        key = index.assign(parsed)
        product_id = key_to_id.get(key)

        if product_id is None:
            canonical = index.canonical(key) or parsed
            cur = conn.execute(
                """
                INSERT INTO products
                    (match_key, display_name, brand, club_type, model, loft, flex,
                     dexterity, shaft, set_composition, bounce, length, year, gtin,
                     image_url, msrp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(match_key) DO NOTHING
                """,
                (
                    key, canonical.display_name, canonical.brand, canonical.club_type,
                    canonical.model, canonical.loft, canonical.flex,
                    canonical.dexterity, canonical.shaft, canonical.set_composition,
                    canonical.bounce, canonical.length, canonical.year, canonical.gtin,
                    offer.image_url, offer.original_price or offer.price,
                ),
            )
            product_id = cur.lastrowid
            if not product_id:
                found = conn.execute(
                    "SELECT id FROM products WHERE match_key = ?", (key,)
                ).fetchone()
                product_id = found["id"] if found else None
            if product_id is None:
                continue
            key_to_id[key] = product_id
        else:
            # Keep MSRP at the highest list price any retailer has shown, and
            # backfill an image if the product came in without one.
            conn.execute(
                """
                UPDATE products
                   SET msrp = MAX(COALESCE(msrp, 0), COALESCE(?, 0)),
                       image_url = COALESCE(image_url, ?),
                       updated_at = datetime('now')
                 WHERE id = ?
                """,
                (offer.original_price or offer.price, offer.image_url, product_id),
            )

        touched.add(product_id)
        conn.execute(
            """
            INSERT INTO offers
                (product_id, source, retailer, external_id, title, price,
                 original_price, shipping, currency, url, condition, grade,
                 shaft, availability, image_url, commission_rate, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(source, external_id) DO UPDATE SET
                price           = excluded.price,
                original_price  = excluded.original_price,
                shipping        = excluded.shipping,
                availability    = excluded.availability,
                url             = excluded.url,
                title           = excluded.title,
                last_seen       = datetime('now')
            """,
            (
                product_id, offer.source, offer.retailer, offer.external_id,
                offer.title, offer.price, offer.original_price, offer.shipping,
                offer.currency, offer.url, parsed.condition, parsed.grade,
                parsed.shaft, offer.availability, offer.image_url,
                offer.commission_rate,
            ),
        )
        written += 1

        # One snapshot per product/retailer/day. Re-running ingest the same day
        # overwrites rather than duplicating.
        conn.execute(
            """
            INSERT INTO price_history (product_id, retailer, price)
            VALUES (?,?,?)
            ON CONFLICT(product_id, retailer, observed_on)
            DO UPDATE SET price = excluded.price
            """,
            (product_id, offer.retailer, offer.price),
        )

    return written, len(touched)


def run(source: str | None = None, limit: int = 2000, query: str | None = None) -> dict:
    db.init()
    results: dict[str, dict] = {}

    if source:
        adapter = ADAPTERS_BY_ID.get(source)
        if not adapter:
            raise SystemExit(f"unknown source {source!r}; "
                             f"try one of {', '.join(ADAPTERS_BY_ID)}")
        adapters: list[Adapter] = [adapter]
    else:
        adapters = configured_adapters()

    if not adapters:
        raise SystemExit(
            "No configured sources. Copy .env.example to .env and add credentials, "
            "or run scripts/make_sample_feed.py to use the sample feed."
        )

    for adapter in adapters:
        started = datetime.now(timezone.utc).isoformat()
        with db.session() as conn:
            cur = conn.execute(
                "INSERT INTO ingest_runs (source, started_at) VALUES (?,?)",
                (adapter.id, started),
            )
            run_id = cur.lastrowid

        try:
            offers = adapter.fetch(query=query, limit=limit)
            with db.session() as conn:
                written, products = ingest_offers(conn, offers)
                conn.execute(
                    """UPDATE ingest_runs
                          SET finished_at = datetime('now'), offers_seen = ?,
                              products = ?, status = 'ok'
                        WHERE id = ?""",
                    (written, products, run_id),
                )
            results[adapter.id] = {"offers": written, "products": products, "ok": True}
            log.info("%s: %d offers -> %d products", adapter.id, written, products)

        except (AdapterError, Exception) as exc:  # one bad source must not stop the rest
            with db.session() as conn:
                conn.execute(
                    """UPDATE ingest_runs
                          SET finished_at = datetime('now'), status = 'error', error = ?
                        WHERE id = ?""",
                    (str(exc), run_id),
                )
            results[adapter.id] = {"ok": False, "error": str(exc)}
            log.error("%s failed: %s", adapter.id, exc)

    return results


def probe(source: str) -> None:
    """Print one raw offer from a source. The fastest way to check that a
    feed's field names match what the adapter expects."""
    adapter = ADAPTERS_BY_ID.get(source)
    if not adapter:
        raise SystemExit(f"unknown source {source!r}")
    offers = adapter.fetch(limit=3)
    for offer in offers[:3]:
        print(json.dumps(offer.__dict__, indent=2, default=str))
        print("  parsed ->", json.dumps(
            parse(offer.title, brand=offer.brand, category=offer.category,
                  gtin=offer.gtin).as_dict(), indent=2, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description="Pull affiliate feeds into the local db")
    ap.add_argument("--source", help="only this adapter id")
    ap.add_argument("--query", help="restrict the pull to a search term")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--probe", help="dump raw+parsed offers from this source, then exit")
    ap.add_argument("--reset", action="store_true", help="delete the db first")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.probe:
        probe(args.probe)
        return

    if args.reset:
        from . import config
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
            for suffix in ("-wal", "-shm"):
                extra = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
                if extra.exists():
                    extra.unlink()
            log.info("dropped %s", config.DB_PATH)

    results = run(source=args.source, limit=args.limit, query=args.query)
    print(json.dumps(results, indent=2))
    if not any(r.get("ok") for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
