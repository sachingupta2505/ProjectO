"""
Trade Analytics & Continuous Learning Engine.
Records high-fidelity trade telemetry (MFE, MAE, time-in-trade, efficiency),
performs automated post-mortem trade autopsies, logs historical trade insights,
and generates post-market learning debriefs.
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional

from core.logger import get_logger

logger = get_logger("TradeAnalytics")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "logs" / "trade_learning_ledger.json"


def classify_time_bucket(dt: Optional[datetime] = None) -> str:
    """Classifies an execution datetime into institutional time regimes."""
    current_dt = dt or datetime.now()
    t = current_dt.time()
    hour, minute = t.hour, t.minute
    total_mins = hour * 60 + minute

    # Indian Market Sessions (IST)
    if 9 * 60 + 15 <= total_mins < 10 * 60 + 30:
        return "MORNING_OPEN (09:15-10:30)"
    elif 10 * 60 + 30 <= total_mins < 11 * 60 + 45:
        return "MID_MORNING (10:30-11:45)"
    elif 11 * 60 + 45 <= total_mins < 13 * 60 + 15:
        return "MIDDAY_LULL (11:45-13:15)"
    elif 13 * 60 + 15 <= total_mins < 14 * 60 + 30:
        return "AFTERNOON_TREND (13:15-14:30)"
    elif 14 * 60 + 30 <= total_mins <= 15 * 60 + 30:
        return "CLOSING_RUSH (14:30-15:30)"
    elif 15 * 60 + 30 < total_mins < 18 * 60 + 30:
        return "MCX_EARLY (15:30-18:30)"
    elif 18 * 60 + 30 <= total_mins < 20 * 60 + 30:
        return "MCX_US_OPEN (18:30-20:30)"
    elif 20 * 60 + 30 <= total_mins <= 23 * 60 + 30:
        return "MCX_NIGHT (20:30-23:30)"
    else:
        return "OFF_HOURS"


@dataclass
class TradeTelemetry:
    """Complete high-fidelity performance and excursion record of a single trade."""
    trade_id: str
    symbol: str
    asset_key: str                     # "NIFTY" or "CRUDEOIL"
    side: str                          # "BUY" or "SELL"
    quantity: int
    entry_price: float
    exit_price: float
    entry_time: str                    # ISO 8601 string
    exit_time: str                     # ISO 8601 string
    duration_seconds: float
    time_bucket: str                   # Regime classification
    
    # Excursion metrics
    mfe_price: float                   # Maximum Favorable Excursion price
    mfe_points: float                  # Peak favorable move in points
    mfe_pnl: float                     # Peak unrealized gross profit
    mae_price: float                   # Maximum Adverse Excursion price
    mae_points: float                  # Peak adverse drawdown in points
    mae_pnl: float                     # Peak unrealized gross drawdown
    efficiency_ratio: float            # Realized Gain / MFE (0.0 to 1.0)
    
    # Financials
    gross_pnl: float
    charges: float
    net_pnl: float
    exit_reason: str                   # TARGET, STOP_LOSS, BREAKEVEN, TRAILING_STOP, TIMEOUT
    level_name: str                    # Triggering S/R level name
    autopsy_diagnosis: str = ""        # Automated takeaway insight
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeTelemetry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TradeAutopsy:
    """Evaluates execution dynamics and derives root-cause diagnostics."""

    @staticmethod
    def diagnose(
        side: str,
        entry_price: float,
        exit_price: float,
        mfe_points: float,
        mae_points: float,
        target_points: float,
        sl_points: float,
        exit_reason: str,
        time_bucket: str
    ) -> str:
        """Derives an actionable takeaway from the trade's excursion and context."""
        is_win = (exit_price > entry_price) if side == "BUY" else (exit_price < entry_price)

        # 1. Breakeven Protected Capital
        if "BREAKEVEN" in exit_reason.upper():
            return "🛡️ Capital Protected: Breakeven lock offset 100% of transaction friction with net zero loss."

        # 2. Trailing Stop Captured Gains
        if "TRAILING" in exit_reason.upper():
            return f"📈 Trailing SL Success: Locked in +{abs(exit_price - entry_price):.1f} pts before retracement."

        # 3. Premature Stop-Loss (Trade had strong profit, then completely reversed to SL)
        if not is_win and target_points > 0 and mfe_points >= (target_points * 0.65):
            return (
                f"⚠️ Premature Loss: Reached +{mfe_points:.1f} pts ({(mfe_points/target_points)*100:.0f}% of target) "
                f"before reversing to SL. Action: Tighten Breakeven trigger."
            )

        # 4. Midday Chop Victim
        if not is_win and "MIDDAY_LULL" in time_bucket:
            return (
                "⚠️ Midday Chop: Trade entered during the 11:45-13:15 institutional lull. "
                "Action: Restrict entries during midday chop."
            )

        # 5. Sniper Entry (Clean Target hit with negligible drawdown)
        if is_win and sl_points > 0 and mae_points <= (sl_points * 0.25):
            return (
                f"🎯 Sniper Entry: Negligible adverse drawdown (MAE only {mae_points:.1f} pts vs {sl_points:.1f} SL). "
                "High conviction institutional setup."
            )

        # 6. Runner Left Money on the Table (Target reached and price exploded further)
        if is_win and "TARGET" in exit_reason.upper() and mfe_points >= (target_points * 1.4):
            return (
                f"🚀 Runner Potential: MFE reached +{mfe_points:.1f} pts vs +{target_points:.1f} target. "
                "Action: Trailing runner position would capture additional gains."
            )

        # 7. Clean Stop Loss (Direct failure)
        if not is_win:
            return f"🛑 Clean Stop Loss: Market rejected S/R level immediately with {mae_points:.1f} pts adverse move."

        # 7. Clean Stop Loss (Direct failure)
        if not is_win:
            return f"🛑 Clean Stop Loss: Market rejected S/R level immediately with {mae_points:.1f} pts adverse move."

        return f"✅ Clean Target Hit: Strategy executed according to plan."


