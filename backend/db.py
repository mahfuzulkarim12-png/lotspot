"""SQLite access layer for LotSpot.

The database lives on the POS machine itself. Timestamps are stored as
naive local-time ISO strings ("YYYY-MM-DDTHH:MM:SS") on purpose: the store's
business day is the machine's local day, and keeping the strings naive means
day filtering is a plain prefix match with no timezone conversion surprises.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB_FILENAME = "lotspot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    transaction_id TEXT,
    -- name/sku/price are snapshotted at sale time so history survives
    -- product deletion and later price changes
    product_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    total_cents INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    payment_method TEXT,
    sold_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_sold_at ON sales (sold_at);

CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_db_path() -> str:
    return os.environ.get(
        "LOTSPOT_DB", str(Path(__file__).resolve().parent / DEFAULT_DB_FILENAME)
    )


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate_sales_table(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_sales_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sales)")}
    needs_backfill = False
    if "transaction_id" not in columns:
        conn.execute("ALTER TABLE sales ADD COLUMN transaction_id TEXT")
        needs_backfill = True
    if "payment_method" not in columns:
        conn.execute("ALTER TABLE sales ADD COLUMN payment_method TEXT")
        needs_backfill = True
    if needs_backfill or "transaction_id" in columns:
        conn.execute(
            """UPDATE sales
               SET transaction_id = COALESCE(transaction_id, printf('legacy-%d', id))
               WHERE transaction_id IS NULL OR transaction_id = ''"""
        )


def local_now_iso() -> str:
    """Naive local-time ISO string, second precision (see module docstring)."""
    return datetime.now().replace(microsecond=0).isoformat()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
