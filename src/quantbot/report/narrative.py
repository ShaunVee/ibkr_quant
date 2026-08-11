"""Claude-written morning-brief narrative over the deterministic ReportModel.

The LLM only summarizes the numbers already computed in code — it never invents
figures and never gives buy/sell advice (enforced by the system prompt). If the
Anthropic API key is absent or the call fails, a deterministic template is used so a
report is always produced.
"""

from __future__ import annotations

import logging

from quantbot.config import Config
from quantbot.report.builder import ReportModel
from quantbot.report.formatter import format_text

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are a portfolio analyst writing a concise morning brief for the account owner. "
    "You are given a deterministic report containing already-computed numbers, risk "
    "metrics, and rule-based flags. Write a tight 2-4 sentence summary that synthesizes "
    "what matters today: overall posture, the most important flags, and any imminent "
    "earnings or macro events.\n\n"
    "STRICT RULES:\n"
    "- Use ONLY numbers present in the provided report. Never invent or estimate figures.\n"
    "- Do NOT give buy/sell/hold advice or price predictions. Surface risks to look at.\n"
    "- Be factual and calm. No hype. Plain prose, no markdown headers.\n"
)


def generate(model: ReportModel, config: Config) -> str | None:
    """Return a narrative string, or None if narrative is disabled/unavailable."""
    settings = config.narrative
    if not settings.get("enabled", True):
        return None

    api_key = config.secrets.get("ANTHROPIC_API_KEY", required=False)
    report_text = format_text(model)

    if not api_key:
        log.info("ANTHROPIC_API_KEY not set — using deterministic narrative fallback.")
        return _fallback(model)

    try:
        return _call_claude(report_text, settings, api_key)
    except Exception as exc:  # noqa: BLE001 - never let narrative break the report
        log.warning("Claude narrative failed (%s) — using fallback.", exc)
        return _fallback(model)


def _call_claude(report_text: str, settings: dict, api_key: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    model_id = settings.get("model", "claude-sonnet-5")
    max_tokens = int(settings.get("max_tokens", 900))

    response = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    "Here is today's deterministic portfolio report. Write the morning "
                    "brief summary as instructed.\n\n" + report_text
                ),
            }
        ],
    )
    text = next((b.text for b in response.content if b.type == "text"), "").strip()
    return text or _fallback_from_text(report_text)


def _fallback(model: ReportModel) -> str:
    """Deterministic one-liner assembled from the computed model."""
    parts: list[str] = []
    cur = model.base_currency
    parts.append(
        f"Portfolio invested value {model.invested_value:,.0f} {cur} "
        f"across {len(model.positions)} positions."
    )
    if model.risk and model.risk.portfolio_beta is not None:
        parts.append(f"Beta ≈ {model.risk.portfolio_beta:.2f}.")

    high_flags = [f for f in model.flags if f.severity in ("high", "warn")]
    if high_flags:
        parts.append(
            f"{len(high_flags)} flag(s) to review: "
            + "; ".join(f.message for f in high_flags[:3])
        )
    else:
        parts.append("No flags breached your thresholds.")
    return " ".join(parts)


def _fallback_from_text(_: str) -> str:
    return "Morning brief generated (narrative model returned no text)."
