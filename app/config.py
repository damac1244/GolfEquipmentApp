"""Configuration. Everything secret comes from the environment."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("GOLF_DB_PATH", DATA_DIR / "golf.db"))

# Your affiliate sub-id. Appended to outbound links so you can tell which
# products and pages actually earn, rather than guessing.
SUB_ID = os.getenv("GOLF_SUB_ID", "golfdeals")

# --- AvantLink -----------------------------------------------------------
# https://support.avantlink.com/hc/en-us/articles/203644699
AVANTLINK_AFFILIATE_ID = os.getenv("AVANTLINK_AFFILIATE_ID")
AVANTLINK_WEBSITE_ID = os.getenv("AVANTLINK_WEBSITE_ID")
AVANTLINK_AUTH_KEY = os.getenv("AVANTLINK_AUTH_KEY")
AVANTLINK_MERCHANT_IDS = os.getenv("AVANTLINK_MERCHANT_IDS", "")

# --- Impact --------------------------------------------------------------
# https://integrations.impact.com/rest-apis/api-quick-start
IMPACT_ACCOUNT_SID = os.getenv("IMPACT_ACCOUNT_SID")
IMPACT_AUTH_TOKEN = os.getenv("IMPACT_AUTH_TOKEN")
IMPACT_CATALOG_IDS = os.getenv("IMPACT_CATALOG_IDS", "")

# --- CJ ------------------------------------------------------------------
# https://developers.cj.com/graphql/reference/Product%20Search
CJ_PERSONAL_ACCESS_TOKEN = os.getenv("CJ_PERSONAL_ACCESS_TOKEN")
CJ_COMPANY_ID = os.getenv("CJ_COMPANY_ID")
CJ_PARTNER_IDS = os.getenv("CJ_PARTNER_IDS", "")

# --- Awin (product data feeds) ---------------------------------------------
# Where 2nd Swing runs its programme. The key is the datafeed key from
# Create-a-Feed, not the publisher id and not the transactions API token.
# https://help.awin.com/developers/docs/product-feed-list-download
AWIN_API_KEY = os.getenv("AWIN_API_KEY")
# Awin shows a ready-made "Feed List Download" URL in the Create-a-Feed screen,
# with the key already embedded. Pasting that is better than having us rebuild
# it: Awin has served this from more than one host, and a URL copied from the
# account is right by definition. Set either this or AWIN_API_KEY.
AWIN_FEED_LIST_URL = os.getenv("AWIN_FEED_LIST_URL", "")
# Which advertisers to pull. Matched loosely against the feed list, so
# "2nd Swing" catches "2nd Swing Golf". Empty means every joined feed.
AWIN_ADVERTISERS = os.getenv("AWIN_ADVERTISERS", "2nd Swing")
# Exact feed ids, if you would rather be precise than rely on the name.
AWIN_FEED_IDS = os.getenv("AWIN_FEED_IDS", "")
# Recorded on every Awin row so the export can mark which listings earn. It is
# a flag, not an accounting figure -- 2nd Swing pays 15% on selected new
# products and 5% otherwise, so the conservative number is the honest default.
AWIN_DEFAULT_RATE = float(os.getenv("AWIN_DEFAULT_RATE", "0.05"))

# --- Shopping search (web-wide prices, no partnership needed) --------------
# Prices from every merchant, including ones you have no affiliate deal with.
# These rows earn nothing -- see app/adapters/shopping.py for the tradeoffs.
SHOPPING_PROVIDER = os.getenv("GOLF_SHOPPING_PROVIDER")     # serpapi | serper
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
SERPER_KEY = os.getenv("SERPER_KEY")
SHOPPING_COUNTRY = os.getenv("GOLF_SHOPPING_COUNTRY", "us")

# Which country's Google Shopping we ask decides which currency comes back.
# Getting this wrong is not a cosmetic bug: a page that shows "$1,099" without
# saying whose dollars is wrong for roughly half the people reading it.
COUNTRY_CURRENCY = {
    "us": "USD", "ca": "CAD", "gb": "GBP", "uk": "GBP", "au": "AUD",
    "nz": "NZD", "ie": "EUR", "de": "EUR", "fr": "EUR", "es": "EUR",
    "it": "EUR", "nl": "EUR", "jp": "JPY", "se": "SEK", "za": "ZAR",
}
# Override only if you know the feed disagrees with its own country setting.
SHOPPING_CURRENCY = os.getenv(
    "GOLF_SHOPPING_CURRENCY",
    COUNTRY_CURRENCY.get(SHOPPING_COUNTRY.lower(), "USD"),
).upper()

# Results per query. Higher = better coverage, same cost per query.
SHOPPING_PER_QUERY = int(os.getenv("GOLF_SHOPPING_PER_QUERY", "40"))
# Cached responses are free. Daily is plenty -- retailers reprice in campaign
# cycles, not by the minute.
SHOPPING_CACHE_HOURS = float(os.getenv("GOLF_SHOPPING_CACHE_HOURS", "24"))

# Terms used to pull a golf-only slice out of feeds that carry a retailer's
# entire catalog. Broad on purpose -- filtering happens after matching.
GOLF_KEYWORDS = [
    "driver", "fairway wood", "hybrid", "irons", "iron set", "wedge",
    "putter", "golf balls", "golf ball", "golf club", "golf bag",
]

HTTP_TIMEOUT = float(os.getenv("GOLF_HTTP_TIMEOUT", "30"))
