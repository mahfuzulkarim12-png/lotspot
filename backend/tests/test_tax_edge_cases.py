"""Edge-case and robustness coverage for the sales-tax feature, written
independently of the implementing coder's test_sales.py / test_pos.py /
test_migrations.py / test_tax_admin.py. Focus areas: tax_account effective
date boundaries, deletion of tax_accounts already used in sale history,
the "no accounts mapped" code path vs the "explicit 0% account" code path,
large integer-cents math near the model's declared caps, rapid mapping
changes racing a checkout, adversarial admin CRUD input, and a second
legacy-DB migration shape.
"""

import sqlite3
import threading
from datetime import date, timedelta

import db


# --------------------------------------------------------------------- helpers

def _create_tax_category(client, headers, name):
    resp = client.post("/api/tax-categories", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _create_tax_account(client, headers, **overrides):
    payload = {
        "name": "Test Tax",
        "jurisdiction": "Test Jurisdiction",
        "rate_bps": 0,
        "effective_from": "2000-01-01",
        "effective_to": None,
    }
    payload.update(overrides)
    resp = client.post("/api/tax-accounts", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _map_tax_accounts(client, headers, category_id, account_ids):
    resp = client.put(
        f"/api/tax-categories/{category_id}/tax-accounts",
        json={"tax_account_ids": account_ids},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _product_with_category(client, headers, category_id, price_cents, sku, qty=100):
    resp = client.post(
        "/api/products",
        json={
            "sku": sku,
            "name": "Tax Edge Item",
            "qty": qty,
            "price_cents": price_cents,
            "tax_category_id": category_id,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _sale_tax_line_rows(sale_id):
    """Direct SQL read, bypassing the API, to check what actually landed in
    sale_tax_lines (the API only ever returns rows for the sale it just
    created, which isn't enough to prove zero rows exist for a different
    code path)."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM sale_tax_lines WHERE sale_id = ? ORDER BY id", (sale_id,)
        ).fetchall()
        return [db.row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _sale_row(sale_id):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        return db.row_to_dict(row) if row else None
    finally:
        conn.close()


# ------------------------------------------------------- 1. effective dates

def test_tax_account_effective_from_today_applies(client, admin_headers, monkeypatch):
    sale_day = "2026-03-15"
    category = _create_tax_category(client, admin_headers, "Effective From Today")
    account = _create_tax_account(
        client, admin_headers, name="Starts Today", rate_bps=500,
        effective_from=sale_day, effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="EFF-FROM-TODAY"
    )

    monkeypatch.setattr("db.local_now_iso", lambda: f"{sale_day}T10:00:00")
    resp = client.post(
        "/api/sales", json={"product_id": product["id"], "qty": 1}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    assert sale["tax_cents"] == 50
    assert len(sale["tax_lines"]) == 1


def test_tax_account_effective_from_tomorrow_does_not_apply(client, admin_headers, monkeypatch):
    sale_day = "2026-03-15"
    starts = (date.fromisoformat(sale_day) + timedelta(days=1)).isoformat()
    category = _create_tax_category(client, admin_headers, "Effective From Tomorrow")
    account = _create_tax_account(
        client, admin_headers, name="Starts Tomorrow", rate_bps=500,
        effective_from=starts, effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="EFF-FROM-TOMORROW"
    )

    monkeypatch.setattr("db.local_now_iso", lambda: f"{sale_day}T10:00:00")
    resp = client.post(
        "/api/sales", json={"product_id": product["id"], "qty": 1}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    assert sale["tax_cents"] == 0
    assert sale["tax_lines"] == []


def test_tax_account_effective_to_today_is_inclusive(client, admin_headers, monkeypatch):
    sale_day = "2026-03-15"
    category = _create_tax_category(client, admin_headers, "Effective To Today")
    account = _create_tax_account(
        client, admin_headers, name="Ends Today", rate_bps=500,
        effective_from="2026-01-01", effective_to=sale_day,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="EFF-TO-TODAY"
    )

    monkeypatch.setattr("db.local_now_iso", lambda: f"{sale_day}T10:00:00")
    resp = client.post(
        "/api/sales", json={"product_id": product["id"], "qty": 1}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    assert sale["tax_cents"] == 50
    assert len(sale["tax_lines"]) == 1


def test_tax_account_effective_to_yesterday_does_not_apply(client, admin_headers, monkeypatch):
    sale_day = "2026-03-15"
    ends = (date.fromisoformat(sale_day) - timedelta(days=1)).isoformat()
    category = _create_tax_category(client, admin_headers, "Effective To Yesterday")
    account = _create_tax_account(
        client, admin_headers, name="Ended Yesterday", rate_bps=500,
        effective_from="2026-01-01", effective_to=ends,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="EFF-TO-YESTERDAY"
    )

    monkeypatch.setattr("db.local_now_iso", lambda: f"{sale_day}T10:00:00")
    resp = client.post(
        "/api/sales", json={"product_id": product["id"], "qty": 1}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    assert sale["tax_cents"] == 0
    assert sale["tax_lines"] == []


# ---------------------------------------------- 2. deleting a used tax_account

def test_delete_tax_account_used_in_history_preserves_snapshot(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Deletable Jurisdiction")
    account = _create_tax_account(
        client, admin_headers, name="Soon Deleted Tax", rate_bps=700,
        effective_from="2000-01-01", effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="DEL-ACCT-USED"
    )

    resp = client.post(
        "/api/sales", json={"product_id": product["id"], "qty": 2}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    sale_id = sale["id"]
    assert sale["tax_cents"] == 140  # 2000 * 7% = 140

    resp = client.delete(f"/api/tax-accounts/{account['id']}", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    # The account is gone from the live list.
    resp = client.get("/api/tax-accounts", headers=admin_headers)
    assert all(a["id"] != account["id"] for a in resp.json()["data"])

    # But the historical sale and its tax breakdown must survive untouched.
    persisted_sale = _sale_row(sale_id)
    assert persisted_sale is not None
    assert persisted_sale["tax_cents"] == 140
    assert persisted_sale["total_cents"] == 2000

    lines = _sale_tax_line_rows(sale_id)
    assert len(lines) == 1
    assert lines[0]["tax_account_id"] is None  # ON DELETE SET NULL
    assert lines[0]["tax_account_name"] == "Soon Deleted Tax"  # snapshot retained
    assert lines[0]["rate_bps"] == 700
    assert lines[0]["tax_cents"] == 140

    # The category's mapping to the now-deleted account is also gone
    # (tax_category_accounts cascades), not left dangling.
    resp = client.get("/api/tax-categories", headers=admin_headers)
    updated_category = next(
        c for c in resp.json()["data"] if c["id"] == category["id"]
    )
    assert account["id"] not in updated_category["tax_account_ids"]


# --------------------------------- 3. unmapped category vs explicit 0% account

def test_tax_category_with_no_mapped_accounts_creates_no_tax_lines(client, admin_headers):
    """Contrast with test_sales.py::test_tax_zero_rate_category_produces_zero_tax,
    which maps an explicit 0%-rate account and DOES get one sale_tax_lines row.
    A category with an empty mapping should take a different path entirely:
    zero tax_lines rows, not one row with tax_cents=0."""
    category = _create_tax_category(client, admin_headers, "Never Mapped Category")
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=999, sku="NO-MAPPING"
    )

    resp = client.post(
        "/api/sales", json={"product_id": product["id"], "qty": 1}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    assert sale["tax_cents"] == 0
    assert sale["tax_lines"] == []
    assert sale["tax_category_name"] == "Never Mapped Category"

    # Direct DB check: no sale_tax_lines rows at all for this sale, not one
    # row with tax_cents=0.
    assert _sale_tax_line_rows(sale["id"]) == []


# ------------------------------------------------- 4. large qty/price/rate

def test_tax_at_max_qty_price_and_rate_has_no_overflow_or_drift(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Max Values Category")
    account = _create_tax_account(
        client, admin_headers, name="Max Rate Tax", rate_bps=10000,
        effective_from="2000-01-01", effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=9999999, sku="MAX-VALUES",
        qty=999999,
    )

    resp = client.post(
        "/api/sales",
        json={"product_id": product["id"], "qty": 999999},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    expected_total = 999999 * 9999999
    assert sale["total_cents"] == expected_total
    # At a 100% rate, tax must equal the line total exactly, no rounding drift.
    assert sale["tax_cents"] == expected_total
    assert sale["grand_total_cents"] == expected_total * 2
    assert sale["tax_lines"][0]["tax_cents"] == expected_total


def test_tax_at_max_amounts_with_nonclean_rate_matches_manual_rounding(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Max Values Nonclean Rate")
    rate_bps = 9999
    account = _create_tax_account(
        client, admin_headers, name="Nonclean Rate Tax", rate_bps=rate_bps,
        effective_from="2000-01-01", effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=9999999, sku="MAX-VALUES-2",
        qty=999999,
    )

    resp = client.post(
        "/api/sales",
        json={"product_id": product["id"], "qty": 999999},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    total_cents = 999999 * 9999999
    # Independent integer round-half-up, computed without touching tax.py,
    # so this doesn't just re-assert the implementation against itself.
    expected_tax = (total_cents * rate_bps + 5000) // 10000
    assert sale["tax_cents"] == expected_tax
    assert sale["tax_lines"][0]["tax_cents"] == expected_tax


# ---------------------------- 5. rapid mapping changes racing a checkout

def test_rapid_tax_category_mapping_changes_do_not_crash_concurrent_sales(
    client, admin_headers
):
    category = _create_tax_category(client, admin_headers, "Racing Category")
    account_a = _create_tax_account(
        client, admin_headers, name="Rate A", rate_bps=100,
        effective_from="2000-01-01", effective_to=None,
    )
    account_b = _create_tax_account(
        client, admin_headers, name="Rate B", rate_bps=500,
        effective_from="2000-01-01", effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account_a["id"]])
    product = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="RACE-PRODUCT",
        qty=500,
    )

    sale_responses = []
    mapping_responses = []
    lock = threading.Lock()

    def flip_mapping():
        options = [[account_a["id"]], [account_b["id"]], [account_a["id"], account_b["id"]]]
        for i in range(20):
            resp = client.put(
                f"/api/tax-categories/{category['id']}/tax-accounts",
                json={"tax_account_ids": options[i % len(options)]},
                headers=admin_headers,
            )
            with lock:
                mapping_responses.append(resp.status_code)

    def do_sale():
        resp = client.post(
            "/api/sales",
            json={"product_id": product["id"], "qty": 1},
            headers=admin_headers,
        )
        with lock:
            sale_responses.append(resp)

    threads = [threading.Thread(target=flip_mapping)]
    threads += [threading.Thread(target=do_sale) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(not t.is_alive() for t in threads), "a thread hung/deadlocked"
    assert all(status == 200 for status in mapping_responses), mapping_responses
    assert len(sale_responses) == 15
    for resp in sale_responses:
        assert resp.status_code == 201, resp.text
        sale = resp.json()["data"]
        # Whichever mapping was active when this particular sale was
        # recorded, the persisted breakdown must be internally consistent.
        assert sum(line["tax_cents"] for line in sale["tax_lines"]) == sale["tax_cents"]


# --------------------------------- 6. malformed / adversarial admin input

def test_create_tax_account_negative_rate_bps_rejected(client, admin_headers):
    resp = _create_tax_account_raw(client, admin_headers, rate_bps=-1)
    assert resp.status_code == 422


def test_create_tax_account_rate_bps_over_max_rejected(client, admin_headers):
    resp = _create_tax_account_raw(client, admin_headers, rate_bps=10001)
    assert resp.status_code == 422


def test_create_tax_account_rate_bps_at_max_accepted(client, admin_headers):
    resp = _create_tax_account_raw(client, admin_headers, rate_bps=10000)
    assert resp.status_code == 201, resp.text


def test_create_tax_account_invalid_date_format_rejected(client, admin_headers):
    for bad_date in ("2026/01/01", "01-01-2026", "not-a-date", "2026-01-01T00:00:00", ""):
        resp = _create_tax_account_raw(client, admin_headers, effective_from=bad_date)
        assert resp.status_code == 422, f"{bad_date!r} should have been rejected: {resp.text}"


def test_create_tax_account_accepts_calendar_invalid_date_matching_shape(client, admin_headers):
    """DATE_PATTERN only checks digit shape (^\\d{4}-\\d{2}-\\d{2}$), not real
    calendar validity. A nonsensical date like month 13 / day 40 currently
    passes validation. Documented here as observed (and arguably
    under-validated) behavior, not asserted as desirable."""
    resp = _create_tax_account_raw(client, admin_headers, effective_from="2026-13-40")
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["effective_from"] == "2026-13-40"


def test_set_tax_category_accounts_duplicate_ids_rejected_gracefully(client, admin_headers):
    """Expected contract: a PUT that fully replaces a category's tax-account
    mapping is idempotent set-replace semantics, so duplicate ids in the
    payload should either be deduped or rejected with a 4xx - never a 500.
    See report for the actual observed behavior."""
    category = _create_tax_category(client, admin_headers, "Duplicate Ids Category")
    account = _create_tax_account(client, admin_headers, name="Dup Target", rate_bps=100)

    resp = client.put(
        f"/api/tax-categories/{category['id']}/tax-accounts",
        json={"tax_account_ids": [account["id"], account["id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["tax_account_ids"] == [account["id"]]


def test_create_tax_account_name_at_max_length_accepted(client, admin_headers):
    resp = _create_tax_account_raw(client, admin_headers, name="A" * 200)
    assert resp.status_code == 201, resp.text


def test_create_tax_account_name_over_max_length_rejected(client, admin_headers):
    resp = _create_tax_account_raw(client, admin_headers, name="A" * 201)
    assert resp.status_code == 422


def test_create_tax_account_jurisdiction_over_max_length_rejected(client, admin_headers):
    resp = _create_tax_account_raw(client, admin_headers, jurisdiction="J" * 201)
    assert resp.status_code == 422


def test_update_tax_account_effective_to_before_from_rejected(client, admin_headers):
    account = _create_tax_account(
        client, admin_headers, effective_from="2026-06-01", effective_to=None
    )
    resp = client.put(
        f"/api/tax-accounts/{account['id']}",
        json={"effective_to": "2026-01-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_delete_tax_account_missing_404(client, admin_headers):
    resp = client.delete("/api/tax-accounts/999999", headers=admin_headers)
    assert resp.status_code == 404


def test_update_tax_category_missing_404(client, admin_headers):
    resp = client.put(
        "/api/tax-categories/999999", json={"name": "Ghost"}, headers=admin_headers
    )
    assert resp.status_code == 404


def test_update_tax_category_duplicate_name_conflict(client, admin_headers):
    _create_tax_category(client, admin_headers, "Existing Name")
    other = _create_tax_category(client, admin_headers, "Renamed Later")
    resp = client.put(
        f"/api/tax-categories/{other['id']}",
        json={"name": "Existing Name"},
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_delete_tax_category_missing_404(client, admin_headers):
    resp = client.delete("/api/tax-categories/999999", headers=admin_headers)
    assert resp.status_code == 404


def test_set_tax_category_accounts_missing_category_404(client, admin_headers):
    resp = client.put(
        "/api/tax-categories/999999/tax-accounts",
        json={"tax_account_ids": []},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_set_tax_category_accounts_empty_list_clears_mapping(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Clearable Mapping")
    account = _create_tax_account(client, admin_headers, name="Clearable Account")
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])

    result = _map_tax_accounts(client, admin_headers, category["id"], [])
    assert result["tax_account_ids"] == []


def test_product_with_null_tax_category_produces_zero_tax(client, admin_headers, sample_product):
    """A product can have its tax_category_id explicitly cleared to null
    (distinct from being assigned to a category with no accounts mapped).
    tax.compute_line_tax must short-circuit to zero tax with no category
    name, rather than erroring on a missing category lookup."""
    resp = client.put(
        f"/api/products/{sample_product['id']}",
        json={"tax_category_id": None},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["tax_category_id"] is None

    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    assert sale["tax_cents"] == 0
    assert sale["tax_category_name"] is None
    assert sale["tax_lines"] == []


def test_checkout_aggregates_same_tax_account_across_multiple_items(client, admin_headers):
    """aggregate_tax_breakdown must merge lines from different sale rows that
    hit the same tax_account into a single breakdown entry with a summed
    tax_cents, not one entry per line."""
    category = _create_tax_category(client, admin_headers, "Shared Jurisdiction")
    account = _create_tax_account(
        client, admin_headers, name="Shared Tax", rate_bps=1000,
        effective_from="2000-01-01", effective_to=None,
    )
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product_a = _product_with_category(
        client, admin_headers, category["id"], price_cents=1000, sku="SHARED-A"
    )
    product_b = _product_with_category(
        client, admin_headers, category["id"], price_cents=2000, sku="SHARED-B"
    )

    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "cash",
            "items": [
                {"product_id": product_a["id"], "qty": 1},
                {"product_id": product_b["id"], "qty": 1},
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    checkout = resp.json()["data"]

    assert checkout["tax_cents"] == 100 + 200
    assert len(checkout["tax_breakdown"]) == 1
    assert checkout["tax_breakdown"][0]["tax_account_name"] == "Shared Tax"
    assert checkout["tax_breakdown"][0]["tax_cents"] == 300


def _create_tax_account_raw(client, headers, **overrides):
    payload = {
        "name": "Adversarial Tax",
        "jurisdiction": "Adversarial Jurisdiction",
        "rate_bps": 100,
        "effective_from": "2026-01-01",
        "effective_to": None,
    }
    payload.update(overrides)
    return client.post("/api/tax-accounts", json=payload, headers=headers)


# ---------------------------------------------------- 7. migration ordering

LEGACY_SCHEMA_V1 = """
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
    conn = sqlite3.connect(path)
    try:
        conn.executescript(LEGACY_SCHEMA_V1)
        conn.execute(
            """INSERT INTO products (sku, name, qty, price_cents, created_at, updated_at)
               VALUES ('LEGACY-2', 'Legacy Widget Two', 50, 300, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        conn.commit()
    finally:
        conn.close()


def test_migrated_legacy_product_supports_checkout_with_correctly_joined_category(
    tmp_path, monkeypatch
):
    """The current test_migrations.py only checks that summary/history math
    stays correct for sales that predate the migration. This test goes one
    step further and exercises a brand-new checkout on a just-migrated
    legacy product, then verifies (by direct join) that
    products.tax_category_id set by _migrate_products_table genuinely
    resolves to a row _seed_tax_categories created - the concrete signal
    that would break if init_db()'s three migration steps ever ran out of
    order."""
    db_path = tmp_path / "legacy_checkout.db"
    _build_legacy_db(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", "admin")
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("LOTSPOT_POS_API_KEY", "test-pos-key")

    from fastapi.testclient import TestClient

    from app import create_app

    with TestClient(create_app()) as client:
        resp = client.post(
            "/api/auth/login", json={"username": "admin", "password": "testpass123"}
        )
        assert resp.status_code == 200, resp.text
        headers = {"Authorization": f"Bearer {resp.json()['data']['token']}"}

        products = client.get("/api/products", headers=headers).json()["data"]
        legacy_product = next(p for p in products if p["sku"] == "LEGACY-2")

        resp = client.post(
            "/api/pos/checkout",
            json={
                "items": [{"product_id": legacy_product["id"], "qty": 1}],
                "payment_method": "cash",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        checkout = resp.json()["data"]
        assert checkout["tax_cents"] == 0  # General Merchandise has no accounts mapped yet

    conn = db.connect()
    try:
        joined = conn.execute(
            """SELECT tax_categories.name FROM products
               JOIN tax_categories ON products.tax_category_id = tax_categories.id
               WHERE products.sku = 'LEGACY-2'"""
        ).fetchone()
        assert joined is not None, "product.tax_category_id does not resolve to a real category"
        assert joined["name"] == db.GENERAL_MERCHANDISE_CATEGORY
    finally:
        conn.close()


def test_migration_seeds_categories_additively_around_preexisting_custom_row(
    tmp_path, monkeypatch
):
    """Simulates a DB where a prior partial/custom deploy already created the
    tax_categories table (so init_db()'s CREATE TABLE IF NOT EXISTS is a
    no-op for it) with its own row already in it. _seed_tax_categories must
    not clobber or duplicate that row, and _migrate_products_table must
    still correctly resolve General Merchandise afterwards."""
    db_path = tmp_path / "legacy_custom_category.db"
    _build_legacy_db(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """CREATE TABLE tax_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO tax_categories (name, created_at) VALUES (?, ?)",
            ("Local Config Category", "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("LOTSPOT_DB", str(db_path))

    import db as db_module

    db_module.init_db()

    conn = db_module.connect()
    try:
        names = {row["name"] for row in conn.execute("SELECT name FROM tax_categories")}
        assert names == {"Local Config Category"} | set(db_module.TAX_CATEGORY_SEED_NAMES)

        product = conn.execute(
            "SELECT * FROM products WHERE sku = 'LEGACY-2'"
        ).fetchone()
        general = conn.execute(
            "SELECT id FROM tax_categories WHERE name = ?",
            (db_module.GENERAL_MERCHANDISE_CATEGORY,),
        ).fetchone()
        assert product["tax_category_id"] == general["id"]
    finally:
        conn.close()
