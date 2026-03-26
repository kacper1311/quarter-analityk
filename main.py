import os
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import groupby as _groupby

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database
from allegro import get_orders, parse_orders, get_billing_entries
from auth import get_token, authorize, poll_for_token
from margins import calculate_margin
from skyshop import build_price_map, extract_skyshop_id, get_all_products, get_skyshop_order_notes

load_dotenv()

SKYSHOP_API_KEY = os.getenv("SKYSHOP_API_KEY")
SKYSHOP_API_URL = os.getenv("SKYSHOP_API_URL")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_MONTH_PL_LONG = {
    "01": "Styczeń",  "02": "Luty",       "03": "Marzec",
    "04": "Kwiecień", "05": "Maj",         "06": "Czerwiec",
    "07": "Lipiec",   "08": "Sierpień",    "09": "Wrzesień",
    "10": "Październik", "11": "Listopad", "12": "Grudzień",
}
_MONTH_PL_SHORT = {
    "01": "Sty", "02": "Lut", "03": "Mar", "04": "Kwi",
    "05": "Maj", "06": "Cze", "07": "Lip", "08": "Sie",
    "09": "Wrz", "10": "Paź", "11": "Lis", "12": "Gru",
}

def _ym_long(ym: str) -> str:
    if not ym or len(ym) < 7:
        return ym
    return f"{_MONTH_PL_LONG.get(ym[5:7], ym[5:7])} {ym[:4]}"

def _ym_short(ym: str) -> str:
    if not ym or len(ym) < 7:
        return ym
    return _MONTH_PL_SHORT.get(ym[5:7], ym[5:7])

