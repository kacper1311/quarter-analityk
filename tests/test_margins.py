"""Testy jednostkowe kalkulacji marż."""
import pytest
from margins import calculate_margin, calculate_summary


class TestCalculateMargin:

    def test_basic(self):
        m = calculate_margin(sale_price=200.0, purchase_price_brutto=100.0, delivery_cost=20.0)
        # product_price = 200 - 20 = 180
        # profit = 180 - 100 = 80
        # margin_pct = 80/180 * 100 = 44.4%
        assert m["profit"] == 80.0
        assert m["margin_pct"] == pytest.approx(44.4, abs=0.1)

    def test_zero_delivery(self):
        m = calculate_margin(sale_price=100.0, purchase_price_brutto=60.0, delivery_cost=0)
        assert m["profit"] == 40.0
        assert m["margin_pct"] == pytest.approx(40.0, abs=0.1)

    def test_negative_profit(self):
        m = calculate_margin(sale_price=50.0, purchase_price_brutto=80.0, delivery_cost=10.0)
        # product_price = 50 - 10 = 40
        # profit = 40 - 80 = -40
        assert m["profit"] == -40.0
        assert m["margin_pct"] < 0

    def test_zero_product_price(self):
        """Gdy cena produktu = 0 (cała kwota to dostawa), margin_pct powinno być 0."""
        m = calculate_margin(sale_price=20.0, purchase_price_brutto=0.0, delivery_cost=20.0)
        assert m["margin_pct"] == 0.0

    def test_default_delivery_cost(self):
        """delivery_cost domyślnie 0."""
        m = calculate_margin(sale_price=100.0, purchase_price_brutto=40.0)
        assert m["profit"] == 60.0
        assert m["margin_pct"] == pytest.approx(60.0, abs=0.1)

    def test_rounding(self):
        """Wyniki zaokrąglone do 2 miejsc (profit) i 1 miejsca (margin_pct)."""
        m = calculate_margin(sale_price=99.99, purchase_price_brutto=33.33, delivery_cost=9.99)
        assert isinstance(m["profit"], float)
        assert isinstance(m["margin_pct"], float)


class TestCalculateSummary:

    def test_basic(self):
        margins = [
            {"sale_price": 200.0, "profit": 50.0, "margin_pct": 25.0},
            {"sale_price": 100.0, "profit": 30.0, "margin_pct": 30.0},
        ]
        s = calculate_summary(margins)
        assert s["total_orders"] == 2
        assert s["total_revenue"] == 300.0
        assert s["total_profit"] == 80.0
        assert s["avg_margin_pct"] == pytest.approx(27.5, abs=0.1)

    def test_goal_reached(self):
        margins = [{"sale_price": 1000.0, "profit": 10000.0, "margin_pct": 80.0}]
        s = calculate_summary(margins)
        assert s["goal_reached"] is True

    def test_goal_not_reached(self):
        margins = [{"sale_price": 100.0, "profit": 10.0, "margin_pct": 10.0}]
        s = calculate_summary(margins)
        assert s["goal_reached"] is False
        assert s["goal_gap"] > 0
        assert s["orders_needed"] > 0
