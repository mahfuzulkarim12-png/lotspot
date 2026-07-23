"""HQ multi-store sync data-shape tests: store_id and sold_at_utc.

Targets gaps the coder's own tests don't cover (test_migrations.py,
test_pos.py::test_pos_checkout_stamps_store_id_and_sold_at_utc,
test_sales.py::test_sold_at_stays_naive_local_and_day_filtering_still_works):
propagation across all four stamped insert points in one running app, a
field-leak sweep across every read/write endpoint and the SSE stream,
migration re-runs against real multi-row data (including a store-rename
scenario), concurrent-checkout stamping, and day-boundary sales filtering.
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from tests.conftest import TEST_EMPLOYEE_PIN, TEST_POS_API_KEY
from tests.test_migrations import _build_pre_store_id_db

INTERNAL_FIELDS = {"store_id", "sold_at_utc"}


def _assert_no_internal_fields(payload, path="data"):
    """Recursively walk a JSON-decoded response body (or SSE event) and fail
    loudly if any dict anywhere in it carries store_id or sold_at_utc — these
    must never leave the process (see app._INTERNAL_ONLY_FIELDS)."""
    if isinstance(payload, dict):
        leaked = INTERNAL_FIELDS & payload.keys()
        assert not leaked, f"internal field(s) {leaked} leaked at {path}"
        for key, value in payload.items():
            _assert_no_internal_fields(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            _assert_no_internal_fields(item, f"{path}[{i}]")


def _drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


# ---------------------------------------------------------- 1. propagation


def test_store_id_propagates_to_all_four_insert_points_in_one_app_instance(
    client, admin_headers, monkeypatch
):
    """A single running app instance, given one LOTSPOT_STORE_ID, must stamp
    that same store_id on every table that carries the column: products,
    sales (via /api/sales), employees, and time_entries (via clock-in). The
    coder's own test only exercises pos checkout in isolation."""
    monkeypatch.setenv("LOTSPOT_STORE_ID", "hq-store-77")

    product = client.post(
        "/api/products",
        json={"sku": "HQ-1", "name": "HQ Widget", "qty": 10, "price_cents": 500},
        headers=admin_headers,
    ).json()["data"]

    employee = client.post(
        "/api/employees",
        json={"name": "HQ Employee", "pin": TEST_EMPLOYEE_PIN},
        headers=admin_headers,
    ).json()["data"]

    clock_in_resp = client.post(
        "/api/timeclock/clock-in",
        json={"employee_id": employee["id"], "pin": TEST_EMPLOYEE_PIN},
        headers=admin_headers,
    )
    assert clock_in_resp.status_code == 201, clock_in_resp.text

    sale_resp = client.post(
        "/api/sales",
        json={"product_id": product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()["data"]

    import db

    conn = db.connect()
    try:
        product_row = conn.execute(
            "SELECT store_id FROM products WHERE id = ?", (product["id"],)
        ).fetchone()
        employee_row = conn.execute(
            "SELECT store_id FROM employees WHERE id = ?", (employee["id"],)
        ).fetchone()
        entry_row = conn.execute(
            "SELECT store_id FROM time_entries WHERE employee_id = ?",
            (employee["id"],),
        ).fetchone()
        sale_row = conn.execute(
            "SELECT store_id FROM sales WHERE id = ?", (sale["id"],)
        ).fetchone()
    finally:
        conn.close()

    assert product_row["store_id"] == "hq-store-77"
    assert employee_row["store_id"] == "hq-store-77"
    assert entry_row["store_id"] == "hq-store-77"
    assert sale_row["store_id"] == "hq-store-77"


def test_store_id_changes_mid_session_are_picked_up_per_request(
    client, admin_headers, monkeypatch
):
    """get_store_id() reads the env var live on every call rather than
    caching it at app startup, so changing LOTSPOT_STORE_ID between two
    requests within the same running app must be reflected in the second
    insert, not stuck on whatever value was active at process boot."""
    monkeypatch.setenv("LOTSPOT_STORE_ID", "store-first")
    first = client.post(
        "/api/products",
        json={"sku": "HQ-FIRST", "name": "First", "qty": 1, "price_cents": 100},
        headers=admin_headers,
    ).json()["data"]

    monkeypatch.setenv("LOTSPOT_STORE_ID", "store-second")
    second = client.post(
        "/api/products",
        json={"sku": "HQ-SECOND", "name": "Second", "qty": 1, "price_cents": 100},
        headers=admin_headers,
    ).json()["data"]

    import db

    conn = db.connect()
    try:
        row1 = conn.execute(
            "SELECT store_id FROM products WHERE id = ?", (first["id"],)
        ).fetchone()
        row2 = conn.execute(
            "SELECT store_id FROM products WHERE id = ?", (second["id"],)
        ).fetchone()
    finally:
        conn.close()

    assert row1["store_id"] == "store-first"
    assert row2["store_id"] == "store-second"


# ---------------------------------------------------------- 2. field leak sweep


def test_no_internal_field_leaks_across_every_read_and_write_endpoint(
    client, admin_headers
):
    """Sweeps every endpoint the mission calls out (products, sales,
    employees, timeclock — POST and GET) plus the SSE stream and asserts
    none of them ever surface store_id/sold_at_utc. No existing test asserts
    an exact field set, so a leak on any single one of these would currently
    pass silently."""
    queue = client.app.state.broadcaster.subscribe()

    product = client.post(
        "/api/products",
        json={"sku": "LEAK-1", "name": "Leak Widget", "qty": 5, "price_cents": 200},
        headers=admin_headers,
    )
    assert product.status_code == 201
    _assert_no_internal_fields(product.json()["data"], "POST /api/products")
    product_data = product.json()["data"]

    employee = client.post(
        "/api/employees",
        json={"name": "Leak Employee", "pin": TEST_EMPLOYEE_PIN},
        headers=admin_headers,
    )
    assert employee.status_code == 201
    _assert_no_internal_fields(employee.json()["data"], "POST /api/employees")
    employee_data = employee.json()["data"]

    sale = client.post(
        "/api/sales",
        json={"product_id": product_data["id"], "qty": 1},
        headers=admin_headers,
    )
    assert sale.status_code == 201
    _assert_no_internal_fields(sale.json()["data"], "POST /api/sales")

    checkout_product = client.post(
        "/api/products",
        json={"sku": "LEAK-2", "name": "Leak Checkout Widget", "qty": 5, "price_cents": 300},
        headers=admin_headers,
    ).json()["data"]
    checkout = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "cash",
            "items": [{"product_id": checkout_product["id"], "qty": 1}],
        },
        headers=admin_headers,
    )
    assert checkout.status_code == 201
    _assert_no_internal_fields(checkout.json()["data"], "POST /api/pos/checkout")

    pos_sale_product = client.post(
        "/api/products",
        json={"sku": "LEAK-3", "name": "Leak POS Widget", "qty": 5, "price_cents": 150},
        headers=admin_headers,
    ).json()["data"]
    pos_sale = client.post(
        "/api/pos/sales",
        json={"sku": pos_sale_product["sku"], "qty": 1},
        headers={"X-API-Key": TEST_POS_API_KEY},
    )
    assert pos_sale.status_code == 201
    _assert_no_internal_fields(pos_sale.json()["data"], "POST /api/pos/sales")

    clock_in = client.post(
        "/api/timeclock/clock-in",
        json={"employee_id": employee_data["id"], "pin": TEST_EMPLOYEE_PIN},
        headers=admin_headers,
    )
    assert clock_in.status_code == 201
    _assert_no_internal_fields(clock_in.json()["data"], "POST /api/timeclock/clock-in")

    status_one = client.get(
        "/api/timeclock/status",
        params={"employee_id": employee_data["id"]},
        headers=admin_headers,
    )
    assert status_one.status_code == 200
    _assert_no_internal_fields(status_one.json()["data"], "GET /api/timeclock/status?employee_id")

    status_all = client.get("/api/timeclock/status", headers=admin_headers)
    assert status_all.status_code == 200
    _assert_no_internal_fields(status_all.json()["data"], "GET /api/timeclock/status")

    clock_out = client.post(
        "/api/timeclock/clock-out",
        json={"employee_id": employee_data["id"], "pin": TEST_EMPLOYEE_PIN},
        headers=admin_headers,
    )
    assert clock_out.status_code == 200
    _assert_no_internal_fields(clock_out.json()["data"], "POST /api/timeclock/clock-out")

    history = client.get(
        "/api/timeclock/history",
        params={"employee_id": employee_data["id"]},
        headers=admin_headers,
    )
    assert history.status_code == 200
    _assert_no_internal_fields(history.json()["data"], "GET /api/timeclock/history")

    products_list = client.get("/api/products")
    assert products_list.status_code == 200
    _assert_no_internal_fields(products_list.json()["data"], "GET /api/products")

    sales_list = client.get("/api/sales", headers=admin_headers)
    assert sales_list.status_code == 200
    _assert_no_internal_fields(sales_list.json()["data"], "GET /api/sales")

    employees_list = client.get("/api/employees", headers=admin_headers)
    assert employees_list.status_code == 200
    _assert_no_internal_fields(employees_list.json()["data"], "GET /api/employees")

    today = datetime.now().date().isoformat()
    summary = client.get(
        "/api/sales/summary", params={"date": today}, headers=admin_headers
    )
    assert summary.status_code == 200
    _assert_no_internal_fields(summary.json()["data"], "GET /api/sales/summary")

    history_range = client.get(
        "/api/sales/history",
        params={"start": today, "end": today},
        headers=admin_headers,
    )
    assert history_range.status_code == 200
    _assert_no_internal_fields(history_range.json()["data"], "GET /api/sales/history")

    events = _drain(queue)
    assert events, "expected at least one broadcast event"
    for i, event in enumerate(events):
        _assert_no_internal_fields(event, f"SSE event[{i}]")


