"""Tests for presentation helpers (narrative sentence splitting)."""

from __future__ import annotations

from quantbot.report.formatter import split_sentences


def test_splits_on_sentence_boundaries():
    text = "Today +0.8%. No flags breached. Watch the CPI print."
    assert split_sentences(text) == [
        "Today +0.8%.",
        "No flags breached.",
        "Watch the CPI print.",
    ]


def test_does_not_split_decimals_or_percentages():
    text = "Beta is 0.42 with R2 0.31. 61% of the book moves with TLT."
    assert split_sentences(text) == [
        "Beta is 0.42 with R2 0.31.",
        "61% of the book moves with TLT.",
    ]


def test_empty_and_none_yield_no_lines():
    assert split_sentences("") == []
    assert split_sentences(None) == []
    assert split_sentences("   ") == []


def test_single_sentence_returns_one_line():
    assert split_sentences("A quiet day, nothing to flag.") == [
        "A quiet day, nothing to flag."
    ]
