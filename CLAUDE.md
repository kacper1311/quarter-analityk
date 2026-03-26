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

**Three-page layout:**
- `GET /` (dashboard) — metrics KPIs with trend vs. previous period, daily revenue line chart (tooltip shows order count + revenue), order heatmap by hour/day, top 5 products table.
- `GET /orders` (orders page) — full orders table; orders are grouped by `order_id` via `_group_orders()` in `main.py` — single-product orders render as a flat row, multi-product orders render as a grouped block with a summary row.
- `GET /costs` (costs page) — monthly view with KPI tiles, Allegro billing breakdown (from `billing_entries` table), editable custom costs (from `monthly_costs` table), and a read-only "Zwroty towaru" preview section at the bottom.

**Data flow:**
1. User authenticates with Allegro via OAuth2 device flow (`auth.py`) — token persisted to `tokens.json`
2. POST `/sync` triggers a 4-phase sync:
   - **Faza 1**: Fetch new Allegro orders since `MAX(bought_at)` → enrich with margins → `INSERT OR IGNORE` into SQLite. Per-item `sale_price` comes from `lineItems[].price.amount × quantity`; for multi-product orders `delivery_cost = 0` per item (delivery is order-level).
   - **Faza 2**: Refresh statuses for last 60 days — "Anulowane" clears all financial fields; "Zwrot towaru" clears only `profit`/`margin_pct` (keeps `purchase_price`/`supplier`).
   - **Faza 3**: Fetch SkyShop admin notes + status overrides for all 10 statuses → `UPDATE orders SET sky_note` / status. Overrides are verified against `ord_status` field in each order to prevent mass-override when the API ignores the status filter.
   - **Faza 4**: Fetch Allegro billing entries for last 60 days → `INSERT OR IGNORE` into `billing_entries`.
3. Dashboard data: `get_metrics_comparison()` (DB) + three JSON endpoints (`/api/chart/daily-revenue`, `/api/chart/heatmap`, `/api/top-products`) fetched client-side.

**Key data model — `orders` table:**
- `order_id` — Allegro UUID (matches SkyShop's `ord_additional_data.auctionForm.id`)
- `external_id` — Allegro offer external ID, format `"0$sky_{prod_id}_..."` → `extract_skyshop_id()` returns just `prod_id`
- `bought_at` — Warsaw-timezone ISO datetime (used for incremental sync)
- `sale_price` — per line-item total (`item_price × quantity`); for single-product orders also covers delivery context
- `delivery_cost` — order-level delivery; stored as 0 for items in multi-product orders
- `purchase_price`, `supplier` — set at INSERT; cleared to NULL for "Anulowane", kept for "Zwrot towaru"
- `profit`, `margin_pct` — cleared to NULL for both "Anulowane" and "Zwrot towaru"
- `sky_note` — SkyShop `ord_note_admin`, updated by `update_sky_notes()`

## Module responsibilities

- **`main.py`** — FastAPI app, route handlers, sync orchestration, `enrich_with_margins()`, `_group_orders()`, chart API endpoints, `_costs_context()` helper, Jinja2 filters (`ym_long`, `ym_short`)
- **`database.py`** — SQLite layer (`quarter.db`); `_conn()` context manager commits on success; `_migrate()` adds columns to existing DBs; billing and costs CRUD functions
- **`allegro.py`** — Allegro REST API; `parse_orders` converts UTC→Warsaw, uses per-item `lineItems[].price.amount`; `get_billing_entries` fetches billing with pagination
- **`skyshop.py`** — SkyShop API; `get_skyshop_order_notes()` returns `(notes_dict, status_overrides_dict)`; `SKYSHOP_STATUS_MAP` maps `optional_one`→"Zwrot towaru", `optional_two`→"Płatność przy odbiorze wysłane"; all prices from SkyShop are already gross (brutto)
- **`auth.py`** — Allegro OAuth2 device flow; auto-refreshes on expiry
- **`margins.py`** — `calculate_margin`: profit = (sale_price − delivery_cost) − purchase_price_brutto

## Templates

- `base.html` — dashboard layout + all Chart.js/JS logic
- `orders.html` — orders page layout
- `costs.html` — costs page layout with year/month tab selectors
- `macros.html` — `order_row`, `order_row_edit`, `order_group` macros; `_BADGE` dict maps status strings to CSS classes
- `row.html` / `row_edit.html` — thin wrappers calling macros; returned by HTMX partial swap routes
- `orders_partial.html` — loops `order_groups` (not flat `orders`); calls `order_group` for multi-product, `order_row` for single
- `costs_partial.html` — KPI tiles + Allegro fees table + custom costs + returned orders preview; target of all HTMX swaps on /costs
- `cost_row.html` / `cost_row_edit.html` — inline edit for monthly_costs entries

HTMX edit flow (orders): `<a hx-get="/orders/{id}/edit">` → returns `row_edit.html` swapped `outerHTML` on `<details id="row-{id}">` → POST `/orders/{id}/price` saves and returns `row.html`. For multi-product orders, editing one item swaps only that item's `<details>`; the summary row stays stale until page reload.

HTMX edit flow (costs): all cost mutations target `#costs-content` with `hx-swap="innerHTML"`, re-rendering the full `costs_partial.html`.

## Critical quirks

- **Route order in `main.py`**: `GET /orders` → `GET /orders/{id}/edit` → `POST /orders/{id}/price` → `GET /orders/{id}` — FastAPI matches in declaration order; static paths before dynamic.
- **Status exclusions are asymmetric**: "Anulowane" is excluded from all metrics and financials. "Zwrot towaru" is excluded from revenue and `profit`/`margin_pct` display, but its `purchase_price` is kept and included in `/costs` purchase_cost KPI. Both are excluded from `chart_daily_revenue()` and `top_products()`.
- **"Zwrot towaru" in the orders table**: Sprzedaż/Cena prod. columns show "–"; Zakup shows `purchase_price`; Zysk shows `−purchase_price` in red.
- **Multi-product order grouping**: `_group_orders()` uses `itertools.groupby` on rows sorted by `bought_at DESC` — relies on same-order rows being consecutive. The `orders_partial.html` template receives `order_groups`, not `orders`.
- **SkyShop status override safety**: When querying `optional_one`/`optional_two` statuses, each returned order's `ord_status` field is checked to match — prevents mass-override when the SkyShop API ignores the filter and returns all orders.
- **Billing entries — PAD excluded**: `get_billing_summary()` filters `type_id != 'PAD'` (settlement transfer, not a fee) and returns signed amounts (negative = cost, positive = credit/refund). `_costs_context()` uses the signed sum for profit calculation, absolute value for the KPI tile.
- **`autofocus` after HTMX swap**: Native `autofocus` only fires on page load. `htmx:afterSwap` listener (50 ms setTimeout) re-focuses `[autofocus]` elements in both `base.html` and `orders.html`. Edit button uses `<a hx-get>` not `<button hx-get>` — more reliable inside `<details>`.
- **`_current_token()`** is a thin wrapper around `get_token()` so tests can `monkeypatch.setattr(main, "_current_token", ...)` to bypass auth.
- **TemplateResponse signature**: `templates.TemplateResponse(request, "template.html", {...})` — `request` is the first positional argument.
- **`INSERT OR IGNORE`** with `UNIQUE(order_id, bought_at, offer_name, external_id)` — history is never overwritten; one Allegro order has one row per line item.
