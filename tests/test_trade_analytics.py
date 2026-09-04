"""Unit and integration tests for Continuous Learning Engine & Trade Analytics."""

import pytest
import json
from pathlib import Path
from core.trade_analytics import (
    TradeTelemetry, TradeAutopsy, TradeLearningLedger, DailyDebriefGenerator, classify_time_bucket
)
from core.adaptive_tuner import AdaptiveExecutionOptimizer


def test_classify_time_bucket():
    from datetime import datetime, time
    d1 = datetime(2026, 9, 4, 9, 30, 0)
    assert "MORNING_OPEN" in classify_time_bucket(d1)

    d2 = datetime(2026, 9, 4, 12, 30, 0)
    assert "MIDDAY_LULL" in classify_time_bucket(d2)

    d3 = datetime(2026, 9, 4, 19, 30, 0)
    assert "MCX_US_OPEN" in classify_time_bucket(d3)


def test_trade_autopsy_diagnostics():
    # 1. Premature Stop-Loss (Reached 18 pts out of 20 pt target, then reversed to SL)
    diag1 = TradeAutopsy.diagnose(
        side="BUY",
        entry_price=100.0,
        exit_price=90.0,
        mfe_points=18.0,
        mae_points=10.0,
        target_points=20.0,
        sl_points=10.0,
        exit_reason="STOP_LOSS",
        time_bucket="MORNING_OPEN"
    )
    assert "Premature Loss" in diag1
    assert "Tighten Breakeven trigger" in diag1

    # 2. Sniper Entry (Target reached with only 1.5 pt adverse move vs 10 pt SL)
    diag2 = TradeAutopsy.diagnose(
        side="BUY",
        entry_price=100.0,
        exit_price=120.0,
        mfe_points=20.0,
        mae_points=1.5,
        target_points=20.0,
        sl_points=10.0,
        exit_reason="TARGET",
        time_bucket="MORNING_OPEN"
    )
    assert "Sniper Entry" in diag2

    # 3. Midday Chop Victim
    diag3 = TradeAutopsy.diagnose(
        side="BUY",
        entry_price=100.0,
        exit_price=90.0,
        mfe_points=4.0,
        mae_points=10.0,
        target_points=20.0,
        sl_points=10.0,
        exit_reason="STOP_LOSS",
        time_bucket="MIDDAY_LULL (11:45-13:15)"
    )
    assert "Midday Chop" in diag3

    # 4. Breakeven Protected Capital
    diag4 = TradeAutopsy.diagnose(
        side="BUY",
        entry_price=100.0,
        exit_price=100.8,
        mfe_points=12.0,
        mae_points=0.0,
        target_points=20.0,
        sl_points=10.0,
        exit_reason="BREAKEVEN_EXIT",
        time_bucket="MORNING_OPEN"
    )
    assert "Capital Protected" in diag4


def test_learning_ledger_persistence(tmp_path):
    ledger_file = tmp_path / "test_ledger.json"
    ledger = TradeLearningLedger(ledger_path=ledger_file)

    t1 = TradeTelemetry(
        trade_id="TRD_001",
        symbol="NIFTY2690824000CE",
        asset_key="NIFTY",
        side="BUY",
        quantity=130,
        entry_price=100.0,
        exit_price=120.0,
        entry_time="2026-09-04T09:30:00",
        exit_time="2026-09-04T09:45:00",
        duration_seconds=900.0,
        time_bucket="MORNING_OPEN (09:15-10:30)",
        mfe_price=122.0,
        mfe_points=22.0,
        mfe_pnl=2860.0,
        mae_price=98.0,
        mae_points=2.0,
        mae_pnl=260.0,
        efficiency_ratio=0.91,
        gross_pnl=2600.0,
        charges=70.0,
        net_pnl=2530.0,
        exit_reason="TARGET",
        level_name="24000 Resistance",
        autopsy_diagnosis="Sniper Entry"
    )

    ledger.record_trade(t1)

    all_t = ledger.get_all_trades()
    assert len(all_t) == 1
    assert all_t[0].trade_id == "TRD_001"
    assert all_t[0].net_pnl == 2530.0
    assert all_t[0].mfe_points == 22.0

    # Level stats
    lvl_stats = ledger.get_level_stats()
    assert "24000 Resistance" in lvl_stats
    assert lvl_stats["24000 Resistance"]["win_rate"] == 100.0

    # Time of day stats
    tod_stats = ledger.get_time_of_day_stats()
    assert "MORNING_OPEN (09:15-10:30)" in tod_stats
    assert tod_stats["MORNING_OPEN (09:15-10:30)"]["wins"] == 1


