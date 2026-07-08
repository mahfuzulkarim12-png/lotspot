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
        },
        {
            "date": "2026-07-02",
            "transaction_count": 0,
            "total_items_sold": 0,
            "total_revenue_cents": 0,
        },
        {
            "date": "2026-07-03",
            "transaction_count": 1,
            "total_items_sold": 4,
            "total_revenue_cents": 1000,
        },
        {
            "date": "2026-07-04",
            "transaction_count": 0,
            "total_items_sold": 0,
            "total_revenue_cents": 0,
        },
    ]


def test_summary_requires_auth(client):
    assert client.get("/api/sales/summary").status_code == 401