templates.env.filters["ym_long"]  = _ym_long
templates.env.filters["ym_short"] = _ym_short

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
        if row.get("status") == "Nowe":
            sky_id = extract_skyshop_id(row.get("external_id", ""))
            price_data = price_map.get(sky_id) if sky_id else None
            if price_data:
                row["purchase_price"] = price_data["buy_price"]
                row["supplier"] = price_data["supplier"]
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
    return templates.TemplateResponse(request, "login.html", {
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

    metrics_cmp = database.get_metrics_comparison(date_from, date_to)

    return templates.TemplateResponse(request, "base.html", {
        "date_from": date_from,
        "date_to": date_to,
        "metrics": metrics_cmp["current"],
        "metrics_trends": metrics_cmp["trends"],
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

        sky_notes, sky_status_overrides = get_skyshop_order_notes(SKYSHOP_API_KEY, SKYSHOP_API_URL)
        if sky_notes:
            database.update_sky_notes(
                [{"order_id": oid, "sky_note": note} for oid, note in sky_notes.items()]
            )
        if sky_status_overrides:
            database.update_order_statuses(
                [{"order_id": oid, "status": s} for oid, s in sky_status_overrides.items()]
            )

        billing_since = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        billing_to    = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        billing_entries = get_billing_entries(token, billing_since, billing_to)
        if billing_entries:
            database.insert_billing_entries(billing_entries)

        # Faza 5: przelicz szablony pct_revenue dla bieżącego miesiąca
        current_ym = date.today().strftime("%Y-%m")
        summary_ym = database.get_orders_summary_for_month(current_ym)
        database.apply_templates_to_month(current_ym, summary_ym["revenue"])

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
# Routes — orders page + inline row editing (more specific paths first)
# ---------------------------------------------------------------------------

def _group_orders(rows: list) -> list:
    """Grupuje wiersze po order_id; zamówienia jednoproduktowe passthrough, wieloproduktowe z summary."""
    groups = []
    for order_id, items in _groupby(rows, key=lambda r: r["order_id"]):
        items = list(items)
        if len(items) == 1:
            groups.append({"multi": False, "rows": items})
        else:
            has_profit = all(r["profit"] is not None for r in items)
            total_sale = round(sum(r["sale_price"] for r in items), 2)
            total_purchase = round(sum(r["purchase_price"] or 0 for r in items), 2)
            total_profit = round(sum(r["profit"] or 0 for r in items), 2) if has_profit else None
            avg_margin = round(total_profit / total_sale * 100, 1) if has_profit and total_sale > 0 else None
            groups.append({
                "multi": True,
                "rows": items,
                "summary": {
                    "date":          items[-1]["date"],
                    "time_local":    items[-1]["time_local"],
                    "order_id":      order_id,
                    "n_products":    len(items),
                    "qty":           sum(r["quantity"] for r in items),
                    "sale_price":    total_sale,
                    "product_price": total_sale,
                    "delivery_cost": items[0]["delivery_cost"],
                    "purchase_price": total_purchase or None,
                    "profit":        total_profit,
                    "margin_pct":    avg_margin,
                    "status":        items[0]["status"],
                    "sky_note":      items[0].get("sky_note", ""),
                },
            })
    return groups


@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, date_from: str = None, date_to: str = None):
    token = _current_token()
    if not token:
        return RedirectResponse("/login")
    if date_from is None or date_to is None:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)
    return templates.TemplateResponse(request, "orders.html", {
        "order_groups": _group_orders(orders),
        "date_from": date_from,
        "date_to": date_to,
        "last_update": _last_update,
    })


@app.get("/orders/{row_id}/edit", response_class=HTMLResponse)
def order_edit_form(request: Request, row_id: int, date_from: str = None, date_to: str = None):
    """Returns row with edit form — HTMX swaps the <details> element."""
    if not date_from or not date_to:
        date_from, date_to = _default_dates()
    try:
        orders = database.get_orders(date_from, date_to)
        row = next((r for r in orders if r["id"] == row_id), None)
        if not row:
            return HTMLResponse(f'<div id="row-{row_id}" style="padding:12px;color:var(--red)">Błąd: nie znaleziono wiersza {row_id}</div>')
        return templates.TemplateResponse(request, "row_edit.html", {
            "row": row,
            "date_from": date_from,
            "date_to": date_to,
        })
    except Exception as e:
        return HTMLResponse(f'<div id="row-{row_id}" style="padding:12px;color:var(--red)">Błąd: {e}</div>')


@app.post("/orders/{row_id}/price", response_class=HTMLResponse)
def order_save_price(
    request: Request,
    row_id: int,
    price: float = Form(...),
    date_from: str = Form(None),
    date_to: str = Form(None),
):
    """Saves purchase price, returns updated normal row HTML."""
    if not date_from or not date_to:
        date_from, date_to = _default_dates()
    try:
        orders = database.get_orders(date_from, date_to)
        row = next((r for r in orders if r["id"] == row_id), None)
        if not row:
            return HTMLResponse(f'<div id="row-{row_id}" style="padding:12px;color:var(--red)">Błąd: nie znaleziono wiersza {row_id}</div>')

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

        orders = database.get_orders(date_from, date_to)
        row = next((r for r in orders if r["id"] == row_id), None)
        return templates.TemplateResponse(request, "row.html", {
            "row": row,
            "date_from": date_from,
            "date_to": date_to,
        })
    except Exception as e:
        return HTMLResponse(f'<div id="row-{row_id}" style="padding:12px;color:var(--red)">Błąd zapisu: {e}</div>')


@app.get("/orders/{row_id}", response_class=HTMLResponse)
def get_order_row(request: Request, row_id: int, date_from: str = None, date_to: str = None):
    """Returns normal row HTML — used by HTMX cancel button."""
    if not date_from or not date_to:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)
    row = next((r for r in orders if r["id"] == row_id), None)
    if not row:
        return HTMLResponse(f'<div id="row-{row_id}"></div>')
    return templates.TemplateResponse(request, "row.html", {
        "row": row,
        "date_from": date_from,
        "date_to": date_to,
    })


# ---------------------------------------------------------------------------
# Routes — costs page
# ---------------------------------------------------------------------------

def _costs_context(year_month: str) -> dict:
    """Compute context dict for costs_partial.html."""
    if not database.get_monthly_costs(year_month):
        summary = database.get_orders_summary_for_month(year_month)
        database.apply_templates_to_month(year_month, summary["revenue"])
    orders_summary  = database.get_orders_summary_for_month(year_month)
    billing_summary = database.get_billing_summary(year_month)
    monthly_costs   = database.get_monthly_costs(year_month)
    returned_orders = database.get_returned_orders_for_month(year_month)
    billing_net     = round(sum(r["total"] for r in billing_summary), 2)
    total_billing   = round(abs(billing_net), 2)
    total_custom    = round(sum(r["amount"] for r in monthly_costs), 2)
    revenue         = orders_summary["revenue"]
    purchase_cost   = orders_summary["purchase_cost"]
    net_profit      = round(revenue - purchase_cost + billing_net - total_custom, 2)
    return {
        "year_month":      year_month,
        "revenue":         revenue,
        "purchase_cost":   purchase_cost,
        "billing_summary": billing_summary,
        "total_billing":   total_billing,
        "monthly_costs":   monthly_costs,
        "total_custom":    total_custom,
        "net_profit":      net_profit,
        "returned_orders": returned_orders,
    }


@app.get("/costs", response_class=HTMLResponse)
def costs_page(request: Request, year_month: str = None):
    token = _current_token()
    if not token:
        return RedirectResponse("/login")
    year_months = database.get_available_year_months()
    years = sorted({ym[:4] for ym in year_months}, reverse=True)
    year_to_latest = {y: next(ym for ym in year_months if ym.startswith(y)) for y in years}
    if not year_month and year_months:
        year_month = year_months[0]
    selected_year = year_month[:4] if year_month else (years[0] if years else None)
    selected_year_months = [ym for ym in year_months if ym.startswith(selected_year)] if selected_year else []
    ctx = _costs_context(year_month) if year_month else {}
    return templates.TemplateResponse(request, "costs.html", {
        "year_months":           year_months,
        "years":                 years,
        "year_to_latest":        year_to_latest,
        "selected_year":         selected_year,
        "selected_year_months":  selected_year_months,
        "last_update":           _last_update,
        **ctx,
    })


@app.post("/costs/cost", response_class=HTMLResponse)
def cost_add(
    request: Request,
    year_month: str = Form(...),
    name: str = Form(...),
    amount: float = Form(...),
):
    name = name.strip()
    if name:
        database.upsert_monthly_cost(year_month, name, amount, is_default=0)
    return templates.TemplateResponse(request, "costs_partial.html", _costs_context(year_month))


@app.get("/costs/cost/{cost_id}/edit", response_class=HTMLResponse)
def cost_edit_form(request: Request, cost_id: int, year_month: str = None):
    cost = database.get_monthly_cost(cost_id)
    if not cost:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "cost_row_edit.html", {
        "cost": cost,
        "year_month": year_month or cost["year_month"],
    })


