

from decimal import Decimal
from typing import Dict


def calculate_single_position_size(
    sl_pips,
    position_price,
    account_balance,
    available_margine,
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
    rr = Decimal("1.5")

    sl = Decimal(str(sl_pips))
    price = Decimal(str(position_price))
    balance = Decimal(str(account_balance))
    available = Decimal(str(available_margine))
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
            "margine_required": Decimal("0.00"),
            "postion_size": 0,
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
            "margine_required": Decimal("0.00"),
            "postion_size": 0,
        }

    size_decimal = Decimal(position_size)
    margine_required = ((size_decimal * price) / leverage).quantize(Decimal("0.01"))
    risk_value = (size_decimal * sl * pip_size).quantize(Decimal("0.01"))
    tp_value = (risk_value * rr).quantize(Decimal("0.01"))

    return {
        "trade_skipped": False,
        "risk_value": risk_value,
        "tp_value": tp_value,
        "margine_required": margine_required,
        "postion_size": position_size,
    }

result = calculate_single_position_size(
    sl_pips=6.95,
    position_price=1.220140000,
    account_balance=10000,
    available_margine=10000,
    risk_percent=1,
    risk_cap_dollar=100
)

print("Result:")
print(f"trade_skipped: {result['trade_skipped']}")
print(f"risk_value: {result['risk_value']}")
print(f"tp_value: {result['tp_value']}")
print(f"margine_required: {result['margine_required']}")
print(f"postion_size: {result['postion_size']}")

