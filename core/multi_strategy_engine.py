"""
Multi-Strategy Parallel Execution Orchestrator.
Runs 4 institutional trading strategies concurrently in Paper Mode,
each with dedicated virtual capital (₹1,00,000 each), isolated broker accounts,
independent Risk Managers, and separate strategy ledgers.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional

from core.logger import get_logger
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from core.models import Tick
from strategies.opening_retest_trader import OpeningRetestStrategy
from strategies.theta_decay_trader import ThetaDecayTraderStrategy

logger = get_logger("CoreDuoEngine")


class MultiStrategyEngine:
    """
    Core Duo Orchestrator:
    Concurrently executes the two highest-probability, proven strategies:
    1. ORION-15 (Opening 15m Retest Momentum Engine)
    2. THETA-0DTE (Afternoon Weekly Expiry Strangle Decay Engine)
    """

    def __init__(
        self,
        lots: int = 2,
        symbol: str = "NIFTY",
        initial_capital_per_strat: float = 100000.0,
        telegram=None,
        active_strategies: Optional[List[str]] = None
    ):
        self.lots = lots
        self.symbol = symbol
        self.initial_capital_per_strat = initial_capital_per_strat
        self.telegram = telegram
        self.active_keys = [k.lower() for k in active_strategies] if active_strategies else ["orion", "theta"]
        supported_keys = {"orion", "theta"}
        unknown_keys = set(self.active_keys) - supported_keys
        if unknown_keys:
            raise ValueError(f"Unsupported strategy keys: {', '.join(sorted(unknown_keys))}")
        if not self.active_keys:
            raise ValueError("At least one strategy must be active")

        # 1. Instantiate Isolated Paper Brokers for Core Duo (₹1,00,000 each)
        self.brokers: Dict[str, PaperBroker] = {
            "orion": PaperBroker(account_name="orion", initial_capital=initial_capital_per_strat, persist=True),
            "theta": PaperBroker(account_name="theta", initial_capital=initial_capital_per_strat, persist=True),
        }

        # 2. Instantiate Decoupled Risk Managers
        self.risk_managers: Dict[str, RiskManager] = {
            "orion": RiskManager(),
            "theta": RiskManager(),
        }

        # 3. Instantiate Core Duo Strategies
        self.strategies = {
            "orion": OpeningRetestStrategy(
                broker=self.brokers["orion"],
                risk_manager=self.risk_managers["orion"],
                lots=self.lots,
                symbol=self.symbol,
                retrace_low=0.35,
                retrace_high=0.65,
                confirmation_body_ratio=0.15,
                entry_cutoff="11:00",
                version="ORION-2.0",
                telegram=self.telegram
            ),
            "theta": ThetaDecayTraderStrategy(
                broker=self.brokers["theta"],
                risk_manager=self.risk_managers["theta"],
                lots=self.lots,
                symbol=self.symbol,
                telegram_notifier=self.telegram
            )
        }
        self.brokers = {key: self.brokers[key] for key in self.active_keys}
        self.risk_managers = {key: self.risk_managers[key] for key in self.active_keys}
        self.strategies = {key: self.strategies[key] for key in self.active_keys}

    def initialize(self):
        """Initializes all active brokers and strategies."""
        strat_names = [s.name for s in self.strategies.values()]
        logger.info(f"🚀 Initializing Strategy Engine ({len(self.strategies)} active strategies: {', '.join(strat_names)})...")
        for key, broker in self.brokers.items():
            broker.authenticate()

        for key, strat in self.strategies.items():
            strat.initialize()

        logger.info(f"✅ Core Strategy Portfolio Armed & Ready: Total Capital: ₹{len(self.brokers)*self.initial_capital_per_strat:,.2f}")

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

    def check_exit_conditions(self, current_dt: datetime) -> None:
        """Run time-based exit checks for every active strategy."""
        for strat in self.strategies.values():
            check_exits = getattr(strat, "check_exit_conditions", None)
            if check_exits:
                check_exits(current_dt)

    def square_off_all_positions(self) -> int:
        """Flatten every active account and return the number of exit orders."""
        return sum(len(broker.square_off_all_positions() or []) for broker in self.brokers.values())

    def get_portfolio_status(self) -> Dict[str, Any]:
        """Calculates combined portfolio metrics and individual strategy stats."""
        margins = [broker.get_margins() for broker in self.brokers.values()]
        tot_capital = sum(b.initial_capital for b in self.brokers.values())
        avail_cash = sum(margin["available_cash"] for margin in margins)
        tot_pnl = sum(margin["total_pnl"] for margin in margins)
        daily_pnl = sum(margin["daily_pnl"] for margin in margins)
        tot_charges = sum(margin.get("total_charges", 0.0) for margin in margins)

        strat_statuses = {}
        for key, strat in self.strategies.items():
            strat_statuses[key] = strat.get_status()

        return {
            "total_capital": tot_capital,
            "available_cash": avail_cash,
            "total_pnl": tot_pnl,
            "daily_pnl": daily_pnl,
            "total_charges": tot_charges,
            "strategies": strat_statuses
        }
