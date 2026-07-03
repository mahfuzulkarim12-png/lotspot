# LotSpot

Real-time inventory visibility and back-office controls for a convenience
store, designed to run **locally on the existing POS machine**.

- **Customer view** (`/`) — public, shows everything currently on the shelf
  with live quantities, instant search by name or SKU. Updates the moment the
  register or back office changes stock (Server-Sent Events, no refresh).
- **Admin dashboard** (`/admin`) — login required. Inventory management
  (add / edit / delete products), manual sales entry, and a daily sales
  summary (revenue, items sold, transactions, top items, full sales table).
- **POS integration point** — `POST /api/pos/sales` accepts sales pushed from
  the existing POS terminal, keyed by SKU and guarded by an API key.

## Stack

| Layer    | Tech |
|----------|------|
| Frontend | React 19 + Vite 8, plain CSS design tokens, no runtime deps beyond React Router |
| Backend  | FastAPI (Python 3.12), uvicorn |
| Database | SQLite (single file, WAL mode) |
| Realtime | Server-Sent Events (`/api/events`) |

Money is stored and transported as **integer cents** (`price_cents`,
`total_cents`). Timestamps are **store-local naive ISO strings**, so the
"business day" is the POS machine's local day.

## Run it (production mode, single origin)

```bash
# 1. Backend deps (once)
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 2. Frontend build (once per UI change)
cd frontend && npm install && npm run build && cd ..

# 3. Start — serves the UI, the API and /docs from one process
cd backend
LOTSPOT_HOST=0.0.0.0 LOTSPOT_PORT=8000 ../.venv/bin/python app.py
```

Open `http://localhost:8000/` (customer screen) and
`http://localhost:8000/admin` (back office). Interactive API docs (Swagger UI)
are at `/docs`, the OpenAPI spec at `/openapi.json`.

### First login

On first start an admin account is seeded from environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOTSPOT_ADMIN_USER` | `admin` | admin username |
| `LOTSPOT_ADMIN_PASSWORD` | `admin` | admin password — **change this** |
| `LOTSPOT_POS_API_KEY` | *(unset — POS endpoint disabled)* | key the POS terminal sends in `X-API-Key` |
| `LOTSPOT_DB` | `backend/lotspot.db` | SQLite file location |
| `LOTSPOT_HOST` / `LOTSPOT_PORT` | `127.0.0.1` / `8000` | bind address |

The default password is for first boot only; set `LOTSPOT_ADMIN_PASSWORD`
before seeding a real store. (The seed happens only when no admin exists yet —
to re-seed, delete the DB file.)

## Development mode

```bash
# terminal 1 — API with auto-reload
cd backend && ../.venv/bin/python -m uvicorn app:app --reload --port 8000

# terminal 2 — Vite dev server (proxies /api to :8000)
cd frontend && npm run dev
```

## Tests

```bash
# Backend: 47 tests (auth, product CRUD, sales flow, summary, POS key, SSE)
cd backend && ../.venv/bin/python -m pytest

# Frontend: 17 tests (money parsing, inventory transforms, ProductGrid)
cd frontend && npm test
```

## POS integration

Point the existing POS at the API with the shared key:

```bash
curl -X POST http://localhost:8000/api/pos/sales \
  -H "X-API-Key: $LOTSPOT_POS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"sku": "COKE-330", "qty": 2}'
```

Optional fields: `unit_price_cents` (overrides the catalog price for that
sale). Stock is decremented atomically; a sale that would go below zero is
rejected with `409` and the current availability. Sales snapshot the product
name, SKU and price at sale time, so history survives product edits and
deletions. If `LOTSPOT_POS_API_KEY` is unset the endpoint answers `503`
(integration disabled) — safe by default.

## API surface

All responses use the envelope `{"success": bool, "data": …, "error": …}`.

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/auth/login` | — | get a bearer token (12 h) |
| `POST /api/auth/logout` | bearer | revoke the token |
| `GET /api/auth/me` | bearer | current user |
| `GET /api/products?search=&in_stock=` | — | catalog (public, customer view) |
| `POST /api/products` | bearer | create product |
| `PUT /api/products/{id}` | bearer | partial update |
| `DELETE /api/products/{id}` | bearer | delete (sales history kept) |
| `POST /api/sales` | bearer | manual sale entry |
| `GET /api/sales?date=YYYY-MM-DD` | bearer | sales list for a day |
| `GET /api/sales/summary?date=` | bearer | day totals + top items |
| `POST /api/pos/sales` | `X-API-Key` | sale pushed from the POS terminal |
| `GET /api/events` | — | SSE stream: `inventory` / `sale` events |
| `GET /api/health` | — | liveness |

## Out of scope (phase 2)

Rewards / loyalty, checkout, multi-store, user management beyond the seeded
admin.
