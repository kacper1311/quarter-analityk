import os
import traceback
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import database
from allegro import get_orders, parse_orders
from auth import authorize, get_token, poll_for_token
from margins import calculate_margin
from skyshop import build_price_map, extract_skyshop_id, get_all_products

SKYSHOP_API_KEY = os.getenv("SKYSHOP_API_KEY")
SKYSHOP_API_URL = os.getenv("SKYSHOP_API_URL")

st.set_page_config(page_title="QUARTER Analytics", layout="wide")

database.init_db()

# --- Session state ---
if "token" not in st.session_state:
    st.session_state.token = get_token()
if "auth_flow" not in st.session_state:
    st.session_state.auth_flow = None
if "last_update" not in st.session_state:
    st.session_state.last_update = None
if "filter_from" not in st.session_state:
    st.session_state.filter_from = date.today() - timedelta(days=30)
if "filter_to" not in st.session_state:
    st.session_state.filter_to = date.today()


@st.dialog("Szczegóły zamówienia")
def show_order_details(row):
    st.write(f"**Produkt:** {row['offer_name']}")
    st.write(f"**Data:** {row['bought_at'][:19] if row.get('bought_at') else row['date']}")
    st.write(f"**Status:** {row['status']}")
    st.divider()
    col1, col2 = st.columns(2)
    col1.metric("Cena sprzedaży", f"{row['sale_price']:.2f} zł")
    col1.metric("Cena zakupu", f"{row['purchase_price']:.2f} zł" if row.get('purchase_price') else "–")
    col1.metric("Koszt dostawy", f"{row['delivery_cost']:.2f} zł")
    col2.metric("Zysk", f"{row['profit']:.2f} zł" if row.get('profit') is not None else "–")
    col2.metric("Marża", f"{row['margin_pct']:.1f}%" if row.get('margin_pct') is not None else "–")
    col2.metric("Hurtownia", row.get('supplier') or "–")


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
        with st.form("filter_form"):
            date_from = st.date_input("Data od", value=st.session_state.filter_from)
            date_to = st.date_input("Data do", value=st.session_state.filter_to)
            if st.form_submit_button("Filtruj"):
                st.session_state.filter_from = date_from
                st.session_state.filter_to = date_to

        if st.session_state.last_update:
            st.caption(f"Ostatnia aktualizacja: {st.session_state.last_update}")

        st.divider()

        if st.button("🔄 Aktualizuj dane", type="primary"):
            with st.spinner("Aktualizacja..."):
                try:
                    products = get_all_products(SKYSHOP_API_KEY, SKYSHOP_API_URL)
                    price_map = build_price_map(products)
                    database.upsert_price_map(price_map)

                    last_ba = database.get_last_bought_at()
                    raw = get_orders(st.session_state.token, date_from=last_ba)
                    raw = [o for o in raw if o is not None]
                    parsed = parse_orders(raw)

                    if parsed:
                        df_new = enrich_with_margins(pd.DataFrame(parsed), price_map)
                        database.insert_orders(df_new.to_dict("records"))

                    st.session_state.last_update = datetime.now().strftime("%Y-%m-%d %H:%M")
                    st.success(f"✅ Łącznie w bazie: {database.count_orders()} wierszy")
                    st.rerun()
                except Exception as e:
                    st.error(f"Błąd: {e}")
                    st.code(traceback.format_exc())
                    if "401" in str(e):
                        st.session_state.token = None
                        st.rerun()

        st.divider()

        if st.button("Wyloguj"):
            if os.path.exists("tokens.json"):
                os.remove("tokens.json")
            st.session_state.token = None
            st.session_state.auth_flow = None
            st.session_state.last_update = None
            st.rerun()

    # --- Główny obszar ---
    df_raw = database.get_orders(str(st.session_state.filter_from), str(st.session_state.filter_to))

    if not df_raw:
        st.info("Brak zamówień w wybranym zakresie. Kliknij \"Aktualizuj dane\" aby pobrać.")
    else:
        df = pd.DataFrame(df_raw)

        unique_orders = df["order_id"].nunique()
        total_revenue = df.drop_duplicates("order_id")["sale_price"].sum()
        avg_order = total_revenue / unique_orders if unique_orders > 0 else 0
        total_profit = df["profit"].sum() if "profit" in df.columns else None
        profit_str_hdr = f"{total_profit:,.2f} zł" if total_profit is not None else "–"

        display_df = df.reset_index(drop=True)

        st.markdown(f"""
<div style="position:fixed; top:3.5rem; left:21rem; right:1rem; z-index:1000;
            background:#0e1117; border-bottom:2px solid #444; padding:0;">
  <div style="display:flex; gap:48px; padding:12px 24px 10px 24px; border-bottom:1px solid #333;">
    <div><div style="font-size:13px;color:#888">Zamówienia</div>
         <div style="font-size:24px;font-weight:700;color:#fafafa">{unique_orders}</div></div>
    <div><div style="font-size:13px;color:#888">Przychód</div>
         <div style="font-size:24px;font-weight:700;color:#fafafa">{total_revenue:,.2f} zł</div></div>
    <div><div style="font-size:13px;color:#888">Śr. zamówienie</div>
         <div style="font-size:24px;font-weight:700;color:#fafafa">{avg_order:,.2f} zł</div></div>
    <div><div style="font-size:13px;color:#888">Łączny zysk</div>
         <div style="font-size:24px;font-weight:700;color:#fafafa">{profit_str_hdr}</div></div>
  </div>
  <div style="display:grid; grid-template-columns:1fr 4fr 1fr 2fr 2fr 2fr 2fr 2fr 1fr;
              padding:6px 16px; font-size:13px; font-weight:600; color:#aaa;">
    <div>Data</div><div>Nazwa oferty</div><div>Ilość</div>
    <div>Sprzedaż</div><div>Zakup</div><div>Zysk</div>
    <div>Marża</div><div>Status</div><div></div>
  </div>
</div>
<div style="height:140px"></div>
""", unsafe_allow_html=True)

        for idx, row in display_df.iterrows():
            profit_val = row.get("profit")
            margin_val = row.get("margin_pct")
            profit_str = f"{profit_val:.2f} zł" if profit_val is not None and profit_val == profit_val else "–"
            margin_str = f"{margin_val:.1f}%" if margin_val is not None and margin_val == margin_val else "–"

            cols = st.columns([1, 4, 1, 2, 2, 2, 2, 2, 1])
            cols[0].write(row["date"])
            cols[1].write(row["offer_name"])
            cols[2].write(str(row["quantity"]))
            cols[3].write(f"{row['sale_price']:.2f} zł")
            cols[4].write(f"{row['purchase_price']:.2f} zł" if row.get("purchase_price") else "–")
            cols[5].write(profit_str)
            cols[6].write(margin_str)
            cols[7].write(row["status"])
            if cols[8].button("👁", key=f"btn_{idx}"):
                show_order_details(row)
            st.divider()
