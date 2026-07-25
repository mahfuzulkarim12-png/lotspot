"""Voided sales: void a single line item or a whole receipt, then browse
fully-voided receipts (with line items) and individually-voided items
separately. See app.py's "voided sales" section and db.py's sales schema
(cashier/voided_at/void_reason/voided_by columns)."""

from datetime import date

import db
from tests.test_store_id_hq_sync import _assert_no_internal_fields

TODAY = date.today().isoformat()


def _checkout(client, admin_headers, product_ids_and_qty, payment_method="cash"):
    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": payment_method,
            "items": [
                {"product_id": product_id, "qty": qty}
                for product_id, qty in product_ids_and_qty
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _make_product(client, admin_headers, sku, price_cents=500, qty=20):
    resp = client.post(
        "/api/products",
        json={"sku": sku, "name": f"Widget {sku}", "qty": qty, "price_cents": price_cents},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _void_item(client, admin_headers, sale_id, reason=None):
    return client.post(
        f"/api/sales/{sale_id}/void", json={"reason": reason}, headers=admin_headers
    )


def _void_receipt(client, admin_headers, transaction_id, reason=None):
    return client.post(
        f"/api/sales/transactions/{transaction_id}/void",
        json={"reason": reason},
        headers=admin_headers,
    )


def _backdate_transaction(transaction_id: str, day: str) -> None:
    """Rewrites sold_at for every line item of a transaction to a fixed day,
    for exercising date-range filtering deterministically."""
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE sales SET sold_at = ? WHERE transaction_id = ?",
            (f"{day}T09:00:00", transaction_id),
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------- voiding a line item


def test_void_sale_marks_single_line_item_voided(client, admin_headers):
    coke = _make_product(client, admin_headers, "VOID-COKE")
    chips = _make_product(client, admin_headers, "VOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    target = next(item for item in checkout["line_items"] if item["sku"] == "VOID-COKE")

    resp = _void_item(client, admin_headers, target["id"], reason="Customer changed mind")
    assert resp.status_code == 200, resp.text
    voided = resp.json()["data"]
    assert voided["voided_at"] is not None
    assert voided["voided_by"] == "admin"
    assert voided["void_reason"] == "Customer changed mind"


def test_void_sale_unknown_id_404(client, admin_headers):
    resp = _void_item(client, admin_headers, 999999)
    assert resp.status_code == 404


def test_void_sale_already_voided_409(client, admin_headers):
    product = _make_product(client, admin_headers, "VOID-DOUBLE")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    sale_id = checkout["line_items"][0]["id"]

    assert _void_item(client, admin_headers, sale_id).status_code == 200
    resp = _void_item(client, admin_headers, sale_id)
    assert resp.status_code == 409


def test_void_sale_requires_auth(client, admin_headers):
    product = _make_product(client, admin_headers, "VOID-NOAUTH")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    sale_id = checkout["line_items"][0]["id"]

    resp = client.post(f"/api/sales/{sale_id}/void", json={"reason": None})
    assert resp.status_code == 401


# ---------------------------------------------------------- voiding a receipt


def test_void_receipt_marks_every_active_line_item_voided(client, admin_headers):
    coke = _make_product(client, admin_headers, "RVOID-COKE")
    chips = _make_product(client, admin_headers, "RVOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 2), (chips["id"], 3)])

    resp = _void_receipt(client, admin_headers, checkout["transaction_id"], reason="Till error")
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["transaction_id"] == checkout["transaction_id"]
    assert len(body["line_items"]) == 2
    assert all(item["voided_at"] is not None for item in body["line_items"])
    assert all(item["void_reason"] == "Till error" for item in body["line_items"])
    assert all(item["voided_by"] == "admin" for item in body["line_items"])


def test_void_receipt_unknown_transaction_404(client, admin_headers):
    resp = _void_receipt(client, admin_headers, "does-not-exist")
    assert resp.status_code == 404


def test_void_receipt_already_fully_voided_409(client, admin_headers):
    product = _make_product(client, admin_headers, "RVOID-DOUBLE")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])

    assert _void_receipt(client, admin_headers, checkout["transaction_id"]).status_code == 200
    resp = _void_receipt(client, admin_headers, checkout["transaction_id"])
    assert resp.status_code == 409


def test_void_receipt_only_touches_still_active_items(client, admin_headers):
    """A line item voided individually keeps its original void_reason/voided_at
    when the rest of the receipt is later voided as a whole."""
    coke = _make_product(client, admin_headers, "MIXED-COKE")
    chips = _make_product(client, admin_headers, "MIXED-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    coke_sale = next(item for item in checkout["line_items"] if item["sku"] == "MIXED-COKE")

    _void_item(client, admin_headers, coke_sale["id"], reason="Item voided first")
    resp = _void_receipt(
        client, admin_headers, checkout["transaction_id"], reason="Receipt voided second"
    )
    assert resp.status_code == 200, resp.text
    by_sku = {item["sku"]: item for item in resp.json()["data"]["line_items"]}
    assert by_sku["MIXED-COKE"]["void_reason"] == "Item voided first"
    assert by_sku["MIXED-CHIPS"]["void_reason"] == "Receipt voided second"


def test_void_receipt_requires_auth(client, admin_headers):
    product = _make_product(client, admin_headers, "RVOID-NOAUTH")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])

    resp = client.post(
        f"/api/sales/transactions/{checkout['transaction_id']}/void",
        json={"reason": None},
    )
    assert resp.status_code == 401


# --------------------------------------------------- listing voided receipts


def test_voided_receipts_empty_state(client, admin_headers):
    resp = client.get("/api/sales/voided/receipts", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_voided_receipts_returns_single_fully_voided_receipt_with_line_items(
    client, admin_headers
):
    coke = _make_product(client, admin_headers, "LISTRVOID-COKE", price_cents=250)
    chips = _make_product(client, admin_headers, "LISTRVOID-CHIPS", price_cents=175)
    checkout = _checkout(client, admin_headers, [(coke["id"], 2), (chips["id"], 1)])
    _void_receipt(client, admin_headers, checkout["transaction_id"])

    resp = client.get("/api/sales/voided/receipts", headers=admin_headers)
    assert resp.status_code == 200
    receipts = resp.json()["data"]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["transaction_id"] == checkout["transaction_id"]
    assert receipt["cashier"] == "admin"
    assert receipt["payment_method"] == "cash"
    assert receipt["item_count"] == 2
    assert receipt["subtotal_cents"] == 2 * 250 + 175
    assert receipt["grand_total_cents"] == receipt["subtotal_cents"] + receipt["tax_cents"]
    assert {item["sku"] for item in receipt["line_items"]} == {
        "LISTRVOID-COKE",
        "LISTRVOID-CHIPS",
    }


def test_voided_receipts_returns_multiple_voided_receipts(client, admin_headers):
    first = _make_product(client, admin_headers, "MULTIRVOID-1")
    second = _make_product(client, admin_headers, "MULTIRVOID-2")
    checkout_a = _checkout(client, admin_headers, [(first["id"], 1)])
    checkout_b = _checkout(client, admin_headers, [(second["id"], 1)])
    _void_receipt(client, admin_headers, checkout_a["transaction_id"])
    _void_receipt(client, admin_headers, checkout_b["transaction_id"])

    resp = client.get("/api/sales/voided/receipts", headers=admin_headers)
    assert resp.status_code == 200
    transaction_ids = {r["transaction_id"] for r in resp.json()["data"]}
    assert transaction_ids == {checkout_a["transaction_id"], checkout_b["transaction_id"]}


def test_voided_receipts_excludes_partially_voided_receipts(client, admin_headers):
    coke = _make_product(client, admin_headers, "PARTIALRVOID-COKE")
    chips = _make_product(client, admin_headers, "PARTIALRVOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    coke_sale = next(i for i in checkout["line_items"] if i["sku"] == "PARTIALRVOID-COKE")
    _void_item(client, admin_headers, coke_sale["id"])

    resp = client.get("/api/sales/voided/receipts", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_voided_receipts_filters_by_date_range(client, admin_headers):
    product = _make_product(client, admin_headers, "DATERVOID")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    _void_receipt(client, admin_headers, checkout["transaction_id"])
    _backdate_transaction(checkout["transaction_id"], "2020-01-01")

    resp = client.get(
        "/api/sales/voided/receipts",
        params={"start": TODAY, "end": TODAY},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []

    resp = client.get(
        "/api/sales/voided/receipts",
        params={"start": "2020-01-01", "end": "2020-01-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert [r["transaction_id"] for r in resp.json()["data"]] == [checkout["transaction_id"]]


def test_voided_receipts_searches_by_transaction_id_substring(client, admin_headers):
    product = _make_product(client, admin_headers, "SEARCHRVOID")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    _void_receipt(client, admin_headers, checkout["transaction_id"])
    needle = checkout["transaction_id"][:8]

    resp = client.get(
        "/api/sales/voided/receipts", params={"q": needle}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert [r["transaction_id"] for r in resp.json()["data"]] == [checkout["transaction_id"]]

    resp = client.get(
        "/api/sales/voided/receipts", params={"q": "no-such-receipt"}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_voided_receipts_range_requires_start_and_end_together(client, admin_headers):
    resp = client.get(
        "/api/sales/voided/receipts", params={"start": TODAY}, headers=admin_headers
    )
    assert resp.status_code == 422


def test_voided_receipts_range_rejects_end_before_start(client, admin_headers):
    resp = client.get(
        "/api/sales/voided/receipts",
        params={"start": TODAY, "end": "2000-01-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_voided_receipts_requires_auth(client):
    resp = client.get("/api/sales/voided/receipts")
    assert resp.status_code == 401


def test_voided_receipts_never_leaks_internal_fields(client, admin_headers):
    product = _make_product(client, admin_headers, "LEAKRVOID")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    _void_receipt(client, admin_headers, checkout["transaction_id"])

    resp = client.get("/api/sales/voided/receipts", headers=admin_headers)
    _assert_no_internal_fields(resp.json())


# ------------------------------------------------------ listing voided items


def test_voided_items_empty_state(client, admin_headers):
    resp = client.get("/api/sales/voided/items", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_voided_items_returns_single_partially_voided_item(client, admin_headers):
    coke = _make_product(client, admin_headers, "LISTIVOID-COKE", price_cents=299)
    chips = _make_product(client, admin_headers, "LISTIVOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    coke_sale = next(i for i in checkout["line_items"] if i["sku"] == "LISTIVOID-COKE")
    _void_item(client, admin_headers, coke_sale["id"], reason="Damaged")

    resp = client.get("/api/sales/voided/items", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert len(items) == 1
    assert items[0]["sku"] == "LISTIVOID-COKE"
    assert items[0]["product_name"] == "Widget LISTIVOID-COKE"
    assert items[0]["unit_price_cents"] == 299
    assert items[0]["void_reason"] == "Damaged"
    assert items[0]["transaction_id"] == checkout["transaction_id"]
    assert items[0]["voided_at"] is not None


def test_voided_items_returns_multiple_voided_items(client, admin_headers):
    coke = _make_product(client, admin_headers, "MULTIIVOID-COKE")
    chips = _make_product(client, admin_headers, "MULTIIVOID-CHIPS")
    water = _make_product(client, admin_headers, "MULTIIVOID-WATER")
    checkout = _checkout(
        client, admin_headers, [(coke["id"], 1), (chips["id"], 1), (water["id"], 1)]
    )
    coke_sale = next(i for i in checkout["line_items"] if i["sku"] == "MULTIIVOID-COKE")
    chips_sale = next(i for i in checkout["line_items"] if i["sku"] == "MULTIIVOID-CHIPS")
    _void_item(client, admin_headers, coke_sale["id"])
    _void_item(client, admin_headers, chips_sale["id"])

    resp = client.get("/api/sales/voided/items", headers=admin_headers)
    assert resp.status_code == 200
    skus = {item["sku"] for item in resp.json()["data"]}
    assert skus == {"MULTIIVOID-COKE", "MULTIIVOID-CHIPS"}


def test_voided_items_excludes_items_from_a_fully_voided_receipt(client, admin_headers):
    product = _make_product(client, admin_headers, "FULLIVOID")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    _void_receipt(client, admin_headers, checkout["transaction_id"])

    resp = client.get("/api/sales/voided/items", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_voided_items_filters_by_date_range(client, admin_headers):
    coke = _make_product(client, admin_headers, "DATEIVOID-COKE")
    chips = _make_product(client, admin_headers, "DATEIVOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    coke_sale = next(i for i in checkout["line_items"] if i["sku"] == "DATEIVOID-COKE")
    _void_item(client, admin_headers, coke_sale["id"])
    _backdate_transaction(checkout["transaction_id"], "2020-01-01")

    resp = client.get(
        "/api/sales/voided/items",
        params={"start": TODAY, "end": TODAY},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []

    resp = client.get(
        "/api/sales/voided/items",
        params={"start": "2020-01-01", "end": "2020-01-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert [item["sku"] for item in resp.json()["data"]] == ["DATEIVOID-COKE"]


def test_voided_items_searches_by_transaction_id_substring(client, admin_headers):
    coke = _make_product(client, admin_headers, "SEARCHIVOID-COKE")
    chips = _make_product(client, admin_headers, "SEARCHIVOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    coke_sale = next(i for i in checkout["line_items"] if i["sku"] == "SEARCHIVOID-COKE")
    _void_item(client, admin_headers, coke_sale["id"])
    needle = checkout["transaction_id"][:8]

    resp = client.get(
        "/api/sales/voided/items", params={"q": needle}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert [item["sku"] for item in resp.json()["data"]] == ["SEARCHIVOID-COKE"]

    resp = client.get(
        "/api/sales/voided/items", params={"q": "no-such-receipt"}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_voided_items_requires_auth(client):
    resp = client.get("/api/sales/voided/items")
    assert resp.status_code == 401


def test_voided_items_never_leaks_internal_fields(client, admin_headers):
    coke = _make_product(client, admin_headers, "LEAKIVOID-COKE")
    chips = _make_product(client, admin_headers, "LEAKIVOID-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    coke_sale = next(i for i in checkout["line_items"] if i["sku"] == "LEAKIVOID-COKE")
    _void_item(client, admin_headers, coke_sale["id"])

    resp = client.get("/api/sales/voided/items", headers=admin_headers)
    _assert_no_internal_fields(resp.json())


# --------------------------------------------------------------- cashier tag


def test_manual_sale_and_checkout_stamp_cashier_with_admin_username(
    client, admin_headers, sample_product
):
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["cashier"] == "admin"

    checkout = _checkout(client, admin_headers, [(sample_product["id"], 1)])
    assert checkout["line_items"][0]["cashier"] == "admin"


def test_external_pos_terminal_sale_leaves_cashier_null(client, sample_product):
    from tests.conftest import TEST_POS_API_KEY

    resp = client.post(
        "/api/pos/sales",
        json={"sku": sample_product["sku"], "qty": 1},
        headers={"X-API-Key": TEST_POS_API_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["cashier"] is None