@app.get("/costs/cost/{cost_id}", response_class=HTMLResponse)
def cost_view_row(request: Request, cost_id: int, year_month: str = None):
    cost = database.get_monthly_cost(cost_id)
    if not cost:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "cost_row.html", {
        "cost": cost,
        "year_month": year_month or cost["year_month"],
    })


@app.post("/costs/cost/{cost_id}", response_class=HTMLResponse)
def cost_save(
    request: Request,
    cost_id: int,
    amount: float = Form(...),
    year_month: str = Form(...),
):
    database.update_monthly_cost(cost_id, amount)
    return templates.TemplateResponse(request, "costs_partial.html", _costs_context(year_month))


@app.delete("/costs/cost/{cost_id}", response_class=HTMLResponse)
def cost_delete(request: Request, cost_id: int, year_month: str = None):
    cost = database.get_monthly_cost(cost_id)
    ym = year_month or (cost["year_month"] if cost else "")
    database.delete_monthly_cost(cost_id)
    if not ym:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "costs_partial.html", _costs_context(ym))


# ---------------------------------------------------------------------------
# Routes — settings (static paths before dynamic)
# ---------------------------------------------------------------------------

@app.get("/settings/templates", response_class=HTMLResponse)
def settings_templates_list(request: Request):
    token = _current_token()
    if not token:
        return HTMLResponse("", status_code=401)
    tmpl_list = database.get_recurring_templates()
    return templates.TemplateResponse(request, "settings_templates.html", {
        "tmpl_list": tmpl_list,
    })


@app.post("/settings/templates", response_class=HTMLResponse)
def settings_template_add(
    request: Request,
    name: str = Form(...),
    amount: float = Form(...),
):
    name = name.strip()
    if not name:
        tmpl_list = database.get_recurring_templates()
        return templates.TemplateResponse(request, "settings_templates.html", {
            "tmpl_list": tmpl_list,
            "error": "Nazwa jest wymagana.",
        })
    try:
        database.insert_recurring_template(name, "fixed", amount, None)
    except Exception:
        tmpl_list = database.get_recurring_templates()
        return templates.TemplateResponse(request, "settings_templates.html", {
            "tmpl_list": tmpl_list,
            "error": f"Szablon o nazwie '{name}' już istnieje.",
        })
    current_ym = date.today().strftime("%Y-%m")
    database.propagate_templates_forward(current_ym)
    tmpl_list = database.get_recurring_templates()
    return templates.TemplateResponse(request, "settings_templates.html", {
        "tmpl_list": tmpl_list,
    })


@app.get("/settings/templates/{template_id}/edit", response_class=HTMLResponse)
def settings_template_edit_form(request: Request, template_id: int):
    t = database.get_recurring_template(template_id)
    if not t:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "settings_template_row_edit.html", {"t": t})


@app.post("/settings/templates/{template_id}", response_class=HTMLResponse)
def settings_template_save(
    request: Request,
    template_id: int,
    amount: float = Form(None),
    rate: float = Form(None),
):
    database.update_recurring_template(template_id, amount, rate)
    current_ym = date.today().strftime("%Y-%m")
    database.propagate_templates_forward(current_ym)
    t = database.get_recurring_template(template_id)
    return templates.TemplateResponse(request, "settings_template_row.html", {"t": t})


