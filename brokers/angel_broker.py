"""
Angel One SmartAPI Broker integration.
Automates TOTP login using pyotp, handles order placement, and queries positions.
"""

from typing import Dict, List, Optional
from core.models import (
    Order, Position, Instrument, OrderStatus, OrderSide, OrderType, ProductType
)
from core.logger import get_logger
from brokers.base_broker import BaseBroker
from config.settings import settings

logger = get_logger("AngelBroker")


class AngelOneBroker(BaseBroker):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        client_id: Optional[str] = None,
        pin: Optional[str] = None,
        totp_key: Optional[str] = None
    ):
        self.api_key = api_key or settings.ANGEL_API_KEY
        self.api_secret = api_secret or settings.ANGEL_API_SECRET
        self.client_id = client_id or settings.ANGEL_CLIENT_ID
        self.pin = pin or settings.ANGEL_PIN
        self.totp_key = totp_key or settings.ANGEL_TOTP_KEY

        self.smart_api = None
        self.jwt_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self.account_name: Optional[str] = None
        self.token_map: Dict[str, str] = {
            "NIFTY": "99926000",
            "NIFTY 50": "99926000",
            "BANKNIFTY": "99926009",
        }

    def _load_instruments_cache(self):
        from pathlib import Path
        import json
        candidates = [
            Path("C:/angel/OpenAPIScripMaster_cache.json"),
            Path(__file__).resolve().parent.parent / "OpenAPIScripMaster_cache.json",
        ]
        for p in candidates:
            if p.exists():
                try:
                    logger.info(f"Loading instrument tokens from {p}...")
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for item in data:
                        if item.get("name") in ("NIFTY", "BANKNIFTY"):
                            self.token_map[item.get("symbol")] = str(item.get("token"))
                    logger.info(f"Loaded {len(self.token_map)} instruments from cache.")
                    return
                except Exception as e:
                    logger.warning(f"Failed loading instruments cache {p}: {e}")

    def get_token_for_symbol(self, symbol: str) -> str:
        return self.token_map.get(symbol, "")

    def authenticate(self) -> bool:
        # First priority: Use user's login.py
        try:
            from login import login
            logger.info("Connecting to Angel One via login.py...")
            api_obj = login()
            if api_obj:
                self.smart_api = api_obj
                self.jwt_token = getattr(api_obj, "access_token", "")
                self.feed_token = getattr(api_obj, "feed_token", "")
                self.account_name = self.client_id or "Angel One User"
                self._load_instruments_cache()
                logger.info("Angel One connected successfully via login.py!")
                return True
        except Exception as e:
            logger.warning(f"Direct login.py invocation failed: {e}. Falling back to SmartConnect.")

        # Fallback to direct SmartConnect
        try:
            from SmartApi import SmartConnect
            import pyotp
        except ImportError:
            logger.error("smartapi-python or pyotp is not installed.")
            return False

        if not self.api_key or not self.client_id or not self.pin or not self.totp_key:
            logger.error("Angel One credentials incomplete. Please check your .env file.")
            return False

        try:
            self.smart_api = SmartConnect(api_key=self.api_key)
            totp = pyotp.TOTP(self.totp_key).now()
            data = self.smart_api.generateSession(self.client_id, self.pin, totp)

            if data and data.get("status"):
                self.jwt_token = data["data"]["jwtToken"]
                self.feed_token = data["data"]["feedToken"]
                self.account_name = data["data"].get("name", self.client_id)
                self._load_instruments_cache()
                logger.info(f"Angel One authenticated successfully for: {self.account_name} ({self.client_id})")
                return True
            else:
                logger.error(f"Angel One authentication failed: {data.get('message')}")
                return False

        except Exception as e:
            logger.error(f"Error connecting to Angel One: {e}")
            return False

    def get_ltp(self, symbol: str, exchange: str = "NFO", token: str = "") -> float:
        try:
            if not token:
                token = self.get_token_for_symbol(symbol)
            res = self.smart_api.ltpData(exchange, symbol, token)
            if res and res.get("status"):
                return float(res["data"]["ltp"])
        except Exception as e:
            logger.error(f"Error fetching Angel One LTP for {symbol}: {e}")
        return 0.0


    def place_order(self, order: Order) -> Order:
        try:
            order_params = {
                "variety": "NORMAL",
                "tradingsymbol": order.instrument.symbol,
                "symboltoken": str(order.instrument.token or ""),
                "transactiontype": "BUY" if order.side == OrderSide.BUY else "SELL",
                "exchange": order.instrument.exchange,
                "ordertype": "MARKET" if order.order_type == OrderType.MARKET else "LIMIT",
                "producttype": "INTRADAY" if order.product == ProductType.MIS else "CARRYFORWARD",
                "duration": "DAY",
                "price": str(order.price),
                "squareoff": "0",
                "stoploss": "0",
                "quantity": str(order.quantity),
            }

            res = self.smart_api.placeOrder(order_params)
            if res and res.get("status"):
                order.order_id = str(res["data"]["orderid"])
                order.status = OrderStatus.OPEN
                logger.info(f"🚀 Angel One Order Placed: ID={order.order_id} {order.side.value} {order.quantity} {order.instrument.symbol}")
            else:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = res.get("message", "Unknown error")
                logger.error(f"Angel One order rejected: {order.rejection_reason}")
            return order
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(e)
            logger.error(f"Error placing Angel One order: {e}")
            return order

    def modify_order(
        self,
        order_id: str,
        new_price: Optional[float] = None,
        new_trigger_price: Optional[float] = None,
        new_quantity: Optional[int] = None
    ) -> bool:
        try:
            params = {
                "orderid": order_id,
                "variety": "NORMAL",
                "ordertype": "LIMIT" if new_price else "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "price": str(new_price or 0),
                "quantity": str(new_quantity or 0)
            }
            res = self.smart_api.modifyOrder(params)
            return bool(res and res.get("status"))
        except Exception as e:
            logger.error(f"Error modifying Angel One order {order_id}: {e}")
            return False

    def cancel_order(self, order_id: str) -> bool:
        try:
            res = self.smart_api.cancelOrder(order_id, "NORMAL")
            return bool(res and res.get("status"))
        except Exception as e:
            logger.error(f"Error cancelling Angel One order {order_id}: {e}")
            return False

    def get_positions(self) -> Dict[str, Position]:
        positions_dict = {}
        try:
            res = self.smart_api.position()
            if res and res.get("status") and res.get("data"):
                for p in res["data"]:
                    symbol = p["tradingsymbol"]
                    inst = Instrument(symbol=symbol, exchange=p.get("exchange", "NFO"))
                    pos = Position(
                        instrument=inst,
                        quantity=int(p["netqty"]),
                        buy_quantity=int(p["buyqty"]),
                        sell_quantity=int(p["sellqty"]),
                        average_buy_price=float(p.get("buyavgprice", 0.0)),
                        average_sell_price=float(p.get("sellavgprice", 0.0)),
                        ltp=float(p.get("ltp", 0.0)),
                        realized_pnl=float(p.get("realised", 0.0)),
                        unrealized_pnl=float(p.get("unrealised", 0.0))
                    )
                    positions_dict[symbol] = pos
        except Exception as e:
            logger.error(f"Error fetching Angel One positions: {e}")
        return positions_dict

    def get_orders(self) -> List[Order]:
        orders_list = []
        try:
            res = self.smart_api.orderBook()
            if res and res.get("status") and res.get("data"):
                for o in res["data"]:
                    inst = Instrument(symbol=o["tradingsymbol"], exchange=o.get("exchange", "NFO"))
                    orders_list.append(
                        Order(
                            order_id=str(o["orderid"]),
                            instrument=inst,
                            side=OrderSide.BUY if o["transactiontype"] == "BUY" else OrderSide.SELL,
                            order_type=OrderType.MARKET if o["ordertype"] == "MARKET" else OrderType.LIMIT,
                            quantity=int(o["quantity"]),
                            price=float(o.get("price", 0.0)),
                            status=OrderStatus.FILLED if o.get("orderstatus") == "complete" else OrderStatus.OPEN,
                            filled_qty=int(o.get("filledshares", 0)),
                            average_price=float(o.get("averageprice", 0.0))
                        )
                    )
        except Exception as e:
            logger.error(f"Error fetching Angel One orders: {e}")
        return orders_list

    def get_trades(self) -> List[dict]:
        trades_list = []
        try:
            res = self.smart_api.tradeBook()
            if res and res.get("status") and res.get("data"):
                for t in res["data"]:
                    trades_list.append({
                        "trade_id": str(t.get("tradeid", "")),
                        "order_id": str(t.get("orderid", "")),
                        "symbol": t.get("tradingsymbol", ""),
                        "exchange": t.get("exchange", "NFO"),
                        "side": t.get("transactiontype", "BUY"),
                        "quantity": int(t.get("fillshares", 0)),
                        "price": float(t.get("fillprice", 0.0)),
                        "charges": 0.0,
                        "timestamp": t.get("filltime", ""),
                        "tag": ""
                    })
        except Exception as e:
            logger.error(f"Error fetching Angel One trades: {e}")
        return trades_list

    def get_margins(self) -> dict:
        try:
            if hasattr(self.smart_api, "rmsLimit"):
                res = self.smart_api.rmsLimit()
            else:
                res = self.smart_api.getRMS()
            if res and (res.get("status") or res.get("success")) and isinstance(res.get("data"), dict):
                return {
                    "available_cash": float(res["data"].get("availablecash", 0.0)),
                    "net_worth": float(res["data"].get("net", 0.0)),
                    "total_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0
                }
        except Exception as e:
            logger.error(f"Error fetching Angel One margins: {e}")
        return {"available_cash": 0.0, "net_worth": 0.0, "total_pnl": 0.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0}
