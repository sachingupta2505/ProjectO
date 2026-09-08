"""
Main Entry Point and Orchestrator for Dual-Asset Algorithmic Trading Bot.
Manages broker initialization, multi-asset strategy lifecycle (NIFTY 50 & CRUDE OIL),
real-time market data streaming, multi-session market timings, and strict daily RMS risk guardrails
(₹10,000 Maximum Profit Target & ₹10,000 Maximum Stop Loss).
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import argparse
import signal
import time
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any
import numpy as np

from config.settings import settings
from core.logger import get_logger
from core.models import Tick, Instrument
from core.risk_manager import RiskManager
from core.option_chain import get_atm_strike
from core.market_data import (
    get_live_nifty_spot, get_live_crude_spot, get_live_option_quote
)
from brokers.paper_broker import PaperBroker
from brokers.angel_broker import AngelOneBroker
from strategies.opening_retest_trader import OpeningRetestStrategy
from strategies.theta_decay_trader import ThetaDecayTraderStrategy
from dashboard.terminal_ui import render_dashboard
from telegram_bridge.bot import TelegramBridge
from rich.live import Live

logger = get_logger("Main")


class TradingBotRunner:
    def __init__(
        self,
        broker_type: str = "paper",
        strategy_type: str = "sr_trader",
        lots: int = 1,
        is_paper: bool = True,
        index_symbol: str = "NIFTY",
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None
    ):
        self.broker_type = broker_type.lower()
        self.strategy_type = strategy_type.lower()
        self.lots = lots
        self.is_paper = is_paper
        self.index_symbol = index_symbol.upper()
        self.running = False
        self._rms_halted = False
        self._last_bar_time = 0.0
        # True candle aggregation state (1-minute bars with genuine high/low tracking)
        self._nifty_bar_open: Optional[float] = None
        self._nifty_bar_high: float = -1e9
        self._nifty_bar_low: float = 1e9
        self._crude_bar_open: Optional[float] = None
        self._crude_bar_high: float = -1e9
        self._crude_bar_low: float = 1e9
        # 5-minute candle aggregation state for ORION Opening Retest
        self._5m_bar_open: Optional[float] = None
        self._5m_bar_high: float = -1e9
        self._5m_bar_low: float = 1e9
        self._last_5m_bar_minute: int = -1

        # Initialize Telegram Bridge for mobile phone interaction
        self.telegram = TelegramBridge(
            token=telegram_token,
            chat_id=telegram_chat_id,
            runner=self
        )

        # Initialize Risk Manager with strict ₹10,000 Daily Target & ₹10,000 Daily Stop Loss
        self.risk_manager = RiskManager(
            max_daily_loss=settings.MAX_DAILY_LOSS,
            max_daily_profit=settings.MAX_DAILY_PROFIT
        )

        # Initialize Broker
        if self.is_paper or self.broker_type == "paper":
            self.broker = PaperBroker(
                initial_capital=settings.PAPER_INITIAL_CAPITAL,
                slippage_pct=settings.SLIPPAGE_PCT,
                persist=True
            )
        elif self.broker_type == "angel":
            self.broker = AngelOneBroker()
        else:
            raise ValueError(f"Unsupported broker: {broker_type}")

        # Initialize Strategy (Core Duo Focus)
        if self.strategy_type in ["duo", "core_duo", "multi", "all"]:
            from core.multi_strategy_engine import MultiStrategyEngine
            self.multi_engine = MultiStrategyEngine(
                lots=self.lots,
                symbol=self.index_symbol,
                initial_capital_per_strat=100000.0,
                telegram=self.telegram,
                active_strategies=["orion", "theta"]
            )
            self.strategy = self.multi_engine.strategies["orion"]
        elif self.strategy_type in ["orion", "opening_retest", "retest"]:
            min_body = 120.0 if self.index_symbol == "SENSEX" else (80.0 if self.index_symbol == "BANKNIFTY" else 30.0)
            leeway = 25.0 if self.index_symbol == "SENSEX" else (15.0 if self.index_symbol == "BANKNIFTY" else 5.0)
            self.strategy = OpeningRetestStrategy(
                broker=self.broker,
                risk_manager=self.risk_manager,
                symbol=self.index_symbol,
                lots=self.lots,
                min_body_points=min_body,
                retest_leeway=leeway,
                telegram=self.telegram
            )
        elif self.strategy_type in ["theta", "theta_0dte"]:
            from strategies.theta_decay_trader import ThetaDecayTraderStrategy
            self.strategy = ThetaDecayTraderStrategy(
                broker=self.broker,
                risk_manager=self.risk_manager,
                lots=self.lots,
                symbol=self.index_symbol,
                telegram_notifier=self.telegram
            )
        else:
            raise ValueError(f"Unsupported strategy: {self.strategy_type}. Choose 'duo', 'orion', or 'theta'.")

        # Graceful shutdown handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.warning("\n⚠️ Shutdown signal received! Flattening positions and stopping...")
        self.running = False
        try:
            # Stop Angel One WebSocket feed
            from core.angel_feed import angel_feed
            angel_feed.stop()
        except Exception:
            pass
        try:
            self.telegram.send_notification("🛑 <b>Trading Bot Stopped</b> (Shutdown signal received).")
            self.telegram.stop()
            exit_orders = self.broker.square_off_all_positions()
            if exit_orders:
                logger.info(f"Closed {len(exit_orders)} positions during emergency shutdown.")
        except Exception as e:
            logger.error(f"Error during square-off: {e}")

        # Run EOD Review and learning analysis on shutdown
        try:
            from scripts.eod_review import run_eod_review
            run_eod_review(send_telegram=True)
        except Exception as e:
            logger.debug(f"EOD review on shutdown error: {e}")

        logger.info("👋 Trading Bot safely stopped.")
        sys.exit(0)

    def start(self, ui_mode: str = "headless", simulate_ticks: bool = False, initial_spot: float = 23950.0, listen_telegram: bool = True):
        logger.info("🚀 Initializing Multi-Asset Trading Engine (NIFTY 50 & CRUDE OIL)...")
        if not self.broker.authenticate():
            logger.error("Failed to authenticate with broker. Exiting.")
            return

        if hasattr(self, "multi_engine") and self.multi_engine:
            self.multi_engine.initialize()
        else:
            self.strategy.initialize()
        if listen_telegram:
            self.telegram.start()
        self.running = True

        # ── Start Angel One WebSocket price feed (real MCX LTP) ──────────────
        try:
            from core.angel_feed import angel_feed
            angel_feed.start()
            logger.info("✅ Angel One WebSocket price feed started (MCX Crude LTP).")
        except Exception as e:
            logger.warning(f"⚠️ Angel One WebSocket feed failed to start: {e}. Falling back to Yahoo Finance.")

        spot_price = initial_spot
        logger.info(f"✅ Bot started! Mode: {'PAPER' if self.is_paper else 'LIVE'} | Strategy: {self.strategy.name}")
        logger.info(f"🛡️ Daily Guardrails Active: Max Target: ₹{self.risk_manager.max_daily_profit:,.2f} | Max Loss: -₹{self.risk_manager.max_daily_loss:,.2f}")

        # Startup notification
        self.telegram.send_notification(
            f"🚀 <b>Trading Bot Active!</b>\n\n"
            f"• <b>Assets:</b> NIFTY 50 (NSE/NFO) & CRUDE OIL (MCX)\n"
            f"• <b>Strategy:</b> {self.strategy.name}\n"
            f"• <b>Mode:</b> {'PAPER' if self.is_paper else 'LIVE'}\n"
            f"• <b>Daily Target:</b> +₹{self.risk_manager.max_daily_profit:,.2f}\n"
            f"• <b>Daily Stop Loss:</b> -₹{self.risk_manager.max_daily_loss:,.2f}\n\n"
            f"🕒 Multi-Session Timings:\n"
            f"  - Nifty: 09:15 to 15:15 IST\n"
            f"  - Crude Oil: 09:00 to 23:15 IST"
        )

        if ui_mode == "terminal":
            self._run_with_terminal_ui(spot_price, simulate_ticks)
        else:
            self._run_headless(spot_price, simulate_ticks)

    def _run_with_terminal_ui(self, spot_price: float, simulate: bool):
        with Live(render_dashboard(self.broker, self.strategy, self.risk_manager, spot_price), refresh_per_second=2) as live:
            while self.running:
                spot_price = self._step_cycle(spot_price, simulate)
                live.update(render_dashboard(self.broker, self.strategy, self.risk_manager, spot_price))
                time.sleep(1)

    def _run_headless(self, spot_price: float, simulate: bool):
        while self.running:
            spot_price = self._step_cycle(spot_price, simulate)
            time.sleep(1)

    def _step_cycle(self, current_spot: float, simulate: bool) -> float:
        now = datetime.now()
        now_ts = time.time()

        # 1. Fetch live market data for NIFTY 50 and CRUDE OIL
        nifty_spot = current_spot
        crude_spot = 8570.0
        nifty_info: Dict[str, Any] = {}
        crude_info: Dict[str, Any] = {}

        if simulate:
            shock_n = np.random.normal(0, 0.0003)
            nifty_spot = round(nifty_spot * (1.0 + shock_n), 2)
            shock_c = np.random.normal(0, 0.0005)
            crude_spot = round(8570.0 * (1.0 + shock_c), 2)
        else:
            try:
                nifty_info = get_live_nifty_spot()
                if nifty_info and nifty_info.get("spot"):
                    nifty_spot = float(nifty_info["spot"])
            except Exception as e:
                logger.debug(f"Nifty spot query: {e}")

            try:
                crude_info = get_live_crude_spot()
                if crude_info and crude_info.get("spot"):
                    crude_spot = float(crude_info["spot"])
            except Exception as e:
                logger.debug(f"Crude spot query: {e}")

        # 2a. Directly update LTP for all open broker positions & commodity symbols from live feeds
        curr_crude_fut = f"CRUDEOIL_{datetime.now().strftime('%b').upper()}FUT"
        self.broker.set_ltp("CRUDEOIL", crude_spot)
        self.broker.set_ltp(curr_crude_fut, crude_spot)
        open_pos_map = self.broker.get_positions()
        for sym, pos in open_pos_map.items():
            if pos.quantity != 0:
                if "CRUDE" in sym.upper():
                    self.broker.set_ltp(sym, crude_spot)

        # 2b. Feed real-time ticks & 5-minute bars to active Core Duo strategies
        if True:
            # 1. Update spot tick to monitor active SL, Target 1 Breakeven, Target 2
            tick_obj = Tick(
                token=99926000,
                symbol=self.index_symbol,
                ltp=nifty_spot,
                timestamp=now
            )
            if hasattr(self, "multi_engine") and self.multi_engine:
                self.multi_engine.on_tick(tick_obj)
            else:
                self.strategy.on_tick(tick_obj)

            # 2. Accumulate real 5-minute candle
            if self._5m_bar_open is None:
                self._5m_bar_open = nifty_spot
            self._5m_bar_high = max(self._5m_bar_high, nifty_spot)
            self._5m_bar_low = min(self._5m_bar_low, nifty_spot)

            curr_min = now.minute
            # Close 5m bar at minutes 0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55
            if (curr_min % 5 == 0) and (curr_min != self._last_5m_bar_minute) and (now.second >= 1):
                self._last_5m_bar_minute = curr_min
                n_open = self._5m_bar_open or nifty_spot
                n_close = nifty_spot
                n_high = max(self._5m_bar_high, n_open, n_close)
                n_low = min(self._5m_bar_low, n_open, n_close)
                vol = float(nifty_info.get("volume", 50000)) if nifty_info else 50000

                bar_5m = {
                    "timestamp": now,
                    "open": n_open,
                    "high": n_high,
                    "low": n_low,
                    "close": n_close,
                    "volume": vol
                }
                if hasattr(self, "multi_engine") and self.multi_engine:
                    self.multi_engine.on_bar(bar_5m)
                else:
                    self.strategy.on_bar(bar_5m)

                # Reset accumulator
                self._5m_bar_open = nifty_spot
                self._5m_bar_high = nifty_spot
                self._5m_bar_low = nifty_spot

        # 4. Check Strategy Exits & Multi-Session Square-Off
        if hasattr(self.strategy, "check_exit_conditions"):
            self.strategy.check_exit_conditions(now)

        # 5. Evaluate RMS Daily Target (+₹10k) and Daily Stop Loss (-₹5k)
        margins = self.broker.get_margins()
        day_pnl = margins.get("daily_pnl", margins.get("total_pnl", 0.0))
        breached, reason = self.risk_manager.evaluate_daily_pnl(day_pnl)
        if breached and not self._rms_halted:
            self._rms_halted = True
            self.running = False
            self.broker.square_off_all_positions()
            if isinstance(self.strategy, LevelTraderStrategy):
                self.strategy.active_trades.clear()

            is_profit = day_pnl >= 0
            alert_header = "🎉 <b>DAILY PROFIT TARGET ACHIEVED!</b>" if is_profit else "🛑 <b>DAILY STOP-LOSS LIMIT HIT!</b>"
            self.telegram.send_notification(
                f"{alert_header}\n\n"
                f"• <b>Net Realized P&L:</b> <b>₹{day_pnl:+,.2f}</b>\n"
                f"• <b>Reason:</b> {reason}\n"
                f"• <b>Action:</b> All open positions squared off immediately.\n"
                f"• <b>Status:</b> Bot halted for today to lock in results."
            )
            logger.critical(f"🛑 BOT HALTED FOR THE DAY: {reason}")
            try:
                from scripts.eod_review import run_eod_review
                run_eod_review(send_telegram=True)
            except Exception as e:
                logger.debug(f"EOD review on RMS halt error: {e}")

        return nifty_spot


def main():
    parser = argparse.ArgumentParser(description="Multi-Asset Algorithmic Trading Bot (NIFTY 50 & CRUDE OIL)")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Execution mode: paper or live")
    parser.add_argument("--broker", choices=["paper", "angel"], default="paper", help="Broker adapter to use")
    parser.add_argument("--strategy", choices=["duo", "orion", "theta"], default="duo", help="Strategy to trade (default: duo [ORION-15 + THETA-0DTE])")
    parser.add_argument("--index", choices=["NIFTY", "FINNIFTY", "SENSEX", "BANKNIFTY"], default="NIFTY", help="Target index for opening retest (default: NIFTY)")
    parser.add_argument("--lots", type=int, default=getattr(settings, "DEFAULT_LOTS", 1), help="Number of lots to trade")
    parser.add_argument("--ui", choices=["terminal", "web", "headless"], default="headless", help="UI to display")
    parser.add_argument("--simulate", action="store_true", help="Simulate ticks for testing")
    parser.add_argument("--spot", type=float, default=23950.0, help="Initial Nifty spot price benchmark")
    parser.add_argument("--telegram-token", type=str, default=None, help="Telegram Bot Token")
    parser.add_argument("--telegram-chat-id", type=str, default=None, help="Authorized Telegram Chat ID")
    parser.add_argument("--no-telegram-listener", action="store_true", help="Do not start Telegram listener (use standalone bridge)")

    args = parser.parse_args()

    if args.ui == "web":
        import os
        logger.info("Starting Streamlit Web Dashboard...")
        app_path = os.path.join(os.path.dirname(__file__), "dashboard", "streamlit_app.py")
        subprocess.run(["streamlit", "run", app_path])
        return

    is_paper = (args.mode == "paper")
    runner = TradingBotRunner(
        broker_type=args.broker,
        strategy_type=args.strategy,
        lots=args.lots,
        is_paper=is_paper,
        index_symbol=args.index,
        telegram_token=args.telegram_token,
        telegram_chat_id=args.telegram_chat_id
    )
    runner.start(
        ui_mode=args.ui,
        simulate_ticks=args.simulate,
        initial_spot=args.spot,
        listen_telegram=not args.no_telegram_listener
    )


if __name__ == "__main__":
    main()
