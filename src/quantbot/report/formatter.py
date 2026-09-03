"""Render a ReportModel to text (console/archive) and Telegram HTML.

Telegram HTML parse mode only requires escaping & < > — far more robust than MarkdownV2.
It has no <table> tag, so aligned tables are emitted as monospace <pre> blocks. A small
neutral IR (see _lines) keeps the text and HTML renderers in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

from quantbot.report.builder import ReportModel
from quantbot.report.numfmt import (
    abbr_money as _abbr_money,
)
from quantbot.report.numfmt import (
    fmt_money as _fmt_money,
)
from quantbot.report.numfmt import (
    fmt_num as _fmt_num,
)
from quantbot.report.numfmt import (
    fmt_pct as _fmt_pct,
)
from quantbot.report.numfmt import (
    signed_full_money as _signed_full_money,
)
from quantbot.report.numfmt import (
    signed_money as _signed_money,
)
from quantbot.report.numfmt import (
    signed_pct as _signed_pct,
)

_SEVERITY_EMOJI = {"high": "🔴", "warn": "🟠", "info": "🔵"}
# Render flags worst-first regardless of build order.
_SEVERITY_ORDER = {"high": 0, "warn": 1, "info": 2}

_MACRO_NAMES = {
    "cpi": "CPI",
    "core_cpi": "Core CPI",
    "unemployment": "Unemployment",
    "fed_funds": "Fed Funds",
    "ten_year": "10Y Yield",
}


# Split on sentence-ending punctuation followed by whitespace and a capital/digit — so
# the narrative's wall of prose becomes one scannable line per sentence. Decimals (no
# space after the dot) and lowercase abbreviations ("e.g.") are left intact.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    """Break narrative prose into individual sentences for line-per-sentence rendering."""
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


@dataclass(slots=True)
class _Table:
    """Column data for a table. Rendered as aligned monospace (text and <pre> HTML)."""

    rows: list[list[str]]
    aligns: list[str] = field(default_factory=list)  # "l" | "r" per column
    headers: list[str] | None = None


def _render_table(t: _Table) -> list[str]:
    """Pad columns to a common width; two spaces between columns."""
    body = ([t.headers] if t.headers else []) + t.rows
    ncols = max((len(r) for r in body), default=0)
    widths = [0] * ncols
    for r in body:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    def render(row: list[str]) -> str:
        cells = []
        for i in range(ncols):
            cell = row[i] if i < len(row) else ""
            align = t.aligns[i] if i < len(t.aligns) else "l"
            cells.append(cell.rjust(widths[i]) if align == "r" else cell.ljust(widths[i]))
        return "  ".join(cells).rstrip()

    lines: list[str] = []
    if t.headers:
        lines.append(render(t.headers))
    lines.extend(render(r) for r in t.rows)
    return lines


def _trend_metric(md: object) -> tuple[str, str, str]:
    """Map a MetricDrift to (label, start_str, end_str) for the Trends readout.

    Herfindahl is shown as effective positions (1/H) — more intuitive than the raw index
    and consistent with the Risk section. Volatility is a fraction rendered as a percent.
    """
    key, start, end = md.key, md.start, md.end
    if key == "annualized_vol":
        return "Vol", _fmt_pct(start * 100), _fmt_pct(end * 100)
    if key == "portfolio_beta":
        return "Beta", _fmt_num(start, 2), _fmt_num(end, 2)
    if key == "herfindahl":
        eff_s = 1 / start if start else None
        eff_e = 1 / end if end else None
        return "Eff. positions", _fmt_num(eff_s, 1), _fmt_num(eff_e, 1)
    if key == "flag_count":
        return "Flags", f"{int(start)}", f"{int(end)}"
    return key.replace("_", " ").title(), _fmt_num(start), _fmt_num(end)


_KIND_PHRASE = {
    "systematic": "mostly the theme",
    "idiosyncratic": "name-specific",
    "mixed": "theme + name",
}


def _attribution_line(a: object) -> str | None:
    """One-liner for a driver attribution, e.g.
    'SLV +3.1% — silver +2.8% (β1.02) → mostly the theme (residual +0.2pp)'.

    Falls back to a bare 'no theme reference' note when the regression couldn't run."""
    head = f"{a.symbol} {_signed_pct(a.ret_pct)}"
    if not a.theme or a.explained_pct is None or a.driver_ret_pct is None:
        return f"{head} — no theme reference available" if a.symbol else None
    beta = f"β{_fmt_num(a.beta, 2)}" if a.beta is not None else ""
    phrase = _KIND_PHRASE.get(a.kind, "")
    resid = ""
    if a.residual_pct is not None:
        resid = f" (residual {_signed_pct(a.residual_pct)})"
    tail = f" → {phrase}{resid}" if phrase else resid
    return f"{head} — {a.theme} {_signed_pct(a.driver_ret_pct)} ({beta}){tail}"


