"""Unit and integration tests for Support & Resistance Level Trader Strategy."""

import pytest
from core.level_models import TradingLevel, LevelType, LevelAction
from strategies.level_trader import LevelTraderStrategy
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from core.models import OptionType, Tick


@pytest.fixture
def strategy():
    broker = PaperBroker(initial_capital=200000.0)
    broker.authenticate()
    rms = RiskManager()

    test_levels = [
        TradingLevel(
            id="test_res",
            name="24000 Resistance",
            price=24000.0,
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_pct=0.05,
            sl_pct=0.025,
            volume_multiplier=1.3
        ),
        TradingLevel(
            id="test_sup",
            name="23800 Support",
            price=23800.0,
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BOTH.value,
            target_pct=0.05,
            sl_pct=0.025,
            volume_multiplier=1.3
        )
    ]

    strat = LevelTraderStrategy(
        broker=broker,
        risk_manager=rms,
        levels=test_levels,
        lots=1
    )
    strat.initialize()
    strat.telegram.send_notification = lambda msg: None
    return strat


def test_resistance_breakout_with_volume(strategy):
    # Establish baseline volume (avg = 10,000)
    for _ in range(5):
        strategy.on_bar({"open": 23980, "high": 23990, "low": 23975, "close": 23985, "volume": 10000})

    # High volume breakout candle (> 1.3x avg volume = 13,000)
    breakout_bar = {"open": 23990, "high": 24020, "low": 23985, "close": 24015, "volume": 20000}
    strategy.on_bar(breakout_bar)

    assert strategy.active_instrument is not None
    assert strategy.active_option_type == OptionType.CE
    assert "Resistance Breakout" in strategy.trade_trigger_reason


def test_breakout_ignored_without_volume(strategy):
    # Establish baseline volume (avg = 10,000)
    for _ in range(5):
        strategy.on_bar({"open": 23980, "high": 23990, "low": 23975, "close": 23985, "volume": 10000})

    # Low volume bar crossing level (volume 8,000 < 13,000 threshold)
    weak_bar = {"open": 23990, "high": 24010, "low": 23985, "close": 24005, "volume": 8000}
    strategy.on_bar(weak_bar)

    # Should NOT enter because volume failed confirmation
    assert strategy.active_instrument is None


def test_support_bounce_bullish_reversal(strategy):
    # Bar before support
    strategy.on_bar({"open": 23830, "high": 23840, "low": 23820, "close": 23825, "volume": 10000})

    # Bar touches 23800 and creates a bullish rejection hammer (low 23795, close 23820 > open 23805)
    bounce_bar = {"open": 23805, "high": 23825, "low": 23795, "close": 23820, "volume": 12000}
    strategy.on_bar(bounce_bar)

    assert strategy.active_instrument is not None
    assert strategy.active_option_type == OptionType.CE
    assert "Support Bounce" in strategy.trade_trigger_reason


def test_target_and_sl_execution(strategy):
    # Establish baseline volume
    for _ in range(4):
        strategy.on_bar({"open": 23980, "high": 23990, "low": 23975, "close": 23985, "volume": 10000})

    # Trigger a breakout entry
    strategy.on_bar({"open": 23990, "high": 24020, "low": 23985, "close": 24015, "volume": 20000})

    assert strategy.active_instrument is not None
    entry = strategy.entry_price
    target = strategy.target_price
    symbol = strategy.active_instrument.symbol

    # Price hits target
    strategy.on_tick(Tick(token=1, symbol=symbol, ltp=target + 1.0))
    assert strategy.active_instrument is None


def test_crude_oil_breakout_trade():
    broker = PaperBroker(initial_capital=200000.0)
    broker.authenticate()
    rms = RiskManager()

    crude_levels = [
        TradingLevel(
            id="crude_res_8600",
            name="Crude 8600 Resistance",
            price=8600.0,
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=40.0,
            sl_spot_pts=20.0,
            volume_multiplier=1.3,
            symbol="CRUDEOIL"
        )
    ]

    strat = LevelTraderStrategy(broker=broker, risk_manager=rms, levels=crude_levels, lots=1)
    strat.initialize()
    strat.telegram.send_notification = lambda msg: None

    # Baseline volume for Crude
    for _ in range(4):
        strat.on_bar({"symbol": "CRUDEOIL", "open": 8580, "high": 8590, "low": 8575, "close": 8585, "volume": 5000})

    # High volume breakout candle (> 8600 with volume 10000)
    breakout_bar = {"symbol": "CRUDEOIL", "open": 8590, "high": 8620, "low": 8585, "close": 8615, "volume": 10000}
    strat.on_bar(breakout_bar)

    assert "CRUDEOIL" in strat.active_trades
    trade = strat.active_trades["CRUDEOIL"]
    assert trade["instrument"].exchange == "MCX"
    assert trade["instrument"].asset_class == "COMMODITY"
    assert "Resistance Breakout" in trade["reason"]
    assert trade["target_price"] == round(trade["entry_price"] + 40.0, 2)
    assert trade["sl_price"] == round(trade["entry_price"] - 20.0, 2)

    # Price hits target
    target = trade["target_price"]
    strat.on_tick(Tick(token=294, symbol=trade["instrument"].symbol, ltp=target + 5.0))
    assert "CRUDEOIL" not in strat.active_trades


