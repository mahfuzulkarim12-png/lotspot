"""Independent exploratory tests for the audit-log + auth-hardening work
(bohor/coder/44-70508e5b), targeting edge cases not already covered by
test_audit_log.py / test_auth.py / test_login_rate_limit.py:

1. A failed mutation (duplicate SKU -> 409 IntegrityError) must not leave a
   dangling audit row, since the row is only meaningful if the mutation it
   describes actually committed.
2. Voiding a receipt that already has one line item voided must write
   exactly one audit row and must not disturb the already-voided line's
   void metadata (voided_by/void_reason/voided_at).
3. Once a key is locked out, neither a wrong-password nor a right-password
   attempt should ever reach password verification: both must 429 and
   write only auth.login_locked (never auth.login_success/login_failure).
"""

import db


def _audit_rows(action: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
        ).fetchall()
        return [db.row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _make_product(client, admin_headers, sku, price_cents=300, qty=10):
    resp = client.post(
        "/api/products",
        json={"sku": sku, "name": f"Widget {sku}", "qty": qty, "price_cents": price_cents},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_failed_product_update_writes_zero_audit_rows(client, admin_headers):
    """A PUT that fails with 409 (duplicate SKU collision) must not commit
    the UPDATE, so record_audit must never fire for it either."""
    _make_product(client, admin_headers, sku="DUP-SKU")
    victim = _make_product(client, admin_headers, sku="VICTIM-SKU")

    before = len(_audit_rows("product.update"))

    resp = client.put(
        f"/api/products/{victim['id']}",
        json={"sku": "DUP-SKU"},
        headers=admin_headers,
    )
    assert resp.status_code == 409, resp.text

    after = _audit_rows("product.update")
    assert len(after) == before, (
        "a failed (409) product update wrote an audit row for a mutation "
        "that never committed"
    )


def test_failed_product_create_writes_zero_audit_rows(client, admin_headers):
    """Same failure-mode check on the create path: INSERT raises
    IntegrityError before record_audit runs, so no row should appear."""
    _make_product(client, admin_headers, sku="ONCE-ONLY")

    before = len(_audit_rows("product.create"))
    resp = client.post(
        "/api/products",
        json={"sku": "ONCE-ONLY", "name": "Duplicate", "qty": 1, "price_cents": 100},
        headers=admin_headers,
    )
    assert resp.status_code == 409, resp.text

    after = _audit_rows("product.create")
    assert len(after) == before


def test_void_receipt_already_partially_voided_writes_one_row_for_remaining_items(
    client, admin_headers
):
    product_a = _make_product(client, admin_headers, sku="PART-A")
    product_b = _make_product(client, admin_headers, sku="PART-B")

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
    transaction_id = checkout["transaction_id"]
    sale_a_id, sale_b_id = (item["id"] for item in checkout["line_items"])

    # Void just the first line item directly, simulating a receipt that is
    # already partially voided before the whole-receipt void is attempted.
    resp = client.post(
        f"/api/sales/{sale_a_id}/void",
        json={"reason": "line-item correction"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    conn = db.connect()
    try:
        line_a_before = db.row_to_dict(
            conn.execute("SELECT * FROM sales WHERE id = ?", (sale_a_id,)).fetchone()
        )
    finally:
        conn.close()

    resp = client.post(
        f"/api/sales/transactions/{transaction_id}/void",
        json={"reason": "whole receipt refund"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    # Exactly one receipt.void row for this call, on top of the sale.void
    # row already written by the earlier single-item void.
    receipt_void_rows = _audit_rows("receipt.void")
    assert len(receipt_void_rows) == 1
    assert receipt_void_rows[0]["entity_id"] == transaction_id

    conn = db.connect()
    try:
        line_a_after = db.row_to_dict(
            conn.execute("SELECT * FROM sales WHERE id = ?", (sale_a_id,)).fetchone()
        )
        line_b_after = db.row_to_dict(
            conn.execute("SELECT * FROM sales WHERE id = ?", (sale_b_id,)).fetchone()
        )
    finally:
        conn.close()

    # The already-voided line item's void metadata must be untouched by the
    # later receipt-level void (its WHERE voided_at IS NULL clause should
    # exclude it).
    assert line_a_after["void_reason"] == line_a_before["void_reason"] == "line-item correction"
    assert line_a_after["voided_at"] == line_a_before["voided_at"]

    # The still-active line item should now be voided with the receipt's
    # reason.
    assert line_b_after["voided_at"] is not None
    assert line_b_after["void_reason"] == "whole receipt refund"


def test_lockout_blocks_correct_and_incorrect_password_without_verifying(client):
    """Once a key is locked, neither branch of verify_password should ever
    run: both a wrong-password and a right-password attempt must 429 and
    write only auth.login_locked, never auth.login_success/login_failure."""
    from auth import LOGIN_MAX_ATTEMPTS
    from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USER

    for _ in range(LOGIN_MAX_ATTEMPTS):
        client.post(
            "/api/auth/login",
            json={"username": TEST_ADMIN_USER, "password": "wrong-password"},
        )

    failures_before = len(_audit_rows("auth.login_failure"))
    successes_before = len(_audit_rows("auth.login_success"))

    resp_wrong = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": "still-wrong"},
    )
    resp_right = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )

    assert resp_wrong.status_code == 429, resp_wrong.text
    assert resp_right.status_code == 429, resp_right.text

    # Neither attempt reached the password-verification/audit branch.
    assert len(_audit_rows("auth.login_failure")) == failures_before
    assert len(_audit_rows("auth.login_success")) == successes_before
    assert len(_audit_rows("auth.login_locked")) == 2
