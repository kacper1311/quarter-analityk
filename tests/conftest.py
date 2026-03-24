import pytest
import database
import main
from fastapi.testclient import TestClient


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Tymczasowa baza danych SQLite dla każdego testu."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    database.init_db()
    return db_file


@pytest.fixture
def sample_order():
    return {
        "order_id": "test-order-001",
        "date": "2026-03-01",
        "bought_at": "2026-03-01T10:00:00+01:00",
        "time_local": "10:00",
        "offer_name": "Testowy produkt XYZ",
        "external_id": "0$sky_12345_abc",
        "quantity": 1,
        "sale_price": 199.99,
        "delivery_cost": 19.99,
        "status": "Odebrane",
        "purchase_price": 120.00,
        "supplier": "TestSupplier",
        "profit": 60.00,
        "margin_pct": 33.3,
        "sky_note": "",
    }


@pytest.fixture
def client(test_db, monkeypatch):
    """TestClient FastAPI z izolowaną bazą danych."""
    # Bypass auth — zwróć fake token
    monkeypatch.setattr(main, "_current_token", lambda: "fake-test-token")
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
