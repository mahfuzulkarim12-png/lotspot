"""Tests for field validation and upper bounds."""


def test_product_qty_exceeds_max(client, admin_headers):
    """Product qty exceeding max should be rejected."""
    resp = client.post(
        "/api/products",
        json={"sku": "TEST-1", "name": "Test", "qty": 1000000, "price_cents": 100},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["success"] is False


def test_product_price_exceeds_max(client, admin_headers):
    """Product price exceeding max should be rejected."""
    resp = client.post(
        "/api/products",
        json={"sku": "TEST-1", "name": "Test", "qty": 10, "price_cents": 10000000},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["success"] is False


def test_product_valid_max_values(client, admin_headers):
    """Products at max valid values should be accepted."""
    resp = client.post(
        "/api/products",
        json={"sku": "TEST-MAX", "name": "Test Max", "qty": 999999, "price_cents": 9999999},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["success"] is True


def test_sale_qty_exceeds_max(client, admin_headers, sample_product):
    """Sale qty exceeding max should be rejected."""
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1000000},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["success"] is False


def test_sale_unit_price_exceeds_max(client, admin_headers, sample_product):
    """Sale unit_price_cents exceeding max should be rejected."""
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1, "unit_price_cents": 10000000},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["success"] is False


def test_envelope_consistency_on_validation_error(client, admin_headers):
    """Validation errors should return consistent envelope."""
    resp = client.post(
        "/api/products",
        json={"sku": "TEST", "name": "Test", "qty": 1000000, "price_cents": 100},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