# ---------------------------------------------------------- 3. migration re-run


def _build_old_schema_db_with_multiple_rows(path: str) -> None:
    """Old (pre store_id/sold_at_utc) schema with several rows per table, so
    the migration is exercised against more than one row per table."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                transaction_id TEXT,
                product_name TEXT NOT NULL,
                sku TEXT NOT NULL,
                qty INTEGER NOT NULL CHECK (qty > 0),
                unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                total_cents INTEGER NOT NULL,
                tax_cents INTEGER NOT NULL DEFAULT 0,
                tax_category_name TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                payment_method TEXT,
                sold_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pin_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE time_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                clock_in_at TEXT NOT NULL,
                clock_out_at TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        for i in range(1, 4):
            conn.execute(
                """INSERT INTO products (sku, name, qty, price_cents, created_at, updated_at)
                   VALUES (?, ?, 5, 300, '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
                (f"MULTI-{i}", f"Multi Widget {i}"),
            )
            conn.execute(
                """INSERT INTO sales
                   (product_id, transaction_id, product_name, sku, qty, unit_price_cents,
                    total_cents, source, sold_at, created_at)
                   VALUES (?, ?, ?, ?, 1, 300, 300, 'manual', '2026-01-01T09:00:00', '2026-01-01T09:00:00')""",
                (i, f"legacy-txn-{i}", f"Multi Widget {i}", f"MULTI-{i}"),
            )
            conn.execute(
                """INSERT INTO employees (name, pin_hash, created_at)
                   VALUES (?, 'hash', '2026-01-01T00:00:00')""",
                (f"Multi Employee {i}",),
            )
            conn.execute(
                """INSERT INTO time_entries (employee_id, clock_in_at, created_at)
                   VALUES (?, '2026-01-01T08:00:00', '2026-01-01T08:00:00')""",
                (i,),
            )
        conn.commit()
    finally:
        conn.close()


