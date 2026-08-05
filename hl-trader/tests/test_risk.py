import pytest

from hltrader.config import RiskConfig
from hltrader.portfolio import Portfolio
from hltrader.risk import RiskManager
from hltrader.strategies.base import Signal


@pytest.fixture
def risk():
    return RiskManager(
        RiskConfig(
            risk_per_trade=0.01,
            max_position_leverage=3.0,
            max_gross_leverage=4.0,
            max_net_leverage=2.5,
            max_concurrent_positions=3,
            daily_loss_limit=0.06,
            max_drawdown_limit=0.20,
            min_order_notional=10.0,
        ),
        taker_fee=0.0,
    )


@pytest.fixture
def portfolio():
    return Portfolio(10_000.0)


def signal(symbol="BTC", direction=1.0, stop=0.05):
    return Signal(symbol=symbol, direction=direction, stop_distance=stop)


def test_size_risks_exactly_the_configured_fraction(risk, portfolio):
    """A stop-out must cost risk_per_trade of equity -- no more, no less."""
    order = risk.size_order(signal(stop=0.05), 100.0, 10_000.0, portfolio, {})
    loss_at_stop = abs(order.size) * abs(order.reference_price - order.stop_price)
    assert loss_at_stop == pytest.approx(10_000 * 0.01)


def test_wider_stops_get_smaller_positions(risk, portfolio):
    tight = risk.size_order(signal(stop=0.02), 100.0, 10_000.0, portfolio, {})
    wide = risk.size_order(signal(stop=0.10), 100.0, 10_000.0, portfolio, {})
    assert wide.notional < tight.notional
    # Both still risk the same dollars, which is the whole point.
    assert tight.notional * 0.02 == pytest.approx(wide.notional * 0.10)


def test_per_position_leverage_cap_binds(portfolio):
    # A 0.1% stop implies 100x notional on risk alone. Loosen the portfolio
    # caps so the per-position cap is the one actually being tested.
    risk = RiskManager(
        RiskConfig(
            risk_per_trade=0.01,
            max_position_leverage=3.0,
            max_gross_leverage=20.0,
            max_net_leverage=20.0,
        ),
        taker_fee=0.0,
    )
    order = risk.size_order(signal(stop=0.001), 100.0, 10_000.0, portfolio, {})
    assert order.notional == pytest.approx(10_000 * 3.0)


def test_the_tightest_cap_is_the_one_that_binds(risk, portfolio):
    # With net capped at 2.5x and position at 3.0x, net wins.
    order = risk.size_order(signal(stop=0.001), 100.0, 10_000.0, portfolio, {})
    assert order.notional == pytest.approx(10_000 * 2.5)


def test_fees_are_charged_against_the_risk_budget(portfolio):
    risk = RiskManager(RiskConfig(risk_per_trade=0.01), taker_fee=0.001)
    order = risk.size_order(signal(stop=0.01), 100.0, 10_000.0, portfolio, {})
    # Effective stop is 1% + 2 * 0.1% = 1.2%, so the position is smaller.
    assert order.notional == pytest.approx(100 / 0.012)


def test_gross_leverage_cap_limits_the_next_position(risk, portfolio):
    prices = {"A": 100.0}
    # Held short, so it consumes gross leverage without consuming long-side
    # net headroom -- which isolates the gross cap as the binding one.
    portfolio.open_position("A", -380.0, 100.0, 105.0, None, 0.0, _now())
    # 38,000 of the 40,000 gross budget is used; only 2,000 remains.
    order = risk.size_order(signal("B", stop=0.05), 100.0, 10_000.0, portfolio, prices)
    assert order.notional == pytest.approx(2_000.0)


def test_net_exposure_cap_limits_a_same_side_position(risk, portfolio):
    prices = {"A": 100.0}
    portfolio.open_position("A", 240.0, 100.0, 95.0, None, 0.0, _now())
    # Net is +24,000 against a 25,000 limit; only 1,000 of long remains.
    order = risk.size_order(signal("B", stop=0.05), 100.0, 10_000.0, portfolio, prices)
    assert order.notional == pytest.approx(1_000.0)


def test_net_exposure_cap_still_allows_the_opposite_side(risk, portfolio):
    prices = {"A": 100.0}
    portfolio.open_position("A", 240.0, 100.0, 95.0, None, 0.0, _now())
    order = risk.size_order(
        signal("B", direction=-1.0, stop=0.05), 100.0, 10_000.0, portfolio, prices
    )
    # Shorting reduces net exposure, so it is not squeezed by the net cap.
    assert order is not None
    assert order.notional > 1_000.0


def test_max_concurrent_positions_blocks_new_entries(risk, portfolio):
    for i in range(3):
        portfolio.open_position(f"S{i}", 1.0, 100.0, 95.0, None, 0.0, _now())
    assert risk.size_order(signal("NEW"), 100.0, 10_000.0, portfolio, {}) is None


def test_existing_position_is_not_re_entered(risk, portfolio):
    portfolio.open_position("BTC", 1.0, 100.0, 95.0, None, 0.0, _now())
    assert risk.size_order(signal("BTC"), 100.0, 10_000.0, portfolio, {}) is None


def test_dust_orders_are_rejected(risk, portfolio):
    order = risk.size_order(signal(stop=0.05), 100.0, 20.0, portfolio, {})
    assert order is None


def test_stop_price_sits_below_entry_for_a_long(risk, portfolio):
    order = risk.size_order(signal(direction=1.0, stop=0.05), 100.0, 10_000.0, portfolio, {})
    assert order.stop_price == pytest.approx(95.0)
    assert order.size > 0


def test_stop_price_sits_above_entry_for_a_short(risk, portfolio):
    order = risk.size_order(signal(direction=-1.0, stop=0.05), 100.0, 10_000.0, portfolio, {})
    assert order.stop_price == pytest.approx(105.0)
    assert order.size < 0


def test_daily_loss_limit_halts_but_is_not_hard(risk, portfolio):
    portfolio.mark(_now(), {})  # sets the day's opening equity to 10,000
    state = risk.check_halt(portfolio, 9_300.0)
    assert state.halted and not state.hard


def test_max_drawdown_triggers_a_hard_halt_that_persists(risk, portfolio):
    portfolio.high_water_mark = 12_000.0
    assert risk.check_halt(portfolio, 9_000.0).hard
    # Recovering does not clear it: a human has to look first.
    assert risk.check_halt(portfolio, 11_999.0).hard
    risk.reset_hard_halt()
    assert not risk.check_halt(portfolio, 11_999.0).halted


def test_no_halt_when_healthy(risk, portfolio):
    portfolio.mark(_now(), {})
    assert not risk.check_halt(portfolio, 10_500.0).halted


def _now():
    from datetime import datetime, timezone

    return datetime(2024, 1, 1, tzinfo=timezone.utc)
