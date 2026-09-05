from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from tests.conftest import TEST_POS_API_KEY


def test_pos_sale_with_valid_key(client, admin_headers, sample_product):
    resp = client.post(
        "/api/pos/sales",
        json={"sku": sample_product["sku"], "qty": 2},
        headers={"X-API-Key": TEST_POS_API_KEY},
    )
    assert resp.status_code == 201
    sale = resp.json()["data"]
    assert sale["source"] == "pos"
    assert sale["qty"] == 2
    assert sale["total_cents"] == 2 * sample_product["price_cents"]

    resp = client.get("/api/products")
    assert resp.json()["data"][0]["qty"] == sample_product["qty"] - 2


def test_pos_sale_wrong_key_rejected(client, sample_product):
    resp = client.post(
        "/api/pos/sales",
        json={"sku": sample_product["sku"], "qty": 1},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_pos_sale_missing_key_rejected(client, sample_product):
    resp = client.post(
        "/api/pos/sales", json={"sku": sample_product["sku"], "qty": 1}
    )
    assert resp.status_code == 401


def test_pos_sale_unknown_sku_404(client):
    resp = client.post(
        "/api/pos/sales",
        json={"sku": "NOPE-404", "qty": 1},
        headers={"X-API-Key": TEST_POS_API_KEY},
    )
    assert resp.status_code == 404


def test_pos_sale_custom_unit_price_overrides_product_price(
    client, sample_product
):
    resp = client.post(
        "/api/pos/sales",
        json={"sku": sample_product["sku"], "qty": 1, "unit_price_cents": 199},
        headers={"X-API-Key": TEST_POS_API_KEY},
    )
    assert resp.status_code == 201
    sale = resp.json()["data"]
    assert sale["unit_price_cents"] == 199
    assert sale["total_cents"] == 199


def test_pos_sale_concurrent_requests_never_oversell(client, admin_headers):
    """Fires more concurrent sale requests than there is stock for one SKU.
    The atomic `UPDATE ... WHERE qty >= ?` guard in `_record_sale` must let
    exactly as many requests succeed as there is stock, reject the rest with
    409, and never leave qty negative or double-decremented."""
    product = client.post(
        "/api/products",
        json={"sku": "RACE-1", "name": "Race Item", "qty": 10, "price_cents": 100},
        headers=admin_headers,
    ).json()["data"]

    request_count = 25

    def buy_one():
        return client.post(
            "/api/pos/sales",
            json={"sku": product["sku"], "qty": 1},
            headers={"X-API-Key": TEST_POS_API_KEY},
        )

    with ThreadPoolExecutor(max_workers=request_count) as pool:
        responses = list(pool.map(lambda _: buy_one(), range(request_count)))

    statuses = [resp.status_code for resp in responses]
    successes = [s for s in statuses if s == 201]
    rejections = [s for s in statuses if s == 409]

    assert len(successes) == 10
    assert len(rejections) == request_count - 10
    assert set(statuses) == {201, 409}

    resp = client.get("/api/products")
    final = next(p for p in resp.json()["data"] if p["sku"] == "RACE-1")
    assert final["qty"] == 0

    resp = client.get("/api/sales", headers=admin_headers)
    race_sales = [s for s in resp.json()["data"] if s["sku"] == "RACE-1"]
    assert len(race_sales) == 10
    assert sum(s["qty"] for s in race_sales) == 10


def test_pos_disabled_when_no_key_configured(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "nopos.db"))
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", "admin")
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", "testpass123")
    monkeypatch.delenv("LOTSPOT_POS_API_KEY", raising=False)

    from app import create_app

    with TestClient(create_app()) as no_pos_client:
        resp = no_pos_client.post(
            "/api/pos/sales",
            json={"sku": "ANY", "qty": 1},
            headers={"X-API-Key": "anything"},
        )
        assert resp.status_code == 503


