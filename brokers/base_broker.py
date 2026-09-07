"""
Abstract Base Broker interface for order routing, live data, and position management.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from core.models import Order, Position, Instrument, OrderStatus, OrderSide, OrderType


class BaseBroker(ABC):
    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the broker API."""
        pass

    @abstractmethod
    def get_ltp(self, symbol: str) -> float:
        """Fetch the Last Traded Price for an instrument."""
        pass

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Place a new order with the broker."""
        pass

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        new_price: Optional[float] = None,
        new_trigger_price: Optional[float] = None,
        new_quantity: Optional[int] = None
    ) -> bool:
        """Modify an existing pending or open order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open or pending order."""
        pass

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """Fetch all current open and closed positions."""
        pass

    @abstractmethod
    def get_orders(self) -> List[Order]:
        """Fetch all orders placed in the current session."""
        pass

    @abstractmethod
    def get_trades(self) -> List[dict]:
        """Fetch all executed trades in the current session."""
        pass

    @abstractmethod
    def get_margins(self) -> dict:
        """Fetch available margin and account balance details."""
        pass

    def square_off_all_positions(self) -> List[Order]:
        """Close out all net non-zero positions at market price."""
        exit_orders = []
        positions = self.get_positions()

        for symbol, pos in positions.items():
            if pos.quantity != 0:
                side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
                qty = abs(pos.quantity)
                exit_order = Order(
                    order_id=f"EXIT_{symbol}_{int(pos.ltp)}",
                    instrument=pos.instrument,
                    side=side,
                    order_type=OrderType.MARKET,
                    quantity=qty,
                    tag="AUTO_SQUARE_OFF"
                )
                placed = self.place_order(exit_order)
                exit_orders.append(placed)

        return exit_orders
