"""SQLite access layer for LotSpot.

The database lives on the POS machine itself. Timestamps are stored as
naive local-time ISO strings ("YYYY-MM-DDTHH:MM:SS") on purpose: the store's
business day is the machine's local day, and keeping the strings naive means
day filtering is a plain prefix match with no timezone conversion surprises.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_FILENAME = "lotspot.db"

GENERAL_MERCHANDISE_CATEGORY = "General Merchandise"
FOOD_INGREDIENTS_CATEGORY = "Food & Food Ingredients"
PREPARED_FOOD_CATEGORY = "Prepared Food"
TAX_CATEGORY_SEED_NAMES = (
    GENERAL_MERCHANDISE_CATEGORY,
    FOOD_INGREDIENTS_CATEGORY,
    PREPARED_FOOD_CATEGORY,
)

# (code, name, type) — seeded idempotently by _seed_ledger_accounts.
LEDGER_ACCOUNT_SEED = (
    ("cash_on_hand", "Cash on Hand", "asset"),
    ("card_clearing", "Card Clearing", "asset"),
    ("sales_revenue", "Sales Revenue", "revenue"),
    ("tax_payable", "Tax Payable", "liability"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    store_id TEXT NOT NULL DEFAULT '',
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
    -- admin username who performed the sale/checkout; NULL for sales pushed
    -- by the external POS terminal via API key (no logged-in operator)
    cashier TEXT,
    sold_at TEXT NOT NULL,
    -- UTC mirror of sold_at for a future HQ sync; sold_at itself stays
    -- naive-local because day filtering depends on its plain prefix match
    sold_at_utc TEXT,
    store_id TEXT NOT NULL DEFAULT '',
    -- a line item is voided individually; a receipt (transaction_id) reads
    -- as fully voided when every one of its line items carries voided_at
    voided_at TEXT,
    void_reason TEXT,
    voided_by TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_sold_at ON sales (sold_at);
-- idx_sales_transaction_id/idx_sales_voided_at are created in
-- _migrate_sales_table, not here: on an upgrading (non-fresh) database this
-- script runs before the migration adds those columns, and CREATE INDEX on a
-- not-yet-existing column raises sqlite3.OperationalError.

-- Jurisdiction-level tax rates (e.g. a state or a city), each with its own
-- effective date range so rate changes over time stay auditable.
CREATE TABLE IF NOT EXISTS tax_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    jurisdiction TEXT,
    rate_bps INTEGER NOT NULL CHECK (rate_bps >= 0),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tax_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Which tax_accounts apply to a given tax_category (e.g. "Prepared Food"
-- is subject to both the state and city accounts, "Food & Food Ingredients"
-- may be exempt from the state account).
CREATE TABLE IF NOT EXISTS tax_category_accounts (
    tax_category_id INTEGER NOT NULL REFERENCES tax_categories(id) ON DELETE CASCADE,
    tax_account_id INTEGER NOT NULL REFERENCES tax_accounts(id) ON DELETE CASCADE,
    PRIMARY KEY (tax_category_id, tax_account_id)
);

-- Per-jurisdiction tax breakdown for a single sales row, snapshotted at sale
-- time (same rationale as sales.product_name/sku) so history survives rate
-- changes and tax_account deletion.
CREATE TABLE IF NOT EXISTS sale_tax_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    tax_account_id INTEGER REFERENCES tax_accounts(id) ON DELETE SET NULL,
    tax_account_name TEXT NOT NULL,
    rate_bps INTEGER NOT NULL,
    tax_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sale_tax_lines_sale_id ON sale_tax_lines (sale_id);

CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- admin_users is the single shared login for the POS terminal itself, not
-- per-person identity, so staff clocking in/out are modeled as their own
-- lightweight entity (name + PIN) rather than reusing admin accounts.
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    store_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    clock_in_at TEXT NOT NULL,
    clock_out_at TEXT,
    store_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_time_entries_employee_id ON time_entries (employee_id);
CREATE INDEX IF NOT EXISTS idx_time_entries_clock_in_at ON time_entries (clock_in_at);

-- Belt-and-braces: the app checks for an open shift before inserting, but
-- this index makes "one open shift per employee" a hard DB-level guarantee.
CREATE UNIQUE INDEX IF NOT EXISTS idx_time_entries_one_open_shift
    ON time_entries (employee_id) WHERE clock_out_at IS NULL;

-- Append-only record of privileged actions (PCI Req 10). App code must only
-- ever INSERT here; there is intentionally no UPDATE/DELETE path.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    store_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log (actor);

-- Chart of accounts for the double-entry ledger. Seeded idempotently by
-- _seed_ledger_accounts with the four accounts LotSpot currently needs.
CREATE TABLE IF NOT EXISTS ledger_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Append-only double-entry journal. App code must only ever INSERT here
-- (via post_journal); there is intentionally no UPDATE/DELETE path.
CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    memo TEXT,
    store_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_entries_transaction_id
    ON journal_entries (transaction_id);

CREATE TABLE IF NOT EXISTS journal_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
    account_code TEXT NOT NULL REFERENCES ledger_accounts(code),
    debit_cents INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
    credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_lines_journal_entry_id
    ON journal_lines (journal_entry_id);

CREATE TABLE IF NOT EXISTS cash_drawers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    opening_float_cents INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    opened_by TEXT,
    closed_at TEXT,
    closed_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drawer_id INTEGER NOT NULL REFERENCES cash_drawers(id),
    transaction_id TEXT,
    kind TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cash_movements_drawer_id ON cash_movements (drawer_id);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    tender_type TEXT NOT NULL CHECK (tender_type IN ('cash', 'card')),
    amount_cents INTEGER NOT NULL,
    processor_ref TEXT,
    auth_code TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_transaction_id ON payments (transaction_id);

-- Append-only audit trail of quantity changes, independent of products.qty
-- itself (which stays the current-value source of truth).
CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    transaction_id TEXT,
    delta_qty INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_product_id ON stock_movements (product_id);

-- Records one response per (store_id, key) so a retried POS request replays
-- the original response instead of double-posting.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    transaction_id TEXT,
    response_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (store_id, key)
);
"""


