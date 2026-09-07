"""
Central Pivot Range (CPR) Institutional Strategy Module.
Executes directional trend breakouts on Narrow CPR days and mean-reversion
fades/bounces on Wide CPR days with automated Breakeven SL Ratchet and dedicated Ledger.
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
from core.market_data import get_live_option_quote, get_live_nifty_spot
from config.settings import settings

logger = get_logger("CPRTrader")


class CPRTraderStrategy:
    """
    Automated Central Pivot Range (CPR) Engine.
    - Narrow CPR (< 0.20% width): Institutional Trend Day Breakout (Above TC -> Buy CE, Below BC -> Buy PE).
    - Wide CPR (>= 0.20% width): Range-bound Mean Reversion (Bounce BC -> Buy CE, Rejection TC -> Buy PE).
    - Trailing Breakeven Lock at Target 1 (R1/S1).
    - Take Profit at Target 2 (R2/S2).
    """

    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        lots: int = 2,
        symbol: str = "NIFTY",
        telegram_notifier=None
    ):
        self.name = "CPR-Institutional"
        self.broker = broker
        self.risk_manager = risk_manager
        self.lots = lots
        self.symbol = symbol
        self.telegram = telegram_notifier

        # Daily CPR Geometry
        self.trade_date: Optional[date] = None
        self.daily_high = 0.0
        self.daily_low = 0.0
        self.daily_close = 0.0
        self.pivot = 0.0
        self.bc = 0.0
        self.tc = 0.0
        self.top_cpr = 0.0
        self.bottom_cpr = 0.0
        self.cpr_width = 0.0
        self.cpr_width_pct = 0.0
        self.is_narrow_cpr = False
        self.r1 = 0.0
        self.s1 = 0.0
        self.r2 = 0.0
        self.s2 = 0.0
        self.cpr_computed = False

        # Trade State Management
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
        self.opt_type: Optional[OptionType] = None
        self.daily_pnl = 0.0
        self.is_active = True

    def initialize(self):
        """Pre-compute Daily CPR levels from prior completed session."""
        now = datetime.now()
        self.trade_date = now.date()
        self.calculate_daily_cpr()
        logger.info(f"🎯 [{self.name}] Initialized for {self.symbol}. CPR Width: {self.cpr_width_pct:.2f}% ({'Narrow Trend Day' if self.is_narrow_cpr else 'Wide Range Day'})")

    def calculate_daily_cpr(self):
        """Fetches prior day's OHLC and calculates CPR levels."""
        try:
            from core.sr_calculator import fetch_multi_timeframe_ohlc
            data = fetch_multi_timeframe_ohlc(self.symbol)
            daily = data.get("daily", {})
            self.daily_high = float(daily.get("high", 24000.0))
            self.daily_low = float(daily.get("low", 23800.0))
            self.daily_close = float(daily.get("close", 23900.0))

            # CPR Formula
            self.pivot = round((self.daily_high + self.daily_low + self.daily_close) / 3.0, 1)
            self.bc = round((self.daily_high + self.daily_low) / 2.0, 1)
            self.tc = round((2.0 * self.pivot) - self.bc, 1)

            self.top_cpr = max(self.tc, self.bc)
            self.bottom_cpr = min(self.tc, self.bc)
            self.cpr_width = round(self.top_cpr - self.bottom_cpr, 1)
            self.cpr_width_pct = round((self.cpr_width / self.pivot) * 100.0, 2)
            self.is_narrow_cpr = self.cpr_width_pct < 0.20

            # Floor Pivots R1, S1, R2, S2
            self.r1 = round((2.0 * self.pivot) - self.daily_low, 1)
            self.s1 = round((2.0 * self.pivot) - self.daily_high, 1)
            self.r2 = round(self.pivot + (self.daily_high - self.daily_low), 1)
            self.s2 = round(self.pivot - (self.daily_high - self.daily_low), 1)
            self.cpr_computed = True

            regime = "NARROW CPR (Trend Day Expected 🚀)" if self.is_narrow_cpr else "WIDE/AVERAGE CPR (Range Fade Expected ↔️)"
            logger.info(f"📊 CPR Levels Calculated: TC={self.top_cpr:.1f} | Pivot={self.pivot:.1f} | BC={self.bottom_cpr:.1f} | Width={self.cpr_width:.1f} pts ({self.cpr_width_pct:.2f}%) | {regime}")

        except Exception as e:
            logger.error(f"Error calculating CPR levels: {e}")

    def on_tick(self, tick: Tick):
        """Real-time tick listener for active trade management."""
        if not self.is_active:
            return

        now = tick.timestamp
        # Day rollover check
        if self.trade_date != now.date():
            self.trade_date = now.date()
            self.trade_closed_today = False
            self.in_trade = False
            self.breakeven_locked = False
            self.calculate_daily_cpr()

        if self.in_trade:
            self.manage_active_trade(tick.price, now)

    def on_bar(self, bar: Dict[str, Any]):
        """
        Process completed 5-minute candle bar.
        Triggers breakout or mean-reversion entries between 09:30 AM and 14:30 PM.
        """
        if not self.is_active or self.in_trade or self.trade_closed_today:
            return

        ts = bar["timestamp"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        t = ts.time()

        # Session trading window: 09:30 AM to 14:30 PM
        if t < dtime(9, 30) or t > dtime(14, 30):
            return

        c_open = bar["open"]
        c_high = bar["high"]
        c_low = bar["low"]
        c_close = bar["close"]

        # Strategy Decision Matrix
        if self.is_narrow_cpr:
            # Narrow CPR = Trend Breakout Mode
            # Bullish Breakout: 5m closes decisively above TC
            if c_close > self.top_cpr and c_open <= self.top_cpr:
                self.enter_trade("CALL", c_close, ts, reason="NARROW CPR BULLISH BREAKOUT (Above TC)")
            # Bearish Breakdown: 5m closes decisively below BC
            elif c_close < self.bottom_cpr and c_open >= self.bottom_cpr:
                self.enter_trade("PUT", c_close, ts, reason="NARROW CPR BEARISH BREAKDOWN (Below BC)")
        else:
            # Wide CPR = Range Reversal Mode
            # Bearish Rejection at TC (Upper Wick >= 50% of candle range)
            rng = c_high - c_low
            upper_wick = c_high - max(c_open, c_close)
            lower_wick = min(c_open, c_close) - c_low

            if c_high >= self.top_cpr and c_close < self.top_cpr and rng > 0 and (upper_wick / rng) >= 0.45:
                self.enter_trade("PUT", c_close, ts, reason="WIDE CPR RESISTANCE REJECTION (TC Reversal)")
            # Bullish Bounce at BC (Lower Wick >= 50% of candle range)
            elif c_low <= self.bottom_cpr and c_close > self.bottom_cpr and rng > 0 and (lower_wick / rng) >= 0.45:
                self.enter_trade("CALL", c_close, ts, reason="WIDE CPR SUPPORT BOUNCE (BC Reversal)")

    def enter_trade(self, side: str, spot_price: float, current_dt: datetime, reason: str):
        """Executes option entry with predefined targets and structural SL."""
        if self.in_trade or self.trade_closed_today:
            return

        self.trade_side = side
        self.entry_spot = spot_price
        self.breakeven_locked = False

        if side == "CALL":
            self.opt_type = OptionType.CE
            self.target1_spot = self.r1 if self.is_narrow_cpr else self.pivot
            self.target2_spot = self.r2 if self.is_narrow_cpr else self.top_cpr
            self.sl_spot = self.pivot if self.is_narrow_cpr else (self.bottom_cpr - 15.0)
        else:
            self.opt_type = OptionType.PE
            self.target1_spot = self.s1 if self.is_narrow_cpr else self.pivot
            self.target2_spot = self.s2 if self.is_narrow_cpr else self.bottom_cpr
            self.sl_spot = self.pivot if self.is_narrow_cpr else (self.top_cpr + 15.0)

        # Strike Selection: ATM Weekly Option
        atm = get_atm_strike(spot_price, step=settings.NIFTY_STRIKE_STEP)
        self.opt_strike = atm
        expiry = get_next_weekly_expiry(symbol="NIFTY", as_string=True)
        opt_type_str = "CE" if side == "CALL" else "PE"
        self.opt_symbol = f"NIFTY{expiry}{atm}{opt_type_str}"

        # Fetch option premium quote
        quote = get_live_option_quote(symbol="nifty", strike=atm, opt_type=opt_type_str)
        self.entry_opt_price = quote.get("price", 120.0)
        self.opt_token = str(quote.get("token", "99926000"))

        contract_qty = self.lots * settings.NIFTY_LOT_SIZE
        order = Order(
            order_id=f"CPR_{int(time.time())}",
            instrument=Instrument(symbol=self.opt_symbol, exchange="NFO", lot_size=settings.NIFTY_LOT_SIZE),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=contract_qty,
            price=self.entry_opt_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag=f"CPR_{side}"
        )

        placed = self.broker.place_order(order)
        if placed and placed.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.FILLED]:
            self.in_trade = True
            self.trade_order = placed
            logger.info(f"🚀 [CPR TRADE ENTERED] {reason} | Bought {self.opt_symbol} @ ₹{self.entry_opt_price:.2f} (Qty: {contract_qty})")

            if self.telegram:
                self.telegram.send_notification(
                    f"🏛️ <b>CPR TRADE ENTERED ({reason})</b>\n"
                    f"• Instrument: <b>{self.opt_symbol}</b>\n"
                    f"• Entry Premium: <b>₹{self.entry_opt_price:.2f}</b> (Spot: {spot_price:.1f})\n"
                    f"• Quantity: <b>{contract_qty} ({self.lots} Lots)</b>\n"
                    f"• Stop Loss: <b>{self.sl_spot:.1f}</b>\n"
                    f"• Target 1 (Breakeven Lock): <b>{self.target1_spot:.1f}</b>\n"
                    f"• Target 2 (Take Profit): <b>{self.target2_spot:.1f}</b>"
                )

    def manage_active_trade(self, current_spot: float, current_dt: datetime):
        """Active trade management: Target 1 Breakeven Lock, Target 2 TP, Stop Loss, and EOD square-off."""
        if not self.in_trade or not self.trade_order:
            return

        is_call = self.trade_side == "CALL"
        spot_move = (current_spot - self.entry_spot) if is_call else (self.entry_spot - current_spot)
        target1_dist = abs(self.target1_spot - self.entry_spot)
        target2_dist = abs(self.target2_spot - self.entry_spot)

        # 1. Target 1 Reached: Lock Stop Loss at Breakeven
        if spot_move >= target1_dist and not self.breakeven_locked:
            self.breakeven_locked = True
            logger.info(f"🔒 [Target 1 Touched ({self.target1_spot:.1f})] Ratcheting Stop Loss to Breakeven @ Spot {self.entry_spot:.1f}!")
            if self.telegram:
                self.telegram.send_notification(
                    f"🔒 <b>CPR TARGET 1 REACHED ({self.target1_spot:.1f})!</b>\n"
                    f"Stop Loss ratcheted to <b>BREAKEVEN (₹{self.entry_opt_price:.2f})</b>.\n"
                    f"Trade is now 100% RISK-FREE. Riding to Target 2 (<code>{self.target2_spot:.1f}</code>)."
                )

        # 2. Target 2 Achieved (Take Profit)
        if spot_move >= target2_dist:
            self.close_trade("TARGET 2 (Take Profit)", current_spot, current_dt)
            return

        # 3. Stop Loss / Breakeven Check
        if self.breakeven_locked:
            if (is_call and current_spot <= self.entry_spot) or (not is_call and current_spot >= self.entry_spot):
                self.close_trade("BREAKEVEN EXIT (Risk Neutralized)", current_spot, current_dt)
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
        """Closes active trade and logs forensics to CPR Strategy Ledger."""
        if not self.in_trade or not self.trade_order:
            return

        # Estimate exit option price
        opt_exit_price = self.entry_opt_price
        delta = 0.65 if "TARGET" in reason else 0.45
        spot_pts = (exit_spot - self.entry_spot) if self.trade_side == "CALL" else (self.entry_spot - exit_spot)
        opt_exit_price = max(1.0, round(self.entry_opt_price + (spot_pts * delta), 2))

        # Close position via broker
        exit_order = Order(
            order_id=f"EX_CPR_{int(time.time())}",
            instrument=self.trade_order.instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=self.trade_order.quantity,
            price=opt_exit_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="CPR_EXIT"
        )
        self.broker.place_order(exit_order)

        net_pnl = round((opt_exit_price - self.entry_opt_price) * self.trade_order.quantity, 2)
        self.daily_pnl += net_pnl
        self.risk_manager.evaluate_daily_pnl(self.daily_pnl)

        # Record to CPR Strategy Ledger
        try:
            from core.strategy_ledger import strategy_ledger
            dur_mins = round((current_dt - self.trade_order.placed_at).total_seconds() / 60.0, 1) if hasattr(self.trade_order, "placed_at") and self.trade_order.placed_at else 0.0
            strategy_ledger.record_trade("cpr", {
                "trade_id": f"CPR_{int(time.time())}",
                "strategy": "CPR-Institutional",
                "index": self.symbol,
                "date": current_dt.strftime("%Y-%m-%d"),
                "side": self.trade_side,
                "cpr_width_pct": self.cpr_width_pct,
                "regime": "Narrow Trend" if self.is_narrow_cpr else "Wide Range",
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
                "notes": f"CPR Execution. Closed via {reason}."
            })
        except Exception as e:
            logger.warning(f"Failed to record trade to CPR ledger: {e}")

        logger.info(f"🏁 [CPR TRADE CLOSED: {reason}] PnL: ₹{net_pnl:+,.2f} (Entry: ₹{self.entry_opt_price:.2f} -> Exit: ₹{opt_exit_price:.2f})")

        if self.telegram:
            emoji = "🏆" if net_pnl > 0 else "🛑"
            self.telegram.send_notification(
                f"{emoji} <b>CPR TRADE CLOSED ({reason})</b>\n"
                f"• Instrument: <b>{self.opt_symbol}</b>\n"
                f"• Entry: <b>₹{self.entry_opt_price:.2f}</b> | Exit: <b>₹{opt_exit_price:.2f}</b>\n"
                f"• Net P&L: <b>₹{net_pnl:+,.2f}</b>\n"
                f"• Today's Strategy P&L: <b>₹{self.daily_pnl:+,.2f}</b>"
            )

        self.in_trade = False
        self.trade_order = None
        self.trade_closed_today = True

    def get_status(self) -> Dict[str, Any]:
        """Returns strategy diagnostic status for UI/Dashboard."""
        return {
            "strategy": self.name,
            "cpr_computed": self.cpr_computed,
            "cpr_width_pct": self.cpr_width_pct,
            "is_narrow": self.is_narrow_cpr,
            "top_cpr": self.top_cpr,
            "pivot": self.pivot,
            "bottom_cpr": self.bottom_cpr,
            "in_trade": self.in_trade,
            "breakeven_active": self.breakeven_locked,
            "daily_pnl": self.daily_pnl
        }
