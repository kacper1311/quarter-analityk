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
    """Aktualizuje tylko status dla istniejących zamówień (identyfikacja po order_id)."""
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            "UPDATE orders SET status = :status WHERE order_id = :order_id",
            rows,
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
