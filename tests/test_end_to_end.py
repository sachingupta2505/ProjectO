"""End-to-end integration test of the trading bot cycle."""
from datetime import datetime
from config.settings import settings
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from strategies.short_straddle import ShortStraddleStrategy
from strategies.momentum_buyer import MomentumBuyerStrategy
from dashboard.terminal_ui import render_dashboard
from core.models import Tick


def test_straddle_lifecycle_e2e():
    broker = PaperBroker(initial_capital=200000.0)
    broker.authenticate()
    rms = RiskManager()
    strategy = ShortStraddleStrategy(broker=broker, risk_manager=rms, lots=1)
    strategy.initialize()

    # Trigger entry at 24500 spot
    strategy._execute_entry(spot_price=24500.0)
    assert strategy.entry_done is True
    assert strategy.ce_instrument is not None
    assert strategy.pe_instrument is not None

    positions = broker.get_positions()
    assert len(positions) == 2
    assert positions[strategy.ce_instrument.symbol].quantity == -settings.NIFTY_LOT_SIZE
    assert positions[strategy.pe_instrument.symbol].quantity == -settings.NIFTY_LOT_SIZE

    # Check terminal layout rendering
    layout = render_dashboard(broker, strategy, rms, 24500.0)
    assert layout is not None

    # Simulate CE Stop Loss hit
    ce_sl = strategy.ce_sl_price
    strategy.on_tick(Tick(token=1, symbol=strategy.ce_instrument.symbol, ltp=ce_sl + 2.0))
    assert strategy.ce_exited is True
    assert positions[strategy.ce_instrument.symbol].quantity == 0

    # Auto square-off remaining leg
    strategy.check_exit_conditions(datetime(2026, 9, 10, 15, 20, 0))
    assert strategy.pe_exited is True
    assert positions[strategy.pe_instrument.symbol].quantity == 0


def test_momentum_buyer_lifecycle_e2e():
    broker = PaperBroker(initial_capital=200000.0)
    broker.authenticate()
    rms = RiskManager()
    strategy = MomentumBuyerStrategy(broker=broker, risk_manager=rms, lots=1)
    strategy.initialize()

    # Simulate bullish crossover: prices rising
    prices = [24400, 24410, 24420, 24430, 24440, 24450, 24460, 24470, 24480, 24500, 24520, 24550, 24580, 24600, 24620, 24650, 24680, 24700, 24720, 24750, 24800, 24850]
    for p in prices:
        strategy.on_bar({"close": p, "volume": 1000})

    assert strategy.active_position is not None
    assert strategy.active_instrument is not None

    # Simulate price moving to target
    target = strategy.target_price
    strategy.on_tick(Tick(token=2, symbol=strategy.active_instrument.symbol, ltp=target + 5.0))
    assert strategy.active_position is None  # Position closed on target hit
