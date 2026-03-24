import sqlite3
from contextlib import contextmanager

DB_PATH = "quarter.db"


def _migrate(conn):
    existing = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "time_local" not in existing:
        conn.execute("ALTER TABLE orders ADD COLUMN time_local TEXT DEFAULT ''")
    if "sky_note" not in existing:
        conn.execute("ALTER TABLE orders ADD COLUMN sky_note TEXT DEFAULT ''")


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
    """Aktualizuje status; dla 'Anulowane' czyści też pola finansowe."""
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            "UPDATE orders SET status = :status WHERE order_id = :order_id",
            rows,
        )
        cancelled = [r for r in rows if r["status"] == "Anulowane"]
        if cancelled:
            conn.executemany(
                "UPDATE orders SET purchase_price=NULL, supplier=NULL, profit=NULL, margin_pct=NULL "
                "WHERE order_id=:order_id AND status='Anulowane'",
                cancelled,
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
            if row.get("status") == "Anulowane":
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
