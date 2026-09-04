"""
Hourly Strategy Monitor & Auto-Tuning Engine.
Monitors all executed paper trades, evaluates performance metrics (Win Rate, Profit Factor,
Realized RR), runs strategy tuning diagnostics, and delivers hourly Telegram reports.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import time
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import get_logger
from telegram_bridge.bot import TelegramBridge
from core.level_models import load_levels_config, save_levels_config
from config.settings import settings
from core.trade_analytics import TradeLearningLedger, DailyDebriefGenerator
from core.adaptive_tuner import AdaptiveExecutionOptimizer

logger = get_logger("HourlyMonitor")


def run_hourly_audit_and_tune(send_telegram: bool = True) -> Dict[str, Any]:
    """Audits current state, analyzes trade profitability, and generates tuning recommendations."""
    state_file = PROJECT_ROOT / "logs" / "paper_broker_state.json"
    if not state_file.exists():
        logger.warning("Paper broker state file not found.")
        return {}

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Error reading paper broker state: {e}")
        return {}

    initial_cap = state.get("initial_capital", 200000.0)
    avail_cash = state.get("available_cash", initial_cap)
    total_charges = state.get("total_charges", 0.0)
    positions = state.get("positions", {})
    orders = state.get("orders", {})
    trades = state.get("trades", [])

    # Calculate PnLs and charges
    total_realized = 0.0
    total_unrealized = 0.0
    total_gross = 0.0
    open_positions = []
    for sym, pos in positions.items():
        qty = pos.get("quantity", 0)
        real_pnl = pos.get("realized_pnl", 0.0)
        unreal_pnl = pos.get("unrealized_pnl", 0.0)
        charges = pos.get("charges", 0.0)
        gross_pnl = pos.get("gross_pnl", real_pnl + unreal_pnl + charges)
        total_realized += real_pnl
        total_unrealized += unreal_pnl
        total_gross += gross_pnl
        if qty != 0:
            open_positions.append({
                "symbol": sym,
                "quantity": qty,
                "avg_price": pos.get("average_buy_price" if qty > 0 else "average_sell_price", 0.0),
                "ltp": pos.get("ltp", 0.0),
                "unrealized_pnl": unreal_pnl
            })

    net_pnl = total_realized + total_unrealized
    if total_charges == 0.0 and len(positions) > 0:
        total_charges = sum(pos.get("charges", 0.0) for pos in positions.values())

    # Analyze trade history
    wins = []
    losses = []
    breakevens = []
    trailing_exits = []
    target_exits = []
    sl_exits = []

    for oid, o in orders.items():
        tag = o.get("tag", "")
        status = o.get("status", "")
        if "EXIT" in tag or "SELL" in tag or "AUTO_SQUARE" in tag:
            # Check corresponding trade
            avg_px = o.get("average_price", 0.0)
            if "TARGET_EXIT" in tag:
                target_exits.append(o)
            elif "TRAILING_STOP_EXIT" in tag or "TRAIL" in tag:
                trailing_exits.append(o)
            elif "BREAKEVEN_EXIT" in tag:
                breakevens.append(o)
            elif "SL_EXIT" in tag:
                sl_exits.append(o)

    # Asset breakdown
    nifty_trades = [t for t in trades if "NIFTY" in t.get("symbol", "").upper()]
    crude_trades = [t for t in trades if "CRUDE" in t.get("symbol", "").upper()]

    # Realized trade outcomes from closed positions
    closed_pos = [p for p in positions.values() if p.get("quantity", 0) == 0]
    for cp in closed_pos:
        rp = cp.get("realized_pnl", 0.0)
        if rp > 10.0:
            wins.append(rp)
        elif rp < -10.0:
            losses.append(abs(rp))
        else:
            breakevens.append(rp)

    total_closed_trades = len(wins) + len(losses) + len(breakevens)
    win_rate = (len(wins) / total_closed_trades * 100.0) if total_closed_trades > 0 else 0.0
    total_gain = sum(wins)
    total_loss = sum(losses)
    profit_factor = round(total_gain / total_loss, 2) if total_loss > 0 else (99.9 if total_gain > 0 else 1.0)
    avg_win = (total_gain / len(wins)) if wins else 0.0
    avg_loss = (total_loss / len(losses)) if losses else 0.0
    risk_reward_realized = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    # ------------------------------------------------------------------
    # Strategy Tuning Diagnostics & Automated Recommendation
    # ------------------------------------------------------------------
    tuning_notes = []
    health_status = "🟢 OPTIMAL"

    if total_closed_trades == 0 and len(open_positions) == 0:
        health_status = "💤 STANDBY (Awaiting Setups)"
        tuning_notes.append("No trades triggered in the past window. Levels and volume gates are standing by.")
    else:
        if total_closed_trades >= 3:
            if win_rate < 40.0:
                health_status = "⚠️ CAUTION - LOW WIN RATE"
                tuning_notes.append("Win rate below 40%. Recommend increasing breakout volume multiplier from 1.3x to 1.5x to eliminate false breakouts.")
            elif win_rate >= 60.0:
                health_status = "🚀 EXCELLENT PERFORMANCE"
                tuning_notes.append("Win rate >= 60% with favorable expectancy. Current 2:1 RR parameters performing strongly.")

            if len(sl_exits) > len(target_exits) + len(trailing_exits):
                tuning_notes.append("Stop-loss count elevated relative to target/trailing exits. Ensure candlestick body confirmation is respected.")
            elif len(trailing_exits) > 0:
                tuning_notes.append(f"Trailing SL active and protected {len(trailing_exits)} runner trade(s), securing profits before reversals.")

            if profit_factor >= 2.0:
                tuning_notes.append(f"Outstanding Profit Factor of {profit_factor:.2f}! Asymmetric 2:1 RR structure is compounding gains.")
            elif profit_factor < 1.0:
                health_status = "⚠️ DRAWDOWN DETECTED"
                tuning_notes.append(f"Profit factor {profit_factor:.2f} < 1.0. Tighter breakeven activation (+1.5%) recommended.")
        else:
            tuning_notes.append(f"Sample size small ({total_closed_trades} closed trades). S/R 2:1 RR baseline active.")

    # ------------------------------------------------------------------
    # Continuous Learning Ledger Excursion Telemetry
    # ------------------------------------------------------------------
    try:
        ledger = TradeLearningLedger()
        l_trades = ledger.get_trades_for_date(datetime.now().strftime("%Y-%m-%d"))
        if l_trades:
            optimizer = AdaptiveExecutionOptimizer(ledger)
            mfe_mae_nifty = optimizer.analyze_mfe_mae_calibration("NIFTY")
            for rec in mfe_mae_nifty.get("recommendations", []):
                tuning_notes.append(f"🧠 Excursion Insight: {rec}")
            mfe_mae_crude = optimizer.analyze_mfe_mae_calibration("CRUDEOIL")
            for rec in mfe_mae_crude.get("recommendations", []):
                tuning_notes.append(f"🛢️ Crude Excursion Insight: {rec}")
    except Exception as e:
        logger.debug(f"Learning ledger check in hourly monitor: {e}")

    # Guardrails check (Target +₹10,000 / SL -₹5,000)
    daily_target = getattr(settings, "MAX_DAILY_PROFIT", 10000.0)
    daily_sl = -getattr(settings, "MAX_DAILY_LOSS", 5000.0)
    dist_to_target = max(0.0, daily_target - net_pnl)
    dist_to_sl = max(0.0, net_pnl - daily_sl)

    report_time = datetime.now().strftime("%d-%b-%Y %H:%M:%S IST")
    pnl_icon = "🟢" if net_pnl >= 0 else "🔴"

    # Format Telegram Message
    msg = (
        f"📊 <b>Hourly Trading Bot & Strategy Health Report</b>\n"
        f"🕒 <code>{report_time}</code>\n\n"
        f"<b>Account Status:</b>\n"
        f"• Available Virtual Capital: <b>₹{avail_cash:,.2f}</b>\n"
        f"• Gross P&L: <b>₹{total_gross:+,.2f}</b>\n"
        f"• Total Charges & Taxes: <b>-₹{total_charges:,.2f}</b>\n"
        f"• Net P&L: {pnl_icon} <b>₹{net_pnl:+,.2f}</b> (Realized: ₹{total_realized:+,.2f} | Unrealized: ₹{total_unrealized:+,.2f})\n"
        f"• Open Positions: <b>{len(open_positions)} active</b>\n\n"
        f"<b>Trade Execution Metrics:</b>\n"
        f"• Closed Trades: <b>{total_closed_trades}</b> (Wins: {len(wins)} | Losses: {len(losses)} | Breakeven: {len(breakevens)})\n"
        f"• Win Rate: <b>{win_rate:.1f}%</b> | Profit Factor: <b>{profit_factor}</b>\n"
        f"• Realized R:R: <b>1:{risk_reward_realized}</b> (Avg Win: ₹{avg_win:,.1f} | Avg Loss: ₹{avg_loss:,.1f})\n"
        f"• Volume Breakouts: NIFTY ({len(nifty_trades)} fills) | CRUDE OIL ({len(crude_trades)} fills)\n\n"
        f"🛡️ <b>Daily Guardrail Status:</b>\n"
        f"• To Max Target (+₹{daily_target:,.0f}): <b>₹{dist_to_target:,.2f}</b> remaining\n"
        f"• To Max Stop Loss (-₹{abs(daily_sl):,.0f}): <b>₹{dist_to_sl:,.2f}</b> buffer\n\n"
        f"🧠 <b>Strategy Auto-Tuning Advisor:</b>\n"
        f"• Health: <b>{health_status}</b>\n"
    )

    for note in tuning_notes:
        msg += f"• <i>{note}</i>\n"

    print("\n" + "=" * 65)
    print(f"HOURLY STRATEGY MONITOR & TUNING REPORT - {report_time}")
    print(f"Capital: Rs.{avail_cash:,.2f} | Gross: Rs.{total_gross:+,.2f} | Charges: Rs.{total_charges:,.2f} | Net P&L: Rs.{net_pnl:+,.2f}")
    print(f"Win Rate: {win_rate:.1f}% | Profit Factor: {profit_factor} | Status: {health_status}")
    for n in tuning_notes:
        print(f"  * {n}")
    print("=" * 65 + "\n")

    if send_telegram:
        try:
            tg = TelegramBridge(runner=None)
            tg.send_notification(msg)
            logger.info("Hourly Telegram audit report dispatched successfully.")
        except Exception as e:
            logger.warning(f"Failed to send hourly report via Telegram: {e}")

    return {
        "timestamp": report_time,
        "capital": avail_cash,
        "gross_pnl": total_gross,
        "total_charges": total_charges,
        "net_pnl": net_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "health": health_status,
        "tuning_notes": tuning_notes
    }


def monitor_daemon():
    """Runs the hourly monitor continuously every 3600 seconds (1 hour)."""
    logger.info("Starting Hourly Strategy Monitor Daemon (interval = 1 hour)...")
    # Run first audit immediately on startup
    run_hourly_audit_and_tune(send_telegram=True)

    while True:
        try:
            time.sleep(3600)  # Sleep 1 hour
            run_hourly_audit_and_tune(send_telegram=True)
        except KeyboardInterrupt:
            logger.info("Hourly monitor daemon stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error in hourly monitor loop: {e}")
            time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hourly Strategy Monitor & Auto-Tuning Engine")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in the background every hour")
    parser.add_argument("--no-tg", action="store_true", help="Skip sending Telegram notification")
    args = parser.parse_args()

    if args.daemon:
        monitor_daemon()
    else:
        run_hourly_audit_and_tune(send_telegram=not args.no_tg)
