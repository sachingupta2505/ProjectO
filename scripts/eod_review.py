"""
Automated End-Of-Day (EOD) Strategy Review & Continuous Learning Script.
Performs forensic post-mortem on all trades executed today, evaluates MFE/MAE efficiency,
runs self-tuning parameter adjustments, saves an archival report, and broadcasts to Telegram.
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import get_logger
from core.trade_analytics import TradeLearningLedger, DailyDebriefGenerator
from core.adaptive_tuner import AdaptiveExecutionOptimizer
from telegram_bridge.bot import TelegramBridge
from config.settings import settings

logger = get_logger("EODReview")


def run_eod_review(target_date: str = None, send_telegram: bool = True) -> Dict[str, Any]:
    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    ledger = TradeLearningLedger()

    # Reconcile against broker tradebook and margins to ensure 100% sync
    broker_trades = None
    broker_margins = None
    try:
        if not settings.PAPER_TRADING and settings.BROKER == "angel":
            from brokers.angel_broker import AngelOneBroker
            broker = AngelOneBroker()
            if broker.authenticate():
                broker_trades = broker.get_trades()
                broker_margins = broker.get_margins()
        else:
            from brokers.paper_broker import PaperBroker
            broker = PaperBroker(initial_capital=settings.PAPER_INITIAL_CAPITAL, persist=True)
            if broker.authenticate():
                broker_trades = broker.get_trades()
                broker_margins = broker.get_margins()
    except Exception as e:
        logger.warning(f"Could not load broker for reconciliation: {e}")

    debrief = DailyDebriefGenerator.generate_debrief(
        ledger,
        target_date=date_str,
        broker_trades=broker_trades,
        broker_margins=broker_margins
    )

    # 1. Self-Tuning Recommendations
    optimizer = AdaptiveExecutionOptimizer(ledger)
    tod_recs = optimizer.get_time_of_day_recommendations()
    lvl_recs = optimizer.get_level_reliability_index()

    # 2. Compile Full EOD Package
    eod_package = {
        "date": date_str,
        "debrief": debrief,
        "time_of_day_recommendations": tod_recs,
        "level_adjustments": lvl_recs,
        "generated_at": datetime.now().isoformat()
    }

    # 3. Save to Archival Directory
    reports_dir = PROJECT_ROOT / "logs" / "eod_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / f"eod_report_{date_str}.json"
    report_file.write_text(json.dumps(eod_package, indent=2), encoding="utf-8")
    logger.info(f"💾 Saved EOD report to: {report_file}")

    # 4. Print clean summary to console
    print("\n" + "=" * 70)
    print(f"📊 END-OF-DAY PERFORMANCE & LEARNING DEBRIEF ({date_str})")
    print("=" * 70)
    print(f"• Total Trades: {debrief['total_trades']}")
    if debrief['total_trades'] > 0:
        print(f"• Win Rate: {debrief['win_rate']:.1f}%")
        print(f"• Gross P&L: ₹{debrief['gross_pnl']:+,.2f}")
        print(f"• Charges: -₹{debrief['charges']:,.2f}")
        print(f"• Net P&L: ₹{debrief['net_pnl']:+,.2f}")
        print("\n🧠 Actionable Learnings:")
        for t in debrief.get("takeaways", []):
            print(f"  {t}")

    # 5. Broadcast to Telegram
    if send_telegram and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        try:
            tg = TelegramBridge()
            tg.send_notification(debrief["report_text"])
            logger.info("📱 Dispatched EOD Debrief to Telegram channel.")
        except Exception as e:
            logger.warning(f"Could not send EOD report to Telegram: {e}")

    return eod_package


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run_eod_review(target_date=target, send_telegram=True)
