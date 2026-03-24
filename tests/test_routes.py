"""Testy integracyjne routów FastAPI."""
import pytest
import database


class TestAuth:

    def test_login_page_loads(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "QUARTER" in resp.text

    def test_index_with_token_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_without_token_redirects_to_login(self, test_db, monkeypatch):
        import main
        from fastapi.testclient import TestClient

        monkeypatch.setattr(main, "_current_token", lambda: None)
        with TestClient(main.app, raise_server_exceptions=False) as c:
            resp = c.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/login" in resp.headers.get("location", "")


class TestOrderEditForm:

    def test_edit_form_returns_html_with_input(self, client, sample_order):
        database.insert_orders([sample_order])
        row_id = database.get_orders("2026-03-01", "2026-03-01")[0]["id"]
        resp = client.get(
            f"/orders/{row_id}/edit",
            params={"date_from": "2026-03-01", "date_to": "2026-03-01"},
        )
        assert resp.status_code == 200
        assert "<input" in resp.text
        assert 'type="number"' in resp.text

    def test_edit_form_unknown_row_returns_error(self, client):
        resp = client.get(
            "/orders/99999/edit",
            params={"date_from": "2026-03-01", "date_to": "2026-03-01"},
        )
        assert resp.status_code == 200
        assert "99999" in resp.text  # error message contains the id


class TestOrderSavePrice:

    def test_save_price_updates_db(self, client, sample_order):
        database.insert_orders([sample_order])
        row_id = database.get_orders("2026-03-01", "2026-03-01")[0]["id"]

        resp = client.post(
            f"/orders/{row_id}/price",
            data={
                "price": "90.00",
                "date_from": "2026-03-01",
                "date_to": "2026-03-01",
            },
        )
        assert resp.status_code == 200

        updated = database.get_orders("2026-03-01", "2026-03-01")[0]
        assert updated["purchase_price"] == pytest.approx(90.0)

    def test_save_price_response_contains_row_html(self, client, sample_order):
        database.insert_orders([sample_order])
        row_id = database.get_orders("2026-03-01", "2026-03-01")[0]["id"]

        resp = client.post(
            f"/orders/{row_id}/price",
            data={
                "price": "95.00",
                "date_from": "2026-03-01",
                "date_to": "2026-03-01",
            },
        )
        assert resp.status_code == 200
        assert str(row_id) in resp.text
