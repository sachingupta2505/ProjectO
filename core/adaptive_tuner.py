"""
Adaptive Execution Optimizer & Self-Tuning Engine.
Analyzes empirical trade telemetry from the learning ledger to dynamically
recommend or calibrate execution rules:
- Time-of-day filters (e.g., skip midday chop)
- S/R level reliability indices & adaptive volume thresholds
- Optimal breakeven & target sizing based on MFE / MAE distributions
"""

from typing import Dict, Any, List, Optional, Tuple
from core.trade_analytics import TradeLearningLedger, TradeTelemetry
from core.logger import get_logger

logger = get_logger("AdaptiveTuner")


class AdaptiveExecutionOptimizer:
    """Computes empirical optimizations from historical trade excursions."""

    def __init__(self, ledger: Optional[TradeLearningLedger] = None):
        self.ledger = ledger or TradeLearningLedger()

    def get_time_of_day_recommendations(self) -> Dict[str, Any]:
        """Identifies toxic chop windows and high-probability trading regimes."""
        time_stats = self.ledger.get_time_of_day_stats()
        blacklisted_regimes = []
        golden_regimes = []

        for regime, data in time_stats.items():
            total = data["total_trades"]
            wr = data["win_rate"]
            pnl = data["net_pnl"]

            if total >= 3 and wr < 35.0 and pnl < 0:
                blacklisted_regimes.append({
                    "regime": regime,
                    "win_rate": wr,
                    "net_pnl": pnl,
                    "recommendation": "Restrict new trade entries during this session"
                })
            elif total >= 3 and wr >= 60.0 and pnl > 0:
                golden_regimes.append({
                    "regime": regime,
                    "win_rate": wr,
                    "net_pnl": pnl,
                    "recommendation": "Optimal high-conviction window"
                })

        return {
            "blacklisted_regimes": blacklisted_regimes,
            "golden_regimes": golden_regimes,
            "all_stats": time_stats
        }

    def get_level_reliability_index(self) -> Dict[str, Any]:
        """Calculates reliability score and recommends volume multipliers per level."""
        level_stats = self.ledger.get_level_stats()
        adjustments = {}

        for lvl_name, data in level_stats.items():
            total = data["trades"]
            wr = data["win_rate"]

            if total >= 2 and wr < 40.0:
                adjustments[lvl_name] = {
                    "win_rate": wr,
                    "status": "CHOPPY / FREQUENT FAKEOUTS",
                    "recommended_volume_multiplier": 1.5,  # Require heavier institutional confirmation
                    "action": "Increase breakout volume threshold to 1.5x"
                }
            elif total >= 2 and wr >= 65.0:
                adjustments[lvl_name] = {
                    "win_rate": wr,
                    "status": "HIGH INTEGRITY LEVEL",
                    "recommended_volume_multiplier": 1.3,
                    "action": "Maintain standard 1.3x confirmation"
                }
            else:
                adjustments[lvl_name] = {
                    "win_rate": wr,
                    "status": "NORMAL",
                    "recommended_volume_multiplier": 1.3,
                    "action": "Standard parameters"
                }

        return adjustments

    def analyze_mfe_mae_calibration(self, asset_key: str = "NIFTY") -> Dict[str, Any]:
        """Calibrates optimal targets, stop losses, and breakeven triggers from MFE/MAE."""
        all_trades = [t for t in self.ledger.get_all_trades() if t.asset_key.upper() == asset_key.upper()]
        if not all_trades:
            return {
                "asset_key": asset_key,
                "sample_size": 0,
                "notes": "Insufficient trade data for empirical calibration"
            }

        wins = [t for t in all_trades if t.net_pnl > 0]
        losses = [t for t in all_trades if t.net_pnl < 0]

        avg_win_mfe = sum(t.mfe_points for t in wins) / len(wins) if wins else 0.0
        avg_loss_mfe = sum(t.mfe_points for t in losses) / len(losses) if losses else 0.0
        avg_win_mae = sum(t.mae_points for t in wins) / len(wins) if wins else 0.0
        avg_loss_mae = sum(t.mae_points for t in losses) / len(losses) if losses else 0.0

        recommendations = []
        # If losing trades reached significant profit before failing
        if losses and avg_loss_mfe > 0:
            if asset_key == "NIFTY" and avg_loss_mfe >= 12.0:
                recommendations.append(
                    f"Losing Nifty trades reached avg MFE of +{avg_loss_mfe:.1f} pts before reversing. "
                    "Tighten breakeven lock trigger from +2.0% to +1.5%."
                )
            elif asset_key == "CRUDEOIL" and avg_loss_mfe >= 35.0:
                recommendations.append(
                    f"Losing Crude trades reached avg MFE of +{avg_loss_mfe:.1f} pts before reversing. "
                    "Tighten breakeven trigger from +20 pts to +15 pts."
                )

        # If winning trades experienced very low drawdown
        if wins and avg_win_mae > 0:
            recommendations.append(
                f"Winning trades suffered avg drawdown (MAE) of only {avg_win_mae:.1f} pts. "
                "Sniper entries confirm institutional S/R integrity."
            )

        return {
            "asset_key": asset_key,
            "sample_size": len(all_trades),
            "avg_win_mfe": round(avg_win_mfe, 2),
            "avg_loss_mfe": round(avg_loss_mfe, 2),
            "avg_win_mae": round(avg_win_mae, 2),
            "avg_loss_mae": round(avg_loss_mae, 2),
            "recommendations": recommendations
        }

    def is_regime_blacklisted(self, time_bucket: Optional[str] = None) -> Tuple[bool, str]:
        """
        Determines if the specified or current time bucket should be restricted from new entries.
        Returns (is_blacklisted, reason_string).
        """
        from datetime import datetime
        from core.trade_analytics import classify_time_bucket

        regime = time_bucket or classify_time_bucket(datetime.now())
        recommendations = self.get_time_of_day_recommendations()

        for b in recommendations.get("blacklisted_regimes", []):
            if b["regime"] == regime:
                return True, f"Blacklisted Regime: '{regime}' (Hist Win Rate: {b['win_rate']:.1f}%, Net PnL: ₹{b['net_pnl']:+,.2f})"

        return False, ""

    def get_effective_volume_multiplier(self, level_name: str, base_multiplier: float = 1.3) -> float:
        """
        Returns dynamic volume multiplier for a specific level based on past breakout integrity.
        Raises to 1.5x on false-breakout prone levels.
        """
        index = self.get_level_reliability_index()
        lvl_data = index.get(level_name)
        if lvl_data:
            return float(lvl_data.get("recommended_volume_multiplier", base_multiplier))
        return base_multiplier

    def get_calibrated_breakeven(
        self, asset_key: str, default_be_pct: float = 0.02, default_be_pts: float = 20.0
    ) -> Tuple[float, float]:
        """
        Dynamically adjusts breakeven thresholds based on historical MFE of losing trades.
        Returns (calibrated_be_pct, calibrated_be_pts).
        """
        cal = self.analyze_mfe_mae_calibration(asset_key)
        avg_loss_mfe = cal.get("avg_loss_mfe", 0.0)

        cal_pct = default_be_pct
        cal_pts = default_be_pts

        if asset_key.upper() == "NIFTY" and avg_loss_mfe >= 12.0:
            cal_pct = 0.015  # Tighten from 2.0% to 1.5%
        elif asset_key.upper() == "CRUDEOIL" and avg_loss_mfe >= 35.0:
            cal_pts = 15.0   # Tighten from 20 pts to 15 pts

        return cal_pct, cal_pts

    def get_active_adaptations_summary(self) -> Dict[str, Any]:
        """
        Compiles a summary of all active AI execution adaptations currently enforced.
        """
        time_rec = self.get_time_of_day_recommendations()
        level_rec = self.get_level_reliability_index()
        nifty_cal = self.analyze_mfe_mae_calibration("NIFTY")
        crude_cal = self.analyze_mfe_mae_calibration("CRUDEOIL")

        blacklisted = [b["regime"] for b in time_rec.get("blacklisted_regimes", [])]
        golden = [g["regime"] for g in time_rec.get("golden_regimes", [])]
        elevated_levels = {
            k: v["recommended_volume_multiplier"]
            for k, v in level_rec.items()
            if v.get("recommended_volume_multiplier", 1.3) > 1.3
        }

        n_pct, _ = self.get_calibrated_breakeven("NIFTY", 0.02, 20.0)
        _, c_pts = self.get_calibrated_breakeven("CRUDEOIL", 0.02, 20.0)

        return {
            "blacklisted_regimes": blacklisted,
            "golden_regimes": golden,
            "elevated_levels": elevated_levels,
            "nifty_be_pct": n_pct,
            "crude_be_pts": c_pts,
            "total_trades_analyzed": len(self.ledger.get_all_trades())
        }

