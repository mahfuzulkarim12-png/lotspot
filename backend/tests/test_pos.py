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
