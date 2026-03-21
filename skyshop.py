import requests

VAT = 1.23


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
    supplier = product.get("prod_sales", "")
    price = float(product.get("prod_buy_price", 0) or 0)
    if supplier == "Tayma":
        return round(price * 1.23, 2)
    return round(price, 2)


def extract_skyshop_id(external_id: str):
    if not external_id or "sky_" not in external_id:
        return None
    parts = external_id.replace("0$sky_", "").split("_")
    return parts[0] if parts else None


def build_price_map(products: dict) -> dict:
    price_map = {}
    seen_suppliers = set()
    for prod_id, product in products.items():
        supplier = product.get("prod_sales", "")
        if supplier not in seen_suppliers:
            print(f"prod_sales repr: {repr(supplier)}")
            seen_suppliers.add(supplier)
        price_map[str(prod_id)] = {
            "buy_price": get_purchase_price_brutto(product),
            "supplier": supplier,
        }
    return price_map
