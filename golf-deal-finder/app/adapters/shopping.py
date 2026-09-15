"""
Shopping-search adapter: prices from across the web, no partnership required.

Affiliate feeds only contain merchants who signed up. This adapter reaches
everyone else by querying a shopping-search API (which in turn reads Google
Shopping), so the compare table can show the real cheapest price rather than
the cheapest price among your partners.

Two things to understand before relying on it:

1. NO COMMISSION. These rows earn you nothing — you get a price and a link,
   not an affiliate relationship. Run it alongside the affiliate adapters, not
   instead of them. Offers carry `commission_rate=None` so you can always tell
   which rows pay.

2. LEGALLY GREY. These providers work by reading Google's results, which
   Google's terms prohibit. The industry operates openly and the law is
   unsettled, but it is a different risk profile from a sanctioned affiliate
   feed. Your call, made knowingly.

Providers are pluggable because pricing and terms shift constantly. Two are
implemented against documented, stable request shapes:

    GOLF_SHOPPING_PROVIDER=serpapi   SERPAPI_KEY=...
    GOLF_SHOPPING_PROVIDER=serper    SERPER_KEY=...

To add another, implement ShoppingProvider and register it in PROVIDERS. Run
`python -m app.ingest --probe shopping` first to see the raw response shape —
never trust a field name you haven't seen come back.

COST CONTROL: every query costs money, so responses are cached on disk
(GOLF_SHOPPING_CACHE_HOURS, default 24). Re-running ingest the same day spends
nothing. Delete data/cache/shopping/ to force a refresh.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Protocol

import httpx

from .. import config
from .base import Adapter, AdapterError, RawOffer

log = logging.getLogger(__name__)

CACHE_DIR = config.DATA_DIR / "cache" / "shopping"


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_PRICE_RE = re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)")


def parse_price(value) -> float | None:
    """
    '$549.99' -> 549.99, '$1,299' -> 1299.0, 'From $299.00' -> 299.0.

    Shopping results are wildly inconsistent about price format, and a string
    that fails to parse must return None rather than 0 — a zero would sort to
    the top of the compare table and look like a free driver.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2) if value > 0 else None
    m = _PRICE_RE.search(str(value).replace(" ", " "))
    if not m:
        return None
    try:
        price = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return round(price, 2) if price > 0 else None


