"""
Multi-Strategy Trade Ledger & Analytics System.
Maintains persistent, dedicated trade ledgers for each strategy:
- ORION (15-Minute Opening Retest)
- LevelTrader (S/R Breakout & Bounce)
- ShortStraddle
- MomentumBuyer

Stores every trade with full execution metrics, anomalies, errors, and learning notes.
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, date
from typing import Dict, Any, List, Optional
import pandas as pd

from core.logger import get_logger

logger = get_logger("StrategyLedger")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = PROJECT_ROOT / "data" / "strategy_ledgers"
LEDGER_DIR.mkdir(parents=True, exist_ok=True)


class StrategyLedgerManager:
    """Manages separate JSON ledgers for each trading strategy."""

    @staticmethod
    def _get_ledger_path(strategy_name: str) -> Path:
        sanitized = strategy_name.lower().replace(" ", "_").replace("-", "_")
        return LEDGER_DIR / f"{sanitized}_ledger.json"

    @classmethod
    def load_trades(cls, strategy_name: str) -> List[Dict[str, Any]]:
        path = cls._get_ledger_path(strategy_name)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error loading ledger for {strategy_name}: {e}")
            return []

    @classmethod
    def record_trade(cls, strategy_name: str, trade_record: Dict[str, Any]) -> bool:
        """Appends a new trade record to the strategy's dedicated ledger."""
        path = cls._get_ledger_path(strategy_name)
        trades = cls.load_trades(strategy_name)

        # Assign unique trade ID if not provided
        if "trade_id" not in trade_record or not trade_record["trade_id"]:
            trade_record["trade_id"] = f"{strategy_name[:4].upper()}_{int(time.time())}"

        if "recorded_at" not in trade_record:
            trade_record["recorded_at"] = datetime.now().isoformat()

        trades.append(trade_record)

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(trades, f, indent=2, default=str)
            logger.info(f"📘 [LEDGER RECORDED] Trade {trade_record.get('trade_id')} saved to {path.name}")
            return True
        except Exception as e:
            logger.error(f"Error saving trade to {strategy_name} ledger: {e}")
            return False

    @classmethod
    def get_summary(cls, strategy_name: str) -> Dict[str, Any]:
        """Calculates key performance metrics for a strategy."""
        trades = cls.load_trades(strategy_name)
        if not trades:
            return {
                "strategy": strategy_name,
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "net_pnl": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_drawdown": 0.0
            }

        df = pd.DataFrame(trades)
        pnl_col = "net_pnl" if "net_pnl" in df.columns else "pnl"
        df[pnl_col] = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)

        total_trades = len(df)
        wins = df[df[pnl_col] > 0]
        losses = df[df[pnl_col] <= 0]

        win_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0
        tot_pnl = df[pnl_col].sum()
        avg_win = wins[pnl_col].mean() if len(wins) > 0 else 0.0
        avg_loss = losses[pnl_col].mean() if len(losses) > 0 else 0.0

        profit_factor = abs(wins[pnl_col].sum() / losses[pnl_col].sum()) if losses[pnl_col].sum() != 0 else (99.0 if wins[pnl_col].sum() > 0 else 0.0)

        # Drawdown
        cum_pnl = df[pnl_col].cumsum()
        peak = cum_pnl.cummax()
        dd = (peak - cum_pnl).max()

        return {
            "strategy": strategy_name,
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "net_pnl": round(tot_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown": round(dd, 2)
        }

    @classmethod
    def list_all_strategies(cls) -> List[str]:
        """Lists all strategies that have a ledger file."""
        return [f.stem.replace("_ledger", "") for f in LEDGER_DIR.glob("*_ledger.json")]


# Global instance
strategy_ledger = StrategyLedgerManager()
