import re
from datetime import date


def _add_product(client, headers, sku, name, qty, price_cents):
    resp = client.post(
        "/api/products",
        json={"sku": sku, "name": name, "qty": qty, "price_cents": price_cents},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_sale_decrements_stock(client, admin_headers, sample_product):
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 3},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    sale = resp.json()["data"]
    assert sale["qty"] == 3
    assert sale["unit_price_cents"] == sample_product["price_cents"]
    assert sale["total_cents"] == 3 * sample_product["price_cents"]
    assert sale["source"] == "manual"
    assert sale["sold_at"]

    resp = client.get("/api/products")
    assert resp.json()["data"][0]["qty"] == sample_product["qty"] - 3


def test_sale_requires_auth(client, sample_product):
    resp = client.post("/api/sales", json={"product_id": sample_product["id"], "qty": 1})
    assert resp.status_code == 401


def test_sale_insufficient_stock_conflict(client, admin_headers, sample_product):
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": sample_product["qty"] + 1},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert "stock" in resp.json()["error"].lower()

    # stock untouched after the failed sale
    resp = client.get("/api/products")
    assert resp.json()["data"][0]["qty"] == sample_product["qty"]


def test_sale_unknown_product_404(client, admin_headers):
    resp = client.post(
        "/api/sales", json={"product_id": 777, "qty": 1}, headers=admin_headers
    )
    assert resp.status_code == 404


