"""LotSpot API — inventory, sales, and real-time updates for a local POS.

Single-origin app: when frontend/dist exists (production build), it is served
from this same process, so the whole system is one `uvicorn` on the POS box.
Interactive API docs live at /docs (Swagger UI) and /openapi.json.
"""

import asyncio
import hmac
import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

import db
from auth import TokenStore, hash_password, seed_admin, verify_password
from events import Broadcaster
from models import (
    CheckoutItemIn,
    ClockInIn,
    ClockOutIn,
    EmployeeIn,
    LoginIn,
    PosCheckoutIn,
    PosSaleIn,
    ProductIn,
    ProductUpdate,
    SaleIn,
)

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
DEFAULT_SSE_HEARTBEAT_SECONDS = 15
TOP_ITEMS_LIMIT = 5


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


def ok(data, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {"success": True, "data": data, "error": None}, status_code=status_code
    )


def fail(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"success": False, "data": None, "error": message}, status_code=status_code
    )


def require_admin(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(401, "Missing bearer token")
    username = request.app.state.tokens.validate(header[7:].strip())
    if username is None:
        raise ApiError(401, "Invalid or expired token")
    return username


def _fetch_product(conn: sqlite3.Connection, product_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def _fetch_employee(conn: sqlite3.Connection, employee_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    return db.row_to_dict(row) if row else None


def _public_employee(employee: dict) -> dict:
    return {k: v for k, v in employee.items() if k != "pin_hash"}


def _open_shift(conn: sqlite3.Connection, employee_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM time_entries WHERE employee_id = ? AND clock_out_at IS NULL",
        (employee_id,),
    ).fetchone()
    return db.row_to_dict(row) if row else None


def _shift_duration_minutes(clock_in_at: str, clock_out_at: str) -> int:
    start = datetime.fromisoformat(clock_in_at)
    end = datetime.fromisoformat(clock_out_at)
    return int((end - start).total_seconds() // 60)


def _sales_daily_totals(
    conn: sqlite3.Connection, start_day: str, end_day: str
) -> list[dict]:
    rows = conn.execute(
        """SELECT substr(sold_at, 1, 10) AS day,
                  COUNT(DISTINCT transaction_id) AS transaction_count,
                  COALESCE(SUM(qty), 0) AS total_items_sold,
                  COALESCE(SUM(total_cents), 0) AS total_revenue_cents
           FROM sales
           WHERE substr(sold_at, 1, 10) BETWEEN ? AND ?
           GROUP BY day
           ORDER BY day""",
        (start_day, end_day),
    ).fetchall()
    by_day = {
        row["day"]: {
            "date": row["day"],
            "transaction_count": row["transaction_count"],
            "total_items_sold": row["total_items_sold"],
            "total_revenue_cents": row["total_revenue_cents"],
        }
        for row in rows
    }

    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    totals = []
    current = start
    while current <= end:
        day = current.isoformat()
        totals.append(
            by_day.get(
                day,
                {
                    "date": day,
                    "transaction_count": 0,
                    "total_items_sold": 0,
                    "total_revenue_cents": 0,
                },
            )
        )
        current += timedelta(days=1)
    return totals


def _insert_sale_row(
    conn: sqlite3.Connection,
    product: dict,
    qty: int,
    unit_price_cents: int | None,
    source: str,
    transaction_id: str,
    payment_method: str | None,
) -> dict:
    now = db.local_now_iso()
    price = unit_price_cents if unit_price_cents is not None else product["price_cents"]
    cur = conn.execute(
        """INSERT INTO sales
           (product_id, transaction_id, product_name, sku, qty, unit_price_cents,
            total_cents, source, payment_method, sold_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            product["id"],
            transaction_id,
            product["name"],
            product["sku"],
            qty,
            price,
            qty * price,
            source,
            payment_method,
            now,
            now,
        ),
    )
    sale_id = cur.lastrowid
    return db.row_to_dict(
        conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    )


def _record_sale(
    conn: sqlite3.Connection,
    broadcaster: Broadcaster,
    product: dict,
    qty: int,
    unit_price_cents: int | None,
    source: str,
) -> dict:
    """Atomically decrement stock and insert the sale row (snapshotting
    name/sku/price so history survives product edits and deletion)."""
    transaction_id = uuid.uuid4().hex
    cur = conn.execute(
        "UPDATE products SET qty = qty - ?, updated_at = ? WHERE id = ? AND qty >= ?",
        (qty, db.local_now_iso(), product["id"], qty),
    )
    if cur.rowcount == 0:
        raise ApiError(
            409,
            f"Insufficient stock for {product['sku']}: "
            f"requested {qty}, available {product['qty']}",
        )

    sale = _insert_sale_row(
        conn,
        product,
        qty,
        unit_price_cents,
        source=source,
        transaction_id=transaction_id,
        payment_method=None,
    )
    conn.commit()

    updated = _fetch_product(conn, product["id"])
    broadcaster.publish({"type": "inventory", "action": "updated", "product": updated})
    broadcaster.publish({"type": "sale", "action": "created", "sale": sale})
    return sale


def _record_checkout(
    conn: sqlite3.Connection,
    broadcaster: Broadcaster,
    items: list[CheckoutItemIn],
    payment_method: str,
) -> dict:
    transaction_id = uuid.uuid4().hex
    now = db.local_now_iso()
    product_ids = [item.product_id for item in items]
    rows = conn.execute(
        f"SELECT * FROM products WHERE id IN ({','.join('?' for _ in product_ids)})",
        product_ids,
    ).fetchall()
    products = {row["id"]: db.row_to_dict(row) for row in rows}

    missing = [item.product_id for item in items if item.product_id not in products]
    if missing:
        raise ApiError(404, f"Product {missing[0]} not found")

    merged: list[dict] = []
    by_product_id: dict[int, dict] = {}
    for item in items:
        # Group duplicate cart lines for a single stock check and sale row.
        entry = by_product_id.get(item.product_id)
        if entry is None:
            entry = {
                "product": products[item.product_id],
                "qty": 0,
                "max_qty": products[item.product_id]["qty"],
            }
            by_product_id[item.product_id] = entry
            merged.append(entry)
        entry["qty"] += item.qty

    for entry in merged:
        product = entry["product"]
        qty = entry["qty"]
        cur = conn.execute(
            "UPDATE products SET qty = qty - ?, updated_at = ? WHERE id = ? AND qty >= ?",
            (qty, now, product["id"], qty),
        )
        if cur.rowcount == 0:
            raise ApiError(
                409,
                f"Insufficient stock for {product['sku']}: "
                f"requested {qty}, available {product['qty']}",
            )

    sales = []
    for entry in merged:
        product = entry["product"]
        qty = entry["qty"]
        sale = _insert_sale_row(
            conn,
            product,
            qty,
            None,
            source="pos",
            transaction_id=transaction_id,
            payment_method=payment_method,
        )
        sales.append(sale)

    conn.commit()

    refreshed_products = []
    for entry in merged:
        updated = _fetch_product(conn, entry["product"]["id"])
        refreshed_products.append(updated)
        broadcaster.publish({"type": "inventory", "action": "updated", "product": updated})
    for sale in sales:
        broadcaster.publish({"type": "sale", "action": "created", "sale": sale})

    total_qty = sum(item["qty"] for item in sales)
    total_cents = sum(item["total_cents"] for item in sales)
    return {
        "transaction_id": transaction_id,
        "payment_method": payment_method,
        "line_items": sales,
        "total_qty": total_qty,
        "total_cents": total_cents,
        "item_count": len(sales),
        "products": refreshed_products,
    }


def create_app() -> FastAPI:
    db.init_db()
    conn = db.connect()
    try:
        seed_admin(conn, db.local_now_iso())
    finally:
        conn.close()

    app = FastAPI(
        title="LotSpot API",
        version="1.0.0",
        description=(
            "Convenience-store inventory and sales API. "
            "Money values are integer cents. Timestamps are store-local time."
        ),
    )
    app.state.tokens = TokenStore()
    app.state.broadcaster = Broadcaster()
    app.state.pos_api_key = os.environ.get("LOTSPOT_POS_API_KEY")

    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError):
        return fail(exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError):
        messages = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'][1:])}: {err['msg']}"
            for err in exc.errors()
        )
        return fail(f"Validation error: {messages}", 422)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException):
        return fail(str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def handle_exception(_request: Request, exc: Exception):
        import logging
        logging.exception("Unhandled exception")
        return fail("Internal server error", 500)

    # ------------------------------------------------------------- health

    @app.get("/api/health", tags=["system"])
    async def health():
        return ok({"status": "up"})

    # --------------------------------------------------------------- auth

    @app.post("/api/auth/login", tags=["auth"])
    async def login(body: LoginIn):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE username = ?", (body.username,)
            ).fetchone()
        finally:
            conn.close()
        if row is None or not verify_password(body.password, row["password_hash"]):
            raise ApiError(401, "Invalid username or password")
        session = app.state.tokens.create(body.username)
        return ok({"username": body.username, **session})

    @app.post("/api/auth/logout", tags=["auth"])
    async def logout(request: Request, _admin: str = Depends(require_admin)):
        app.state.tokens.revoke(request.headers["authorization"][7:].strip())
        return ok({"logged_out": True})

    @app.get("/api/auth/me", tags=["auth"])
    async def me(admin: str = Depends(require_admin)):
        return ok({"username": admin})

    # ----------------------------------------------------------- products

    @app.get("/api/products", tags=["products"])
    async def list_products(
        search: str | None = Query(default=None, max_length=200),
        in_stock: bool = Query(default=False),
    ):
        sql = "SELECT * FROM products"
        clauses, params = [], []
        if search:
            clauses.append("(name LIKE ? OR sku LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if in_stock:
            clauses.append("qty > 0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY name COLLATE NOCASE"

        conn = db.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return ok([db.row_to_dict(r) for r in rows])

    @app.post("/api/products", status_code=201, tags=["products"])
    async def create_product(body: ProductIn, _admin: str = Depends(require_admin)):
        now = db.local_now_iso()
        conn = db.connect()
        try:
            try:
                cur = conn.execute(
                    """INSERT INTO products (sku, name, qty, price_cents, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (body.sku, body.name, body.qty, body.price_cents, now, now),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                raise ApiError(409, f"A product with SKU {body.sku!r} already exists")
            product = _fetch_product(conn, cur.lastrowid)
        finally:
            conn.close()
        app.state.broadcaster.publish(
            {"type": "inventory", "action": "created", "product": product}
        )
        return ok(product, status_code=201)

    @app.put("/api/products/{product_id}", tags=["products"])
    async def update_product(
        product_id: int, body: ProductUpdate, _admin: str = Depends(require_admin)
    ):
        changes = body.model_dump(exclude_unset=True)
        conn = db.connect()
        try:
            product = _fetch_product(conn, product_id)
            if product is None:
                raise ApiError(404, f"Product {product_id} not found")
            if changes:
                assignments = ", ".join(f"{field} = ?" for field in changes)
                try:
                    conn.execute(
                        f"UPDATE products SET {assignments}, updated_at = ? WHERE id = ?",
                        (*changes.values(), db.local_now_iso(), product_id),
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    raise ApiError(
                        409, f"A product with SKU {changes.get('sku')!r} already exists"
                    )
            product = _fetch_product(conn, product_id)
        finally:
            conn.close()
        app.state.broadcaster.publish(
            {"type": "inventory", "action": "updated", "product": product}
        )
        return ok(product)

    @app.delete("/api/products/{product_id}", tags=["products"])
    async def delete_product(product_id: int, _admin: str = Depends(require_admin)):
        conn = db.connect()
        try:
            if _fetch_product(conn, product_id) is None:
                raise ApiError(404, f"Product {product_id} not found")
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        finally:
            conn.close()
        app.state.broadcaster.publish(
            {"type": "inventory", "action": "deleted", "product_id": product_id}
        )
        return ok({"deleted": product_id})

    # -------------------------------------------------------------- sales

    @app.post("/api/sales", status_code=201, tags=["sales"])
    async def create_sale(body: SaleIn, _admin: str = Depends(require_admin)):
        conn = db.connect()
        try:
            product = _fetch_product(conn, body.product_id)
            if product is None:
                raise ApiError(404, f"Product {body.product_id} not found")
            sale = _record_sale(
                conn, app.state.broadcaster, product, body.qty,
                body.unit_price_cents, source="manual",
            )
        finally:
            conn.close()
        return ok(sale, status_code=201)

    @app.post("/api/pos/checkout", status_code=201, tags=["pos"])
    async def pos_checkout(body: PosCheckoutIn, admin: str = Depends(require_admin)):
        conn = db.connect()
        try:
            checkout = _record_checkout(
                conn, app.state.broadcaster, body.items, body.payment_method
            )
        finally:
            conn.close()
        return ok({**checkout, "cashier": admin}, status_code=201)

    @app.get("/api/sales", tags=["sales"])
    async def list_sales(
        _admin: str = Depends(require_admin),
        day: str | None = Query(default=None, alias="date", pattern=DATE_PATTERN),
    ):
        day = day or date.today().isoformat()
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sales WHERE substr(sold_at, 1, 10) = ? "
                "ORDER BY sold_at DESC, id DESC",
                (day,),
            ).fetchall()
        finally:
            conn.close()
        return ok([db.row_to_dict(r) for r in rows])

    @app.get("/api/sales/summary", tags=["sales"])
    async def sales_summary(
        _admin: str = Depends(require_admin),
        day: str | None = Query(default=None, alias="date", pattern=DATE_PATTERN),
    ):
        day = day or date.today().isoformat()
        conn = db.connect()
        try:
            totals = _sales_daily_totals(conn, day, day)[0]
            top = conn.execute(
                """SELECT sku, product_name AS name,
                          SUM(qty) AS qty_sold, SUM(total_cents) AS revenue_cents
                   FROM sales WHERE substr(sold_at, 1, 10) = ?
                   GROUP BY sku, product_name
                   ORDER BY qty_sold DESC, revenue_cents DESC
                   LIMIT ?""",
                (day, TOP_ITEMS_LIMIT),
            ).fetchall()
        finally:
            conn.close()
        return ok(
            {
                "date": day,
                **totals,
                "top_items": [db.row_to_dict(r) for r in top],
            }
        )

    @app.get("/api/sales/history", tags=["sales"])
    async def sales_history(
        _admin: str = Depends(require_admin),
        start: str = Query(..., pattern=DATE_PATTERN),
        end: str = Query(..., pattern=DATE_PATTERN),
    ):
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end)
        if end_day < start_day:
            raise ApiError(422, "end must be on or after start")

        conn = db.connect()
        try:
            totals = _sales_daily_totals(conn, start, end)
        finally:
            conn.close()
        return ok(
            {
                "start": start,
                "end": end,
                "days": [
                    {
                        "date": row["date"],
                        "transaction_count": row["transaction_count"],
                        "total_items_sold": row["total_items_sold"],
                        "total_revenue_cents": row["total_revenue_cents"],
                    }
                    for row in totals
                ],
            }
        )

    # ------------------------------------------------- POS integration

    @app.post("/api/pos/sales", status_code=201, tags=["pos"])
    async def pos_sale(body: PosSaleIn, request: Request):
        expected = app.state.pos_api_key
        if not expected:
            raise ApiError(
                503, "POS integration is disabled: LOTSPOT_POS_API_KEY is not set"
            )
        provided = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(provided, expected):
            raise ApiError(401, "Invalid POS API key")

        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM products WHERE sku = ?", (body.sku,)
            ).fetchone()
            if row is None:
                raise ApiError(404, f"No product with SKU {body.sku!r}")
            sale = _record_sale(
                conn, app.state.broadcaster, db.row_to_dict(row), body.qty,
                body.unit_price_cents, source="pos",
            )
        finally:
            conn.close()
        return ok(sale, status_code=201)

    # ----------------------------------------------------------- employees

    @app.post("/api/employees", status_code=201, tags=["timeclock"])
    async def create_employee(body: EmployeeIn, _admin: str = Depends(require_admin)):
        now = db.local_now_iso()
        conn = db.connect()
        try:
            cur = conn.execute(
                "INSERT INTO employees (name, pin_hash, created_at) VALUES (?, ?, ?)",
                (body.name, hash_password(body.pin), now),
            )
            conn.commit()
            employee = _fetch_employee(conn, cur.lastrowid)
        finally:
            conn.close()
        return ok(_public_employee(employee), status_code=201)

    @app.get("/api/employees", tags=["timeclock"])
    async def list_employees(_admin: str = Depends(require_admin)):
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM employees ORDER BY name COLLATE NOCASE"
            ).fetchall()
        finally:
            conn.close()
        return ok([_public_employee(db.row_to_dict(r)) for r in rows])

    # ----------------------------------------------------------- timeclock

    @app.post("/api/timeclock/clock-in", status_code=201, tags=["timeclock"])
    async def timeclock_clock_in(body: ClockInIn, _admin: str = Depends(require_admin)):
        conn = db.connect()
        try:
            employee = _fetch_employee(conn, body.employee_id)
            if employee is None:
                raise ApiError(404, f"Employee {body.employee_id} not found")
            if not verify_password(body.pin, employee["pin_hash"]):
                raise ApiError(401, "Invalid PIN")
            if _open_shift(conn, body.employee_id) is not None:
                raise ApiError(409, f"{employee['name']} already has an open shift")

            now = db.local_now_iso()
            cur = conn.execute(
                "INSERT INTO time_entries (employee_id, clock_in_at, created_at) "
                "VALUES (?, ?, ?)",
                (body.employee_id, now, now),
            )
            conn.commit()
            entry = db.row_to_dict(
                conn.execute(
                    "SELECT * FROM time_entries WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
            )
        finally:
            conn.close()
        result = {**entry, "employee_name": employee["name"]}
        app.state.broadcaster.publish(
            {"type": "timeclock", "action": "clock-in", "entry": result}
        )
        return ok(result, status_code=201)

    @app.post("/api/timeclock/clock-out", tags=["timeclock"])
    async def timeclock_clock_out(body: ClockOutIn, _admin: str = Depends(require_admin)):
        conn = db.connect()
        try:
            employee = _fetch_employee(conn, body.employee_id)
            if employee is None:
                raise ApiError(404, f"Employee {body.employee_id} not found")
            if not verify_password(body.pin, employee["pin_hash"]):
                raise ApiError(401, "Invalid PIN")
            shift = _open_shift(conn, body.employee_id)
            if shift is None:
                raise ApiError(409, f"{employee['name']} has no open shift")

            now = db.local_now_iso()
            conn.execute(
                "UPDATE time_entries SET clock_out_at = ? WHERE id = ?",
                (now, shift["id"]),
            )
            conn.commit()
            entry = db.row_to_dict(
                conn.execute(
                    "SELECT * FROM time_entries WHERE id = ?", (shift["id"],)
                ).fetchone()
            )
        finally:
            conn.close()
        result = {
            **entry,
            "employee_name": employee["name"],
            "duration_minutes": _shift_duration_minutes(
                entry["clock_in_at"], entry["clock_out_at"]
            ),
        }
        app.state.broadcaster.publish(
            {"type": "timeclock", "action": "clock-out", "entry": result}
        )
        return ok(result)

    @app.get("/api/timeclock/status", tags=["timeclock"])
    async def timeclock_status(
        _admin: str = Depends(require_admin),
        employee_id: int | None = Query(default=None, gt=0),
    ):
        conn = db.connect()
        try:
            if employee_id is not None:
                employee = _fetch_employee(conn, employee_id)
                if employee is None:
                    raise ApiError(404, f"Employee {employee_id} not found")
                data = {
                    "employee_id": employee_id,
                    "employee_name": employee["name"],
                    "open_shift": _open_shift(conn, employee_id),
                }
            else:
                rows = conn.execute(
                    """SELECT time_entries.*, employees.name AS employee_name
                       FROM time_entries
                       JOIN employees ON employees.id = time_entries.employee_id
                       WHERE time_entries.clock_out_at IS NULL
                       ORDER BY time_entries.clock_in_at"""
                ).fetchall()
                data = {"open_shifts": [db.row_to_dict(r) for r in rows]}
        finally:
            conn.close()
        return ok(data)

    @app.get("/api/timeclock/history", tags=["timeclock"])
    async def timeclock_history(
        _admin: str = Depends(require_admin),
        employee_id: int | None = Query(default=None, gt=0),
        start: str | None = Query(default=None, pattern=DATE_PATTERN),
        end: str | None = Query(default=None, pattern=DATE_PATTERN),
    ):
        if (start is None) != (end is None):
            raise ApiError(422, "start and end must be provided together")
        if start and end and date.fromisoformat(end) < date.fromisoformat(start):
            raise ApiError(422, "end must be on or after start")

        clauses, params = [], []
        if employee_id is not None:
            clauses.append("time_entries.employee_id = ?")
            params.append(employee_id)
        if start and end:
            clauses.append("substr(time_entries.clock_in_at, 1, 10) BETWEEN ? AND ?")
            params += [start, end]

        sql = """SELECT time_entries.*, employees.name AS employee_name
                  FROM time_entries
                  JOIN employees ON employees.id = time_entries.employee_id"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY time_entries.clock_in_at DESC"

        conn = db.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        entries = []
        for row in rows:
            entry = db.row_to_dict(row)
            entry["duration_minutes"] = (
                _shift_duration_minutes(entry["clock_in_at"], entry["clock_out_at"])
                if entry["clock_out_at"]
                else None
            )
            entries.append(entry)
        return ok({"entries": entries})

    # ---------------------------------------------------------- real-time

    @app.get("/api/events", tags=["system"])
    async def events():
        broadcaster: Broadcaster = app.state.broadcaster
        heartbeat = float(
            os.environ.get("LOTSPOT_SSE_HEARTBEAT", DEFAULT_SSE_HEARTBEAT_SECONDS)
        )
        queue = broadcaster.subscribe()

        async def stream():
            try:
                yield f"data: {json.dumps({'type': 'connected'})}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                broadcaster.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------- static frontend (prod)

    if FRONTEND_DIST.is_dir():
        assets_dir = FRONTEND_DIST / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/") or full_path == "api":
                return fail("Not Found", 404)
            candidate = (FRONTEND_DIST / full_path).resolve()
            frontend_dist_resolved = FRONTEND_DIST.resolve()
            if full_path and candidate.is_relative_to(frontend_dist_resolved) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_dist_resolved / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.environ.get("LOTSPOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("LOTSPOT_PORT", "8000")),
    )
