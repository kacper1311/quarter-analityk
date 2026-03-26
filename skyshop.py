import json as _json
from datetime import datetime

import requests

def get_all_products(api_key, api_url) -> dict:
    products = {}
    start = 0

    while True:
        url = f"{api_url}?function=getProducts&APIkey={api_key}&start={start}&limit=1000"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        batch_count = 0
        for key, value in data.items():
            if key != "response_code" and isinstance(value, dict):
                prod_id = value.get("prod_id")
                if prod_id:
                    products[str(prod_id)] = value
                    batch_count += 1

        if batch_count == 0:
            break
        start += 1000

    return products


def get_purchase_price_brutto(product: dict) -> float:
    price = float(product.get("prod_buy_price", 0) or 0)
    return round(price, 2)


def extract_skyshop_id(external_id: str):
    if not external_id or "sky_" not in external_id:
        return None
    parts = external_id.replace("0$sky_", "").split("_")
    return parts[0] if parts else None


_SKYSHOP_STATUSES = [
    "new", "waiting_for_shipment", "waiting_for_send", "send",
    "ready", "finished", "canceled", "returned",
    "optional_one", "optional_two",
]

# Statusy SkyShop, które nadpisują status z Allegro w naszej bazie
SKYSHOP_STATUS_MAP = {
    "optional_one": "Zwrot towaru",
    "optional_two": "Płatność przy odbiorze wysłane",
}


def get_skyshop_order_notes(api_key, api_url) -> tuple[dict, dict]:
    """Zwraca (notes_dict, status_overrides_dict).

    notes_dict: {allegro_order_id: note}
    status_overrides_dict: {allegro_order_id: polska_nazwa_statusu}
        — tylko dla statusów z SKYSHOP_STATUS_MAP
    """
    notes = {}
    status_overrides = {}
    date_from = "2026-01-01"
    date_to = "2030-12-31"

    for status in _SKYSHOP_STATUSES:
        start = 0
        while True:
            url = (
                f"{api_url}?function=getOrders&APIkey={api_key}"
                f"&dateStart={date_from}&dateEnd={date_to}"
                f"&status={status}&fix_on_array=1&start={start}&limit=1000"
            )
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()

            orders_list = data.get("data", [])
            if not isinstance(orders_list, list):
                break

            for value in orders_list:
                if not isinstance(value, dict):
                    continue
                note = value.get("ord_note_admin", "") or ""
                additional_raw = value.get("ord_additional_data", "") or ""
                try:
                    additional = _json.loads(additional_raw)
                    allegro_order_id = (additional.get("auctionForm") or {}).get("id", "")
                except Exception:
                    allegro_order_id = ""
                if allegro_order_id:
                    if note:
                        notes[allegro_order_id] = note
                    if status in SKYSHOP_STATUS_MAP:
                        actual_status = value.get("ord_status", "")
                        if actual_status == status:
                            status_overrides[allegro_order_id] = SKYSHOP_STATUS_MAP[status]

            if len(orders_list) < 1000:
                break
            start += 1000

    return notes, status_overrides


def build_price_map(products: dict) -> dict:
    price_map = {}
    for prod_id, product in products.items():
        supplier = product.get("prod_sales", "")
        price_map[str(prod_id)] = {
            "buy_price": get_purchase_price_brutto(product),
            "supplier": supplier,
        }
    return price_map
