"""Why did a name move? — driver attribution + catalyst headlines (analysis #7).

The move layer (analysis/movement.py) answers *which* names drove today's book move and
*whether* each move was abnormal. It cannot say *why* — a return number carries no cause.
This module supplies the "why" in two honest layers:

A. Driver attribution (deterministic, no causal guesswork). Each notable mover is regressed
   against a "theme" reference series — SLV against the silver/precious-metals complex, IBIT
   against bitcoin, META against comm-services, an unmapped name against the broad market.
   Today's move is then split into the part the theme explains (beta x theme return) and the
   name-specific residual. That distinguishes "the whole silver complex rose" from "this name
   did something of its own", which is exactly what a raw P/L number hides.

B. Catalyst headlines (color, not proof). For abnormal *single-stock* movers we attach a
   couple of recent headlines as *possible* catalysts — explicitly unconfirmed, since a
   headline near a price move does not establish causation. ETFs/commodity/crypto proxies get
   no headlines (there is no company news to attach), so this stays quiet for them.

Everything in layer A is computed from price series already pulled elsewhere plus a handful of
cheap theme-ETF pulls; layer B degrades to nothing when no news provider is configured.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from quantbot.analysis.movement import MoveContext, PositionMove
from quantbot.models import Fundamentals

# Minimum overlapping observations before a driver regression is trusted (matches benchmark).
MIN_OBS = 60
# Share of the move the theme must explain to be called systematic vs idiosyncratic.
_SYSTEMATIC_SHARE = 0.60
_IDIOSYNCRATIC_SHARE = 0.35

# A move smaller than this (percent, absolute) is too small to bother attributing.
_MIN_MOVE_PCT = 0.25


@dataclass(slots=True)
class DriverAttribution:
    symbol: str
    ret_pct: float                       # the name's own close-to-close move, percent
    driver: str                          # theme ticker used (e.g. "SI=F", "XLC")
    theme: str                           # human label (e.g. "silver", "comm-services")
    beta: float | None = None            # regressed sensitivity to the theme
    r_squared: float | None = None       # how tightly the name tracks the theme
    driver_ret_pct: float | None = None  # the theme's move today, percent
    explained_pct: float | None = None   # beta x driver move, in the name's own percent
    residual_pct: float | None = None    # ret_pct - explained_pct (name-specific)
    kind: str = "unattributed"           # systematic | mixed | idiosyncratic | unattributed

    @property
    def explained_share(self) -> float | None:
        """|explained| / (|explained| + |residual|), 0..1 — how much the theme accounts for."""
        if self.explained_pct is None or self.residual_pct is None:
            return None
        denom = abs(self.explained_pct) + abs(self.residual_pct)
        if denom == 0:
            return None
        return abs(self.explained_pct) / denom


@dataclass(slots=True)
class Catalyst:
    symbol: str
    headline: str
    source: str | None = None
    url: str | None = None
    when: date | None = None
    days_ago: int | None = None


@dataclass(slots=True)
class DriverModel:
    attributions: list[DriverAttribution] = field(default_factory=list)
    catalysts: list[Catalyst] = field(default_factory=list)


def driver_for(
    symbol: str,
    driver_map: dict[str, dict],
    default_driver: dict | None,
) -> dict | None:
    """Resolve a name to its {ticker, theme} driver, falling back to the default.

    Returns None when there is no usable driver (no default, or the driver is the name
    itself — a name cannot be attributed to its own series)."""
    entry = driver_map.get(symbol) or driver_map.get(symbol.upper()) or default_driver
    if not entry:
        return None
    ticker = entry.get("ticker")
    if not ticker or ticker.upper() == symbol.upper():
        return None
    return {"ticker": ticker, "theme": entry.get("theme") or ticker}


def needed_driver_tickers(
    move: MoveContext | None,
    driver_map: dict[str, dict],
    default_driver: dict | None,
    *,
    max_movers: int = 5,
) -> list[str]:
    """The distinct set of theme tickers the caller must fetch for this move's movers."""
    tickers: list[str] = []
    for m in _notable_movers(move, max_movers):
        d = driver_for(m.symbol, driver_map, default_driver)
        if d and d["ticker"] not in tickers:
            tickers.append(d["ticker"])
    return tickers


def _notable_movers(move: MoveContext | None, max_movers: int) -> list[PositionMove]:
    """Union of the top contributors and the abnormal names, deduped, biggest move first."""
    if move is None:
        return []
    seen: dict[str, PositionMove] = {}
    for m in list(move.top_contributors) + list(move.abnormal_names):
        if m.symbol not in seen:
            seen[m.symbol] = m
    movers = sorted(seen.values(), key=lambda m: -abs(m.ret_pct))
    return movers[:max_movers]


