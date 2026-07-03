def _create(client, headers, **overrides):
    payload = {"sku": "SKU-1", "name": "Item", "qty": 5, "price_cents": 100}
    payload.update(overrides)
    return client.post("/api/products", json=payload, headers=headers)


def test_create_requires_auth(client):
    resp = client.post(
        "/api/products",
        json={"sku": "X", "name": "X", "qty": 1, "price_cents": 1},
    )
    assert resp.status_code == 401


def test_create_product(client, admin_headers):
    resp = _create(client, admin_headers, sku="CHIP-01", name="Salt Chips", qty=10, price_cents=350)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["sku"] == "CHIP-01"
    assert data["name"] == "Salt Chips"
    assert data["qty"] == 10
    assert data["price_cents"] == 350
    assert data["id"] > 0


def test_duplicate_sku_conflict(client, admin_headers):
    assert _create(client, admin_headers, sku="DUP-1").status_code == 201
    resp = _create(client, admin_headers, sku="DUP-1", name="Other")
    assert resp.status_code == 409
    assert resp.json()["success"] is False


def test_validation_rejects_bad_payloads(client, admin_headers):
    assert _create(client, admin_headers, qty=-1).status_code == 422
    assert _create(client, admin_headers, price_cents=-5).status_code == 422
    assert _create(client, admin_headers, name="").status_code == 422
    assert _create(client, admin_headers, sku="").status_code == 422


def test_list_products_is_public(client, sample_product):
    resp = client.get("/api/products")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 1
    assert body["data"][0]["sku"] == sample_product["sku"]


def test_search_filters_by_name_and_sku(client, admin_headers):
    _create(client, admin_headers, sku="COKE-330", name="Coca-Cola 330ml")
    _create(client, admin_headers, sku="PEPSI-330", name="Pepsi 330ml")
    _create(client, admin_headers, sku="BREAD-01", name="White Bread")

    resp = client.get("/api/products", params={"search": "cola"})
    names = [p["name"] for p in resp.json()["data"]]
    assert names == ["Coca-Cola 330ml"]

    resp = client.get("/api/products", params={"search": "bread"})
    assert [p["sku"] for p in resp.json()["data"]] == ["BREAD-01"]

    resp = client.get("/api/products", params={"search": "330"})
    assert len(resp.json()["data"]) == 2


def test_in_stock_filter(client, admin_headers):
    _create(client, admin_headers, sku="GONE-1", name="Sold Out", qty=0)
    _create(client, admin_headers, sku="HERE-1", name="Available", qty=3)

    resp = client.get("/api/products", params={"in_stock": "true"})
    skus = [p["sku"] for p in resp.json()["data"]]
    assert skus == ["HERE-1"]

    resp = client.get("/api/products")
    assert len(resp.json()["data"]) == 2


def test_update_product_partial(client, admin_headers, sample_product):
    resp = client.put(
        f"/api/products/{sample_product['id']}",
        json={"qty": 99, "price_cents": 275},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["qty"] == 99
    assert data["price_cents"] == 275
    assert data["name"] == sample_product["name"]  # untouched


def test_update_missing_product_404(client, admin_headers):
    resp = client.put("/api/products/9999", json={"qty": 1}, headers=admin_headers)
    assert resp.status_code == 404


def test_update_requires_auth(client, sample_product):
    resp = client.put(f"/api/products/{sample_product['id']}", json={"qty": 1})
    assert resp.status_code == 401


def test_delete_product(client, admin_headers, sample_product):
    resp = client.delete(f"/api/products/{sample_product['id']}", headers=admin_headers)
    assert resp.status_code == 200

    resp = client.get("/api/products")
    assert resp.json()["data"] == []


def test_delete_missing_product_404(client, admin_headers):
    resp = client.delete("/api/products/424242", headers=admin_headers)
    assert resp.status_code == 404


def test_delete_product_with_sales_keeps_history(client, admin_headers, sample_product):
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 2},
        headers=admin_headers,
    )
    assert resp.status_code == 201

    resp = client.delete(f"/api/products/{sample_product['id']}", headers=admin_headers)
    assert resp.status_code == 200

    resp = client.get("/api/sales", headers=admin_headers)
    sales = resp.json()["data"]
    assert len(sales) == 1
    assert sales[0]["product_name"] == sample_product["name"]
