import sqlite3
from contextlib import contextmanager

DB_PATH = "quarter.db"


def _migrate(conn):
    existing = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "time_local" not in existing:
        conn.execute("ALTER TABLE orders ADD COLUMN time_local TEXT DEFAULT ''")
    if "sky_note" not in existing:
        conn.execute("ALTER TABLE orders ADD COLUMN sky_note TEXT DEFAULT ''")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "recurring_cost_templates" not in tables:
        conn.execute("""
            CREATE TABLE recurring_cost_templates (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                name   TEXT UNIQUE NOT NULL,
                type   TEXT NOT NULL DEFAULT 'fixed',
                amount REAL,
                rate   REAL
            )
        """)
        conn.executemany(
            "INSERT OR IGNORE INTO recurring_cost_templates (name, type, amount) VALUES (?,?,?)",
            [("ZUS", "fixed", 650.0), ("Księgowość", "fixed", 320.0), ("SkyShop", "fixed", 275.0)],
        )
        conn.execute(
            "INSERT OR IGNORE INTO recurring_cost_templates (name, type, rate) VALUES (?,?,?)",
            ("Ryczałt 3%", "pct_revenue", 3.0),
        )


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                date TEXT,
                bought_at TEXT,
                time_local TEXT DEFAULT '',
                offer_name TEXT,
                external_id TEXT,
                quantity INTEGER,
                sale_price REAL,
                delivery_cost REAL,
                status TEXT,
                purchase_price REAL,
                supplier TEXT,
                profit REAL,
                margin_pct REAL,
                UNIQUE(order_id, bought_at, offer_name, external_id)
            )
        """)
        _migrate(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_map (
                prod_id TEXT PRIMARY KEY,
                buy_price REAL,
                supplier TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS billing_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT UNIQUE,
                type_id TEXT,
                type_name TEXT,
                amount REAL,
                tax REAL,
                date TEXT,
                year_month TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year_month TEXT,
                name TEXT,
                amount REAL,
                is_default INTEGER DEFAULT 0,
                UNIQUE(year_month, name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recurring_cost_templates (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                name   TEXT UNIQUE NOT NULL,
                type   TEXT NOT NULL DEFAULT 'fixed',
                amount REAL,
                rate   REAL
            )
        """)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_last_bought_at():
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(bought_at) as max_ba FROM orders WHERE bought_at != ''"
        ).fetchone()
        return row["max_ba"] if row and row["max_ba"] else None


def get_min_date() -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT MIN(date) as min_d FROM orders WHERE date != ''"
        ).fetchone()
        return row["min_d"] if row and row["min_d"] else None


