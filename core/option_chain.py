"""
Option chain helper utilities: Strike calculations, Expiry dates, Symbol resolution,
and Black-Scholes Greeks engine.
"""

import math
from datetime import date, datetime, timedelta
from typing import List, Tuple, Optional
from core.models import OptionType, Instrument


def get_atm_strike(spot_price: float, strike_step: int = 50) -> int:
    """Calculate the nearest At-The-Money (ATM) strike price."""
    return int(round(spot_price / strike_step) * strike_step)


def get_strikes_around_atm(spot_price: float, count: int = 5, strike_step: int = 50) -> List[int]:
    """Return a sorted list of strikes centered on the ATM strike."""
    atm = get_atm_strike(spot_price, strike_step)
    strikes = [atm + (i * strike_step) for i in range(-count, count + 1)]
    return sorted(strikes)


def get_next_weekly_expiry(from_date: Optional[date] = None, weekday: int = 1) -> date:
    """
    Get the next weekly expiry date.
    NSE NIFTY weekly expiry is Tuesday (weekday = 1).
    BSE SENSEX weekly expiry is Thursday (weekday = 3).
    If today is the expiry day and time is past 15:30 IST, returns next week's expiry.
    """
    now = datetime.now()
    if from_date is None:
        from_date = now.date()
        if from_date.weekday() == weekday and now.hour >= 15 and now.minute >= 30:
            from_date += timedelta(days=1)

    days_ahead = weekday - from_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def get_monthly_expiry(year: int, month: int, weekday: int = 1) -> date:
    """Get the last designated weekday (default Tuesday = 1) of the given month and year."""
    if month == 12:
        last_day = date(year, 12, 31)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    while last_day.weekday() != weekday:
        last_day -= timedelta(days=1)
    return last_day


def get_next_monthly_expiry(from_date: Optional[date] = None, weekday: int = 1) -> date:
    """
    Get the nearest upcoming monthly expiry date (default last Tuesday of month).
    Used for BANKNIFTY, FINNIFTY, MIDCPNIFTY (which have monthly expiries under SEBI rules).
    """
    now = datetime.now()
    if from_date is None:
        from_date = now.date()

    exp = get_monthly_expiry(from_date.year, from_date.month, weekday=weekday)
    if from_date > exp or (from_date == exp and now.hour >= 15 and now.minute >= 30):
        if from_date.month == 12:
            exp = get_monthly_expiry(from_date.year + 1, 1, weekday=weekday)
        else:
            exp = get_monthly_expiry(from_date.year, from_date.month + 1, weekday=weekday)
    return exp


def format_nifty_symbol(
    expiry: date,
    strike: int,
    option_type: OptionType,
    is_monthly: bool = False
) -> str:
    """
    Format standard NSE option symbol:
    Monthly example: NIFTY24SEP24500CE
    Weekly example:  NIFTY2490524500CE (Year 24, Month 9 -> '9', Day 05)
                     For Oct/Nov/Dec weekly: O, N, D are used for month code.
    """
    year_str = str(expiry.year)[2:]

    if is_monthly:
        month_str = expiry.strftime("%b").upper()
        return f"NIFTY{year_str}{month_str}{strike}{option_type.value}"
    else:
        month = expiry.month
        month_code = "O" if month == 10 else "N" if month == 11 else "D" if month == 12 else str(month)
        day_str = f"{expiry.day:02d}"
        return f"NIFTY{year_str}{month_code}{day_str}{strike}{option_type.value}"


# ----------------------------------------------------------------------
# Black-Scholes Greeks Calculator
# ----------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function approximation."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def calculate_black_scholes(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    risk_free_rate: float = 0.07,  # Typical RBI rate ~6.5 - 7%
    option_type: OptionType = OptionType.CE
) -> dict:
    """
    Calculate theoretical option price and Greeks (Delta, Gamma, Theta, Vega).
    """
    if time_to_expiry_years <= 0 or volatility <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == OptionType.CE else max(0.0, strike - spot)
        return {
            "price": intrinsic,
            "delta": 1.0 if (option_type == OptionType.CE and spot > strike) else (-1.0 if (option_type == OptionType.PE and spot < strike) else 0.0),
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0
        }

    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * volatility ** 2) * time_to_expiry_years) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    discount = math.exp(-risk_free_rate * time_to_expiry_years)
    pdf_d1 = _norm_pdf(d1)

    if option_type == OptionType.CE:
        price = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf_d1 * volatility) / (2.0 * sqrt_t) - risk_free_rate * strike * discount * _norm_cdf(d2)) / 365.0
    else:
        price = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * volatility) / (2.0 * sqrt_t) + risk_free_rate * strike * discount * _norm_cdf(-d2)) / 365.0

    gamma = pdf_d1 / (spot * volatility * sqrt_t)
    vega = (spot * sqrt_t * pdf_d1) / 100.0  # Vega per 1% change in IV

    return {
        "price": round(price, 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 2),
        "vega": round(vega, 2)
    }
