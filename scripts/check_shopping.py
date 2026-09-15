#!/usr/bin/env python3
"""
One query against your shopping provider, showing exactly what came back.

Run this FIRST, before a full ingest. It spends a single API call and answers
the two questions that matter:

  1. Do the credentials work?
  2. Does the provider's response match what the adapter expects?

Usage:
    export GOLF_SHOPPING_PROVIDER=serper
    export SERPER_KEY=your_key
    python scripts/check_shopping.py
    python scripts/check_shopping.py "Callaway Elyte driver"

If anything looks wrong, the raw JSON is saved to shopping-response.json —
send that file and the mismatch can be fixed without guessing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import config  # noqa: E402
from app.adapters.shopping import PROVIDERS, ShoppingAdapter  # noqa: E402
from app.matching import parse  # noqa: E402

QUERY = sys.argv[1] if len(sys.argv) > 1 else "TaylorMade Qi35 driver"
OUT = Path("shopping-response.json")
RAW_OUT = Path("shopping-raw.json")


def fail(msg: str) -> None:
    print(f"\n  ✗ {msg}\n")
    raise SystemExit(1)


def main() -> None:
    print(f"\nProvider : {config.SHOPPING_PROVIDER or '(not set)'}")
    print(f"Query    : {QUERY}\n")

    if not config.SHOPPING_PROVIDER:
        fail("GOLF_SHOPPING_PROVIDER is not set. "
             f"Choose one of: {', '.join(PROVIDERS)}")

    provider = PROVIDERS.get(config.SHOPPING_PROVIDER)
    if not provider:
        fail(f"Unknown provider {config.SHOPPING_PROVIDER!r}. "
             f"Choose one of: {', '.join(PROVIDERS)}")
    if not provider.is_configured():
        fail(f"No API key found for {provider.id}. Set its key and try again.")

    # --- raw call ---------------------------------------------------------
    # The RAW payload is saved before any of our field mapping touches it.
    # That is the only way to see what the provider actually calls things, and
    # the only way to tell whether a field we care about (a direct merchant
    # link, a condition flag) exists under a name we are not reading.
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
            if provider.id == "serper":
                raw = client.post(
                    "https://google.serper.dev/shopping",
                    headers={"X-API-KEY": config.SERPER_KEY or "",
                             "Content-Type": "application/json"},
                    json={"q": QUERY, "gl": config.SHOPPING_COUNTRY, "num": 20},
                )
            else:
                raw = client.get("https://serpapi.com/search.json", params={
                    "engine": "google_shopping", "q": QUERY,
                    "api_key": config.SERPAPI_KEY, "num": 20,
                    "gl": config.SHOPPING_COUNTRY, "hl": "en",
                })
            raw.raise_for_status()
            RAW_OUT.write_text(json.dumps(raw.json(), indent=2)[:400000])
            print(f"Raw provider response saved to {RAW_OUT}")

            rows = provider.search(client, QUERY, 20)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = {
            401: "the API key was rejected — check it was copied in full",
            403: "the key is valid but not permitted for this endpoint",
            429: "rate limited or out of quota",
        }.get(code, "see the response body below")
        print(exc.response.text[:600])
        fail(f"HTTP {code}: {hint}")
    except httpx.HTTPError as exc:
        fail(f"Could not reach the provider: {exc}")

    OUT.write_text(json.dumps(rows, indent=2)[:200000])
    print(f"{len(rows)} results returned. Raw response saved to {OUT}\n")

    if not rows:
        fail("The provider returned nothing. Either the query found no "
             "shopping results, or the response shape has changed — check "
             f"{OUT}.")

    # --- did our field mapping work? --------------------------------------
    missing = [f for f in ("title", "merchant", "price", "url")
               if not rows[0].get(f)]
    if missing:
        print("  ⚠  These fields came back empty, so the provider likely")
        print(f"     renamed them: {', '.join(missing)}")
        print(f"     Send {OUT} and the mapping can be corrected.\n")
    else:
        print("  ✓ Field mapping looks right.\n")

    # --- what the engine makes of it --------------------------------------
    adapter = ShoppingAdapter()
    offers = [o for o in (adapter._to_offer(r, QUERY) for r in rows) if o]

    print(f"{len(offers)} of {len(rows)} rows are usable golf equipment.")
    if len(rows) - len(offers):
        print(f"({len(rows) - len(offers)} dropped: accessories, or missing "
              "a price, seller or link.)")

    if not offers:
        fail("Nothing usable. Check the raw file — the results may be "
             "accessories rather than clubs.")

    print(f"\n{'SELLER':<26}{'CONDITION':<11}{'GRADE':<11}{'TOTAL':>10}   TITLE")
    print("-" * 100)
    for o in sorted(offers, key=lambda x: x.price + (x.shipping or 0))[:15]:
        p = parse(o.title, brand=o.brand)
        total = o.price + (o.shipping or 0)
        ship = "" if o.shipping is not None else " ?"
        print(f"{o.retailer[:25]:<26}{p.condition:<11}{(p.grade or '-'):<11}"
              f"${total:>8.2f}{ship:<2} {o.title[:44]}")

    # --- does it collapse into one product? -------------------------------
    from app.matching import ProductIndex

    index = ProductIndex()
    keys = {index.assign(parse(o.title, brand=o.brand)) for o in offers}
    print(f"\nMatched into {len(keys)} distinct product(s).")
    if len(keys) == 1:
        print("  ✓ Every listing collapsed into one comparable product.")
    else:
        print("  These are genuinely different specs (loft, flex, dexterity),")
        print("  or the matcher is splitting them. Worth a look:")
        for k in sorted(keys)[:12]:
            print(f"    {k}")
    print()


if __name__ == "__main__":
    main()
