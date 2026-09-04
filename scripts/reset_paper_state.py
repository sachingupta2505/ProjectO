"""
Maintenance Script: Archive existing trade history and reset paper broker state & logs.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import shutil
import json
from pathlib import Path
from datetime import datetime

def reset_paper_trading():
    project_root = Path(__file__).resolve().parent.parent
    logs_dir = project_root / "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = logs_dir / f"archive_{timestamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1. Archive files if they exist
    for filename in ["paper_broker_state.json", "trading_bot.log", "telegram_chat.jsonl", "trade_learning_ledger.json"]:
        f_path = logs_dir / filename
        if f_path.exists():
            shutil.copy2(f_path, archive_dir / filename)
            print(f"Archived {filename} -> {archive_dir.name}")

    # 2. Reset paper broker state to ₹200,000.00
    fresh_state = {
        "initial_capital": 200000.0,
        "available_cash": 200000.0,
        "positions": {},
        "orders": {},
        "trades": []
    }
    state_file = logs_dir / "paper_broker_state.json"
    state_file.write_text(json.dumps(fresh_state, indent=2), encoding="utf-8")
    print(f"Reset {state_file.name} to fresh ₹200,000.00 capital with 0 positions and 0 orders.")

    # 3. Clean log files & learning ledger
    bot_log = logs_dir / "trading_bot.log"
    bot_log.write_text(f"[{datetime.now().isoformat()}] INFO Bot logs reset cleanly. Fresh session initialized.\n", encoding="utf-8")
    
    tg_chat = logs_dir / "telegram_chat.jsonl"
    tg_chat.write_text("", encoding="utf-8")

    ledger_file = logs_dir / "trade_learning_ledger.json"
    ledger_file.write_text("[]", encoding="utf-8")
    print("Reset trading_bot.log, telegram_chat.jsonl, and trade_learning_ledger.json.")

    print("\n✅ Reset completed successfully! Ready to start afresh.")

if __name__ == "__main__":
    reset_paper_trading()