def test_adaptive_execution_optimizer(tmp_path):
    ledger_file = tmp_path / "test_optimizer_ledger.json"
    ledger = TradeLearningLedger(ledger_path=ledger_file)

    # Add 3 losses in Midday Lull
    for i in range(3):
        ledger.record_trade(TradeTelemetry(
            trade_id=f"LOSS_{i}",
            symbol="NIFTY",
            asset_key="NIFTY",
            side="BUY",
            quantity=130,
            entry_price=100.0,
            exit_price=90.0,
            entry_time=f"2026-09-04T12:0{i}:00",
            exit_time=f"2026-09-04T12:1{i}:00",
            duration_seconds=600.0,
            time_bucket="MIDDAY_LULL (11:45-13:15)",
            mfe_price=103.0,
            mfe_points=3.0,
            mfe_pnl=390.0,
            mae_price=90.0,
            mae_points=10.0,
            mae_pnl=1300.0,
            efficiency_ratio=0.0,
            gross_pnl=-1300.0,
            charges=70.0,
            net_pnl=-1370.0,
            exit_reason="STOP_LOSS",
            level_name="23800 Support",
            autopsy_diagnosis="Midday Chop"
        ))

    optimizer = AdaptiveExecutionOptimizer(ledger)
    reg_rec = optimizer.get_time_of_day_recommendations()

    # Verify toxic regime detected
    assert len(reg_rec["blacklisted_regimes"]) == 1
    assert "MIDDAY_LULL" in reg_rec["blacklisted_regimes"][0]["regime"]
    assert reg_rec["blacklisted_regimes"][0]["win_rate"] == 0.0

    # Level reliability check (23800 Support had 3 losses -> choppy)
    lvl_rec = optimizer.get_level_reliability_index()
    assert "23800 Support" in lvl_rec
    assert lvl_rec["23800 Support"]["recommended_volume_multiplier"] == 1.5


def test_daily_debrief_generator(tmp_path):
    ledger_file = tmp_path / "test_debrief_ledger.json"
    ledger = TradeLearningLedger(ledger_path=ledger_file)

    debrief_empty = DailyDebriefGenerator.generate_debrief(ledger, target_date="2026-09-04")
    assert debrief_empty["total_trades"] == 0
    assert "No trades executed" in debrief_empty["report_text"]

    # Record a trade
    ledger.record_trade(TradeTelemetry(
        trade_id="TRD_W1",
        symbol="NIFTY",
        asset_key="NIFTY",
        side="BUY",
        quantity=130,
        entry_price=100.0,
        exit_price=120.0,
        entry_time="2026-09-04T09:30:00",
        exit_time="2026-09-04T09:45:00",
        duration_seconds=900.0,
        time_bucket="MORNING_OPEN (09:15-10:30)",
        mfe_price=122.0,
        mfe_points=22.0,
        mfe_pnl=2860.0,
        mae_price=98.0,
        mae_points=2.0,
        mae_pnl=260.0,
        efficiency_ratio=0.91,
        gross_pnl=2600.0,
        charges=70.0,
        net_pnl=2530.0,
        exit_reason="TARGET",
        level_name="24000 Resistance",
        autopsy_diagnosis="Sniper Entry"
    ))

    debrief_filled = DailyDebriefGenerator.generate_debrief(ledger, target_date="2026-09-04")
    assert debrief_filled["total_trades"] == 1
    assert debrief_filled["win_rate"] == 100.0
    assert debrief_filled["net_pnl"] == 2530.0
    assert "DAILY LEARNING & PERFORMANCE DEBRIEF" in debrief_filled["report_text"]
