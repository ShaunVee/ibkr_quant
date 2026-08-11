"""Tests for the HTML brief renderer and the photo caption (no browser, no network)."""

from __future__ import annotations

from quantbot.report import formatter, html
from tests.test_report import _model


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


def test_caption_has_headline_and_flag_summary():
    caption = formatter.format_caption(_model())
    assert "Morning Brief" in caption
    # Two flags in the sample: one high, one warn.
    assert "🔴1" in caption and "🟠1" in caption
    assert "2 flags" in caption
    # Nudge to open the attached document.
    assert "Tap the file" in caption
