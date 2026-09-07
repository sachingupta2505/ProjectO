"""
Historical Candle Data Downloader & Backtesting Utility.
Uses Angel One SmartAPI (authenticated via login.py) to fetch official,
clean exchange candlestick data across any timeframe:
5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo for NIFTY 50 and MCX CRUDE OIL.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from typing import Optional

# Ensure project root is in path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from login import login
from core.logger import get_logger

logger = get_logger("HistoricalData")

INTERVAL_MAP = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "60m": "ONE_HOUR",
    "1d": "ONE_DAY",
    "daily": "ONE_DAY"
}

TOKEN_MAP = {
    "NIFTY": {"exchange": "NSE", "token": "99926000", "market_start": "09:15", "market_end": "15:30"},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009", "market_start": "09:15", "market_end": "15:30"},
    "FINNIFTY": {"exchange": "NSE", "token": "99926037", "market_start": "09:15", "market_end": "15:30"},
    "MIDCPNIFTY": {"exchange": "NSE", "token": "99926074", "market_start": "09:15", "market_end": "15:30"},
    "SENSEX": {"exchange": "BSE", "token": "99919000", "market_start": "09:15", "market_end": "15:30"},
    "CRUDEOIL": {"exchange": "MCX", "token": "565900", "market_start": "09:00", "market_end": "23:30"}
}


def fetch_and_save_candles(
    symbol: str = "NIFTY",
    timeframe: str = "5m",
    days_back: int = 30,
    save_csv: bool = True
) -> pd.DataFrame:
    """
    Downloads historical candlestick chart data from Angel One SmartAPI.
    Supports higher-order timeframes (4h, 1w, 1mo) via pandas resampling.
    """
    sym = symbol.upper()
    if sym not in TOKEN_MAP:
        raise ValueError(f"Unsupported symbol '{sym}'. Supported: {list(TOKEN_MAP.keys())}")

    meta = TOKEN_MAP[sym]
    exchange = meta["exchange"]
    token = meta["token"]

    # Determine native API interval
    tf_lower = timeframe.lower()
    needs_resample = None
    if tf_lower in ("4h", "240m"):
        api_interval = "ONE_HOUR"
        needs_resample = "4h"
    elif tf_lower in ("1w", "weekly"):
        api_interval = "ONE_DAY"
        needs_resample = "W-FRI"
    elif tf_lower in ("1mo", "monthly"):
        api_interval = "ONE_DAY"
        needs_resample = "ME"
    else:
        api_interval = INTERVAL_MAP.get(tf_lower)
        if not api_interval:
            raise ValueError(f"Unsupported timeframe '{timeframe}'. Use: 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo")

    logger.info(f"Connecting to Angel One to download {sym} [{timeframe}] data for last {days_back} days...")
    api = login()

    now = datetime.now()
    from_date = (now - timedelta(days=days_back)).strftime(f"%Y-%m-%d {meta['market_start']}")
    to_date = now.strftime(f"%Y-%m-%d {meta['market_end']}")

    resp = api.getCandleData({
        "exchange": exchange,
        "symboltoken": token,
        "interval": api_interval,
        "fromdate": from_date,
        "todate": to_date
    })

    raw_candles = resp.get("data", [])
    if not raw_candles:
        logger.warning(f"No candle data returned by Angel One for {sym} ({api_interval}). Message: {resp.get('message')}")
        return pd.DataFrame()

    df = pd.DataFrame(raw_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})

    # Resample to 4H, Weekly, or Monthly if requested
    if needs_resample:
        df = df.resample(needs_resample).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna()

    df.reset_index(inplace=True)

    if save_csv:
        out_dir = project_root / "data" / "historical"
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{sym}_{tf_lower}_{days_back}d.csv"
        df.to_csv(file_path, index=False)
        file_size_kb = file_path.stat().st_size / 1024
        logger.info(f"✅ Saved {len(df)} candles to {file_path.name} ({file_size_kb:.1f} KB)")

    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download Historical Candlesticks from Angel One")
    parser.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "CRUDEOIL"], help="Asset Symbol")
    parser.add_argument("--timeframe", default="5m", help="Timeframe: 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch")
    args = parser.parse_args()

    df = fetch_and_save_candles(symbol=args.symbol, timeframe=args.timeframe, days_back=args.days)
    if not df.empty:
        print("\n--- Download Summary ---")
        print(f"Asset:     {args.symbol}")
        print(f"Timeframe: {args.timeframe}")
        print(f"Total Bars: {len(df)}")
        print(f"From:      {df['timestamp'].iloc[0]}")
        print(f"To:        {df['timestamp'].iloc[-1]}")
        print("\nRecent 3 Bars:")
        print(df.tail(3)[["timestamp", "open", "high", "low", "close", "volume"]].to_string(index=False))
