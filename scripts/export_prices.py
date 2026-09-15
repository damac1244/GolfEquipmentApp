#!/usr/bin/env python3
"""
Turn the database into one JSON file a static website can read.

This is what lets the site work with no server at all. Cloudflare Pages serves
prices.json next to index.html, the page fetches it, and there is nothing to
run, pay for, or keep alive.

    python scripts/export_prices.py                       # -> site/prices.json
    python scripts/export_prices.py --out docs/prices.json
    python scripts/export_prices.py --min-offers 2        # only real comparisons

Prices are as fresh as the last ingest, which for golf equipment is fine —
retailers reprice in campaign cycles, not by the second.

Sanity limit: at a few thousand products this file gets too big for a browser
to load happily. That is the point to move to a real server, and the script
warns you when you are close.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

SIZE_WARN_MB = 2.0


def build(conn: sqlite3.Connection, min_offers: int, include_used: bool) -> dict:
    products = []

    rows = conn.execute("""
        SELECT id, display_name, brand, club_type, model, loft, flex,
               dexterity, set_composition, image_url, msrp
          FROM products ORDER BY display_name
    """).fetchall()

    for p in rows:
        sql = """
            SELECT retailer, source, condition, grade, price, shipping, url,
                   commission_rate, last_seen
              FROM offers WHERE product_id = ?
        """
        params: list = [p["id"]]
        if not include_used:
            sql += " AND condition = 'new'"
        sql += " ORDER BY (price + COALESCE(shipping, 0)) ASC"

        offers = []
        for o in conn.execute(sql, params):
            total = round(o["price"] + (o["shipping"] or 0), 2)
            offers.append({
                "retailer": o["retailer"],
                "condition": o["condition"],
                "grade": o["grade"],
                "price": round(o["price"], 2),
                # null means unknown shipping, which is not the same as free.
                "shipping": o["shipping"],
                "total": total,
                "url": o["url"],
                # Whether this row pays you. The page must never sort on it,
                # but you want to know.
                "earns": bool(o["commission_rate"]),
                "seen": o["last_seen"],
            })

        if len(offers) < min_offers:
            continue

        totals = [o["total"] for o in offers]
        best, worst = min(totals), max(totals)

        history = [dict(h) for h in conn.execute("""
            SELECT observed_on, MIN(price) AS low
              FROM price_history
             WHERE product_id = ? AND observed_on >= date('now', '-90 days')
             GROUP BY observed_on ORDER BY observed_on
        """, (p["id"],))]

        lows = [h["low"] for h in history]
        products.append({
            "id": p["id"],
            "name": p["display_name"],
            "brand": p["brand"],
            "type": p["club_type"],
            "model": p["model"],
            "loft": p["loft"],
            "flex": p["flex"],
            "dex": p["dexterity"],
            "set": p["set_composition"],
            "image": p["image_url"],
            "best": best,
            "worst": worst,
            "spread": round(worst - best, 2),
            "spread_pct": round((worst - best) / worst * 100, 1) if worst else 0.0,
            "new_from": min([o["total"] for o in offers if o["condition"] == "new"],
                            default=None),
            "used_from": min([o["total"] for o in offers if o["condition"] != "new"],
                             default=None),
            "retailers": len({o["retailer"] for o in offers}),
            "lowest_90d": round(min(lows), 2) if lows else None,
            # Only claim a low when there is enough history to mean it.
            "is_low": bool(lows and len(history) >= 3 and best <= min(lows) + 0.005),
            "offers": offers,
        })

    products.sort(key=lambda x: x["spread"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product_count": len(products),
        "offer_count": sum(len(p["offers"]) for p in products),
        "retailer_count": len({o["retailer"] for p in products for o in p["offers"]}),
        "products": products,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Export prices for a static site")
    ap.add_argument("--out", default="site/prices.json")
    ap.add_argument("--min-offers", type=int, default=1,
                    help="skip products with fewer offers than this "
                         "(2 = only real comparisons)")
    ap.add_argument("--new-only", action="store_true",
                    help="exclude used and open-box listings")
    args = ap.parse_args()

    conn = db.connect()
    try:
        payload = build(conn, args.min_offers, include_used=not args.new_only)
    finally:
        conn.close()

    if not payload["products"]:
        print("No products to export. Run an ingest first:")
        print("  python -m app.ingest --source shopping")
        raise SystemExit(1)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))

    size_mb = out.stat().st_size / 1_048_576
    print(f"{payload['product_count']} products, {payload['offer_count']} offers "
          f"from {payload['retailer_count']} retailers")
    print(f"-> {out} ({size_mb:.2f} MB)")

    if size_mb > SIZE_WARN_MB:
        print(f"\n  ! Over {SIZE_WARN_MB} MB. Browsers will start to feel this.")
        print("    Narrow it with --min-offers 2, or move to a real API.")


if __name__ == "__main__":
    main()
