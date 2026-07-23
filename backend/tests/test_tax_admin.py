"""CRUD and validation coverage for the tax_accounts / tax_categories admin
endpoints, and product tax_category_id assignment."""

import db


def _create_account(client, headers, **overrides):
    payload = {
        "name": "State Tax",
        "jurisdiction": "OK State",
        "rate_bps": 450,
        "effective_from": "2026-01-01",
        "effective_to": None,
    }
    payload.update(overrides)
    return client.post("/api/tax-accounts", json=payload, headers=headers)


def _create_category(client, headers, name="Custom Category"):
    return client.post("/api/tax-categories", json={"name": name}, headers=headers)


def test_tax_categories_seeded_on_boot(client, admin_headers):
    resp = client.get("/api/tax-categories", headers=admin_headers)
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()["data"]}
    assert names == set(db.TAX_CATEGORY_SEED_NAMES)
    assert all(c["tax_account_ids"] == [] for c in resp.json()["data"])


def test_tax_accounts_require_auth(client):
    assert client.get("/api/tax-accounts").status_code == 401
    assert _create_account(client, {}).status_code == 401


def test_create_list_update_delete_tax_account(client, admin_headers):
    resp = _create_account(client, admin_headers, name="City Tax", rate_bps=200)
    assert resp.status_code == 201, resp.text
    account = resp.json()["data"]
    assert account["rate_bps"] == 200
    assert account["effective_to"] is None

    resp = client.get("/api/tax-accounts", headers=admin_headers)
    assert any(a["id"] == account["id"] for a in resp.json()["data"])

    resp = client.put(
        f"/api/tax-accounts/{account['id']}",
        json={"rate_bps": 275},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["rate_bps"] == 275
    assert resp.json()["data"]["name"] == "City Tax"  # untouched

    resp = client.delete(f"/api/tax-accounts/{account['id']}", headers=admin_headers)
    assert resp.status_code == 200

    resp = client.get("/api/tax-accounts", headers=admin_headers)
    assert all(a["id"] != account["id"] for a in resp.json()["data"])


def test_tax_account_effective_to_before_from_rejected(client, admin_headers):
    resp = _create_account(
        client, admin_headers, effective_from="2026-06-01", effective_to="2026-01-01"
    )
    assert resp.status_code == 422


def test_update_tax_account_missing_404(client, admin_headers):
    resp = client.put(
        "/api/tax-accounts/9999", json={"rate_bps": 100}, headers=admin_headers
    )
    assert resp.status_code == 404


def test_create_list_update_delete_tax_category(client, admin_headers):
    resp = _create_category(client, admin_headers, name="Alcohol")
    assert resp.status_code == 201, resp.text
    category = resp.json()["data"]
    assert category["tax_account_ids"] == []

    resp = client.put(
        f"/api/tax-categories/{category['id']}",
        json={"name": "Alcoholic Beverages"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "Alcoholic Beverages"

    resp = client.delete(f"/api/tax-categories/{category['id']}", headers=admin_headers)
    assert resp.status_code == 200


def test_tax_category_duplicate_name_conflict(client, admin_headers):
    assert _create_category(client, admin_headers, name="Dup").status_code == 201
    resp = _create_category(client, admin_headers, name="Dup")
    assert resp.status_code == 409


def test_tax_category_delete_blocked_when_assigned_to_product(client, admin_headers):
    category = _create_category(client, admin_headers, name="In Use").json()["data"]
    client.post(
        "/api/products",
        json={
            "sku": "INUSE-1",
            "name": "Taxed Widget",
            "qty": 5,
            "price_cents": 100,
            "tax_category_id": category["id"],
        },
        headers=admin_headers,
    )
    resp = client.delete(f"/api/tax-categories/{category['id']}", headers=admin_headers)
    assert resp.status_code == 409


def test_set_tax_category_accounts_replaces_mapping(client, admin_headers):
    category = _create_category(client, admin_headers, name="Mapped").json()["data"]
    account_a = _create_account(client, admin_headers, name="A").json()["data"]
    account_b = _create_account(client, admin_headers, name="B").json()["data"]

    resp = client.put(
        f"/api/tax-categories/{category['id']}/tax-accounts",
        json={"tax_account_ids": [account_a["id"], account_b["id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert set(resp.json()["data"]["tax_account_ids"]) == {account_a["id"], account_b["id"]}

    # Replacing with a subset drops the other mapping entirely.
    resp = client.put(
        f"/api/tax-categories/{category['id']}/tax-accounts",
        json={"tax_account_ids": [account_a["id"]]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["tax_account_ids"] == [account_a["id"]]


def test_set_tax_category_accounts_unknown_account_404(client, admin_headers):
    category = _create_category(client, admin_headers, name="Broken Mapping").json()["data"]
    resp = client.put(
        f"/api/tax-categories/{category['id']}/tax-accounts",
        json={"tax_account_ids": [999999]},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_product_create_defaults_to_general_merchandise_category(client, admin_headers):
    resp = client.post(
        "/api/products",
        json={"sku": "DEFAULT-CAT", "name": "Default Cat Item", "qty": 1, "price_cents": 100},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    product = resp.json()["data"]

    categories = client.get("/api/tax-categories", headers=admin_headers).json()["data"]
    general = next(c for c in categories if c["name"] == db.GENERAL_MERCHANDISE_CATEGORY)
    assert product["tax_category_id"] == general["id"]


def test_product_create_with_invalid_tax_category_404(client, admin_headers):
    resp = client.post(
        "/api/products",
        json={
            "sku": "BAD-CAT",
            "name": "Bad Cat Item",
            "qty": 1,
            "price_cents": 100,
            "tax_category_id": 999999,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_product_update_tax_category_id(client, admin_headers, sample_product):
    category = _create_category(client, admin_headers, name="Reassigned").json()["data"]
    resp = client.put(
        f"/api/products/{sample_product['id']}",
        json={"tax_category_id": category["id"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["tax_category_id"] == category["id"]

    resp = client.put(
        f"/api/products/{sample_product['id']}",
        json={"tax_category_id": 999999},
        headers=admin_headers,
    )
    assert resp.status_code == 404
