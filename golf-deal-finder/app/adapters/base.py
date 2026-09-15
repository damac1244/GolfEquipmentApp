"""
The adapter contract.

Every data source -- an affiliate network API, a CSV datafeed, a local sample
file -- reduces to the same job: hand back a list of RawOffer. Everything
downstream (matching, dedupe, pricing, the API, the UI) is source-agnostic.

That's the whole point of the layer. When you get approved for a new network,
you write one file here and change nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class RawOffer:
    """One retailer's listing of one product, before any normalization."""

    # --- required ---
    source: str                  # adapter id, e.g. "impact"
    retailer: str                # human name, e.g. "PGA TOUR Superstore"
    external_id: str             # the source's own id; unique within a source
    title: str                   # raw listing title, warts and all
    price: float                 # current selling price
    url: str                     # affiliate-tracked destination

    # --- optional but valuable ---
    currency: str = "USD"
    original_price: float | None = None   # list/MSRP if the feed gives one
    shipping: float | None = None         # None = unknown, 0.0 = free
    brand: str | None = None
    category: str | None = None
    condition: str | None = None
    availability: str | None = None       # in_stock / out_of_stock / preorder
    image_url: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    commission_rate: float | None = None  # your cut, for deal ranking/reporting
    # Seller trust signals. Shopping search returns these for free and they
    # matter: the cheapest price from a seller nobody has heard of is a
    # different proposition from the cheapest at 2nd Swing.
    rating: float | None = None
    rating_count: int | None = None
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def is_sellable(self) -> bool:
        """Filter out the junk rows every feed contains."""
        if self.price is None or self.price <= 0:
            return False
        if not self.title or not self.url:
            return False
        if self.availability and self.availability.lower() in {
            "out_of_stock", "outofstock", "unavailable", "discontinued", "0"
        }:
            return False
        return True


class Adapter(Protocol):
    """What every source must implement."""

    id: str
    name: str

    def is_configured(self) -> bool:
        """True when credentials are present and this source can actually run."""
        ...

    def fetch(self, query: str | None = None, limit: int = 500) -> list[RawOffer]:
        """
        Pull offers. `query` narrows the pull where the source supports search;
        sources that only offer bulk datafeeds ignore it and filter locally.
        """
        ...


class AdapterError(RuntimeError):
    """Raised for source-level failures. Ingest logs and continues to the next
    adapter -- one dead network must never take down the whole refresh."""
