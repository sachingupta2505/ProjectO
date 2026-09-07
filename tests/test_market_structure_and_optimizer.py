"""
Verification Script: Test Market Structure Engine and Multi-Tier Active Trade Optimizer.
"""

import sys
from pathlib import Path
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.market_structure import MarketStructureEngine, StructureRegime
from core.models import Instrument, OrderSide, Tick
from strategies.level_trader import LevelTraderStrategy
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from config.settings import settings


def test_market_structure():
    print("--- 1. Testing Market Structure Engine ---")
    mse = MarketStructureEngine(ema_period=5)

    # Feed rising bars (Uptrend)
    prices = [23800, 23810, 23820, 23835, 23850, 23865, 23880]
    for p in prices:
        mse.update_bar({"symbol": "NIFTY", "close": p, "high": p + 5, "low": p - 2, "volume": 10000})

    status = mse.get_market_structure("NIFTY")
    print(f"Uptrend Status: Regime={status['regime']}, EMA={status['ema_20']}, Slope={status['ema_slope']}")
    assert status["regime"] == StructureRegime.BULLISH.value, "Expected BULLISH regime!"

    # Test alignment
    allow_call, r1 = mse.validate_setup_alignment("BULLISH", "NIFTY")
    allow_put, r2 = mse.validate_setup_alignment("BEARISH", "NIFTY")
    print(f"Call Allowed in Uptrend: {allow_call} ({r1})")
    print(f"Put Allowed in Uptrend:  {allow_put} ({r2})")
    assert allow_call is True, "Call should be allowed in Uptrend!"
    assert allow_put is False, "Put should be blocked in Uptrend!"

    # Feed sharp falling bars (Downtrend)
    falling = [23840, 23810, 23780, 23750, 23720]
    for p in falling:
        mse.update_bar({"symbol": "NIFTY", "close": p, "high": p + 2, "low": p - 5, "volume": 15000})

    status_down = mse.get_market_structure("NIFTY")
    print(f"Downtrend Status: Regime={status_down['regime']}, EMA={status_down['ema_20']}, Slope={status_down['ema_slope']}")
    assert status_down["regime"] == StructureRegime.BEARISH.value, "Expected BEARISH regime!"

    allow_call_down, r3 = mse.validate_setup_alignment("BULLISH", "NIFTY")
    allow_put_down, r4 = mse.validate_setup_alignment("BEARISH", "NIFTY")
    print(f"Call Allowed in Downtrend: {allow_call_down} ({r3})")
    print(f"Put Allowed in Downtrend:  {allow_put_down} ({r4})")
    assert allow_call_down is False, "Call should be blocked in Downtrend!"
    assert allow_put_down is True, "Put should be allowed in Downtrend!"
    print("✅ Market Structure Tests Passed!\n")


def test_active_trade_optimizer():
    print("--- 2. Testing Multi-Tier Active Trade Optimizer ---")
    broker = PaperBroker(initial_capital=200000.0, persist=False)
    risk_manager = RiskManager(max_daily_profit=11000.0, max_daily_loss=6000.0)
    strat = LevelTraderStrategy(broker=broker, risk_manager=risk_manager)

    # Simulate an active trade: Buy @ 100.0, Target @ 120.0 (+20 pts), Initial SL @ 90.0 (-10 pts)
    inst = Instrument(symbol="TEST_OPT_CE", exchange="NFO")
    trade_info = {
        "instrument": inst,
        "side": OrderSide.BUY,
        "entry_price": 100.0,
        "target_price": 120.0,
        "sl_price": 90.0,
        "initial_sl": 90.0,
        "target_distance": 20.0,
        "peak_price": 100.0,
        "trough_price": 100.0,
        "breakeven_locked": False,
        "trailing_active": False,
        "quantity": 65,
        "lot_size": 65,
        "asset_key": "NIFTY"
    }
    strat.active_trades["NIFTY"] = trade_info

    # Test 1: Price reaches +25% (105.0) -> Not yet 30% breakeven
    strat.on_tick(Tick(token=1, symbol="TEST_OPT_CE", ltp=105.0))
    assert strat.active_trades["NIFTY"]["breakeven_locked"] is False, "Breakeven should not trigger at 25%"

    # Test 2: Price reaches +35% (107.0) -> Tier 1 Breakeven Triggered!
    strat.on_tick(Tick(token=1, symbol="TEST_OPT_CE", ltp=107.0))
    assert strat.active_trades["NIFTY"]["breakeven_locked"] is True, "Breakeven should be locked at +35%"
    assert strat.active_trades["NIFTY"]["sl_price"] == 100.80, "SL should be locked at Entry + 0.80"
    print(f"Tier 1 Passed: SL moved to ₹{strat.active_trades['NIFTY']['sl_price']} (Risk = ₹0)")

    # Test 3: Price reaches +55% (111.0) -> Tier 2 Profit Lock Triggered!
    strat.on_tick(Tick(token=1, symbol="TEST_OPT_CE", ltp=111.0))
    assert strat.active_trades["NIFTY"]["trailing_active"] is True, "Trailing should be active at +55%"
    assert strat.active_trades["NIFTY"]["sl_price"] >= 105.0, f"SL should lock in guaranteed profit (got {strat.active_trades['NIFTY']['sl_price']})"
    print(f"Tier 2 Passed: SL ratcheted to ₹{strat.active_trades['NIFTY']['sl_price']} (Guaranteed profit locked!)")

    # Test 4: Price reaches +75% (115.0), then pulls back to 111.5 (gives back 17.5% progress from peak)
    strat.on_tick(Tick(token=1, symbol="TEST_OPT_CE", ltp=115.0))
    # Pullback near level:
    strat.on_tick(Tick(token=1, symbol="TEST_OPT_CE", ltp=111.5))
    assert "NIFTY" not in strat.active_trades, "Tier 3: Trade should have exited on stall/reversal near target!"
    print("Tier 3 Passed: Successfully exited on momentum stall near target!")

    print("✅ All Active Trade Optimizer Tests Passed!\n")


def test_macro_bias_and_bounce_suppression():
    print("--- 3. Testing Macro Bias & Counter-Trend Bounce Suppression ---")
    mse = MarketStructureEngine(ema_period=5)
    mse.set_day_open("NIFTY", 23883.0)

    # Price at 23800 is 83 points below Day Open (Macro Bearish)
    for p in [23805, 23803, 23801, 23802, 23803]:
        mse.update_bar({"symbol": "NIFTY", "close": p, "high": p + 2, "low": p - 2, "volume": 10000, "day_open": 23883.0})

    macro = mse.get_macro_bias("NIFTY", 23803.0)
    assert macro == "BEARISH", f"Expected BEARISH macro bias, got {macro}"

    # Bounce attempt under EMA-20 while macro bias is BEARISH -> MUST BE BLOCKED
    allowed, reason = mse.validate_setup_alignment("BULLISH", "NIFTY", current_price=23798.0, is_bounce=True)
    assert allowed is False, "Counter-trend bounce should be blocked when below EMA in macro downtrend!"
    assert "BEARISH" in reason, f"Expected BEARISH reason, got: {reason}"
    print(f"Verified bounce blocked: {reason}")
    print("✅ Macro Bias Tests Passed!\n")


if __name__ == "__main__":
    test_market_structure()
    test_active_trade_optimizer()
    test_macro_bias_and_bounce_suppression()
    print("🎉 ALL TESTS PASSED SUCCESSFULLY!")