def test_sale_zero_qty_rejected(client, admin_headers, sample_product):
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 0},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_list_sales_for_today(client, admin_headers, sample_product):
    for _ in range(2):
        resp = client.post(
            "/api/sales",
            json={"product_id": sample_product["id"], "qty": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 201

    resp = client.get("/api/sales", headers=admin_headers)
    assert resp.status_code == 200
    sales = resp.json()["data"]
    assert len(sales) == 2
    assert all(s["product_name"] == sample_product["name"] for s in sales)


def test_list_sales_requires_auth(client):
    assert client.get("/api/sales").status_code == 401


def test_list_sales_rejects_bad_date(client, admin_headers):
    resp = client.get("/api/sales", params={"date": "not-a-date"}, headers=admin_headers)
    assert resp.status_code == 422


def test_list_sales_other_day_is_empty(client, admin_headers, sample_product):
    client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    resp = client.get("/api/sales", params={"date": "2000-01-01"}, headers=admin_headers)
    assert resp.json()["data"] == []


def test_sold_at_stays_naive_local_and_day_filtering_still_works(
    client, admin_headers, sample_product
):
    """sold_at must keep its naive-local 'YYYY-MM-DDTHH:MM:SS' shape (no
    timezone suffix) since the summary/day filters do a plain prefix match
    against it — adding sold_at_utc must not disturb that."""
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", sale["sold_at"])

    today = date.today().isoformat()
    resp = client.get(
        "/api/sales/summary", params={"date": today}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()["data"]
    assert summary["transaction_count"] == 1
    assert summary["total_items_sold"] == 1


def test_daily_summary_totals_and_top_items(client, admin_headers):
    coke = _add_product(client, admin_headers, "COKE-330", "Coca-Cola 330ml", 50, 250)
    bread = _add_product(client, admin_headers, "BREAD-01", "White Bread", 20, 400)
    gum = _add_product(client, admin_headers, "GUM-01", "Mint Gum", 30, 150)

    for product, qty in ((coke, 5), (coke, 3), (bread, 2), (gum, 1)):
        resp = client.post(
            "/api/sales",
            json={"product_id": product["id"], "qty": qty},
            headers=admin_headers,
        )
        assert resp.status_code == 201

    resp = client.get("/api/sales/summary", headers=admin_headers)
    assert resp.status_code == 200
    summary = resp.json()["data"]

    assert summary["transaction_count"] == 4
    assert summary["total_items_sold"] == 11
    assert summary["total_revenue_cents"] == 8 * 250 + 2 * 400 + 1 * 150

    top = summary["top_items"]
    assert top[0]["sku"] == "COKE-330"
    assert top[0]["qty_sold"] == 8
    assert top[0]["revenue_cents"] == 2000
    assert [item["sku"] for item in top] == ["COKE-330", "BREAD-01", "GUM-01"]


def test_daily_summary_empty_day(client, admin_headers):
    resp = client.get(
        "/api/sales/summary", params={"date": "2000-01-01"}, headers=admin_headers
    )
    assert resp.status_code == 200
    summary = resp.json()["data"]
    assert summary["transaction_count"] == 0
    assert summary["total_items_sold"] == 0
    assert summary["total_revenue_cents"] == 0
    assert summary["top_items"] == []


def test_daily_history_rollup_matches_raw_sales(client, admin_headers, monkeypatch):
    product = _add_product(client, admin_headers, "HIST-01", "History Item", 20, 250)

    raw_sales = [
        ("2026-07-01T09:00:00", 2),
        ("2026-07-01T13:15:00", 1),
        ("2026-07-03T11:30:00", 4),
    ]

    for sold_at, qty in raw_sales:
        monkeypatch.setattr("db.local_now_iso", lambda sold_at=sold_at: sold_at)
        resp = client.post(
            "/api/sales",
            json={"product_id": product["id"], "qty": qty},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text

    resp = client.get(
        "/api/sales/history",
        params={"start": "2026-07-01", "end": "2026-07-04"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    history = resp.json()["data"]["days"]

    assert [row["date"] for row in history] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]
    assert history == [
        {
            "date": "2026-07-01",
            "transaction_count": 2,
            "total_items_sold": 3,
            "total_revenue_cents": 750,
            "total_tax_cents": 0,
        },
        {
            "date": "2026-07-02",
            "transaction_count": 0,
            "total_items_sold": 0,
            "total_revenue_cents": 0,
            "total_tax_cents": 0,
        },
        {
            "date": "2026-07-03",
            "transaction_count": 1,
            "total_items_sold": 4,
            "total_revenue_cents": 1000,
            "total_tax_cents": 0,
        },
        {
            "date": "2026-07-04",
            "transaction_count": 0,
            "total_items_sold": 0,
            "total_revenue_cents": 0,
            "total_tax_cents": 0,
        },
    ]


def test_summary_requires_auth(client):
    assert client.get("/api/sales/summary").status_code == 401


def _create_tax_category(client, headers, name):
    resp = client.post("/api/tax-categories", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _create_tax_account(client, headers, **overrides):
    payload = {
        "name": "Test Tax",
        "jurisdiction": "Test Jurisdiction",
        "rate_bps": 0,
        "effective_from": "2000-01-01",
        "effective_to": None,
    }
    payload.update(overrides)
    resp = client.post("/api/tax-accounts", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _map_tax_accounts(client, headers, category_id, account_ids):
    resp = client.put(
        f"/api/tax-categories/{category_id}/tax-accounts",
        json={"tax_account_ids": account_ids},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _product_with_category(client, headers, category_id, price_cents, sku):
    resp = client.post(
        "/api/products",
        json={
            "sku": sku,
            "name": "Tax Test Item",
            "qty": 100,
            "price_cents": price_cents,
            "tax_category_id": category_id,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_tax_round_half_up_at_half_cent_boundary(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Half Cent Category")
    account = _create_tax_account(client, admin_headers, name="Half Cent Tax", rate_bps=2500)
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(client, admin_headers, category["id"], price_cents=2, sku="HALFCENT-1")

    resp = client.post(
        "/api/sales",
        json={"product_id": product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    # 2 cents * 25% = 0.5 cents exactly -> round-half-up gives 1 cent.
    assert sale["total_cents"] == 2
    assert sale["tax_cents"] == 1
    assert sale["grand_total_cents"] == 3
    assert len(sale["tax_lines"]) == 1
    assert sale["tax_lines"][0]["tax_cents"] == 1
    assert sale["tax_lines"][0]["rate_bps"] == 2500


def test_tax_zero_rate_category_produces_zero_tax(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Zero Rate Category")
    account = _create_tax_account(client, admin_headers, name="Zero Rate Tax", rate_bps=0)
    _map_tax_accounts(client, admin_headers, category["id"], [account["id"]])
    product = _product_with_category(client, admin_headers, category["id"], price_cents=500, sku="ZERO-1")

    resp = client.post(
        "/api/sales",
        json={"product_id": product["id"], "qty": 3},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    assert sale["tax_cents"] == 0
    assert sale["grand_total_cents"] == sale["total_cents"]
    assert len(sale["tax_lines"]) == 1
    assert sale["tax_lines"][0]["tax_cents"] == 0


def test_tax_multi_jurisdiction_lines_sum_to_sale_tax_cents(client, admin_headers):
    category = _create_tax_category(client, admin_headers, "Multi Jurisdiction Category")
    state = _create_tax_account(
        client, admin_headers, name="State Tax", jurisdiction="OK State", rate_bps=825
    )
    city = _create_tax_account(
        client, admin_headers, name="City Tax", jurisdiction="Tulsa City", rate_bps=200
    )
    _map_tax_accounts(client, admin_headers, category["id"], [state["id"], city["id"]])
    product = _product_with_category(client, admin_headers, category["id"], price_cents=1000, sku="MULTI-1")

    resp = client.post(
        "/api/sales",
        json={"product_id": product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["data"]

    assert sale["total_cents"] == 1000
    assert len(sale["tax_lines"]) == 2
    assert sum(line["tax_cents"] for line in sale["tax_lines"]) == sale["tax_cents"]
    assert sale["tax_cents"] == 83 + 20  # 8.25% (83c) + 2% (20c) of a $10.00 line
    assert sale["grand_total_cents"] == sale["total_cents"] + sale["tax_cents"]
