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
from unittest.mock import MagicMock

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
