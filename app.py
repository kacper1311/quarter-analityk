import html as _html
import os
import traceback
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import database
from allegro import get_orders, parse_orders
from auth import authorize, get_token, poll_for_token
from margins import calculate_margin
from skyshop import build_price_map, extract_skyshop_id, get_all_products, get_skyshop_order_notes

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
    # --- Główny obszar ---
    df_raw = database.get_orders(str(st.session_state.filter_from), str(st.session_state.filter_to))

    with st.sidebar:
        _edit_id = st.query_params.get("edit")
        if _edit_id:
            _edit_row = next((r for r in df_raw if str(r["id"]) == str(_edit_id)), None)
            if _edit_row:
                st.subheader("Edycja ceny zakupu")
                st.caption(str(_edit_row["offer_name"])[:50])
                _new_price = st.number_input(
                    "Cena zakupu (zł)",
                    value=float(_edit_row["purchase_price"] or 0.0),
                    min_value=0.0, step=0.01, format="%.2f",
                )
                _c1, _c2 = st.columns(2)
                if _c1.button("Zapisz", type="primary", use_container_width=True):
                    _m = calculate_margin(
                        sale_price=_edit_row["sale_price"],
                        purchase_price_brutto=_new_price,
                        delivery_cost=_edit_row["delivery_cost"],
                    )
                    database.update_purchase_price(
                        row_id=int(_edit_id),
                        purchase_price=_new_price,
                        profit=_m["profit"],
                        margin_pct=_m["margin_pct"],
                    )
                    st.query_params.clear()
                    st.rerun()
                if _c2.button("Anuluj", use_container_width=True):
                    st.query_params.clear()
                    st.rerun()
                st.divider()

        st.title("QUARTER Analytics")
        st.divider()

        st.write("**Zakres dat:**")
        with st.form("filter_form"):
            date_from = st.date_input("Data od", value=st.session_state.filter_from)
            date_to = st.date_input("Data do", value=st.session_state.filter_to)
            if st.form_submit_button("Filtruj"):
                st.session_state.filter_from = date_from
                st.session_state.filter_to = date_to
                st.rerun()

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

                    # Faza 2: odśwież statusy zamówień z ostatnich 60 dni
                    status_since = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    raw_recent = get_orders(st.session_state.token, date_from=status_since)
                    raw_recent = [o for o in raw_recent if o is not None]
                    parsed_recent = parse_orders(raw_recent)
                    status_map = {r["order_id"]: r["status"] for r in parsed_recent}
                    database.update_order_statuses(
                        [{"order_id": oid, "status": s} for oid, s in status_map.items()]
                    )

                    # Faza 3: pobierz notatki admin ze wszystkich zamówień SkyShop
                    sky_notes = get_skyshop_order_notes(SKYSHOP_API_KEY, SKYSHOP_API_URL)
                    if sky_notes:
                        database.update_sky_notes(
                            [{"order_id": oid, "sky_note": note} for oid, note in sky_notes.items()]
                        )

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
  <div style="display:grid; grid-template-columns:1fr 4fr 1fr 2fr 2fr 2fr 2fr 2fr 2fr;
              column-gap:1rem; padding:10px 16px; font-size:13px; font-weight:600; color:#aaa;">
    <div>Data</div><div>Nazwa oferty</div><div>Ilość</div>
    <div>Sprzedaż</div><div>Cena prod.</div><div>Zakup</div><div>Zysk</div>
    <div>Marża</div><div>Status</div>
  </div>
</div>
<div style="height:140px"></div>
""", unsafe_allow_html=True)

        STATUS_COLORS = {
            "Odebrane":     ("#1b5e20", "#e8f5e9"),
            "Anulowane":    ("#b71c1c", "#ffebee"),
            "W realizacji": ("#e65100", "#fff3e0"),
        }

        GRID = "grid-template-columns:1fr 4fr 1fr 2fr 2fr 2fr 2fr 2fr 2fr;column-gap:1rem"

        table_css = """
