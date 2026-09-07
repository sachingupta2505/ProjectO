"""
Paper Trading Simulation Broker.
Provides a zero-risk simulated execution environment with realistic fills, slippage,
order book management, persistent disk state, and real-time MTM position tracking.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from core.models import (
    Order, Position, Instrument, OrderStatus, OrderSide, OrderType, ProductType, OptionType
)
from core.logger import get_logger
from brokers.base_broker import BaseBroker
from config.settings import settings
from core.charges import calculate_order_charges

logger = get_logger("PaperBroker")


class PaperBroker(BaseBroker):
    def __init__(self, initial_capital: float = 100000.0, slippage_pct: float = 0.002, persist: bool = False, account_name: str = "default"):
        self.account_name = account_name
        self.initial_capital = initial_capital
        self.available_cash = initial_capital
        self.total_charges: float = 0.0
        self.slippage_pct = slippage_pct
        self.persist = persist
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Position] = {}
        self.trades: List[Dict[str, Any]] = []
        self.market_prices: Dict[str, float] = {}
        self.is_connected: bool = False

        # Daily Session tracking to isolate day PnL from historical paper data
        self.session_date: str = datetime.now().strftime("%Y-%m-%d")
        self.daily_realized_pnl: float = 0.0
        self.daily_charges: float = 0.0

        if account_name == "default":
            self.state_file = Path(__file__).resolve().parent.parent / "logs" / "paper_broker_state.json"
        else:
            self.state_file = Path(__file__).resolve().parent.parent / "logs" / f"paper_broker_{account_name}.json"

        if self.persist:
            self._load_state()

    def _save_state(self):
        """Persist positions, orders, trades, and balances to disk."""
        if not self.persist:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            pos_dict = {}
            for k, p in self.positions.items():
                pos_dict[k] = {
                    "symbol": p.instrument.symbol,
                    "exchange": p.instrument.exchange,
                    "quantity": p.quantity,
                    "buy_quantity": p.buy_quantity,
                    "sell_quantity": p.sell_quantity,
                    "buy_amount": p.buy_amount,
                    "sell_amount": p.sell_amount,
                    "average_buy_price": p.average_buy_price,
                    "average_sell_price": p.average_sell_price,
                    "ltp": p.ltp,
                    "realized_pnl": p.realized_pnl,
                    "unrealized_pnl": p.unrealized_pnl,
                    "charges": getattr(p, "charges", 0.0),
                    "gross_pnl": getattr(p, "gross_pnl", p.realized_pnl + p.unrealized_pnl),
                    "net_pnl": p.total_pnl
                }

            ord_dict = {}
            for oid, o in self.orders.items():
                p_time = o.placed_at.isoformat() if hasattr(o, "placed_at") and o.placed_at else datetime.now().isoformat()
                u_time = o.updated_at.isoformat() if hasattr(o, "updated_at") and o.updated_at else p_time
                ord_dict[oid] = {
                    "order_id": o.order_id,
                    "symbol": o.instrument.symbol,
                    "exchange": o.instrument.exchange,
                    "side": o.side.value,
                    "order_type": o.order_type.value,
                    "quantity": o.quantity,
                    "price": o.price,
                    "trigger_price": o.trigger_price,
                    "status": o.status.value,
                    "filled_qty": o.filled_qty,
                    "average_price": o.average_price,
                    "tag": o.tag,
                    "placed_at": p_time,
                    "updated_at": u_time
                }

            # Always calculate available_cash accurately with futures margin
            total_realized = sum(p.realized_pnl for p in self.positions.values())
            tot_chg = getattr(self, "total_charges", 0.0)
            tot_margin = 0.0
            for p in self.positions.values():
                if p.quantity != 0:
                    is_fut = (getattr(p.instrument, "exchange", "") == "MCX") or ("FUT" in p.instrument.symbol.upper())
                    m_rate = 0.10 if is_fut else 1.0
                    exec_price = p.average_buy_price if p.quantity > 0 else p.average_sell_price
                    tot_margin += abs(p.quantity) * exec_price * m_rate
            self.available_cash = round(self.initial_capital + total_realized - tot_chg - tot_margin, 2)

            data = {
                "initial_capital": self.initial_capital,
                "available_cash": self.available_cash,
                "total_charges": getattr(self, "total_charges", 0.0),
                "session_date": self.session_date,
                "daily_realized_pnl": getattr(self, "daily_realized_pnl", 0.0),
                "daily_charges": getattr(self, "daily_charges", 0.0),
                "positions": pos_dict,
                "orders": ord_dict,
                "trades": self.trades,
                "market_prices": self.market_prices,
                "updated_at": datetime.now().isoformat()
            }
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to persist paper broker state: {e}")

    def _load_state(self):
        """Load state from disk if available."""
        if not self.persist:
            return
        try:
            if not self.state_file.exists():
                return
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.initial_capital = data.get("initial_capital", self.initial_capital)
            self.available_cash = data.get("available_cash", self.available_cash)
            self.total_charges = data.get("total_charges", 0.0)

            # Daily Session Isolation: only restore daily metrics if file is from today
            today_str = datetime.now().strftime("%Y-%m-%d")
            saved_date = data.get("session_date", "")
            if saved_date == today_str:
                self.session_date = today_str
                self.daily_realized_pnl = float(data.get("daily_realized_pnl", 0.0))
                self.daily_charges = float(data.get("daily_charges", 0.0))
            else:
                self.session_date = today_str
                self.daily_realized_pnl = 0.0
                self.daily_charges = 0.0

            # Clear existing in-memory state to cleanly sync with disk
            self.positions.clear()
            self.orders.clear()

            for sym, d in data.get("positions", {}).items():
                inst = Instrument(symbol=d["symbol"], exchange=d.get("exchange", "NFO"))
                p = Position(
                    instrument=inst,
                    quantity=d.get("quantity", 0),
                    buy_quantity=d.get("buy_quantity", 0),
                    sell_quantity=d.get("sell_quantity", 0),
                    buy_amount=d.get("buy_amount", 0.0),
                    sell_amount=d.get("sell_amount", 0.0),
                    average_buy_price=d.get("average_buy_price", 0.0),
                    average_sell_price=d.get("average_sell_price", 0.0),
                    ltp=d.get("ltp", 0.0),
                    realized_pnl=d.get("realized_pnl", 0.0),
                    unrealized_pnl=d.get("unrealized_pnl", 0.0),
                    charges=d.get("charges", 0.0)
                )
                self.positions[sym] = p

            # Recalculate available_cash accurately from margin requirement and realized pnl
            total_realized = sum(p.realized_pnl for p in self.positions.values())
            tot_chg = self.total_charges
            tot_margin = 0.0
            for p in self.positions.values():
                if p.quantity != 0:
                    is_fut = (getattr(p.instrument, "exchange", "") == "MCX") or ("FUT" in p.instrument.symbol.upper())
                    m_rate = 0.10 if is_fut else 1.0
                    exec_price = p.average_buy_price if p.quantity > 0 else p.average_sell_price
                    tot_margin += abs(p.quantity) * exec_price * m_rate
            self.available_cash = round(self.initial_capital + total_realized - tot_chg - tot_margin, 2)

            for oid, o in data.get("orders", {}).items():
                inst = Instrument(symbol=o["symbol"], exchange=o.get("exchange", "NFO"))
                placed_dt = datetime.now()
                if "placed_at" in o and o["placed_at"]:
                    try:
                        placed_dt = datetime.fromisoformat(o["placed_at"])
                    except Exception:
                        pass
                updated_dt = placed_dt
                if "updated_at" in o and o["updated_at"]:
                    try:
                        updated_dt = datetime.fromisoformat(o["updated_at"])
                    except Exception:
                        pass

                self.orders[oid] = Order(
                    order_id=o["order_id"],
                    instrument=inst,
                    side=OrderSide(o["side"]),
                    order_type=OrderType(o["order_type"]),
                    quantity=o["quantity"],
                    price=o.get("price", 0.0),
                    trigger_price=o.get("trigger_price", 0.0),
                    status=OrderStatus(o["status"]),
                    filled_qty=o.get("filled_qty", 0),
                    average_price=o.get("average_price", 0.0),
                    tag=o.get("tag", ""),
                    placed_at=placed_dt,
                    updated_at=updated_dt
                )

            # Load trades history
            self.trades = data.get("trades", [])
            # If trades empty but filled orders exist, synthesize them
            if not self.trades and self.orders:
                for o in self.orders.values():
                    if o.status == OrderStatus.FILLED and o.average_price > 0:
                        self.trades.append({
                            "trade_id": f"TRD_{o.order_id[:8]}",
                            "order_id": o.order_id,
                            "symbol": o.instrument.symbol,
                            "exchange": o.instrument.exchange,
                            "side": o.side.value,
                            "quantity": o.filled_qty or o.quantity,
                            "price": o.average_price,
                            "timestamp": o.placed_at.isoformat(),
                            "tag": o.tag
                        })
        except Exception as e:
            logger.debug(f"Failed to load paper broker state: {e}")

    def reset_account(self, initial_capital: float = 100000.0):
        """Resets paper account to fresh starting capital and clears positions, orders, and charges."""
        self.initial_capital = initial_capital
        self.available_cash = initial_capital
        self.total_charges = 0.0
        self.daily_realized_pnl = 0.0
        self.daily_charges = 0.0
        self.positions.clear()
        self.orders.clear()
        self.trades.clear()
        if self.persist:
            self._save_state()
        logger.info(f"🔄 Paper account reset to fresh balance: ₹{initial_capital:,.2f}")

    def authenticate(self) -> bool:
        self.is_connected = True
        self._load_state()
        logger.info(f"✅ Paper Broker initialized with virtual capital: ₹{self.initial_capital:,.2f}")
        return True

    def set_ltp(self, symbol: str, price: float):
        """Update market price for a symbol and recalculate position PnL."""
        self.market_prices[symbol] = round(price, 2)

        # Update unrealized PnL for active positions
        if symbol in self.positions:
            self.positions[symbol].update_ltp(self.market_prices[symbol])

        # Check pending SL / Limit orders
        self._check_pending_orders(symbol, price)
        self._save_state()

    def get_ltp(self, symbol: str) -> float:
        """Get cached LTP or default fallback price."""
        return self.market_prices.get(symbol, 100.0)

    def place_order(self, order: Order) -> Order:
        if not order.order_id:
            order.order_id = f"PAPER_{uuid.uuid4().hex[:8].upper()}"

        symbol = order.instrument.symbol
        if order.price and order.price > 0:
            current_ltp = float(order.price)
            self.market_prices[symbol] = current_ltp
        else:
            current_ltp = self.market_prices.get(symbol, 100.0)

        # Immediate fill for Market Orders
        if order.order_type == OrderType.MARKET:
            slippage = current_ltp * self.slippage_pct
            exec_price = round(current_ltp + slippage if order.side == OrderSide.BUY else current_ltp - slippage, 2)
            self._fill_order(order, exec_price)
        else:
            # Check if Limit or SL order can be filled immediately
            if order.order_type == OrderType.LIMIT:
                if (order.side == OrderSide.BUY and current_ltp <= order.price) or \
                   (order.side == OrderSide.SELL and current_ltp >= order.price):
                    self._fill_order(order, order.price)
                else:
                    order.status = OrderStatus.OPEN
            elif order.order_type in (OrderType.SL_LIMIT, OrderType.SL_MARKET):
                if (order.side == OrderSide.BUY and current_ltp >= order.trigger_price) or \
                   (order.side == OrderSide.SELL and current_ltp <= order.trigger_price):
                    self._fill_order(order, current_ltp)
                else:
                    order.status = OrderStatus.OPEN

        self.orders[order.order_id] = order
        self._save_state()
        return order

    def modify_order(
        self,
        order_id: str,
        new_price: Optional[float] = None,
        new_trigger_price: Optional[float] = None,
        new_quantity: Optional[int] = None
    ) -> bool:
        if order_id not in self.orders:
            logger.warning(f"Modify failed: Order {order_id} not found")
            return False

        order = self.orders[order_id]
        if not order.is_active:
            logger.warning(f"Modify failed: Order {order_id} is already {order.status.value}")
            return False

        if new_price is not None:
            order.price = new_price
        if new_trigger_price is not None:
            order.trigger_price = new_trigger_price
        if new_quantity is not None:
            order.quantity = new_quantity

        order.updated_at = datetime.now()
        logger.info(f"✏️ Order {order_id} modified: Price={order.price}, Trigger={order.trigger_price}")
        self._save_state()
        return True

    def cancel_order(self, order_id: str) -> bool:
        if order_id not in self.orders:
            return False
        order = self.orders[order_id]
        if order.is_active:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            logger.info(f"❌ Order {order_id} cancelled")
            self._save_state()
            return True
        return False

    def get_positions(self) -> Dict[str, Position]:
        self._load_state()
        return self.positions

    def get_orders(self) -> List[Order]:
        self._load_state()
        return list(self.orders.values())

    def get_trades(self) -> List[Dict[str, Any]]:
        self._load_state()
        return list(self.trades)

    def get_margins(self) -> dict:
        self._load_state()
        total_realized = sum(p.realized_pnl for p in self.positions.values())
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        total_charges = getattr(self, "total_charges", 0.0) or sum(getattr(p, "charges", 0.0) for p in self.positions.values())
        gross_pnl = total_realized + total_unrealized
        net_pnl = gross_pnl - total_charges
        net_worth = self.initial_capital + net_pnl

        # Calculate exact blocked margin for active positions
        total_margin_blocked = 0.0
        for p in self.positions.values():
            if p.quantity != 0:
                is_fut = (getattr(p.instrument, "exchange", "") == "MCX") or ("FUT" in p.instrument.symbol.upper())
                m_rate = 0.10 if is_fut else 1.0
                exec_price = p.average_buy_price if p.quantity > 0 else p.average_sell_price
                total_margin_blocked += abs(p.quantity) * exec_price * m_rate

        self.available_cash = round(self.initial_capital + total_realized - total_charges - total_margin_blocked, 2)

        return {
            "initial_capital": self.initial_capital,
            "net_worth": round(net_worth, 2),
            "available_cash": round(self.available_cash, 2),
            "margin_used": round(total_margin_blocked, 2),
            "gross_pnl": round(gross_pnl, 2),
            "total_charges": round(total_charges, 2),
            "realized_pnl": round(total_realized - total_charges, 2),
            "gross_realized_pnl": round(total_realized, 2),
            "unrealized_pnl": round(total_unrealized, 2),
            "total_pnl": round(net_pnl, 2),
            "daily_pnl": round(self.daily_realized_pnl + total_unrealized - self.daily_charges, 2),
            "daily_realized_pnl": round(self.daily_realized_pnl, 2),
            "daily_charges": round(self.daily_charges, 2),
        }

    # ------------------------------------------------------------------
    # Internal Execution Engine
    # ------------------------------------------------------------------

    def _fill_order(self, order: Order, fill_price: float):
        order.status = OrderStatus.FILLED
        order.filled_qty = order.quantity
        order.average_price = fill_price
        order.updated_at = datetime.now()

        symbol = order.instrument.symbol
        if symbol not in self.positions:
            self.positions[symbol] = Position(instrument=order.instrument, ltp=fill_price)

        pos = self.positions[symbol]

        # Calculate transaction charges for this fill
        exch = getattr(order.instrument, "exchange", "NFO")
        aclass = getattr(order.instrument, "asset_class", "INDEX")
        order_charges = calculate_order_charges(order.side, fill_price, order.quantity, exchange=exch, asset_class=aclass)
        chg_amt = order_charges["total_charges"]
        self.total_charges = round(getattr(self, "total_charges", 0.0) + chg_amt, 2)
        self.daily_charges = round(getattr(self, "daily_charges", 0.0) + chg_amt, 2)
        pos.charges = round(getattr(pos, "charges", 0.0) + chg_amt, 2)
        self.available_cash = round(self.available_cash - chg_amt, 2)

        is_future = (exch == "MCX") or ("FUT" in symbol.upper())
        margin_pct = 0.10 if is_future else 1.0

        if order.side == OrderSide.BUY:
            trade_cost = fill_price * order.quantity
            margin_blocked = trade_cost * margin_pct
            self.available_cash -= margin_blocked
            pos.buy_amount += trade_cost
            pos.buy_quantity += order.quantity

            if pos.quantity >= 0:
                # Adding to existing long
                prev_cost = pos.quantity * pos.average_buy_price
                new_qty = pos.quantity + order.quantity
                pos.average_buy_price = round((prev_cost + trade_cost) / new_qty, 2)
                pos.quantity = new_qty
            else:
                # Covering short position
                cover_qty = min(abs(pos.quantity), order.quantity)
                realized = (pos.average_sell_price - fill_price) * cover_qty
                pos.realized_pnl = round(pos.realized_pnl + realized, 2)
                self.daily_realized_pnl = round(getattr(self, "daily_realized_pnl", 0.0) + realized, 2)
                new_qty = pos.quantity + order.quantity
                pos.quantity = new_qty
                if new_qty == 0:
                    pos.average_sell_price = 0.0
                    pos.average_buy_price = 0.0
                elif new_qty > 0:
                    pos.average_buy_price = fill_price
                    pos.average_sell_price = 0.0

        else:
            # Selling
            trade_credit = fill_price * order.quantity
            margin_released = trade_credit * margin_pct
            self.available_cash += margin_released
            pos.sell_amount += trade_credit
            pos.sell_quantity += order.quantity

            if pos.quantity <= 0:
                # Adding to existing short
                prev_rev = abs(pos.quantity) * pos.average_sell_price
                new_qty = abs(pos.quantity) + order.quantity
                pos.average_sell_price = round((prev_rev + trade_credit) / new_qty, 2)
                pos.quantity = -new_qty
            else:
                # Closing existing long
                close_qty = min(pos.quantity, order.quantity)
                realized = (fill_price - pos.average_buy_price) * close_qty
                pos.realized_pnl = round(pos.realized_pnl + realized, 2)
                self.daily_realized_pnl = round(getattr(self, "daily_realized_pnl", 0.0) + realized, 2)
                new_qty = pos.quantity - order.quantity
                pos.quantity = new_qty
                if new_qty == 0:
                    pos.average_buy_price = 0.0
                    pos.average_sell_price = 0.0
                elif new_qty < 0:
                    pos.average_sell_price = fill_price
                    pos.average_buy_price = 0.0

        pos.update_ltp(fill_price)

        # Record executed trade in trade book with exact charge details
        trade_rec = {
            "trade_id": f"TRD_{uuid.uuid4().hex[:6].upper()}",
            "order_id": order.order_id,
            "symbol": symbol,
            "exchange": exch,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": fill_price,
            "charges": chg_amt,
            "brokerage": order_charges["brokerage"],
            "taxes": round(chg_amt - order_charges["brokerage"], 2),
            "timestamp": order.updated_at.isoformat() if hasattr(order, "updated_at") and order.updated_at else datetime.now().isoformat(),
            "tag": order.tag
        }
        self.trades.append(trade_rec)

        logger.info(f"⚡ [PAPER FILL] {order.side.value} {order.quantity} {symbol} @ ₹{fill_price:.2f} | Charges: ₹{chg_amt:.2f} (Tag: {order.tag})")
        self._save_state()

    def _check_pending_orders(self, symbol: str, price: float):
        for order in list(self.orders.values()):
            if order.instrument.symbol == symbol and order.is_active:
                if order.order_type in (OrderType.SL_MARKET, OrderType.SL_LIMIT):
                    if (order.side == OrderSide.BUY and price >= order.trigger_price) or \
                       (order.side == OrderSide.SELL and price <= order.trigger_price):
                        self._fill_order(order, price)
                elif order.order_type == OrderType.LIMIT:
                    if (order.side == OrderSide.BUY and price <= order.price) or \
                       (order.side == OrderSide.SELL and price >= order.price):
                        self._fill_order(order, order.price)