def _risk_gloss(r: object) -> str:
    """One plain-English sentence translating the risk grid into felt terms."""
    bits: list[str] = []
    if r.portfolio_beta is not None:
        bits.append(f"moves about {r.portfolio_beta:.1f}× the market")
    if r.annualized_vol:
        bits.append(f"a normal year swings roughly ±{r.annualized_vol * 100:.0f}%")
    return "; ".join(bits)


def _money_lines(model: ReportModel) -> list[tuple[str, object]]:
    """The plain-English 'Your Money' block (analysis #8): dollars first, no jargon."""
    m = model.money
    if m is None:
        return []
    cur = m.currency
    out: list[tuple[str, object]] = [("h2", "Your Money")]

    if m.windows:
        rows = [
            [
                w.label,
                _signed_pct(w.pct),
                f"≈ {_signed_full_money(w.pnl, cur)}",
            ]
            for w in m.windows
        ]
        out.append(("line", "What your holdings made/lost:"))
        out.append(("table", _Table(rows=rows, aligns=["l", "r", "r"])))

    if m.winners or m.losers:
        out.append((
            "line",
            f"Where you stand: {len(m.winners)} up, {len(m.losers)} underwater"
            + (f"  ·  {_signed_full_money(m.total_unrealized, cur)} unrealized"
               if m.total_unrealized is not None else ""),
        ))
        if m.winners:
            wins = ", ".join(f"{h.symbol} {_signed_money(h.pnl)}" for h in m.winners[:3])
            out.append(("line", f"  Winners: {wins}"))
        if m.losers:
            losses = ", ".join(f"{h.symbol} {_signed_money(h.pnl)}" for h in m.losers[:3])
            out.append(("line", f"  Underwater: {losses}"))

    if m.best_day is not None and m.worst_day is not None:
        out.append((
            "line",
            f"Best day {_signed_pct(m.best_day.ret_pct)} ({_signed_money(m.best_day.pnl)})  ·  "
            f"Worst day {_signed_pct(m.worst_day.ret_pct)} ({_signed_money(m.worst_day.pnl)})",
        ))

    if m.vs_index_pnl is not None and m.bench_symbol:
        verb = "ahead of" if m.vs_index_pnl >= 0 else "behind"
        out.append((
            "line",
            f"vs {m.bench_symbol} ({m.vs_index_label.lower()}): "
            f"you're {_signed_full_money(m.vs_index_pnl, cur)} — {verb} just buying the index.",
        ))

    if m.recovery is not None:
        out.append((
            "line",
            f"Underwater: {_fmt_pct(m.recovery.drawdown_pct)} below your peak — "
            f"needs {_signed_pct(m.recovery.gain_needed_pct)} to get back to even.",
        ))

    if m.betting_on:
        out.append(("line", f"Betting on: {m.betting_on}"))

    out.append(("blank", ""))
    return out


