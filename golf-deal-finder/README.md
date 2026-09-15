# Golf Equipment App

Find the best price on a given piece of golf equipment across multiple
retailers.

The app pulls product feeds from affiliate networks, collapses the same club
listed by different retailers into one comparable product, and ranks results by
the spread between the cheapest and dearest seller.

**Status: engine and compare screen built; no live data yet.** The ingest
pipeline, matcher, export and front end all work end to end against sample
data. What is missing is a live price source — see *Data sources*.

---

## Why this is harder than it looks

The obvious version of this app is a scraper and a price column. The actual
problem is that five retailers sell the identical driver and describe it five
different ways:

```
PGA TOUR Superstore  TaylorMade Qi35 Driver - 10.5° / Stiff / Right Hand
Golf Galaxy          TaylorMade Qi35 Driver (Right Hand, 10.5 Degree, Stiff Flex)
TGW                  TaylorMade Qi35 Driver 10.5 Stiff RH
Rock Bottom Golf     2025 TaylorMade Qi35 Men's Driver 10.5* S-Flex RH - NEW!
Global Golf          TaylorMade Golf Qi35 Driver 10.5 Degree Stiff Right Handed
Carl's Golfland      TaylorMade Qi35 Driver w/ Ventus Blue 10.5 Stiff RH
```

If those don't collapse into one product, the app shows six products with one
price each, which is worse than useless. `app/matching.py` is the component
that does this, and it is where the value of the whole project sits.

It fails in two directions, and they are not equally bad:

- **Under-merging** — one club shows up as several products. Embarrassing.
- **Over-merging** — two different clubs collapse into one. Much worse: a
  shopper sees a 9° price attached to the 10.5° they're looking at.

The matcher is deliberately conservative. Loft, flex, dexterity and set
composition must all agree exactly before a fuzzy name match is even
considered.

---

## Features

- **Pluggable feed adapters** — one file per source, all reducing to the same
  `RawOffer`. Adding a network touches nothing else in the codebase.
- **Web-wide pricing without partnerships** — a shopping-search adapter pulls
  prices from merchants you have no affiliate deal with, so the compare table
  shows the real cheapest price, not the cheapest among your partners.
- **Condition grading** — seller wording ("Excellent", "Very Good", "Mint")
  mapped onto one scale, so new and used sit in the same table and stay
  comparable.
- **Product matching** — GTIN first, then a structured brand/type/model/spec
  key, then a guarded fuzzy fallback. Condition and grade ride on the offer,
  never the product key, so a new listing and three differently-graded used
  ones are one product with four rows.
- **Price history** — one snapshot per product per retailer per day, which is
  what backs a defensible "lowest in 90 days" claim.
- **Deal ranking by real spread** — cheapest vs dearest retailer for the same
  club, rather than "% off MSRP" (MSRP is whatever a retailer says it is).
- **Affiliate link handling** — outbound clicks are stamped with your sub-id
  per network's parameter convention and logged locally.
- **Runs with zero credentials** — a synthetic sample feed ships with the repo
  so the app works before any affiliate application is approved.

## Prerequisites

- Python 3.11+
- No database server. Storage is SQLite, which is the right call until you're
  past a few million offers. Move to Postgres when concurrent ingest actually
  hurts, not before.
- Affiliate network accounts, eventually — see *Data sources* below. Nothing
  below requires them to get running.

## Installation

```bash
git clone https://github.com/damac1244/GolfEquipmentApp.git
cd GolfEquipmentApp

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # optional: only needed for live feeds
```

## Usage

```bash
# 1. Generate the synthetic sample feed (skip once you have real credentials)
python scripts/make_sample_feed.py

# 2. Ingest: fetch -> match -> store
python -m app.ingest --reset

# 3. Serve the API
uvicorn app.main:app --reload
```

Expected output from step 2 against the sample feed:

```
INFO app.adapters.sample: sample: 118 offers
INFO ingest: sample: 118 offers -> 24 products
```

118 listings collapsing to exactly 24 products, with no single-offer orphans,
is the signal that matching is working.

### Turning on web-wide pricing

This is the fastest route to real numbers — no application, no waiting.

```bash
# 1. Get a key from a shopping-search provider, then:
export GOLF_SHOPPING_PROVIDER=serper      # or serpapi
export SERPER_KEY=your_key_here

# 2. Check the response shape matches what the adapter expects
python -m app.ingest --probe shopping

# 3. Pull real prices
python -m app.ingest --source shopping
```

