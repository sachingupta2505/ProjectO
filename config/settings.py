"""
Global Configuration & Settings Module.
Loads environment variables and defines operational defaults for strategies and risk management.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Load .env file if available
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        # Also check .env.example as fallback
        example_path = Path(__file__).resolve().parent.parent / ".env.example"
        if example_path.exists():
            load_dotenv(dotenv_path=example_path)
except ImportError:
    pass

# Direct fallback parser for .env
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()
    except Exception:
        pass


@dataclass
class Settings:
    # Execution Mode
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "True").lower() in ("true", "1", "yes")
    BROKER: str = os.getenv("BROKER", "paper").lower()
    PAPER_INITIAL_CAPITAL: float = float(os.getenv("PAPER_INITIAL_CAPITAL", "200000.0"))

    # Market Timing (IST format HH:MM:SS)
    MARKET_START_TIME: str = "09:15:00"
    STRATEGY_ENTRY_TIME: str = "09:20:00"
    AUTO_SQUARE_OFF_TIME: str = "15:15:00"
    MARKET_END_TIME: str = "15:30:00"

    # Multi-Session Market Timings
    NIFTY_START_TIME: str = "09:15:00"
    NIFTY_SQUARE_OFF_TIME: str = "15:15:00"
    CRUDE_START_TIME: str = "09:00:00"
    CRUDE_SQUARE_OFF_TIME: str = "23:15:00"

    # Instrument Specifications: NIFTY 50
    INDEX_NAME: str = "NIFTY"
    NIFTY_STRIKE_STEP: int = 50
    NIFTY_LOT_SIZE: int = int(os.getenv("NIFTY_LOT_SIZE", "65"))
    CRUDE_LOT_SIZE: int = int(os.getenv("CRUDE_LOT_SIZE", "10"))
    DEFAULT_CRUDE_LOTS: int = 4  # 4 Lots = 40 bbl Mini for CRUDE OIL
    CRUDE_MCX_BASIS: float = float(os.getenv("CRUDE_MCX_BASIS", "15.0"))  # Domestic MCX futures basis premium over NYMEX spot
    DEFAULT_LOTS: int = 2        # 2 Lots = 130 Qty for NIFTY
    SLIPPAGE_PCT: float = 0.002  # 0.2% slippage assumption for simulation

    # Instrument Specifications: CRUDE OIL (MCX)
    CRUDE_NAME: str = "CRUDEOIL"
    CRUDE_TICK_SIZE: float = 1.0
    CRUDE_STRIKE_STEP: int = 50
    CRUDE_SLIPPAGE_PCT: float = 0.001

    # Risk Management Settings (RMS) - Strict ₹10,000 Target & ₹5,000 Max Daily Loss
    MAX_DAILY_LOSS: float = float(os.getenv("MAX_DAILY_LOSS", "5000.0"))
    MAX_DAILY_PROFIT: float = float(os.getenv("MAX_DAILY_PROFIT", "10000.0"))
    MAX_LOSS_PER_TRADE: float = float(os.getenv("MAX_LOSS_PER_TRADE", "1500.0"))
    TRAILING_STOP_LOSS: bool = True
    TRAILING_STEP_POINTS: float = 5.0
    TRAILING_MOVE_POINTS: float = 5.0

    # Short Straddle Specific Settings
    STRADDLE_SL_PCT: float = 0.25  # 25% Stop loss on each individual leg
    STRADDLE_TARGET_PCT: float = 0.80  # 80% Target profit decay
    STRADDLE_REENTRY: bool = False  # Enable re-entry on SL trigger if desired

    # Momentum Strategy Settings
    MOMENTUM_TIMEFRAME_MIN: int = 5
    MOMENTUM_EMA_FAST: int = 9
    MOMENTUM_EMA_SLOW: int = 21
    MOMENTUM_SL_POINTS: float = 20.0
    MOMENTUM_TARGET_POINTS: float = 40.0

    # Zerodha Credentials
    ZERODHA_API_KEY: str = os.getenv("ZERODHA_API_KEY", "")
    ZERODHA_API_SECRET: str = os.getenv("ZERODHA_API_SECRET", "")
    ZERODHA_USER_ID: str = os.getenv("ZERODHA_USER_ID", "")
    ZERODHA_PASSWORD: str = os.getenv("ZERODHA_PASSWORD", "")
    ZERODHA_TOTP_KEY: str = os.getenv("ZERODHA_TOTP_KEY", "")

    # Angel One Credentials
    ANGEL_API_KEY: str = os.getenv("ANGEL_API_KEY", "")
    ANGEL_API_SECRET: str = os.getenv("ANGEL_API_SECRET", "")
    ANGEL_CLIENT_ID: str = os.getenv("ANGEL_CLIENT_ID", "")
    ANGEL_PIN: str = os.getenv("ANGEL_PIN", "")
    ANGEL_TOTP_KEY: str = os.getenv("ANGEL_TOTP_KEY", "")

    # Telegram Bridge Configuration
    TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "False").lower() in ("true", "1", "yes")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Log directories
    LOG_DIR: Path = Path(__file__).resolve().parent.parent / "logs"


settings = Settings()
settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
