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

# An offer priced below this fraction of the group's median is almost certainly
# not the same thing as the others -- typically a single iron sitting in a set's
# listing, or a headcover, or a shaft alone.
#
# Why a price rule rather than better parsing: retailers list an individual iron
# and a full seven-iron set under the same words ("Titleist T150 Irons"), so no
# amount of title reading separates them. The price does. Real observed case:
# a $170 single iron inside a set group averaging $1,400, which the site would
# have advertised as $1,825 of savings that do not exist.
#
# These offers are NOT deleted. They are flagged, kept out of the headline
# best/spread figures, and shown with a warning — hiding data would be worse
# than showing it honestly labelled.
QUANTITY_OUTLIER_RATIO = 0.5

# And the same problem at the other end. Observed live: Titleist Pro V1x golf
# balls listed from $27.99 to $591.44 — the $591 being a bulk case, not a
# dozen. The first version of this guard only looked downward, so an expensive
# mismatch sailed through and inflated the spread from the top instead.
#
# 3x the median is deliberately loose: a new club really can cost three times a
# well-worn used one, and that is a comparison worth showing. Beyond 3x you are
# almost always looking at a different quantity.
QUANTITY_OUTLIER_HIGH = 3.0

# Below this many offers a median means little, so small groups use the rule
# below instead of being left unchecked. Leaving them unchecked was the first
# attempt, and it let a 2-seller product through at $137 against $1,057.
MIN_OFFERS_FOR_MEDIAN = 4

# Small-group rule: with only 2-3 offers there is no meaningful middle, so
# compare against the dearest instead. Anything under this fraction of the most
# expensive offer is treated as a different item.
#
# Calibrated against real golf pricing: the legitimate range for one club
# across sellers — new at full price down to a well-worn used copy — tops out
# around 3x. Beyond that you are looking at a single iron in a set's listing,
# a head-only, or a shaft. 0.35 sits just past 3x and leaves genuine used
# bargains alone.
SMALL_GROUP_MAX_RATIO = 0.35


def median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def flag_quantity_outliers(offers: list[dict]) -> int:
    """
    Mark offers too cheap to plausibly be the same item. Returns how many.
    Every offer gets an explicit `suspect` key so the front end never has to
    guess at a missing field.
    """
    for o in offers:
        o["suspect"] = False

    totals = [o["total"] for o in offers]
    if len(totals) < 2:
        return 0

    if len(totals) >= MIN_OFFERS_FOR_MEDIAN:
        reference = median(totals)
        low = reference * QUANTITY_OUTLIER_RATIO
        high = reference * QUANTITY_OUTLIER_HIGH
    else:
        # With two or three offers there is no meaningful middle, and no way to
        # tell which end is the odd one out. Compare against the dearest and
        # flag only the bottom -- the safer guess, since a mismatched single
        # club is far more common than a mismatched bulk case.
        reference = max(totals)
        low = reference * SMALL_GROUP_MAX_RATIO
        high = float("inf")

    if reference <= 0:
        return 0

    flagged = 0
    for o in offers:
        if o["total"] < low or o["total"] > high:
            o["suspect"] = True
            flagged += 1

    # Never flag everything -- if each offer is below the threshold the
    # threshold is wrong, not the data.
    if flagged == len(offers):
        for o in offers:
            o["suspect"] = False
        return 0
    return flagged


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
                   commission_rate, rating, rating_count, last_seen
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
                "rating": o["rating"],
                "rating_count": o["rating_count"],
                "seen": o["last_seen"],
            })

        if len(offers) < min_offers:
            continue

        suspect_count = flag_quantity_outliers(offers)

        # Headline numbers come from comparable offers only. A suspect row can
        # still be the genuinely cheapest thing on the page, but claiming it as
        # "the best price for this product" would be false.
        comparable = [o for o in offers if not o["suspect"]]
        if not comparable:
            comparable = offers

        totals = [o["total"] for o in comparable]
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
            "new_from": min([o["total"] for o in comparable if o["condition"] == "new"],
                            default=None),
            "used_from": min([o["total"] for o in comparable if o["condition"] != "new"],
                             default=None),
            "retailers": len({o["retailer"] for o in offers}),
            "suspect_count": suspect_count,
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