**Controlling the bill.** `data/seed_queries.txt` is your catalogue and your
invoice: one billed API call per line per refresh. Responses cache for 24
hours (`GOLF_SHOPPING_CACHE_HOURS`), so re-running the same day costs nothing.
Roughly 45 queries daily is ~1,350 calls a month — a couple of dollars on a
cheap provider, over quota on an expensive one. Start with the clubs people
actually search for and grow the list once you can see which ones earn.

Delete `data/cache/shopping/` to force fresh prices.

### Other commands

```bash
python -m app.ingest --source impact      # one source only
python -m app.ingest --probe shopping     # dump raw + parsed offers, then exit
python -m app.ingest --query "driver"     # restrict the pull
python tests/test_matching.py             # matcher tests, no pytest needed
python tests/test_shopping.py             # shopping tests, no API key needed
pytest tests/                             # everything, the normal way
```

`--probe` is the one to reach for first when a new network is connected. It
prints a raw feed item next to what the parser made of it, which is the fastest
way to catch a field-name mismatch.

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/search?q=&brand=&type=&limit=` | Full-text product search with prices attached |
| `GET /api/products/{id}?include_used=` | One product, every offer, 90-day price history |
| `GET /api/deals?min_savings_pct=&type=` | Products ranked by cheapest-vs-dearest spread |
| `GET /api/brands` | Brand facet counts |
| `GET /api/stats` | Catalog size and recent ingest runs |
| `GET /go/{offer_id}` | 302 to the affiliate link, sub-id stamped, click logged |

## Data sources

Affiliate feeds rather than scraping. Scraping golf retailers gets you blocked,
breaks constantly, and generally violates the sites' terms; affiliate feeds are
sanctioned, structured, and pay you a commission on top.

| Source | What it brings | Adapter |
|---|---|---|
| **Shopping search** | **Every merchant on the web. No partnership, works today.** | `app/adapters/shopping.py` |
| Impact | GlobalGolf (used + new), PGA TOUR Superstore, TaylorMade | `app/adapters/impact.py` |
| Awin | 2nd Swing — the big used specialist | *not yet written* |
| CJ Affiliate | Golf Direct Now, Puma/Cobra | `app/adapters/cj.py` |
| AvantLink | Many independent golf specialists | `app/adapters/avantlink.py` |
| eBay | Deepest used inventory | *not yet written* |
| — | Synthetic dev data | `app/adapters/sample.py` |

### Two kinds of source, and why you need both

**Affiliate feeds pay you.** Narrow — only merchants who signed up — and every
one needs approval, which takes days to weeks. This is how the site eventually
funds itself.

**Shopping search completes the picture.** It reads Google Shopping, so it
reaches merchants you have no relationship with, needs no approval, and works
this afternoon. It earns you **nothing**.

Run both. Offers from shopping search carry `commission_rate=None`, so you can
always tell which half of a compare table actually pays. Ranking stays on total
price regardless — the moment you sort by what earns, you're a worse Google and
there's no reason for anyone to use you.

**Know before you switch it on:** shopping-search providers work by reading
Google's results, which Google's terms prohibit. The industry operates openly
and the law is unsettled, but it's a different risk profile from a sanctioned
affiliate feed. Decide that knowingly.

Approval is per-retailer and takes days to weeks, so apply early. The sample
adapter exists so development isn't blocked on that.

**Verified against docs:** Impact's base URL and Basic-auth scheme, CJ's
GraphQL Product Search, AvantLink's plain-HTTP query interface.
**Not verified against a live account:** the exact response field names. Each
adapter reads defensively (multiple candidate keys per field) and every one
carries a docs link. Run `--probe` on first connection.

### Adding a network

Implement `is_configured()` and `fetch()` from `app/adapters/base.py`, return
`RawOffer` objects, and register it in `app/adapters/__init__.py`. Nothing else
changes.

## Putting results on a website

No server needed. The engine writes one JSON file; a static page reads it.

```bash
python -m app.ingest --source shopping        # fetch prices
python scripts/export_prices.py --min-offers 2  # -> site/prices.json
```

`site/` is the whole website — drop it on any static host:

```
site/
  index.html     landing page
  compare.html   the product: search + compare
  prices.json    written by export_prices.py
