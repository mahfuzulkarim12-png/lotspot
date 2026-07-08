from datetime import date

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
