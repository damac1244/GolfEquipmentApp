"""
Sample adapter -- reads data/sample_feed.json.

Exists so the app runs the moment you clone it, before any affiliate
application is approved. The feed is synthetic but shaped like the real thing:
the same club appears under several retailers with genuinely different titles,
which is what makes the matcher's job visible.

Delete this adapter once your real programs are live, or keep it around as a
fixture for the matching tests.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import config
from .base import Adapter, AdapterError, RawOffer

log = logging.getLogger(__name__)

FEED_PATH = config.DATA_DIR / "sample_feed.json"


class SampleAdapter(Adapter):
    id = "sample"
    name = "Sample feed (synthetic)"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or FEED_PATH

    def is_configured(self) -> bool:
        return self.path.exists()

    def fetch(self, query: str | None = None, limit: int = 500) -> list[RawOffer]:
        if not self.is_configured():
            raise AdapterError(
                f"{self.path} not found; run: python scripts/make_sample_feed.py"
            )

        rows = json.loads(self.path.read_text())
        offers: list[RawOffer] = []

        for row in rows:
            if query and query.lower() not in row["title"].lower():
                continue
            offer = RawOffer(
                source=self.id,
                retailer=row["retailer"],
                external_id=f"sample:{row['id']}",
                title=row["title"],
                price=float(row["price"]),
                original_price=(
                    float(row["original_price"]) if row.get("original_price") else None
                ),
                url=row["url"],
                shipping=row.get("shipping"),
                brand=row.get("brand"),
                category=row.get("category"),
                condition=row.get("condition"),
                availability=row.get("availability", "in_stock"),
                image_url=row.get("image_url"),
                gtin=row.get("gtin"),
                mpn=row.get("mpn"),
                commission_rate=row.get("commission_rate"),
            )
            if offer.is_sellable():
                offers.append(offer)
            if len(offers) >= limit:
                break

        log.info("sample: %d offers", len(offers))
        return offers
