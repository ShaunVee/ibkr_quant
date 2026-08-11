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

from quantbot.analysis import flags as flags_mod
from quantbot.analysis import fundamental, macro, risk, technical
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

    model = builder.build(
        portfolio,
        fundamentals,
        technicals,
        risk_metrics,
        macro_snapshot,
        the_flags,
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

    mode = config.delivery  # text | image | link | both
    today = model.as_of

    # Always render + persist the rich HTML — it's cheap, archives the day, and the web
    # sidecar serves it for the per-day link.
    html_doc = html_mod.render_html(model)
    reports_dir = Path(config.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{today.isoformat()}.html").write_text(html_doc, encoding="utf-8")
    store.save_report(today, "html", html_doc)
    store.save_report(today, "markdown", formatter.format_text(model))

    url = (
        f"{config.base_url}/{today.isoformat()}.html"
        if config.base_url and mode in ("link", "both")
        else None
    )

    notifier = TelegramNotifier(
        config.secrets.get("TELEGRAM_BOT_TOKEN"),
        config.secrets.get("TELEGRAM_CHAT_ID"),
    )

    delivered_image = False
    if mode in ("image", "both"):
        try:
            from quantbot.report.image import render_png

            png_path = render_png(
                html_doc, reports_dir / f"{today.isoformat()}.png", theme=config.image_theme
            )
            notifier.send_photo(png_path, formatter.format_caption(model, url))
            delivered_image = True
        except Exception as exc:  # noqa: BLE001 - degrade to text on any render/send issue
            log.warning("Image delivery unavailable (%s); falling back to text brief.", exc)

    if not delivered_image:
        body = formatter.format_telegram_html(model)
        if url:
            body += f'\n\n🔗 <a href="{url}">Full report</a>'
        notifier.send(body)

    log.info(
        "Report delivered to Telegram (mode=%s, image=%s, link=%s).",
        mode, delivered_image, bool(url),
    )


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
