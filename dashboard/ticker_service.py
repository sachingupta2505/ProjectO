"""
Real-Time Market Ticker Microservice for ProjectO Dashboard.
Serves high-frequency live market indices and ATM option quotes over HTTP
at sub-10ms response times, enabling broker-style real-time banners without page refreshes.
"""

import json
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from core.logger import get_logger
from core.market_data import (
    get_live_nifty_spot,
    get_live_banknifty_spot,
    get_live_crude_spot,
    get_live_option_chain,
    get_live_option_quote
)
from config.settings import settings

logger = get_logger("TickerService")

_SERVER_INSTANCE: Optional[HTTPServer] = None
_SERVER_LOCK = threading.Lock()


_TICKER_CACHE = {}
_TICKER_CACHE_TIME = 0.0

class TickerHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _TICKER_CACHE, _TICKER_CACHE_TIME
        if self.path.startswith("/api/ticker"):
            try:
                now = time.time()
                if not _TICKER_CACHE or (now - _TICKER_CACHE_TIME > 1.5):
                    n = get_live_nifty_spot()
                    b = get_live_banknifty_spot()
                    c = get_live_crude_spot()
                    spot = float(n.get("spot", 23950.0))
                    atm = int(round(spot / 50.0) * 50)

                    # Fetch ATM PE & CE quotes
                    pe_q = get_live_option_quote("nifty", atm, "PE")
                    ce_q = get_live_option_quote("nifty", atm, "CE")

                    _TICKER_CACHE = {
                        "nifty": spot,
                        "n_chg": float(n.get("change", 0.0)),
                        "n_pct": float(n.get("pct_change", 0.0)),
                        "n_high": float(n.get("high", 0.0)),
                        "n_low": float(n.get("low", 0.0)),
                        "n_open": float(n.get("open", 0.0)),
                        "atm": atm,
                        "bank": float(b.get("spot", 0.0)),
                        "b_chg": float(b.get("change", 0.0)),
                        "b_pct": float(b.get("pct_change", 0.0)),
                        "crude": float(c.get("spot", 8570.0)),
                        "c_chg": float(c.get("change", 0.0)),
                        "c_pct": float(c.get("change_pct", 0.0)),
                        "ce_ltp": float(ce_q.get("ltp", 0.0)),
                        "pe_ltp": float(pe_q.get("ltp", 0.0)),
                        "expiry": pe_q.get("expiry", "2026-09-08"),
                        "lot_size": settings.NIFTY_LOT_SIZE,
                        "ts": time.strftime("%H:%M:%S")
                    }
                    _TICKER_CACHE_TIME = now

                body = json.dumps(_TICKER_CACHE).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err_msg = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(err_msg)
        elif self.path.startswith("/api/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        # Silence access logs to keep terminal uncluttered
        pass


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_ticker_service(port: int = 8502) -> bool:
    """Starts the real-time ticker background daemon if not already running."""
    global _SERVER_INSTANCE
    with _SERVER_LOCK:
        if _SERVER_INSTANCE is not None:
            return True

        if is_port_in_use(port):
            logger.info(f"Ticker service port {port} already in use; assuming existing service is running.")
            return True

        try:
            server = HTTPServer(("0.0.0.0", port), TickerHTTPHandler)
            _SERVER_INSTANCE = server
            t = threading.Thread(target=server.serve_forever, daemon=True, name="MarketTickerDaemon")
            t.start()
            logger.info(f"✅ Market Ticker Microservice started on http://0.0.0.0:{port}/api/ticker")
            return True
        except Exception as e:
            logger.error(f"Failed to start ticker service on port {port}: {e}")
            return False


if __name__ == "__main__":
    start_ticker_service(8502)
    print("Market Ticker Service running on http://localhost:8502/api/ticker. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping service.")
