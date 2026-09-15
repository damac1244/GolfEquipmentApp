"""
Read API over the matched catalog.

Deliberately thin. It exists so a frontend -- whatever you design it in -- has
real endpoints to hit, and so you can see the matcher's output without opening
the database. All the interesting logic lives in matching.py and ingest.py.

    uvicorn app.main:app --reload

Endpoints:
    GET  /api/search?q=&brand=&type=&limit=
    GET  /api/products/{id}
    GET  /api/deals?min_discount=&type=&limit=
    GET  /api/brands
    GET  /api/stats
    GET  /go/{offer_id}          -> 302 to the affiliate link, logs the click
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from . import config, db

app = FastAPI(title="Golf Deal Finder", version="0.1.0")


def conn() -> sqlite3.Connection:
    return db.connect()


# --------------------------------------------------------------------------
# Pricing helpers
# --------------------------------------------------------------------------

def _offer_rows(c: sqlite3.Connection, product_id: int, condition: str | None = "new"):
    sql = """
        SELECT id, retailer, source, price, original_price, shipping, currency,
               url, condition, grade, shaft, availability, title, last_seen,
               commission_rate
          FROM offers
         WHERE product_id = ?
    """
    params: list = [product_id]
    if condition:
        sql += " AND condition = ?"
        params.append(condition)
    sql += " ORDER BY (price + COALESCE(shipping, 0)) ASC"
    return [dict(r) for r in c.execute(sql, params)]


def _summarize(product: dict, offers: list[dict], history: list[dict]) -> dict:
    """
    Attach the numbers that make this a deals app rather than a list of links.

    `savings` is the spread between the cheapest and the most expensive
    retailer for the SAME product -- that's the number that justifies the
    site's existence, and it's honest in a way that "% off MSRP" is not,
    because MSRP is whatever a retailer says it is.
    """
    out = dict(product)
    out["offers"] = offers
    out["offer_count"] = len(offers)
    out["retailer_count"] = len({o["retailer"] for o in offers})

    if not offers:
        out.update(best_price=None, best_total=None, worst_total=None,
                   savings=None, discount_pct=None, is_lowest_ever=False,
                   lowest_90d=None)
        return out

    totals = [o["price"] + (o["shipping"] or 0) for o in offers]
    best_total, worst_total = min(totals), max(totals)
    best = offers[0]

    out["best_price"] = best["price"]
    out["best_total"] = round(best_total, 2)
    out["worst_total"] = round(worst_total, 2)
    out["best_retailer"] = best["retailer"]
    out["best_url"] = f"/go/{best['id']}"
    out["savings"] = round(worst_total - best_total, 2)
    out["savings_pct"] = (
        round((worst_total - best_total) / worst_total * 100, 1) if worst_total else 0.0
    )

    msrp = product.get("msrp")
    out["discount_pct"] = (
        round((msrp - best_total) / msrp * 100, 1) if msrp and msrp > best_total else 0.0
    )

    prices = [h["price"] for h in history]
    out["lowest_90d"] = round(min(prices), 2) if prices else None
    # "Cheapest it's been" is the claim people act on. Only make it when the
    # history actually supports it -- a single day of data proves nothing.
    out["is_lowest_ever"] = bool(
        prices and len({h["observed_on"] for h in history}) >= 3
        and best["price"] <= min(prices) + 0.005
    )
    return out


def _history(c: sqlite3.Connection, product_id: int, days: int = 90) -> list[dict]:
    return [dict(r) for r in c.execute(
        """SELECT observed_on, retailer, price
             FROM price_history
            WHERE product_id = ? AND observed_on >= date('now', ?)
            ORDER BY observed_on""",
        (product_id, f"-{days} days"),
    )]


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/api/search")
def search(
    q: str | None = None,
    brand: str | None = None,
    type: str | None = Query(None, alias="type"),
    limit: int = Query(40, le=200),
):
    c = conn()
    try:
        params: list = []
        if q:
            # FTS5 prefix search: "qi35 dri" matches "Qi35 Driver".
            terms = " ".join(f'"{t}"*' for t in q.split() if t)
            sql = """
                SELECT p.* FROM products_fts f
                JOIN products p ON p.id = f.rowid
                WHERE products_fts MATCH ?
            """
            params.append(terms)
        else:
            sql = "SELECT p.* FROM products p WHERE 1=1"

        if brand:
            sql += " AND p.brand = ?"
            params.append(brand)
        if type:
            sql += " AND p.club_type = ?"
            params.append(type)
        sql += " LIMIT ?"
        params.append(limit)

        try:
            rows = [dict(r) for r in c.execute(sql, params)]
        except sqlite3.OperationalError as exc:
            raise HTTPException(400, f"bad search query: {exc}") from exc

        return {
            "count": len(rows),
            "results": [
                _summarize(r, _offer_rows(c, r["id"]), _history(c, r["id"]))
                for r in rows
            ],
        }
    finally:
        c.close()


@app.get("/api/products/{product_id}")
def product(product_id: int, include_used: bool = False):
    c = conn()
    try:
        row = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            raise HTTPException(404, "product not found")
        offers = _offer_rows(c, product_id, condition=None if include_used else "new")
        history = _history(c, product_id)
        result = _summarize(dict(row), offers, history)
        result["price_history"] = history
        return result
    finally:
        c.close()


@app.get("/api/deals")
def deals(
    min_savings_pct: float = 5.0,
    type: str | None = None,
    limit: int = Query(40, le=200),
):
    """
    Products ranked by the spread between the cheapest and dearest retailer.

    Ranking on spread rather than "% off MSRP" is the deliberate choice here.
    Every retailer inflates MSRP, so % off is close to meaningless; the spread
    between two real prices for the same club is a number you can defend.
    """
    c = conn()
    try:
        sql = "SELECT * FROM products"
        params: list = []
        if type:
            sql += " WHERE club_type = ?"
            params.append(type)
        rows = [dict(r) for r in c.execute(sql, params)]

        scored = []
        for r in rows:
            offers = _offer_rows(c, r["id"])
            if len(offers) < 2:  # a "deal" needs something to compare against
                continue
            s = _summarize(r, offers, _history(c, r["id"]))
            if s["savings_pct"] >= min_savings_pct:
                scored.append(s)

        scored.sort(key=lambda s: s["savings"], reverse=True)
        return {"count": len(scored[:limit]), "results": scored[:limit]}
    finally:
        c.close()


@app.get("/api/brands")
def brands():
    c = conn()
    try:
        return {"brands": [dict(r) for r in c.execute(
            """SELECT brand, COUNT(*) AS products FROM products
                WHERE brand IS NOT NULL GROUP BY brand ORDER BY products DESC"""
        )]}
    finally:
        c.close()


@app.get("/api/stats")
def stats():
    c = conn()
    try:
        one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "products": one("SELECT COUNT(*) FROM products"),
            "offers": one("SELECT COUNT(*) FROM offers"),
            "retailers": one("SELECT COUNT(DISTINCT retailer) FROM offers"),
            "price_points": one("SELECT COUNT(*) FROM price_history"),
            "multi_retailer_products": one(
                """SELECT COUNT(*) FROM (
                     SELECT product_id FROM offers
                     GROUP BY product_id HAVING COUNT(DISTINCT retailer) > 1)"""
            ),
            "last_runs": [dict(r) for r in c.execute(
                """SELECT source, status, offers_seen, products, finished_at, error
                     FROM ingest_runs ORDER BY id DESC LIMIT 5"""
            )],
        }
    finally:
        c.close()


@app.get("/go/{offer_id}")
def go(offer_id: int, request: Request):
    """
    Outbound click handler.

    Two jobs: stamp your sub-id onto the link so the network reports which of
    your pages earned the commission, and log the click locally so you know
    what converts before the network's reporting catches up (it lags by days).
    """
    c = conn()
    try:
        row = c.execute("SELECT id, url, source FROM offers WHERE id = ?",
                        (offer_id,)).fetchone()
        if not row:
            raise HTTPException(404, "offer not found")

        c.execute(
            "INSERT INTO clicks (offer_id, referrer) VALUES (?,?)",
            (offer_id, request.headers.get("referer")),
        )
        c.commit()

        # Each network names its sub-id parameter differently.
        param = {"impact": "subId1", "cj": "sid", "avantlink": "ctc"}.get(
            row["source"], "subid"
        )
        parts = urlparse(row["url"])
        query = parts.query + ("&" if parts.query else "") + urlencode(
            {param: config.SUB_ID}
        )
        return RedirectResponse(urlunparse(parts._replace(query=query)), status_code=302)
    finally:
        c.close()


@app.on_event("startup")
def _startup() -> None:
    db.init()
