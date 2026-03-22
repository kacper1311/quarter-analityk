from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests

ORDERS_URL = "https://api.allegro.pl/order/checkout-forms"
WARSAW = ZoneInfo("Europe/Warsaw")


def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.allegro.public.v1+json",
    }


def get_orders(token, date_from=None):
    headers = get_headers(token)
    all_orders = []
    offset = 0

    while True:
        params = {
            "limit": 100,
            "offset": offset,
        }
        if date_from:
            # Upewnij się że format to pełny ISO 8601
            date_str = str(date_from)
            if len(date_str) == 10:  # samo YYYY-MM-DD
                date_str = date_str + "T00:00:00Z"
            params["boughtAt.gte"] = date_str

        response = requests.get(ORDERS_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        batch = data.get("checkoutForms", [])
        all_orders.extend(batch)

        if len(batch) < 100:
            break
        offset += 100

    return all_orders


FULFILLMENT_MAP = {
    "NEW": "Nowe",
    "PROCESSING": "W realizacji",
    "SUSPENDED": "Wstrzymane",
    "READY_FOR_SHIPMENT": "Do wysłania",
    "READY_FOR_PICKUP": "Do odbioru",
    "SENT": "Wysłane",
    "PICKED_UP": "Odebrane",
    "CANCELLED": "Anulowane",
}


def parse_orders(orders_raw):
    rows = []
    for order in orders_raw:
        order_id = order.get("id", "")
        fulfillment_raw = (order.get("fulfillment") or {}).get("status", "")
        status = FULFILLMENT_MAP.get(fulfillment_raw, fulfillment_raw)
        sale_price = float(((order.get("payment") or {}).get("paidAmount") or {}).get("amount", 0) or 0)
        delivery_cost = float((order.get("delivery") or {}).get("cost", {}).get("amount", 0) or 0)

        line_items = order.get("lineItems", [])
        if not line_items:
            rows.append({
                "order_id": order_id,
                "date": "",
                "bought_at": "",
                "time_local": "",
                "offer_name": "",
                "external_id": "",
                "quantity": 0,
                "sale_price": sale_price,
                "delivery_cost": delivery_cost,
                "status": status,
            })
            continue

        for item in line_items:
            bought_at_raw = item.get("boughtAt", "") or ""
            if bought_at_raw:
                dt_utc = datetime.fromisoformat(bought_at_raw.replace("Z", "+00:00"))
                dt_warsaw = dt_utc.astimezone(WARSAW)
                date = dt_warsaw.strftime("%Y-%m-%d")
                time_local = dt_warsaw.strftime("%H:%M")
                bought_at_local = dt_warsaw.isoformat()
            else:
                date = ""
                time_local = ""
                bought_at_local = ""
            rows.append({
                "order_id": order_id,
                "date": date,
                "bought_at": bought_at_local,
                "time_local": time_local,
                "offer_name": (item.get("offer") or {}).get("name", ""),
                "external_id": ((item.get("offer") or {}).get("external") or {}).get("id", "") or "",
                "quantity": item.get("quantity", 0),
                "sale_price": sale_price,
                "delivery_cost": delivery_cost,
                "status": status,
            })

    return rows
