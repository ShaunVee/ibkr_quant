"""Technical indicators computed directly from a price series.

Hand-rolled (pandas/numpy only) so results are deterministic, unit-testable, and free
of fragile third-party TA dependencies. Each indicator returns NaN-safe values; the
caller decides what to do with `None`s.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantbot.models import TechnicalSnapshot

TRADING_DAYS_YEAR = 252


def sma(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    return float(close.tail(window).mean())


def rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing via EWM with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float | None, float | None]:
    if len(close) < slow + signal:
        return None, None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1])


def bollinger_pct(close: pd.Series, window: int = 20, num_std: float = 2.0) -> float | None:
    """Position of last close within the Bollinger band, 0 (lower) .. 1 (upper)."""
    if len(close) < window:
        return None
    tail = close.tail(window)
    mid = tail.mean()
    std = tail.std(ddof=0)
    if std == 0:
        return 0.5
    upper = mid + num_std * std
    lower = mid - num_std * std
    return float((close.iloc[-1] - lower) / (upper - lower))


def atr(df: pd.DataFrame, period: int = 14) -> float | None:
    needed = {"high", "low", "close"}
    if not needed.issubset(df.columns) or len(df) < period + 1:
        return None
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1])


def _pct_return(close: pd.Series, lookback: int) -> float | None:
    if len(close) <= lookback:
        return None
    past = close.iloc[-1 - lookback]
    if past == 0:
        return None
    return float((close.iloc[-1] / past - 1.0) * 100.0)


def compute(symbol: str, df: pd.DataFrame) -> TechnicalSnapshot:
    """Compute a full TechnicalSnapshot from an OHLCV DataFrame (date-indexed)."""
    snap = TechnicalSnapshot(symbol=symbol)
    if df is None or df.empty or "close" not in df.columns:
        return snap

    close = df["close"].dropna()
    if close.empty:
        return snap

    snap.last_price = float(close.iloc[-1])
    snap.sma50 = sma(close, 50)
    snap.sma200 = sma(close, 200)
    if snap.sma50 is not None and snap.sma200 is not None:
        snap.golden_cross = snap.sma50 > snap.sma200
    snap.rsi14 = rsi(close, 14)
    snap.macd, snap.macd_signal = macd(close)
    snap.bollinger_pct = bollinger_pct(close)
    snap.atr14 = atr(df, 14)

    window_52w = close.tail(TRADING_DAYS_YEAR)
    hi = float(window_52w.max())
    lo = float(window_52w.min())
    if hi:
        snap.pct_from_52w_high = float((snap.last_price / hi - 1.0) * 100.0)
    if lo:
        snap.pct_from_52w_low = float((snap.last_price / lo - 1.0) * 100.0)

    snap.ret_1m = _pct_return(close, 21)
    snap.ret_3m = _pct_return(close, 63)
    snap.ret_6m = _pct_return(close, 126)
    return snap


def daily_returns(close: pd.Series) -> pd.Series:
    """Simple daily returns, used by risk metrics."""
    return close.pct_change().dropna()


def annualized_vol(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR))
