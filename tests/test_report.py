"""Tests for report building and formatting (no network, no LLM)."""

from __future__ import annotations

from datetime import date

from quantbot.analysis.macro import MacroReading, MacroSnapshot
from quantbot.analysis.risk import RiskMetrics
from quantbot.models import (
    AccountSummary,
    Flag,
    Fundamentals,
    Holding,
    Portfolio,
    TechnicalSnapshot,
)
from quantbot.report import builder, formatter


def _model():
    portfolio = Portfolio(
        account=AccountSummary(
            account_id="U1", base_currency="USD", net_liquidation=100000, total_cash=5000
        ),
        holdings=[
            Holding(symbol="AAPL", quantity=100, avg_cost=150, market_value=60000,
                    market_price=600, asset_class="STK"),
            Holding(symbol="MSFT", quantity=50, avg_cost=300, market_value=30000,
                    market_price=600, asset_class="STK"),
        ],
    )
    fundamentals = {
        "AAPL": Fundamentals(symbol="AAPL", sector="Technology", pe=28.0,
                             next_earnings=date(2026, 8, 13)),
        "MSFT": Fundamentals(symbol="MSFT", sector="Technology", pe=32.0),
    }
    technicals = {
        "AAPL": TechnicalSnapshot(symbol="AAPL", rsi14=75.0, ret_1m=3.2),
        "MSFT": TechnicalSnapshot(symbol="MSFT", rsi14=55.0, ret_1m=-1.1),
    }
    risk = RiskMetrics(
        invested_value=90000,
        weights={"AAPL": 0.6667, "MSFT": 0.3333},
        sector_weights={"Technology": 1.0},
        herfindahl=0.555,
        effective_positions=1.8,
        portfolio_beta=1.35,
        annualized_vol=0.22,
        sharpe=0.9,
        max_drawdown=-0.12,
        var_pct=0.021,
        top_position=("AAPL", 0.6667),
    )
    macro = MacroSnapshot(
        readings=[MacroReading(key="cpi", series_id="CPIAUCSL", latest=320.1,
                               latest_date="2026-07-01", yoy_pct=2.9)],
        events=[{"date": "2026-08-12", "event": "US CPI", "impact": "high"}],
    )
    the_flags = [
        Flag(code="CONCENTRATION", severity="high", symbol="AAPL",
             message="AAPL is 66.7% of the book — concentration risk."),
        Flag(code="HIGH_BETA", severity="warn", symbol=None,
             message="Portfolio beta ≈ 1.35 — elevated market risk."),
    ]
    return builder.build(portfolio, fundamentals, technicals, risk, macro, the_flags,
                         today=date(2026, 8, 11))


def test_build_orders_positions_by_value():
    model = _model()
    assert [p.symbol for p in model.positions] == ["AAPL", "MSFT"]
    assert model.positions[0].weight > model.positions[1].weight


def test_format_text_contains_key_sections():
    text = formatter.format_text(_model())
    assert "Morning Brief" in text
    assert "Portfolio Risk" in text
    assert "Flags (2)" in text
    assert "AAPL" in text
    assert "CPI" in text


def test_format_telegram_html_escapes_and_bolds():
    html = formatter.format_telegram_html(_model())
    assert "<b>" in html
    # Beta value present; no raw unescaped ampersands from content.
    assert "1.35" in html
    assert "&amp;" not in html or "&" not in html.replace("&amp;", "")


def test_holdings_rendered_as_aligned_table():
    text = formatter.format_text(_model())
    lines = text.splitlines()
    header = next(ln for ln in lines if ln.startswith("Sym"))
    aapl = next(ln for ln in lines if ln.startswith("AAPL "))
    msft = next(ln for ln in lines if ln.startswith("MSFT "))
    # Columns are space-padded to a common width across rows.
    assert len(aapl) == len(msft)
    # Money is abbreviated (k) and P/L is signed.
    assert "60.0k" in aapl and "+45.0k" in aapl
    # Header names the compact columns.
    assert "Wt%" in header and "1m%" in header


def test_telegram_tables_wrapped_in_pre():
    html = formatter.format_telegram_html(_model())
    assert "<pre>" in html and "</pre>" in html
    # Holdings and macro and risk grid -> at least three <pre> blocks.
    assert html.count("<pre>") >= 3


def test_narrative_prepended_when_present():
    model = _model()
    model.narrative = "Tech-heavy book, one concentration flag to watch."
    text = formatter.format_text(model)
    assert text.startswith("Tech-heavy book")
