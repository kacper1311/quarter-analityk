# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
streamlit run app.py
```

Requires a `.env` file with:
```
ALLEGRO_CLIENT_ID=...
ALLEGRO_CLIENT_SECRET=...
SKYSHOP_API_KEY=...
SKYSHOP_API_URL=...
```

## Architecture

Single-page Streamlit dashboard for a Polish e-commerce business selling on Allegro with products sourced via SkyShop.

**Data flow:**
1. User authenticates with Allegro via OAuth2 device flow (`auth.py`)
2. "Aktualizuj dane" button triggers a 3-phase sync:
   - **Faza 1**: Fetch new Allegro orders since `MAX(bought_at)` → enrich with margins → `INSERT OR IGNORE` into SQLite
   - **Faza 2**: Refresh statuses for last 60 days (`UPDATE orders SET status`)
   - **Faza 3**: Fetch SkyShop admin notes for all statuses → `UPDATE orders SET sky_note`
3. Orders are displayed from DB filtered by date range; rendered as HTML `<details>/<summary>` rows

**Key data model — `orders` table:**
- `order_id` — Allegro UUID (matches SkyShop's `ord_additional_data.auctionForm.id`)
- `external_id` — Allegro offer external ID, format `"0$sky_{prod_id}_..."` → used to look up SkyShop product price
- `bought_at` — Warsaw-timezone ISO datetime (used for incremental sync)
- `purchase_price`, `supplier`, `profit`, `margin_pct` — set at INSERT time, never overwritten
- `sky_note` — SkyShop `ord_note_admin`, updated on every sync

## Module responsibilities

- **`app.py`** — Streamlit UI, sync orchestration, HTML table rendering
- **`database.py`** — SQLite layer (`quarter.db`); `_conn()` is a context manager that commits on success
- **`allegro.py`** — Allegro REST API (`/order/checkout-forms`); `parse_orders` converts UTC timestamps to Warsaw timezone; `sale_price` uses `paidAmount` or falls back to `summary.totalToPay` (cash-on-delivery)
- **`skyshop.py`** — SkyShop API; `getProducts` returns `{"response_code":200, "0":{...}, "1":{...}}` (dict keys); `getOrders` returns `{"response_code":200, "data":[...]}` (list); notes are fetched per-status (8 statuses) because the API default only returns finished orders
- **`auth.py`** — Allegro OAuth2 device flow; tokens persisted in `tokens.json`; auto-refreshes on expiry
- **`margins.py`** — `calculate_margin`: profit = (sale_price − delivery_cost) − purchase_price_brutto; constants: Allegro commission 12%, payment fee 1.5%, fixed costs 1245 zł/month, target 4000 zł net

## Critical quirks

- **SkyShop `getOrders` requires `&status=X`** — without it, only finished orders are returned. The function iterates 8 statuses: `new, waiting_for_shipment, waiting_for_send, send, ready, finished, canceled, returned`
- **SkyShop product price VAT**: Tayma supplier prices are net (multiply × 1.23); others are already gross
- **Allegro `external_id`** format: `"0$sky_{prod_id}_{...}"` — `extract_skyshop_id()` strips the prefix and returns just `prod_id`
- **`INSERT OR IGNORE`** with `UNIQUE(order_id, bought_at, offer_name, external_id)` — history is never overwritten; one Allegro order can have multiple rows (one per line item)
- **Table rendering** uses raw HTML (`unsafe_allow_html=True`) with CSS Grid for alignment; status badges are colored via `STATUS_COLORS` dict
- **Purchase price editing**: HTML `<input>` inside `unsafe_allow_html` cannot receive keyboard events (Streamlit intercepts at document level). Edit is triggered via `?edit=N` query param → `st.number_input` renders in sidebar. CSS checkbox hack (`[id^="chk-"]:checked ~ [id^="efc-"]`) toggles the edit link in the detail panel without scroll or page reload.
