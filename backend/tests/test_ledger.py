"""Ledger substrate tests: post_journal balance enforcement, idempotent
account seeding, and record_stock_movement."""

import sqlite3


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    entries = conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"]
    lines = conn.execute("SELECT COUNT(*) AS n FROM journal_lines").fetchone()["n"]
    return entries, lines


def test_post_journal_rejects_unbalanced_lines_and_writes_zero_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    conn = db.connect()
    try:
        import pytest

        with pytest.raises(ValueError):
            db.post_journal(
                conn,
                "txn-1",
                [
                    {"account_code": "cash_on_hand", "debit_cents": 500},
                    {"account_code": "sales_revenue", "credit_cents": 400},
                ],
            )
        assert _counts(conn) == (0, 0)
    finally:
        conn.close()


def test_post_journal_rejects_line_with_both_debit_and_credit_set(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    conn = db.connect()
    try:
        import pytest

        with pytest.raises(ValueError):
            db.post_journal(
                conn,
                "txn-2",
                [
                    {"account_code": "cash_on_hand", "debit_cents": 500, "credit_cents": 500},
                    {"account_code": "sales_revenue", "credit_cents": 500},
                ],
            )
        assert _counts(conn) == (0, 0)
    finally:
        conn.close()


def test_post_journal_rejects_line_with_neither_debit_nor_credit_set(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    conn = db.connect()
    try:
        import pytest

        with pytest.raises(ValueError):
            db.post_journal(
                conn,
                "txn-3",
                [
                    {"account_code": "cash_on_hand"},
                    {"account_code": "sales_revenue", "credit_cents": 500},
                ],
            )
        assert _counts(conn) == (0, 0)
    finally:
        conn.close()


def test_post_journal_writes_one_entry_and_n_lines_when_balanced(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    conn = db.connect()
    try:
        journal_entry_id = db.post_journal(
            conn,
            "txn-4",
            [
                {"account_code": "cash_on_hand", "debit_cents": 300},
                {"account_code": "sales_revenue", "credit_cents": 250},
                {"account_code": "tax_payable", "credit_cents": 50},
            ],
            memo="sale",
        )
        conn.commit()

        assert _counts(conn) == (1, 3)
        entry = conn.execute(
            "SELECT * FROM journal_entries WHERE id = ?", (journal_entry_id,)
        ).fetchone()
        assert entry["transaction_id"] == "txn-4"
        assert entry["memo"] == "sale"
        assert entry["created_at_utc"] is not None

        lines = conn.execute(
            "SELECT * FROM journal_lines WHERE journal_entry_id = ? ORDER BY id",
            (journal_entry_id,),
        ).fetchall()
        assert [row["account_code"] for row in lines] == [
            "cash_on_hand",
            "sales_revenue",
            "tax_payable",
        ]
        assert lines[0]["debit_cents"] == 300
        assert lines[0]["credit_cents"] == 0
        assert lines[1]["credit_cents"] == 250
        assert lines[2]["credit_cents"] == 50
    finally:
        conn.close()


def test_init_db_seeds_exactly_four_ledger_accounts_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    db.init_db()

    conn = db.connect()
    try:
        rows = conn.execute("SELECT code FROM ledger_accounts").fetchall()
        assert len(rows) == 4
        assert {row["code"] for row in rows} == {
            "cash_on_hand",
            "card_clearing",
            "sales_revenue",
            "tax_payable",
        }
    finally:
        conn.close()


def test_record_stock_movement_writes_one_row(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    import db

    db.init_db()
    conn = db.connect()
    try:
        conn.execute(
            """INSERT INTO products (sku, name, qty, price_cents, created_at, updated_at)
               VALUES ('SKU-1', 'Widget', 10, 100, ?, ?)""",
            (db.local_now_iso(), db.local_now_iso()),
        )
        product_id = conn.execute(
            "SELECT id FROM products WHERE sku = 'SKU-1'"
        ).fetchone()["id"]

        db.record_stock_movement(conn, product_id, "txn-5", -1, "sale")
        conn.commit()

        rows = conn.execute(
            "SELECT * FROM stock_movements WHERE product_id = ?", (product_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["delta_qty"] == -1
        assert rows[0]["reason"] == "sale"
        assert rows[0]["transaction_id"] == "txn-5"
    finally:
        conn.close()


def test_init_db_upgrades_pre_ledger_db_cleanly(tmp_path, monkeypatch):
    """A DB built before this migration (only the pre-existing tables) must
    upgrade cleanly and gain the new ledger tables + seeded accounts."""
    db_path = tmp_path / "pre_ledger.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                store_id TEXT NOT NULL DEFAULT '',
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
                cashier TEXT,
                sold_at TEXT NOT NULL,
                sold_at_utc TEXT,
                store_id TEXT NOT NULL DEFAULT '',
                voided_at TEXT,
                void_reason TEXT,
                voided_by TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    import db

    db.init_db()
    db.init_db()  # re-running the migration must not raise

    conn = db.connect()
    try:
        for table in (
            "ledger_accounts",
            "journal_entries",
            "journal_lines",
            "cash_drawers",
            "cash_movements",
            "payments",
            "stock_movements",
            "idempotency_keys",
        ):
            conn.execute(f"SELECT * FROM {table}")  # raises if table is missing

        accounts = conn.execute("SELECT code FROM ledger_accounts").fetchall()
        assert len(accounts) == 4
    finally:
        conn.close()