def parse_shipping(value) -> float | None:
    """
    'Free delivery' -> 0.0, '$5.99 delivery' -> 5.99, anything else -> None.

    None means unknown, which is different from free. The compare table ranks
    on price + shipping, so guessing zero here would understate real totals.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().lower()
    if not text:
        return None
    if "free" in text:
        return 0.0
    return parse_price(text)


# Accessories that name a club without being one. "Qi35 Driver Headcover"
# contains "driver", so a keyword-only filter happily stores it as a $34
# driver and wrecks the compare table. Rejected before the keep-list runs.
_ACCESSORY_RE = re.compile(
    r"\b(head\s*cover|headcover|cover|towel|hat|cap|visor|umbrella|"
    r"grip|grips|ferrule|wrench|tool|weight\s*kit|shaft\s*adapter|sleeve|"
    r"alignment\s*stick|divot|tee[s]?|ball\s*marker|sticker|decal|"
    r"t-?shirt|polo|hoodie|jacket|pants|socks|keychain|poster)\b",
    re.I,
)

_EQUIPMENT_RE = re.compile(
    r"\b(driver|fairway|wood|hybrid|rescue|iron|irons|wedge|putter|"
    r"golf\s*balls?|balls?)\b",
    re.I,
)


def looks_like_golf(title: str) -> bool:
    """
    Shopping search for "TaylorMade Qi35" also returns headcovers, towels and
    phone cases. Keep rows that name real equipment and reject ones that name
    an accessory, checking the accessory list FIRST — most accessories also
    name the club they belong to.
    """
    if not title:
        return False
    if _ACCESSORY_RE.search(title):
        return False
    return bool(_EQUIPMENT_RE.search(title))


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def first_list(payload: dict, *names: str) -> list[dict]:
    """
    Find the results array whatever the provider calls it. Providers rename
    these between versions without warning, and a rename would otherwise look
    like "zero results" rather than an error.
    """
    for name in names:
        value = payload.get(name)
        if isinstance(value, list):
            return value
    # Last resort: the longest list of dicts anywhere at the top level.
    best: list[dict] = []
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if len(value) > len(best):
                best = value
    return best


def pick(row: dict, *names: str):
    """First non-empty value among several candidate key names."""
    for name in names:
        value = row.get(name)
        if value not in (None, "", []):
            return value
    return None


def normalize_row(r: dict) -> dict:
    """
    Map a provider row onto our shape, accepting the several names each field
    goes by across providers and versions. Being generous here is cheap;
    guessing one name and being wrong costs a whole run.
    """
    return {
        "title": pick(r, "title", "name", "productName"),
        "merchant": pick(r, "source", "merchant", "seller", "store", "sellerName"),
        "price": pick(r, "extracted_price", "priceValue", "price", "currentPrice"),
        "old_price": pick(r, "extracted_old_price", "originalPrice", "old_price",
                          "listPrice", "wasPrice"),
        "url": pick(r, "link", "productLink", "product_link", "url", "offerUrl"),
        "image": pick(r, "imageUrl", "thumbnail", "image", "imageURL"),
        "shipping": pick(r, "delivery", "deliveryPrice", "shipping",
                         "shippingPrice", "deliveryInfo"),
        "id": pick(r, "productId", "product_id", "id", "position"),
        "rating": pick(r, "rating", "reviewRating", "stars"),
        "rating_count": pick(r, "ratingCount", "reviews", "reviewCount"),
    }


class ShoppingProvider(Protocol):
    id: str

    def is_configured(self) -> bool: ...

    def search(self, client: httpx.Client, query: str, limit: int) -> list[dict]:
        """Return raw provider rows; normalization happens in the adapter."""
        ...


class SerpApiProvider:
    """
    SerpApi's Google Shopping engine.
    https://serpapi.com/google-shopping-api

    Returns `shopping_results`, each with title, source (the merchant),
    price / extracted_price, link, thumbnail, delivery.
    """

    id = "serpapi"
    URL = "https://serpapi.com/search.json"

    def is_configured(self) -> bool:
        return bool(config.SERPAPI_KEY)

    def search(self, client: httpx.Client, query: str, limit: int) -> list[dict]:
        resp = client.get(self.URL, params={
            "engine": "google_shopping",
            "q": query,
            "api_key": config.SERPAPI_KEY,
            "num": min(limit, 100),
            "gl": config.SHOPPING_COUNTRY,
            "hl": "en",
        })
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise AdapterError(f"serpapi: {payload['error']}")
        rows = first_list(payload, "shopping_results", "inline_shopping_results")
        return [normalize_row(r) for r in rows]


class SerperProvider:
    """
    Serper's shopping endpoint. https://serper.dev
    POST with an X-API-KEY header; returns a `shopping` array.
    """

    id = "serper"
    URL = "https://google.serper.dev/shopping"

    def is_configured(self) -> bool:
        return bool(config.SERPER_KEY)

    def search(self, client: httpx.Client, query: str, limit: int) -> list[dict]:
        resp = client.post(
            self.URL,
            headers={"X-API-KEY": config.SERPER_KEY or "", "Content-Type": "application/json"},
            json={"q": query, "gl": config.SHOPPING_COUNTRY, "num": min(limit, 100)},
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = first_list(payload, "shopping", "shoppingResults", "shopping_results")
        return [normalize_row(r) for r in rows]


PROVIDERS: dict[str, ShoppingProvider] = {
    p.id: p for p in (SerpApiProvider(), SerperProvider())
}


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def _cache_path(provider: str, query: str) -> Path:
    key = hashlib.sha256(f"{provider}|{query}|{config.SHOPPING_COUNTRY}".encode()).hexdigest()[:20]
    return CACHE_DIR / f"{provider}-{key}.json"


def cache_get(provider: str, query: str) -> list[dict] | None:
    path = _cache_path(provider, query)
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > config.SHOPPING_CACHE_HOURS:
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def cache_put(provider: str, query: str, rows: list[dict]) -> None:
    path = _cache_path(provider, query)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(rows))
    except OSError as exc:
        log.warning("could not cache %s: %s", query, exc)


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------

class ShoppingAdapter(Adapter):
    id = "shopping"
    name = "Shopping search (web-wide)"

    def __init__(self) -> None:
        self.provider = PROVIDERS.get(config.SHOPPING_PROVIDER or "")

    def is_configured(self) -> bool:
        return bool(self.provider and self.provider.is_configured())

    def seed_queries(self) -> list[str]:
        """
        Shopping APIs are search-driven, not bulk feeds — you can't ask for
        "all golf clubs". So we drive them from a seed list: the clubs worth
        having prices for. Every query costs money, so this list is the single
        biggest lever on your bill. Start narrow.
        """
        path = config.DATA_DIR / "seed_queries.txt"
        if not path.exists():
            raise AdapterError(
                f"{path} not found — it lists the clubs to price. "
                "See data/seed_queries.txt in the repo."
            )
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
        return out

    def fetch(self, query: str | None = None, limit: int = 500) -> list[RawOffer]:
        if not self.is_configured():
            raise AdapterError(
                "Shopping search not configured. Set GOLF_SHOPPING_PROVIDER "
                f"(one of: {', '.join(PROVIDERS)}) and that provider's API key."
            )
        assert self.provider is not None

        queries = [query] if query else self.seed_queries()
        offers: list[RawOffer] = []
        seen: set[str] = set()
        spent = cached = 0

        with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
            for q in queries:
                if len(offers) >= limit:
                    break

                rows = cache_get(self.provider.id, q)
                if rows is None:
                    try:
                        rows = self.provider.search(client, q, config.SHOPPING_PER_QUERY)
                        cache_put(self.provider.id, q, rows)
                        spent += 1
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in (401, 403):
                            raise AdapterError(
                                f"{self.provider.id}: auth rejected — check your API key"
                            ) from exc
                        if exc.response.status_code == 429:
                            log.warning("%s rate limited; stopping early", self.provider.id)
                            break
                        log.warning("query %r failed: %s", q, exc)
                        continue
                    except httpx.HTTPError as exc:
                        log.warning("query %r failed: %s", q, exc)
                        continue
                else:
                    cached += 1

                for row in rows:
                    offer = self._to_offer(row, q)
                    if offer and offer.is_sellable() and offer.external_id not in seen:
                        seen.add(offer.external_id)
                        offers.append(offer)

        log.info(
            "shopping(%s): %d offers from %d queries (%d billed, %d cached)",
            self.provider.id, len(offers), len(queries), spent, cached,
        )
        return offers[:limit]

    def _to_offer(self, row: dict, query: str) -> RawOffer | None:
        title = (row.get("title") or "").strip()
        url = row.get("url")
        price = parse_price(row.get("price"))
        merchant = (row.get("merchant") or "").strip()

        if not (title and url and price and merchant):
            return None
        if not looks_like_golf(title):
            return None

        old_price = parse_price(row.get("old_price"))
        return RawOffer(
            source=self.id,
            retailer=merchant,
            # Merchant + title keys the row: shopping APIs reshuffle their own
            # ids between calls, so using theirs would duplicate every run.
            external_id="shop:" + hashlib.sha256(
                f"{merchant}|{title}|{url}".encode()
            ).hexdigest()[:24],
            title=title,
            price=price,
            original_price=old_price if old_price and old_price > price else None,
            url=url,
            shipping=parse_shipping(row.get("shipping")),
            image_url=row.get("image"),
            availability="in_stock",
            # None, not zero: these rows pay nothing, and the difference
            # matters when you look at what the site actually earns.
            commission_rate=None,
            rating=_num(row.get("rating")),
            rating_count=_int(row.get("rating_count")),
        )


def _num(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
