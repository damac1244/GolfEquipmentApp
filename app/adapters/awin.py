"""
Awin adapter — product data feeds.

Awin is where 2nd Swing runs its programme, and 2nd Swing is the largest
dedicated used-club seller in golf. That matters more than the commission
rate: used listings are a rounding error in the shopping-search data, and the
site's whole premise is new and used side by side.

It also fixes the thing that has been wrong since the first run. Shopping
search gives a price and a Google link; an Awin feed gives `aw_deep_link`, a
real tracked URL to the seller's own product page. Those rows earn, and more
importantly they work.

HOW THE FEED IS REACHED
-----------------------
Awin publishes a list of every feed you have access to:

    https://productdata.awin.com/datafeed/list/apikey/<KEY>

That CSV carries one row per advertiser feed, including a ready-made download
URL with the columns already mapped. The adapter reads the list, picks the
advertisers it was asked for, and downloads those URLs — rather than trying to
assemble a datafeed URL by hand. Awin has changed that URL's shape before, and
the list is the documented way to avoid caring.

    AWIN_API_KEY=...            the datafeed key, from Create-a-Feed
    AWIN_ADVERTISERS=2nd Swing  comma-separated, matched loosely by name
    AWIN_FEED_IDS=12345         optional: exact feed ids, wins over the names

Docs: https://help.awin.com/developers/docs/product-feed-list-download

ON COLUMN NAMES
---------------
Feeds are per-advertiser and the column set depends on what that advertiser
mapped, so every field is looked up across several plausible names rather than
one. This is the same lesson the shopping adapter taught: guessing one name and
being wrong costs a whole run and is invisible until someone reads the output.

Before trusting a scheduled run, look at what actually arrives:

    python -m app.ingest --probe awin
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import zlib

import httpx

from .. import config
from .base import Adapter, AdapterError, RawOffer

log = logging.getLogger(__name__)

LIST_URL = "https://productdata.awin.com/datafeed/list/apikey/{key}"

# Feed rows are wide and we want a handful of fields. Each is tried in order.
FIELDS: dict[str, tuple[str, ...]] = {
    "title": ("product_name", "product_title", "name", "title"),
    "price": ("search_price", "price", "store_price", "display_price"),
    "was": ("rrp_price", "rrp", "was_price", "base_price"),
    "url": ("aw_deep_link", "deep_link", "awDeepLink", "merchant_deep_link"),
    "image": ("merchant_image_url", "aw_image_url", "image_url", "large_image"),
    "brand": ("brand_name", "brand", "manufacturer"),
    "category": ("merchant_category", "category_name", "product_type"),
    "condition": ("condition", "product_condition", "item_condition"),
    "stock": ("in_stock", "stock_status", "availability", "is_available"),
    "shipping": ("delivery_cost", "shipping_cost", "delivery_price"),
    "currency": ("currency", "curr"),
    "merchant": ("merchant_name", "advertiser_name", "programme_name"),
    "pid": ("merchant_product_id", "aw_product_id", "product_id", "sku"),
    "gtin": ("ean", "product_gtin", "gtin", "upc"),
    "mpn": ("mpn", "model_number", "part_number"),
}


def lower_keys(row: dict) -> dict:
    """
    Column case is the advertiser's choice. The feed list uses "Advertiser
    Name", product feeds use "product_name", and some advertisers ship
    "Product_Name". Normalise once per row rather than guessing per lookup.
    """
    return {str(k).strip().lower(): v for k, v in row.items()}


def pick(row: dict, names: tuple[str, ...]) -> str | None:
    """First non-empty value among candidate column names, case-insensitively."""
    low = lower_keys(row)
    for name in names:
        value = low.get(name.lower())
        if value not in (None, "", "0.00", "NULL"):
            return str(value).strip()
    return None


def to_float(value: str | None) -> float | None:
    """'129.99' -> 129.99, '£129.99' -> 129.99, '' -> None."""
    if not value:
        return None
    cleaned = "".join(c for c in str(value) if c.isdigit() or c in ".-")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if number > 0 else None


def decompress(body: bytes) -> str:
    """Feeds arrive gzipped, zlib-wrapped or plain depending on the URL."""
    if body[:2] == b"\x1f\x8b":
        return gzip.decompress(body).decode("utf-8", "replace")
    try:
        return zlib.decompress(body).decode("utf-8", "replace")
    except zlib.error:
        return body.decode("utf-8", "replace")


def normalize_condition(raw: str | None) -> str | None:
    """
    Map a feed's condition wording onto ours. 2nd Swing grades used clubs, so
    this is the field that finally makes "used" mean something on the site.
    """
    if not raw:
        return None
    low = raw.strip().lower()
    if low in {"new", "brand new", "1", "new_without_tags"}:
        return "new"
    if "refurb" in low:
        return "refurbished"
    if "open" in low and "box" in low:
        return "open_box"
    if any(word in low for word in ("used", "pre-owned", "preowned", "second")):
        return "used"
    # Anything else is the advertiser's own grade wording ("Very Good"), which
    # the matcher reads separately. Treat it as used rather than guessing new:
    # calling a used club new is the more damaging mistake.
    return "used"


class AwinAdapter(Adapter):
    id = "awin"
    name = "Awin"

    def is_configured(self) -> bool:
        return bool(config.AWIN_API_KEY or config.AWIN_FEED_LIST_URL)

    def _list_url(self) -> str:
        """
        Prefer the URL Awin itself printed. Falling back to building one is
        fine, but it is a guess about a host that has moved before, and the
        account page is not a guess.
        """
        if config.AWIN_FEED_LIST_URL:
            return config.AWIN_FEED_LIST_URL.strip()
        return LIST_URL.format(key=config.AWIN_API_KEY)

    def _wanted(self, rows: list[dict]) -> list[dict]:
        """The feeds this account is joined to and was asked for."""
        feed_ids = {f.strip() for f in config.AWIN_FEED_IDS.split(",") if f.strip()}
        names = [n.strip().lower() for n in config.AWIN_ADVERTISERS.split(",") if n.strip()]

        chosen = []
        for row in rows:
            fid = pick(row, ("Feed ID", "feed_id", "fid")) or ""
            advertiser = (pick(row, ("Advertiser Name", "advertiser_name")) or "").lower()
            joined = (pick(row, ("Membership Status", "membership_status")) or "").lower()

            if feed_ids:
                if fid in feed_ids:
                    chosen.append(row)
                continue
            # Without an explicit id list, only take feeds we are actually
            # joined to — the list includes programmes you could apply to.
            if joined and joined not in {"joined", "active", "yes", "1"}:
                continue
            if names and not any(n in advertiser for n in names):
                continue
            chosen.append(row)
        return chosen

    def fetch(self, query: str | None = None, limit: int = 5000) -> list[RawOffer]:
        if not self.is_configured():
            raise AdapterError(
                "Awin not configured. Set AWIN_FEED_LIST_URL to the Feed List "
                "Download URL shown in Create-a-Feed, or AWIN_API_KEY to the "
                "key inside it."
            )

        offers: list[RawOffer] = []
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            feeds = self._feed_list(client)
            wanted = self._wanted(feeds)
            if not wanted:
                raise AdapterError(
                    "Awin returned no feeds matching AWIN_ADVERTISERS="
                    f"{config.AWIN_ADVERTISERS!r}. The list showed "
                    f"{len(feeds)} feed(s); check the advertiser name and that "
                    "the programme shows as joined."
                )

            for feed in wanted:
                advertiser = pick(feed, ("Advertiser Name", "advertiser_name")) or "Unknown"
                url = pick(feed, ("URL", "url", "download_url"))
                if not url:
                    log.error(
                        "awin: the feed row for %s has no download URL. Columns "
                        "present: %s", advertiser, list(feed.keys()),
                    )
                    continue
                offers.extend(self._feed_rows(client, url, advertiser, limit - len(offers)))
                if len(offers) >= limit:
                    break

        log.info("awin: %d offers from %d feed(s)", len(offers), len(wanted))
        return offers[:limit]

    def _feed_list(self, client: httpx.Client) -> list[dict]:
        log.info("awin: fetching the feed list")
        response = client.get(self._list_url())
        if response.status_code in (401, 403):
            raise AdapterError(
                "awin: the datafeed API key was rejected. It is the key from "
                "Create-a-Feed, not your publisher ID and not the Awin API "
                "token used for transactions."
            )
        response.raise_for_status()
        text = decompress(response.content)
        rows = list(csv.DictReader(io.StringIO(text)))

        # Say what came back. Every failure from here on is either a column
        # named something unexpected or an advertiser named something
        # unexpected, and both are invisible without this.
        if not rows:
            log.warning("awin: the feed list was empty. First 200 bytes: %r", text[:200])
            return rows
        log.info("awin: feed list columns: %s", list(rows[0].keys()))
        for row in rows[:10]:
            log.info(
                "awin: feed | advertiser=%r status=%r id=%r url=%s",
                pick(row, ("Advertiser Name", "advertiser_name")),
                pick(row, ("Membership Status", "membership_status")),
                pick(row, ("Feed ID", "feed_id", "fid")),
                "yes" if pick(row, ("URL", "url", "download_url")) else "MISSING",
            )
        if len(rows) > 10:
            log.info("awin: ...and %d more feeds", len(rows) - 10)
        return rows

    def _feed_rows(
        self, client: httpx.Client, url: str, advertiser: str, remaining: int
    ) -> list[RawOffer]:
        if remaining <= 0:
            return []
        response = client.get(url)
        response.raise_for_status()
        text = decompress(response.content)

        offers: list[RawOffer] = []
        for row in csv.DictReader(io.StringIO(text)):
            offer = self._to_offer(row, advertiser)
            if offer and offer.is_sellable():
                offers.append(offer)
                if len(offers) >= remaining:
                    break
        log.info("awin: %s -> %d usable rows", advertiser, len(offers))
        return offers

    def _to_offer(self, row: dict, advertiser: str) -> RawOffer | None:
        row = lower_keys(row)
        title = pick(row, FIELDS["title"])
        price = to_float(pick(row, FIELDS["price"]))
        url = pick(row, FIELDS["url"])
        if not (title and price and url):
            return None

        was = to_float(pick(row, FIELDS["was"]))
        retailer = pick(row, FIELDS["merchant"]) or advertiser
        pid = pick(row, FIELDS["pid"]) or url

        return RawOffer(
            source=self.id,
            retailer=retailer,
            external_id=f"awin:{retailer}:{pid}",
            title=title,
            price=price,
            original_price=was if was and was > price else None,
            # The whole point: a tracked link to the seller's own page.
            url=url,
            currency=pick(row, FIELDS["currency"]) or "USD",
            shipping=to_float(pick(row, FIELDS["shipping"])),
            brand=pick(row, FIELDS["brand"]),
            category=pick(row, FIELDS["category"]),
            condition=normalize_condition(pick(row, FIELDS["condition"])),
            availability=pick(row, FIELDS["stock"]),
            image_url=pick(row, FIELDS["image"]),
            gtin=pick(row, FIELDS["gtin"]),
            mpn=pick(row, FIELDS["mpn"]),
            # Rows from a joined programme earn. The export uses this to mark
            # which listings pay, and the page must never sort on it.
            commission_rate=config.AWIN_DEFAULT_RATE,
        )
