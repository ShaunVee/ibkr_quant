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
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from quantbot.analysis import changes as changes_mod
from quantbot.analysis import flags as flags_mod
from quantbot.analysis import (
    benchmark,
    contribution,
    covariance,
    diversification,
    events as events_mod,
    fundamental,
    macro,
    movement,
    risk,
    technical,
)
from quantbot.config import Config, load_config
from quantbot.ingestion.brokers.factory import make_broker
from quantbot.ingestion.marketdata.composite import MarketData
from quantbot.models import Portfolio, TechnicalSnapshot
from quantbot.report import builder, formatter, narrative
from quantbot.report.builder import ReportModel
from quantbot.storage.db import Store

log = logging.getLogger("quantbot")

_DEFAULT_DB = Path("data/portfolio.db")


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
    return portfolio


def stage_analyze(
    config: Config, portfolio: Portfolio, market: MarketData, store: Store
) -> ReportModel:
    today = _local_today(config)

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
    macro_snapshot = macro.gather(config, market)
    the_flags = flags_mod.evaluate(
        portfolio, fundamentals, technicals, risk_metrics, config.risk, today=today
    )

    # --- change/memory layer: contextualize today's move, diff flags vs last run ---
    account_id = portfolio.account.account_id
    prior_flags = store.prior_flags(account_id, today)          # None on first-ever run
    prior_weights = store.prior_position_weights(account_id, today)

    moves = movement.compute(
        price_frames, risk_metrics.weights, prior_weights=prior_weights or None
    )

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

    # --- event radar (analysis #4): forward calendar + rates exposure ---
    events_model = _events(
        config, market, store, fundamentals, risk_metrics.weights,
        price_frames, macro_snapshot, today,
    )

    store.save_flags(account_id, today, the_flags)             # persist before streaks
    flag_changes = changes_mod.diff(
        the_flags,
        prior_flags,
        streak_of=lambda code, sym: store.flag_streak(account_id, code, sym, today),
    )

    model = builder.build(
        portfolio,
        fundamentals,
        technicals,
        risk_metrics,
        macro_snapshot,
        the_flags,
        moves=moves,
        flag_changes=flag_changes,
        diversification=diversification_model,
        contribution=contribution_model,
        benchmark=benchmark_model,
        events=events_model,
        today=today,
    )

    # Persist the snapshot (weights, metrics) for historical risk tracking.
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


def _events(
    config: Config,
    market: MarketData,
    store: Store,
    fundamentals,
    weights: dict[str, float],
    price_frames: dict[str, pd.DataFrame],
    macro_snapshot,
    today: date,
):
    """Assemble the forward event radar. The only new fetch is the rates-proxy price
    series; everything else reuses data already gathered. Degrades to None on any issue."""
    cfg = config.events
    horizon = int(cfg.get("horizon_days", 14))
    proxy = cfg.get("rate_proxy", "TLT")
    try:
        proxy_close = None
        if proxy:
            df = market.daily_prices(proxy, config.history_days)
            if not df.empty:
                _cache_prices(store, proxy, df)
                proxy_close = df["close"]
        return events_mod.compute(
            fundamentals,
            weights,
            macro_events=macro_snapshot.events if macro_snapshot else None,
            price_frames=price_frames,
            rate_proxy_close=proxy_close,
            rate_proxy=proxy or "TLT",
            horizon_days=horizon,
            rate_beta_threshold=float(cfg.get("rate_beta_threshold", 0.20)),
            today=today,
        )
    except Exception as exc:  # noqa: BLE001 - event radar is optional context
        log.warning("Event radar failed (%s); skipping.", exc)
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
    from quantbot.delivery.telegram import TelegramNotifier
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
        except Exception as exc:  # noqa: BLE001 - degrade to text on any send issue
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
        portfolio = stage_ingest(config)
        market = MarketData(config)
        model = stage_analyze(config, portfolio, market, store)
        model = stage_report(config, model)
    except Exception as exc:  # noqa: BLE001
        log.exception("Pipeline failed during analysis")
        _notify_failure(config, str(exc))
        return 1

    if args.dry_run:
        _print_report(model)
        return 0

    try:
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
