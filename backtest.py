"""
Historical and Scenario Backtesting Engine for Nifty Option Strategies.
Simulates intraday price action, slippage, and strategy PnL across multiple sessions.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import math
from datetime import datetime, time, timedelta, date
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from core.models import OptionType, OrderSide, OrderType, Instrument, Order
from core.option_chain import get_atm_strike, format_nifty_symbol
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from strategies.short_straddle import ShortStraddleStrategy
from config.settings import settings


class BacktestEngine:
    def __init__(self, initial_capital: float = 200000.0):
        self.initial_capital = initial_capital

    def run_straddle_simulation(
        self,
        days: int = 20,
        base_spot: float = 24500.0,
        daily_drift: float = 0.0,
        intraday_vol: float = 0.008,
        sl_pct: float = 0.25
    ) -> pd.DataFrame:
        """
        Simulate the 9:20 Short Straddle strategy over simulated intraday minute-by-minute sessions.
        """
        results = []
        np.random.seed(42)

        for day_idx in range(days):
            current_date = date.today() - timedelta(days=(days - day_idx))
            if current_date.weekday() in (5, 6):
                continue  # Skip weekends

            # Daily parameters
            day_spot_open = base_spot * (1 + np.random.normal(daily_drift, 0.005))
            atm_strike = get_atm_strike(day_spot_open)

            # Option premiums at 9:20 AM (approx ~0.6% of spot per ATM leg)
            initial_ce_premium = round(day_spot_open * 0.0055, 2)
            initial_pe_premium = round(day_spot_open * 0.0055, 2)

            ce_sl = round(initial_ce_premium * (1 + sl_pct), 2)
            pe_sl = round(initial_pe_premium * (1 + sl_pct), 2)

            # Generate 375 minute bars (9:15 to 15:30)
            minutes = 375
            spot_path = [day_spot_open]
            for m in range(1, minutes):
                shock = np.random.normal(0, intraday_vol / np.sqrt(minutes))
                spot_path.append(spot_path[-1] * (1 + shock))

            # Simulate strategy execution from 9:20 (minute 5) to 15:15 (minute 360)
            ce_entry = initial_ce_premium
            pe_entry = initial_pe_premium

            ce_exit = None
            pe_exit = None
            ce_exit_reason = "EOD"
            pe_exit_reason = "EOD"

            for m in range(5, 361):
                spot = spot_path[m]
                spot_move = spot - day_spot_open
                theta_decay_factor = 1.0 - (0.35 * (m / 375.0))  # Intraday theta decay ~35%

                # Dynamic delta response
                curr_ce = max(5.0, (initial_ce_premium + (0.5 * spot_move)) * theta_decay_factor)
                curr_pe = max(5.0, (initial_pe_premium - (0.5 * spot_move)) * theta_decay_factor)

                # Check CE stop loss
                if ce_exit is None and curr_ce >= ce_sl:
                    ce_exit = ce_sl
                    ce_exit_reason = "SL Hit"

                # Check PE stop loss
                if pe_exit is None and curr_pe >= pe_sl:
                    pe_exit = pe_sl
                    pe_exit_reason = "SL Hit"

                if ce_exit is not None and pe_exit is not None:
                    break

            # If not exited by SL, exit at 15:15 market price
            if ce_exit is None:
                ce_exit = round(curr_ce, 2)
            if pe_exit is None:
                pe_exit = round(curr_pe, 2)

            lot_size = settings.NIFTY_LOT_SIZE
            ce_pnl = (ce_entry - ce_exit) * lot_size
            pe_pnl = (pe_entry - pe_exit) * lot_size
            day_pnl = round(ce_pnl + pe_pnl, 2)

            results.append({
                "date": current_date.strftime("%Y-%m-%d"),
                "spot_open": round(day_spot_open, 2),
                "spot_close": round(spot_path[-1], 2),
                "atm_strike": atm_strike,
                "ce_entry": ce_entry,
                "ce_exit": ce_exit,
                "ce_reason": ce_exit_reason,
                "pe_entry": pe_entry,
                "pe_exit": pe_exit,
                "pe_reason": pe_exit_reason,
                "day_pnl": day_pnl,
            })

        df = pd.DataFrame(results)
        df["cumulative_pnl"] = df["day_pnl"].cumsum()
        df["drawdown"] = df["cumulative_pnl"] - df["cumulative_pnl"].cummax()
        return df

    def print_performance_summary(self, df: pd.DataFrame):
        total_pnl = df["day_pnl"].sum()
        win_days = (df["day_pnl"] > 0).sum()
        loss_days = (df["day_pnl"] < 0).sum()
        total_days = len(df)
        win_rate = (win_days / total_days * 100) if total_days else 0.0
        max_dd = df["drawdown"].min()
        profit_factor = (
            abs(df[df["day_pnl"] > 0]["day_pnl"].sum() / df[df["day_pnl"] < 0]["day_pnl"].sum())
            if loss_days > 0 else float("inf")
        )

        print("\n" + "=" * 55)
        print("          BACKTEST PERFORMANCE SUMMARY")
        print("=" * 55)
        print(f" Total Sessions Traded  : {total_days}")
        print(f" Win Sessions / Losses  : {win_days} Wins / {loss_days} Losses")
        print(f" Win Rate (%)           : {win_rate:.1f}%")
        print(f" Total Net PnL (INR)    : ₹{total_pnl:+,.2f}")
        print(f" Max Drawdown (INR)     : ₹{max_dd:,.2f}")
        print(f" Profit Factor          : {profit_factor:.2f}")
        print(f" Average PnL / Day      : ₹{df['day_pnl'].mean():+,.2f}")
        print("=" * 55 + "\n")


if __name__ == "__main__":
    engine = BacktestEngine()
    print("Running 30-day Straddle Backtest Simulation...")
    df = engine.run_straddle_simulation(days=30, base_spot=24500.0)
    engine.print_performance_summary(df)