def get_db_path() -> str:
    return os.environ.get(
        "LOTSPOT_DB", str(Path(__file__).resolve().parent / DEFAULT_DB_FILENAME)
    )


def get_store_id() -> str:
    return os.environ.get("LOTSPOT_STORE_ID", "store-01")


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
        _seed_tax_categories(conn)
        _migrate_products_table(conn)
        _migrate_employees_table(conn)
        _migrate_time_entries_table(conn)
        _seed_ledger_accounts(conn)
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
    if "tax_cents" not in columns:
        conn.execute("ALTER TABLE sales ADD COLUMN tax_cents INTEGER NOT NULL DEFAULT 0")
    if "tax_category_name" not in columns:
        conn.execute("ALTER TABLE sales ADD COLUMN tax_category_name TEXT")
    if "store_id" not in columns:
        conn.execute("ALTER TABLE sales ADD COLUMN store_id TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE sales SET store_id = ? WHERE store_id IS NULL OR store_id = ''",
            (get_store_id(),),
        )
    if "sold_at_utc" not in columns:
        # No guessing a timezone for pre-existing rows; leave NULL.
        conn.execute("ALTER TABLE sales ADD COLUMN sold_at_utc TEXT")
    if "cashier" not in columns:
        # No attribution to guess for pre-existing rows; leave NULL.
        conn.execute("ALTER TABLE sales ADD COLUMN cashier TEXT")
    if "voided_at" not in columns:
        conn.execute("ALTER TABLE sales ADD COLUMN voided_at TEXT")
        conn.execute("ALTER TABLE sales ADD COLUMN void_reason TEXT")
        conn.execute("ALTER TABLE sales ADD COLUMN voided_by TEXT")
    if needs_backfill or "transaction_id" in columns:
        conn.execute(
            """UPDATE sales
               SET transaction_id = COALESCE(transaction_id, printf('legacy-%d', id))
               WHERE transaction_id IS NULL OR transaction_id = ''"""
        )
    # Safe here regardless of upgrade path: transaction_id/voided_at are
    # guaranteed to exist on `sales` by this point in the function.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_transaction_id ON sales (transaction_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_voided_at ON sales (voided_at)")


def _seed_tax_categories(conn: sqlite3.Connection) -> None:
    now = local_now_iso()
    for name in TAX_CATEGORY_SEED_NAMES:
        conn.execute(
            "INSERT OR IGNORE INTO tax_categories (name, created_at) VALUES (?, ?)",
            (name, now),
        )


