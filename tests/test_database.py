"""Testy jednostkowe warstwy SQLite."""
import pytest
import database


@pytest.fixture
def db(test_db):
    """Alias dla czytelności."""
    return test_db


@pytest.fixture
def order_row():
    return {
        "order_id": "order-abc",
        "date": "2026-03-01",
        "bought_at": "2026-03-01T10:00:00+01:00",
        "time_local": "10:00",
        "offer_name": "Klocki LEGO 42157",
        "external_id": "0$sky_99_x",
        "quantity": 1,
        "sale_price": 299.00,
        "delivery_cost": 19.00,
        "status": "Odebrane",
        "purchase_price": 180.00,
        "supplier": "Acme",
        "profit": 100.00,
        "margin_pct": 35.7,
        "sky_note": "",
    }


class TestInsertOrders:

    def test_insert_single(self, db, order_row):
        database.insert_orders([order_row])
        assert database.count_orders() == 1

    def test_deduplication(self, db, order_row):
        """INSERT OR IGNORE — drugi insert tego samego wiersza nie duplikuje."""
        database.insert_orders([order_row])
        database.insert_orders([order_row])
        assert database.count_orders() == 1

    def test_multiple_line_items_same_order(self, db, order_row):
        """Różne produkty z tego samego order_id → osobne wiersze."""
        row2 = {**order_row, "offer_name": "Inny produkt", "external_id": "0$sky_88_y"}
        database.insert_orders([order_row, row2])
        assert database.count_orders() == 2

    def test_empty_list_does_nothing(self, db):
        database.insert_orders([])
        assert database.count_orders() == 0


class TestGetOrders:

    def test_date_filter_inclusive(self, db, order_row):
        database.insert_orders([order_row])
        # Dokładna data — powinna być zwrócona
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert len(rows) == 1

    def test_date_filter_excludes_outside(self, db, order_row):
        database.insert_orders([order_row])
        rows = database.get_orders("2026-03-02", "2026-03-31")
        assert len(rows) == 0

    def test_returns_sky_note(self, db, order_row):
        """sky_note jest ustawiane przez update_sky_notes, nie przez insert."""
        database.insert_orders([order_row])
        database.update_sky_notes([{"order_id": order_row["order_id"], "sky_note": "Pilne!"}])
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert rows[0]["sky_note"] == "Pilne!"

    def test_sorted_by_bought_at_desc(self, db, order_row):
        older = {**order_row, "bought_at": "2026-03-01T08:00:00+01:00", "offer_name": "Stary"}
        newer = {**order_row, "bought_at": "2026-03-01T12:00:00+01:00",
                 "offer_name": "Nowy", "external_id": "0$sky_11_z"}
        database.insert_orders([older, newer])
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert rows[0]["offer_name"] == "Nowy"


class TestUpdatePurchasePrice:

    def test_updates_correctly(self, db, order_row):
        database.insert_orders([order_row])
        row_id = database.get_orders("2026-03-01", "2026-03-01")[0]["id"]
        database.update_purchase_price(row_id, 150.0, 130.0, 46.4)
        updated = database.get_orders("2026-03-01", "2026-03-01")[0]
        assert updated["purchase_price"] == 150.0
        assert updated["profit"] == 130.0
        assert updated["margin_pct"] == pytest.approx(46.4, abs=0.01)


class TestGetLastBoughtAt:

    def test_empty_returns_none(self, db):
        assert database.get_last_bought_at() is None

    def test_returns_max(self, db, order_row):
        database.insert_orders([order_row])
        result = database.get_last_bought_at()
        assert result == "2026-03-01T10:00:00+01:00"


class TestUpdateOrderStatuses:

    def test_updates_status(self, db, order_row):
        database.insert_orders([order_row])
        database.update_order_statuses([{"order_id": "order-abc", "status": "Anulowane"}])
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert rows[0]["status"] == "Anulowane"

    def test_cancelled_clears_financial_fields(self, db, order_row):
        """Przejście na 'Anulowane' czyści pola finansowe."""
        database.insert_orders([order_row])
        database.update_order_statuses([{"order_id": "order-abc", "status": "Anulowane"}])
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert rows[0]["status"] == "Anulowane"
        assert rows[0]["purchase_price"] is None
        assert rows[0]["profit"] is None
        assert rows[0]["margin_pct"] is None

    def test_non_cancelled_keeps_financial_fields(self, db, order_row):
        """Przejście na inny status nie kasuje pól finansowych."""
        database.insert_orders([order_row])
        database.update_order_statuses([{"order_id": "order-abc", "status": "Wysłane"}])
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert rows[0]["status"] == "Wysłane"
        assert rows[0]["purchase_price"] == order_row["purchase_price"]
        assert rows[0]["profit"] == order_row["profit"]

    def test_unknown_order_id_ignored(self, db):
        """Aktualizacja nieistniejącego order_id nie wyrzuca błędu."""
        database.update_order_statuses([{"order_id": "nie-istnieje", "status": "X"}])


class TestUpdateSkyNotes:

    def test_updates_note(self, db, order_row):
        database.insert_orders([order_row])
        database.update_sky_notes([{"order_id": "order-abc", "sky_note": "Priorytet!"}])
        rows = database.get_orders("2026-03-01", "2026-03-01")
        assert rows[0]["sky_note"] == "Priorytet!"


class TestPriceMap:

    def test_upsert_and_load(self, db):
        price_map = {
            "111": {"buy_price": 50.0, "supplier": "Tayma"},
            "222": {"buy_price": 80.0, "supplier": "Acme"},
        }
        database.upsert_price_map(price_map)
        loaded = database.load_price_map()
        assert loaded["111"]["buy_price"] == 50.0
        assert loaded["111"]["supplier"] == "Tayma"
        assert loaded["222"]["buy_price"] == 80.0

    def test_upsert_replaces(self, db):
        database.upsert_price_map({"999": {"buy_price": 10.0, "supplier": "A"}})
        database.upsert_price_map({"999": {"buy_price": 20.0, "supplier": "B"}})
        loaded = database.load_price_map()
        assert loaded["999"]["buy_price"] == 20.0
        assert loaded["999"]["supplier"] == "B"


class TestMigration:

    def test_migration_idempotent(self, test_db):
        """_migrate() wywołane dwa razy nie crashuje."""
        import sqlite3
        conn = sqlite3.connect(test_db)
        database._migrate(conn)
        database._migrate(conn)  # drugie wywołanie nie powinno rzucić błędu
        conn.close()
