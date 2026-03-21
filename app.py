import os
import traceback
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from allegro import get_orders, parse_orders
from auth import authorize, get_token, poll_for_token
from margins import calculate_margin
from skyshop import build_price_map, extract_skyshop_id, get_all_products

SKYSHOP_API_KEY = os.getenv("SKYSHOP_API_KEY")
SKYSHOP_API_URL = os.getenv("SKYSHOP_API_URL")

st.set_page_config(page_title="QUARTER Analytics", layout="wide")

# --- Session state ---
if "token" not in st.session_state:
    st.session_state.token = get_token()
if "orders" not in st.session_state:
    st.session_state.orders = None
if "auth_flow" not in st.session_state:
    st.session_state.auth_flow = None
if "price_map" not in st.session_state:
    st.session_state.price_map = None


def enrich_with_margins(df, price_map):
    rows = df.to_dict("records")
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
    return pd.DataFrame(rows)


# --- Ekran 1: Brak autoryzacji ---
if st.session_state.token is None:
    st.title("QUARTER Analytics")
    st.write("Połącz konto Allegro aby rozpocząć.")

    if st.session_state.auth_flow is None:
        if st.button("Połącz z Allegro"):
            try:
                flow = authorize()
                st.session_state.auth_flow = flow
                st.rerun()
            except Exception as e:
                st.error(f"Błąd podczas inicjowania autoryzacji: {e}")
    else:
        flow = st.session_state.auth_flow
        uri = flow.get("verification_uri_complete", flow.get("verification_uri", ""))
        st.markdown(f"[Kliknij tutaj aby autoryzować]({uri})")

        if st.button("Już autoryzowałem"):
            try:
                result = poll_for_token(
                    flow["device_code"],
                    flow.get("interval", 5),
                )
                if result:
                    st.session_state.token = result["access_token"]
                    st.session_state.auth_flow = None
                    st.rerun()
                else:
                    st.warning("Spróbuj ponownie — autoryzacja nie została potwierdzona.")
            except Exception as e:
                st.error(f"Błąd autoryzacji: {e}")

# --- Ekran 2: Dashboard ---
else:
    with st.sidebar:
        st.title("QUARTER Analytics")
        st.divider()

        st.write("**Zakres dat:**")
        default_from = date.today() - timedelta(days=30)
        date_from = st.date_input("Data od", value=default_from)
        date_to = st.date_input("Data do", value=date.today())

        if st.button("Pobierz zamówienia", type="primary"):
            try:
                date_from_str = date_from.strftime("%Y-%m-%dT00:00:00Z")
                raw = get_orders(st.session_state.token, date_from=date_from_str)
                raw = [o for o in raw if o is not None]
                parsed = parse_orders(raw)
                df = pd.DataFrame(parsed) if parsed else pd.DataFrame()
                if not df.empty:
                    df = df[df["date"] >= str(date_from)]
                    df = df[df["date"] <= str(date_to)]
                st.session_state.price_map = None
                st.session_state.orders = df
            except Exception as e:
                st.error(f"Błąd: {e}")
                st.code(traceback.format_exc())
                if "401" in str(e):
                    st.session_state.token = None
                    st.rerun()

        if st.button("🔄 Pobierz ceny z SkyShop"):
            with st.spinner("Pobieranie produktów z SkyShop..."):
                try:
                    products = get_all_products(SKYSHOP_API_KEY, SKYSHOP_API_URL)
                    st.session_state.price_map = build_price_map(products)
                    st.success(f"✅ Pobrano {len(st.session_state.price_map)} produktów")
                except Exception as e:
                    st.error(f"Błąd SkyShop: {e}")

        st.divider()

        if st.button("Wyloguj"):
            if os.path.exists("tokens.json"):
                os.remove("tokens.json")
            st.session_state.token = None
            st.session_state.orders = None
            st.session_state.auth_flow = None
            st.session_state.price_map = None
            st.rerun()

    # --- Główny obszar ---
    if st.session_state.orders is None:
        st.info("Wybierz zakres dat i kliknij \"Pobierz zamówienia\".")
    else:
        df = st.session_state.orders
        if "external_id" not in df.columns:
            df["external_id"] = ""

        if df.empty:
            st.warning("Brak zamówień w wybranym zakresie dat.")
        else:
            if st.session_state.price_map:
                df = enrich_with_margins(df, st.session_state.price_map)

            # Metryki
            col1, col2, col3, col4 = st.columns(4)
            unique_orders = df["order_id"].nunique()
            total_revenue = df.drop_duplicates("order_id")["sale_price"].sum()
            avg_order = total_revenue / unique_orders if unique_orders > 0 else 0
            total_profit = df["profit"].sum() if "profit" in df.columns else None

            col1.metric("Zamówienia", unique_orders)
            col2.metric("Przychód", f"{total_revenue:,.2f} zł")
            col3.metric("Śr. zamówienie", f"{avg_order:,.2f} zł")
            col4.metric("Łączny zysk", f"{total_profit:,.2f} zł" if total_profit is not None else "–")

            # Tabela
            st.divider()

            price_map = st.session_state.price_map
            if price_map:
                df["purchase_price"] = df["external_id"].apply(
                    lambda eid: price_map.get(extract_skyshop_id(eid), {}).get("buy_price")
                    if extract_skyshop_id(eid) else None
                )

            columns_base = ["date", "offer_name", "quantity", "sale_price", "delivery_cost", "status"]
            columns_margin = ["purchase_price", "supplier", "profit", "margin_pct"]
            show_cols = columns_base + (columns_margin if price_map else [])
            display_df = df[[c for c in show_cols if c in df.columns]]
            sort_col = "bought_at" if "bought_at" in df.columns else "date"
            display_df = display_df.assign(bought_at=df["bought_at"]).sort_values(sort_col, ascending=False).drop(columns=["bought_at"], errors="ignore")

            rename_map = {
                "date": "Data",
                "offer_name": "Nazwa oferty",
                "quantity": "Ilość",
                "sale_price": "Cena sprzedaży (zł)",
                "delivery_cost": "Koszt dostawy (zł)",
                "status": "Status",
                "purchase_price": "Cena zakupu (zł)",
                "supplier": "Hurtownia",
                "profit": "Zysk (zł)",
                "margin_pct": "Marża %",
            }
            display_df = display_df.rename(columns=rename_map)

            col_config = {
                "Cena sprzedaży (zł)": st.column_config.NumberColumn(format="%.2f zł"),
                "Koszt dostawy (zł)": st.column_config.NumberColumn(format="%.2f zł"),
                "Cena zakupu (zł)": st.column_config.NumberColumn(format="%.2f zł"),
                "Zysk (zł)": st.column_config.NumberColumn(format="%.2f zł"),
                "Marża %": st.column_config.NumberColumn(format="%.1f%%"),
            }

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                height=600,
                column_config=col_config,
            )
