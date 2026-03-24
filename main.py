import os
import traceback
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database
from allegro import get_orders, parse_orders
from auth import get_token, authorize, poll_for_token
from margins import calculate_margin
from skyshop import build_price_map, extract_skyshop_id, get_all_products, get_skyshop_order_notes

load_dotenv()

SKYSHOP_API_KEY = os.getenv("SKYSHOP_API_KEY")
SKYSHOP_API_URL = os.getenv("SKYSHOP_API_URL")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Status badge colors shared with templates
templates.env.globals["STATUS_COLORS"] = {
    "Odebrane":     {"bg": "#e8f5e9", "tc": "#1b5e20"},
    "Anulowane":    {"bg": "#ffebee", "tc": "#b71c1c"},
    "W realizacji": {"bg": "#fff3e0", "tc": "#e65100"},
}
templates.env.globals["STATUS_DEFAULT_COLOR"] = {"bg": "#eceff1", "tc": "#37474f"}

database.init_db()

# Module-level state (single-user app)
_auth_flow: dict | None = None
_last_update: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_token() -> str | None:
    return get_token()


def _default_dates() -> tuple[str, str]:
    return (
        str(date.today() - timedelta(days=30)),
        str(date.today()),
    )


def _calc_metrics(orders: list) -> dict:
    if not orders:
        return {"unique_orders": 0, "total_revenue": 0.0, "avg_order": 0.0, "total_profit": None}

    seen: dict[str, float] = {}
    total_profit = 0.0
    has_profit = False

    for row in orders:
        oid = row["order_id"]
        if oid not in seen:
            seen[oid] = row["sale_price"]
        if row.get("profit") is not None:
            total_profit += row["profit"]
            has_profit = True

    unique_orders = len(seen)
    total_revenue = sum(seen.values())
    return {
        "unique_orders": unique_orders,
        "total_revenue": total_revenue,
        "avg_order": total_revenue / unique_orders if unique_orders > 0 else 0.0,
        "total_profit": total_profit if has_profit else None,
    }


def enrich_with_margins(rows: list, price_map: dict) -> list:
    for row in rows:
        if row.get("status") == "Anulowane":
            row["purchase_price"] = None
            row["supplier"] = None
            row["profit"] = None
            row["margin_pct"] = None
            continue
        sky_id = extract_skyshop_id(row.get("external_id", ""))
        price_data = price_map.get(sky_id) if sky_id else None
        if price_data:
            m = calculate_margin(
                sale_price=row["sale_price"],
                purchase_price_brutto=price_data["buy_price"],
                delivery_cost=row["delivery_cost"],
            )
            row["purchase_price"] = price_data["buy_price"]
            row["supplier"] = price_data["supplier"]
            row["profit"] = m["profit"]
            row["margin_pct"] = m["margin_pct"]
        else:
            row["purchase_price"] = None
            row["supplier"] = None
            row["profit"] = None
            row["margin_pct"] = None
    return rows


# ---------------------------------------------------------------------------
# Routes — auth
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "auth_flow": _auth_flow,
    })


@app.post("/auth/start")
def auth_start():
    global _auth_flow
    try:
        _auth_flow = authorize()
    except Exception as e:
        # On error, redirect back — login page will show no flow
        pass
    return RedirectResponse("/login", status_code=303)


@app.post("/auth/poll")
def auth_poll(request: Request):
    global _auth_flow
    if not _auth_flow:
        return RedirectResponse("/login", status_code=303)
    try:
        result = poll_for_token(
            _auth_flow["device_code"],
            _auth_flow.get("interval", 5),
        )
        if result:
            _auth_flow = None
            return RedirectResponse("/", status_code=303)
    except Exception:
        pass
    return RedirectResponse("/login", status_code=303)


@app.post("/logout")
def logout():
    if os.path.exists("tokens.json"):
        os.remove("tokens.json")
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Routes — dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, date_from: str = None, date_to: str = None):
    token = _current_token()
    if not token:
        return RedirectResponse("/login")

    if date_from is None or date_to is None:
        date_from, date_to = _default_dates()

    orders = database.get_orders(date_from, date_to)
    metrics = _calc_metrics(orders)

    return templates.TemplateResponse("base.html", {
        "request": request,
        "orders": orders,
        "date_from": date_from,
        "date_to": date_to,
        "metrics": metrics,
        "last_update": _last_update,
    })