def test_pos_checkout_records_multiple_items_and_one_transaction(
    client, admin_headers
):
    coke = client.post(
        "/api/products",
        json={"sku": "BULK-COKE", "name": "Bulk Coke", "qty": 10, "price_cents": 250},
        headers=admin_headers,
    ).json()["data"]
    chips = client.post(
        "/api/products",
        json={"sku": "BULK-CHIPS", "name": "Bulk Chips", "qty": 8, "price_cents": 175},
        headers=admin_headers,
    ).json()["data"]

    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "cash",
            "items": [
                {"product_id": coke["id"], "qty": 2},
                {"product_id": chips["id"], "qty": 3},
                {"product_id": coke["id"], "qty": 1},
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    checkout = resp.json()["data"]
    assert checkout["payment_method"] == "cash"
    assert checkout["cashier"] == "admin"
    assert checkout["item_count"] == 2
    assert checkout["total_qty"] == 6
    assert checkout["total_cents"] == 3 * 250 + 3 * 175

    resp = client.get("/api/products")
    products = {row["sku"]: row for row in resp.json()["data"]}
    assert products["BULK-COKE"]["qty"] == 7
    assert products["BULK-CHIPS"]["qty"] == 5

    resp = client.get("/api/sales", headers=admin_headers)
    sales = resp.json()["data"]
    assert len(sales) == 2
    assert {sale["transaction_id"] for sale in sales} == {checkout["transaction_id"]}
    assert {sale["payment_method"] for sale in sales} == {"cash"}

    today = date.today().isoformat()
    resp = client.get(
        "/api/sales/history",
        params={"start": today, "end": today},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    history = resp.json()["data"]["days"]
    assert history[0]["transaction_count"] == 1
    assert history[0]["total_items_sold"] == 6


def test_pos_checkout_requires_auth(client):
    resp = client.post(
        "/api/pos/checkout",
        json={"payment_method": "cash", "items": [{"product_id": 1, "qty": 1}]},
    )
    assert resp.status_code == 401


def test_pos_checkout_stamps_store_id_and_sold_at_utc(
    client, admin_headers, sample_product, monkeypatch
):
    """store_id/sold_at_utc exist only for a future HQ sync and must never
    reach the API response, so this asserts them by reading the DB row
    directly rather than trusting the JSON payload."""
    monkeypatch.setenv("LOTSPOT_STORE_ID", "store-99")

    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "cash",
            "items": [{"product_id": sample_product["id"], "qty": 1}],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    checkout = resp.json()["data"]
    sale = checkout["line_items"][0]
    assert "store_id" not in sale
    assert "sold_at_utc" not in sale

    import db

    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale["id"],)).fetchone()
    finally:
        conn.close()

    assert row["store_id"] == "store-99"
    assert row["sold_at_utc"] is not None
    parsed = datetime.fromisoformat(row["sold_at_utc"])
    assert parsed.utcoffset().total_seconds() == 0


def test_pos_checkout_receipt_includes_tax_breakdown(client, admin_headers):
    category = client.post(
        "/api/tax-categories", json={"name": "Taxed Goods"}, headers=admin_headers
    ).json()["data"]
    state = client.post(
        "/api/tax-accounts",
        json={
            "name": "State Tax",
            "jurisdiction": "OK State",
            "rate_bps": 450,
            "effective_from": "2000-01-01",
        },
        headers=admin_headers,
    ).json()["data"]
    city = client.post(
        "/api/tax-accounts",
        json={
            "name": "City Tax",
            "jurisdiction": "Tulsa City",
            "rate_bps": 200,
            "effective_from": "2000-01-01",
        },
        headers=admin_headers,
    ).json()["data"]
    client.put(
        f"/api/tax-categories/{category['id']}/tax-accounts",
        json={"tax_account_ids": [state["id"], city["id"]]},
        headers=admin_headers,
    )

    taxed = client.post(
        "/api/products",
        json={
            "sku": "TAXED-CHECKOUT",
            "name": "Taxed Checkout Item",
            "qty": 10,
            "price_cents": 1000,
            "tax_category_id": category["id"],
        },
        headers=admin_headers,
    ).json()["data"]

    resp = client.post(
        "/api/pos/checkout",
        json={"payment_method": "cash", "items": [{"product_id": taxed["id"], "qty": 1}]},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    checkout = resp.json()["data"]

    assert checkout["subtotal_cents"] == 1000
    assert checkout["tax_cents"] == 45 + 20
    assert checkout["total_cents"] == checkout["subtotal_cents"]
    assert checkout["grand_total_cents"] == checkout["subtotal_cents"] + checkout["tax_cents"]
    assert {row["tax_account_name"]: row["tax_cents"] for row in checkout["tax_breakdown"]} == {
        "State Tax": 45,
        "City Tax": 20,
    }


def test_pos_checkout_rejects_unrecognized_payment_method(
    client, admin_headers, sample_product
):
    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "bitcoin",
            "items": [{"product_id": sample_product["id"], "qty": 1}],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_pos_checkout_insufficient_stock_rolls_back(client, admin_headers, sample_product):
    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "card",
            "items": [
                {"product_id": sample_product["id"], "qty": sample_product["qty"] - 1},
                {"product_id": sample_product["id"], "qty": 2},
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 409

    resp = client.get("/api/products")
    assert resp.json()["data"][0]["qty"] == sample_product["qty"]
