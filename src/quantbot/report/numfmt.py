"""Shared number formatting for reports. Used by both the text formatter and the
HTML renderer so figures read identically across channels."""

from __future__ import annotations


def fmt_money(val: float | None, currency: str) -> str:
    """Full-precision money — used for headline net-liq/cash/invested figures."""
    if val is None:
        return "—"
    return f"{val:,.0f} {currency}"


def abbr_money(val: float | None) -> str:
    """Compact money for tables: 71,931 -> 71.9k, 1,240,000 -> 1.2M, -45 -> -45."""
    if val is None:
        return "—"
    a = abs(val)
    sign = "-" if val < 0 else ""
    if a >= 1_000_000:
        return f"{sign}{a / 1e6:.1f}M"
    if a >= 1_000:
        return f"{sign}{a / 1e3:.1f}k"
    return f"{sign}{a:.0f}"


def signed_money(val: float | None) -> str:
    """Compact money with an explicit +/- sign (for P/L)."""
    if val is None:
        return "—"
    return ("+" if val >= 0 else "-") + abbr_money(abs(val))


def signed_full_money(val: float | None, currency: str) -> str:
    """Full-precision money with an explicit +/- sign (headline totals)."""
    if val is None:
        return "—"
    return ("+" if val >= 0 else "-") + f"{abs(val):,.0f} {currency}"


def signed_pct(val: float | None, digits: int = 1) -> str:
    """Percent with an explicit +/- sign (returns / YoY)."""
    if val is None:
        return "—"
    return f"{val:+.{digits}f}%"


def fmt_pct(val: float | None, digits: int = 1) -> str:
    if val is None:
        return "—"
    return f"{val:.{digits}f}%"


def fmt_num(val: float | None, digits: int = 2) -> str:
    if val is None:
        return "—"
    return f"{val:.{digits}f}"