def insert_orders(rows: list):
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO orders
                (order_id, date, bought_at, time_local, offer_name, external_id, quantity,
                 sale_price, delivery_cost, status, purchase_price, supplier, profit, margin_pct)
            VALUES
                (:order_id, :date, :bought_at, :time_local, :offer_name, :external_id, :quantity,
                 :sale_price, :delivery_cost, :status, :purchase_price, :supplier, :profit, :margin_pct)
            """,
            rows,
        )


def update_purchase_price(row_id: int, purchase_price: float, profit: float, margin_pct: float):
    with _conn() as conn:
        conn.execute(
            "UPDATE orders SET purchase_price=?, profit=?, margin_pct=? WHERE id=?",
            (purchase_price, profit, margin_pct, row_id),
        )


def update_sky_notes(rows: list):
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            "UPDATE orders SET sky_note = :sky_note WHERE order_id = :order_id",
            rows,
        )


def update_order_statuses(rows: list):
    """Aktualizuje status; dla 'Anulowane' czyści wszystkie pola finansowe;
    dla 'Zwrot towaru' czyści tylko profit/margin (zachowuje purchase_price/supplier)."""
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            "UPDATE orders SET status = :status WHERE order_id = :order_id",
            rows,
        )
        to_clear_all = [r for r in rows if r["status"] == "Anulowane"]
        if to_clear_all:
            conn.executemany(
                "UPDATE orders SET purchase_price=NULL, supplier=NULL, profit=NULL, margin_pct=NULL "
                "WHERE order_id=:order_id AND status = 'Anulowane'",
                to_clear_all,
            )
        to_clear_returns = [r for r in rows if r["status"] == "Zwrot towaru"]
        if to_clear_returns:
            conn.executemany(
                "UPDATE orders SET profit=NULL, margin_pct=NULL "
                "WHERE order_id=:order_id AND status = 'Zwrot towaru'",
                to_clear_returns,
            )


def get_orders(date_from: str, date_to: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT *, COALESCE(sky_note, '') as sky_note FROM orders WHERE date >= ? AND date <= ? ORDER BY bought_at DESC",
            (date_from, date_to),
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_price_map(price_map: dict):
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO price_map (prod_id, buy_price, supplier) VALUES (?, ?, ?)",
            [(k, v["buy_price"], v["supplier"]) for k, v in price_map.items()],
        )


def load_price_map() -> dict:
    with _conn() as conn:
        rows = conn.execute("SELECT prod_id, buy_price, supplier FROM price_map").fetchall()
        return {r["prod_id"]: {"buy_price": r["buy_price"], "supplier": r["supplier"]} for r in rows}


def count_orders() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def insert_billing_entries(entries: list):
    if not entries:
        return
    with _conn() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO billing_entries
               (entry_id, type_id, type_name, amount, tax, date, year_month)
               VALUES (:entry_id, :type_id, :type_name, :amount, :tax, :date, :year_month)""",
            entries,
        )


def get_billing_summary(year_month: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT type_id, type_name, ROUND(SUM(amount), 2) as total
               FROM billing_entries
               WHERE year_month = ? AND type_id != 'PAD'
               GROUP BY type_id, type_name
               ORDER BY total ASC""",
            (year_month,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_available_year_months() -> list:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT ym FROM (
                   SELECT DISTINCT substr(date, 1, 7) as ym FROM orders WHERE length(date) >= 7
                   UNION
                   SELECT DISTINCT year_month FROM billing_entries WHERE year_month != ''
               ) ORDER BY ym DESC"""
        ).fetchall()
        return [r["ym"] for r in rows if r["ym"]]


