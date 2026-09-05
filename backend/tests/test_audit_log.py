"""Every privileged mutation and login attempt must append exactly one row
to audit_log (PCI Req 10), and app code must never UPDATE or DELETE it."""

import re
from pathlib import Path

import db

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _audit_rows(action: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
        ).fetchall()
        return [db.row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _make_product(client, admin_headers, sku="AUDIT-1", price_cents=300, qty=10):
    resp = client.post(
        "/api/products",
        json={"sku": sku, "name": f"Widget {sku}", "qty": qty, "price_cents": price_cents},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_login_success_writes_audit_row(client):
    from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USER

    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    rows = _audit_rows("auth.login_success")
    assert len(rows) == 1
    assert rows[0]["actor"] == TEST_ADMIN_USER
    assert rows[0]["entity_type"] == "auth"


def test_login_failure_writes_audit_row(client):
    from tests.conftest import TEST_ADMIN_USER

    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    rows = _audit_rows("auth.login_failure")
    assert len(rows) == 1
    assert rows[0]["actor"] == TEST_ADMIN_USER


def test_product_create_writes_audit_row(client, admin_headers):
    product = _make_product(client, admin_headers)
    rows = _audit_rows("product.create")
    assert len(rows) == 1
    assert rows[0]["entity_type"] == "product"
    assert rows[0]["entity_id"] == str(product["id"])


def test_product_update_writes_audit_row(client, admin_headers):
    product = _make_product(client, admin_headers)
    resp = client.put(
        f"/api/products/{product['id']}", json={"qty": 5}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    rows = _audit_rows("product.update")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == str(product["id"])


def test_product_delete_writes_audit_row(client, admin_headers):
    product = _make_product(client, admin_headers)
    resp = client.delete(f"/api/products/{product['id']}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    rows = _audit_rows("product.delete")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == str(product["id"])


def test_tax_account_crud_writes_audit_rows(client, admin_headers):
    resp = client.post(
        "/api/tax-accounts",
        json={"name": "State Tax", "rate_bps": 700, "effective_from": "2026-01-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    account = resp.json()["data"]
    assert len(_audit_rows("tax_account.create")) == 1

    resp = client.put(
        f"/api/tax-accounts/{account['id']}",
        json={"rate_bps": 800},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(_audit_rows("tax_account.update")) == 1

    resp = client.delete(f"/api/tax-accounts/{account['id']}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert len(_audit_rows("tax_account.delete")) == 1


def test_tax_category_crud_and_set_accounts_write_audit_rows(client, admin_headers):
    resp = client.post(
        "/api/tax-categories", json={"name": "Alcohol"}, headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    category = resp.json()["data"]
    assert len(_audit_rows("tax_category.create")) == 1

    resp = client.put(
        f"/api/tax-categories/{category['id']}",
        json={"name": "Alcohol & Tobacco"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(_audit_rows("tax_category.update")) == 1

    resp = client.post(
        "/api/tax-accounts",
        json={"name": "City Tax", "rate_bps": 200, "effective_from": "2026-01-01"},
        headers=admin_headers,
    )
    account = resp.json()["data"]

    resp = client.put(
        f"/api/tax-categories/{category['id']}/tax-accounts",
        json={"tax_account_ids": [account["id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(_audit_rows("tax_category.set_accounts")) == 1

    resp = client.delete(f"/api/tax-categories/{category['id']}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert len(_audit_rows("tax_category.delete")) == 1


def test_employee_create_writes_audit_row(client, admin_headers):
    resp = client.post(
        "/api/employees",
        json={"name": "Jamie Rivera", "pin": "4321"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    employee = resp.json()["data"]
    rows = _audit_rows("employee.create")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == str(employee["id"])


def test_void_sale_writes_audit_row(client, admin_headers):
    product = _make_product(client, admin_headers)
    resp = client.post(
        "/api/sales",
        json={
            "product_id": product["id"],
            "qty": 1,
            "unit_price_cents": product["price_cents"],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    resp = client.post(
        f"/api/sales/{sale['id']}/void",
        json={"reason": "test"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = _audit_rows("sale.void")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == str(sale["id"])


def test_void_receipt_writes_exactly_one_audit_row_for_multi_item_receipt(
    client, admin_headers
):
    product_a = _make_product(client, admin_headers, sku="AUDIT-A")
    product_b = _make_product(client, admin_headers, sku="AUDIT-B")
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
    transaction_id = resp.json()["data"]["transaction_id"]

    resp = client.post(
        f"/api/sales/transactions/{transaction_id}/void",
        json={"reason": "test"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    # One privileged action == one audit row, even though it voids two
    # underlying sales line items.
    rows = _audit_rows("receipt.void")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == transaction_id


def test_locked_out_login_attempt_writes_audit_row(client):
    from auth import LOGIN_MAX_ATTEMPTS
    from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USER

    for _ in range(LOGIN_MAX_ATTEMPTS):
        client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USER, "password": "wrong-password"},
        )

    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 429
    rows = _audit_rows("auth.login_locked")
    assert len(rows) == 1
    assert rows[0]["actor"] == TEST_ADMIN_USER


def test_audit_log_never_updated_or_deleted_from_app_code():
    pattern = re.compile(r"(?i)\b(UPDATE|DELETE\s+FROM)\s+audit_log\b")
    for filename in ("app.py", "db.py"):
        source = (BACKEND_DIR / filename).read_text()
        assert not pattern.search(source), f"{filename} mutates audit_log"
