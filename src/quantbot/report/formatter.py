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
    fmt_money as _fmt_money,
    fmt_num as _fmt_num,
    fmt_pct as _fmt_pct,
    signed_money as _signed_money,
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
                ["Sharpe", _fmt_num(r.sharpe),
                 "Max DD", _fmt_pct((r.max_drawdown or 0) * 100) if r.max_drawdown is not None else "—"],
                ["1d VaR", _fmt_pct((r.var_pct or 0) * 100) if r.var_pct else "—",
                 "Eff Pos", _fmt_num(r.effective_positions, 1)],
            ],
            aligns=["l", "r", "l", "r"],
        )
        out.append(("table", grid))
        if r.sector_weights:
            top_sectors = sorted(r.sector_weights.items(), key=lambda x: -x[1])[:3]
            sec_str = ", ".join(f"{s} {_fmt_pct(w * 100, 0)}" for s, w in top_sectors)
            out.append(("line", f"Top sectors: {sec_str}"))
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
        if d.avg_correlation is not None:
            out.append(("line", f"Avg correlation: {_fmt_num(d.avg_correlation, 2)} (weighted)"))
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
        if bm.windows:
            excess = "  ·  ".join(f"{w.label} {_signed_pct(w.excess_pct)}" for w in bm.windows)
            out.append(("line", f"Excess: {excess}"))
        stat_bits = []
        if bm.beta is not None:
            r2 = f" (R² {_fmt_num(bm.r_squared, 2)})" if bm.r_squared is not None else ""
            stat_bits.append(f"Beta {_fmt_num(bm.beta, 2)}{r2}")
        if bm.alpha_annual_pct is not None:
            stat_bits.append(f"alpha {_signed_pct(bm.alpha_annual_pct)}/yr")
        if bm.tracking_error_pct is not None:
            stat_bits.append(f"tracking error {_fmt_pct(bm.tracking_error_pct)}")
        if stat_bits:
            out.append(("line", "  ·  ".join(stat_bits)))
        if bm.up_capture is not None and bm.down_capture is not None:
            out.append(("line", f"Capture: up {_fmt_pct(bm.up_capture * 100, 0)}  ·  "
                                f"down {_fmt_pct(bm.down_capture * 100, 0)}"))
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
    # PE / earnings are sparse — keep them as compact footnotes, not table columns.
    for p in model.positions:
        notes = []
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
    parts = [f"<b>📈 Morning Brief — {escape(model.as_of.isoformat())}</b>"]
    parts.append(
        escape(f"Net liq {_fmt_money(model.net_liquidation, cur)}  ·  "
               f"Cash {_fmt_money(model.total_cash, cur)}")
    )
    if model.narrative:
        text = model.narrative.strip()
        if len(text) > 380:
            cut = text.rfind(". ", 0, 380)
            text = text[: cut + 1] if cut > 120 else text[:377].rstrip() + "…"
        parts.append(f"<i>{escape(text)}</i>")
    if model.flags:
        counts = {"high": 0, "warn": 0, "info": 0}
        for f in model.flags:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        summary = "  ".join(
            f"{_SEVERITY_EMOJI[s]}{counts[s]}" for s in ("high", "warn", "info") if counts.get(s)
        )
        parts.append(f"{summary}  ·  {len(model.flags)} flags")
    parts.append("📄 Tap the file above for the full brief.")
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