@app.post("/sync", response_class=HTMLResponse)
def sync(request: Request):
    global _last_update
    token = _current_token()
    if not token:
        return HTMLResponse('<span id="sync-status" style="color:#e53935">Brak autoryzacji — zaloguj się ponownie.</span>')

    try:
        products = get_all_products(SKYSHOP_API_KEY, SKYSHOP_API_URL)
        price_map = build_price_map(products)
        database.upsert_price_map(price_map)

        last_ba = database.get_last_bought_at()
        raw = get_orders(token, date_from=last_ba)
        raw = [o for o in raw if o is not None]
        parsed = parse_orders(raw)
        if parsed:
            enriched = enrich_with_margins(parsed, price_map)
            database.insert_orders(enriched)

        status_since = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_recent = get_orders(token, date_from=status_since)
        raw_recent = [o for o in raw_recent if o is not None]
        parsed_recent = parse_orders(raw_recent)
        status_map = {r["order_id"]: r["status"] for r in parsed_recent}
        database.update_order_statuses(
            [{"order_id": oid, "status": s} for oid, s in status_map.items()]
        )

        sky_notes = get_skyshop_order_notes(SKYSHOP_API_KEY, SKYSHOP_API_URL)
        if sky_notes:
            database.update_sky_notes(
                [{"order_id": oid, "sky_note": note} for oid, note in sky_notes.items()]
            )

        _last_update = datetime.now().strftime("%Y-%m-%d %H:%M")
        count = database.count_orders()

        response = HTMLResponse(
            content=f'<span id="sync-status" style="color:#00c853">✅ {count} wierszy · {_last_update}</span>'
        )
        response.headers["HX-Refresh"] = "true"
        return response

    except Exception as e:
        tb = traceback.format_exc()
        if "401" in str(e):
            if os.path.exists("tokens.json"):
                os.remove("tokens.json")
        return HTMLResponse(
            f'<span id="sync-status" style="color:#e53935">Błąd: {e}</span>'
            f'<pre style="font-size:11px;color:#888;white-space:pre-wrap">{tb}</pre>'
        )


# ---------------------------------------------------------------------------
# Routes — inline row editing
# ---------------------------------------------------------------------------

@app.get("/orders/{row_id}", response_class=HTMLResponse)
def get_order_row(request: Request, row_id: int, date_from: str = None, date_to: str = None):
    """Returns normal row HTML — used by HTMX cancel button."""
    if date_from is None or date_to is None:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)
    row = next((r for r in orders if r["id"] == row_id), None)
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("row.html", {
        "request": request,
        "row": row,
        "date_from": date_from,
        "date_to": date_to,
    })


@app.get("/orders/{row_id}/edit", response_class=HTMLResponse)
def order_edit_form(request: Request, row_id: int, date_from: str = None, date_to: str = None):
    """Returns row with edit form — HTMX swaps the <details> element."""
    if date_from is None or date_to is None:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)
    row = next((r for r in orders if r["id"] == row_id), None)
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("row_edit.html", {
        "request": request,
        "row": row,
        "date_from": date_from,
        "date_to": date_to,
    })


@app.post("/orders/{row_id}/price", response_class=HTMLResponse)
def order_save_price(
    request: Request,
    row_id: int,
    price: float = Form(...),
    date_from: str = Form(None),
    date_to: str = Form(None),
):
    """Saves purchase price, returns updated normal row HTML."""
    if date_from is None or date_to is None:
        date_from, date_to = _default_dates()

    orders = database.get_orders(date_from, date_to)
    row = next((r for r in orders if r["id"] == row_id), None)
    if not row:
        raise HTTPException(status_code=404)

    m = calculate_margin(
        sale_price=row["sale_price"],
        purchase_price_brutto=price,
        delivery_cost=row["delivery_cost"],
    )
    database.update_purchase_price(
        row_id=row_id,
        purchase_price=price,
        profit=m["profit"],
        margin_pct=m["margin_pct"],
    )

    # Reload updated row from DB
    orders = database.get_orders(date_from, date_to)
    row = next((r for r in orders if r["id"] == row_id), None)

    return templates.TemplateResponse("row.html", {
        "request": request,
        "row": row,
        "date_from": date_from,
        "date_to": date_to,
    })
