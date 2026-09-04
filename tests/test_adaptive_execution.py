"""
Unit tests for Active AI Learning Execution Engine, Polarity Flipping, and S/R Recalculator.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock
from core.level_models import TradingLevel, LevelType, LevelAction
from core.trade_analytics import TradeLearningLedger, TradeTelemetry
from core.adaptive_tuner import AdaptiveExecutionOptimizer
from core.sr_calculator import calculate_pivot_levels, refresh_daily_sr_levels
from strategies.level_trader import LevelTraderStrategy
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager


def test_calculate_pivot_levels_math():
    """Verify mathematical accuracy of CPR, Camarilla, and Classical Pivots."""
    levels = calculate_pivot_levels(
        symbol="NIFTY",
        high=24200.0,
        low=23800.0,
        close=24000.0,
        prefix="test"
    )
    lvl_map = {l.id: l for l in levels}

    # CPR
    cpr = lvl_map["nifty_cpr_test"]
    assert cpr.price == 24000.0
    assert cpr.range_low == 24000.0
    assert cpr.range_high == 24000.0

    # PDH & PDL
    assert lvl_map["nifty_pdh_test"].price == 24200.0
    assert lvl_map["nifty_pdl_test"].price == 23800.0

    # R1 & S1
    # R1 = 2*P - Low = 48000 - 23800 = 24200.0
    # S1 = 2*P - High = 48000 - 24200 = 23800.0
    assert lvl_map["nifty_r1_test"].price == 24200.0
    assert lvl_map["nifty_s1_test"].price == 23800.0

    # Camarilla H4 & L4
    # diff = 400. H4 = 24000 + 400 * 1.1 / 2 = 24000 + 220 = 24220.0
    # L4 = 24000 - 220 = 23780.0
    assert lvl_map["nifty_cam_h4_test"].price == 24220.0
    assert lvl_map["nifty_cam_l4_test"].price == 23780.0


def test_adaptive_regime_blacklisting(tmp_path):
    """Verify that toxic time regimes with low win rate and negative PnL get blacklisted."""
    ledger = TradeLearningLedger(ledger_path=tmp_path / "test_ledger.json")

    # Log 3 losing trades during Midday Lull
    for i in range(3):
        t = TradeTelemetry(
            trade_id=f"LOSS_{i}",
            symbol="NIFTY_PE",
            asset_key="NIFTY",
            side="BUY",
            level_name="24000 Resistance",
            entry_price=100.0,
            exit_price=90.0,
            quantity=130,
            entry_time="2026-09-04T12:00:00",
            exit_time="2026-09-04T12:15:00",
            duration_seconds=900,
            time_bucket="Midday Lull (11:30 - 13:30)",
            mfe_price=102.0,
            mfe_points=2.0,
            mfe_pnl=260.0,
            mae_price=89.0,
            mae_points=11.0,
            mae_pnl=1430.0,
            gross_pnl=-1300.0,
            charges=70.0,
            net_pnl=-1370.0,
            efficiency_ratio=0.0,
            exit_reason="STOP_LOSS",
            autopsy_diagnosis="Chop"
        )
        ledger.record_trade(t)

    optimizer = AdaptiveExecutionOptimizer(ledger)
    is_blacklisted, reason = optimizer.is_regime_blacklisted("Midday Lull (11:30 - 13:30)")
    assert is_blacklisted is True
    assert "Blacklisted Regime: 'Midday Lull (11:30 - 13:30)'" in reason


def test_dynamic_volume_multiplier_elevation(tmp_path):
    """Verify that choppy levels elevate volume confirmation threshold to 1.5x."""
    ledger = TradeLearningLedger(ledger_path=tmp_path / "test_ledger.json")

    # Log 2 losing trades on specific level
    for i in range(2):
        t = TradeTelemetry(
            trade_id=f"CHOP_{i}",
            symbol="CRUDEOIL",
            asset_key="CRUDEOIL",
            side="BUY",
            level_name="Crude Choppy Resistance",
            entry_price=8600.0,
            exit_price=8570.0,
            quantity=40,
            entry_time="2026-09-04T18:00:00",
            exit_time="2026-09-04T18:10:00",
            duration_seconds=600,
            time_bucket="MCX US Open (18:00 - 20:00)",
            mfe_price=8605.0,
            mfe_points=5.0,
            mfe_pnl=200.0,
            mae_price=8568.0,
            mae_points=32.0,
            mae_pnl=1280.0,
            gross_pnl=-1200.0,
            charges=115.0,
            net_pnl=-1315.0,
            efficiency_ratio=0.0,
            exit_reason="STOP_LOSS",
            autopsy_diagnosis="Chop"
        )
        ledger.record_trade(t)

    optimizer = AdaptiveExecutionOptimizer(ledger)
    eff_mult = optimizer.get_effective_volume_multiplier("Crude Choppy Resistance", base_multiplier=1.3)
    assert eff_mult == 1.5


def test_strategy_polarity_flipping():
    """Verify that a Resistance level breached upwards dynamically flips to SUPPORT."""
    broker = PaperBroker(initial_capital=200000.0)
    rms = RiskManager(enforce_market_hours=False)

    test_level = TradingLevel(
        id="test_res",
        name="Test 8600 Resistance",
        price=8600.0,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        symbol="CRUDEOIL",
        volume_multiplier=1.3
    )

    strat = LevelTraderStrategy(
        broker=broker,
        risk_manager=rms,
        levels=[test_level],
        lots=1,
        enable_adaptive_learning=False
    )

    # Prime volume history
    strat.volume_histories["CRUDEOIL"].extend([5000, 5000, 5000])

    # Send bar crossing above 8600 with volume
    bar1 = {"symbol": "CRUDEOIL", "open": 8590.0, "high": 8595.0, "low": 8585.0, "close": 8595.0, "volume": 5000}
    bar2 = {"symbol": "CRUDEOIL", "open": 8596.0, "high": 8620.0, "low": 8595.0, "close": 8615.0, "volume": 10000}

    strat.on_bar(bar1)
    strat.on_bar(bar2)

    # Level must have flipped polarity to SUPPORT
    assert test_level.level_type == LevelType.SUPPORT.value
    # A position must have been entered
    assert "CRUDEOIL" in strat.active_trades
