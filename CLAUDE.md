# QUARTER Analytics — Etap 2: Integracja SkyShop + Kalkulator marży

## Co już istnieje (nie modyfikuj bez potrzeby)
- `auth.py` — autoryzacja Allegro
- `allegro.py` — pobieranie i parsowanie zamówień
- `app.py` — główna aplikacja Streamlit

---

## Nowy plik: `skyshop.py`

### Stałe
```python
VAT = 1.23
```

### Funkcja `get_all_products(api_key, api_url) -> dict`
- Pobiera wszystkie produkty z SkyShop przez `getProducts`
- Paginacja: `start=0`, `limit=1000`, zwiększaj `start` o 1000 dopóki wyniki nie są puste
- Endpoint: `GET {api_url}?function=getProducts&APIkey={api_key}&start={start}&limit=1000`
- Zwraca słownik: `{ prod_id: produkt_dict }` (klucz to string `prod_id`)
- SkyShop zwraca JSON: `{"response_code": 200, "0": {...}, "1": {...}, ...}`
- Iteruj po kluczach pomijając `"response_code"`:
```python
for key, value in data.items():
    if key != "response_code" and isinstance(value, dict):
        prod_id = value.get("prod_id")
        if prod_id:
            products[str(prod_id)] = value
```

### Funkcja `get_purchase_price_brutto(product: dict) -> float`
Przelicza cenę zakupu na brutto w zależności od hurtowni:
- `prod_sales == "Tayma"` → cena netto → `round(float(prod_buy_price) * 1.23, 2)`
- `prod_sales == "VIVAB2B"` → cena już brutto → `round(float(prod_buy_price), 2)`
- inne → `round(float(prod_buy_price) * 1.23, 2)` (domyślnie traktuj jak netto)

### Funkcja `extract_skyshop_id(external_id: str) -> str | None`
Wyciąga prod_id z external_id Allegro.

Format external_id z Allegro: `"0$sky_4072_1773514019_728952"`
```python
if not external_id or "sky_" not in external_id:
    return None
parts = external_id.replace("0$sky_", "").split("_")
return parts[0] if parts else None
```
Zwraca `"4072"` dla powyższego przykładu.

### Funkcja `build_price_map(products: dict) -> dict`
Zwraca: `{ prod_id: {"price_brutto": float, "supplier": str, "name": str} }`
```python
price_map = {}
for prod_id, product in products.items():
    price_map[str(prod_id)] = {
        "price_brutto": get_purchase_price_brutto(product),
        "supplier": product.get("prod_sales", ""),
        "name": product.get("prod_name", ""),
    }
return price_map
```

---

## Nowy plik: `margins.py`

### Stałe
```python
import math
PROWIZJA_ALLEGRO = 0.12
OPLATA_PLATNICZA = 0.015
KOSZTY_STALE = 1245  # ZUS 650 + księgowość 320 + SkyShop 275
CEL_NETTO = 4000
```

### Funkcja `calculate_margin(sale_price, purchase_price_brutto, delivery_cost) -> dict`
```python
allegro_commission = round(sale_price * PROWIZJA_ALLEGRO, 2)
payment_fee = round(sale_price * OPLATA_PLATNICZA, 2)
profit = round(sale_price - purchase_price_brutto - allegro_commission - payment_fee - delivery_cost, 2)
margin_pct = round((profit / sale_price) * 100, 1) if sale_price > 0 else 0.0
return {
    "allegro_commission": allegro_commission,
    "payment_fee": payment_fee,
    "profit": profit,
    "margin_pct": margin_pct,
}
```

### Funkcja `calculate_summary(margins: list[dict]) -> dict`
```python
total_orders = len(margins)
total_revenue = sum(m["sale_price"] for m in margins)
total_profit = sum(m["profit"] for m in margins)
avg_margin_pct = round(sum(m["margin_pct"] for m in margins) / total_orders, 1) if total_orders > 0 else 0
avg_profit_per_order = round(total_profit / total_orders, 2) if total_orders > 0 else 0
profit_after_fixed = round(total_profit - KOSZTY_STALE, 2)
goal_gap = round(CEL_NETTO - profit_after_fixed, 2)
orders_needed = math.ceil(goal_gap / avg_profit_per_order) if avg_profit_per_order > 0 and goal_gap > 0 else 0
return {
    "total_orders": total_orders,
    "total_revenue": total_revenue,
    "total_profit": total_profit,
    "avg_margin_pct": avg_margin_pct,
    "avg_profit_per_order": avg_profit_per_order,
    "profit_after_fixed": profit_after_fixed,
    "goal_gap": goal_gap,
    "goal_reached": profit_after_fixed >= CEL_NETTO,
    "orders_needed": orders_needed,
}
```

---

## Modyfikacje `allegro.py`

W funkcji `parse_orders` dodaj pole `external_id` do każdego wiersza:
```python
"external_id": ((item.get("offer") or {}).get("external") or {}).get("id", "") or ""
```

---

## Modyfikacje `app.py`

### Nowe importy
```python
from skyshop import get_all_products, build_price_map, extract_skyshop_id
from margins import calculate_margin, calculate_summary
import math
import os
```

### Nowe zmienne środowiskowe
```python
SKYSHOP_API_KEY = os.getenv("SKYSHOP_API_KEY")
SKYSHOP_API_URL = os.getenv("SKYSHOP_API_URL")
```

### Nowy session_state
```python
if "price_map" not in st.session_state:
    st.session_state.price_map = None
```

