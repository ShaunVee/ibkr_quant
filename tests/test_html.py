"""Tests for the HTML brief renderer and the photo caption (no browser, no network)."""

from __future__ import annotations

from quantbot.report import formatter, html
from tests.test_report import _model, _money_model


def test_render_html_is_a_complete_document():
    doc = html.render_html(_model())
    assert doc.startswith("<!doctype html>")
    assert "<title>Morning Brief" in doc
    assert doc.rstrip().endswith("</html>")
    # Self-contained: styles inlined, no external asset links.
    assert "<style>" in doc
    assert "http://" not in doc and "https://" not in doc


def test_render_html_contains_sections_and_data():
    doc = html.render_html(_model())
    for section in ("Portfolio Risk", "Allocation", "Flags", "Holdings", "Macro"):
        assert section in doc
    assert "AAPL" in doc and "MSFT" in doc
    assert "CPI" in doc
    # Total unrealized P/L is summed and signed in the hero.
    assert "Unrealized P/L" in doc


def test_render_html_marks_breach_tiles_from_flags():
    doc = html.render_html(_model())
    # The sample has a HIGH_BETA flag -> the Beta tile gets the crit treatment + badge.
    assert "flag-crit" in doc


def test_render_html_escapes_narrative():
    model = _model()
    model.narrative = "Risk & reward < balanced > today"
    doc = html.render_html(model)
    assert "Risk &amp; reward &lt; balanced &gt; today" in doc


def test_render_html_includes_your_money_section():
    doc = html.render_html(_money_model())
    assert "Your Money" in doc
    assert "just buying the index" in doc
    assert "to get back to even" in doc
    # Per-holding standing chips ("am I up or down on this one?").
    assert "Where you stand" in doc
    assert "AAPL +2.1k" in doc and "MSFT -1.4k" in doc
    # The money hero sits above the Portfolio Risk section.
    assert doc.index("Your Money") < doc.index("Portfolio Risk")


def test_caption_has_headline_and_flag_summary():
    caption = formatter.format_caption(_model())
    assert "Morning Brief" in caption
    # Two flags in the sample: one high, one warn.
    assert "🔴 1" in caption and "🟠 1" in caption
    assert "2 flags" in caption
    # Nudge to open the attached document.
    assert "Tap the file" in caption


def test_caption_deterministic_money_line_from_money_model():
    from quantbot.analysis.movement import MoveContext

    model = _money_model()  # vs_index_pnl=-1800, bench SPY
    model.moves = MoveContext(port_ret_pct=-1.2, port_z=-2.6, unusual=True)
    caption = formatter.format_caption(model)
    # Today's move + sigma tag, and the vs-index dollar figure, are always present
    # regardless of what the (LLM or fallback) narrative leads with.
    assert "Today -1.2% (2.6σ, unusual)" in caption
    assert "behind just buying SPY" in caption
    assert "-1,800 USD" in caption


def test_caption_deterministic_lines_omitted_when_no_data():
    caption = formatter.format_caption(_model())  # no moves/money set
    assert "Today +" not in caption and "Today -" not in caption
    assert "just buying" not in caption
