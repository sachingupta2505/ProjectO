"""
Angel One WebSocket Live Feed — Real MCX LTP via SmartWebSocketV2.

Provides actual exchange-traded prices for MCX Crude Oil (and optionally other
MCX/NSE instruments) via the Angel One feed token, which works independently
of the restricted Market Data REST API scope.

Usage (singleton):
    from core.angel_feed import angel_feed
    ltp = angel_feed.get_crude_ltp()   # returns float or None if stale/disconnected
    angel_feed.start()                 # called once at bot startup
    angel_feed.stop()                  # called on shutdown
"""

import threading
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("AngelFeed")

# ──────────────────────────────────────────────────────────────────────────────
# Exchange type codes (per SmartWebSocketV2 spec)
# ──────────────────────────────────────────────────────────────────────────────
MCX_FO_EXCHANGE = 5        # MCX Futures & Options
NSE_CM_EXCHANGE = 1        # NSE Cash Market

# Subscription mode
LTP_MODE = 1               # Only Last Traded Price (lowest bandwidth)

# MCX Crude Oil Sept 2026 tokens
CRUDE_MINI_TOKEN = "565900"
CRUDE_MAIN_TOKEN = "565899"

# NSE Index tokens
NIFTY_TOKEN = "26000"
NIFTY_ALT_TOKEN = "99926000"
BANKNIFTY_TOKEN = "26009"
BANKNIFTY_ALT_TOKEN = "99926009"

# How stale an LTP is allowed to be before we consider it dead (seconds)
MAX_LTP_AGE_SEC = 10