### Przycisk w sidebarze — dodaj POD przyciskiem "Pobierz zamówienia"
```python
if st.button("🔄 Pobierz ceny z SkyShop"):
    with st.spinner("Pobieranie produktów z SkyShop..."):
        try:
            products = get_all_products(SKYSHOP_API_KEY, SKYSHOP_API_URL)
            st.session_state.price_map = build_price_map(products)
            st.success(f"✅ Pobrano {len(st.session_state.price_map)} produktów")
        except Exception as e:
            st.error(f"Błąd SkyShop: {e}")
```

### Funkcja wzbogacania df o marże — dodaj przed głównym widokiem
```python
def enrich_with_margins(df, price_map):
    rows = df.to_dict("records")
    for row in rows:
        sky_id = extract_skyshop_id(row.get("external_id", ""))
        price_data = price_map.get(sky_id) if sky_id else None
        if price_data:
            m = calculate_margin(
                sale_price=row["sale_price"],
                purchase_price_brutto=price_data["price_brutto"],
                delivery_cost=row["delivery_cost"],
            )
            row["purchase_price"] = price_data["price_brutto"]
            row["supplier"] = price_data["supplier"]
            row["allegro_commission"] = m["allegro_commission"]
            row["profit"] = m["profit"]
            row["margin_pct"] = m["margin_pct"]
        else:
            row["purchase_price"] = None
            row["supplier"] = None
            row["allegro_commission"] = None
            row["profit"] = None
            row["margin_pct"] = None
    return pd.DataFrame(rows)
```

### Główny widok — zastąp istniejącą sekcję metryk i tabeli

```python
if st.session_state.orders is not None:
    df = st.session_state.orders

    if df.empty:
        st.warning("Brak zamówień w wybranym zakresie dat.")
    else:
        # Wzbogać o marże jeśli dostępna mapa cen
        if st.session_state.price_map:
            df = enrich_with_margins(df, st.session_state.price_map)
            df_with_margin = df[df["profit"].notna()]
            margins_list = df_with_margin.to_dict("records")
            summary = calculate_summary(margins_list) if margins_list else None
        else:
            summary = None

        # Metryki
        col1, col2, col3, col4, col5 = st.columns(5)
        unique_orders = df["order_id"].nunique()
        total_revenue = df.drop_duplicates("order_id")["sale_price"].sum()
        avg_order = total_revenue / unique_orders if unique_orders > 0 else 0

        col1.metric("Zamówienia", unique_orders)
        col2.metric("Przychód", f"{total_revenue:,.2f} zł")
        col3.metric("Śr. zamówienie", f"{avg_order:,.2f} zł")

        if summary:
            col4.metric("Zysk netto", f"{summary['total_profit']:,.2f} zł")
            col5.metric("Śr. marża", f"{summary['avg_margin_pct']}%")

            # Blok celu
            st.divider()
            if summary["goal_reached"]:
                st.success(f"✅ Cel osiągnięty! Zysk po kosztach stałych: {summary['profit_after_fixed']:,.2f} zł")
            elif summary["profit_after_fixed"] >= 0:
                st.warning(f"⚠️ Brakuje {summary['goal_gap']:,.2f} zł do celu 4000 zł (~{summary['orders_needed']} zamówień przy obecnej średniej {summary['avg_profit_per_order']:.2f} zł/zam.)")
            else:
                st.error(f"❌ Zysk netto poniżej kosztów stałych (1 245 zł). Obecny zysk: {summary['total_profit']:,.2f} zł")
        else:
            col4.metric("Zysk netto", "–")
            col5.metric("Śr. marża", "–")
            st.info("ℹ️ Kliknij 'Pobierz ceny z SkyShop' aby zobaczyć marże i zysk netto")

        # Tabela
        st.divider()
        columns_base = ["date", "offer_name", "quantity", "sale_price", "delivery_cost", "status"]
        columns_margin = ["purchase_price", "supplier", "allegro_commission", "profit", "margin_pct"]
        show_cols = columns_base + (columns_margin if summary else [])
        display_df = df[[c for c in show_cols if c in df.columns]].sort_values("date", ascending=False)

        rename_map = {
            "date": "Data",
            "offer_name": "Nazwa oferty",
            "quantity": "Ilość",
            "sale_price": "Cena sprzedaży (zł)",
            "delivery_cost": "Koszt dostawy (zł)",
            "status": "Status",
            "purchase_price": "Cena zakupu (zł)",
            "supplier": "Hurtownia",
            "allegro_commission": "Prowizja (zł)",
            "profit": "Zysk netto (zł)",
            "margin_pct": "Marża %",
        }
        display_df = display_df.rename(columns=rename_map)

        col_config = {
            "Cena sprzedaży (zł)": st.column_config.NumberColumn(format="%.2f zł"),
            "Koszt dostawy (zł)": st.column_config.NumberColumn(format="%.2f zł"),
            "Cena zakupu (zł)": st.column_config.NumberColumn(format="%.2f zł"),
            "Prowizja (zł)": st.column_config.NumberColumn(format="%.2f zł"),
            "Zysk netto (zł)": st.column_config.NumberColumn(format="%.2f zł"),
            "Marża %": st.column_config.NumberColumn(format="%.1f%%"),
        }

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=600,
            column_config=col_config,
        )
```

---

## Kolejność działania w aplikacji po zmianach
1. Pobierz zamówienia (zakres dat → "Pobierz zamówienia")
2. Kliknij "Pobierz ceny z SkyShop"
3. Marże, zysk netto i progress do celu pojawią się automatycznie