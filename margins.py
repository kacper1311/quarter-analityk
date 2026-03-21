import math

PROWIZJA_ALLEGRO = 0.12
OPLATA_PLATNICZA = 0.015
KOSZTY_STALE = 1245  # ZUS 650 + księgowość 320 + SkyShop 275
CEL_NETTO = 4000


def calculate_margin(sale_price, purchase_price_brutto, delivery_cost=0) -> dict:
    product_price = round(sale_price - delivery_cost, 2)
    profit = round(product_price - purchase_price_brutto, 2)
    margin_pct = round((profit / product_price) * 100, 1) if product_price > 0 else 0.0
    return {
        "profit": profit,
        "margin_pct": margin_pct,
    }


def calculate_summary(margins: list) -> dict:
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