def _lines(model: ReportModel) -> list[tuple[str, object]]:
    """Return a list of (kind, payload) tuples. kind in {h1,h2,line,blank,table}.

    payload is a str for h1/h2/line and a _Table for table. A neutral intermediate
    representation so the text and HTML renderers stay in sync.
    """
    cur = model.base_currency
    out: list[tuple[str, object]] = []

    out.append(("h1", f"Morning Brief — {model.as_of.isoformat()}"))
    out.append(("line", f"Account {model.account_id}"))
    out.append(
        ("line", f"Net liq: {_fmt_money(model.net_liquidation, cur)}  ·  "
                 f"Cash: {_fmt_money(model.total_cash, cur)}  ·  "
                 f"Invested: {_fmt_money(model.invested_value, cur)}")
    )
    out.append(("blank", ""))

    # --- Your money, plain English (analysis #8) — lead with dollars ---
    out.extend(_money_lines(model))

    # --- Today's move (contextualized) ---
    mv = model.moves
    if mv is not None and mv.port_ret_pct is not None:
        out.append(("h2", "Today"))
        if mv.port_z is not None:
            tag = "unusual" if mv.unusual else "normal range"
            out.append(("line", f"Move: {_signed_pct(mv.port_ret_pct)}  ·  "
                                f"{abs(mv.port_z):.1f}σ ({tag})"))
        else:
            out.append(("line", f"Move: {_signed_pct(mv.port_ret_pct)}"))
        if mv.top_contributors:
            drivers = "  ·  ".join(
                f"{m.symbol} {m.contribution_pp:+.1f}pp" for m in mv.top_contributors
            )
            out.append(("line", f"Drivers: {drivers}"))
        if mv.abnormal_names:
            ab = "  ·  ".join(f"{m.symbol} ({m.z:+.1f}σ)" for m in mv.abnormal_names)
            out.append(("line", f"Abnormal: {ab}"))
        dv = model.drivers
        if dv is not None:
            for a in dv.attributions:
                line = _attribution_line(a)
                if line:
                    out.append(("line", f"Why: {line}"))
            for c in dv.catalysts:
                age = f"{c.days_ago}d ago" if c.days_ago is not None else "recent"
                src = f", {c.source}" if c.source else ""
                out.append(
                    ("line", f"  ↳ {c.symbol}: “{c.headline}” ({age}{src}) — "
                             f"possible catalyst, unconfirmed")
                )
        out.append(("blank", ""))

    # --- What changed since the last run ---
    new = [c for c in model.flag_changes if c.status == "new"]
    cleared = [c for c in model.flag_changes if c.status == "cleared"]
    ongoing = [
        c for c in model.flag_changes
        if c.status == "persistent" and c.streak >= 2 and c.flag.severity in ("high", "warn")
    ]
    if new or cleared or ongoing:
        out.append(("h2", "What Changed"))
        for c in new:
            out.append(("line", f"▲ NEW  {c.flag.message}"))
        for c in cleared:
            out.append(("line", f"✓ CLEARED  {c.flag.message}"))
        for c in sorted(ongoing, key=lambda c: -c.streak):
            out.append(("line", f"•  day {c.streak}  {c.flag.message}"))
        out.append(("blank", ""))

    # --- Trends (history layer): how the book moved over the recorded window ---
    tr = model.trends
    if tr is not None:
        out.append(("h2", f"Trends ({tr.sessions} sessions · {tr.span_days}d)"))
        if tr.value_start is not None and tr.value_end is not None:
            chg = tr.value_end - tr.value_start
            out.append(("line", f"Account value: {_abbr_money(tr.value_start)} → "
                                f"{_abbr_money(tr.value_end)}  ·  {_signed_money(chg)} "
                                f"(incl. any deposits/withdrawals)"))
        if tr.drawdown_from_peak is not None and tr.drawdown_from_peak < -0.001:
            out.append(("line", f"Down {_fmt_pct(abs(tr.drawdown_from_peak) * 100)} "
                                f"from the window peak"))
        for md in tr.metric_drifts:
            label, s_str, e_str = _trend_metric(md)
            out.append(("line", f"{label}: {s_str} → {e_str}"))
        if tr.weight_drifts:
            shifts = "  ·  ".join(
                f"{d.symbol} {d.delta * 100:+.1f}pp" for d in tr.weight_drifts
            )
            out.append(("line", f"Weight shifts: {shifts}"))
        out.append(("blank", ""))

    # --- Risk ---
    r = model.risk
    if r is not None:
        out.append(("h2", "Portfolio Risk"))
        if r.top_position:
            sym, w = r.top_position
            out.append(("line", f"Top position: {sym} ({_fmt_pct(w * 100)})"))
        grid = _Table(
            rows=[
                ["Beta", _fmt_num(r.portfolio_beta),
                 "Ann Vol", _fmt_pct((r.annualized_vol or 0) * 100) if r.annualized_vol else "—"],
                ["Max DD", _fmt_pct((r.realized_drawdown or 0) * 100) if r.realized_drawdown is not None else "—",
                 "1d VaR", _fmt_pct((r.var_pct or 0) * 100) if r.var_pct else "—"],
                ["Sim DD", _fmt_pct((r.max_drawdown or 0) * 100) if r.max_drawdown is not None else "—",
                 "Eff Pos", _fmt_num(r.effective_positions, 1)],
            ],
            aligns=["l", "r", "l", "r"],
        )
        out.append(("table", grid))
        gloss = _risk_gloss(r)
        if gloss:
            out.append(("line", f"In plain terms: {gloss}"))
        if r.sector_weights:
            top_sectors = sorted(r.sector_weights.items(), key=lambda x: -x[1])[:3]
            sec_str = ", ".join(f"{s} {_fmt_pct(w * 100, 0)}" for s, w in top_sectors)
            out.append(("line", f"Top sectors: {sec_str}"))
        out.append(("blank", ""))

    # --- Stress test (analysis #6): risk numbers -> dollar impact ---
    st = model.stress
    if st is not None:
        out.append(("h2", "Stress Test"))
        if st.scenarios:
            stress_tbl = _Table(
                headers=["Scenario", "Port%", "P/L"],
                aligns=["l", "r", "r"],
                rows=[
                    [s.label, _signed_pct(s.port_ret_pct), _signed_money(s.pnl)]
                    for s in st.scenarios
                ],
            )
            out.append(("table", stress_tbl))
        hist_bits = []
        if st.worst_day is not None:
            hist_bits.append(
                f"worst 1-day {_signed_pct(st.worst_day.ret_pct)} "
                f"({_signed_money(st.worst_day.pnl)})"
            )
        if st.worst_week is not None:
            hist_bits.append(
                f"worst 5-day {_signed_pct(st.worst_week.ret_pct)} "
                f"({_signed_money(st.worst_week.pnl)})"
            )
        if hist_bits:
            out.append(("line", "Historical: " + "  ·  ".join(hist_bits)))
        if st.cvar_pct is not None:
            cvar_pnl = f" ({_signed_money(st.cvar_pnl)})" if st.cvar_pnl is not None else ""
            out.append(("line", f"CVaR 95%: -{_fmt_pct(st.cvar_pct * 100)}{cvar_pnl} "
                                f"(avg loss on the worst days)"))
        out.append(("blank", ""))

    # --- Structure / diversification (analysis #1) ---
    d = model.diversification
    if d is not None:
        out.append(("h2", "Structure"))
        if d.effective_bets is not None:
            share = (
                f"  ·  top factor {_fmt_pct((d.top_factor_share or 0) * 100, 0)} of variance"
                if d.top_factor_share is not None else ""
            )
            out.append(("line", f"Effective bets: {_fmt_num(d.effective_bets, 1)} "
                                f"of {d.coverage} holdings{share}"))
        for c in d.clusters:
            out.append(("line", f"Cluster: {', '.join(c.symbols)} "
                                f"(corr {_fmt_num(c.avg_corr, 2)}  ·  "
                                f"{_fmt_pct(c.weight * 100, 0)} of book)"))
        out.append(("blank", ""))

    # --- Risk contribution (analysis #2) ---
    rc = model.contribution
    if rc is not None and rc.contributions:
        out.append(("h2", "Risk Contribution"))
        rc_tbl = _Table(
            headers=["Sym", "Wt%", "Risk%", "x"],
            aligns=["l", "r", "r", "r"],
            rows=[
                [
                    c.symbol,
                    _fmt_num(c.weight * 100, 1),
                    _fmt_num(c.risk_pct * 100, 1),
                    _fmt_num(c.ratio, 1) if c.ratio is not None else "—",
                ]
                for c in rc.contributions
            ],
        )
        out.append(("table", rc_tbl))
        top = rc.contributions[0]
        if top.ratio is not None and top.ratio >= 1.2:
            out.append(("line", f"{top.symbol} drives {_fmt_pct(top.risk_pct * 100, 0)} "
                                f"of risk on {_fmt_pct(top.weight * 100, 0)} weight."))
        out.append(("blank", ""))

    # --- Benchmark-relative (analysis #5) ---
    bm = model.benchmark
    if bm is not None:
        out.append(("h2", f"vs {bm.symbol}"))
        # Excess return / tracking error / capture / R² are all folded into "Your Money"
        # as dollars now; here we keep only how much of the ride is just market exposure.
        stat_bits = []
        if bm.beta is not None:
            stat_bits.append(f"Beta {_fmt_num(bm.beta, 2)} (share of moves that's just {bm.symbol})")
        if bm.alpha_annual_pct is not None:
            stat_bits.append(f"alpha {_signed_pct(bm.alpha_annual_pct)}/yr")
        if stat_bits:
            out.append(("line", "  ·  ".join(stat_bits)))
        for dr in bm.drifts:
            out.append(("line", f"Drift: {dr.symbol} {_fmt_pct(dr.weight * 100, 0)} vs "
                                f"{_fmt_pct(dr.target * 100, 0)} target "
                                f"({_signed_pct(dr.drift * 100, 0)})"))
        out.append(("blank", ""))

    # --- Event radar (analysis #4): forward calendar, weighted by exposure ---
    ev = model.events
    if ev is not None:
        out.append(("h2", f"Event Radar (next {ev.horizon_days}d)"))
        if ev.earnings:
            earn = "  ·  ".join(
                f"{e.symbol} {e.day.strftime('%a')}·{e.days_away}d ({_fmt_pct(e.weight * 100, 0)})"
                for e in ev.earnings
            )
            out.append(("line", f"Earnings: {earn}"))
            if ev.earnings_weight > 0:
                out.append(("line", f"→ {_fmt_pct(ev.earnings_weight * 100, 0)} "
                                    f"of the book reports inside the window."))
        if ev.macro:
            mac = "  ·  ".join(
                f"{m.event} {m.day.strftime('%a')}·{m.days_away}d"
                + (f" [{m.impact}]" if m.impact else "")
                for m in ev.macro[:6]
            )
            out.append(("line", f"Macro: {mac}"))
        if ev.rate_sensitive:
            names = "  ·  ".join(
                f"{s.symbol} β{s.beta:+.2f}" for s in ev.rate_sensitive[:5]
            )
            out.append(("line", f"Rate exposure: {_fmt_pct(ev.rate_sensitive_weight * 100, 0)} "
                                f"of the book moves with {ev.rate_proxy} — {names}"))
        out.append(("blank", ""))

    # --- Flags ---
    out.append(("h2", f"Flags ({len(model.flags)})"))
    if not model.flags:
        out.append(("line", "No flags — nothing breached your thresholds."))
    else:
        for f in sorted(model.flags, key=lambda x: _SEVERITY_ORDER.get(x.severity, 9)):
            emoji = _SEVERITY_EMOJI.get(f.severity, "•")
            out.append(("line", f"{emoji} {f.message}"))
    out.append(("blank", ""))

    # --- Holdings ---
    out.append(("h2", "Holdings"))
    holdings = _Table(
        headers=["Sym", "Wt%", "Val", "P/L", "RSI", "1m%"],
        aligns=["l", "r", "r", "r", "r", "r"],
        rows=[
            [
                p.symbol,
                _fmt_num((p.weight or 0) * 100, 1),
                _abbr_money(p.market_value),
                _signed_money(p.unrealized_pnl),
                _fmt_num(p.rsi14, 0),
                _signed_pct(p.ret_1m),
            ]
            for p in model.positions
        ],
    )
    out.append(("table", holdings))
    # Signal tags + sparse PE/earnings live as compact footnotes, not table columns.
    for p in model.positions:
        notes = [s.label for s in p.signals]
        if p.pe is not None:
            notes.append(f"PE {_fmt_num(p.pe)}")
        if p.days_to_earnings is not None and p.days_to_earnings >= 0:
            notes.append(f"earnings in {p.days_to_earnings}d")
        if notes:
            out.append(("line", f"{p.symbol}: {'  ·  '.join(notes)}"))
    out.append(("blank", ""))

    # --- Macro ---
    m = model.macro
    if m is not None:
        out.append(("h2", "Macro"))
        macro_tbl = _Table(
            headers=["Series", "Latest", "YoY"],
            aligns=["l", "r", "r"],
            rows=[
                [
                    _MACRO_NAMES.get(reading.key, reading.key.replace("_", " ").title()),
                    _fmt_num(reading.latest),
                    _signed_pct(reading.yoy_pct) if reading.yoy_pct is not None else "—",
                ]
                for reading in m.readings
            ],
        )
        out.append(("table", macro_tbl))
        # As-of dates vary per series; show the most recent as a single note.
        dates = [reading.latest_date for reading in m.readings if reading.latest_date]
        if dates:
            out.append(("line", f"As of {max(dates)}"))
        if m.events:
            out.append(("line", "Upcoming events:"))
            for e in m.events[:8]:
                impact = f" [{e['impact']}]" if e.get("impact") else ""
                out.append(("line", f"  {e['date']} — {e.get('event', '?')}{impact}"))
        out.append(("blank", ""))

    return out


