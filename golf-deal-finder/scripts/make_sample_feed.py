#!/usr/bin/env python3
"""
Generate data/sample_feed.json -- synthetic but realistically messy.

The point of this file is to exercise the matcher. Each canonical club below is
emitted once per retailer, and every retailer formats titles its own way: some
use degree symbols, some asterisks, some spell out "Right Handed", some prepend
a model year, one adds the stock shaft. If the matcher is working, all of those
collapse into ONE product with N prices.

Prices are deterministic (seeded) so test assertions stay stable.

Nothing here is real pricing. It is placeholder data for development only --
replace it with live feeds before you show anyone a number.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sample_feed.json"

RNG = random.Random(1957)  # deterministic output

# Retailers, the network that carries them, and their title style.
RETAILERS = [
    ("PGA TOUR Superstore", "impact", "dash", 6.0),
    ("Golf Galaxy", "cj", "parens", 5.0),
    ("TGW", "cj", "terse", 8.0),
    ("Rock Bottom Golf", "avantlink", "shouty", 9.0),
    ("Global Golf", "avantlink", "verbose", 7.0),
    ("Carl's Golfland", "avantlink", "shaft", 6.5),
]

# (brand, model, club_type, loft, flex, dex, set_comp, msrp, stock_shaft)
CLUBS = [
    ("TaylorMade", "Qi35", "Driver", "10.5", "Stiff", "RH", None, 629.99, "Ventus Blue"),
    ("TaylorMade", "Qi35", "Driver", "9", "Stiff", "RH", None, 629.99, "Ventus Blue"),
    ("TaylorMade", "Qi35", "Driver", "10.5", "Regular", "RH", None, 629.99, "Ventus Blue"),
    ("Callaway", "Elyte", "Driver", "9", "Stiff", "RH", None, 649.99, "Tensei AV"),
    ("Callaway", "Elyte", "Driver", "10.5", "Regular", "RH", None, 649.99, "Tensei AV"),
    ("Titleist", "GT2", "Driver", "10", "Regular", "RH", None, 649.00, "Denali"),
    ("Titleist", "GT3", "Driver", "9", "Stiff", "RH", None, 649.00, "Denali"),
    ("PING", "G440 Max", "Driver", "10.5", "Stiff", "RH", None, 599.00, "Alta CB"),
    ("PING", "G440 Max", "Driver", "10.5", "Stiff", "LH", None, 599.00, "Alta CB"),
    ("Cobra", "DS-Adapt Max", "Driver", "10.5", "Stiff", "RH", None, 549.00, "Hzrdus"),
    ("TaylorMade", "P790", "Irons", None, "Stiff", "RH", "4-PW", 1399.99, "Dynamic Gold"),
    ("Mizuno", "JPX925 Hot Metal", "Irons", None, "Regular", "RH", "5-PW", 1099.99, "Modus"),
    ("Srixon", "ZXi5", "Irons", None, "Stiff", "RH", "5-PW", 1199.99, "Modus"),
    ("PING", "i230", "Irons", None, "Regular", "RH", "4-PW", 1299.00, "Dynamic Gold"),
    ("Vokey", "SM10", "Wedge", "56", "Wedge", "RH", None, 189.00, "Dynamic Gold"),
    ("Vokey", "SM10", "Wedge", "60", "Wedge", "RH", None, 189.00, "Dynamic Gold"),
    ("Cleveland", "RTZ", "Wedge", "54", "Wedge", "RH", None, 169.99, "Dynamic Gold"),
    ("Scotty Cameron", "Phantom 11", "Putter", None, None, "RH", None, 499.00, None),
    ("Odyssey", "Ai-One Milled Seven T", "Putter", None, None, "RH", None, 449.99, None),
    ("Titleist", "Pro V1", "Golf Balls", None, None, None, None, 54.99, None),
    ("Titleist", "Pro V1x", "Golf Balls", None, None, None, None, 54.99, None),
    ("Callaway", "Chrome Soft", "Golf Balls", None, None, None, None, 49.99, None),
    ("Bridgestone", "Tour B XS", "Golf Balls", None, None, None, None, 49.99, None),
    ("TaylorMade", "TP5x", "Golf Balls", None, None, None, None, 54.99, None),
]

FLEX_SHORT = {"Stiff": "S", "Regular": "R", "X-Stiff": "X", "Senior": "A", "Ladies": "L"}


def title_for(style: str, club: tuple) -> str:
    brand, model, ctype, loft, flex, dex, comp, _msrp, shaft = club

    spec: list[str] = []
    if comp:
        spec.append(comp)
    if loft and ctype != "Wedge":
        spec.append(loft)
    if loft and ctype == "Wedge":
        spec.append(loft)
    if flex and flex != "Wedge":
        spec.append(flex)

    if style == "dash":
        bits = [f"{brand} {model} {ctype}"]
        tail = []
        if comp:
            tail.append(comp)
        if loft:
            tail.append(f"{loft}°")
        if flex and flex != "Wedge":
            tail.append(flex)
        if dex:
            tail.append("Right Hand" if dex == "RH" else "Left Hand")
        return f"{bits[0]} - " + " / ".join(tail) if tail else bits[0]

    if style == "parens":
        inner = []
        if dex:
            inner.append("Right Hand" if dex == "RH" else "Left Hand")
        if loft:
            inner.append(f"{loft} Degree")
        if comp:
            inner.append(comp)
        if flex and flex != "Wedge":
            inner.append(f"{flex} Flex")
        core = f"{brand} {model} {ctype}"
        return f"{core} ({', '.join(inner)})" if inner else core

    if style == "terse":
        bits = [brand, model, ctype]
        if comp:
            bits.append(comp)
        if loft:
            bits.append(loft)
        if flex and flex != "Wedge":
            bits.append(flex)
        if dex:
            bits.append(dex)
        return " ".join(bits)

    if style == "shouty":
        bits = ["2025", brand, model, "Men's", ctype]
        if comp:
            bits.append(comp)
        if loft:
            bits.append(f"{loft}*")
        if flex and flex != "Wedge":
            bits.append(f"{FLEX_SHORT.get(flex, flex)}-Flex")
        if dex:
            bits.append(dex)
        return " ".join(bits) + " - NEW!"

    if style == "verbose":
        bits = [f"{brand} Golf", model, ctype]
        if comp:
            bits.append(comp)
        if loft:
            bits.append(f"{loft} Degree")
        if flex and flex != "Wedge":
            bits.append(flex)
        if dex:
            bits.append("Right Handed" if dex == "RH" else "Left Handed")
        return " ".join(bits)

    if style == "shaft":
        # The awkward one: names the stock shaft even though it's the same SKU.
        core = f"{brand} {model} {ctype}"
        if shaft:
            core += f" w/ {shaft}"
        tail = []
        if comp:
            tail.append(comp)
        if loft:
            tail.append(loft)
        if flex and flex != "Wedge":
            tail.append(flex)
        if dex:
            tail.append(dex)
        return f"{core} {' '.join(tail)}".strip()

    raise ValueError(style)


def build() -> list[dict]:
    rows: list[dict] = []
    next_id = 1

    for club in CLUBS:
        brand, model, ctype, loft, flex, dex, comp, msrp, _shaft = club

        # Not every retailer carries every club -- 4 to 6 of the 6.
        carriers = RNG.sample(RETAILERS, RNG.randint(4, len(RETAILERS)))

        for retailer, source, style, commission in carriers:
            # Discount depth varies by retailer personality.
            depth = {
                "PGA TOUR Superstore": (0.00, 0.10),
                "Golf Galaxy": (0.00, 0.12),
                "TGW": (0.05, 0.22),
                "Rock Bottom Golf": (0.10, 0.35),
                "Global Golf": (0.05, 0.25),
                "Carl's Golfland": (0.00, 0.18),
            }[retailer]
            off = RNG.uniform(*depth)
            price = round(msrp * (1 - off), 2)
            price = round(price - 0.01 if price % 1 > 0.5 else price, 2)
            on_sale = off > 0.03

            rows.append({
                "id": next_id,
                "retailer": retailer,
                "network": source,
                "title": title_for(style, club),
                "brand": brand,
                "category": ctype,
                "price": price,
                "original_price": msrp if on_sale else None,
                "shipping": 0.0 if price > 75 else round(RNG.choice([0.0, 6.95, 9.95]), 2),
                "condition": "new",
                "availability": "in_stock",
                "url": (
                    f"https://example-affiliate-link.test/{source}"
                    f"/{retailer.lower().replace(' ', '-').replace(chr(39), '')}"
                    f"/{model.lower().replace(' ', '-')}?sku={next_id}"
                ),
                "image_url": None,
                "gtin": None,
                "mpn": f"{brand[:3].upper()}-{model.replace(' ', '')[:8].upper()}-{next_id}",
                "commission_rate": commission,
            })
            next_id += 1

    return rows


def main() -> None:
    rows = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2))
    retailers = {r["retailer"] for r in rows}
    print(f"wrote {len(rows)} offers across {len(retailers)} retailers -> {OUT}")


if __name__ == "__main__":
    main()
