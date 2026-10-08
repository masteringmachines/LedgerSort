"""
money.py — parse and format amounts with Decimal, never float.

Bookkeeping totals must add up to the cent. Floats can't promise that
(0.1 + 0.2 != 0.3), so every amount in this tool is a Decimal.
"""

from __future__ import annotations
from decimal import Decimal, InvalidOperation


def parse_amount(text: str) -> Decimal:
    """'$1,234.50' -> 1234.50, '(54.99)' -> -54.99, '' -> 0."""
    s = (text or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return Decimal("0")
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"cannot read amount '{text}'") from None
    return -value if negative else value


def fmt(amount: Decimal) -> str:
    """Two decimal places, thousands separators: -1234.5 -> '-1,234.50'."""
    return f"{amount:,.2f}"
