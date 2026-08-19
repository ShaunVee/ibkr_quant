"""Rule-based risk flags — the honest form of "recommendations".

Each flag is deterministic, explainable, and thresholded by config (your risk profile).
Flags surface *what to look at* — concentration, high beta, RSI extremes, imminent
earnings, drawdown — and never emit buy/sell verdicts.
"""

from __future__ import annotations

from datetime import date

from quantbot.analysis.fundamental import days_to_earnings
from quantbot.analysis.risk import RiskMetrics
from quantbot.models import Flag, Fundamentals, Portfolio, TechnicalSnapshot

# Severity ordering for sorting a flag list, most severe first.
_SEVERITY_RANK = {"high": 0, "warn": 1, "info": 2}


def evaluate(
    portfolio: Portfolio,
    fundamentals: dict[str, Fundamentals],
    technicals: dict[str, TechnicalSnapshot],
    risk: RiskMetrics,
    thresholds: dict,
    *,
    today: date | None = None,
) -> list[Flag]:
    today = today or date.today()
    flags: list[Flag] = []

    conc_pct = float(thresholds.get("concentration_pct", 25.0))
    sector_pct = float(thresholds.get("sector_pct", 40.0))
    beta_high = float(thresholds.get("portfolio_beta_high", 1.3))
    rsi_hi = float(thresholds.get("rsi_overbought", 70.0))
    rsi_lo = float(thresholds.get("rsi_oversold", 30.0))
    earn_days = int(thresholds.get("earnings_soon_days", 5))
    dd_pct = float(thresholds.get("drawdown_pct", 15.0))

    # --- portfolio-level ---
    for symbol, w in risk.weights.items():
        if w * 100.0 >= conc_pct:
            flags.append(
                Flag(
                    code="CONCENTRATION",
                    severity="high" if w * 100.0 >= conc_pct * 1.5 else "warn",
                    symbol=symbol,
                    message=(
                        f"{symbol} is {w * 100:.1f}% of the book "
                        f"(threshold {conc_pct:.0f}%) — concentration risk."
                    ),
                )
            )

    for sector, w in risk.sector_weights.items():
        if w * 100.0 >= sector_pct:
            flags.append(
                Flag(
                    code="SECTOR_OVERWEIGHT",
                    severity="warn",
                    symbol=None,
                    message=(
                        f"{sector} sector is {w * 100:.1f}% of the book "
                        f"(threshold {sector_pct:.0f}%) — sector concentration."
                    ),
                )
            )

    if risk.portfolio_beta is not None and risk.portfolio_beta >= beta_high:
        flags.append(
            Flag(
                code="HIGH_BETA",
                severity="warn",
                symbol=None,
                message=(
                    f"Portfolio beta ≈ {risk.portfolio_beta:.2f} "
                    f"(threshold {beta_high:.2f}) — elevated market risk."
                ),
            )
        )

    # Breach on the *realized* account drawdown (a loss actually taken), not the simulated
    # current-weights backtest — the latter is a "could have" and would cry wolf here.
    if risk.realized_drawdown is not None and risk.realized_drawdown * -100.0 >= dd_pct:
        flags.append(
            Flag(
                code="DRAWDOWN",
                severity="warn",
                symbol=None,
                message=(
                    f"Account drawdown ≈ {risk.realized_drawdown * 100:.1f}% from peak "
                    f"(threshold -{dd_pct:.0f}%)."
                ),
            )
        )

    # --- per-symbol technical ---
    for symbol, tech in technicals.items():
        if tech.rsi14 is not None:
            if tech.rsi14 >= rsi_hi:
                flags.append(
                    Flag(
                        code="OVERBOUGHT",
                        severity="info",
                        symbol=symbol,
                        message=f"{symbol} RSI {tech.rsi14:.0f} ≥ {rsi_hi:.0f} — overbought.",
                    )
                )
            elif tech.rsi14 <= rsi_lo:
                flags.append(
                    Flag(
                        code="OVERSOLD",
                        severity="info",
                        symbol=symbol,
                        message=f"{symbol} RSI {tech.rsi14:.0f} ≤ {rsi_lo:.0f} — oversold.",
                    )
                )
        if tech.golden_cross is False and tech.sma200 is not None:
            flags.append(
                Flag(
                    code="TREND_BREAK",
                    severity="info",
                    symbol=symbol,
                    message=f"{symbol} trades below its 200-day average (downtrend).",
                )
            )

    # --- per-symbol earnings ---
    for symbol, fund in fundamentals.items():
        dte = days_to_earnings(fund, today)
        if dte is not None and 0 <= dte <= earn_days:
            when = "today" if dte == 0 else f"in {dte}d"
            flags.append(
                Flag(
                    code="EARNINGS_SOON",
                    severity="warn",
                    symbol=symbol,
                    message=f"{symbol} reports earnings {when} — expect volatility.",
                )
            )

    flags.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.code, f.symbol or ""))
    return flags
