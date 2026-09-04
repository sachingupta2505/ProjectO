"""
Core domain models and schemas for orders, positions, instruments, and ticks.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Optional, Dict, Any


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"
    NONE = "NONE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL_LIMIT = "SL"
    SL_MARKET = "SL-M"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ProductType(str, Enum):
    MIS = "MIS"      # Intraday
    NRML = "NRML"    # Overnight / Normal
    CNC = "CNC"      # Cash and carry


@dataclass
class Instrument:
    symbol: str
    exchange: str = "NFO"  # "NFO", "NSE", "MCX"
    token: Optional[int] = None
    lot_size: int = 65
    strike: Optional[float] = None
    expiry: Optional[date] = None
    option_type: OptionType = OptionType.NONE
    tick_size: float = 0.05
    asset_class: str = "INDEX"  # "INDEX", "COMMODITY", "EQUITY"

    def is_option(self) -> bool:
        return self.option_type in (OptionType.CE, OptionType.PE)


@dataclass
class Order:
    order_id: str
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: float = 0.0
    trigger_price: float = 0.0
    product: ProductType = ProductType.MIS
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    average_price: float = 0.0
    tag: str = ""
    placed_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    rejection_reason: str = ""

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.OPEN)


@dataclass
class Position:
    instrument: Instrument
    quantity: int = 0               # Net quantity: > 0 Long, < 0 Short
    buy_quantity: int = 0
    sell_quantity: int = 0
    buy_amount: float = 0.0
    sell_amount: float = 0.0
    average_buy_price: float = 0.0
    average_sell_price: float = 0.0
    ltp: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    charges: float = 0.0

    @property
    def gross_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def total_pnl(self) -> float:
        return round(self.realized_pnl + self.unrealized_pnl - self.charges, 2)

    def update_ltp(self, new_ltp: float):
        self.ltp = new_ltp
        if self.quantity > 0:
            # Long position
            self.unrealized_pnl = (self.ltp - self.average_buy_price) * self.quantity
        elif self.quantity < 0:
            # Short position
            self.unrealized_pnl = (self.average_sell_price - self.ltp) * abs(self.quantity)
        else:
            self.unrealized_pnl = 0.0


@dataclass
class Tick:
    token: int
    symbol: str
    ltp: float
    timestamp: datetime = field(default_factory=datetime.now)
    volume: int = 0
    open_interest: int = 0
    bid_price: float = 0.0
    ask_price: float = 0.0
    change_pct: float = 0.0
