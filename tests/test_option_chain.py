from datetime import date
from core.models import OptionType
from core.option_chain import (
    get_atm_strike,
    get_strikes_around_atm,
    get_next_weekly_expiry,
    format_nifty_symbol,
    calculate_black_scholes,
)


def test_get_atm_strike():
    assert get_atm_strike(24524.0, 50) == 24500
    assert get_atm_strike(24526.0, 50) == 24550
    assert get_atm_strike(24500.0, 50) == 24500
    assert get_atm_strike(24549.9, 50) == 24550


def test_get_strikes_around_atm():
    strikes = get_strikes_around_atm(24500, count=2, strike_step=50)
    assert strikes == [24400, 24450, 24500, 24550, 24600]


def test_get_next_weekly_expiry():
    # A known Wednesday: 2026-09-02 (weekday 2). Next Thursday should be 2026-09-03
    test_wed = date(2026, 9, 2)
    next_thurs = get_next_weekly_expiry(from_date=test_wed, weekday=3)
    assert next_thurs == date(2026, 9, 3)
    assert next_thurs.weekday() == 3


def test_format_nifty_symbol():
    exp = date(2026, 9, 10)
    sym_ce = format_nifty_symbol(exp, 24500, OptionType.CE, is_monthly=False)
    assert sym_ce == "NIFTY2691024500CE"

    sym_pe = format_nifty_symbol(exp, 24500, OptionType.PE, is_monthly=False)
    assert sym_pe == "NIFTY2691024500PE"

    monthly_ce = format_nifty_symbol(date(2026, 9, 24), 24500, OptionType.CE, is_monthly=True)
    assert monthly_ce == "NIFTY26SEP24500CE"


def test_black_scholes_greeks():
    # ATM Call: Delta should be close to 0.50
    res = calculate_black_scholes(
        spot=24500.0,
        strike=24500.0,
        time_to_expiry_years=7 / 365.0,
        volatility=0.15,
        risk_free_rate=0.07,
        option_type=OptionType.CE
    )
    assert 0.45 <= res["delta"] <= 0.60
    assert res["price"] > 0
    assert res["gamma"] > 0
