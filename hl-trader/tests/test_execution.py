"""Venue precision rules and the paper broker.

Getting size/price rounding wrong is not cosmetic: Hyperliquid rejects
out-of-spec orders outright, so a system that rounds badly simply never trades.
"""

import pytest

from hltrader.broker import PaperBroker
from hltrader.marketdata import Instrument


def inst(sz_decimals=2, is_spot=False):
    return Instrument(
        name="TEST", sz_decimals=sz_decimals, max_leverage=10, is_spot=is_spot, mark_px=100.0
    )


def test_size_is_rounded_to_the_venue_precision():
    assert inst(sz_decimals=2).round_size(1.23456) == pytest.approx(1.23)
    assert inst(sz_decimals=0).round_size(1.7) == pytest.approx(2.0)


def test_price_keeps_at_most_five_significant_figures():
    assert inst().round_price(1.234567) == pytest.approx(1.2346)
    # Spot with szDecimals=0 allows 8 decimals, so sig figs are what bind.
    spot = inst(sz_decimals=0, is_spot=True)
    assert spot.round_price(0.00123456) == pytest.approx(0.0012346)


@pytest.mark.parametrize(
    "sz_decimals,expected",
    [(0, 0.001235), (2, 0.0012)],
)
def test_the_decimal_cap_wins_when_it_is_tighter_than_sig_figs(sz_decimals, expected):
    """Perps cap decimals at 6 - szDecimals, which can bite before 5 sig figs.

    Submitting more precision than this is rejected by the venue outright.
    """
    assert inst(sz_decimals=sz_decimals).round_price(0.00123456) == pytest.approx(expected)


def test_high_priced_assets_round_to_whole_numbers():
    # 64,951.37 has more than 5 significant figures; integers are always legal.
    assert inst(sz_decimals=5).round_price(64951.37) == pytest.approx(64951.0)


def test_decimal_cap_is_tighter_than_significant_figures_for_large_sz_decimals():
    # A perp with szDecimals=5 allows only 6 - 5 = 1 decimal place.
    i = inst(sz_decimals=5)
    assert i.px_decimals == 1
    assert i.round_price(1.23456) == pytest.approx(1.2)


def test_spot_gets_two_more_decimal_places_than_perps():
    assert inst(sz_decimals=2, is_spot=True).px_decimals == 6
    assert inst(sz_decimals=2, is_spot=False).px_decimals == 4


def test_non_positive_prices_round_to_zero():
    assert inst().round_price(0.0) == 0.0
    assert inst().round_price(-5.0) == 0.0


class FakeMarket:
    def __init__(self, price):
        self._price = price

    def mid_prices(self):
        return {"TEST": self._price}


def test_paper_buys_above_the_mid_and_sells_below_it():
    broker = PaperBroker(FakeMarket(100.0), 10_000.0, taker_fee=0.0, slippage=0.001)
    buy = broker.market_order(inst(), 1.0).fill
    sell = broker.market_order(inst(), -1.0).fill
    assert buy.price == pytest.approx(100.1)
    assert sell.price == pytest.approx(99.9)


def test_paper_charges_the_taker_fee_on_notional():
    broker = PaperBroker(FakeMarket(100.0), 10_000.0, taker_fee=0.001, slippage=0.0)
    fill = broker.market_order(inst(), 2.0).fill
    assert fill.fee == pytest.approx(2.0 * 100.0 * 0.001)


def test_paper_rejects_a_zero_size_order():
    broker = PaperBroker(FakeMarket(100.0), 10_000.0)
    assert not broker.market_order(inst(), 0.0).ok


def test_paper_fill_size_keeps_the_sign_of_the_request():
    broker = PaperBroker(FakeMarket(100.0), 10_000.0)
    assert broker.market_order(inst(), -3.0).fill.size == pytest.approx(-3.0)
