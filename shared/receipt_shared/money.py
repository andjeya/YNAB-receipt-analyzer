from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def dollars_to_milliunits(amount: float | int | str, outflow: bool = True) -> int:
    dec = Decimal(str(amount)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    milliunits = int((dec * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    if outflow and milliunits > 0:
        return -milliunits
    return milliunits


def milliunits_to_dollars(amount_milliunits: int) -> float:
    dec = (Decimal(amount_milliunits) / Decimal(1000)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return float(dec)