class AngelOneWebSocketFeed:
    """
    Background thread that maintains a live WebSocket connection to Angel One
    and keeps the latest MCX Crude LTP updated in memory.

    Design:
    - Logs in fresh on every (re)connect using login.py to get a fresh auth_token.
    - Runs connect() in a daemon thread so it doesn't block the main loop.
    - Auto-reconnects with exponential back-off on disconnect/error.
    - Exposes get_crude_ltp() → float | None for safe cross-thread reads.
    """

    def __init__(self):
        self._ltp: Dict[str, float] = {}       # token → last traded price
        self._ltp_ts: Dict[str, float] = {}    # token → epoch timestamp of last tick
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._sws = None                        # SmartWebSocketV2 instance
        self._connected = False
        self._retry_delay = 5                   # seconds, doubles on each failure

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def start(self):
        """Start the WebSocket feed in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="AngelFeedThread", daemon=True
        )
        self._thread.start()
        logger.info("AngelOneWebSocketFeed started.")

    def stop(self):
        """Gracefully stop the WebSocket feed."""
        self._stop_event.set()
        if self._sws:
            try:
                self._sws.close_connection()
            except Exception:
                pass
        logger.info("AngelOneWebSocketFeed stopped.")

    def get_crude_ltp(self) -> Optional[float]:
        """
        Returns the latest MCX Crude Oil Mini LTP, or None if the feed is
        disconnected or the last tick is older than MAX_LTP_AGE_SEC seconds.
        Falls back to the main contract if mini is not available.
        """
        for token in (CRUDE_MINI_TOKEN, CRUDE_MAIN_TOKEN):
            ltp = self._get_ltp(token)
            if ltp is not None:
                return ltp
        return None

    def get_nifty_ltp(self) -> Optional[float]:
        """Returns the latest NIFTY 50 index spot LTP from WebSocket."""
        for token in (NIFTY_TOKEN, NIFTY_ALT_TOKEN):
            ltp = self._get_ltp(token)
            if ltp is not None:
                return ltp
        return None

    def get_banknifty_ltp(self) -> Optional[float]:
        """Returns the latest BANKNIFTY index spot LTP from WebSocket."""
        for token in (BANKNIFTY_TOKEN, BANKNIFTY_ALT_TOKEN):
            ltp = self._get_ltp(token)
            if ltp is not None:
                return ltp
        return None

    def is_connected(self) -> bool:
        return self._connected

    def get_status(self) -> Dict[str, Any]:
        """Return a status dict for logging/dashboard."""
        ltp = self.get_crude_ltp()
        return {
            "connected": self._connected,
            "crude_ltp": ltp,
            "crude_ltp_age_sec": self._ltp_age(CRUDE_MINI_TOKEN),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get_ltp(self, token: str) -> Optional[float]:
        with self._lock:
            ts = self._ltp_ts.get(token, 0.0)
            if time.time() - ts > MAX_LTP_AGE_SEC:
                return None
            return self._ltp.get(token)

    def _ltp_age(self, token: str) -> float:
        with self._lock:
            ts = self._ltp_ts.get(token, 0.0)
            return round(time.time() - ts, 1) if ts else 9999.0

    _LTP_FILE = "logs/angel_ltp.json"

    def _set_quote(self, token: str, price: float, close: float = 0.0, high: float = 0.0, low: float = 0.0, open_p: float = 0.0):
        now = time.time()
        with self._lock:
            self._ltp[token] = price
            self._ltp_ts[token] = now
            if not hasattr(self, "_quotes"):
                self._quotes = {}
            self._quotes[token] = {
                "ltp": price,
                "close": close if close > 0 else self._quotes.get(token, {}).get("close", price),
                "high": high if high > 0 else max(self._quotes.get(token, {}).get("high", price), price),
                "low": low if low > 0 else min(self._quotes.get(token, {}).get("low", price), price),
                "open": open_p if open_p > 0 else self._quotes.get(token, {}).get("open", price),
                "ts": now,
            }
            current_copy = dict(self._quotes)

        # Also persist to file so other processes (Streamlit dashboard) can read it
        try:
            import json, os
            os.makedirs("logs", exist_ok=True)
            # Atomic write: write to temp then rename
            tmp = self._LTP_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(current_copy, f)
            os.replace(tmp, self._LTP_FILE)
        except Exception:
            pass  # Never let file I/O crash the feed thread

    # ──────────────────────────────────────────────────────────────────────────
    # Background loop
    # ──────────────────────────────────────────────────────────────────────────

    def _run_loop(self):
        """Outer retry loop — reconnects on every failure."""
        while not self._stop_event.is_set():
            try:
                self._connect_and_run()
                # If connect_and_run returns cleanly (stop requested), exit
                if self._stop_event.is_set():
                    break
            except Exception as exc:
                logger.warning(f"AngelFeed connection error: {exc}. Retrying in {self._retry_delay}s...")
            finally:
                self._connected = False

            # Exponential back-off, cap at 60s
            self._stop_event.wait(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, 60)

    def _connect_and_run(self):
        """Login, create WebSocket, subscribe, run until error or stop."""
        import sys
        sys.path.insert(0, "c:/projectO")

        try:
            from login import login
            api = login()
        except Exception as e:
            logger.error(f"AngelFeed login failed: {e}")
            raise

        auth_token = api.access_token
        api_key = api.privateKey
        client_code = api.userId
        feed_token = api.getfeedToken()

        if not all([auth_token, api_key, client_code, feed_token]):
            raise ValueError("Missing one or more Angel One auth tokens.")

        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        sws = SmartWebSocketV2(
            auth_token=auth_token,
            api_key=api_key,
            client_code=client_code,
            feed_token=feed_token,
            max_retry_attempt=3,
            retry_strategy=0,
            retry_delay=5,
        )
        self._sws = sws

        def on_open(wsapp):
            logger.info("Angel WebSocket connected. Subscribing MCX Crude & NSE Indices...")
            self._connected = True
            self._retry_delay = 5  # reset back-off on successful connect
            token_list = [
                {
                    "exchangeType": MCX_FO_EXCHANGE,
                    "tokens": [CRUDE_MINI_TOKEN, CRUDE_MAIN_TOKEN],
                },
                {
                    "exchangeType": NSE_CM_EXCHANGE,
                    "tokens": [NIFTY_TOKEN, NIFTY_ALT_TOKEN, BANKNIFTY_TOKEN, BANKNIFTY_ALT_TOKEN],
                }
            ]
            QUOTE_MODE = 2
            sws.subscribe(
                correlation_id="multi_feed001",
                mode=QUOTE_MODE,
                token_list=token_list,
            )
            logger.info("Subscribed MCX Crude & NSE Indices in QUOTE mode.")

        def on_data(wsapp, message):
            # message is already parsed by SmartWebSocketV2._parse_binary_data
            try:
                token = str(message.get("token", ""))
                ltp = message.get("last_traded_price", 0)
                # Angel One sends prices in paise (1/100 of rupee)
                if ltp and ltp > 0:
                    price_inr = ltp / 100.0
                    close_p = (message.get("closed_price", 0) or 0) / 100.0
                    high_p = (message.get("high_price_of_the_day", 0) or 0) / 100.0
                    low_p = (message.get("low_price_of_the_day", 0) or 0) / 100.0
                    open_p = (message.get("open_price_of_the_day", 0) or 0) / 100.0
                    self._set_quote(token, price_inr, close_p, high_p, low_p, open_p)
                    logger.debug(f"MCX Quote tick: token={token}, ltp={price_inr:.2f}, close={close_p:.2f}")
            except Exception as e:
                logger.warning(f"AngelFeed on_data parse error: {e} | raw={message}")

        def on_error(wsapp, error):
            logger.warning(f"Angel WebSocket error: {error}")
            self._connected = False

        def on_close(wsapp):
            logger.warning("Angel WebSocket closed.")
            self._connected = False

        sws.on_open = on_open
        sws.on_data = on_data
        sws.on_error = on_error
        sws.on_close = on_close

        logger.info("Connecting to Angel One WebSocket...")
        sws.connect()   # blocks until connection closes


# ──────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────────────
angel_feed = AngelOneWebSocketFeed()