def _migrate_products_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    if "tax_category_id" not in columns:
        # No inline REFERENCES: SQLite's ALTER TABLE ADD COLUMN does not
        # reliably enforce foreign keys declared this way.
        conn.execute("ALTER TABLE products ADD COLUMN tax_category_id INTEGER")
    general_id = conn.execute(
        "SELECT id FROM tax_categories WHERE name = ?", (GENERAL_MERCHANDISE_CATEGORY,)
    ).fetchone()["id"]
    conn.execute(
        "UPDATE products SET tax_category_id = ? WHERE tax_category_id IS NULL",
        (general_id,),
    )
    if "store_id" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN store_id TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE products SET store_id = ? WHERE store_id IS NULL OR store_id = ''",
            (get_store_id(),),
        )


def _migrate_employees_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(employees)")}
    if "store_id" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN store_id TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE employees SET store_id = ? WHERE store_id IS NULL OR store_id = ''",
            (get_store_id(),),
        )


def _migrate_time_entries_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(time_entries)")}
    if "store_id" not in columns:
        conn.execute("ALTER TABLE time_entries ADD COLUMN store_id TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE time_entries SET store_id = ? WHERE store_id IS NULL OR store_id = ''",
            (get_store_id(),),
        )


def _seed_ledger_accounts(conn: sqlite3.Connection) -> None:
    now = local_now_iso()
    for code, name, account_type in LEDGER_ACCOUNT_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO ledger_accounts (code, name, type, created_at) "
            "VALUES (?, ?, ?, ?)",
            (code, name, account_type, now),
        )


def post_journal(
    conn: sqlite3.Connection,
    transaction_id: str,
    lines: list[dict],
    memo: str | None = None,
) -> int:
    """Insert one balanced double-entry journal_entries row plus its
    journal_lines. Raises ValueError (writing zero rows) if debits and
    credits are not equal, or if any line does not set exactly one of
    debit_cents/credit_cents. Caller owns the transaction: this does not
    commit."""
    total_debits = 0
    total_credits = 0
    for line in lines:
        debit_cents = line.get("debit_cents", 0)
        credit_cents = line.get("credit_cents", 0)
        has_debit = debit_cents not in (0, None)
        has_credit = credit_cents not in (0, None)
        if has_debit == has_credit:
            raise ValueError(
                "each journal line must set exactly one of debit_cents/credit_cents"
            )
        total_debits += debit_cents or 0
        total_credits += credit_cents or 0
    if total_debits != total_credits:
        raise ValueError(
            f"unbalanced journal entry: debits={total_debits} credits={total_credits}"
        )

    now = local_now_iso()
    cursor = conn.execute(
        """INSERT INTO journal_entries
           (transaction_id, memo, store_id, created_at, created_at_utc)
           VALUES (?, ?, ?, ?, ?)""",
        (
            transaction_id,
            memo,
            get_store_id(),
            now,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        ),
    )
    journal_entry_id = cursor.lastrowid
    for line in lines:
        conn.execute(
            """INSERT INTO journal_lines
               (journal_entry_id, account_code, debit_cents, credit_cents, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                journal_entry_id,
                line["account_code"],
                line.get("debit_cents", 0) or 0,
                line.get("credit_cents", 0) or 0,
                now,
            ),
        )
    return journal_entry_id


def record_stock_movement(
    conn: sqlite3.Connection,
    product_id: int,
    transaction_id: str | None,
    delta_qty: int,
    reason: str,
) -> None:
    """Append one row to stock_movements. Caller commits as part of its own
    transaction so the movement is atomic with the qty change it records."""
    conn.execute(
        """INSERT INTO stock_movements
           (product_id, transaction_id, delta_qty, reason, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (product_id, transaction_id, delta_qty, reason, local_now_iso()),
    )


def record_audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | int | None,
    store_id: str | None = None,
) -> None:
    """Append one row to audit_log. Caller commits as part of its own
    transaction so the audit row is atomic with the mutation it records."""
    conn.execute(
        """INSERT INTO audit_log
           (actor, action, entity_type, entity_id, store_id, created_at, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            actor,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            store_id or get_store_id(),
            local_now_iso(),
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        ),
    )


def local_now_iso() -> str:
    """Naive local-time ISO string, second precision (see module docstring)."""
    return datetime.now().replace(microsecond=0).isoformat()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
