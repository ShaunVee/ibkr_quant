"""Technical indicators computed directly from a price series.

Hand-rolled (pandas/numpy only) so results are deterministic, unit-testable, and free
of fragile third-party TA dependencies. Each indicator returns NaN-safe values; the
caller decides what to do with `None`s.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantbot.models import TechnicalSignal, TechnicalSnapshot

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


def obv(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> tuple[float | None, str | None]:
    """On-Balance Volume and its trend.

    OBV adds the day's volume on up-days and subtracts it on down-days — a running
    tally of whether volume is flowing into or out of the name (accumulation vs
    distribution). The absolute level is arbitrary, so the trend is what matters: we
    classify it by an EMA cross of the OBV line itself (fast above slow = rising).
    """
    if "close" not in df.columns or "volume" not in df.columns:
        return None, None
    close = df["close"]
    vol = df["volume"]
    if len(close) < 2:
        return None, None
    direction = np.sign(close.diff().fillna(0.0))
    obv_series = (direction * vol.fillna(0.0)).cumsum()
    last = float(obv_series.iloc[-1])
    if len(obv_series) < slow:
        return last, None
    ema_fast = obv_series.ewm(span=fast, adjust=False).mean().iloc[-1]
    ema_slow = obv_series.ewm(span=slow, adjust=False).mean().iloc[-1]
    if pd.isna(ema_fast) or pd.isna(ema_slow) or ema_fast == ema_slow:
        return last, None
    return last, ("rising" if ema_fast > ema_slow else "falling")


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
    snap.obv, snap.obv_trend = obv(df)
    return snap


# Thresholds for the Bollinger / 52-week "extreme" signals. Only surface these tags
# when a name is genuinely near an edge, so the summary stays signal-rich, not noisy.
_BOLL_UPPER = 0.95
_BOLL_LOWER = 0.05
_NEAR_52W_HIGH_PCT = -2.0   # within 2% below the 52w high
_NEAR_52W_LOW_PCT = 2.0     # within 2% above the 52w low


def derive_signals(
    snap: TechnicalSnapshot,
    *,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
) -> list[TechnicalSignal]:
    """Distil a snapshot into a short list of technical signal tags for the brief.

    "Signal summary only": emit a tag when a read is meaningful (trend direction,
    MACD posture, OBV flow always; RSI / Bollinger / 52w only at their extremes) and
    stay silent otherwise. Deterministic and threshold-driven so it's unit-testable.
    """
    out: list[TechnicalSignal] = []

    # Trend structure — 50d vs 200d (golden/death cross state).
    if snap.golden_cross is True:
        out.append(TechnicalSignal("TREND_UP", "uptrend 50>200", "bull"))
    elif snap.golden_cross is False:
        out.append(TechnicalSignal("TREND_DOWN", "downtrend 50<200", "bear"))

    # MACD posture — line vs signal.
    if snap.macd is not None and snap.macd_signal is not None:
        if snap.macd >= snap.macd_signal:
            out.append(TechnicalSignal("MACD_BULL", "MACD+", "bull"))
        else:
            out.append(TechnicalSignal("MACD_BEAR", "MACD−", "bear"))

    # RSI — only at extremes.
    if snap.rsi14 is not None:
        if snap.rsi14 >= rsi_overbought:
            out.append(TechnicalSignal("RSI_OVERBOUGHT", "overbought", "warn"))
        elif snap.rsi14 <= rsi_oversold:
            out.append(TechnicalSignal("RSI_OVERSOLD", "oversold", "warn"))

    # Bollinger band position — only when riding an edge.
    if snap.bollinger_pct is not None:
        if snap.bollinger_pct >= _BOLL_UPPER:
            out.append(TechnicalSignal("BB_UPPER", "at upper band", "warn"))
        elif snap.bollinger_pct <= _BOLL_LOWER:
            out.append(TechnicalSignal("BB_LOWER", "at lower band", "warn"))

    # 52-week range — only when near an edge.
    if snap.pct_from_52w_high is not None and snap.pct_from_52w_high >= _NEAR_52W_HIGH_PCT:
        out.append(TechnicalSignal("NEAR_52W_HIGH", "near 52w high", "bull"))
    elif snap.pct_from_52w_low is not None and snap.pct_from_52w_low <= _NEAR_52W_LOW_PCT:
        out.append(TechnicalSignal("NEAR_52W_LOW", "near 52w low", "bear"))

    # OBV — volume flow (accumulation vs distribution).
    if snap.obv_trend == "rising":
        out.append(TechnicalSignal("OBV_RISING", "OBV rising", "bull"))
    elif snap.obv_trend == "falling":
        out.append(TechnicalSignal("OBV_FALLING", "OBV falling", "bear"))

    return out


def daily_returns(close: pd.Series) -> pd.Series:
    """Simple daily returns, used by risk metrics."""
    return close.pct_change().dropna()


def annualized_vol(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR))
