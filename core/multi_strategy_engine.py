"""
Multi-Strategy Parallel Execution Orchestrator.
Runs 4 institutional trading strategies concurrently in Paper Mode,
each with dedicated virtual capital (₹1,00,000 each), isolated broker accounts,
independent Risk Managers, and separate strategy ledgers.
"""

from datetime import datetime
from typing import Dict, Any, List
import pandas as pd

from core.logger import get_logger
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from core.models import Tick
from strategies.opening_retest_trader import OpeningRetestStrategy
from strategies.cpr_trader import CPRTraderStrategy
from strategies.ict_sweep_trader import ICTSweepTraderStrategy
from strategies.theta_decay_trader import ThetaDecayTraderStrategy

logger = get_logger("MultiStrategyEngine")


class MultiStrategyEngine:
    """
    Orchestrates concurrent execution of all 4 algorithmic trading strategies:
    1. ORION-15 (Opening Retest)
    2. CPR-Institutional (Central Pivot Range Engine)
    3. ICT-Liquidity (PDH/PDL Sweep & FVG)
    4. THETA-0DTE (Expiry Day Afternoon Strangle)
    """

    def __init__(self, lots: int = 2, symbol: str = "NIFTY", initial_capital_per_strat: float = 100000.0, telegram=None):
        self.lots = lots
        self.symbol = symbol
        self.initial_capital_per_strat = initial_capital_per_strat
        self.telegram = telegram

        # 1. Instantiate Isolated Paper Brokers (₹1,00,000 each)
        self.brokers: Dict[str, PaperBroker] = {
            "orion": PaperBroker(account_name="orion", initial_capital=initial_capital_per_strat, persist=True),
            "cpr": PaperBroker(account_name="cpr", initial_capital=initial_capital_per_strat, persist=True),
            "ict": PaperBroker(account_name="ict", initial_capital=initial_capital_per_strat, persist=True),
            "theta": PaperBroker(account_name="theta", initial_capital=initial_capital_per_strat, persist=True),
        }

        # 2. Instantiate Decoupled Risk Managers
        self.risk_managers: Dict[str, RiskManager] = {
            "orion": RiskManager(),
            "cpr": RiskManager(),
            "ict": RiskManager(),
            "theta": RiskManager(),
        }

        # 3. Instantiate Strategies
        self.strategies = {
            "orion": OpeningRetestStrategy(
                broker=self.brokers["orion"],
                risk_manager=self.risk_managers["orion"],
                lots=self.lots,
                symbol=self.symbol,
                telegram=self.telegram
            ),
            "cpr": CPRTraderStrategy(
                broker=self.brokers["cpr"],
                risk_manager=self.risk_managers["cpr"],
                lots=self.lots,
                symbol=self.symbol,
                telegram_notifier=self.telegram
            ),
            "ict": ICTSweepTraderStrategy(
                broker=self.brokers["ict"],
                risk_manager=self.risk_managers["ict"],
                lots=self.lots,
                symbol=self.symbol,
                telegram_notifier=self.telegram
            ),
            "theta": ThetaDecayTraderStrategy(
                broker=self.brokers["theta"],
                risk_manager=self.risk_managers["theta"],
                lots=self.lots,
                symbol=self.symbol,
                telegram_notifier=self.telegram
            )
        }

    def initialize(self):
        """Initializes all brokers and strategies."""
        logger.info(f"🚀 Initializing Multi-Strategy Engine ({len(self.strategies)} parallel strategies)...")
        for key, broker in self.brokers.items():
            broker.authenticate()

        for key, strat in self.strategies.items():
            strat.initialize()

        logger.info(f"✅ All 4 Strategies Armed & Ready: Total Capital: ₹{len(self.brokers)*self.initial_capital_per_strat:,.2f}")

    def on_tick(self, tick: Tick):
        """Broadcasts real-time tick to all active brokers and strategies."""
        # Update LTP on all paper brokers
        for broker in self.brokers.values():
            broker.set_ltp(tick.symbol, tick.ltp)

        # Feed tick to each strategy
        for strat in self.strategies.values():
            try:
                strat.on_tick(tick)
            except Exception as e:
                logger.error(f"Error in {strat.name} on_tick: {e}")

    def on_bar(self, bar: Dict[str, Any]):
        """Broadcasts completed 5-minute candle to all active strategies."""
        for strat in self.strategies.values():
            try:
                strat.on_bar(bar)
            except Exception as e:
                logger.error(f"Error in {strat.name} on_bar: {e}")

    def get_portfolio_status(self) -> Dict[str, Any]:
        """Calculates combined portfolio metrics and individual strategy stats."""
        tot_capital = sum(b.initial_capital for b in self.brokers.values())
        avail_cash = sum(b.available_cash for b in self.brokers.values())
        tot_pnl = sum(b.get_margins()["total_pnl"] for b in self.brokers.values())
        tot_charges = sum(b.get_margins().get("total_charges", 0.0) for b in self.brokers.values())

        strat_statuses = {}
        for key, strat in self.strategies.items():
            strat_statuses[key] = strat.get_status()

        return {
            "total_capital": tot_capital,
            "available_cash": avail_cash,
            "total_pnl": tot_pnl,
            "total_charges": tot_charges,
            "strategies": strat_statuses
        }
