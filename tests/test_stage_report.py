"""Tests for the narrative cache in pipeline.stage_report: a same-day re-run reuses the
stored brief instead of paying for a fresh Claude call (no network, no LLM)."""

from __future__ import annotations

from datetime import date

import pytest

from quantbot import pipeline
from quantbot.config import Config, Secrets
from quantbot.report import builder, narrative
from quantbot.storage.db import Store
from tests.test_report import _model


@pytest.fixture()
def config():
    return Config(raw={"narrative": {"enabled": True}}, secrets=Secrets())


def test_stage_report_caches_narrative_per_day(config, tmp_path, monkeypatch):
    store = Store(tmp_path / "test.db")
    calls = []

    def fake_generate(model, cfg):
        calls.append(1)
        return "Fresh Claude brief."

    monkeypatch.setattr(narrative, "generate", fake_generate)

    model = _model()  # as_of = 2026-08-11
    first = pipeline.stage_report(config, model, store)
    assert first.narrative == "Fresh Claude brief."
    assert len(calls) == 1

    # Same day again: served from the reports table, no second Claude call.
    second = pipeline.stage_report(config, builder.ReportModel(
        as_of=model.as_of, account_id=model.account_id, base_currency=model.base_currency,
        net_liquidation=model.net_liquidation, total_cash=model.total_cash,
        invested_value=model.invested_value,
    ), store)
    assert second.narrative == "Fresh Claude brief."
    assert len(calls) == 1
    assert store.get_report(date(2026, 8, 11), "narrative") == "Fresh Claude brief."

    # A different day is a cache miss and calls Claude again.
    model_next = _model()
    model_next.as_of = date(2026, 8, 12)
    pipeline.stage_report(config, model_next, store)
    assert len(calls) == 2
