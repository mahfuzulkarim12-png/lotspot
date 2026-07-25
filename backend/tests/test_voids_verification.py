"""Independent tester-lane verification of the voided-sales feature (app.py's
"voided sales" section: POST /api/sales/{id}/void, POST
/api/sales/transactions/{transaction_id}/void, GET /api/sales/voided/receipts,
GET /api/sales/voided/items).

This file is deliberately separate from tests/test_voids.py (written by the
coder lane) so coverage isn't just re-running the implementer's own
assumptions. It targets scenarios the acceptance criteria call out that
weren't exercised elsewhere: field leaks on the two POST /void responses
themselves, a combined multi-receipt scenario (active + partial + fully
voided all at once), the single-item-cart edge case, and that q/date
filters actually narrow results rather than merely being accepted.
"""

from datetime import date

from tests.test_store_id_hq_sync import _assert_no_internal_fields

TODAY = date.today().isoformat()


def _make_product(client, admin_headers, sku, price_cents=500, qty=20):
    resp = client.post(
        "/api/products",
        json={"sku": sku, "name": f"Widget {sku}", "qty": qty, "price_cents": price_cents},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


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


# --------------------------------------------------------------- empty state


def test_empty_db_both_voided_endpoints_return_empty_list(client, admin_headers):
    receipts = client.get("/api/sales/voided/receipts", headers=admin_headers)
    items = client.get("/api/sales/voided/items", headers=admin_headers)
    assert receipts.status_code == 200
    assert items.status_code == 200
    assert receipts.json() == {"success": True, "data": [], "error": None}
    assert items.json() == {"success": True, "data": [], "error": None}


# --------------------------------------------- field leaks on POST responses


def test_void_sale_response_never_leaks_internal_fields(client, admin_headers):
    product = _make_product(client, admin_headers, "VVOID-LEAK-ITEM")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    sale_id = checkout["line_items"][0]["id"]

    resp = _void_item(client, admin_headers, sale_id, reason="leak check")
    assert resp.status_code == 200, resp.text
    _assert_no_internal_fields(resp.json(), "POST /api/sales/{id}/void")


def test_void_receipt_response_never_leaks_internal_fields(client, admin_headers):
    coke = _make_product(client, admin_headers, "VVOID-LEAK-COKE")
    chips = _make_product(client, admin_headers, "VVOID-LEAK-CHIPS")
    checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])

    resp = _void_receipt(client, admin_headers, checkout["transaction_id"], reason="leak check")
    assert resp.status_code == 200, resp.text
    _assert_no_internal_fields(resp.json(), "POST /api/sales/transactions/{id}/void")


# ------------------------------------------------- single-item cart edge case


def test_voiding_sole_item_of_single_item_cart_promotes_receipt_to_fully_voided(
    client, admin_headers
):
    """A single-item cart has only one line item. Voiding that one item via
    the per-sale endpoint (not the per-receipt endpoint) makes every item on
    the receipt voided, so it must surface via /voided/receipts and must NOT
    appear via /voided/items (that endpoint explicitly excludes items whose
    receipt is fully voided)."""
    product = _make_product(client, admin_headers, "VVOID-SOLO")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    sale_id = checkout["line_items"][0]["id"]

    resp = _void_item(client, admin_headers, sale_id, reason="solo void")
    assert resp.status_code == 200, resp.text

    receipts = client.get("/api/sales/voided/receipts", headers=admin_headers).json()["data"]
    items = client.get("/api/sales/voided/items", headers=admin_headers).json()["data"]

    assert [r["transaction_id"] for r in receipts] == [checkout["transaction_id"]]
    assert items == []


# ------------------------------------------ combined multi-receipt scenario


def test_mixed_active_partial_and_fully_voided_receipts_are_correctly_bucketed(
    client, admin_headers
):
    """Three receipts in play at once: one left fully active, one partially
    voided (one of two items voided individually), and one fully voided via
    the receipt-level endpoint. /voided/receipts must show exactly the fully
    voided one (with correct aggregates); /voided/items must show exactly the
    partially voided receipt's voided item, and nothing from the fully voided
    receipt or the untouched receipt."""
    active_product = _make_product(client, admin_headers, "MIX-ACTIVE")
    active_checkout = _checkout(client, admin_headers, [(active_product["id"], 1)])

    coke = _make_product(client, admin_headers, "MIX-PARTIAL-COKE", price_cents=300)
    chips = _make_product(client, admin_headers, "MIX-PARTIAL-CHIPS", price_cents=150)
    partial_checkout = _checkout(client, admin_headers, [(coke["id"], 1), (chips["id"], 1)])
    partial_coke_sale = next(
        i for i in partial_checkout["line_items"] if i["sku"] == "MIX-PARTIAL-COKE"
    )
    _void_item(client, admin_headers, partial_coke_sale["id"], reason="partial void")

    full_a = _make_product(client, admin_headers, "MIX-FULL-A", price_cents=200)
    full_b = _make_product(client, admin_headers, "MIX-FULL-B", price_cents=400)
    full_checkout = _checkout(
        client, admin_headers, [(full_a["id"], 2), (full_b["id"], 1)]
    )
    full_resp = _void_receipt(
        client, admin_headers, full_checkout["transaction_id"], reason="full void"
    )
    assert full_resp.status_code == 200, full_resp.text

    receipts = client.get("/api/sales/voided/receipts", headers=admin_headers).json()["data"]
    assert [r["transaction_id"] for r in receipts] == [full_checkout["transaction_id"]]
    receipt = receipts[0]
    assert receipt["item_count"] == 2
    assert receipt["subtotal_cents"] == 2 * 200 + 400
    assert receipt["grand_total_cents"] == receipt["subtotal_cents"] + receipt["tax_cents"]
    assert receipt["cashier"] == "admin"
    assert receipt["payment_method"] == "cash"
    assert {li["sku"] for li in receipt["line_items"]} == {"MIX-FULL-A", "MIX-FULL-B"}

    items = client.get("/api/sales/voided/items", headers=admin_headers).json()["data"]
    assert [i["sku"] for i in items] == ["MIX-PARTIAL-COKE"]

    # sanity: the untouched receipt never shows up anywhere
    all_transaction_ids_seen = {r["transaction_id"] for r in receipts} | {
        i["transaction_id"] for i in items
    }
    assert active_checkout["transaction_id"] not in all_transaction_ids_seen