def _last_return(close: pd.Series) -> float | None:
    close = close.dropna()
    if len(close) < 2 or close.iloc[-2] == 0:
        return None
    return float(close.iloc[-1] / close.iloc[-2] - 1.0)


def _classify(share: float | None) -> str:
    if share is None:
        return "unattributed"
    if share >= _SYSTEMATIC_SHARE:
        return "systematic"
    if share <= _IDIOSYNCRATIC_SHARE:
        return "idiosyncratic"
    return "mixed"


def _attribute(
    mover: PositionMove,
    name_close: pd.Series,
    driver_close: pd.Series,
    theme: str,
    ticker: str,
) -> DriverAttribution:
    """Regress the name on its theme and split today's move into explained + residual."""
    attr = DriverAttribution(
        symbol=mover.symbol, ret_pct=mover.ret_pct, driver=ticker, theme=theme
    )

    name_ret = name_close.pct_change().replace([np.inf, -np.inf], np.nan)
    drv_ret = driver_close.pct_change().replace([np.inf, -np.inf], np.nan)
    aligned = pd.DataFrame({"n": name_ret, "d": drv_ret}).dropna()
    if len(aligned) < MIN_OBS:
        return attr

    n = aligned["n"].to_numpy()
    d = aligned["d"].to_numpy()
    var_d = float(np.var(d, ddof=1))
    if var_d <= 0:
        return attr

    beta = float(np.cov(n, d, ddof=1)[0, 1] / var_d)
    r = float(np.corrcoef(n, d)[0, 1])
    attr.beta = beta
    attr.r_squared = r * r

    drv_today = _last_return(driver_close)
    if drv_today is None:
        return attr
    attr.driver_ret_pct = drv_today * 100.0
    attr.explained_pct = beta * drv_today * 100.0
    attr.residual_pct = mover.ret_pct - attr.explained_pct
    attr.kind = _classify(attr.explained_share)
    return attr


def compute(
    move: MoveContext | None,
    price_frames: dict[str, pd.DataFrame],
    driver_frames: dict[str, pd.DataFrame],
    driver_map: dict[str, dict],
    default_driver: dict | None,
    *,
    fundamentals: dict[str, Fundamentals] | None = None,
    news_fn: Callable[[str], list[Catalyst]] | None = None,
    max_movers: int = 5,
) -> DriverModel:
    """Attribute each notable mover to its theme, and attach catalyst headlines to the
    abnormal single-stock movers. Degrades silently: a missing theme series yields an
    unattributed entry, and a missing news_fn yields no catalysts."""
    model = DriverModel()
    movers = _notable_movers(move, max_movers)
    if not movers:
        return model

    abnormal = {m.symbol for m in (move.abnormal_names if move else [])}

    for m in movers:
        if abs(m.ret_pct) < _MIN_MOVE_PCT:
            continue
        d = driver_for(m.symbol, driver_map, default_driver)
        name_df = price_frames.get(m.symbol)
        if d is None or name_df is None or "close" not in name_df.columns:
            model.attributions.append(
                DriverAttribution(symbol=m.symbol, ret_pct=m.ret_pct,
                                  driver="", theme="")
            )
            continue
        drv_df = driver_frames.get(d["ticker"])
        if drv_df is None or "close" not in drv_df.columns:
            model.attributions.append(
                DriverAttribution(symbol=m.symbol, ret_pct=m.ret_pct,
                                  driver=d["ticker"], theme=d["theme"])
            )
            continue
        model.attributions.append(
            _attribute(m, name_df["close"], drv_df["close"], d["theme"], d["ticker"])
        )

    # --- catalyst headlines: abnormal single-stock movers only ---
    if news_fn is not None:
        for m in movers:
            if m.symbol not in abnormal:
                continue
            if not _looks_like_stock(m.symbol, fundamentals):
                continue
            try:
                model.catalysts.extend(news_fn(m.symbol))
            except Exception:  # noqa: BLE001 - news is optional color, never fatal
                continue

    return model


def _looks_like_stock(symbol: str, fundamentals: dict[str, Fundamentals] | None) -> bool:
    """Heuristic ETF/proxy filter: a real single stock carries a sector; commodity, crypto
    and broad-index ETFs (SLV, IBIT, SPY) do not, so they get no company headlines."""
    if not fundamentals:
        return True
    fund = fundamentals.get(symbol)
    if fund is None:
        return True
    return bool(fund.sector)
