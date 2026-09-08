"""
Unit tests for OpeningRetestStrategy.
Verifies:
1. Strategy initialization and day reset
2. Rejection of weak body (< 30 pts) and rejection wicks
3. Proper calculation of 50% retest zone, invalidation SL, and Targets
4. Entry execution on retest bounce
5. Breakeven ratchet upon hitting Target 1
6. Exit at Target 2 and SL
"""

import pytest
from datetime import datetime, date, time
from unittest.mock import MagicMock, patch
import pandas as pd

from strategies.opening_retest_trader import OpeningRetestStrategy
from core.models import Instrument, Order, OrderSide, OrderType, OrderStatus
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager


@pytest.fixture
def mock_setup():
    broker = PaperBroker(initial_capital=50000.0, persist=False)
    risk_manager = RiskManager(max_daily_loss=5000.0, max_daily_profit=10000.0)
    strategy = OpeningRetestStrategy(
        broker=broker,
        risk_manager=risk_manager,
        symbol="NIFTY",
        lots=1,
        min_body_points=30.0,
        retest_leeway=5.0
    )
    strategy.initialize()
    return strategy, broker, risk_manager


def test_opening_retest_weak_candle_rejected(mock_setup):
    strategy, broker, rm = mock_setup

    # Feed 3 bars with only 10 point body (24000 to 24010)
    d = date(2026, 9, 8)
    bars = [
        {"timestamp": datetime.combine(d, time(9, 15)), "open": 24000.0, "high": 24015.0, "low": 23995.0, "close": 24005.0, "volume": 1000},
        {"timestamp": datetime.combine(d, time(9, 20)), "open": 24005.0, "high": 24020.0, "low": 24000.0, "close": 24008.0, "volume": 1000},
        {"timestamp": datetime.combine(d, time(9, 25)), "open": 24008.0, "high": 24015.0, "low": 24002.0, "close": 24010.0, "volume": 1000},
    ]

    for b in bars:
        strategy.on_bar(b)

    # Trigger evaluation at 09:30
    strategy.on_bar({"timestamp": datetime.combine(d, time(9, 30)), "open": 24010.0, "high": 24012.0, "low": 24005.0, "close": 24008.0, "volume": 1000})

    assert strategy.setup_valid is False
    assert strategy.in_trade is False


def test_opening_retest_valid_bullish_setup_and_trigger(mock_setup):
    strategy, broker, rm = mock_setup

    # Feed 3 bars with 50 point green body (24000 to 24050)
    d = date(2026, 9, 8)
    bars = [
        {"timestamp": datetime.combine(d, time(9, 15)), "open": 24000.0, "high": 24020.0, "low": 23990.0, "close": 24015.0, "volume": 1000},
        {"timestamp": datetime.combine(d, time(9, 20)), "open": 24015.0, "high": 24035.0, "low": 24010.0, "close": 24030.0, "volume": 1000},
        {"timestamp": datetime.combine(d, time(9, 25)), "open": 24030.0, "high": 24055.0, "low": 24025.0, "close": 24050.0, "volume": 1000},
    ]

    for b in bars:
        strategy.on_bar(b)

    # Trigger evaluation at 09:30
    strategy.on_bar({"timestamp": datetime.combine(d, time(9, 30)), "open": 24050.0, "high": 24052.0, "low": 24040.0, "close": 24045.0, "volume": 1000})

    assert strategy.setup_valid is True
    assert strategy.setup_side == "CALL"
    assert strategy.retest_min == 24000.0
    assert strategy.retest_max == 24025.0  # 50% retracement
    assert strategy.target1_spot == 24055.0
    assert strategy.target2_spot == 24055.0 + (50.0 * 0.80)  # 24095.0
    assert strategy.invalidation_spot == 23990.0 - 5.0  # 23985.0

    # At 09:40, price pulls back to 24020 (inside retest zone) and closes green
    retest_bar = {
        "timestamp": datetime.combine(d, time(9, 40)),
        "open": 24018.0,
        "high": 24028.0,
        "low": 24016.0,
        "close": 24025.0,
        "volume": 1500
    }
    strategy.on_bar(retest_bar)

    assert strategy.in_trade is True
    assert strategy.entry_spot == 24025.0
    assert strategy.breakeven_ratchet_done is False

    # Simulate price moving to Target 1 (24055.0) -> Breakeven Ratchet should activate
    strategy.manage_active_trade(24060.0, datetime.combine(d, time(10, 0)))
    assert strategy.breakeven_ratchet_done is True
    assert strategy.in_trade is True

    # Simulate price moving to Target 2 (24095.0) -> Should close with profit
    strategy.manage_active_trade(24096.0, datetime.combine(d, time(10, 30)))
    assert strategy.in_trade is False
    assert strategy.trade_closed_today is True