```

### Refreshing automatically

`.github/workflows/refresh-prices.yml` runs the whole chain daily in GitHub's
cloud — nothing on your machine. One-time setup:

1. Repo → Settings → Secrets and variables → Actions → New repository secret
   named `SERPER_KEY`.
2. Actions tab → "Refresh prices" → Run workflow. Watch this first run.

It commits `site/prices.json` when prices change. **Connect your host to this
repo** (Cloudflare Pages → Connect to Git) so that commit publishes itself —
otherwise you are re-uploading by hand every day, which defeats the point.

Price history lives in the SQLite file, cached between runs on a best-effort
basis. Expect gaps until that moves to a real database.

## Project structure

```
app/
  matching.py          # brand/model/spec parsing and SKU identity  <- the core
  ingest.py            # fetch -> match -> upsert -> snapshot prices
  db.py                # SQLite schema (products, offers, price_history, clicks)
  main.py              # FastAPI read API + affiliate redirect
  config.py            # env-driven settings
  adapters/
    base.py            # RawOffer + the Adapter protocol
    sample.py          # synthetic feed, no credentials needed
    avantlink.py       # AvantLink ProductSearch
    impact.py          # Impact catalog items
    cj.py              # CJ GraphQL product search
scripts/
  make_sample_feed.py  # generates data/sample_feed.json
  check_shopping.py    # one API call; verifies key + response shape
  export_prices.py     # database -> site/prices.json
site/
  index.html           # landing page
  compare.html         # the compare screen (reads prices.json)
.github/workflows/
  refresh-prices.yml   # daily price refresh, free, in GitHub's cloud
tests/
  test_matching.py     # 50 tests, runnable with or without pytest
```

## Design decisions worth knowing

**Shaft is not part of product identity.** Retailers are inconsistent about
naming the *stock* shaft — one writes "Qi35 Driver", the next writes "Qi35
Driver w/ Ventus Blue" for the identical club. Treating shaft as identity
splits one product into two, each showing a single price. So shaft is captured
per-offer and displayed in the compare table instead. Practical consequence for
the UI: **the compare table needs a shaft column**, or an aftermarket-shaft
listing sitting $150 above the rest looks like a bug. Flip `SHAFT_IN_KEY` in
`matching.py` if your feeds turn out to be disciplined.

**Condition is not part of identity either.** An open-box club is the same
product at a different price; it belongs in the same table with a badge, not in
a separate listing. `/api/products/{id}?include_used=true` opts in.

**Deals rank on retailer spread, not % off MSRP.** MSRP is self-reported and
routinely inflated. The gap between two real prices for the same club is a
number you can defend.

**Bare numbers stay in model names.** An earlier version stripped 1–2 digit
tokens as spec noise, which silently merged Scotty Cameron Phantom 5 into
Phantom 11. Lofts, bounces, set compositions and lengths are stripped
explicitly instead; everything else is assumed to be the model.

## Known gaps

- The Awin (2nd Swing) and eBay adapters aren't written yet — those are the
  deepest used-gear sources, and used is the differentiator.
- Per-seller grade calibration isn't done: we normalise the *words*, not yet
  what each seller actually means by them. That needs their grading guides.
- `app/main.py` is written but has not been run — FastAPI couldn't be installed
  in the environment it was authored in. The ingest pipeline and matcher are
  verified; treat the API as untested code.
- Live network adapters are unexercised against real accounts (see above).
- No pagination on search; fine at sample scale, not at 100k products.
- Matching handles clubs and balls. Apparel, shoes and accessories parse poorly
  and are dropped at ingest rather than matched badly.
- No scheduling. Ingest is a manual command; put it on cron or a scheduled task
  once feeds are live. Daily is plenty — retailers reprice in campaign cycles.

## Legal

Use affiliate feeds, not scrapers. Affiliate programs require disclosure that
you earn commission on outbound links — put it somewhere visible before you
launch. Prices in the sample feed are invented and must not be shown to anyone
as real.

## Contributing

Issues and pull requests welcome. If you touch `matching.py`, add a test case
to `tests/test_matching.py` first — particularly an over-merge guard, since
that's the failure mode nothing else catches.

## Contact

[@damac1244](https://github.com/damac1244)

## License

Not yet chosen. MIT is the usual default for a project like this; add a
`LICENSE` file before making the repo public.
