"""
Abstract Base Strategy defining the lifecycle of an algorithmic options trading strategy.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional
from core.models import Tick
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager


class BaseStrategy(ABC):
    def __init__(self, name: str, broker: BaseBroker, risk_manager: RiskManager):
        self.name = name
        self.broker = broker
        self.risk_manager = risk_manager
        self.is_active = False

    @abstractmethod
    def initialize(self):
        """Initialize parameters, indicators, and state before market open."""
        pass

    @abstractmethod
    def on_tick(self, tick: Tick):
        """Process incoming live market tick."""
        pass

    @abstractmethod
    def on_bar(self, bar: Dict[str, Any]):
        """Process completed candle bar (Open, High, Low, Close, Volume)."""
        pass

    @abstractmethod
    def check_entry_conditions(self, current_dt: datetime, spot_price: float):
        """Evaluate strategy entry rules."""
        pass

    @abstractmethod
    def check_exit_conditions(self, current_dt: datetime):
        """Evaluate stop-loss, take-profit, or time-based exits."""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Return human-readable status for dashboard display."""
        pass
