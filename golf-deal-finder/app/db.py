"""
SQLite storage.

Three tables that matter:
  products      -- one row per canonical SKU (the matcher's output)
  offers        -- one row per retailer selling that SKU, refreshed each ingest
  price_history -- append-only snapshot per offer per day

price_history is what turns a price comparison site into a deals site. Without
it you can only say "cheapest right now"; with it you can say "cheapest it has
been in 90 days", which is the claim people act on.

SQLite is the right call until you're past a few million offers. It handles
this workload fine and it means zero infrastructure while you're validating.
Swap to Postgres when concurrent ingest becomes a problem, not before.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id               INTEGER PRIMARY KEY,
    match_key        TEXT NOT NULL UNIQUE,
    display_name     TEXT NOT NULL,
    brand            TEXT,
    club_type        TEXT,
    model            TEXT,
    loft             REAL,
    flex             TEXT,
    dexterity        TEXT,
    shaft            TEXT,
    set_composition  TEXT,
    bounce           TEXT,
    length           REAL,
    year             INTEGER,
    gtin             TEXT,
    image_url        TEXT,
    msrp             REAL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_brand    ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_type     ON products(club_type);
CREATE INDEX IF NOT EXISTS idx_products_name     ON products(display_name);

CREATE TABLE IF NOT EXISTS offers (
    id              INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    retailer        TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    price           REAL NOT NULL,
    original_price  REAL,
    shipping        REAL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    url             TEXT NOT NULL,
    condition       TEXT NOT NULL DEFAULT 'new',
    grade           TEXT,
    shaft           TEXT,
    availability    TEXT,
    image_url       TEXT,
    commission_rate REAL,
    rating          REAL,
    rating_count    INTEGER,
    last_seen       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id);
CREATE INDEX IF NOT EXISTS idx_offers_price   ON offers(product_id, price);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    retailer    TEXT NOT NULL,
    price       REAL NOT NULL,
    observed_on TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE(product_id, retailer, observed_on)
);

CREATE INDEX IF NOT EXISTS idx_history_product ON price_history(product_id, observed_on);

CREATE TABLE IF NOT EXISTS clicks (
    id         INTEGER PRIMARY KEY,
    offer_id   INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    clicked_at TEXT NOT NULL DEFAULT (datetime('now')),
    referrer   TEXT
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    offers_seen INTEGER DEFAULT 0,
    products    INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running',
    error       TEXT
);

-- Full-text search over product names. Kept in sync by triggers so a rebuild
-- is never needed.
CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
    display_name, brand, model, club_type,
    content='products', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
    INSERT INTO products_fts(rowid, display_name, brand, model, club_type)
    VALUES (new.id, new.display_name, new.brand, new.model, new.club_type);
END;

CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, display_name, brand, model, club_type)
    VALUES ('delete', old.id, old.display_name, old.brand, old.model, old.club_type);
END;

CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, display_name, brand, model, club_type)
    VALUES ('delete', old.id, old.display_name, old.brand, old.model, old.club_type);
    INSERT INTO products_fts(rowid, display_name, brand, model, club_type)
    VALUES (new.id, new.display_name, new.brand, new.model, new.club_type);
END;
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path or config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a table that already exists, so a database carried over from an
# earlier version would be missing these and every insert would fail with
# "table offers has no column named ...". Adding them here keeps old databases
# working, which matters because the scheduled job caches the file between runs.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("offers", "grade", "TEXT"),
    ("offers", "shaft", "TEXT"),
    ("offers", "rating", "REAL"),
    ("offers", "rating_count", "INTEGER"),
]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any missing columns. Safe to run every time; returns what it added."""
    added = []
    for table, column, coltype in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue          # table doesn't exist yet; SCHEMA will create it
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            added.append(f"{table}.{column}")
    return added


def init(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        added = migrate(conn)
        if added:
            conn.commit()


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