# --------------------------------------------------- filters actually narrow


def test_date_range_and_q_filters_actually_narrow_results_not_just_accepted(
    client, admin_headers
):
    """Guards against an implementation that accepts start/end/q as params
    but silently ignores them. Creates two distinct fully-voided receipts
    with different transaction ids and confirms narrowing the query by q
    excludes the non-matching one, and narrowing the query by an unrelated
    date range excludes both (since both are dated 'today' by checkout)."""
    first_product = _make_product(client, admin_headers, "FILTER-ONE")
    second_product = _make_product(client, admin_headers, "FILTER-TWO")
    first_checkout = _checkout(client, admin_headers, [(first_product["id"], 1)])
    second_checkout = _checkout(client, admin_headers, [(second_product["id"], 1)])
    _void_receipt(client, admin_headers, first_checkout["transaction_id"])
    _void_receipt(client, admin_headers, second_checkout["transaction_id"])

    unfiltered = client.get("/api/sales/voided/receipts", headers=admin_headers).json()["data"]
    assert {r["transaction_id"] for r in unfiltered} == {
        first_checkout["transaction_id"],
        second_checkout["transaction_id"],
    }

    needle = first_checkout["transaction_id"][:8]
    q_filtered = client.get(
        "/api/sales/voided/receipts", params={"q": needle}, headers=admin_headers
    ).json()["data"]
    assert [r["transaction_id"] for r in q_filtered] == [first_checkout["transaction_id"]]

    unrelated_range = client.get(
        "/api/sales/voided/receipts",
        params={"start": "2000-01-01", "end": "2000-01-02"},
        headers=admin_headers,
    ).json()["data"]
    assert unrelated_range == []

    today_range = client.get(
        "/api/sales/voided/receipts",
        params={"start": TODAY, "end": TODAY},
        headers=admin_headers,
    ).json()["data"]
    assert {r["transaction_id"] for r in today_range} == {
        first_checkout["transaction_id"],
        second_checkout["transaction_id"],
    }


def test_empty_q_string_is_treated_as_no_filter(client, admin_headers):
    product = _make_product(client, admin_headers, "EMPTYQ")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    _void_receipt(client, admin_headers, checkout["transaction_id"])

    resp = client.get(
        "/api/sales/voided/receipts", params={"q": ""}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert [r["transaction_id"] for r in resp.json()["data"]] == [checkout["transaction_id"]]


# --------------------------------------------------------- request body edges


def test_void_sale_accepts_omitted_body_defaulting_reason_to_none(client, admin_headers):
    product = _make_product(client, admin_headers, "OMITBODY")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    sale_id = checkout["line_items"][0]["id"]

    resp = client.post(
        f"/api/sales/{sale_id}/void", json={}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["void_reason"] is None


def test_void_sale_reason_at_max_length_accepted_over_max_rejected(client, admin_headers):
    """VoidSaleIn.reason has max_length=VOID_REASON_MAX (200); exercise both
    edges of that boundary rather than assuming Pydantic enforces it."""
    from models import VOID_REASON_MAX

    product_ok = _make_product(client, admin_headers, "REASONLEN-OK")
    checkout_ok = _checkout(client, admin_headers, [(product_ok["id"], 1)])
    sale_id_ok = checkout_ok["line_items"][0]["id"]
    at_max = "x" * VOID_REASON_MAX
    resp_ok = _void_item(client, admin_headers, sale_id_ok, reason=at_max)
    assert resp_ok.status_code == 200, resp_ok.text
    assert resp_ok.json()["data"]["void_reason"] == at_max

    product_over = _make_product(client, admin_headers, "REASONLEN-OVER")
    checkout_over = _checkout(client, admin_headers, [(product_over["id"], 1)])
    sale_id_over = checkout_over["line_items"][0]["id"]
    over_max = "x" * (VOID_REASON_MAX + 1)
    resp_over = _void_item(client, admin_headers, sale_id_over, reason=over_max)
    assert resp_over.status_code == 422


# ------------------------------------------------------------- unauthenticated


def test_all_four_void_endpoints_reject_unauthenticated_requests(client, admin_headers):
    product = _make_product(client, admin_headers, "AUTH-CHECK")
    checkout = _checkout(client, admin_headers, [(product["id"], 1)])
    sale_id = checkout["line_items"][0]["id"]

    assert client.post(f"/api/sales/{sale_id}/void", json={"reason": None}).status_code == 401
    assert (
        client.post(
            f"/api/sales/transactions/{checkout['transaction_id']}/void",
            json={"reason": None},
        ).status_code
        == 401
    )
    assert client.get("/api/sales/voided/receipts").status_code == 401
    assert client.get("/api/sales/voided/items").status_code == 401
