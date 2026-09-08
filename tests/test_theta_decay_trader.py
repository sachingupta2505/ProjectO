"""
Unit tests for THETA-0DTE Afternoon Strangle Expiry Decay Strategy.
"""

import pytest
from datetime import datetime, date, time as dtime
from unittest.mock import MagicMock, patch

from strategies.theta_decay_trader import ThetaDecayTraderStrategy
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from core.models import Tick, OrderStatus


@pytest.fixture
def mock_theta_setup():
    broker = PaperBroker(account_name="test_theta", initial_capital=100000.0, persist=False)
    risk_manager = RiskManager()
    telegram = MagicMock()
    strategy = ThetaDecayTraderStrategy(
        broker=broker,
        risk_manager=risk_manager,
        lots=2,
        symbol="NIFTY",
        telegram_notifier=telegram
    )
    return strategy, broker, telegram


def test_expiry_day_detection(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup

    # Tuesday: Sep 8, 2026 (weekday = 1)
    tue = datetime(2026, 9, 8, 10, 0)
    strategy.check_expiry_status(tue)
    assert strategy.is_expiry_day is True

    # Wednesday: Sep 9, 2026 (weekday = 2)
    wed = datetime(2026, 9, 9, 10, 0)
    strategy.check_expiry_status(wed)
    assert strategy.is_expiry_day is False


def test_no_entry_before_1245(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup
    strategy.initialize()
    strategy.is_expiry_day = True

    # Bar at 12:40 PM
    bar_1240 = {
        "timestamp": datetime(2026, 9, 8, 12, 40),
        "open": 23660.0,
        "high": 23670.0,
        "low": 23655.0,
        "close": 23665.0,
        "volume": 5000
    }
    strategy.on_bar(bar_1240)
    assert strategy.in_trade is False


def test_entry_at_1245_pm(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup
    strategy.initialize()
    strategy.is_expiry_day = True

    # Spot 23665 -> ATM is 23650 -> CE is 23750 (+100), PE is 23550 (-100)
    bar_1245 = {
        "timestamp": datetime(2026, 9, 8, 12, 45),
        "open": 23665.0,
        "high": 23675.0,
        "low": 23660.0,
        "close": 23670.0,
        "volume": 5000
    }

    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {
            "symbol": f"NIFTY{strike}{opt_type}",
            "ltp": 20.0 if opt_type == "CE" else 25.0
        }
        strategy.on_bar(bar_1245)

    assert strategy.in_trade is True
    assert strategy.ce_open is True
    assert strategy.pe_open is True
    assert strategy.ce_entry_price == 20.0
    assert strategy.pe_entry_price == 25.0
    assert strategy.ce_sl_price == 25.0  # 20.0 * 1.25
    assert strategy.pe_sl_price == 31.25  # 25.0 * 1.25
    assert strategy.ce_order.quantity == 130
    assert strategy.pe_order.quantity == 130
    assert telegram.send_notification.called


def test_tick_entry_trigger(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup
    strategy.initialize()
    strategy.is_expiry_day = True

    # Tick exactly at 12:45:01 PM
    tick = Tick(
        token="26000",
        symbol="NIFTY 50",
        ltp=23675.0,
        timestamp=datetime(2026, 9, 8, 12, 45, 1)
    )

    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {"ltp": 18.0}
        strategy.on_tick(tick)

    assert strategy.in_trade is True
    assert strategy.ce_entry_price == 18.0


def test_stop_loss_hit_on_one_leg(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup
    strategy.initialize()
    strategy.is_expiry_day = True

    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {"ltp": 20.0}
        strategy.enter_theta_strangle(23650.0, datetime(2026, 9, 8, 12, 45))

    assert strategy.in_trade is True
    ce_sl = strategy.ce_sl_price

    # CE spikes to SL
    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {
            "ltp": ce_sl + 1.0 if opt_type == "CE" else 18.0
        }
        strategy.manage_active_strangle(datetime(2026, 9, 8, 13, 0))

    # CE stopped out, PE still open
    assert strategy.ce_open is False
    assert strategy.pe_open is True
    assert strategy.in_trade is True


def test_profit_target_50_percent_decay(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup
    strategy.initialize()
    strategy.is_expiry_day = True

    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {"ltp": 20.0}
        strategy.enter_theta_strangle(23650.0, datetime(2026, 9, 8, 12, 45))

    # Both decay by 60% (remaining premium 40% <= 50%)
    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {
            "ltp": 8.0  # 20.0 * 0.4
        }
        strategy.manage_active_strangle(datetime(2026, 9, 8, 13, 45))

    assert strategy.ce_open is False
    assert strategy.pe_open is False
    assert strategy.in_trade is False
    assert strategy.trade_closed_today is True
    assert strategy.daily_pnl > 0


def test_time_exit_at_1445(mock_theta_setup):
    strategy, broker, telegram = mock_theta_setup
    strategy.initialize()
    strategy.is_expiry_day = True

    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {"ltp": 20.0}
        strategy.enter_theta_strangle(23650.0, datetime(2026, 9, 8, 12, 45))

    with patch("strategies.theta_decay_trader.get_live_option_quote") as mock_quote:
        mock_quote.side_effect = lambda symbol, strike, opt_type: {"ltp": 5.0}
        strategy.manage_active_strangle(datetime(2026, 9, 8, 14, 45))

    assert strategy.in_trade is False
    assert strategy.trade_closed_today is True