def test_crude_oil_breakeven_and_trailing():
    broker = PaperBroker(initial_capital=200000.0)
    broker.authenticate()
    rms = RiskManager()

    crude_levels = [
        TradingLevel(
            id="crude_res_8600",
            name="Crude 8600 Resistance",
            price=8600.0,
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=50.0,
            sl_spot_pts=25.0,
            volume_multiplier=1.3,
            symbol="CRUDEOIL"
        )
    ]

    strat = LevelTraderStrategy(broker=broker, risk_manager=rms, levels=crude_levels, lots=1)
    strat.initialize()
    strat.telegram.send_notification = lambda msg: None

    for _ in range(4):
        strat.on_bar({"symbol": "CRUDEOIL", "open": 8580, "high": 8590, "low": 8575, "close": 8585, "volume": 5000})

    breakout_bar = {"symbol": "CRUDEOIL", "open": 8590, "high": 8620, "low": 8585, "close": 8615, "volume": 10000}
    strat.on_bar(breakout_bar)

    assert "CRUDEOIL" in strat.active_trades
    trade = strat.active_trades["CRUDEOIL"]
    entry = trade["entry_price"]
    sym = trade["instrument"].symbol

    # Move up +21 pts -> triggers breakeven lock
    strat.on_tick(Tick(token=294, symbol=sym, ltp=entry + 21.0))
    assert trade["breakeven_locked"] is True
    assert trade["sl_price"] >= entry

    # Move up +35 pts -> triggers trailing SL
    strat.on_tick(Tick(token=294, symbol=sym, ltp=entry + 35.0))
    assert trade["trailing_active"] is True
    assert trade["sl_price"] == round((entry + 35.0) - 15.0, 2)
    assert trade["sl_price"] > entry

    # Retrace to hit trailing SL
    strat.on_tick(Tick(token=294, symbol=sym, ltp=trade["sl_price"] - 1.0))
    assert "CRUDEOIL" not in strat.active_trades


def test_shooting_star_fakeout_rejected(strategy):
    # Establish baseline volume
    for _ in range(5):
        strategy.on_bar({"open": 23980, "high": 23990, "low": 23975, "close": 23985, "volume": 10000})

    # Fakeout candle: high spikes above 24000 to 24040, but closes near open as a shooting star/doji
    shooting_star = {"open": 23995, "high": 24040, "low": 23990, "close": 24002, "volume": 25000}
    strategy.on_bar(shooting_star)

    # Should be rejected because upper wick (38 pts) vastly exceeds body (7 pts)
    assert strategy.active_instrument is None


def test_breakeven_lock_and_trailing_stop(strategy):
    # Establish baseline volume
    for _ in range(4):
        strategy.on_bar({"open": 23980, "high": 23990, "low": 23975, "close": 23985, "volume": 10000})

    # Trigger breakout
    breakout_bar = {"open": 23990, "high": 24020, "low": 23985, "close": 24015, "volume": 20000}
    strategy.on_bar(breakout_bar)

    assert strategy.active_instrument is not None
    entry_sl = strategy.sl_price
    entry_px = strategy.entry_price
    symbol = strategy.active_instrument.symbol
    trade = strategy.active_trades["NIFTY"]

    # Advance price to +2.1% profit -> triggers breakeven lock
    tick_be = Tick(token=1, symbol=symbol, ltp=round(entry_px * 1.021, 2))
    strategy.on_tick(tick_be)

    assert trade["breakeven_locked"] is True
    assert strategy.sl_price > entry_sl
    assert strategy.sl_price >= entry_px  # SL is at or above entry price!

    # Advance price further to +4.0% profit -> triggers dynamic trailing SL
    tick_trail = Tick(token=1, symbol=symbol, ltp=round(entry_px * 1.04, 2))
    strategy.on_tick(tick_trail)

    assert trade["trailing_active"] is True
    trail_sl = strategy.sl_price
    assert trail_sl > entry_px  # Trailing SL has locked in profit

    # Pullback hitting trailing stop loss
    strategy.on_tick(Tick(token=1, symbol=symbol, ltp=trail_sl - 0.5))
    assert strategy.active_instrument is None  # Trade successfully exited in locked profit!


