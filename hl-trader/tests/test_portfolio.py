from datetime import datetime, timedelta, timezone

import pytest

from hltrader.portfolio import Portfolio, Position

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_long_pnl_is_positive_when_price_rises():
    pos = Position("BTC", 2.0, 100.0, 95.0)
    assert pos.unrealized_pnl(110.0) == pytest.approx(20.0)


def test_short_pnl_is_positive_when_price_falls():
    pos = Position("BTC", -2.0, 100.0, 105.0)
    assert pos.unrealized_pnl(90.0) == pytest.approx(20.0)


def test_long_stop_triggers_on_the_low():
    pos = Position("BTC", 1.0, 100.0, 95.0)
    assert pos.stop_hit(low=94.0, high=101.0)
    assert not pos.stop_hit(low=96.0, high=101.0)


def test_short_stop_triggers_on_the_high():
    pos = Position("BTC", -1.0, 100.0, 105.0)
    assert pos.stop_hit(low=99.0, high=106.0)
    assert not pos.stop_hit(low=99.0, high=104.0)


def test_target_only_triggers_when_set():
    assert not Position("BTC", 1.0, 100.0, 95.0).target_hit(90.0, 200.0)
    assert Position("BTC", 1.0, 100.0, 95.0, target_price=110.0).target_hit(99.0, 111.0)


def test_round_trip_pnl_flows_into_cash():
    p = Portfolio(10_000.0)
    p.open_position("BTC", 1.0, 100.0, 95.0, None, fee=1.0, when=T0)
    assert p.cash == pytest.approx(9_999.0)
    trade = p.close_position("BTC", 120.0, fee=1.2, when=T0, reason="target")
    assert trade.pnl == pytest.approx(20.0)
    assert p.cash == pytest.approx(9_999.0 + 20.0 - 1.2)
    assert p.total_fees == pytest.approx(2.2)


def test_equity_marks_open_positions_to_market():
    p = Portfolio(10_000.0)
    p.open_position("BTC", 1.0, 100.0, 95.0, None, fee=0.0, when=T0)
    assert p.equity({"BTC": 130.0}) == pytest.approx(10_030.0)


def test_gross_and_net_notional_treat_shorts_differently():
    p = Portfolio(10_000.0)
    p.open_position("A", 10.0, 100.0, 95.0, None, 0.0, T0)
    p.open_position("B", -10.0, 100.0, 105.0, None, 0.0, T0)
    prices = {"A": 100.0, "B": 100.0}
    assert p.gross_notional(prices) == pytest.approx(2_000.0)
    assert p.net_notional(prices) == pytest.approx(0.0)


def test_funding_charges_longs_and_credits_shorts():
    p = Portfolio(10_000.0)
    p.open_position("A", 10.0, 100.0, 95.0, None, 0.0, T0)
    # Positive rate: longs pay.
    assert p.apply_funding("A", 0.0001, 100.0) == pytest.approx(-0.1)

    p.open_position("B", -10.0, 100.0, 105.0, None, 0.0, T0)
    assert p.apply_funding("B", 0.0001, 100.0) == pytest.approx(0.1)


def test_funding_is_included_in_realised_trade_pnl():
    p = Portfolio(10_000.0)
    p.open_position("A", 10.0, 100.0, 95.0, None, 0.0, T0)
    p.apply_funding("A", 0.001, 100.0)  # -1.0
    trade = p.close_position("A", 100.0, 0.0, T0, "flat")
    assert trade.funding == pytest.approx(-1.0)
    assert trade.pnl == pytest.approx(-1.0)


def test_high_water_mark_only_ratchets_up():
    p = Portfolio(10_000.0)
    p.open_position("A", 1.0, 100.0, 95.0, None, 0.0, T0)
    p.mark(T0, {"A": 150.0})
    assert p.high_water_mark == pytest.approx(10_050.0)
    p.mark(T0 + timedelta(hours=1), {"A": 50.0})
    assert p.high_water_mark == pytest.approx(10_050.0)


def test_drawdown_is_measured_from_the_high_water_mark():
    p = Portfolio(10_000.0)
    p.high_water_mark = 20_000.0
    assert p.drawdown_pct(15_000.0) == pytest.approx(0.25)
    assert p.drawdown_pct(25_000.0) == 0.0


def test_daily_pnl_resets_when_the_utc_day_rolls():
    p = Portfolio(10_000.0)
    p.mark(T0, {})
    p.open_position("A", 1.0, 100.0, 95.0, None, 0.0, T0)

    # Same day, down 5%.
    equity = p.mark(T0 + timedelta(hours=5), {"A": 100.0})
    p.cash -= 500
    equity = p.equity({"A": 100.0})
    assert p.daily_pnl_pct(equity) == pytest.approx(-0.05)

    # New day: the loss is history, the counter starts fresh.
    p.mark(T0 + timedelta(days=1), {"A": 100.0})
    assert p.daily_pnl_pct(p.equity({"A": 100.0})) == pytest.approx(0.0)


def test_double_opening_the_same_symbol_is_rejected():
    p = Portfolio(10_000.0)
    p.open_position("A", 1.0, 100.0, 95.0, None, 0.0, T0)
    with pytest.raises(ValueError):
        p.open_position("A", 1.0, 100.0, 95.0, None, 0.0, T0)
