import datetime
from decimal import Decimal
import math
from os import truncate
from typing import Dict

from dateutil import parser
import public_settings as ps



def format_time_simple(iso_time_str):
    """
    Convert ISO8601 string like '2026-02-19T01:04:00.000+00:00' to '2026-02-19 01:04'.
    """
    dt = parser.parse(iso_time_str)
    return dt.strftime('%Y-%m-%d %H:%M')

def _to_oanda_instrument(symbol: str) -> str:
    """
    Convert internal symbol like 'EUR/USD' to OANDA instrument 'EUR_USD'.
    """
    return symbol.replace("/", "_")

fmt = lambda v, n: "N/A" if v is None else truncate(v, n)

def truncate(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.trunc(value * factor) / factor

def _pip_size(symbol: str) -> Decimal:
    s = symbol.upper()
    if "JPY" in s or "DXY" in s:
        return Decimal("0.01")
    return Decimal("0.0001")


TRADING_HOURS = {
    0: [("00:00", "23:59")],  # Monday
    1: [("00:00", "23:59")],  # Tuesday
    2: [("00:00", "23:59")],  # Wednesday
    3: [("00:00", "23:59")],  # Thursday
    4: [("00:00", "19:00")],  # Friday
}


def validate_trading_hours(close_time: datetime.datetime) -> bool:
    if ps.ignore_session_ckeck == 1:
        return True

    utc_time = close_time
    day_of_week = utc_time.weekday()  # Monday=0 ... Sunday=6
    current_time = utc_time.time()
    current_hour = utc_time.hour
    current_minute = utc_time.minute

    # Example hour filter (UTC): allow only 06:00 to 18:59
    # if not (6 <= current_hour <= 18):
    #     return False

    monday_start = datetime.time(12, 0)
    friday_end = datetime.time(12, 0)

    #if day_of_week == 0:
        #return current_time >= monday_start

    #if day_of_week in (1, 2, 3):
        #return True

    #if day_of_week == 4:
        #return current_time <= friday_end

    if current_hour >= 20 and current_hour <= 22:
        return False
    
    return True


def calculate_single_position_size(
    sl_pips,
    position_price,
    account_balance,
    available_margin,
    risk_percent,
    risk_cap_dollar,
) -> Dict[str, object]:
    """
    Calculate one potential trade sizing using the same logic as the SQL script.

    Assumptions (same as SQL version):
    - GBPUSD pip size is fixed at 0.0001
    - Leverage is fixed at 20:1
    - TP is calculated with RR=1:1.5
    - risk_limit = max(risk_cap_dollar, account_balance * risk_percent)

    Notes:
    - `risk_percent` accepts either `1` (meaning 1%) or `0.01`.
    - Dollar outputs are rounded to 2 decimals.
    """
    pip_size = Decimal("0.0001")
    leverage = Decimal("20")
    rr = ps.RR_ratio

    sl = Decimal(str(sl_pips))
    price = Decimal(str(position_price))
    balance = Decimal(str(account_balance))
    available = Decimal(str(available_margin))
    rp = Decimal(str(risk_percent))
    cap = Decimal(str(risk_cap_dollar))

    # Accept either 1 (meaning 1%) or 0.01.
    if rp == Decimal("1"):
        rp_fraction = Decimal("0.01")
    elif rp < Decimal("1"):
        rp_fraction = rp
    else:
        rp_fraction = rp / Decimal("100")

    if sl <= 0 or price <= 0 or available <= 0:
        return {
            "trade_skipped": True,
            "risk_value": Decimal("0.00"),
            "tp_value": Decimal("0.00"),
            "margin_required": Decimal("0.00"),
            "position_size": 0,
        }

    risk_limit = max(cap, (balance * rp_fraction).quantize(Decimal("0.01")))

    units_by_risk = int(risk_limit / (sl * pip_size))
    units_by_margin = int((available * leverage) / price)
    position_size = max(0, min(units_by_risk, units_by_margin))

    if position_size <= 0:
        return {
            "trade_skipped": True,
            "risk_value": Decimal("0.00"),
            "tp_value": Decimal("0.00"),
            "margin_required": Decimal("0.00"),
            "position_size": 0,
        }

    size_decimal = Decimal(position_size)
    margin_required = ((size_decimal * price) / leverage).quantize(Decimal("0.01"))
    risk_value = (size_decimal * sl * pip_size).quantize(Decimal("0.01"))
    tp_value = (risk_value * rr).quantize(Decimal("0.01"))

    return {
        "trade_skipped": False,
        "risk_value": risk_value,
        "tp_value": tp_value,
        "margin_required": margin_required,
        "position_size": position_size,
    }