<style>
.order-row > summary::-webkit-details-marker { display: none; }
.order-row > summary::marker { display: none; }
.order-row > summary { cursor: pointer; }
.order-row > summary:hover { filter: brightness(1.2); }
.order-row[open] > summary { background: rgba(255,255,255,0.06) !important; }
[id^="chk-"] { position: absolute; opacity: 0; pointer-events: none; }
[id^="efc-"] { max-height: 0; overflow: hidden; }
[id^="chk-"]:checked ~ [id^="efc-"] { max-height: 300px !important; overflow: visible; }
</style>
"""

        rows_html = []
        for idx, row in display_df.iterrows():
            bg = "rgba(255,255,255,0.03)" if idx % 2 == 0 else "transparent"

            date_cell = f'<span style="white-space:nowrap;font-size:13px">{row["date"]}</span>'
            if row.get("time_local"):
                date_cell += f'<br><small style="color:#888;font-size:11px">{row["time_local"]}</small>'

            profit_val = row.get("profit")
            if profit_val is not None and profit_val == profit_val:
                p_color = "#00c853" if profit_val >= 0 else "#e53935"
                profit_cell = f'<span style="color:{p_color};font-weight:600">{profit_val:+.2f} zł</span>'
            else:
                profit_cell = '<span style="color:#555">–</span>'

            margin_val = row.get("margin_pct")
            if margin_val is not None and margin_val == margin_val:
                m_color = "#00c853" if margin_val >= 0 else "#e53935"
                margin_cell = f'<span style="color:{m_color};font-weight:600">{margin_val:.1f}%</span>'
            else:
                margin_cell = '<span style="color:#555">–</span>'

            s_tc, s_bg = STATUS_COLORS.get(row["status"], ("#37474f", "#eceff1"))
            status_cell = (
                f'<span style="background:{s_bg};color:{s_tc};padding:2px 8px;'
                f'border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap">'
                f'{_html.escape(row["status"])}</span>'
            )

            product_price = row["sale_price"] - row["delivery_cost"]
            purchase_cell = (
                f'{row["purchase_price"]:.2f} zł'
                if row.get("purchase_price") else '<span style="color:#555">–</span>'
            )

            sky_note = str(row.get("sky_note") or "").strip()
            detail_parts = [
                f'<b>Hurtownia:</b> {_html.escape(str(row.get("supplier") or "–"))}',
                f'<b>Koszt dostawy:</b> {row["delivery_cost"]:.2f} zł',
                f'<b>Order ID:</b> {_html.escape(str(row.get("order_id", "–")))}',
            ]
            if sky_note:
                detail_parts.append(f'<b>Notatka admin:</b> {_html.escape(sky_note)}')
            detail_parts.append(
                f'<label for="chk-{row["id"]}" '
                f'style="color:#58a6ff;cursor:pointer">✏️ Edytuj cenę zakupu</label>'
            )
            detail_html = ' &nbsp;·&nbsp; '.join(detail_parts)

            purchase_val = f'{row["purchase_price"]:.2f}' if row.get("purchase_price") else "0.00"
            edit_form_html = f'''<input type="checkbox" id="chk-{row['id']}">
<div id="efc-{row['id']}" style="padding-top:8px">
  <span style="color:#aaa;font-size:12px">Aktualna cena: {purchase_val} zł &nbsp;</span>
  <a href="?edit={row['id']}" style="color:#58a6ff;font-size:12px">→ Otwórz edycję w panelu bocznym</a>
</div>'''

            rows_html.append(f'''
<details class="order-row" style="background:{bg};border-bottom:1px solid #1e2530;">
  <summary style="display:grid;{GRID};padding:10px 16px;align-items:center;list-style:none;">
    <div>{date_cell}</div>
    <div style="font-size:13px">{_html.escape(str(row["offer_name"]))}</div>
    <div style="font-size:13px">{row["quantity"]}</div>
    <div style="font-size:13px">{row["sale_price"]:.2f} zł</div>
    <div style="font-size:13px">{product_price:.2f} zł</div>
    <div style="font-size:13px">{purchase_cell}</div>
    <div>{profit_cell}</div>
    <div>{margin_cell}</div>
    <div>{status_cell}</div>
  </summary>
  <div style="padding:10px 24px 12px 24px;background:#161b22;border-top:1px solid #1e2530;
              font-size:13px;color:#aaa;">
    {detail_html}
    {edit_form_html}
  </div>
</details>''')

        st.markdown(table_css + '\n'.join(rows_html), unsafe_allow_html=True)
