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
    "metrics, and rule-based flags. Write a tight 5-8 sentence summary (about 120 words "
    "max) that synthesizes what matters today.\n\n"
    "PRIORITY ORDER — earlier items are mandatory, later items only when notable:\n"
    "1. (always) The money lead below.\n"
    "2. (always) What CHANGED today and why the movers moved.\n"
    "3. (only if notable) Concentration, event radar, stress — mention one only when it "
    "carries news: a new or cleared flag, an unusual move (high sigma), a macro print "
    "inside a few days, or a scenario affecting a large share of the book. If nothing "
    "there changed, omit it entirely — do not restate standing figures.\n"
    "NOVELTY RULE: if nothing changed versus the previous run (same flags persisting, "
    "move within its normal range, no new catalysts), say so in one clause and keep the "
    "whole brief at the short end of the range. Do not re-explain a standing situation "
    "the owner has already read — a flag on its day-5 streak needs a nod, not a "
    "paragraph.\n\n"
    "LEAD in plain money, from the 'Your Money' section: what the holdings made or lost in "
    "the account currency over today / the past month / the past three months, whether the "
    "book is ahead of or behind just buying the index (the dollar figure), and — if "
    "underwater — how far below peak it is and the gain needed to break even. You may also "
    "name where the owner stands per holding (which names are in the green, which are "
    "underwater, and the standout on each side) from the 'Your Money' winners/losers list. "
    "Speak in currency a layman feels, not percentages alone. This is the owner's first "
    "question.\n"
    "THEN cover what CHANGED and whether today's move was notable — the 'Today' and 'What "
    "Changed' sections. Say if the move was within normal range or unusual (the sigma), "
    "and name what drove it. Call out newly-triggered and cleared flags before anything "
    "static. Demote unchanged posture and standing figures to supporting detail.\n"
    "EXPLAIN WHY the movers moved using the 'Why:' driver-attribution lines under Today. "
    "Each mover is split into what its theme (a reference index/commodity — e.g. silver, "
    "bitcoin, comm-services) explains via beta, versus a name-specific residual. Say whether "
    "a move was mostly the theme (systematic — 'the whole silver complex rose, SLV rode it') "
    "or name-specific (idiosyncratic — 'the sector was flat but this name fell on its own'). "
    "This is the answer to 'why', so give it real space. If a '↳' catalyst headline is "
    "present, you MAY mention it as a POSSIBLE, UNCONFIRMED catalyst for an idiosyncratic "
    "move — never state it as established cause, and never let it override the attribution "
    "math. Do not attach headlines to moves the theme already explains.\n"
    "When the report shows hidden concentration — few effective bets, a high top-factor "
    "share, a correlated cluster, or a name whose risk share far exceeds its weight — "
    "surface it: it is exactly what the owner can't see on a positions screen.\n"
    "The index counterfactual belongs in the money lead above; if a benchmark section is "
    "present, use it only to note how much of the ride is just market exposure (the beta) "
    "rather than skill, and mention any target-weight drift as a rebalancing cue.\n"
    "If an event radar is present, flag forward risk the owner is walking into: earnings "
    "clustering in the next few days and how much of the book reports, an imminent macro "
    "print (CPI/FOMC), and — if a large share of the book is rate-sensitive — that it is "
    "exposed to a rate print. Frame it as what to watch, never as a trade.\n"
    "If a stress test is present, ground the risk in money: cite the modelled dollar loss "
    "under a market shock and/or the book's worst historical day, so the owner feels the "
    "downside in currency, not just in beta and vol. Present it as sizing, not a forecast.\n\n"
    "STRICT RULES:\n"
    "- Use ONLY numbers, themes, and headlines present in the provided report. Never invent "
    "or estimate figures, and never introduce a cause or headline not in the report.\n"
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
        return _call_claude(model, report_text, settings, api_key)
    except Exception as exc:  # noqa: BLE001 - never let narrative break the report
        log.warning("Claude narrative failed (%s) — using fallback.", exc)
        return _fallback(model)


