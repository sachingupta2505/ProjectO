"""
Real-Time Market Data Provider for NIFTY 50 and Indices.
Continuously fetches live spot prices, day high/low, and market breadth
with high-frequency caching and multiple fallback providers.
"""

import time
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional
from core.logger import get_logger
from config.settings import settings

logger = get_logger("MarketData")

# In-memory cache
_CACHE: Dict[str, Any] = {}
_LAST_FETCH_TIME: float = 0.0
_CACHE_TTL: float = 1.5  # Ultra-fast refresh (1.5 seconds)

_HTTP_SESSION: Optional[requests.Session] = None


def _get_http_session() -> requests.Session:
    """Singleton persistent HTTP session with connection pooling for sub-100ms latency."""
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        _HTTP_SESSION = requests.Session()
        _HTTP_SESSION.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate"
        })
    return _HTTP_SESSION


def get_live_nifty_spot(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Returns real-time NIFTY 50 spot price data with sub-100ms response time.
    Cached for 1.5 seconds to balance responsiveness and bandwidth.
    """
    global _CACHE, _LAST_FETCH_TIME
    now_ts = time.time()

    if not force_refresh and _CACHE and (now_ts - _LAST_FETCH_TIME < _CACHE_TTL):
        return _CACHE

    # Attempt 1: Ultra-fast derivatives live quote feed (~60ms latency, zero cookie handshake)
    try:
        session = _get_http_session()
        res = session.get("https://groww.in/v1/api/option_chain_service/v1/option_chain/derivatives/nifty", timeout=2.5)
        if res.status_code == 200:
            data = res.json()
            live = data.get("livePrice", {})
            spot = float(live.get("value", 0.0))
            if spot > 0:
                change = float(live.get("dayChange", 0.0))
                pct_change = float(live.get("dayChangePerc", 0.0))
                high = float(live.get("high", spot))
                low = float(live.get("low", spot))
                open_p = float(live.get("open", spot))
                prev_close = float(live.get("close", spot))

                _CACHE = {
                    "symbol": "NIFTY 50",
                    "spot": spot,
                    "change": change,
                    "pct_change": pct_change,
                    "high": high,
                    "low": low,
                    "open": open_p,
                    "prev_close": prev_close,
                    "advances": "N/A",
                    "declines": "N/A",
                    "timestamp": datetime.now(),
                    "is_live": True,
                    "source": "NSE Fast Feed"
                }
                _LAST_FETCH_TIME = now_ts
                return _CACHE
    except Exception as e:
        logger.debug(f"Fast feed spot fetch failed: {e}")

    # Attempt 2: NSE All Indices fallback
    try:
        session = _get_http_session()
        res = session.get("https://www.nseindia.com/api/allIndices", timeout=3.0)
        if res.status_code == 200:
            data = res.json().get("data", [])
            for item in data:
                if item.get("index") == "NIFTY 50":
                    spot = float(item.get("last", 24000.0))
                    change = float(item.get("variation", 0.0))
                    pct_change = float(item.get("percentChange", 0.0))
                    high = float(item.get("high", spot))
                    low = float(item.get("low", spot))
                    open_p = float(item.get("open", spot))
                    prev_close = float(item.get("previousClose", spot))
                    adv = item.get("advances", "N/A")
                    dec = item.get("declines", "N/A")

                    _CACHE = {
                        "symbol": "NIFTY 50",
                        "spot": spot,
                        "change": change,
                        "pct_change": pct_change,
                        "high": high,
                        "low": low,
                        "open": open_p,
                        "prev_close": prev_close,
                        "advances": adv,
                        "declines": dec,
                        "timestamp": datetime.now(),
                        "is_live": True,
                        "source": "NSE"
                    }
                    _LAST_FETCH_TIME = now_ts
                    return _CACHE
    except Exception as e:
        logger.debug(f"NSE Live fetch failed: {e}")

    # Attempt 2: If we have previous cached data, return it with timestamp update
    if _CACHE:
        return _CACHE

    # Fallback if market closed / offline: Reasonable default benchmark
    fallback = {
        "symbol": "NIFTY 50",
        "spot": 23955.0,
        "change": 0.0,
        "pct_change": 0.0,
        "high": 24005.0,
        "low": 23895.0,
        "open": 23910.0,
        "prev_close": 23955.0,
        "advances": "29",
        "declines": "21",
        "timestamp": datetime.now(),
        "is_live": False,
        "source": "Fallback"
    }
    _CACHE = fallback
    _LAST_FETCH_TIME = now_ts
    return _CACHE


def get_live_banknifty_spot() -> Dict[str, Any]:
    """Returns real-time BANKNIFTY spot price data."""
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        session.get("https://www.nseindia.com", timeout=3)
        res = session.get("https://www.nseindia.com/api/allIndices", timeout=4)
        if res.status_code == 200:
            data = res.json().get("data", [])
            for item in data:
                if item.get("index") == "NIFTY BANK":
                    return {
                        "symbol": "BANKNIFTY",
                        "spot": float(item.get("last", 57600.0)),
                        "change": float(item.get("variation", 0.0)),
                        "pct_change": float(item.get("percentChange", 0.0)),
                        "high": float(item.get("high", 0.0)),
                        "low": float(item.get("low", 0.0)),
                        "timestamp": datetime.now(),
                        "is_live": True
                    }
    except Exception:
        pass
    return {
        "symbol": "BANKNIFTY",
        "spot": 57600.0,
        "change": 0.0,
        "pct_change": 0.0,
        "timestamp": datetime.now(),
        "is_live": False
    }


# ----------------------------------------------------------------------
# Real-Time Option Chain & Live Option Quotes
# ----------------------------------------------------------------------

_OC_CACHE: Dict[str, Any] = {}
_OC_LAST_FETCH: float = 0.0
_OC_CACHE_TTL: float = 2.0


def get_live_option_chain(symbol: str = "nifty", force_refresh: bool = False) -> Dict[str, Any]:
    """
    Fetches real-time Indian stock exchange option chain data with genuine market LTPs,
    open interest, lot size, expiry, and strike details.
    Cached for 2 seconds to avoid excessive network overhead.
    """
    global _OC_CACHE, _OC_LAST_FETCH
    now_ts = time.time()
    raw_sym = symbol.lower().replace(" ", "").replace("_", "-")
    if raw_sym in ("banknifty", "niftybank", "bank-nifty"):
        slug = "nifty-bank"
    elif raw_sym in ("nifty", "nifty50", "nifty-50"):
        slug = "nifty"
    else:
        slug = raw_sym
    cache_key = slug

    if not force_refresh and cache_key in _OC_CACHE and (now_ts - _OC_LAST_FETCH < _OC_CACHE_TTL):
        return _OC_CACHE[cache_key]

    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*"
        })
        url = f"https://groww.in/v1/api/option_chain_service/v1/option_chain/derivatives/{slug}"
        res = session.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            oc = data.get("optionChain", {})
            exp_dto = oc.get("expiryDetailsDto", {})
            live_dto = data.get("livePrice", {})
            current_exp = exp_dto.get("currentExpiry")
            lot_size = exp_dto.get("expiryLotSize", 65)
            spot = float(live_dto.get("value", 23950.0))

            strikes: Dict[int, Dict[str, Any]] = {}
            for row in oc.get("optionChains", []):
                stk = int(round(row.get("strikePrice", 0) / 100.0))
                strikes[stk] = {
                    "strike": stk,
                    "ce": row.get("callOption") or {},
                    "pe": row.get("putOption") or {}
                }

            result = {
                "symbol": symbol.upper(),
                "spot": spot,
                "current_expiry": current_exp,
                "expiry_dates": exp_dto.get("expiryDates", []),
                "lot_size": lot_size,
                "strikes": strikes,
                "timestamp": datetime.now(),
                "is_live": True
            }
            _OC_CACHE[cache_key] = result
            _OC_LAST_FETCH = now_ts
            return result
    except Exception as e:
        logger.warning(f"Live option chain query failed for {symbol}: {e}")

    if cache_key in _OC_CACHE:
        return _OC_CACHE[cache_key]

    return {
        "symbol": symbol.upper(),
        "spot": 23950.0,
        "current_expiry": "2026-09-08",
        "expiry_dates": ["2026-09-08"],
        "lot_size": 65,
        "strikes": {},
        "timestamp": datetime.now(),
        "is_live": False
    }


def get_live_option_quote(symbol: str = "nifty", strike: Optional[int] = None, opt_type: str = "PE") -> Dict[str, Any]:
    """
    Returns the real-time quote for a specific strike and option type (CE or PE).
    If strike is None, automatically resolves the ATM strike.
    """
    oc_data = get_live_option_chain(symbol)
    spot = oc_data.get("spot", 23950.0)

    strike_step = 100 if "bank" in symbol.lower() else 50
    if strike is None:
        strike = int(round(spot / strike_step) * strike_step)

    strikes = oc_data.get("strikes", {})
    stk_data = strikes.get(strike)

    opt_key = "pe" if opt_type.upper() == "PE" else "ce"
    if stk_data and stk_data.get(opt_key):
        opt_info = stk_data[opt_key]
        return {
            "symbol": opt_info.get("growwContractId", f"NIFTY{strike}{opt_type.upper()}"),
            "display_name": opt_info.get("longDisplayName", f"NIFTY {strike} {opt_type.upper()}"),
            "strike": strike,
            "option_type": opt_type.upper(),
            "ltp": float(opt_info.get("ltp", 0.0)),
            "high": float(opt_info.get("high", 0.0)),
            "low": float(opt_info.get("low", 0.0)),
            "open": float(opt_info.get("open", 0.0)),
            "close": float(opt_info.get("close", 0.0)),
            "volume": int(opt_info.get("volume", 0)),
            "oi": int(opt_info.get("openInterest", 0)),
            "lot_size": oc_data.get("lot_size", 65),
            "spot": spot,
            "expiry": oc_data.get("current_expiry", "2026-09-08"),
            "is_live": True
        }

    return {
        "symbol": f"NIFTY{strike}{opt_type.upper()}",
        "display_name": f"NIFTY {strike} {opt_type.upper()}",
        "strike": strike,
        "option_type": opt_type.upper(),
        "ltp": 75.0 if opt_type.upper() == "PE" else 105.0,
        "high": 0.0,
        "low": 0.0,
        "open": 0.0,
        "close": 0.0,
        "volume": 0,
        "oi": 0,
        "lot_size": 65,
        "spot": spot,
        "expiry": oc_data.get("current_expiry", "2026-09-08"),
        "is_live": False
    }


_CRUDE_CACHE: Dict[str, Any] = {}
_CRUDE_LAST_FETCH = 0.0
_CRUDE_CACHE_TTL = 1.0

# ── Live USDINR cache (separate from crude cache, refreshed every 30s) ────────
_FX_CACHE: Dict[str, float] = {}
_FX_LAST_FETCH: float = 0.0
_FX_CACHE_TTL: float = 30.0

# ── Self-calibrating MCX basis (updated whenever Angel One WebSocket is live) ──
_CALIBRATED_BASIS: Optional[float] = None   # MCX_actual - WTI*FX


def _get_live_usdinr() -> float:
    """
    Returns live USD/INR rate.  Uses open.er-api.com (free, updates hourly)
    which always returns the current market rate — unlike Yahoo USDINR=X which
    is stale during Indian market hours.
    Falls back to Yahoo as secondary, then hardcoded 94.5.
    Cached for 30 seconds to avoid hammering the free tier.
    """
    global _FX_CACHE, _FX_LAST_FETCH
    now_ts = time.time()
    if _FX_CACHE and (now_ts - _FX_LAST_FETCH < _FX_CACHE_TTL):
        return _FX_CACHE.get("inr", 94.5)

    session = _get_http_session()

    # Primary: open.er-api.com (free, reliable, no key needed)
    try:
        r = session.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        if r.status_code == 200:
            data = r.json()
            inr = float(data["rates"]["INR"])
            _FX_CACHE = {"inr": inr}
            _FX_LAST_FETCH = now_ts
            return inr
    except Exception:
        pass

    # Secondary: Yahoo Finance USDINR=X
    try:
        r = session.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X?interval=1d&range=1d",
            timeout=5,
        )
        if r.status_code == 200:
            meta = r.json()["chart"]["result"][0]["meta"]
            inr = float(meta.get("regularMarketPrice", 94.5))
            _FX_CACHE = {"inr": inr}
            _FX_LAST_FETCH = now_ts
            return inr
    except Exception:
        pass

    # Last resort: return stale value or default
    return _FX_CACHE.get("inr", 94.5)


def get_live_crude_spot(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Returns real-time MCX Crude Oil price in INR per barrel.

    Priority:
      1. Angel One WebSocket LTP  — actual MCX exchange price (most accurate)
      2. Yahoo Finance 1m WTI + live USDINR + self-calibrated MCX basis
      3. Stale cache
      4. Hardcoded fallback

    When source (1) is active, we also calibrate the MCX basis so that
    source (2) remains accurate during brief WebSocket disconnects.
    """
    global _CRUDE_CACHE, _CRUDE_LAST_FETCH, _CALIBRATED_BASIS
    now_ts = time.time()
    if not force_refresh and _CRUDE_CACHE and (now_ts - _CRUDE_LAST_FETCH < _CRUDE_CACHE_TTL):
        return _CRUDE_CACHE

    session = _get_http_session()

    # ── Fetch WTI price (1-minute resolution, most current) ──────────────────
    wti_usd: Optional[float] = None
    prev_close_usd: Optional[float] = None
    day_high_usd: Optional[float] = None
    day_low_usd: Optional[float] = None

    try:
        r1 = session.get(
            "https://query2.finance.yahoo.com/v8/finance/chart/CL=F?interval=1m&range=1d",
            timeout=5.0,
        )
        if r1.status_code == 200:
            meta_cl = r1.json()["chart"]["result"][0]["meta"]
            wti_usd = float(meta_cl.get("regularMarketPrice", 0) or 0)
            prev_close_usd = float(meta_cl.get("chartPreviousClose", wti_usd) or wti_usd)
            day_high_usd = float(meta_cl.get("regularMarketDayHigh", wti_usd) or wti_usd)
            day_low_usd = float(meta_cl.get("regularMarketDayLow", wti_usd) or wti_usd)
    except Exception as e:
        logger.warning(f"Yahoo WTI fetch failed: {e}")

    # ── Fetch live USDINR ─────────────────────────────────────────────────────
    fx_rate = _get_live_usdinr()

    # ── Try Angel One WebSocket for actual MCX LTP ───────────────────────────
    # Check BOTH sources:
    #  (a) shared file written by main.py's WS thread — works across processes (Streamlit etc.)
    #  (b) in-process angel_feed singleton — only works if THIS process started the feed
    source = "Yahoo+LiveFX"
    angel_ltp: Optional[float] = None

    # (a) Read from shared file first (cross-process safe)
    try:
        import json as _json
        _ltp_file = "logs/angel_ltp.json"
        with open(_ltp_file, "r") as _f:
            _payload = _json.load(_f)
        _age = now_ts - _payload.get("ts", 0)
        if _age < 10:  # Fresh within 10 seconds
            angel_ltp = float(_payload["ltp"])
    except Exception:
        pass

    # (b) In-process WebSocket (if this process started the feed — e.g. main.py itself)
    if angel_ltp is None:
        try:
            from core.angel_feed import angel_feed
            angel_ltp = angel_feed.get_crude_ltp()
        except Exception:
            pass

    if angel_ltp and angel_ltp > 0:
        # BEST CASE: actual MCX exchange price
        inr_spot = angel_ltp
        source = "AngelOne-WS"

        # Self-calibrate basis for fallback periods
        if wti_usd and wti_usd > 0:
            computed_basis = round(angel_ltp - wti_usd * fx_rate, 2)
            _CALIBRATED_BASIS = computed_basis
            logger.debug(f"MCX basis calibrated: {computed_basis:.2f} (was: {_CALIBRATED_BASIS})")

    elif wti_usd and wti_usd > 0:
        # FALLBACK: derive MCX price from WTI + FX + best available basis
        mcx_basis = (
            _CALIBRATED_BASIS
            if _CALIBRATED_BASIS is not None
            else getattr(settings, "CRUDE_MCX_BASIS", 15.0)
        )
        inr_spot = round(wti_usd * fx_rate + mcx_basis, 2)
        source = "Yahoo+LiveFX" + ("+CalibBasis" if _CALIBRATED_BASIS is not None else "+StaticBasis")
    else:
        # No fresh data — return stale cache
        if _CRUDE_CACHE:
            return _CRUDE_CACHE
        return {
            "symbol": "CRUDEOIL",
            "spot": 8570.0,
            "usd_price": 0.0,
            "usdinr": fx_rate,
            "prev_close": 8620.0,
            "day_high": 8650.0,
            "day_low": 8520.0,
            "change": -50.0,
            "change_pct": -0.58,
            "lot_size": getattr(settings, "CRUDE_LOT_SIZE", 10),
            "timestamp": datetime.now(),
            "is_live": False,
            "source": "Fallback",
        }

    # ── Compute INR OHLC from WTI when available ──────────────────────────────
    mcx_basis_used = _CALIBRATED_BASIS if _CALIBRATED_BASIS is not None else getattr(settings, "CRUDE_MCX_BASIS", 15.0)
    if wti_usd:
        inr_prev = round((prev_close_usd or wti_usd) * fx_rate + mcx_basis_used, 2)
        inr_high = round((day_high_usd or wti_usd) * fx_rate + mcx_basis_used, 2)
        inr_low = round((day_low_usd or wti_usd) * fx_rate + mcx_basis_used, 2)
    else:
        inr_prev = inr_spot
        inr_high = inr_spot
        inr_low = inr_spot

    # If Angel WS is the source, use it for the spot but keep Yahoo-derived OHLC
    if angel_ltp:
        inr_high = max(inr_high, inr_spot)
        inr_low = min(inr_low, inr_spot)

    change_pts = round(inr_spot - inr_prev, 2)
    change_pct = round((change_pts / inr_prev) * 100.0, 2) if inr_prev else 0.0

    result = {
        "symbol": "CRUDEOIL",
        "spot": inr_spot,
        "usd_price": wti_usd or 0.0,
        "usdinr": fx_rate,
        "prev_close": inr_prev,
        "day_high": inr_high,
        "day_low": inr_low,
        "change": change_pts,
        "change_pct": change_pct,
        "lot_size": getattr(settings, "CRUDE_LOT_SIZE", 10),
        "timestamp": datetime.now(),
        "is_live": True,
        "source": source,
        "calibrated_basis": _CALIBRATED_BASIS,
    }
    _CRUDE_CACHE = result
    _CRUDE_LAST_FETCH = now_ts
    return result


def fetch_crude_candles(interval_minutes: int = 60, days_back: int = 30) -> pd.DataFrame:
    """
    Fetches OHLCV historical candle data for Crude Oil converted to INR.
    """
    import pandas as pd
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    if interval_minutes <= 5:
        interval_str = "5m"
        range_str = f"{min(days_back, 5)}d"
    elif interval_minutes <= 15:
        interval_str = "15m"
        range_str = f"{min(days_back, 10)}d"
    elif interval_minutes <= 60:
        interval_str = "60m"
        range_str = f"{min(days_back, 30)}d"
    else:
        interval_str = "1d"
        range_str = f"{min(days_back, 365)}d"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/CL=F?interval={interval_str}&range={range_str}"
    try:
        r = session.get(url, timeout=5)
        if r.status_code == 200:
            res = r.json()["chart"]["result"][0]
            timestamps = res.get("timestamp", [])
            quotes = res["indicators"]["quote"][0]
            fx_rate = _get_live_usdinr()

            df = pd.DataFrame({
                "timestamp": timestamps,
                "open": [round(float(v) * fx_rate, 2) if v is not None else None for v in quotes.get("open", [])],
                "high": [round(float(v) * fx_rate, 2) if v is not None else None for v in quotes.get("high", [])],
                "low": [round(float(v) * fx_rate, 2) if v is not None else None for v in quotes.get("low", [])],
                "close": [round(float(v) * fx_rate, 2) if v is not None else None for v in quotes.get("close", [])],
                "volume": quotes.get("volume", [0] * len(timestamps))
            })
            df = df.dropna().reset_index(drop=True)
            df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
            return df
    except Exception as e:
        logger.error(f"Error fetching crude candles: {e}")

    return pd.DataFrame()