def format_text(model: ReportModel) -> str:
    """Plain text: console, archive, and the input handed to the narrative model."""
    parts: list[str] = []
    for kind, payload in _lines(model):
        if kind == "h1":
            parts.append(f"=== {payload} ===")
        elif kind == "h2":
            parts.append(f"\n-- {payload} --")
        elif kind == "blank":
            parts.append("")
        elif kind == "table":
            assert isinstance(payload, _Table)
            parts.extend(_render_table(payload))
        else:
            parts.append(str(payload))
    body = "\n".join(parts)
    if model.narrative:
        summary = "\n".join(split_sentences(model.narrative))
        body = f"{summary}\n\n{body}"
    return body


def format_caption(model: ReportModel) -> str:
    """Short HTML caption for the HTML-document message: headline, a trimmed narrative,
    and a flag summary. Kept well under Telegram's 1024-char caption limit. Ends with a
    nudge to open the attached brief."""
    cur = model.base_currency
    parts = [f"<b>📈 Morning Brief · {escape(model.as_of.isoformat())}</b>"]
    parts.append(
        escape(f"💰 Net liq  {_fmt_money(model.net_liquidation, cur)}"
               f"    ·    Cash  {_fmt_money(model.total_cash, cur)}")
    )
    if model.narrative:
        # Trim on whole-sentence boundaries so the teaser never cuts mid-sentence. A raw
        # ". " scan would break on abbreviations ("vs.", "e.g."); split_sentences only
        # splits when a capital/digit follows, so it keeps them intact.
        sentences = split_sentences(model.narrative.strip())
        budget = 380
        kept: list[str] = []
        total = 0
        for s in sentences:
            if kept and total + len(s) + 1 > budget:
                break
            kept.append(s)
            total += len(s) + 1
        truncated = len(kept) < len(sentences)
        if kept and len(kept) == 1 and len(kept[0]) > budget + 20:
            # A single over-long sentence: hard-cap it so the caption stays bounded.
            kept[0] = kept[0][: budget - 3].rstrip() + "…"
        elif truncated and kept:
            kept[-1] = kept[-1].rstrip() + " …"
        body = "\n".join(escape(s) for s in kept)
        parts.append(f"<blockquote><i>{body}</i></blockquote>")
    parts.append("")
    if model.flags:
        counts = {"high": 0, "warn": 0, "info": 0}
        for f in model.flags:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        summary = "   ".join(
            f"{_SEVERITY_EMOJI[s]} {counts[s]}" for s in ("high", "warn", "info") if counts.get(s)
        )
        parts.append(f"🚩 <b>{len(model.flags)} flags</b>    {summary}")
    else:
        parts.append("✅ <b>No flags</b> — nothing breached your thresholds.")
    parts.append("📄 <i>Tap the file above for the full brief.</i>")
    return "\n".join(parts)


def format_telegram_html(model: ReportModel) -> str:
    """Telegram HTML: bold headers, escaped body, tables in <pre> monospace blocks."""
    parts: list[str] = []
    if model.narrative:
        summary = "\n".join(escape(s) for s in split_sentences(model.narrative))
        parts.append(f"<i>{summary}</i>\n")
    for kind, payload in _lines(model):
        if kind == "h1":
            parts.append(f"<b>📈 {escape(str(payload))}</b>")
        elif kind == "h2":
            parts.append(f"\n<b>{escape(str(payload))}</b>")
        elif kind == "blank":
            parts.append("")
        elif kind == "table":
            assert isinstance(payload, _Table)
            table_text = "\n".join(_render_table(payload))
            parts.append(f"<pre>{escape(table_text)}</pre>")
        else:
            parts.append(escape(str(payload)))
    return "\n".join(parts)
