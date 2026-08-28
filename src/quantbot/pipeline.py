"""Top-level orchestration + CLI.

Stages: ingest -> store -> analyze -> report -> deliver. Each stage is isolated with
its own error handling so a failed data provider degrades the report rather than
killing the run. On a hard failure the pipeline attempts a Telegram failure ping so a
silent morning is noticed.

Usage:
    python -m quantbot.pipeline                 # full run -> Telegram
    python -m quantbot.pipeline --stage ingest  # pull + print holdings only
    python -m quantbot.pipeline --dry-run       # full run, print report, no Telegram
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from quantbot.analysis import changes as changes_mod
from quantbot.analysis import flags as flags_mod
from quantbot.analysis import (
    benchmark,
    contribution,
    covariance,
    diversification,
    drivers as drivers_mod,
    events as events_mod,
    fundamental,
    macro,
    money as money_mod,
    movement,
    risk,
    stress,
    technical,
    trends,
)
from quantbot.config import Config, load_config
from quantbot.ingestion.brokers.factory import make_broker
from quantbot.ingestion.marketdata.composite import MarketData
from quantbot.models import Flag, Portfolio, TechnicalSnapshot
from quantbot.report import builder, formatter, narrative
from quantbot.report.builder import ReportModel
from quantbot.storage.db import Store

log = logging.getLogger("quantbot")

_DEFAULT_DB = Path("data/portfolio.db")

# Stage progress markers are printed to stdout with this prefix. The service runner
# (quantbot.scheduler.run_pipeline) picks them out and forwards them to Telegram so an
# on-demand /report shows live progress; on a plain run they're just visible in the log.
PROGRESS_PREFIX = "@@PROGRESS@@ "


def _progress(msg: str) -> None:
    print(f"{PROGRESS_PREFIX}{msg}", flush=True)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _local_today(config: Config) -> date:
    """Today's date in the report's configured timezone (not UTC / system local)."""
    try:
        tz = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Unknown timezone %r in config; falling back to UTC.", config.timezone)
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _prior_trading_day(run_date: date) -> date:
    """The most recent weekday before ``run_date`` — the freshest US session we can
    expect a morning run to reflect. Skips Sat/Sun so a Sun run correctly expects Fri
    (not a non-existent Sat session). US market holidays aren't modelled here.
    """
    d = run_date - timedelta(days=1)
    while d.weekday() >= 5:  # 5 = Sat, 6 = Sun
        d -= timedelta(days=1)
    return d


def _staleness_flag(report_date: date | None, run_date: date) -> Flag | None:
    """Warn only when the statement is *abnormally* stale.

    IBKR's Flex batch runs overnight, so a morning fetch routinely returns the session
    before the last completed one (most visibly a Sat run serving Thu instead of Fri).
    That one-session lag is a permanent property of how IBKR publishes data, not a
    problem worth repeating in every brief — so we tolerate it silently. We only flag
    when the report date is behind by *more* than that expected lag (a stuck/broken
    Flex feed), which is genuinely worth someone's attention. A US market holiday can
    still trip this benignly, hence the soft wording.
    """
    if report_date is None:
        return Flag(
            code="DATA_DATE_UNKNOWN",
            severity="warn",
            symbol=None,
            message=(
                "Broker statement carried no report date — can't confirm which session "
                "this P&L reflects."
            ),
        )
    # The freshest session we can *reasonably* expect after the overnight batch lag is
    # the one before the last completed session; anything at or after that is normal.
    expected = _prior_trading_day(run_date)
    tolerated = _prior_trading_day(expected)
    if report_date >= tolerated:
        return None
    return Flag(
        code="STALE_DATA",
        severity="warn",
        symbol=None,
        message=(
            f"Portfolio data is as of {report_date.isoformat()}, but the last completed "
            f"session was {expected.isoformat()} — that's further behind than IBKR's "
            "usual overnight batch lag. The Flex feed may be stuck (or it was a US "
            "holiday); P&L and prices reflect an older day's close."
        ),
    )


# --- stages ---------------------------------------------------------------
def stage_ingest(config: Config) -> Portfolio:
    broker = make_broker(config)
    portfolio = broker.get_portfolio()
    log.info(
        "Ingested %d holdings for account %s (invested %.2f %s)",
        len(portfolio.holdings),
        portfolio.account.account_id,
        portfolio.invested_value,
        portfolio.account.base_currency,
    )
    # Log the session the statement reflects vs. the day we ran, so the Flex batch-lag
    # turnover time is visible in `docker logs` (used to tune the schedule hour).
    run_date = _local_today(config)
    report_date = portfolio.report_date
    log.info(
        "Statement report_date=%s, run_date=%s (lag %s day(s))",
        report_date.isoformat() if report_date else "unknown",
        run_date.isoformat(),
        (run_date - report_date).days if report_date else "?",
    )
    return portfolio


def stage_analyze(
    config: Config, portfolio: Portfolio, market: MarketData, store: Store
) -> ReportModel:
    run_date = _local_today(config)
    # Key the whole brief off the session IBKR's statement actually reflects, not the
    # wall-clock day we happen to run. The Flex batch runs overnight, so a morning fetch
    # frequently still returns the *previous* session (most visibly on Sat, which would
    # otherwise claim to be Fri's close while serving Thu's). Falling back to run_date
    # keeps behaviour unchanged when the broker doesn't report a date.
    today = portfolio.report_date or run_date

    # Prices: fetch, cache to SQLite, build DataFrames for TA + risk.
    price_frames: dict[str, pd.DataFrame] = {}
    technicals: dict[str, TechnicalSnapshot] = {}
    for h in portfolio.equity_holdings:
        df = market.daily_prices(h.symbol, config.history_days)
        if not df.empty:
            _cache_prices(store, h.symbol, df)
            price_frames[h.symbol] = df
        technicals[h.symbol] = technical.compute(h.symbol, df)

    fundamentals = fundamental.gather(portfolio, market)

    risk_metrics = risk.compute(
        portfolio,
        fundamentals,
        price_frames,
        risk_free_rate=float(config.risk_param("risk_free_rate", 0.04)),
        var_confidence=float(config.risk_param("var_confidence", 0.95)),
    )
    # Realized drawdown from the recorded account-equity (net_liq) curve. snapshot_history
    # here excludes today (today's snapshot is saved further down), so append today's value
    # unless a same-day re-run already recorded it. Computed before flags so the DRAWDOWN
    # breach keys off the loss actually taken, not the simulated one.
    account_id_early = portfolio.account.account_id
    equity_curve = [
        r["net_liq"]
        for r in store.snapshot_history(account_id_early)
        if r.get("net_liq") is not None
    ]
    if portfolio.account.net_liquidation is not None:
        equity_curve.append(portfolio.account.net_liquidation)
    risk_metrics.realized_drawdown = risk.realized_drawdown(equity_curve)

    macro_snapshot = macro.gather(config, market)
    the_flags = flags_mod.evaluate(
        portfolio, fundamentals, technicals, risk_metrics, config.risk, today=today
    )
    stale_flag = _staleness_flag(portfolio.report_date, run_date)
    if stale_flag is not None:
        the_flags.insert(0, stale_flag)

    # --- change/memory layer: contextualize today's move, diff flags vs last run ---
    account_id = portfolio.account.account_id
    prior_flags = store.prior_flags(account_id, today)          # None on first-ever run
    prior_weights = store.prior_position_weights(account_id, today)

    moves = movement.compute(
        price_frames, risk_metrics.weights, prior_weights=prior_weights or None
    )

    # --- driver attribution (#7): why did each notable mover move? theme vs name-specific ---
    drivers_model = _drivers(config, market, store, moves, price_frames, fundamentals)

    # --- structure layer (A): correlation/covariance-derived analyses ---
    cov_model = covariance.build(price_frames)
    diversification_model = diversification.compute(
        cov_model,
        risk_metrics.weights,
        cluster_corr=float(config.risk_param("cluster_corr", 0.7)),
    )
    contribution_model = contribution.compute(cov_model, risk_metrics.weights)

    # --- benchmark-relative performance (analysis #5) ---
    benchmark_model = _benchmark(config, market, store, risk_metrics.weights, price_frames)

    # Rate proxy series — fetched once and shared by the event radar (#4) and the stress
    # test (#6), so a single bond-ETF pull feeds both.
    rate_proxy_symbol = config.events.get("rate_proxy", "TLT")
    rate_proxy_close = _rate_proxy_series(config, market, store, rate_proxy_symbol)

    # --- event radar (analysis #4): forward calendar + rates exposure ---
    events_model = _events(
        config, fundamentals, risk_metrics.weights, price_frames,
        macro_snapshot, rate_proxy_close, rate_proxy_symbol, today,
    )

    # --- scenario stress test (analysis #6): risk numbers -> dollar impact ---
    stress_model = _stress(
        config, risk_metrics, price_frames, portfolio.invested_value,
        rate_proxy_close, rate_proxy_symbol,
    )

    store.save_flags(account_id, today, the_flags)             # persist before streaks
    flag_changes = changes_mod.diff(
        the_flags,
        prior_flags,
        streak_of=lambda code, sym: store.flag_streak(account_id, code, sym, today),
    )

    # Persist today's snapshot (weights, metrics) *before* trending so the recorded
    # series includes today as its endpoint. prior_flags/prior_weights above already read
    # the earlier runs (they filter strictly < today), so this ordering is safe.
    store.save_snapshot(
        portfolio,
        metrics={
            "portfolio_beta": risk_metrics.portfolio_beta,
            "annualized_vol": risk_metrics.annualized_vol,
            "sharpe": risk_metrics.sharpe,
            "max_drawdown": risk_metrics.max_drawdown,
            "herfindahl": risk_metrics.herfindahl,
            "flag_count": len(the_flags),
        },
        snapshot_date=today,
    )

    # --- history & trends: read the recorded series back (None until 2+ snapshots) ---
    trend_model = trends.compute(store.snapshot_history(account_id))

    # --- your money, in plain English (#8): re-express the analytics above as dollars ---
    money_model = money_mod.compute(
        invested_value=portfolio.invested_value,
        currency=portfolio.account.base_currency,
        benchmark=benchmark_model,
        trends=trend_model,
        port_returns=risk.portfolio_return_series(risk_metrics.weights, price_frames),
        diversification=diversification_model,
        holdings=portfolio.holdings,
    )

    model = builder.build(
        portfolio,
        fundamentals,
        technicals,
        risk_metrics,
        macro_snapshot,
        the_flags,
        moves=moves,
        drivers=drivers_model,
        money=money_model,
        flag_changes=flag_changes,
        diversification=diversification_model,
        contribution=contribution_model,
        benchmark=benchmark_model,
        events=events_model,
        stress=stress_model,
        trends=trend_model,
        today=today,
        rsi_overbought=float(config.risk_param("rsi_overbought", 70.0)),
        rsi_oversold=float(config.risk_param("rsi_oversold", 30.0)),
    )
    return model


def _benchmark(
    config: Config,
    market: MarketData,
    store: Store,
    weights: dict[str, float],
    price_frames: dict[str, pd.DataFrame],
):
    """Fetch the benchmark and compute relative performance. Degrades to None (or
    drift-only) on any data issue so it never breaks the report."""
    cfg = config.benchmark
    symbol = cfg.get("symbol", "SPY")
    targets = cfg.get("targets") or None
    if not symbol and not targets:
        return None
    try:
        bench_close = None
        if symbol:
            df = market.daily_prices(symbol, config.history_days)
            if not df.empty:
                _cache_prices(store, symbol, df)
                bench_close = df["close"]
        port_returns = risk.portfolio_return_series(weights, price_frames)
        return benchmark.compute(
            port_returns,
            bench_close if bench_close is not None else pd.Series(dtype="float64"),
            symbol=symbol or "target",
            weights=weights,
            targets=targets,
            drift_tolerance=float(cfg.get("drift_tolerance", 0.05)),
        )
    except Exception as exc:  # noqa: BLE001 - benchmark is optional context
        log.warning("Benchmark analysis failed (%s); skipping.", exc)
        return None


def _drivers(
    config: Config,
    market: MarketData,
    store: Store,
    moves,
    price_frames: dict[str, pd.DataFrame],
    fundamentals,
):
    """Attribute today's notable movers to their themes + attach catalyst headlines.

    Fetches only the handful of theme tickers the movers actually need (free via the price
    provider, cached like every other series). Degrades to None on any issue so it never
    breaks the report."""
    cfg = config.drivers
    driver_map = {k.upper(): v for k, v in (cfg.get("map") or {}).items()}
    default_driver = cfg.get("default")
    if not driver_map and not default_driver:
        return None
    try:
        tickers = drivers_mod.needed_driver_tickers(
            moves, driver_map, default_driver,
            max_movers=int(cfg.get("max_movers", 5)),
        )
        driver_frames: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            if ticker in price_frames:            # already held — reuse the pulled series
                driver_frames[ticker] = price_frames[ticker]
                continue
            df = market.daily_prices(ticker, config.history_days)
            if not df.empty:
                _cache_prices(store, ticker, df)
                driver_frames[ticker] = df

        news_fn = None
        if config.providers.get("market_data", {}).get("news"):
            news_fn = lambda sym: _catalysts(market, sym)  # noqa: E731

        return drivers_mod.compute(
            moves, price_frames, driver_frames, driver_map, default_driver,
            fundamentals=fundamentals, news_fn=news_fn,
            max_movers=int(cfg.get("max_movers", 5)),
        )
    except Exception as exc:  # noqa: BLE001 - driver attribution is optional context
        log.warning("Driver attribution failed (%s); skipping.", exc)
        return None


def _catalysts(market: MarketData, symbol: str, limit: int = 2):
    """Map raw provider headlines to Catalyst records for one abnormal mover."""
    today = date.today()
    out = []
    for item in market.company_news(symbol, days_back=5)[:limit]:
        when = None
        days_ago = None
        raw = item.get("date")
        if raw:
            try:
                when = date.fromisoformat(raw)
                days_ago = (today - when).days
            except ValueError:
                when = None
        out.append(
            drivers_mod.Catalyst(
                symbol=symbol,
                headline=item.get("headline", ""),
                source=item.get("source"),
                url=item.get("url"),
                when=when,
                days_ago=days_ago,
            )
        )
    return out


def _rate_proxy_series(
    config: Config, market: MarketData, store: Store, symbol: str
) -> pd.Series | None:
    """Fetch the rates-proxy (bond ETF) close series once. Cached to SQLite and shared by
    the event radar and the stress test. Degrades to None on any issue."""
    if not symbol:
        return None
    try:
        df = market.daily_prices(symbol, config.history_days)
        if df.empty:
            return None
        _cache_prices(store, symbol, df)
        return df["close"]
    except Exception as exc:  # noqa: BLE001 - rate proxy is optional context
        log.warning("Rate proxy fetch failed (%s); skipping rate-sensitive analyses.", exc)
        return None


def _events(
    config: Config,
    fundamentals,
    weights: dict[str, float],
    price_frames: dict[str, pd.DataFrame],
    macro_snapshot,
    rate_proxy_close: pd.Series | None,
    rate_proxy: str,
    today: date,
):
    """Assemble the forward event radar. Reuses data already gathered (the rate proxy is
    fetched once by the caller). Degrades to None on any issue."""
    cfg = config.events
    horizon = int(cfg.get("horizon_days", 14))
    try:
        return events_mod.compute(
            fundamentals,
            weights,
            macro_events=macro_snapshot.events if macro_snapshot else None,
            price_frames=price_frames,
            rate_proxy_close=rate_proxy_close,
            rate_proxy=rate_proxy or "TLT",
            horizon_days=horizon,
            rate_beta_threshold=float(cfg.get("rate_beta_threshold", 0.20)),
            today=today,
        )
    except Exception as exc:  # noqa: BLE001 - event radar is optional context
        log.warning("Event radar failed (%s); skipping.", exc)
        return None


def _stress(
    config: Config,
    risk_metrics,
    price_frames: dict[str, pd.DataFrame],
    invested_value: float,
    rate_proxy_close: pd.Series | None,
    rate_proxy: str,
):
    """Size the book's downside under named shocks + its own worst history. Reuses the
    beta, return series and rate proxy already in hand. Degrades to None on any issue."""
    cfg = config.stress
    market_shocks = [float(s) for s in cfg.get("market_shocks", [-0.05, -0.10])]
    rate_shocks = [float(s) for s in cfg.get("rate_shocks", [-0.05])]
    try:
        port_returns = risk.portfolio_return_series(risk_metrics.weights, price_frames)
        proxy_returns = None
        if rate_proxy_close is not None and not rate_proxy_close.empty:
            proxy_returns = (
                rate_proxy_close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
            )
        return stress.compute(
            port_returns,
            beta=risk_metrics.portfolio_beta,
            invested_value=invested_value,
            market_shocks=market_shocks,
            rate_proxy_returns=proxy_returns,
            rate_proxy=rate_proxy,
            rate_shocks=rate_shocks,
            var_confidence=float(config.risk_param("var_confidence", 0.95)),
        )
    except Exception as exc:  # noqa: BLE001 - stress test is optional context
        log.warning("Stress test failed (%s); skipping.", exc)
        return None


def _cache_prices(store: Store, symbol: str, df: pd.DataFrame) -> None:
    rows = [
        {
            "px_date": idx.date().isoformat(),
            "open": _num(row.get("open")),
            "high": _num(row.get("high")),
            "low": _num(row.get("low")),
            "close": _num(row.get("close")),
            "volume": _num(row.get("volume")),
        }
        for idx, row in df.iterrows()
    ]
    store.upsert_prices(symbol, rows)


def _num(val) -> float | None:
    if val is None or pd.isna(val):
        return None
    return float(val)


def stage_report(config: Config, model: ReportModel) -> ReportModel:
    model.narrative = narrative.generate(model, config)
    return model


def stage_deliver(config: Config, model: ReportModel, store: Store) -> None:
    from quantbot.delivery.telegram import AmbiguousDeliveryError, TelegramNotifier
    from quantbot.report import html as html_mod

    mode = config.delivery  # html | text
    today = model.as_of

    # Render + persist the self-contained HTML brief. It's the attachment we send and an
    # archive of the day.
    html_doc = html_mod.render_html(model)
    reports_dir = Path(config.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / f"{today.isoformat()}.html"
    html_path.write_text(html_doc, encoding="utf-8")
    store.save_report(today, "html", html_doc)
    store.save_report(today, "markdown", formatter.format_text(model))

    notifier = TelegramNotifier(
        config.secrets.get("TELEGRAM_BOT_TOKEN"),
        config.secrets.get("TELEGRAM_CHAT_ID"),
    )

    if mode != "text":
        try:
            notifier.send_document(
                html_path,
                formatter.format_caption(model),
                filename=f"brief-{today.isoformat()}.html",
            )
            log.info("Report delivered to Telegram (HTML document).")
            return
        except AmbiguousDeliveryError as exc:
            # The upload almost certainly landed (and is archived to reports_dir).
            # Retrying would duplicate it and dumping the full text brief would spam
            # a second copy — so just note the hiccup and stop.
            log.warning(
                "HTML document delivery ambiguous (%s); file likely delivered and is "
                "archived at %s.", exc, html_path,
            )
            notifier.send(
                "📄 Morning brief uploaded — Telegram was slow to confirm. "
                "If the file didn't arrive, it's saved to the archive."
            )
            return
        except Exception as exc:  # noqa: BLE001 - degrade to text on a real send failure
            log.warning("HTML document delivery failed (%s); falling back to text brief.", exc)

    notifier.send(formatter.format_telegram_html(model))
    log.info("Report delivered to Telegram (text).")


def _notify_failure(config: Config, message: str) -> None:
    """Best-effort failure ping so a silent morning is noticed."""
    try:
        from quantbot.delivery.telegram import TelegramNotifier

        token = config.secrets.get("TELEGRAM_BOT_TOKEN", required=False)
        chat = config.secrets.get("TELEGRAM_CHAT_ID", required=False)
        if token and chat:
            TelegramNotifier(token, chat).send(f"⚠️ quantbot failed: {message}")
    except Exception as exc:  # noqa: BLE001
        log.error("Failure notification also failed: %s", exc)


# --- console output for ingest / dry-run ----------------------------------
def _print_report(model: ReportModel) -> None:
    print("\n" + formatter.format_text(model) + "\n")


def _run_full(config: Config, args) -> int:
    store = Store(args.db or _DEFAULT_DB)
    try:
        _progress("📥 Pulling portfolio from IBKR…")
        portfolio = stage_ingest(config)
        _progress(f"📊 Fetching market data & crunching {len(portfolio.holdings)} holdings…")
        market = MarketData(config)
        model = stage_analyze(config, portfolio, market, store)
        _progress("📝 Composing the brief…")
        model = stage_report(config, model)
    except Exception as exc:  # noqa: BLE001
        log.exception("Pipeline failed during analysis")
        _notify_failure(config, str(exc))
        return 1

    if args.dry_run:
        _print_report(model)
        return 0

    try:
        _progress("📤 Delivering to Telegram…")
        stage_deliver(config, model, store)
    except Exception as exc:  # noqa: BLE001
        log.exception("Delivery failed")
        _notify_failure(config, f"delivery error: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="quantbot pipeline")
    parser.add_argument(
        "--stage",
        choices=["ingest", "all"],
        default="all",
        help="'ingest' pulls+prints holdings only; 'all' runs the full pipeline.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run everything but print the report instead of sending to Telegram.",
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--db", default=None, help="Path to the SQLite database")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    config = load_config(args.config)

    if args.stage == "ingest":
        portfolio = stage_ingest(config)
        # Minimal holdings print without full analysis.
        for h in sorted(portfolio.holdings, key=lambda x: -(x.market_value or 0)):
            print(
                f"{h.symbol:<10}{h.quantity:>12.2f}{(h.market_value or 0):>15,.2f}"
                f"{h.asset_class:>7}"
            )
        return 0

    return _run_full(config, args)


if __name__ == "__main__":
    sys.exit(main())
