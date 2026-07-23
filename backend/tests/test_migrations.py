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
