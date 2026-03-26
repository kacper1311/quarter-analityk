from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests

ORDERS_URL   = "https://api.allegro.pl/order/checkout-forms"
BILLING_URL  = "https://api.allegro.pl/billing/billing-entries"
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


def get_billing_entries(token, date_from: str, date_to: str) -> list:
    """Pobiera wpisy billing z Allegro API z paginacją."""
    headers = get_headers(token)
    all_entries = []
    offset = 0

    while True:
        params = {
            "occurredAt.gte": date_from,
            "occurredAt.lte": date_to,
            "limit": 100,
            "offset": offset,
        }
        response = requests.get(BILLING_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        batch = data.get("billingEntries", [])
        for entry in batch:
            occurred = entry.get("occurredAt", "")
            try:
                dt = datetime.fromisoformat(occurred.replace("Z", "+00:00")).astimezone(WARSAW)
                date_str = dt.strftime("%Y-%m-%d")
                year_month = dt.strftime("%Y-%m")
            except Exception:
                date_str = ""
                year_month = ""

            value = entry.get("value") or {}
            tax   = entry.get("tax")   or {}
            entry_type = entry.get("type") or {}

            all_entries.append({
                "entry_id":  entry.get("id", ""),
                "type_id":   entry_type.get("id", ""),
                "type_name": entry_type.get("name", ""),
                "amount":    float(value.get("amount", 0) or 0),
                "tax":       float(tax.get("amount", 0) or 0),
                "date":      date_str,
                "year_month": year_month,
            })

        if len(batch) < 100:
            break
        offset += 100

    return all_entries


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
        paid_amount = float(((order.get("payment") or {}).get("paidAmount") or {}).get("amount", 0) or 0)
        total_to_pay = float(((order.get("summary") or {}).get("totalToPay") or {}).get("amount", 0) or 0)
        sale_price = paid_amount if paid_amount > 0 else total_to_pay
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
            item_price = float((item.get("price") or {}).get("amount", 0) or 0)
            item_qty = item.get("quantity", 1) or 1
            item_sale = round(item_price * item_qty, 2)
            # Dla zamówień wieloproduktowych: dostawa na poziomie zamówienia (nie per item)
            item_delivery = delivery_cost if len(line_items) == 1 else 0
            rows.append({
                "order_id": order_id,
                "date": date,
                "bought_at": bought_at_local,
                "time_local": time_local,
                "offer_name": (item.get("offer") or {}).get("name", ""),
                "external_id": ((item.get("offer") or {}).get("external") or {}).get("id", "") or "",
                "quantity": item_qty,
                "sale_price": item_sale,
                "delivery_cost": item_delivery,
                "status": status,
            })

    return rows
