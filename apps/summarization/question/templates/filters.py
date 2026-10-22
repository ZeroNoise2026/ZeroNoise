"""
question/templates/filters.py
Jinja2 filters — uniform number/percentage formatting. All robust against None / invalid inputs (return "N/A").
"""
from __future__ import annotations
from typing import Any


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def money(val: Any, *, decimals: int | None = None) -> str:
    """Money formatting: 12_345_678_901 -> '$12.3B', 840_000_000 -> '$840M'.

    - T / B: 1 decimal place (can be overridden by decimals)
    - M / K: default 0 decimals
    - < 1K:  2 decimals (stock-price use case)
    - Negative values prefixed with '-'
    - None / non-numeric: 'N/A'
    """
    f = _to_float(val)
    if f is None:
        return "N/A"
    sign = "-" if f < 0 else ""
    v = abs(f)
    if v >= 1e12:
        d = 1 if decimals is None else decimals
        return f"{sign}${v/1e12:.{d}f}T"
    if v >= 1e9:
        d = 1 if decimals is None else decimals
        return f"{sign}${v/1e9:.{d}f}B"
    if v >= 1e6:
        d = 0 if decimals is None else decimals
        return f"{sign}${v/1e6:.{d}f}M"
    if v >= 1e3:
        d = 0 if decimals is None else decimals
        return f"{sign}${v/1e3:.{d}f}K"
    d = 2 if decimals is None else decimals
    return f"{sign}${v:,.{d}f}"


def pct(val: Any, *, decimals: int = 2, signed: bool = True) -> str:
    """Percentage formatting. Input 0.1537 -> '+15.37%'.

    Convention: input is *decimal form* (0.15 = 15%). If it's already a large number like 15, divide by 100 yourself first.
    """
    f = _to_float(val)
    if f is None:
        return "N/A"
    pct_val = f * 100
    if signed:
        return f"{pct_val:+.{decimals}f}%"
    return f"{pct_val:.{decimals}f}%"


def compact(val: Any, *, decimals: int = 0) -> str:
    """Compact representation of plain quantities (non-currency): 1_234_567 -> '1.2M'."""
    f = _to_float(val)
    if f is None:
        return "N/A"
    sign = "-" if f < 0 else ""
    v = abs(f)
    if v >= 1e9:
        return f"{sign}{v/1e9:.{max(decimals,1)}f}B"
    if v >= 1e6:
        return f"{sign}{v/1e6:.{max(decimals,1)}f}M"
    if v >= 1e3:
        return f"{sign}{v/1e3:.{max(decimals,1)}f}K"
    return f"{sign}{v:,.{decimals}f}"


# Centralized export for convenient use by engine.register_filters
ALL_FILTERS = {
    "money": money,
    "pct": pct,
    "compact": compact,
}
