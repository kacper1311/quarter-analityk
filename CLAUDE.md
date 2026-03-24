# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
uvicorn main:app --reload
```

Requires a `.env` file with:
```
ALLEGRO_CLIENT_ID=...
ALLEGRO_CLIENT_SECRET=...
SKYSHOP_API_KEY=...
SKYSHOP_API_URL=...
```

## Running tests

```bash
python3.13 -m pytest tests/ -v
python3.13 -m pytest tests/test_database.py::TestInsertOrders::test_deduplication -v  # single test
python3.13 -m pytest tests/ --cov=. --cov-report=term-missing  # with coverage
```

## Architecture

FastAPI + Jinja2 + HTMX dashboard for a Polish e-commerce business (Allegro sales, SkyShop sourcing). Single-user app — auth state and last sync time are module-level globals in `main.py`.

**Two-page layout:**
- `GET /` (dashboard) — metrics KPIs with trend vs. previous period, daily revenue line chart, order heatmap by hour/day, top 5 products table. No orders list.
- `GET /orders` (orders page) — full orders table with inline row editing.

**Data flow:**
1. User authenticates with Allegro via OAuth2 device flow (`auth.py`) — token persisted to `tokens.json`
2. POST `/sync` triggers a 3-phase sync:
   - **Faza 1**: Fetch new Allegro orders since `MAX(bought_at)` → enrich with margins → `INSERT OR IGNORE` into SQLite
   - **Faza 2**: Refresh statuses for last 60 days (`UPDATE orders SET status`) — if status becomes "Anulowane", also clears `purchase_price`, `supplier`, `profit`, `margin_pct`
   - **Faza 3**: Fetch SkyShop admin notes for all 8 statuses → `UPDATE orders SET sky_note`
3. Dashboard data comes from `get_metrics_comparison()` (DB) + three JSON API endpoints (`/api/chart/daily-revenue`, `/api/chart/heatmap`, `/api/top-products`) fetched client-side by Chart.js / JS

**Key data model — `orders` table:**
- `order_id` — Allegro UUID (matches SkyShop's `ord_additional_data.auctionForm.id`)
- `external_id` — Allegro offer external ID, format `"0$sky_{prod_id}_..."` → used to look up SkyShop product price
- `bought_at` — Warsaw-timezone ISO datetime (used for incremental sync)
- `purchase_price`, `supplier`, `profit`, `margin_pct` — set at INSERT time; cleared to NULL if status later changes to "Anulowane"
- `sky_note` — SkyShop `ord_note_admin`, updated by `update_sky_notes()` (not stored during `insert_orders`)

## Module responsibilities

- **`main.py`** — FastAPI app, route handlers, sync orchestration, `enrich_with_margins()`, three JSON chart API endpoints
- **`database.py`** — SQLite layer (`quarter.db`); `_conn()` is a context manager that commits on success; `_migrate()` adds columns to existing DBs; `get_metrics_comparison()` returns current + previous period metrics (excludes "Anulowane")
- **`allegro.py`** — Allegro REST API (`/order/checkout-forms`); `parse_orders` converts UTC timestamps to Warsaw timezone; `sale_price` uses `paidAmount` or falls back to `summary.totalToPay` (cash-on-delivery)
- **`skyshop.py`** — SkyShop API; `getProducts` returns `{"response_code":200, "0":{...}, "1":{...}}` (numeric dict keys); `getOrders` returns `{"response_code":200, "data":[...]}` (list); notes are fetched per-status (8 statuses) because the API default only returns finished orders
- **`auth.py`** — Allegro OAuth2 device flow; auto-refreshes on expiry
- **`margins.py`** — `calculate_margin`: profit = (sale_price − delivery_cost) − purchase_price_brutto; constants: Allegro commission 12%, payment fee 1.5%, fixed costs 1245 zł/month, target 4000 zł net

## Templates

- `base.html` — dashboard layout: sidebar (nav, date filter, sync, logout), metrics grid with trends, charts section (Chart.js), top products table
- `orders.html` — orders page layout: same sidebar structure, sticky table header, orders list
- `macros.html` — `order_row` and `order_row_edit` macros render a single `<details>` row with expand panel and HTMX edit link. Status badge CSS classes defined here as a Jinja2 dict.
- `row.html` / `row_edit.html` — thin wrappers that call macros; returned by HTMX partial swap routes
- `orders_partial.html` — orders list fragment included in `orders.html`

HTMX edit flow: "✏️ Edytuj" `<a hx-get>` → `GET /orders/{id}/edit` returns `row_edit.html` swapped via `hx-swap="outerHTML"` on the `<details>` element → POST `/orders/{id}/price` saves and returns `row.html` restoring the normal row.

## Critical quirks

- **Route order in `main.py`**: `GET /orders` (full page) → `GET /orders/{id}/edit` → `POST /orders/{id}/price` → `GET /orders/{id}` — FastAPI matches in declaration order; static paths before dynamic
- **"Anulowane" orders are excluded everywhere**: `get_metrics_comparison()`, `chart_daily_revenue()`, and `top_products()` all skip rows with `status == "Anulowane"`. The template macro also shows "–" for financial fields when status is "Anulowane" (defense against legacy DB data).
- **`autofocus` after HTMX swap**: Native `autofocus` only fires on page load. A `htmx:afterSwap` JS listener in both `base.html` and `orders.html` re-focuses inputs with `autofocus` (with 50 ms `setTimeout`). Edit button uses `<a hx-get>` not `<button hx-get>` — more reliable inside `<details>` elements.
- **SkyShop `getOrders` requires `&status=X`** — without it only finished orders are returned. Iterates 8 statuses: `new, waiting_for_shipment, waiting_for_send, send, ready, finished, canceled, returned`
- **SkyShop product price VAT**: Tayma supplier prices are net (multiply × 1.23); others are already gross
- **Allegro `external_id`** format: `"0$sky_{prod_id}_{...}"` — `extract_skyshop_id()` strips the prefix and returns just `prod_id`
- **`INSERT OR IGNORE`** with `UNIQUE(order_id, bought_at, offer_name, external_id)` — history is never overwritten; one Allegro order can have multiple rows (one per line item)
- **TemplateResponse signature**: Newer Starlette requires `request` as the first positional argument — `templates.TemplateResponse(request, "template.html", {...})`
- **`_current_token()`** is a thin wrapper around `get_token()` specifically so tests can `monkeypatch.setattr(main, "_current_token", ...)` to bypass auth