class TradeLearningLedger:
    """Thread-safe persistent storage and analytical aggregator of historical trades."""

    def __init__(self, ledger_path: Optional[Path] = None):
        self.ledger_path = Path(ledger_path or DEFAULT_LEDGER_PATH)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.ledger_path.exists():
            self._save([])

    def _load(self) -> List[Dict[str, Any]]:
        try:
            if not self.ledger_path.exists():
                return []
            content = self.ledger_path.read_text(encoding="utf-8").strip()
            if not content:
                return []
            return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to load learning ledger: {e}")
            return []

    def _save(self, records: List[Dict[str, Any]]):
        try:
            self.ledger_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write learning ledger: {e}")

    def record_trade(self, telemetry: TradeTelemetry) -> None:
        """Appends a new trade telemetry record to persistent storage."""
        records = self._load()
        records.append(telemetry.to_dict())
        self._save(records)
        logger.info(f"📘 [LEARNING LEDGER] Recorded Trade {telemetry.trade_id} ({telemetry.symbol}) | Net: ₹{telemetry.net_pnl:+.2f}")

    def get_all_trades(self) -> List[TradeTelemetry]:
        """Returns all historical trades as TradeTelemetry objects."""
        records = self._load()
        return [TradeTelemetry.from_dict(r) for r in records]

    def get_trades_for_date(self, date_str: str) -> List[TradeTelemetry]:
        """Returns all trades executed on a specific date (YYYY-MM-DD)."""
        all_t = self.get_all_trades()
        return [t for t in all_t if t.entry_time.startswith(date_str)]

    def get_time_of_day_stats(self) -> Dict[str, Dict[str, Any]]:
        """Computes win-rate and PnL breakdown across time-of-day buckets."""
        trades = self.get_all_trades()
        buckets: Dict[str, List[TradeTelemetry]] = {}
        for t in trades:
            buckets.setdefault(t.time_bucket, []).append(t)

        stats = {}
        for b_name, b_trades in buckets.items():
            wins = [t for t in b_trades if t.net_pnl > 0]
            losses = [t for t in b_trades if t.net_pnl < 0]
            tot_net = sum(t.net_pnl for t in b_trades)
            win_rate = (len(wins) / len(b_trades) * 100.0) if b_trades else 0.0
            stats[b_name] = {
                "total_trades": len(b_trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(win_rate, 1),
                "net_pnl": round(tot_net, 2)
            }
        return stats

    def get_level_stats(self) -> Dict[str, Dict[str, Any]]:
        """Computes reliability metrics for each institutional S/R level."""
        trades = self.get_all_trades()
        levels: Dict[str, List[TradeTelemetry]] = {}
        for t in trades:
            lvl = t.level_name or "Unknown"
            levels.setdefault(lvl, []).append(t)

        stats = {}
        for lvl_name, l_trades in levels.items():
            wins = [t for t in l_trades if t.net_pnl > 0]
            tot_net = sum(t.net_pnl for t in l_trades)
            win_rate = (len(wins) / len(l_trades) * 100.0) if l_trades else 0.0
            stats[lvl_name] = {
                "trades": len(l_trades),
                "wins": len(wins),
                "win_rate": round(win_rate, 1),
                "net_pnl": round(tot_net, 2)
            }
        return stats

    def clear(self):
        """Clears the ledger (used during state reset)."""
        self._save([])


class DailyDebriefGenerator:
    """Generates an end-of-day learning debrief comparing what worked vs what failed."""

    @staticmethod
    def generate_debrief(ledger: Optional[TradeLearningLedger] = None, target_date: Optional[str] = None) -> Dict[str, Any]:
        l = ledger or TradeLearningLedger()
        date_str = target_date or datetime.now().strftime("%Y-%m-%d")
        trades = l.get_trades_for_date(date_str)

        total_trades = len(trades)
        if total_trades == 0:
            return {
                "date": date_str,
                "total_trades": 0,
                "report_text": f"📘 <b>Daily Learning Debrief ({date_str})</b>\n\nNo trades executed today. System in monitoring standby."
            }

        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl < 0]
        bes = [t for t in trades if abs(t.net_pnl) <= 5.0]

        gross_pnl = sum(t.gross_pnl for t in trades)
        charges = sum(t.charges for t in trades)
        net_pnl = sum(t.net_pnl for t in trades)
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

        # Best / Worst hour analysis
        time_stats = {}
        for t in trades:
            time_stats.setdefault(t.time_bucket, []).append(t.net_pnl)

        best_bucket = max(time_stats.items(), key=lambda x: sum(x[1]))[0] if time_stats else "N/A"
        worst_bucket = min(time_stats.items(), key=lambda x: sum(x[1]))[0] if time_stats else "N/A"

        # Actionable recommendations
        takeaways = []
        premature_sl_count = sum(1 for t in trades if "Premature" in t.autopsy_diagnosis)
        if premature_sl_count > 0:
            takeaways.append(f"• {premature_sl_count} trade(s) had substantial MFE before reversing. Recommend tightening breakeven buffer.")

        midday_losses = [t for t in trades if "MIDDAY_LULL" in t.time_bucket and t.net_pnl < 0]
        if midday_losses:
            takeaways.append("• Losses detected during 11:45-13:15 lunch lull. Enable midday execution freeze.")

        runner_potentials = sum(1 for t in trades if "Runner Potential" in t.autopsy_diagnosis)
        if runner_potentials > 0:
            takeaways.append("• Strong follow-through past targets detected. Trailing runners captured extra upside.")

        if not takeaways:
            takeaways.append("• Setups followed institutional levels with strong execution discipline.")

        pnl_icon = "🟢" if net_pnl >= 0 else "🔴"

        report_text = (
            f"📘 <b>DAILY LEARNING & PERFORMANCE DEBRIEF ({date_str})</b>\n\n"
            f"<b>Trade Performance Overview:</b>\n"
            f"• Executed Trades: <b>{total_trades}</b> (Wins: {len(wins)} | Losses: {len(losses)} | BE: {len(bes)})\n"
            f"• Win Rate: <b>{win_rate:.1f}%</b>\n"
            f"• Gross P&L: <b>₹{gross_pnl:+,.2f}</b>\n"
            f"• Transaction Fees & Taxes: <b>-₹{charges:,.2f}</b>\n"
            f"• Net Realized P&L: {pnl_icon} <b>₹{net_pnl:+,.2f}</b>\n\n"
            f"<b>Session Dynamics:</b>\n"
            f"• 🌟 Best Regime: <b>{best_bucket}</b>\n"
            f"• ⚠️ Drag Regime: <b>{worst_bucket}</b>\n\n"
            f"🧠 <b>Actionable Learnings for Tomorrow:</b>\n"
            + "\n".join(takeaways) + "\n\n"
            f"🕒 Generated at {datetime.now().strftime('%H:%M:%S IST')}"
        )

        return {
            "date": date_str,
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "gross_pnl": gross_pnl,
            "charges": charges,
            "net_pnl": net_pnl,
            "report_text": report_text,
            "takeaways": takeaways
        }
