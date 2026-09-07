"""
ICT / Smart Money Concepts Liquidity Sweep & FVG Strategy Module.
Traps false breakouts at Previous Day High (PDH) and Previous Day Low (PDL),
entering high-R:R counter-trend reversals back towards liquidity targets.
"""

import time
from datetime import datetime, date, time as dtime
from typing import Dict, Any, Optional, List
import pandas as pd

from core.logger import get_logger
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from core.models import Order, OrderSide, OrderType, OrderStatus, Instrument, Tick
from core.option_chain import get_next_weekly_expiry, OptionType, get_atm_strike
from core.market_data import get_live_option_quote
from config.settings import settings

logger = get_logger("ICTSweepTrader")


class ICTSweepTraderStrategy:
    """
    ICT Liquidity Sweep Engine:
    - Identifies PDH (Previous Day High) and PDL (Previous Day Low).
    - Detects institutional sweeps (wick breaches level then closes back inside).
    - Enters reversal trade targeting liquidity pools (Midpoint / Opposite Level).
    - Asymmetric Risk-to-Reward (1:2.5 to 1:4.0).
    """

    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        lots: int = 2,
        symbol: str = "NIFTY",
        telegram_notifier=None
    ):
        self.name = "ICT-Liquidity"
        self.broker = broker
        self.risk_manager = risk_manager
        self.lots = lots
        self.symbol = symbol
        self.telegram = telegram_notifier

        self.trade_date: Optional[date] = None
        self.pdh = 0.0
        self.pdl = 0.0
        self.p_close = 0.0
        self.levels_loaded = False

        self.in_trade = False
        self.trade_closed_today = False
        self.trade_side: Optional[str] = None  # "CALL" or "PUT"
        self.entry_spot = 0.0
        self.entry_opt_price = 0.0
        self.target1_spot = 0.0
        self.target2_spot = 0.0
        self.sl_spot = 0.0
        self.breakeven_locked = False
        self.trade_order: Optional[Order] = None
        self.opt_symbol = ""
        self.opt_token = ""
        self.opt_strike = 0
        self.daily_pnl = 0.0
        self.is_active = True

    def initialize(self):
        """Fetch PDH and PDL levels."""
        now = datetime.now()
        self.trade_date = now.date()
        self.load_daily_anchors()
        logger.info(f"🎯 [{self.name}] Initialized for {self.symbol}. PDH={self.pdh:.1f} | PDL={self.pdl:.1f}")

    def load_daily_anchors(self):
        """Fetches prior day's OHLC from SmartAPI/Yahoo."""
        try:
            from core.sr_calculator import fetch_multi_timeframe_ohlc
            data = fetch_multi_timeframe_ohlc(self.symbol)
            daily = data.get("daily", {})
            self.pdh = float(daily.get("high", 24000.0))
            self.pdl = float(daily.get("low", 23800.0))
            self.p_close = float(daily.get("close", 23900.0))
            self.levels_loaded = True
            logger.info(f"📍 Anchors Loaded: PDH={self.pdh:.1f} | PDL={self.pdl:.1f}")
        except Exception as e:
            logger.error(f"Error loading daily anchors for ICT: {e}")

    def on_tick(self, tick: Tick):
        if not self.is_active:
            return

        now = tick.timestamp
        if self.trade_date != now.date():
            self.trade_date = now.date()
            self.trade_closed_today = False
            self.in_trade = False
            self.breakeven_locked = False
            self.load_daily_anchors()

        if self.in_trade:
            self.manage_active_trade(tick.price, now)

    def on_bar(self, bar: Dict[str, Any]):
        """Detects liquidity sweeps on 5m bars between 09:45 AM and 13:30 PM."""
        if not self.is_active or self.in_trade or self.trade_closed_today or not self.levels_loaded:
            return

        ts = bar["timestamp"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        t = ts.time()

        if t < dtime(9, 45) or t > dtime(13, 30):
            return

        c_open = bar["open"]
        c_high = bar["high"]
        c_low = bar["low"]
        c_close = bar["close"]
        rng = c_high - c_low
        if rng <= 0:
            return

        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # 1. Bearish Sweep of PDH (High probed PDH by up to 25 pts, but closed back below PDH with upper wick)
        if c_high > self.pdh and c_high <= (self.pdh + 30.0) and c_close < self.pdh and (upper_wick / rng) >= 0.40:
            self.enter_trade("PUT", c_close, c_high + 5.0, ts, "PDH LIQUIDITY SWEEP (Buy-Side Trapped)")

        # 2. Bullish Sweep of PDL (Low probed PDL by up to 25 pts, but closed back above PDL with lower wick)
        elif c_low < self.pdl and c_low >= (self.pdl - 30.0) and c_close > self.pdl and (lower_wick / rng) >= 0.40:
            self.enter_trade("CALL", c_close, c_low - 5.0, ts, "PDL LIQUIDITY SWEEP (Sell-Side Trapped)")

    def enter_trade(self, side: str, spot_price: float, sl_level: float, current_dt: datetime, reason: str):
        if self.in_trade or self.trade_closed_today:
            return

        self.trade_side = side
        self.entry_spot = spot_price
        self.sl_spot = sl_level
        self.breakeven_locked = False

        mid_point = round((self.pdh + self.pdl) / 2.0, 1)

        if side == "CALL":
            self.target1_spot = mid_point
            self.target2_spot = self.pdh
            opt_type_str = "CE"
        else:
            self.target1_spot = mid_point
            self.target2_spot = self.pdl
            opt_type_str = "PE"

        atm = get_atm_strike(spot_price, step=settings.NIFTY_STRIKE_STEP)
        self.opt_strike = atm
        expiry = get_next_weekly_expiry(symbol="NIFTY", as_string=True)
        self.opt_symbol = f"NIFTY{expiry}{atm}{opt_type_str}"

        quote = get_live_option_quote(symbol="nifty", strike=atm, opt_type=opt_type_str)
        self.entry_opt_price = quote.get("price", 120.0)
        self.opt_token = str(quote.get("token", "99926000"))

        contract_qty = self.lots * settings.NIFTY_LOT_SIZE
        order = Order(
            order_id=f"ICT_{int(time.time())}",
            instrument=Instrument(symbol=self.opt_symbol, exchange="NFO", lot_size=settings.NIFTY_LOT_SIZE),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=contract_qty,
            price=self.entry_opt_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag=f"ICT_{side}"
        )

        placed = self.broker.place_order(order)
        if placed and placed.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.FILLED]:
            self.in_trade = True
            self.trade_order = placed
            logger.info(f"🚀 [ICT TRADE ENTERED] {reason} | Bought {self.opt_symbol} @ ₹{self.entry_opt_price:.2f}")

            if self.telegram:
                self.telegram.send_notification(
                    f"⚡ <b>ICT LIQUIDITY SWEEP ENTERED ({reason})</b>\n"
                    f"• Instrument: <b>{self.opt_symbol}</b>\n"
                    f"• Entry Premium: <b>₹{self.entry_opt_price:.2f}</b> (Spot: {spot_price:.1f})\n"
                    f"• Quantity: <b>{contract_qty} ({self.lots} Lots)</b>\n"
                    f"• Invalidation SL: <b>{self.sl_spot:.1f}</b>\n"
                    f"• Target 1 (Midpoint BE Lock): <b>{self.target1_spot:.1f}</b>\n"
                    f"• Target 2 (Opposite Extreme): <b>{self.target2_spot:.1f}</b>"
                )

    def manage_active_trade(self, current_spot: float, current_dt: datetime):
        if not self.in_trade or not self.trade_order:
            return

        is_call = self.trade_side == "CALL"
        spot_move = (current_spot - self.entry_spot) if is_call else (self.entry_spot - current_spot)
        target1_dist = abs(self.target1_spot - self.entry_spot)
        target2_dist = abs(self.target2_spot - self.entry_spot)

        # 1. Breakeven Lock at Target 1 (Median)
        if spot_move >= target1_dist and not self.breakeven_locked:
            self.breakeven_locked = True
            logger.info(f"🔒 [ICT Midpoint Reached ({self.target1_spot:.1f})] Ratcheting Stop Loss to Breakeven!")
            if self.telegram:
                self.telegram.send_notification(
                    f"🔒 <b>ICT TARGET 1 HIT ({self.target1_spot:.1f})!</b>\n"
                    f"SL ratcheted to <b>BREAKEVEN</b>. Trade is now 100% Risk-Free."
                )

        # 2. Target 2 Take Profit
        if spot_move >= target2_dist:
            self.close_trade("TARGET 2 (Take Profit)", current_spot, current_dt)
            return

        # 3. Stop Loss / Breakeven Exit
        if self.breakeven_locked:
            if (is_call and current_spot <= self.entry_spot) or (not is_call and current_spot >= self.entry_spot):
                self.close_trade("BREAKEVEN EXIT", current_spot, current_dt)
                return
        else:
            if (is_call and current_spot <= self.sl_spot) or (not is_call and current_spot >= self.sl_spot):
                self.close_trade("STOP LOSS HIT", current_spot, current_dt)
                return

        # 4. EOD Square-off (15:15 PM)
        if current_dt.time() >= dtime(15, 15):
            self.close_trade("EOD SQUARE-OFF (15:15 PM)", current_spot, current_dt)
            return

    def close_trade(self, reason: str, exit_spot: float, current_dt: datetime):
        if not self.in_trade or not self.trade_order:
            return

        opt_exit_price = self.entry_opt_price
        delta = 0.65 if "TARGET" in reason else 0.45
        spot_pts = (exit_spot - self.entry_spot) if self.trade_side == "CALL" else (self.entry_spot - exit_spot)
        opt_exit_price = max(1.0, round(self.entry_opt_price + (spot_pts * delta), 2))

        exit_order = Order(
            order_id=f"EX_ICT_{int(time.time())}",
            instrument=self.trade_order.instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=self.trade_order.quantity,
            price=opt_exit_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="ICT_EXIT"
        )
        self.broker.place_order(exit_order)

        net_pnl = round((opt_exit_price - self.entry_opt_price) * self.trade_order.quantity, 2)
        self.daily_pnl += net_pnl
        self.risk_manager.evaluate_daily_pnl(self.daily_pnl)

        try:
            from core.strategy_ledger import strategy_ledger
            dur_mins = round((current_dt - self.trade_order.placed_at).total_seconds() / 60.0, 1) if hasattr(self.trade_order, "placed_at") and self.trade_order.placed_at else 0.0
            strategy_ledger.record_trade("ict", {
                "trade_id": f"ICT_{int(time.time())}",
                "strategy": "ICT-Liquidity",
                "index": self.symbol,
                "date": current_dt.strftime("%Y-%m-%d"),
                "side": self.trade_side,
                "entry_time": self.trade_order.placed_at.strftime("%H:%M:%S") if hasattr(self.trade_order, "placed_at") and self.trade_order.placed_at else "",
                "entry_spot": round(self.entry_spot, 1),
                "entry_premium": round(self.entry_opt_price, 2),
                "exit_time": current_dt.strftime("%H:%M:%S"),
                "exit_spot": round(exit_spot, 1),
                "exit_premium": round(opt_exit_price, 2),
                "reason": reason,
                "breakeven_triggered": self.breakeven_locked,
                "quantity": self.trade_order.quantity,
                "net_pnl": net_pnl,
                "duration_mins": dur_mins,
                "mode": "PAPER",
                "notes": f"ICT Sweep Reversal. Closed via {reason}."
            })
        except Exception as e:
            logger.warning(f"Failed to record trade to ICT ledger: {e}")

        logger.info(f"🏁 [ICT TRADE CLOSED: {reason}] PnL: ₹{net_pnl:+,.2f}")

        if self.telegram:
            emoji = "🏆" if net_pnl > 0 else "🛑"
            self.telegram.send_notification(
                f"{emoji} <b>ICT TRADE CLOSED ({reason})</b>\n"
                f"• Instrument: <b>{self.opt_symbol}</b>\n"
                f"• Entry: <b>₹{self.entry_opt_price:.2f}</b> | Exit: <b>₹{opt_exit_price:.2f}</b>\n"
                f"• Net P&L: <b>₹{net_pnl:+,.2f}</b>\n"
                f"• Today's Strategy P&L: <b>₹{self.daily_pnl:+,.2f}</b>"
            )

        self.in_trade = False
        self.trade_order = None
        self.trade_closed_today = True

    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy": self.name,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "in_trade": self.in_trade,
            "breakeven_active": self.breakeven_locked,
            "daily_pnl": self.daily_pnl
        }