def test_runtime_opening_bars_are_replaced_with_authoritative_candles(mock_setup):
    """A stale tick-built opening range must not decide the live ORION setup."""
    strategy, _, _ = mock_setup
    d = date(2026, 9, 8)
    stale_bars = [
        {"timestamp": datetime.combine(d, time(9, 15)), "open": 23950.0, "high": 23950.0, "low": 23930.0, "close": 23940.0, "data_source": "Fallback", "is_live": False},
        {"timestamp": datetime.combine(d, time(9, 20)), "open": 23940.0, "high": 23942.0, "low": 23925.0, "close": 23930.0, "data_source": "Fallback", "is_live": False},
        {"timestamp": datetime.combine(d, time(9, 25)), "open": 23930.0, "high": 23935.0, "low": 23920.0, "close": 23926.8, "data_source": "Fallback", "is_live": False},
    ]
    authoritative = pd.DataFrame([
        [datetime.combine(d, time(9, 15)), 23743.1, 23758.95, 23680.65, 23694.15, 0],
        [datetime.combine(d, time(9, 20)), 23693.6, 23695.4, 23678.9, 23679.5, 0],
        [datetime.combine(d, time(9, 25)), 23680.6, 23685.05, 23669.2, 23672.5, 0],
    ], columns=["timestamp", "open", "high", "low", "close", "volume"])

    strategy.bars_5m = stale_bars
    with patch("scripts.download_candles.fetch_and_save_candles", return_value=authoritative):
        strategy.evaluate_opening_15m_candle(datetime.combine(d, time(9, 30)))

    assert strategy.setup_valid is True
    assert strategy.setup_side == "PUT"
    assert strategy.body_15m == pytest.approx(70.6)
    assert strategy.candle_15m["source"] == "Angel historical"


def test_runtime_opening_bars_are_not_used_when_authoritative_fetch_fails(mock_setup):
    strategy, _, _ = mock_setup
    d = date(2026, 9, 8)
    strategy.bars_5m = [
        {"timestamp": datetime.combine(d, time(9, minute)), "open": 24000.0, "high": 24010.0, "low": 23995.0, "close": 24005.0, "data_source": "Fallback", "is_live": False}
        for minute in (15, 20, 25)
    ]

    with patch("scripts.download_candles.fetch_and_save_candles", side_effect=RuntimeError("rate limited")):
        strategy.evaluate_opening_15m_candle(datetime.combine(d, time(9, 30)))

    assert strategy.setup_valid is False
    assert strategy.candle_15m is None
    assert strategy.opening_fetch_attempts == 1
    assert strategy.next_opening_fetch_retry_at == datetime.combine(d, time(9, 35))


def test_orion_20_uses_balanced_zone_and_confirmation(mock_setup):
    _, broker, risk_manager = mock_setup
    d = date(2026, 9, 8)
    strategy = OpeningRetestStrategy(
        broker=broker,
        risk_manager=risk_manager,
        symbol="NIFTY",
        retrace_low=0.35,
        retrace_high=0.65,
        confirmation_body_ratio=0.15,
        entry_cutoff="11:00",
        version="ORION-2.0",
    )
    strategy.initialize()
    bars = [
        {"timestamp": datetime.combine(d, time(9, 15)), "open": 24000.0, "high": 24005.0, "low": 23990.0, "close": 23982.0, "volume": 0},
        {"timestamp": datetime.combine(d, time(9, 20)), "open": 23982.0, "high": 23985.0, "low": 23970.0, "close": 23965.0, "volume": 0},
        {"timestamp": datetime.combine(d, time(9, 25)), "open": 23965.0, "high": 23968.0, "low": 23945.0, "close": 23950.0, "volume": 0},
    ]
    for bar in bars:
        strategy.on_bar(bar)
    strategy.on_bar({"timestamp": datetime.combine(d, time(9, 30)), "open": 23950.0, "high": 23960.0, "low": 23948.0, "close": 23955.0, "volume": 0})

    assert strategy.name == "ORION-2.0 (NIFTY)"
    assert strategy.retest_min == pytest.approx(23967.5)
    assert strategy.retest_max == pytest.approx(23982.5)

    # Red rejection with a 10-point body clears the 7.5-point confirmation.
    strategy.on_bar({"timestamp": datetime.combine(d, time(9, 35)), "open": 23978.0, "high": 23982.0, "low": 23965.0, "close": 23968.0, "volume": 0})
    assert strategy.in_trade is True


def test_stop_based_sizer_caps_orion_at_two_lots(mock_setup):
    _, broker, risk_manager = mock_setup
    strategy = OpeningRetestStrategy(
        broker=broker,
        risk_manager=risk_manager,
        symbol="NIFTY",
        lots=2,
        max_lots=2,
        risk_per_trade=2500.0,
    )
    strategy.initialize()
    strategy.setup_side = "CALL"
    strategy.invalidation_spot = 23980.0
    now = datetime(2026, 9, 9, 10, 0)
    with patch("strategies.opening_retest_trader.get_live_option_quote", return_value={"price": 100.0, "symbol": "NIFTY_TEST_CE", "token": "1"}):
        strategy.execute_entry(24000.0, now)

    # 20 spot points estimates to a 10-point option stop: ₹705 per lot,
    # so the ₹2,500 budget permits more than two lots but the policy cap wins.
    assert strategy.sized_lots == 2
    assert strategy.estimated_loss_per_lot == 705.0
    assert strategy.trade_order.quantity == 130
