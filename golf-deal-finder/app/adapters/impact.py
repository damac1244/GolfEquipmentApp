"""
Impact adapter.

Impact hosts PGA TOUR Superstore and TaylorMade among others, so it's the
first program worth applying to for a new-gear comparison app.

Auth and base URL are confirmed from Impact's API quick start: HTTP Basic with
your AccountSid as username and AuthToken as password, against
https://api.impact.com, with partner endpoints under /Mediapartners/{AccountSid}/.

Docs: https://integrations.impact.com/rest-apis/api-quick-start

Impact versions its Catalogs endpoints and has migrated them more than once,
so the item field names are the ones Impact documents today -- check your
account's actual response shape on first run. `python -m app.ingest --probe
impact` prints the first raw item so you can see exactly what you're getting.
"""

from __future__ import annotations

import logging

import httpx

from .. import config
from .base import Adapter, AdapterError, RawOffer

log = logging.getLogger(__name__)

BASE_URL = "https://api.impact.com"


class ImpactAdapter(Adapter):
    id = "impact"
    name = "Impact"

    def is_configured(self) -> bool:
        return bool(config.IMPACT_ACCOUNT_SID and config.IMPACT_AUTH_TOKEN)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            auth=(config.IMPACT_ACCOUNT_SID or "", config.IMPACT_AUTH_TOKEN or ""),
            headers={"Accept": "application/json"},
            timeout=config.HTTP_TIMEOUT,
        )

    def list_catalogs(self) -> list[dict]:
        """Catalogs you have access to. Run this first to find your catalog ids."""
        with self._client() as client:
            path = f"/Mediapartners/{config.IMPACT_ACCOUNT_SID}/Catalogs"
            resp = client.get(path, params={"PageSize": 100})
            resp.raise_for_status()
            return resp.json().get("Catalogs", [])

    def fetch(self, query: str | None = None, limit: int = 500) -> list[RawOffer]:
        if not self.is_configured():
            raise AdapterError("Impact credentials missing; see .env.example")

        catalog_ids = [c.strip() for c in config.IMPACT_CATALOG_IDS.split(",") if c.strip()]
        offers: list[RawOffer] = []

        with self._client() as client:
            if not catalog_ids:
                try:
                    resp = client.get(
                        f"/Mediapartners/{config.IMPACT_ACCOUNT_SID}/Catalogs",
                        params={"PageSize": 100},
                    )
                    resp.raise_for_status()
                    catalog_ids = [
                        str(c.get("Id")) for c in resp.json().get("Catalogs", [])
                    ]
                except httpx.HTTPError as exc:
                    raise AdapterError(f"Impact catalog list failed: {exc}") from exc

            for catalog_id in catalog_ids:
                if len(offers) >= limit:
                    break
                offers.extend(
                    self._fetch_catalog(client, catalog_id, query, limit - len(offers))
                )

        log.info("impact: %d offers across %d catalogs", len(offers), len(catalog_ids))
        return offers[:limit]

    def _fetch_catalog(
        self, client: httpx.Client, catalog_id: str, query: str | None, limit: int
    ) -> list[RawOffer]:
        path = f"/Mediapartners/{config.IMPACT_ACCOUNT_SID}/Catalogs/{catalog_id}/Items"
        out: list[RawOffer] = []
        page = 1

        while len(out) < limit and page <= 20:  # hard page cap; feeds are huge
            params: dict[str, object] = {"Page": page, "PageSize": 100}
            if query:
                params["Query"] = query
            try:
                resp = client.get(path, params=params)
                if resp.status_code == 429:
                    raise AdapterError("Impact rate limited; back off and retry")
                resp.raise_for_status()
                payload = resp.json()
            except httpx.HTTPError as exc:
                log.warning("impact catalog %s page %s failed: %s", catalog_id, page, exc)
                break

            items = payload.get("Items") or payload.get("CatalogItems") or []
            if not items:
                break

            for item in items:
                offer = self._to_offer(item, catalog_id)
                if offer and offer.is_sellable():
                    out.append(offer)

            if not payload.get("@nextpageuri") and len(items) < 100:
                break
            page += 1

        return out

    def _to_offer(self, item: dict, catalog_id: str) -> RawOffer | None:
        def g(*names: str):
            for n in names:
                v = item.get(n)
                if v not in (None, ""):
                    return v
            return None

        title = g("Name", "ProductName", "Title")
        url = g("Url", "ProductUrl", "TrackingUrl", "Link")
        price = _money(g("CurrentPrice", "Price", "SalePrice"))
        if not (title and url and price):
            return None

        original = _money(g("OriginalPrice", "ListPrice", "RetailPrice"))
        return RawOffer(
            source=self.id,
            retailer=str(g("CampaignName", "AdvertiserName", "Merchant") or "Unknown"),
            external_id=f"im:{catalog_id}:{g('Id', 'CatalogItemId', 'Sku') or url}",
            title=str(title),
            price=price,
            original_price=original if original and original > price else None,
            url=str(url),
            currency=str(g("Currency") or "USD"),
            brand=_str(g("Manufacturer", "Brand")),
            category=_str(g("Category", "ProductType")),
            condition=_str(g("Condition")),
            availability=_str(g("StockAvailability", "Availability")),
            image_url=_str(g("ImageUrl", "Image")),
            gtin=_str(g("Gtin", "Upc", "Ean")),
            mpn=_str(g("Mpn", "ManufacturerPartNumber")),
        )


def _money(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def _str(value) -> str | None:
    return str(value) if value not in (None, "") else None
