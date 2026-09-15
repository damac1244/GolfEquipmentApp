"""
AvantLink ProductSearch adapter.

AvantLink is the network most worth having for this app: it carries a lot of
the independent golf specialists (the ones that actually discount) rather than
just the big-box chains. Its API is plain HTTP GET with credentials in the
query string, and it can emit JSON directly.

Docs: https://support.avantlink.com/hc/en-us/articles/203644699-Affiliate-API-Technical-Integration
Full module list: http://classic.avantlink.com/api.php?help=1

Field names below match AvantLink's standard ProductSearch output. Confirm
against your own account's datafeed schema once approved -- merchants can add
custom columns, and a few use different casing.
"""

from __future__ import annotations

import logging

import httpx

from .. import config
from .base import Adapter, AdapterError, RawOffer

log = logging.getLogger(__name__)

API_URL = "https://classic.avantlink.com/api.php"


class AvantLinkAdapter(Adapter):
    id = "avantlink"
    name = "AvantLink"

    def is_configured(self) -> bool:
        return bool(
            config.AVANTLINK_AFFILIATE_ID
            and config.AVANTLINK_WEBSITE_ID
            and config.AVANTLINK_AUTH_KEY
        )

    def fetch(self, query: str | None = None, limit: int = 500) -> list[RawOffer]:
        if not self.is_configured():
            raise AdapterError("AvantLink credentials missing; see .env.example")

        terms = [query] if query else config.GOLF_KEYWORDS
        offers: list[RawOffer] = []
        seen: set[str] = set()

        with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
            for term in terms:
                if len(offers) >= limit:
                    break
                params = {
                    "module": "ProductSearch",
                    "affiliate_id": config.AVANTLINK_AFFILIATE_ID,
                    "website_id": config.AVANTLINK_WEBSITE_ID,
                    "auth_key": config.AVANTLINK_AUTH_KEY,
                    "search_term": term,
                    "search_results_count": min(200, limit),
                    "output": "json",
                }
                if config.AVANTLINK_MERCHANT_IDS:
                    params["merchant_ids"] = config.AVANTLINK_MERCHANT_IDS

                try:
                    resp = client.get(API_URL, params=params)
                    resp.raise_for_status()
                    rows = resp.json()
                except httpx.HTTPError as exc:
                    raise AdapterError(f"AvantLink request failed: {exc}") from exc
                except ValueError as exc:
                    # AvantLink returns an HTML error page on bad credentials
                    # rather than a JSON error object.
                    raise AdapterError(
                        "AvantLink returned non-JSON; usually bad credentials"
                    ) from exc

                if isinstance(rows, dict):
                    rows = rows.get("results") or rows.get("Products") or []

                for row in rows:
                    offer = self._to_offer(row)
                    if offer and offer.is_sellable() and offer.external_id not in seen:
                        seen.add(offer.external_id)
                        offers.append(offer)

        log.info("avantlink: %d offers", len(offers))
        return offers[:limit]

    def _to_offer(self, row: dict) -> RawOffer | None:
        def g(*names: str) -> str | None:
            for n in names:
                v = row.get(n)
                if v not in (None, "", "0.00"):
                    return str(v)
            return None

        title = g("Product Name", "product_name", "lngProductName")
        url = g("Buy URL", "buy_url", "strBuyURL")
        price_raw = g("Retail Price", "retail_price", "dblProductPrice")
        sale_raw = g("Sale Price", "sale_price", "dblProductSalePrice")
        if not (title and url and (price_raw or sale_raw)):
            return None

        list_price = _money(price_raw)
        sale_price = _money(sale_raw)
        # AvantLink sends sale price only when one is active; when it is, that's
        # what the shopper pays and the retail price is the strike-through.
        price = sale_price if sale_price and sale_price > 0 else list_price
        if price is None:
            return None

        return RawOffer(
            source=self.id,
            retailer=g("Merchant Name", "merchant_name") or "Unknown",
            external_id=f"al:{g('Product SKU', 'product_sku') or url}",
            title=title,
            price=price,
            original_price=list_price if sale_price and list_price else None,
            url=url,
            brand=g("Brand Name", "brand_name", "Manufacturer"),
            category=g("Category Name", "category_name", "Subcategory Name"),
            image_url=g("Medium Image", "Thumbnail Image", "Large Image"),
            gtin=g("UPC", "upc", "EAN"),
            mpn=g("Manufacturer Id", "MPN", "Part Number"),
            availability=g("Stock Availability", "In Stock"),
            commission_rate=_pct(g("Commission Rate", "commission_rate")),
        )


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def _pct(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except ValueError:
        return None