@app.get("/settings/templates/{template_id}", response_class=HTMLResponse)
def settings_template_view(request: Request, template_id: int):
    t = database.get_recurring_template(template_id)
    if not t:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "settings_template_row.html", {"t": t})


@app.delete("/settings/templates/{template_id}", response_class=HTMLResponse)
def settings_template_delete(request: Request, template_id: int):
    database.delete_recurring_template(template_id)
    tmpl_list = database.get_recurring_templates()
    return templates.TemplateResponse(request, "settings_templates.html", {
        "tmpl_list": tmpl_list,
    })


# ---------------------------------------------------------------------------
# API — chart data
# ---------------------------------------------------------------------------

@app.get("/api/chart/daily-revenue")
def chart_daily_revenue(date_from: str = None, date_to: str = None):
    """Przychód per dzień dla wybranego zakresu."""
    if not date_from or not date_to:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)

    day_revenue: dict[str, float] = defaultdict(float)
    day_seen: dict[str, set] = defaultdict(set)
    for row in orders:
        d = row.get("date", "")
        oid = row.get("order_id", "")
        if d and oid and row.get("status") not in {"Anulowane", "Zwrot towaru", "Nowe"} and oid not in day_seen[d]:
            day_seen[d].add(oid)
            day_revenue[d] += row.get("sale_price", 0.0)

    d_from = datetime.strptime(date_from, "%Y-%m-%d")
    d_to   = datetime.strptime(date_to,   "%Y-%m-%d")
    labels, values, counts = [], [], []
    cur = d_from
    while cur <= d_to:
        s = cur.strftime("%Y-%m-%d")
        labels.append(cur.strftime("%d.%m"))
        values.append(round(day_revenue.get(s, 0.0), 2))
        counts.append(len(day_seen.get(s, set())))
        cur += timedelta(days=1)

    return JSONResponse({"labels": labels, "values": values, "counts": counts})


@app.get("/api/chart/heatmap")
def chart_heatmap(date_from: str = None, date_to: str = None):
    """Liczba zamówień per (dzień_tygodnia, godzina). Macierz 7×24."""
    if not date_from or not date_to:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)

    matrix = [[0] * 24 for _ in range(7)]
    seen_per_cell: dict[tuple, set] = defaultdict(set)

    for row in orders:
        tl  = row.get("time_local", "")
        d   = row.get("date", "")
        oid = row.get("order_id", "")
        if not tl or not d or not oid:
            continue
        try:
            dt  = datetime.strptime(f"{d} {tl}", "%Y-%m-%d %H:%M")
            dow  = dt.weekday()
            hour = dt.hour
            key  = (dow, hour)
            if oid not in seen_per_cell[key]:
                seen_per_cell[key].add(oid)
                matrix[dow][hour] += 1
        except Exception:
            continue

    days = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sb", "Nd"]
    return JSONResponse({"matrix": matrix, "days": days})


@app.get("/api/top-products")
def top_products(date_from: str = None, date_to: str = None, limit: int = 5):
    """Top produkty wg przychodu."""
    if not date_from or not date_to:
        date_from, date_to = _default_dates()
    orders = database.get_orders(date_from, date_to)

    products: dict[str, dict] = {}
    for row in orders:
        if row.get("status") in {"Anulowane", "Zwrot towaru", "Nowe"}:
            continue
        name = row.get("offer_name") or "–"
        key  = name[:60]
        if key not in products:
            products[key] = {"name": name, "revenue": 0.0, "sales": 0,
                              "profit": 0.0, "has_profit": False}
        p = products[key]
        p["revenue"] += row.get("sale_price", 0.0)
        p["sales"]   += row.get("quantity", 1)
        if row.get("profit") is not None:
            p["profit"]    += row["profit"]
            p["has_profit"] = True

    sorted_p  = sorted(products.values(), key=lambda x: x["revenue"], reverse=True)[:limit]
    total_rev = sum(x["revenue"] for x in sorted_p) or 1

    result = []
    for p in sorted_p:
        margin = round(p["profit"] / p["revenue"] * 100, 1) if p["has_profit"] and p["revenue"] else None
        result.append({
            "name":    p["name"][:55] + ("…" if len(p["name"]) > 55 else ""),
            "revenue": round(p["revenue"], 2),
            "sales":   p["sales"],
            "margin":  margin,
            "share":   round(p["revenue"] / total_rev * 100, 1),
        })
    return JSONResponse({"products": result})
