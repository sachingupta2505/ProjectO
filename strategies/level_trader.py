"""
Multi-Asset Support & Resistance Level Trader Strategy (NIFTY 50 & CRUDE OIL).
Monitors predefined support and resistance levels / zones for both NIFTY 50 and CRUDE OIL,
trades volume breakouts and bounces/rejection reversals, and enforces predefined per-trade SL/Target
alongside the strict ₹10,000 Daily Target and ₹10,000 Daily Stop Loss.
"""

import json
import time
import uuid
from pathlib import Path
from collections import deque
from datetime import datetime
from typing import Dict, Any, List, Optional

from core.logger import get_logger
from core.models import (
    Instrument, Order, OrderSide, OrderType, OptionType, Tick
)
from core.option_chain import get_atm_strike, get_next_weekly_expiry
from core.market_data import get_live_option_quote, get_live_nifty_spot, get_live_crude_spot
from core.level_models import TradingLevel, LevelType, LevelAction, load_levels_config
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy
from config.settings import settings
from telegram_bridge.bot import TelegramBridge
from core.charges import calculate_round_trip_charges
from core.trade_analytics import TradeTelemetry, TradeLearningLedger, TradeAutopsy, classify_time_bucket

logger = get_logger("LevelTrader")


class LevelTraderStrategy(BaseStrategy):
    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        levels: Optional[List[TradingLevel]] = None,
        lots: int = 2,
        volume_multiplier: float = 1.3,
        default_tp_pct: float = 0.10,
        default_sl_pct: float = 0.05,
        max_trades_per_day: int = 5,
        adaptive_optimizer: Optional[Any] = None,
        enable_adaptive_learning: bool = True
    ):
        super().__init__("Multi-Asset S/R Level Trader", broker, risk_manager)
        self.levels = levels if levels is not None else load_levels_config()
        self.lots = lots
        self.volume_multiplier = volume_multiplier
        self.default_tp_pct = default_tp_pct
        self.default_sl_pct = default_sl_pct
        self.max_trades_per_day = max_trades_per_day
        self.enable_adaptive_learning = enable_adaptive_learning

        from core.trade_analytics import TradeLearningLedger
        from core.adaptive_tuner import AdaptiveExecutionOptimizer
        self.ledger = TradeLearningLedger()
        self.optimizer = adaptive_optimizer if adaptive_optimizer is not None else AdaptiveExecutionOptimizer(self.ledger)

        # Multi-asset state tracking: keys are "NIFTY" and "CRUDEOIL"
        self.active_trades: Dict[str, Dict[str, Any]] = {}
        self.volume_histories: Dict[str, deque] = {
            "NIFTY": deque(maxlen=20),
            "CRUDEOIL": deque(maxlen=20)
        }
        self.bar_histories: Dict[str, deque] = {
            "NIFTY": deque(maxlen=50),
            "CRUDEOIL": deque(maxlen=50)
        }
        self.trades_today_map: Dict[str, int] = {"NIFTY": 0, "CRUDEOIL": 0}
        self.last_trade_time_map: Dict[str, float] = {"NIFTY": 0.0, "CRUDEOIL": 0.0}
        self.level_cooldown_map: Dict[str, float] = {}

        # Backwards compatibility state holders
        self._last_nifty_trade: Dict[str, Any] = {}
        self.telegram = TelegramBridge(runner=None)
        self.active_trades_file = Path("logs/active_trades.json")

    # ------------------------------------------------------------------
    # Backwards-compatible properties for single-asset callers / tests
    # ------------------------------------------------------------------
    @property
    def active_instrument(self) -> Optional[Instrument]:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"]["instrument"]
        if self.active_trades:
            first_key = next(iter(self.active_trades))
            return self.active_trades[first_key]["instrument"]
        return None

    @property
    def active_option_type(self) -> Optional[OptionType]:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"].get("option_type")
        return None

    @property
    def entry_price(self) -> float:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"]["entry_price"]
        if self.active_trades:
            first_key = next(iter(self.active_trades))
            return self.active_trades[first_key]["entry_price"]
        return self._last_nifty_trade.get("entry_price", 0.0)

    @property
    def target_price(self) -> float:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"]["target_price"]
        if self.active_trades:
            first_key = next(iter(self.active_trades))
            return self.active_trades[first_key]["target_price"]
        return self._last_nifty_trade.get("target_price", 0.0)

    @property
    def sl_price(self) -> float:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"]["sl_price"]
        if self.active_trades:
            first_key = next(iter(self.active_trades))
            return self.active_trades[first_key]["sl_price"]
        return self._last_nifty_trade.get("sl_price", 0.0)

    @property
    def triggered_level(self) -> Optional[TradingLevel]:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"].get("level")
        return None

    @property
    def trade_trigger_reason(self) -> str:
        if "NIFTY" in self.active_trades:
            return self.active_trades["NIFTY"].get("reason", "")
        return self._last_nifty_trade.get("reason", "")

    @property
    def trades_today(self) -> int:
        return sum(self.trades_today_map.values())

    @property
    def lot_size(self) -> int:
        return settings.NIFTY_LOT_SIZE

    def _save_active_trades(self):
        """Persist active trades state to disk for crash recovery and cross-process sync."""
        try:
            data = {}
            for asset_key, t in self.active_trades.items():
                data[asset_key] = {
                    "symbol": t["instrument"].symbol,
                    "exchange": getattr(t["instrument"], "exchange", "MCX" if asset_key == "CRUDEOIL" else "NFO"),
                    "asset_class": getattr(t["instrument"], "asset_class", "COMMODITY" if asset_key == "CRUDEOIL" else "INDEX"),
                    "strike": getattr(t["instrument"], "strike", None),
                    "side": t["side"].value if hasattr(t["side"], "value") else str(t["side"]),
                    "option_type": t["option_type"].value if (t.get("option_type") and hasattr(t["option_type"], "value")) else (str(t["option_type"]) if t.get("option_type") else None),
                    "entry_price": t["entry_price"],
                    "target_price": t["target_price"],
                    "sl_price": t["sl_price"],
                    "initial_sl": t.get("initial_sl", t["sl_price"]),
                    "peak_price": t.get("peak_price", t["entry_price"]),
                    "trough_price": t.get("trough_price", t["entry_price"]),
                    "breakeven_locked": t.get("breakeven_locked", False),
                    "trailing_active": t.get("trailing_active", False),
                    "breakeven_pct": t.get("breakeven_pct", 0.02),
                    "trail_sl_pct": t.get("trail_sl_pct", 0.015),
                    "breakeven_pts": t.get("breakeven_pts", 20.0),
                    "trail_sl_pts": t.get("trail_sl_pts", 15.0),
                    "quantity": t["quantity"],
                    "lot_size": t.get("lot_size", 10 if asset_key == "CRUDEOIL" else 65),
                    "level_id": t["level"].id if t.get("level") else None,
                    "reason": t.get("reason", ""),
                    "entry_time": t.get("entry_time", time.time()),
                    "entry_time_iso": t.get("entry_time_iso", datetime.now().isoformat()),
                    "time_bucket": t.get("time_bucket", "UNKNOWN"),
                    "asset_key": asset_key
                }
            self.active_trades_file.parent.mkdir(parents=True, exist_ok=True)
            self.active_trades_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not save active trades state: {e}")

    def _sync_active_trades_with_broker(self):
        """
        Synchronizes in-memory active trades with open broker positions across restarts.
        Recovers persisted trade parameters or reconstructs them from broker position.
        """
        try:
            open_positions = {sym: p for sym, p in self.broker.get_positions().items() if p.quantity != 0}
        except Exception as e:
            logger.debug(f"Broker get_positions error in sync: {e}")
            open_positions = {}

        saved_trades = {}
        if self.active_trades_file.exists():
            try:
                saved_trades = json.loads(self.active_trades_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug(f"Failed reading active_trades_file: {e}")

        # Sync open broker positions into active_trades
        for sym, pos in open_positions.items():
            asset_key = "CRUDEOIL" if ("CRUDE" in sym.upper()) else "NIFTY"
            if asset_key in self.active_trades:
                continue

            if asset_key in saved_trades and saved_trades[asset_key].get("symbol") == sym:
                s = saved_trades[asset_key]
                lvl = next((l for l in self.levels if l.id == s.get("level_id")), None)
                inst = Instrument(
                    symbol=s["symbol"],
                    exchange=s.get("exchange", "MCX" if asset_key == "CRUDEOIL" else "NFO"),
                    asset_class=s.get("asset_class", "COMMODITY" if asset_key == "CRUDEOIL" else "INDEX"),
                    strike=s.get("strike")
                )
                opt_type = OptionType(s["option_type"]) if s.get("option_type") else None
                self.active_trades[asset_key] = {
                    "instrument": inst,
                    "side": OrderSide(s["side"]),
                    "option_type": opt_type,
                    "entry_price": s["entry_price"],
                    "target_price": s["target_price"],
                    "sl_price": s["sl_price"],
                    "initial_sl": s.get("initial_sl", s["sl_price"]),
                    "peak_price": s.get("peak_price", s["entry_price"]),
                    "trough_price": s.get("trough_price", s["entry_price"]),
                    "breakeven_locked": s.get("breakeven_locked", False),
                    "trailing_active": s.get("trailing_active", False),
                    "breakeven_pct": s.get("breakeven_pct", 0.02),
                    "trail_sl_pct": s.get("trail_sl_pct", 0.015),
                    "breakeven_pts": s.get("breakeven_pts", 20.0),
                    "trail_sl_pts": s.get("trail_sl_pts", 15.0),
                    "quantity": s["quantity"],
                    "lot_size": s.get("lot_size", 10 if asset_key == "CRUDEOIL" else 65),
                    "level": lvl,
                    "reason": s.get("reason", "Restored active trade"),
                    "entry_time": s.get("entry_time", time.time()),
                    "entry_time_iso": s.get("entry_time_iso", datetime.now().isoformat()),
                    "time_bucket": s.get("time_bucket", "UNKNOWN"),
                    "asset_key": asset_key
                }
                logger.info(f"🔄 Restored active trade for {asset_key} ({sym}) from persisted state.")
            else:
                # Reconstruct from broker position
                is_buy = pos.quantity > 0
                side = OrderSide.BUY if is_buy else OrderSide.SELL
                fill_p = pos.average_buy_price if is_buy else pos.average_sell_price
                qty = abs(pos.quantity)
                exch = getattr(pos.instrument, "exchange", "MCX" if asset_key == "CRUDEOIL" else "NFO")
                aclass = "COMMODITY" if asset_key == "CRUDEOIL" else "INDEX"

                # Check broker orders for level tag first
                matched_lvl = None
                try:
                    for o in self.broker.get_orders():
                        if getattr(o.instrument, "symbol", "") == sym and o.tag and o.tag.startswith(f"LVL_{asset_key}_"):
                            lvl_id = o.tag.replace(f"LVL_{asset_key}_", "")
                            matched_lvl = next((l for l in self.levels if l.id == lvl_id), None)
                            if matched_lvl:
                                break
                except Exception:
                    pass

                # Fallback: Find closest level
                if not matched_lvl:
                    for l in self.levels:
                        if l.symbol.upper() == asset_key:
                            if matched_lvl is None or abs(l.price - fill_p) < abs(matched_lvl.price - fill_p):
                                matched_lvl = l

                tp_pts = getattr(matched_lvl, "target_spot_pts", 50.0) if asset_key == "CRUDEOIL" else 10.0
                sl_pts = getattr(matched_lvl, "sl_spot_pts", 25.0) if asset_key == "CRUDEOIL" else 5.0
                target_p = round(fill_p + tp_pts if is_buy else fill_p - tp_pts, 2)
                sl_p = round(fill_p - sl_pts if is_buy else fill_p + sl_pts, 2)

                inst = Instrument(symbol=sym, exchange=exch, asset_class=aclass)
                self.active_trades[asset_key] = {
                    "instrument": inst,
                    "side": side,
                    "option_type": None,
                    "entry_price": fill_p,
                    "target_price": target_p,
                    "sl_price": sl_p,
                    "initial_sl": sl_p,
                    "peak_price": fill_p,
                    "trough_price": fill_p,
                    "breakeven_locked": False,
                    "trailing_active": False,
                    "breakeven_pct": 0.02,
                    "trail_sl_pct": 0.015,
                    "breakeven_pts": 20.0,
                    "trail_sl_pts": 15.0,
                    "quantity": qty,
                    "lot_size": 10 if asset_key == "CRUDEOIL" else 65,
                    "level": matched_lvl,
                    "reason": f"Recovered from open broker position ({sym})",
                    "entry_time": time.time(),
                    "entry_time_iso": datetime.now().isoformat(),
                    "time_bucket": classify_time_bucket(datetime.now()),
                    "asset_key": asset_key
                }
                logger.info(f"🔄 Reconstructed active trade for {asset_key} ({sym} @ ₹{fill_p:,.2f}) from open broker position.")

        # Prune any trades that are no longer open in broker
        for ak in list(self.active_trades.keys()):
            trd_sym = self.active_trades[ak]["instrument"].symbol
            if trd_sym not in open_positions:
                self.active_trades.pop(ak, None)

        self._save_active_trades()

    def initialize(self):
        """Initialize parameters and load latest levels for all assets."""
        if not self.levels:
            self.levels = load_levels_config()
        for k in self.volume_histories:
            self.volume_histories[k].clear()
        for k in self.bar_histories:
            self.bar_histories[k].clear()
        self._sync_active_trades_with_broker()
        self.is_active = True
        nifty_lvls = [l for l in self.levels if l.symbol.upper() == "NIFTY" and l.is_active]
        crude_lvls = [l for l in self.levels if l.symbol.upper() == "CRUDEOIL" and l.is_active]
        logger.info(f"🎯 Strategy '{self.name}' initialized with {len(nifty_lvls)} NIFTY levels and {len(crude_lvls)} CRUDEOIL levels.")

    def reload_levels(self):
        """Hot-reload levels from config file."""
        self.levels = load_levels_config()
        logger.info(f"🔄 S/R Levels reloaded: {len(self.levels)} total levels active.")

    def get_nearest_levels(self, spot: float, symbol: str = "NIFTY") -> Dict[str, Any]:
        """Returns the nearest resistance above and support below for the specified symbol."""
        sym_upper = symbol.upper()
        supports = [lvl for lvl in self.levels if lvl.is_active and lvl.symbol.upper() == sym_upper and lvl.price < spot]
        resistances = [lvl for lvl in self.levels if lvl.is_active and lvl.symbol.upper() == sym_upper and lvl.price > spot]

        nearest_sup = max(supports, key=lambda l: l.price) if supports else None
        nearest_res = min(resistances, key=lambda l: l.price) if resistances else None

        return {
            "symbol": sym_upper,
            "spot": spot,
            "nearest_support": nearest_sup,
            "support_distance": round(spot - nearest_sup.price, 2) if nearest_sup else None,
            "nearest_resistance": nearest_res,
            "resistance_distance": round(nearest_res.price - spot, 2) if nearest_res else None
        }

    def on_tick(self, tick: Tick):
        """
        Monitors real-time tick price for any active positions.
        Enforces dynamic Breakeven Lock, Trailing Stop-Loss, and Predefined Target/SL.
        """
        for asset_key, trade in list(self.active_trades.items()):
            inst: Instrument = trade["instrument"]
            if inst.symbol != tick.symbol:
                continue

            current_price = tick.ltp
            entry_price = trade["entry_price"]
            side = trade["side"]
            target_price = trade["target_price"]

            if side == OrderSide.BUY:
                peak_price = max(trade.get("peak_price", entry_price), current_price)
                trough_price = min(trade.get("trough_price", entry_price), current_price)
                trade["peak_price"] = peak_price
                trade["trough_price"] = trough_price
                pnl_pct = ((current_price - entry_price) / entry_price) * 100.0

                # 1. Breakeven Lock Trigger
                be_threshold_reached = (
                    (pnl_pct >= (trade.get("breakeven_pct", 0.02) * 100.0))
                    if asset_key == "NIFTY"
                    else ((current_price - entry_price) >= trade.get("breakeven_pts", 30.0))
                )
                if be_threshold_reached and not trade.get("breakeven_locked", False):
                    be_sl = round(entry_price + 0.80, 2) if asset_key == "NIFTY" else round(entry_price + 3.5, 2)
                    if be_sl > trade["sl_price"]:
                        trade["sl_price"] = be_sl
                        trade["breakeven_locked"] = True
                        self._save_active_trades()
                        logger.info(f"🛡️ [BREAKEVEN - {asset_key}] SL locked at ₹{be_sl:.2f} (Entry: ₹{entry_price:.2f}, covers charges)")
                        alert_msg = (
                            f"🛡️ <b>Breakeven Activated ({asset_key})!</b>\n\n"
                            f"• Contract: <code>{inst.symbol}</code>\n"
                            f"• Entry Fill: ₹{entry_price:,.2f}\n"
                            f"• Current LTP: ₹{current_price:,.2f} ({pnl_pct:+.2f}%)\n"
                            f"• New Stop Loss: <b>₹{be_sl:,.2f}</b> (Breakeven + Friction Buffer)\n"
                            f"• Capital Risk: <b>₹0.00 (Charges Covered)</b>"
                        )
                        self.telegram.send_notification(alert_msg)

                # 2. Dynamic Trailing Stop-Loss
                trail_threshold_reached = (
                    (peak_price >= entry_price * 1.025)
                    if asset_key == "NIFTY"
                    else ((peak_price - entry_price) >= max(trade.get("breakeven_pts", 20.0) + 5.0, 25.0))
                )
                if trail_threshold_reached:
                    trail_dist = (
                        round(entry_price * trade.get("trail_sl_pct", 0.015), 2)
                        if asset_key == "NIFTY"
                        else trade.get("trail_sl_pts", 15.0)
                    )
                    cand_sl = round(peak_price - trail_dist, 2)
                    if cand_sl > trade["sl_price"]:
                        prev_sl = trade["sl_price"]
                        trade["sl_price"] = cand_sl
                        trade["trailing_active"] = True
                        self._save_active_trades()
                        logger.info(f"📈 [TRAILING SL - {asset_key}] SL ratcheted: ₹{prev_sl:.2f} -> ₹{cand_sl:.2f} (Peak: ₹{peak_price:.2f})")

                # 3. Check Target & SL Exits
                if current_price >= target_price:
                    self._exit_position(current_price, f"🎯 TARGET REACHED (+{pnl_pct:.2f}%)", asset_key=asset_key)
                    return
                elif current_price <= trade["sl_price"]:
                    if trade.get("trailing_active"):
                        lbl = f"📈 TRAILING STOP HIT ({pnl_pct:+.2f}% locked profit)" if pnl_pct >= 0 else f"🛑 TRAILING STOP HIT ({pnl_pct:.2f}%)"
                    elif trade.get("breakeven_locked"):
                        lbl = f"🛡️ BREAKEVEN STOP HIT ({pnl_pct:+.2f}%)"
                    else:
                        lbl = f"🛑 STOP LOSS HIT ({pnl_pct:.2f}%)"
                    self._exit_position(current_price, lbl, asset_key=asset_key)
                    return

            else:  # Short position
                peak_price = min(trade.get("peak_price", entry_price), current_price)
                trough_price = max(trade.get("trough_price", entry_price), current_price)
                trade["peak_price"] = peak_price
                trade["trough_price"] = trough_price
                pnl_pct = ((entry_price - current_price) / entry_price) * 100.0

                # 1. Breakeven Lock Trigger for Short
                be_threshold_reached = (
                    (pnl_pct >= (trade.get("breakeven_pct", 0.02) * 100.0))
                    if asset_key == "NIFTY"
                    else ((entry_price - current_price) >= trade.get("breakeven_pts", 30.0))
                )
                if be_threshold_reached and not trade.get("breakeven_locked", False):
                    be_sl = round(entry_price - 0.80, 2) if asset_key == "NIFTY" else round(entry_price - 3.5, 2)
                    if be_sl < trade["sl_price"]:
                        trade["sl_price"] = be_sl
                        trade["breakeven_locked"] = True
                        self._save_active_trades()
                        logger.info(f"🛡️ [BREAKEVEN - {asset_key}] Short SL locked at ₹{be_sl:.2f} (Entry: ₹{entry_price:.2f}, covers charges)")
                        alert_msg = (
                            f"🛡️ <b>Breakeven Activated ({asset_key})!</b>\n\n"
                            f"• Contract: <code>{inst.symbol}</code> (SHORT)\n"
                            f"• Entry Fill: ₹{entry_price:,.2f}\n"
                            f"• Current LTP: ₹{current_price:,.2f} ({pnl_pct:+.2f}%)\n"
                            f"• New Stop Loss: <b>₹{be_sl:,.2f}</b> (Breakeven + Friction Buffer)\n"
                            f"• Capital Risk: <b>₹0.00 (Charges Covered)</b>"
                        )
                        self.telegram.send_notification(alert_msg)

                # 2. Dynamic Trailing Stop-Loss for Short
                trail_threshold_reached = (
                    (peak_price <= entry_price * 0.975)
                    if asset_key == "NIFTY"
                    else ((entry_price - peak_price) >= 35.0)
                )
                if trail_threshold_reached:
                    trail_dist = (
                        round(entry_price * trade.get("trail_sl_pct", 0.015), 2)
                        if asset_key == "NIFTY"
                        else trade.get("trail_sl_pts", 20.0)
                    )
                    cand_sl = round(peak_price + trail_dist, 2)
                    if cand_sl < trade["sl_price"]:
                        prev_sl = trade["sl_price"]
                        trade["sl_price"] = cand_sl
                        trade["trailing_active"] = True
                        self._save_active_trades()
                        logger.info(f"📈 [TRAILING SL - {asset_key}] Short SL ratcheted: ₹{prev_sl:.2f} -> ₹{cand_sl:.2f} (Trough: ₹{peak_price:.2f})")

                # 3. Check Target & SL Exits for Short
                if current_price <= target_price:
                    self._exit_position(current_price, f"🎯 TARGET REACHED (+{pnl_pct:.2f}%)", asset_key=asset_key)
                    return
                elif current_price >= trade["sl_price"]:
                    if trade.get("trailing_active"):
                        lbl = f"📈 TRAILING STOP HIT ({pnl_pct:+.2f}% locked profit)" if pnl_pct >= 0 else f"🛑 TRAILING STOP HIT ({pnl_pct:.2f}%)"
                    elif trade.get("breakeven_locked"):
                        lbl = f"🛡️ BREAKEVEN STOP HIT ({pnl_pct:+.2f}%)"
                    else:
                        lbl = f"🛑 STOP LOSS HIT ({pnl_pct:.2f}%)"
                    self._exit_position(current_price, lbl, asset_key=asset_key)
                    return

    def on_bar(self, bar: Dict[str, Any]):
        """
        Processes completed candle bar for either NIFTY or CRUDE OIL.
        Detects volume breakout or bounce/reversal against active levels.
        """
        raw_sym = bar.get("symbol", "NIFTY").upper()
        asset_key = "CRUDEOIL" if ("CRUDE" in raw_sym) else "NIFTY"

        vol = float(bar.get("volume", 0.0))
        if vol > 0:
            self.volume_histories[asset_key].append(vol)
        self.bar_histories[asset_key].append(bar)

        history = self.bar_histories[asset_key]
        if len(history) < 2:
            return

        prev_bar = history[-2]
        curr_bar = history[-1]

        prev_close = float(prev_bar["close"])
        curr_close = float(curr_bar["close"])
        curr_open = float(curr_bar["open"])
        curr_high = float(curr_bar["high"])
        curr_low = float(curr_bar["low"])

        vol_hist = self.volume_histories[asset_key]
        avg_volume = sum(vol_hist) / len(vol_hist) if len(vol_hist) >= 3 else vol

        # Don't take a new entry if we already have an open position for this asset
        if asset_key in self.active_trades:
            return

        # Double guard: verify broker open positions directly
        try:
            for s, p in self.broker.get_positions().items():
                if p.quantity != 0:
                    if (asset_key == "CRUDEOIL" and "CRUDE" in s.upper()) or (asset_key == "NIFTY" and "NIFTY" in s.upper()):
                        return
        except Exception:
            pass

        if self.trades_today_map.get(asset_key, 0) >= self.max_trades_per_day:
            return

        # Cooldown: avoid repeated entries on same asset within 3 minutes
        if time.time() - self.last_trade_time_map.get(asset_key, 0.0) < 180:
            return

        # Check Adaptive Learning Regime Blacklist Guard
        if self.enable_adaptive_learning and self.optimizer:
            is_blacklisted, regime_reason = self.optimizer.is_regime_blacklisted()
            if is_blacklisted:
                logger.info(f"🧠 [AI LEARNING GUARD] Suppressing new entry for {asset_key}: {regime_reason}")
                return

        # Filter levels matching this asset
        asset_levels = [lvl for lvl in self.levels if lvl.is_active and lvl.symbol.upper() == asset_key]

        for lvl in asset_levels:
            # Check level cooldown (prevents re-entering a failed level for 15 mins)
            if time.time() - self.level_cooldown_map.get(lvl.id, 0.0) < 900:
                continue

            # Dynamic volume multiplier from learning engine
            eff_vol_mult = lvl.volume_multiplier
            if self.enable_adaptive_learning and self.optimizer:
                eff_vol_mult = self.optimizer.get_effective_volume_multiplier(lvl.name, lvl.volume_multiplier)

            # ----------------------------------------------------------
            # Setup 1: Resistance Breakout with Volume Surge -> BUY CE / LONG
            # ----------------------------------------------------------
            if lvl.action in (LevelAction.BREAKOUT_ONLY.value, LevelAction.BOTH.value):
                ref_price = lvl.range_high if lvl.range_high is not None else lvl.price
                if prev_close < ref_price and curr_close > ref_price:
                    vol_threshold = avg_volume * eff_vol_mult
                    if vol >= vol_threshold or avg_volume <= 0:
                        body = curr_close - curr_open
                        upper_wick = curr_high - curr_close
                        # Require bullish candle (close >= open) and ensure upper wick is not rejecting breakout
                        if body >= 0 and upper_wick <= max(body * 1.2, 5.0 if asset_key == "NIFTY" else 15.0):
                            if lvl.level_type != LevelType.SUPPORT.value:
                                lvl.level_type = LevelType.SUPPORT.value
                                logger.info(f"🔄 Polarity Flip: Level '{lvl.name}' ({ref_price:.1f}) breached upwards -> flipped to SUPPORT")
                            reason = f"🚀 Resistance Breakout Confirmed: {lvl.name} (Closed {curr_close:.2f} > {ref_price:.2f} with Bullish Body {body:.1f} & Volume {vol:,.0f} >= {vol_threshold:,.0f})"
                            self._execute_level_trade(OptionType.CE, lvl, reason, asset_key=asset_key)
                            return

            # ----------------------------------------------------------
            # Setup 2: Support Breakdown with Volume Surge -> BUY PE / SHORT
            # ----------------------------------------------------------
            if lvl.action in (LevelAction.BREAKOUT_ONLY.value, LevelAction.BOTH.value):
                ref_price = lvl.range_low if lvl.range_low is not None else lvl.price
                if prev_close > ref_price and curr_close < ref_price:
                    vol_threshold = avg_volume * eff_vol_mult
                    if vol >= vol_threshold or avg_volume <= 0:
                        body = curr_open - curr_close
                        lower_wick = curr_close - curr_low
                        # Require bearish candle (close <= open) and ensure lower wick is not rejecting breakdown
                        if body >= 0 and lower_wick <= max(body * 1.2, 5.0 if asset_key == "NIFTY" else 15.0):
                            if lvl.level_type != LevelType.RESISTANCE.value:
                                lvl.level_type = LevelType.RESISTANCE.value
                                logger.info(f"🔄 Polarity Flip: Level '{lvl.name}' ({ref_price:.1f}) breached downwards -> flipped to RESISTANCE")
                            reason = f"⚡ Support Breakdown Confirmed: {lvl.name} (Closed {curr_close:.2f} < {ref_price:.2f} with Bearish Body {body:.1f} & Volume {vol:,.0f} >= {vol_threshold:,.0f})"
                            self._execute_level_trade(OptionType.PE, lvl, reason, asset_key=asset_key)
                            return

            # ----------------------------------------------------------
            # Setup 3: Support Sustain / Bullish Bounce -> BUY CE / LONG
            # ----------------------------------------------------------
            if lvl.action in (LevelAction.BOUNCE_ONLY.value, LevelAction.BOTH.value) and lvl.level_type in (LevelType.SUPPORT.value, LevelType.DEMAND_ZONE.value):
                ref_low = lvl.range_low if lvl.range_low is not None else lvl.price
                ref_high = lvl.range_high if lvl.range_high is not None else lvl.price
                tolerance = 5.0 if asset_key == "NIFTY" else 15.0
                min_body = 2.0 if asset_key == "NIFTY" else 6.0
                min_wick = 3.0 if asset_key == "NIFTY" else 8.0

                if curr_low <= (ref_high + tolerance) and curr_close > curr_open and curr_close >= ref_low:
                    lower_wick = min(curr_open, curr_close) - curr_low
                    body = curr_close - curr_open
                    # Require confirmed bullish rejection candle with conviction and volume support
                    if body >= min_body and lower_wick >= min_wick and (vol >= avg_volume * 0.7 if avg_volume > 0 else True):
                        reason = f"🟢 Support Bounce Confirmed: {lvl.name} (Low {curr_low:.2f} rejected with {lower_wick:.1f}pt wick, closed {curr_close:.2f})"
                        self._execute_level_trade(OptionType.CE, lvl, reason, asset_key=asset_key)
                        return

            # ----------------------------------------------------------
            # Setup 4: Resistance Sustain / Bearish Rejection -> BUY PE / SHORT
            # ----------------------------------------------------------
            if lvl.action in (LevelAction.BOUNCE_ONLY.value, LevelAction.BOTH.value) and lvl.level_type in (LevelType.RESISTANCE.value, LevelType.SUPPLY_ZONE.value):
                ref_low = lvl.range_low if lvl.range_low is not None else lvl.price
                ref_high = lvl.range_high if lvl.range_high is not None else lvl.price
                tolerance = 5.0 if asset_key == "NIFTY" else 15.0
                min_body = 2.0 if asset_key == "NIFTY" else 6.0
                min_wick = 3.0 if asset_key == "NIFTY" else 8.0

                if curr_high >= (ref_low - tolerance) and curr_close < curr_open and curr_close <= ref_high:
                    upper_wick = curr_high - max(curr_open, curr_close)
                    body = curr_open - curr_close
                    # Require confirmed bearish rejection candle with conviction and volume support
                    if body >= min_body and upper_wick >= min_wick and (vol >= avg_volume * 0.7 if avg_volume > 0 else True):
                        reason = f"🔴 Resistance Rejection Confirmed: {lvl.name} (High {curr_high:.2f} rejected with {upper_wick:.1f}pt wick, closed {curr_close:.2f})"
                        self._execute_level_trade(OptionType.PE, lvl, reason, asset_key=asset_key)
                        return

    def _execute_level_trade(self, option_type: OptionType, level: TradingLevel, reason: str, asset_key: str = "NIFTY"):
        """Places ATM option buy trade or commodity trade with predefined Target & Stop Loss."""
        tp_pct = level.target_pct or self.default_tp_pct
        sl_pct = level.sl_pct or self.default_sl_pct

        if asset_key == "NIFTY":
            spot_data = get_live_nifty_spot()
            spot = spot_data.get("spot", 23950.0)
            atm_strike = get_atm_strike(spot)

            quote = get_live_option_quote("nifty", atm_strike, option_type.value)
            entry_price = float(quote.get("ltp", 75.0 if option_type == OptionType.PE else 105.0))
            if entry_price <= 0:
                entry_price = 75.0

            lot_sz = settings.NIFTY_LOT_SIZE
            quantity = self.lots * lot_sz
            target_price = round(entry_price * (1.0 + tp_pct), 2)
            sl_price = round(entry_price * (1.0 - sl_pct), 2)

            expiry_str = quote.get("expiry", "2026-09-08")
            try:
                expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            except Exception:
                expiry_dt = get_next_weekly_expiry()

            symbol = quote.get("symbol", f"NIFTY{atm_strike}{option_type.value}")
            display_name = quote.get("display_name", f"NIFTY {atm_strike} {option_type.value}")

            instrument = Instrument(
                symbol=symbol,
                exchange="NFO",
                strike=atm_strike,
                expiry=expiry_dt,
                option_type=option_type,
                lot_size=lot_sz,
                asset_class="INDEX"
            )
            order_side = OrderSide.BUY

        else:  # CRUDEOIL
            crude_data = get_live_crude_spot()
            spot = crude_data.get("spot", 8570.0)
            entry_price = spot
            lot_sz = getattr(settings, "CRUDE_LOT_SIZE", 10)
            crude_lots = getattr(self, "crude_lots", None) or getattr(settings, "DEFAULT_CRUDE_LOTS", 1)
            quantity = crude_lots * lot_sz

            # For Crude, trade directional Futures / CFDs or ATM
            is_bullish = (option_type == OptionType.CE)
            order_side = OrderSide.BUY if is_bullish else OrderSide.SELL

            pts_target = level.target_spot_pts if level.target_spot_pts is not None else 70.0
            pts_sl = level.sl_spot_pts if level.sl_spot_pts is not None else 35.0

            if is_bullish:
                target_price = round(entry_price + pts_target, 2)
                sl_price = round(entry_price - pts_sl, 2)
            else:
                target_price = round(entry_price - pts_target, 2)
                sl_price = round(entry_price + pts_sl, 2)

            symbol = f"CRUDEOIL_{datetime.now().strftime('%b').upper()}FUT"
            display_name = f"CRUDE OIL {symbol} ({'LONG' if is_bullish else 'SHORT'})"

            instrument = Instrument(
                symbol=symbol,
                exchange="MCX",
                lot_size=lot_sz,
                tick_size=1.0,
                asset_class="COMMODITY"
            )

        order = Order(
            order_id=f"LVL_{uuid.uuid4().hex[:6].upper()}",
            instrument=instrument,
            side=order_side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=entry_price,
            tag=f"LVL_{asset_key}_{level.id}"
        )

        # Pre-trade RMS check
        valid, msg = self.risk_manager.validate_new_order(order)
        if not valid:
            logger.warning(f"RMS blocked level trade: {msg}")
            return

        placed = self.broker.place_order(order)
        fill_price = placed.average_price or entry_price

        # Calibrate target & stop-loss precisely from actual fill price
        if asset_key == "NIFTY":
            target_price = round(fill_price * (1.0 + tp_pct), 2)
            sl_price = round(fill_price * (1.0 - sl_pct), 2)
        else:
            if is_bullish:
                target_price = round(fill_price + pts_target, 2)
                sl_price = round(fill_price - pts_sl, 2)
            else:
                target_price = round(fill_price - pts_target, 2)
                sl_price = round(fill_price + pts_sl, 2)

        now_dt = datetime.now()
        cal_be_pct = getattr(level, "breakeven_pct", 0.02)
        cal_be_pts = getattr(level, "breakeven_pts", 20.0)
        if self.enable_adaptive_learning and self.optimizer:
            cal_be_pct, cal_be_pts = self.optimizer.get_calibrated_breakeven(asset_key, cal_be_pct, cal_be_pts)

        trade_info = {
            "order": placed,
            "instrument": instrument,
            "side": order_side,
            "option_type": option_type,
            "entry_price": fill_price,
            "target_price": target_price,
            "sl_price": sl_price,
            "initial_sl": sl_price,
            "peak_price": fill_price,
            "trough_price": fill_price,
            "breakeven_locked": False,
            "trailing_active": False,
            "breakeven_pct": cal_be_pct,
            "trail_sl_pct": getattr(level, "trail_sl_pct", 0.015),
            "breakeven_pts": cal_be_pts,
            "trail_sl_pts": getattr(level, "trail_sl_pts", 15.0),
            "quantity": quantity,
            "lot_size": lot_sz,
            "level": level,
            "reason": reason,
            "entry_time": time.time(),
            "entry_time_iso": now_dt.isoformat(),
            "time_bucket": classify_time_bucket(now_dt),
            "asset_key": asset_key
        }

        self.active_trades[asset_key] = trade_info
        if asset_key == "NIFTY":
            self._last_nifty_trade = trade_info
        self._save_active_trades()

        self.trades_today_map[asset_key] = self.trades_today_map.get(asset_key, 0) + 1
        self.last_trade_time_map[asset_key] = time.time()

        target_gain = round(abs(target_price - fill_price) * quantity, 2)
        sl_loss = round(abs(fill_price - sl_price) * quantity, 2)

        logger.info(f"⚡ [LEVEL TRADE - {asset_key}] {order_side.value} {quantity} {symbol} @ ₹{fill_price:.2f} | {reason}")
        logger.info(f"   Target: ₹{target_price:.2f} (+₹{target_gain:.2f}) | SL: ₹{sl_price:.2f} (-₹{sl_loss:.2f})")

        rr_ratio = round(target_gain / sl_loss, 1) if sl_loss > 0 else 2.0
        alert_msg = (
            f"🎯 <b>S/R Trade Triggered ({asset_key})!</b>\n\n"
            f"• <b>Trigger Reason:</b> {reason}\n"
            f"• <b>Contract:</b> <b>{display_name}</b> (<code>{symbol}</code>)\n"
            f"• <b>Spot Reference:</b> ₹{spot:,.2f}\n"
            f"• <b>Position:</b> {order_side.value} {quantity} Qty ({self.lots} Lot)\n"
            f"• <b>Entry Price:</b> ₹{fill_price:.2f} (Value: ₹{fill_price * quantity:,.2f})\n\n"
            f"🎯 <b>Target:</b> ₹{target_price:.2f} (+₹{target_gain:,.2f})\n"
            f"🛑 <b>Initial Stop Loss:</b> ₹{sl_price:.2f} (-₹{sl_loss:,.2f})\n"
            f"⚖️ <b>Risk:Reward:</b> 1:{rr_ratio}\n"
            f"🛡️ <b>Protection:</b> Auto-Breakeven Lock & Dynamic Trailing SL Active\n\n"
            f"🛡️ <i>Daily Guardrails Active: Max Target +₹{getattr(settings, 'MAX_DAILY_PROFIT', 10000.0):,.0f} | Max SL -₹{getattr(settings, 'MAX_DAILY_LOSS', 5000.0):,.0f}</i>"
        )
        self.telegram.send_notification(alert_msg)

    def _exit_position(self, exit_price: float, reason: str, asset_key: str = "NIFTY"):
        """Exits active position for the given asset and enforces RMS circuit."""
        trade = self.active_trades.pop(asset_key, None)
        if not trade:
            return
        self._save_active_trades()

        instrument: Instrument = trade["instrument"]
        quantity = trade["quantity"]
        entry_price = trade["entry_price"]
        side = trade["side"]

        # Opposing exit side
        exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        exit_order = Order(
            order_id=f"LVL_EXIT_{uuid.uuid4().hex[:6].upper()}",
            instrument=instrument,
            side=exit_side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=exit_price,
            tag=f"LVL_EXIT_{asset_key}"
        )
        self.broker.place_order(exit_order)

        # Calculate exact round-trip charges for this trade
        rt = calculate_round_trip_charges(
            buy_price=entry_price if side == OrderSide.BUY else exit_price,
            sell_price=exit_price if side == OrderSide.BUY else entry_price,
            quantity=quantity,
            exchange=getattr(instrument, "exchange", "NFO"),
            asset_class=getattr(instrument, "asset_class", "INDEX")
        )
        trade_charges = rt["total_charges"]

        if side == OrderSide.BUY:
            gross_pnl = round((exit_price - entry_price) * quantity, 2)
        else:
            gross_pnl = round((entry_price - exit_price) * quantity, 2)

        net_pnl = round(gross_pnl - trade_charges, 2)
        invested = entry_price * quantity
        net_pnl_pct = round((net_pnl / invested * 100.0), 2) if invested > 0 else 0.0

        # --------------------------------------------------------------
        # Continuous Learning Engine: Excursion Telemetry & Autopsy
        # --------------------------------------------------------------
        now_dt = datetime.now()
        exit_time_iso = now_dt.isoformat()
        entry_time_sec = trade.get("entry_time", time.time())
        duration_sec = time.time() - entry_time_sec
        side_str = "BUY" if side == OrderSide.BUY else "SELL"

        if side == OrderSide.BUY:
            mfe_points = max(0.0, trade.get("peak_price", entry_price) - entry_price)
            mae_points = max(0.0, entry_price - trade.get("trough_price", entry_price))
            target_pts = abs(trade.get("target_price", entry_price) - entry_price)
            sl_pts = abs(entry_price - trade.get("initial_sl", entry_price))
        else:
            mfe_points = max(0.0, entry_price - trade.get("peak_price", entry_price))
            mae_points = max(0.0, trade.get("trough_price", entry_price) - entry_price)
            target_pts = abs(entry_price - trade.get("target_price", entry_price))
            sl_pts = abs(trade.get("initial_sl", entry_price) - entry_price)

        mfe_pnl = round(mfe_points * quantity, 2)
        mae_pnl = round(mae_points * quantity, 2)
        eff_ratio = round(gross_pnl / mfe_pnl, 2) if (mfe_pnl > 0 and gross_pnl > 0) else 0.0

        autopsy = TradeAutopsy.diagnose(
            side=side_str,
            entry_price=entry_price,
            exit_price=exit_price,
            mfe_points=mfe_points,
            mae_points=mae_points,
            target_points=target_pts,
            sl_points=sl_pts,
            exit_reason=reason,
            time_bucket=trade.get("time_bucket", classify_time_bucket(now_dt))
        )

        lvl_obj = trade.get("level")
        lvl_name = getattr(lvl_obj, "name", "QuickTrigger/Manual")
        if lvl_obj and hasattr(lvl_obj, "id") and "STOP LOSS" in reason.upper():
            self.level_cooldown_map[lvl_obj.id] = time.time()
            logger.info(f"⏳ Level '{lvl_name}' placed on 15-min cooldown after stop loss.")

        telemetry = TradeTelemetry(
            trade_id=exit_order.order_id,
            symbol=instrument.symbol,
            asset_key=asset_key,
            side=side_str,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=trade.get("entry_time_iso", now_dt.isoformat()),
            exit_time=exit_time_iso,
            duration_seconds=round(duration_sec, 1),
            time_bucket=trade.get("time_bucket", classify_time_bucket(now_dt)),
            mfe_price=round(trade.get("peak_price", entry_price), 2),
            mfe_points=round(mfe_points, 2),
            mfe_pnl=mfe_pnl,
            mae_price=round(trade.get("trough_price", entry_price), 2),
            mae_points=round(mae_points, 2),
            mae_pnl=mae_pnl,
            efficiency_ratio=eff_ratio,
            gross_pnl=gross_pnl,
            charges=trade_charges,
            net_pnl=net_pnl,
            exit_reason=reason,
            level_name=lvl_name,
            autopsy_diagnosis=autopsy
        )

        try:
            TradeLearningLedger().record_trade(telemetry)
        except Exception as e:
            logger.error(f"Failed to record trade to learning ledger: {e}")

        pnl_icon = "🟢" if net_pnl >= 0 else "🔴"
        logger.info(f"🏁 [LEVEL EXIT - {asset_key}] {exit_side.value} {quantity} {instrument.symbol} @ ₹{exit_price:.2f} | Gross: ₹{gross_pnl:+,.2f} | Charges: -₹{trade_charges:.2f} | Net: ₹{net_pnl:+,.2f} ({net_pnl_pct:+.2f}%) | {reason}")
        logger.info(f"   [AUTOPSY] {autopsy} | MFE: +{mfe_points:.1f} pts | MAE: -{mae_points:.1f} pts")

        alert_msg = (
            f"🏁 <b>{asset_key} Level Trade Closed!</b>\n\n"
            f"• <b>Status:</b> {reason}\n"
            f"• <b>Contract:</b> <code>{instrument.symbol}</code>\n"
            f"• <b>Exit Price:</b> ₹{exit_price:.2f} | <b>Entry Price:</b> ₹{entry_price:.2f}\n"
            f"• <b>Gross PnL:</b> ₹{gross_pnl:+,.2f}\n"
            f"• <b>Transaction Charges:</b> -₹{trade_charges:.2f} (Brokerage + STT/CTT + Taxes)\n"
            f"• <b>Net In-Pocket PnL:</b> {pnl_icon} <b>₹{net_pnl:+,.2f} ({net_pnl_pct:+.2f}%)</b>\n\n"
            f"🔬 <b>AI Trade Autopsy:</b>\n<i>{autopsy}</i>\n"
            f"• <b>MFE (Peak Profit):</b> +{mfe_points:.1f} pts (+₹{mfe_pnl:,.0f}) | <b>MAE (Max Drawdown):</b> -{mae_points:.1f} pts\n\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S IST')}"
        )
        self.telegram.send_notification(alert_msg)

        # Check RMS Daily Target (+₹10,000) / Max Loss (-₹5,000) on NET PnL
        margins = self.broker.get_margins()
        net_total_pnl = margins.get("total_pnl", 0.0)  # Net after all charges
        breached, rms_reason = self.risk_manager.evaluate_daily_pnl(net_total_pnl)
        if breached:
            logger.critical(f"🚨 RMS HALT TRIGGERED: {rms_reason}")
            self.broker.square_off_all_positions()
            self.active_trades.clear()
            self.telegram.send_notification(f"🚨 <b>DAILY LIMIT REACHED!</b>\n{rms_reason}\nTrading halted for the day.")

    def check_entry_conditions(self, current_dt: datetime, spot_price: float):
        pass

    def check_exit_conditions(self, current_dt: datetime):
        """Auto square-off positions past market close thresholds."""
        for asset_key in list(self.active_trades.keys()):
            if self.risk_manager.check_time_for_square_off(current_dt, symbol=asset_key):
                trade = self.active_trades[asset_key]
                exit_px = trade["entry_price"]  # Market close fill
                self._exit_position(exit_px, f"⏰ Market Session Auto Square-Off ({asset_key})", asset_key=asset_key)

    def get_status(self) -> Dict[str, Any]:
        details = {}
        for k, v in self.active_trades.items():
            details[k] = {
                "symbol": v["instrument"].symbol,
                "side": v["side"].value,
                "entry_price": v["entry_price"],
                "target_price": v["target_price"],
                "sl_price": v["sl_price"],
                "peak_price": v.get("peak_price", v["entry_price"]),
                "breakeven_locked": v.get("breakeven_locked", False),
                "trailing_active": v.get("trailing_active", False),
                "reason": v.get("reason", "")
            }
        return {
            "name": self.name,
            "is_active": self.is_active,
            "active_positions_count": len(self.active_trades),
            "active_positions": {k: v["instrument"].symbol for k, v in self.active_trades.items()},
            "active_trade_details": details,
            "trades_today": self.trades_today,
            "trades_by_asset": self.trades_today_map,
            "active_levels_count": sum(1 for l in self.levels if l.is_active),
            "daily_target": self.risk_manager.max_daily_profit,
            "daily_stop_loss": self.risk_manager.max_daily_loss,
            "kill_switch_active": self.risk_manager.kill_switch_active
        }
