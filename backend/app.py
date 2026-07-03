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
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

import db
from auth import TokenStore, seed_admin, verify_password
from events import Broadcaster
from models import LoginIn, PosSaleIn, ProductIn, ProductUpdate, SaleIn

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
    now = db.local_now_iso()
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

    price = unit_price_cents if unit_price_cents is not None else product["price_cents"]
    cur = conn.execute(
        """INSERT INTO sales
           (product_id, product_name, sku, qty, unit_price_cents, total_cents,
            source, sold_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (product["id"], product["name"], product["sku"], qty, price, qty * price,
         source, now, now),
    )
    sale_id = cur.lastrowid
    conn.commit()

    sale = db.row_to_dict(
        conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    )
    updated = _fetch_product(conn, product["id"])
    broadcaster.publish({"type": "inventory", "action": "updated", "product": updated})
    broadcaster.publish({"type": "sale", "action": "created", "sale": sale})
    return sale


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
            totals = conn.execute(
                """SELECT COUNT(*) AS transaction_count,
                          COALESCE(SUM(qty), 0) AS total_items_sold,
                          COALESCE(SUM(total_cents), 0) AS total_revenue_cents
                   FROM sales WHERE substr(sold_at, 1, 10) = ?""",
                (day,),
            ).fetchone()
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
                **db.row_to_dict(totals),
                "top_items": [db.row_to_dict(r) for r in top],
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
            candidate = FRONTEND_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.environ.get("LOTSPOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("LOTSPOT_PORT", "8000")),
    )
