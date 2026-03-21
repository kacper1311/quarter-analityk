import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from allegro import get_orders, parse_orders
from auth import authorize, get_token, poll_for_token

st.set_page_config(page_title="QUARTER Analytics", layout="wide")

# --- Session state ---
if "token" not in st.session_state:
    st.session_state.token = get_token()
if "orders" not in st.session_state:
    st.session_state.orders = None
if "auth_flow" not in st.session_state:
    st.session_state.auth_flow = None


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
                st.session_state.orders = df
            except Exception as e:
                import traceback
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
            st.session_state.orders = None
            st.session_state.auth_flow = None
            st.rerun()

    # --- Główny obszar ---
    if st.session_state.orders is None:
        st.info("Wybierz zakres dat i kliknij \"Pobierz zamówienia\".")
    else:
        df = st.session_state.orders

        if df.empty:
            st.warning("Brak zamówień w wybranym zakresie dat.")
        else:
            col1, col2, col3 = st.columns(3)
            unique_orders = df["order_id"].nunique()
            total_revenue = df.drop_duplicates("order_id")["sale_price"].sum()
            avg_order = total_revenue / unique_orders if unique_orders > 0 else 0

            col1.metric("Liczba zamówień", unique_orders)
            col2.metric("Łączny przychód", f"{total_revenue:,.2f} zł")
            col3.metric("Średnia wartość zamówienia", f"{avg_order:,.2f} zł")

            display_df = df.sort_values("date", ascending=False).rename(columns={
                "date": "Data",
                "offer_name": "Nazwa oferty",
                "quantity": "Ilość",
                "sale_price": "Cena sprzedaży (zł)",
                "delivery_cost": "Koszt dostawy (zł)",
                "status": "Status",
            })[["Data", "Nazwa oferty", "Ilość", "Cena sprzedaży (zł)", "Koszt dostawy (zł)", "Status"]]

            st.dataframe(display_df, use_container_width=True)
