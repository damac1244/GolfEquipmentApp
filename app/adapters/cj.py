"""
CJ Affiliate (Commission Junction) adapter.

CJ replaced their old REST product-search service with a GraphQL API. One POST,
one query, cursor-free offset paging.

Docs: https://developers.cj.com/graphql/reference/Product%20Search

Auth is a personal access token from the CJ developer portal, sent as a bearer
token. `companyId` is your CJ publisher company id; `partnerIds` are the
advertiser (CID) ids of the golf retailers you've been approved for -- leave it
empty and you get every advertiser you're joined to, which is usually far more
than you want to ingest.
"""

from __future__ import annotations

import logging

import httpx

from .. import config
from .base import Adapter, AdapterError, RawOffer

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://ads.api.cj.com/query"

PRODUCT_QUERY = """
query ProductSearch(
  $companyId: ID!
  $partnerIds: [ID!]
  $keywords: [String!]
  $limit: Int!
  $offset: Int!
) {
  products(
    companyId: $companyId
    partnerIds: $partnerIds
    keywords: $keywords
    limit: $limit
    offset: $offset
  ) {
    totalCount
    count
    resultList {
      id
      advertiserId
      advertiserName
      title
      brand
      description
      link
      imageLink
      price { amount currency }
      salePrice { amount currency }
      availability
      condition
      gtin
      mpn
      productType
      googleProductCategory { name }
    }
  }
}
"""


class CJAdapter(Adapter):
    id = "cj"
    name = "CJ Affiliate"

    def is_configured(self) -> bool:
        return bool(config.CJ_PERSONAL_ACCESS_TOKEN and config.CJ_COMPANY_ID)

    def fetch(self, query: str | None = None, limit: int = 500) -> list[RawOffer]:
        if not self.is_configured():
            raise AdapterError("CJ credentials missing; see .env.example")

        keywords = [query] if query else config.GOLF_KEYWORDS
        partner_ids = [p.strip() for p in config.CJ_PARTNER_IDS.split(",") if p.strip()]

        offers: list[RawOffer] = []
        seen: set[str] = set()
        headers = {
            "Authorization": f"Bearer {config.CJ_PERSONAL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
            for keyword in keywords:
                offset = 0
                while len(offers) < limit:
                    variables = {
                        "companyId": config.CJ_COMPANY_ID,
                        "partnerIds": partner_ids or None,
                        "keywords": [keyword],
                        "limit": min(100, limit - len(offers)),
                        "offset": offset,
                    }
                    try:
                        resp = client.post(
                            GRAPHQL_URL,
                            headers=headers,
                            json={"query": PRODUCT_QUERY, "variables": variables},
                        )
                        resp.raise_for_status()
                        payload = resp.json()
                    except httpx.HTTPError as exc:
                        raise AdapterError(f"CJ request failed: {exc}") from exc

                    # GraphQL returns HTTP 200 with an errors array on failure,
                    # so raise_for_status alone is not enough.
                    if payload.get("errors"):
                        raise AdapterError(f"CJ GraphQL error: {payload['errors']}")

                    block = (payload.get("data") or {}).get("products") or {}
                    rows = block.get("resultList") or []
                    if not rows:
                        break

                    for row in rows:
                        offer = self._to_offer(row)
                        if offer and offer.is_sellable() and offer.external_id not in seen:
                            seen.add(offer.external_id)
                            offers.append(offer)

                    offset += len(rows)
                    if offset >= int(block.get("totalCount") or 0):
                        break

        log.info("cj: %d offers", len(offers))
        return offers[:limit]

    def _to_offer(self, row: dict) -> RawOffer | None:
        title = row.get("title")
        url = row.get("link")
        if not (title and url):
            return None

        list_price = _amount(row.get("price"))
        sale_price = _amount(row.get("salePrice"))
        price = sale_price if sale_price and sale_price > 0 else list_price
        if not price:
            return None

        category = row.get("productType") or (
            (row.get("googleProductCategory") or {}).get("name")
        )
        return RawOffer(
            source=self.id,
            retailer=row.get("advertiserName") or "Unknown",
            external_id=f"cj:{row.get('advertiserId')}:{row.get('id') or url}",
            title=title,
            price=price,
            original_price=list_price if sale_price and list_price else None,
            url=url,
            currency=(row.get("price") or {}).get("currency") or "USD",
            brand=row.get("brand"),
            category=category,
            condition=row.get("condition"),
            availability=row.get("availability"),
            image_url=row.get("imageLink"),
            gtin=row.get("gtin"),
            mpn=row.get("mpn"),
        )


def _amount(block) -> float | None:
    if not block:
        return None
    try:
        return round(float(block.get("amount")), 2)
    except (TypeError, ValueError):
        return None