def test_migration_backfills_every_row_across_multiple_rows_per_table(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "multi_row.db"
    _build_old_schema_db_with_multiple_rows(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    monkeypatch.setenv("LOTSPOT_STORE_ID", "hq-multi")

    import db

    db.init_db()
    db.init_db()
    db.init_db()  # three runs: idempotency isn't just "twice happens to work"

    conn = db.connect()
    try:
        for table in ("products", "sales", "employees", "time_entries"):
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
            assert len(columns) == len(set(columns)), f"duplicate columns on {table}"
            rows = conn.execute(f"SELECT store_id FROM {table}").fetchall()
            assert len(rows) == 3
            assert all(row["store_id"] == "hq-multi" for row in rows)
    finally:
        conn.close()


def test_migration_backfill_is_one_time_not_reapplied_when_store_id_env_changes(
    tmp_path, monkeypatch
):
    """Backfilling store_id must happen exactly once, at the moment the
    column is added (guarded by `if "store_id" not in columns`). If a later
    init_db() run — e.g. after LOTSPOT_STORE_ID changes to reflect a renamed
    or relocated store — re-ran the backfill UPDATE unconditionally, every
    historical row would be silently relabeled to the new store id,
    corrupting the very sync data this column exists for."""
    db_path = tmp_path / "store_rename.db"
    _build_pre_store_id_db(str(db_path))
    monkeypatch.setenv("LOTSPOT_DB", str(db_path))
    monkeypatch.setenv("LOTSPOT_STORE_ID", "store-old-name")

    import db

    db.init_db()  # backfills existing rows with 'store-old-name'

    monkeypatch.setenv("LOTSPOT_STORE_ID", "store-new-name")
    db.init_db()  # store renamed; must NOT relabel historical rows

    conn = db.connect()
    try:
        product = conn.execute(
            "SELECT store_id FROM products WHERE sku = 'PRE-1'"
        ).fetchone()
        sale = conn.execute("SELECT store_id FROM sales WHERE sku = 'PRE-1'").fetchone()
        employee = conn.execute(
            "SELECT store_id FROM employees WHERE name = 'Pre Employee'"
        ).fetchone()
        entry = conn.execute(
            "SELECT store_id FROM time_entries WHERE employee_id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert product["store_id"] == "store-old-name"
    assert sale["store_id"] == "store-old-name"
    assert employee["store_id"] == "store-old-name"
    assert entry["store_id"] == "store-old-name"

    # A brand-new row created after the rename picks up the new store id —
    # only historical backfilled rows are protected, not the current setting.
    conn = db.connect()
    try:
        now = db.local_now_iso()
        conn.execute(
            """INSERT INTO products (sku, name, qty, price_cents, store_id, created_at, updated_at)
               VALUES ('POST-RENAME', 'New Store Widget', 1, 100, ?, ?, ?)""",
            (db.get_store_id(), now, now),
        )
        conn.commit()
        new_product = conn.execute(
            "SELECT store_id FROM products WHERE sku = 'POST-RENAME'"
        ).fetchone()
    finally:
        conn.close()
    assert new_product["store_id"] == "store-new-name"


# ---------------------------------------------------------- 4. concurrency


def test_concurrent_pos_checkouts_stamp_store_id_and_sold_at_utc_without_races(
    client, admin_headers, monkeypatch
):
    """Same oversell-race pattern as
    test_pos_sale_concurrent_requests_never_oversell in test_pos.py, but
    through /api/pos/checkout — confirms the added columns (stamped inside
    the same _insert_sale_row call as tax computation) don't introduce a
    race that lets stock go negative, and that every row that does land has
    consistent, correct stamping."""
    monkeypatch.setenv("LOTSPOT_STORE_ID", "hq-race")
    product = client.post(
        "/api/products",
        json={"sku": "HQ-RACE-1", "name": "HQ Race Item", "qty": 10, "price_cents": 100},
        headers=admin_headers,
    ).json()["data"]

    request_count = 25

    def buy_one():
        return client.post(
            "/api/pos/checkout",
            json={
                "payment_method": "cash",
                "items": [{"product_id": product["id"], "qty": 1}],
            },
            headers=admin_headers,
        )

    with ThreadPoolExecutor(max_workers=request_count) as pool:
        responses = list(pool.map(lambda _: buy_one(), range(request_count)))

    statuses = [resp.status_code for resp in responses]
    successes = [s for s in statuses if s == 201]
    assert len(successes) == 10
    assert set(statuses) == {201, 409}

    import db

    conn = db.connect()
    try:
        final_product = conn.execute(
            "SELECT qty FROM products WHERE id = ?", (product["id"],)
        ).fetchone()
        rows = conn.execute(
            "SELECT store_id, sold_at_utc FROM sales WHERE sku = 'HQ-RACE-1'"
        ).fetchall()
    finally:
        conn.close()

    assert final_product["qty"] == 0
    assert len(rows) == 10
    for row in rows:
        assert row["store_id"] == "hq-race"
        assert row["sold_at_utc"] is not None
        parsed = datetime.fromisoformat(row["sold_at_utc"])
        assert parsed.utcoffset().total_seconds() == 0


# ---------------------------------------------------------- 5. day boundary


def test_sales_summary_and_history_day_boundary_unaffected_by_sold_at_utc(
    client, admin_headers, sample_product, monkeypatch
):
    """sold_at (naive local) still drives day bucketing via a plain prefix
    match; sold_at_utc is a UTC mirror computed independently via
    datetime.now(timezone.utc) and must never influence which day a sale is
    attributed to. Locks down two sales one second apart across a real day
    boundary (23:59:59 -> 00:00:00) landing in the correct, distinct days."""
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-03-14T23:59:59")
    late = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert late.status_code == 201, late.text

    monkeypatch.setattr("db.local_now_iso", lambda: "2026-03-15T00:00:00")
    early = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert early.status_code == 201, early.text

    day1 = client.get(
        "/api/sales/summary", params={"date": "2026-03-14"}, headers=admin_headers
    ).json()["data"]
    day2 = client.get(
        "/api/sales/summary", params={"date": "2026-03-15"}, headers=admin_headers
    ).json()["data"]
    assert day1["transaction_count"] == 1
    assert day2["transaction_count"] == 1

    history = client.get(
        "/api/sales/history",
        params={"start": "2026-03-14", "end": "2026-03-15"},
        headers=admin_headers,
    ).json()["data"]
    by_date = {d["date"]: d for d in history["days"]}
    assert by_date["2026-03-14"]["transaction_count"] == 1
    assert by_date["2026-03-15"]["transaction_count"] == 1

    # sold_at_utc exists on both rows (a real UTC timestamp, independent of
    # the mocked local sold_at) and never appears in either response.
    import db

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT sold_at, sold_at_utc FROM sales "
            "WHERE substr(sold_at, 1, 10) IN ('2026-03-14', '2026-03-15')"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    for row in rows:
        assert row["sold_at_utc"] is not None
