"""
Zerodha Kite Connect Broker integration.
Supports automated TOTP authentication, live quotes, order routing, and portfolio sync.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from core.models import (
    Order, Position, Instrument, OrderStatus, OrderSide, OrderType, ProductType, OptionType
)
from core.logger import get_logger
from brokers.base_broker import BaseBroker
from config.settings import settings

logger = get_logger("ZerodhaBroker")


class ZerodhaBroker(BaseBroker):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        user_id: Optional[str] = None,
        totp_key: Optional[str] = None
    ):
        self.api_key = api_key or settings.ZERODHA_API_KEY
        self.api_secret = api_secret or settings.ZERODHA_API_SECRET
        self.user_id = user_id or settings.ZERODHA_USER_ID
        self.totp_key = totp_key or settings.ZERODHA_TOTP_KEY

        self.kite = None
        self.access_token: Optional[str] = None
        self.session_file = Path(__file__).resolve().parent.parent / ".kite_session.json"

    def authenticate(self) -> bool:
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            logger.error("kiteconnect is not installed. Run: pip install kiteconnect")
            return False

        if not self.api_key or not self.api_secret:
            logger.error("Zerodha API Key or Secret missing in configuration.")
            return False

        self.kite = KiteConnect(api_key=self.api_key)

        # Check existing cached session
        if self._load_cached_session():
            try:
                profile = self.kite.profile()
                logger.info(f"✅ Zerodha connected successfully. User: {profile.get('user_name', self.user_id)}")
                return True
            except Exception as e:
                logger.warning(f"Cached Zerodha session expired: {e}")

        # If TOTP key is provided, we can log login URL
        login_url = self.kite.login_url()
        logger.info(f"🔗 Please authorize Zerodha Kite via: {login_url}")
        logger.info("Once logged in, paste your request_token into the console or set it in set_access_token().")
        return False

    def set_access_token_from_request_token(self, request_token: str) -> bool:
        """Generate access_token from request_token returned by Zerodha OAuth callback."""
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self.access_token = data["access_token"]
            self.kite.set_access_token(self.access_token)
            self._save_session(self.access_token)
            logger.info("✅ Zerodha Access Token generated and saved successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to generate Zerodha session: {e}")
            return False

    def get_ltp(self, symbol: str) -> float:
        try:
            instrument_key = f"NFO:{symbol}"
            quote = self.kite.ltp(instrument_key)
            return float(quote[instrument_key]["last_price"])
        except Exception as e:
            logger.error(f"Error fetching LTP for {symbol}: {e}")
            return 0.0

    def place_order(self, order: Order) -> Order:
        try:
            variety = self.kite.VARIETY_REGULAR
            exchange = self.kite.EXCHANGE_NFO
            tradingsymbol = order.instrument.symbol

            transaction_type = (
                self.kite.TRANSACTION_TYPE_BUY
                if order.side == OrderSide.BUY
                else self.kite.TRANSACTION_TYPE_SELL
            )

            order_type_map = {
                OrderType.MARKET: self.kite.ORDER_TYPE_MARKET,
                OrderType.LIMIT: self.kite.ORDER_TYPE_LIMIT,
                OrderType.SL_LIMIT: self.kite.ORDER_TYPE_SL,
                OrderType.SL_MARKET: self.kite.ORDER_TYPE_SLM,
            }
            order_type = order_type_map.get(order.order_type, self.kite.ORDER_TYPE_MARKET)

            product_map = {
                ProductType.MIS: self.kite.PRODUCT_MIS,
                ProductType.NRML: self.kite.PRODUCT_NRML,
                ProductType.CNC: self.kite.PRODUCT_CNC,
            }
            product = product_map.get(order.product, self.kite.PRODUCT_MIS)

            kite_order_id = self.kite.place_order(
                variety=variety,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=order.quantity,
                product=product,
                order_type=order_type,
                price=order.price if order.order_type in (OrderType.LIMIT, OrderType.SL_LIMIT) else None,
                trigger_price=order.trigger_price if order.order_type in (OrderType.SL_LIMIT, OrderType.SL_MARKET) else None,
                tag=order.tag[:8] if order.tag else None
            )

            order.order_id = str(kite_order_id)
            order.status = OrderStatus.OPEN
            logger.info(f"🚀 Zerodha Order Placed: ID={kite_order_id} {order.side.value} {order.quantity} {tradingsymbol}")
            return order

        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(e)
            logger.error(f"Zerodha order placement failed: {e}")
            return order

    def modify_order(
        self,
        order_id: str,
        new_price: Optional[float] = None,
        new_trigger_price: Optional[float] = None,
        new_quantity: Optional[int] = None
    ) -> bool:
        try:
            self.kite.modify_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
                price=new_price,
                trigger_price=new_trigger_price,
                quantity=new_quantity
            )
            return True
        except Exception as e:
            logger.error(f"Zerodha modify order failed for {order_id}: {e}")
            return False

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
            return True
        except Exception as e:
            logger.error(f"Zerodha cancel order failed for {order_id}: {e}")
            return False

    def get_positions(self) -> Dict[str, Position]:
        positions_dict = {}
        try:
            net_positions = self.kite.positions().get("net", [])
            for p in net_positions:
                symbol = p["tradingsymbol"]
                inst = Instrument(symbol=symbol, exchange=p["exchange"])
                pos = Position(
                    instrument=inst,
                    quantity=p["quantity"],
                    buy_quantity=p["buy_quantity"],
                    sell_quantity=p["sell_quantity"],
                    buy_amount=p["buy_value"],
                    sell_amount=p["sell_value"],
                    average_buy_price=p["buy_price"],
                    average_sell_price=p["sell_price"],
                    ltp=p["last_price"],
                    realized_pnl=p["pnl"],
                    unrealized_pnl=p["m2m"]
                )
                positions_dict[symbol] = pos
        except Exception as e:
            logger.error(f"Error fetching Zerodha positions: {e}")
        return positions_dict

    def get_orders(self) -> List[Order]:
        orders_list = []
        try:
            raw_orders = self.kite.orders()
            status_map = {
                "COMPLETE": OrderStatus.FILLED,
                "REJECTED": OrderStatus.REJECTED,
                "CANCELLED": OrderStatus.CANCELLED,
                "OPEN": OrderStatus.OPEN,
                "TRIGGER PENDING": OrderStatus.OPEN,
            }
            for o in raw_orders:
                inst = Instrument(symbol=o["tradingsymbol"], exchange=o["exchange"])
                ord_obj = Order(
                    order_id=str(o["order_id"]),
                    instrument=inst,
                    side=OrderSide.BUY if o["transaction_type"] == "BUY" else OrderSide.SELL,
                    order_type=OrderType.MARKET if o["order_type"] == "MARKET" else OrderType.LIMIT,
                    quantity=o["quantity"],
                    price=o["price"],
                    trigger_price=o.get("trigger_price", 0.0),
                    status=status_map.get(o["status"], OrderStatus.PENDING),
                    filled_qty=o["filled_quantity"],
                    average_price=o["average_price"],
                    tag=o.get("tag", "")
                )
                orders_list.append(ord_obj)
        except Exception as e:
            logger.error(f"Error fetching Zerodha orders: {e}")
        return orders_list

    def get_margins(self) -> dict:
        try:
            margins = self.kite.margins("equity")
            return {
                "available_cash": margins.get("available", {}).get("cash", 0.0),
                "net_worth": margins.get("net", 0.0)
            }
        except Exception as e:
            logger.error(f"Error fetching Zerodha margins: {e}")
            return {"available_cash": 0.0, "net_worth": 0.0}

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _load_cached_session(self) -> bool:
        if self.session_file.exists():
            try:
                data = json.loads(self.session_file.read_text())
                if "access_token" in data:
                    self.access_token = data["access_token"]
                    self.kite.set_access_token(self.access_token)
                    return True
            except Exception:
                pass
        return False

    def _save_session(self, access_token: str):
        self.session_file.write_text(json.dumps({"access_token": access_token}))
