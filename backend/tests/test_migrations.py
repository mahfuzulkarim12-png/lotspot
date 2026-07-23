"""Migration tests: an existing (pre-tax) SQLite DB must upgrade cleanly,
be re-runnable, and leave net revenue on pre-migration sales unchanged."""

import sqlite3

LEGACY_SCHEMA = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    total_cents INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    sold_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# products/sales/employees/time_entries as they existed before store_id and
# sold_at_utc were added, each with one pre-existing row to backfill.
PRE_STORE_ID_SCHEMA = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    transaction_id TEXT,
    product_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    total_cents INTEGER NOT NULL,
    tax_cents INTEGER NOT NULL DEFAULT 0,
    tax_category_name TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    payment_method TEXT,
    sold_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    clock_in_at TEXT NOT NULL,
    clock_out_at TEXT,
    created_at TEXT NOT NULL
);
"""


def _build_pre_store_id_db(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(PRE_STORE_ID_SCHEMA)
        conn.execute(
            """INSERT INTO products (sku, name, qty, price_cents, created_at, updated_at)
               VALUES ('PRE-1', 'Pre Store Widget', 5, 300, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        conn.execute(
            """INSERT INTO sales
               (product_id, transaction_id, product_name, sku, qty, unit_price_cents,
                total_cents, source, sold_at, created_at)
               VALUES (1, 'legacy-txn', 'Pre Store Widget', 'PRE-1', 1, 300, 300,
                       'manual', '2026-01-01T09:00:00', '2026-01-01T09:00:00')"""
        )
        conn.execute(
            """INSERT INTO employees (name, pin_hash, created_at)
               VALUES ('Pre Employee', 'hash', '2026-01-01T00:00:00')"""
        )
        conn.execute(
            """INSERT INTO time_entries (employee_id, clock_in_at, created_at)
               VALUES (1, '2026-01-01T08:00:00', '2026-01-01T08:00:00')"""
        )
        conn.commit()
    finally:
        conn.close()


def _build_legacy_db(path: str) -> None:
    """A pre-tax, pre-transaction_id sales table, matching what a real
    early-version LotSpot install would have on disk."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            """INSERT INTO products (sku, name, qty, price_cents, created_at, updated_at)
               VALUES ('LEGACY-1', 'Legacy Widget', 10, 500, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        conn.execute(
            """INSERT INTO sales
               (product_id, product_name, sku, qty, unit_price_cents, total_cents,
                source, sold_at, created_at)
               VALUES (1, 'Legacy Widget', 'LEGACY-1', 2, 500, 1000,
                       'manual', '2026-01-01T09:00:00', '2026-01-01T09:00:00')"""
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_upgrades_legacy_db_and_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    _build_legacy_db(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))

    import db

    db.init_db()
    db.init_db()  # re-running the migration must not raise

    conn = db.connect()
    try:
        sales_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sales)")}
        assert {"tax_cents", "tax_category_name", "transaction_id", "payment_method"} <= sales_columns

        products_columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        assert "tax_category_id" in products_columns

        sale = conn.execute("SELECT * FROM sales WHERE sku = 'LEGACY-1'").fetchone()
        assert sale["tax_cents"] == 0
        assert sale["total_cents"] == 1000

        product = conn.execute("SELECT * FROM products WHERE sku = 'LEGACY-1'").fetchone()
        general = conn.execute(
            "SELECT id FROM tax_categories WHERE name = ?", (db.GENERAL_MERCHANDISE_CATEGORY,)
        ).fetchone()
        assert product["tax_category_id"] == general["id"]

        categories = {row["name"] for row in conn.execute("SELECT name FROM tax_categories")}
        assert categories == set(db.TAX_CATEGORY_SEED_NAMES)
    finally:
        conn.close()


def test_migration_regression_net_revenue_unchanged_for_pre_migration_sales(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy2.db"
    _build_legacy_db(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", "admin")
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", "testpass123")

    from fastapi.testclient import TestClient

    from app import create_app

    with TestClient(create_app()) as client:
        resp = client.post(
            "/api/auth/login", json={"username": "admin", "password": "testpass123"}
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/api/sales/summary", params={"date": "2026-01-01"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["data"]
        # The pre-migration sale carried no tax; net revenue must equal the
        # original total_cents unchanged, with tax reported separately as 0.
        assert summary["total_revenue_cents"] == 1000
        assert summary["total_tax_cents"] == 0

        resp = client.get(
            "/api/sales/history",
            params={"start": "2026-01-01", "end": "2026-01-01"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        day = resp.json()["data"]["days"][0]
        assert day["total_revenue_cents"] == 1000
        assert day["total_tax_cents"] == 0


def test_store_id_and_sold_at_utc_migrate_idempotently_with_backfill(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "pre_store_id.db"
    _build_pre_store_id_db(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    monkeypatch.setenv("LOTSPOT_STORE_ID", "store-42")

    import db

    db.init_db()
    db.init_db()  # re-running the migration must not raise or duplicate columns

    conn = db.connect()
    try:
        for table in ("products", "sales", "employees", "time_entries"):
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
            assert "store_id" in columns
            assert len(columns) == len(set(columns)), f"duplicate columns on {table}"

        sales_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sales)")}
        assert "sold_at_utc" in sales_columns

        product = conn.execute("SELECT * FROM products WHERE sku = 'PRE-1'").fetchone()
        assert product["store_id"] == "store-42"

        sale = conn.execute("SELECT * FROM sales WHERE sku = 'PRE-1'").fetchone()
        assert sale["store_id"] == "store-42"
        assert sale["sold_at_utc"] is None

        employee = conn.execute(
            "SELECT * FROM employees WHERE name = 'Pre Employee'"
        ).fetchone()
        assert employee["store_id"] == "store-42"

        entry = conn.execute("SELECT * FROM time_entries WHERE employee_id = 1").fetchone()
        assert entry["store_id"] == "store-42"
    finally:
        conn.close()


def test_store_id_defaults_to_store_01_when_env_unset(tmp_path, monkeypatch):
    db_path = tmp_path / "default_store.db"
    _build_pre_store_id_db(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    monkeypatch.delenv("LOTSPOT_STORE_ID", raising=False)

    import db

    db.init_db()

    conn = db.connect()
    try:
        product = conn.execute("SELECT * FROM products WHERE sku = 'PRE-1'").fetchone()
        assert product["store_id"] == "store-01"
    finally:
        conn.close()