def _call_claude(model: ReportModel, report_text: str, settings: dict, api_key: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    model_id = settings.get("model", "claude-sonnet-5")
    max_tokens = int(settings.get("max_tokens", 4000))
    effort = settings.get("effort", "high")

    response = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=_SYSTEM,
        output_config={"effort": effort},
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
    # On Sonnet 5 adaptive thinking is on by default and shares the max_tokens budget
    # with the response, so a too-small cap can truncate before any text block is
    # produced. If that happens, fall back to the deterministic narrative rather than
    # a useless placeholder.
    text = next((b.text for b in response.content if b.type == "text"), "").strip()
    return text or _fallback(model)


def _fallback(model: ReportModel) -> str:
    """Deterministic one-liner assembled from the computed model."""
    parts: list[str] = []
    cur = model.base_currency

    mn = model.money
    if mn is not None:
        from quantbot.report.numfmt import signed_full_money, signed_pct

        if mn.windows:
            spans = ", ".join(
                f"{w.label.lower()} {signed_full_money(w.pnl, cur)}" for w in mn.windows
            )
            parts.append(f"Your holdings: {spans}.")
        if mn.vs_index_pnl is not None and mn.bench_symbol:
            verb = "ahead of" if mn.vs_index_pnl >= 0 else "behind"
            parts.append(
                f"That's {signed_full_money(mn.vs_index_pnl, cur)} {verb} just buying "
                f"{mn.bench_symbol}."
            )
        if mn.recovery is not None:
            parts.append(
                f"Down {mn.recovery.drawdown_pct:.1f}% from peak — needs "
                f"{signed_pct(mn.recovery.gain_needed_pct)} to break even."
            )
        if mn.winners or mn.losers:
            stand = f"{len(mn.winners)} up, {len(mn.losers)} underwater"
            if mn.winners:
                stand += f"; best {mn.winners[0].symbol} {signed_full_money(mn.winners[0].pnl, cur)}"
            if mn.losers:
                stand += f"; worst {mn.losers[0].symbol} {signed_full_money(mn.losers[0].pnl, cur)}"
            parts.append(stand + ".")

    mv = model.moves
    if mv is not None and mv.port_ret_pct is not None:
        sign = "+" if mv.port_ret_pct >= 0 else ""
        move = f"Today {sign}{mv.port_ret_pct:.1f}%"
        if mv.port_z is not None:
            move += f" ({abs(mv.port_z):.1f}σ, {'unusual' if mv.unusual else 'normal range'})"
        if mv.top_contributors:
            move += ", led by " + ", ".join(
                f"{m.symbol} {m.contribution_pp:+.1f}pp" for m in mv.top_contributors[:2]
            )
        parts.append(move + ".")

    dv = model.drivers
    if dv is not None and dv.attributions:
        whys = []
        for a in dv.attributions[:2]:
            if a.theme and a.kind in ("systematic", "mixed", "idiosyncratic"):
                if a.kind == "idiosyncratic":
                    whys.append(f"{a.symbol}'s move was name-specific")
                else:
                    whys.append(f"{a.symbol} tracked {a.theme} ({a.driver_ret_pct:+.1f}%)")
        if whys:
            parts.append("Why: " + "; ".join(whys) + ".")

    new = [c for c in model.flag_changes if c.status == "new"]
    cleared = [c for c in model.flag_changes if c.status == "cleared"]
    if new:
        parts.append(f"New: {'; '.join(c.flag.message for c in new[:2])}")
    if cleared:
        parts.append(f"Cleared: {'; '.join(c.flag.message for c in cleared[:2])}")

    ev = model.events
    if ev is not None:
        if ev.earnings and ev.earnings_weight > 0:
            names = ", ".join(f"{e.symbol} ({e.days_away}d)" for e in ev.earnings[:3])
            parts.append(
                f"Earnings ahead: {names} — {ev.earnings_weight * 100:.0f}% of the book "
                f"reports within {ev.horizon_days}d."
            )
        if ev.rate_sensitive and ev.rate_sensitive_weight >= 0.30:
            parts.append(
                f"{ev.rate_sensitive_weight * 100:.0f}% of the book moves with "
                f"{ev.rate_proxy} — watch rate prints."
            )

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