def get_monthly_costs(year_month: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM monthly_costs WHERE year_month = ? ORDER BY is_default DESC, id",
            (year_month,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_monthly_cost(cost_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM monthly_costs WHERE id = ?", (cost_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_monthly_cost(year_month: str, name: str, amount: float, is_default: int = 0):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO monthly_costs (year_month, name, amount, is_default)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(year_month, name) DO UPDATE SET amount=excluded.amount""",
            (year_month, name, amount, is_default),
        )


def update_monthly_cost(cost_id: int, amount: float):
    with _conn() as conn:
        conn.execute(
            "UPDATE monthly_costs SET amount = ? WHERE id = ?",
            (amount, cost_id),
        )


def delete_monthly_cost(cost_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM monthly_costs WHERE id = ?", (cost_id,))


def get_orders_summary_for_month(year_month: str) -> dict:
    with _conn() as conn:
        rev_row = conn.execute(
            """SELECT COALESCE(SUM(sale_price), 0) as revenue FROM (
                   SELECT order_id, MAX(sale_price) as sale_price
                   FROM orders
                   WHERE substr(date, 1, 7) = ?
                   AND status NOT IN ('Anulowane', 'Zwrot towaru', 'Nowe')
                   GROUP BY order_id
               )""",
            (year_month,),
        ).fetchone()
        purchase_row = conn.execute(
            """SELECT COALESCE(SUM(purchase_price), 0) as purchase_cost
               FROM orders
               WHERE substr(date, 1, 7) = ?
               AND status NOT IN ('Anulowane', 'Nowe')""",
            (year_month,),
        ).fetchone()
    return {
        "revenue": rev_row["revenue"] if rev_row else 0.0,
        "purchase_cost": purchase_row["purchase_cost"] if purchase_row else 0.0,
    }


def get_returned_orders_for_month(year_month: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, offer_name, purchase_price, date
               FROM orders
               WHERE substr(date, 1, 7) = ?
               AND status = 'Zwrot towaru'
               ORDER BY date DESC""",
            (year_month,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Recurring cost templates
# ---------------------------------------------------------------------------

def get_recurring_templates() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM recurring_cost_templates ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_recurring_template(template_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM recurring_cost_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return dict(row) if row else None


def insert_recurring_template(name: str, type_: str, amount: float | None, rate: float | None):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO recurring_cost_templates (name, type, amount, rate) VALUES (?, ?, ?, ?)",
            (name, type_, amount, rate),
        )


def update_recurring_template(template_id: int, amount: float | None, rate: float | None):
    with _conn() as conn:
        conn.execute(
            "UPDATE recurring_cost_templates SET amount=?, rate=? WHERE id=?",
            (amount, rate, template_id),
        )


def delete_recurring_template(template_id: int):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM recurring_cost_templates WHERE id=?", (template_id,)
        )


def apply_templates_to_month(year_month: str, revenue: float):
    templates = get_recurring_templates()
    if not templates:
        return
    with _conn() as conn:
        for t in templates:
            if t["type"] == "fixed":
                amount = t["amount"] or 0.0
            else:  # pct_revenue
                amount = round(revenue * (t["rate"] or 0.0) / 100, 2)
            conn.execute(
                """INSERT INTO monthly_costs (year_month, name, amount, is_default)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(year_month, name) DO UPDATE SET amount=excluded.amount, is_default=1""",
                (year_month, t["name"], amount),
            )


def propagate_templates_forward(from_year_month: str):
    templates = get_recurring_templates()
    if not templates:
        return
    template_names = [t["name"] for t in templates]
    placeholders = ",".join("?" * len(template_names))
    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT year_month FROM monthly_costs
                WHERE year_month >= ? AND name IN ({placeholders})
                ORDER BY year_month""",
            [from_year_month] + template_names,
        ).fetchall()
        months = [r["year_month"] for r in rows]
    for ym in months:
        summary = get_orders_summary_for_month(ym)
        apply_templates_to_month(ym, summary["revenue"])


def get_metrics_comparison(date_from: str, date_to: str) -> dict:
    """
    Zwraca metryki dla bieżącego okresu i poprzedniego okresu tej samej długości.
    """
    from datetime import datetime, timedelta
    d_from = datetime.strptime(date_from, "%Y-%m-%d")
    d_to   = datetime.strptime(date_to,   "%Y-%m-%d")
    delta  = d_to - d_from
    prev_from = str((d_from - delta - timedelta(days=1)).date())
    prev_to   = str((d_from - timedelta(days=1)).date())

    def _metrics(orders):
        seen = {}
        total_profit = 0.0
        has_profit = False
        for row in orders:
            if row.get("status") in {"Anulowane", "Zwrot towaru", "Nowe"}:
                continue
            oid = row["order_id"]
            if oid not in seen:
                seen[oid] = row["sale_price"]
            if row.get("profit") is not None:
                total_profit += row["profit"]
                has_profit = True
        n = len(seen)
        rev = sum(seen.values())
        return {
            "orders":    n,
            "revenue":   rev,
            "avg_order": rev / n if n > 0 else 0.0,
            "profit":    total_profit if has_profit else None,
        }

    curr = _metrics(get_orders(date_from, date_to))
    prev = _metrics(get_orders(prev_from, prev_to))

    def _pct(cur, prv):
        if prv and prv != 0:
            return round((cur - prv) / abs(prv) * 100, 1)
        return None

    return {
        "current": curr,
        "prev":    prev,
        "trends": {
            "orders":    _pct(curr["orders"],          prev["orders"]),
            "revenue":   _pct(curr["revenue"],         prev["revenue"]),
            "avg_order": _pct(curr["avg_order"],       prev["avg_order"]),
            "profit":    _pct(curr["profit"] or 0,     prev["profit"] or 0),
        },
    }
