"""Render a ReportModel to a complete, self-contained HTML document.

This is the rich channel: a styled, responsive, theme-aware page served per-day and
screenshotted to a PNG for Telegram. It reuses the same number formatting as the text
formatter so figures match across channels. No external assets — inline CSS only, so it
renders identically offline and inside a headless browser screenshot.
"""

from __future__ import annotations

from datetime import date
from html import escape

from quantbot.report.builder import ReportModel
from quantbot.report.numfmt import (
    abbr_money,
    fmt_money,
    fmt_num,
    fmt_pct,
    signed_full_money,
    signed_money,
    signed_pct,
)

_SEVERITY_ORDER = {"high": 0, "warn": 1, "info": 2}
_SEVERITY_LABEL = {"high": "high", "warn": "warn", "info": "info"}
_MACRO_NAMES = {
    "cpi": "CPI",
    "core_cpi": "Core CPI",
    "unemployment": "Unemployment",
    "fed_funds": "Fed Funds",
    "ten_year": "10Y Yield",
}
# Sequential blue ramp slots for the allocation bar (largest weight = deepest).
_ALLOC_SLOTS = 6

_CSS = """
:root {
  color-scheme: light;
  --ground:#eef1f6; --surface-1:#ffffff; --surface-2:#f7f9fc;
  --border:#e2e7f0; --ink-1:#12151c; --ink-2:#545d6e; --ink-3:#8b94a5;
  --accent:#2a78d6; --pos:#12864a; --neg:#d13438; --warn:#b96a06; --crit:#d13438; --info:#2563eb;
  --w1:#184f95; --w2:#256abf; --w3:#3987e5; --w4:#6da7ec; --w5:#9ec5f4; --w6:#cde2fb; --w-rest:#b7c0cf;
  --shadow:0 1px 2px rgba(16,22,38,.06),0 6px 20px rgba(16,22,38,.05);
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  color-scheme:dark;
  --ground:#0b0e14; --surface-1:#141a24; --surface-2:#1a212d;
  --border:#242d3b; --ink-1:#e9edf4; --ink-2:#a3adbe; --ink-3:#6c7789;
  --accent:#4f9bf0; --pos:#3ec77f; --neg:#f97066; --warn:#f5b544; --crit:#f97066; --info:#6aa6ff;
  --w1:#7fb2f0; --w2:#4f9bf0; --w3:#2f7ad6; --w4:#245f9f; --w5:#1c4a7d; --w6:#163a61; --w-rest:#3a4557;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
}}
:root[data-theme="dark"]{
  color-scheme:dark;
  --ground:#0b0e14; --surface-1:#141a24; --surface-2:#1a212d;
  --border:#242d3b; --ink-1:#e9edf4; --ink-2:#a3adbe; --ink-3:#6c7789;
  --accent:#4f9bf0; --pos:#3ec77f; --neg:#f97066; --warn:#f5b544; --crit:#f97066; --info:#6aa6ff;
  --w1:#7fb2f0; --w2:#4f9bf0; --w3:#2f7ad6; --w4:#245f9f; --w5:#1c4a7d; --w6:#163a61; --w-rest:#3a4557;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
}
*{box-sizing:border-box;}
body{margin:0;background:var(--ground);color:var(--ink-1);font-family:var(--sans);
  line-height:1.5;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums;}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px 44px;}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;}
.pos{color:var(--pos);} .neg{color:var(--neg);}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3);font-weight:600;margin:0 0 6px;}
.title-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;}
h1{font-size:clamp(24px,6vw,32px);margin:0;letter-spacing:-.02em;font-weight:700;}
.acct{font-family:var(--mono);font-size:12px;color:var(--ink-2);background:var(--surface-1);
  border:1px solid var(--border);padding:4px 9px;border-radius:999px;white-space:nowrap;}
.hero{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-top:16px;padding:16px 18px;
  background:var(--surface-1);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);}
.hero .big{font-size:clamp(30px,8vw,42px);font-weight:700;letter-spacing:-.02em;}
.hero .cur{font-size:15px;color:var(--ink-3);font-weight:600;}
.hero .sub{margin-left:auto;text-align:right;font-size:13px;color:var(--ink-2);}
.hero .sub b{color:var(--ink-1);font-weight:600;}
.lead{margin:18px 0 26px;padding:16px 18px 16px 20px;background:var(--surface-2);border:1px solid var(--border);
  border-left:3px solid var(--accent);border-radius:0 12px 12px 0;font-size:15px;line-height:1.6;}
section{margin-bottom:30px;}
.sec-head{display:flex;align-items:center;gap:10px;margin:0 0 14px;}
.sec-head h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);margin:0;font-weight:700;}
.sec-head .rule{flex:1;height:1px;background:var(--border);}
.sec-head .count{font-family:var(--mono);font-size:12px;color:var(--ink-3);background:var(--surface-1);
  border:1px solid var(--border);padding:2px 8px;border-radius:999px;}
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;}
@media(min-width:560px){.tiles{grid-template-columns:repeat(3,1fr);}}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:13px 14px;position:relative;overflow:hidden;}
.tile .k{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);font-weight:600;}
.tile .v{font-size:22px;font-weight:700;margin-top:4px;letter-spacing:-.01em;}
.tile .n{font-size:11px;color:var(--ink-3);margin-top:2px;}
.tile.flag-crit{border-color:color-mix(in srgb,var(--crit) 45%,var(--border));}
.tile.flag-crit .v{color:var(--crit);}
.tile .badge{position:absolute;top:10px;right:10px;font-size:10px;font-weight:700;letter-spacing:.04em;
  text-transform:uppercase;color:var(--crit);background:color-mix(in srgb,var(--crit) 14%,transparent);padding:2px 6px;border-radius:6px;}
.context{margin-top:12px;font-size:13px;color:var(--ink-2);display:flex;flex-wrap:wrap;gap:4px 18px;}
.context b{color:var(--ink-1);font-weight:600;}
.alloc-bar{display:flex;height:30px;border-radius:8px;overflow:hidden;gap:2px;background:var(--border);}
.alloc-bar>span{display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700;font-family:var(--mono);min-width:0;text-shadow:0 1px 1px rgba(0,0,0,.3);}
.alloc-legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:12px;font-size:12.5px;color:var(--ink-2);}
.alloc-legend .it{display:flex;align-items:center;gap:7px;}
.alloc-legend .sw{width:11px;height:11px;border-radius:3px;flex:none;}
.alloc-legend b{color:var(--ink-1);font-weight:600;font-family:var(--mono);}
.flags{display:flex;flex-direction:column;gap:8px;}
.flag{display:flex;align-items:flex-start;gap:11px;padding:11px 14px;background:var(--surface-1);
  border:1px solid var(--border);border-left-width:3px;border-radius:0 10px 10px 0;font-size:14px;}
.flag .dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:none;}
.flag .msg{flex:1;} .flag b{font-weight:600;}
.flag .tag{font-family:var(--mono);font-size:11px;font-weight:700;padding:1px 6px;border-radius:5px;
  white-space:nowrap;text-transform:uppercase;letter-spacing:.03em;}
.sev-high{border-left-color:var(--crit);} .sev-high .dot{background:var(--crit);}
.sev-high .tag{color:var(--crit);background:color-mix(in srgb,var(--crit) 14%,transparent);}
.sev-warn{border-left-color:var(--warn);} .sev-warn .dot{background:var(--warn);}
.sev-warn .tag{color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent);}
.sev-info{border-left-color:var(--info);} .sev-info .dot{background:var(--info);}
.sev-info .tag{color:var(--info);background:color-mix(in srgb,var(--info) 14%,transparent);}
.holdings{display:flex;flex-direction:column;gap:10px;}
.hold{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:14px;
  display:grid;grid-template-columns:1fr auto;gap:10px 14px;align-items:center;}
.hold .id{display:flex;align-items:baseline;gap:9px;}
.hold .sym{font-size:17px;font-weight:700;letter-spacing:-.01em;}
.hold .wt{font-family:var(--mono);font-size:12px;color:var(--ink-3);}
.hold .mv{font-family:var(--mono);font-size:16px;font-weight:600;text-align:right;}
.hold .track{grid-column:1/-1;height:6px;background:var(--surface-2);border-radius:4px;overflow:hidden;border:1px solid var(--border);}
.hold .fill{height:100%;background:var(--accent);border-radius:4px;}
.hold .meta{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px 8px;}
.chip{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);background:var(--surface-2);
  border:1px solid var(--border);padding:2px 8px;border-radius:6px;}
.chip.up{color:var(--pos);border-color:color-mix(in srgb,var(--pos) 30%,var(--border));}
.chip.down{color:var(--neg);border-color:color-mix(in srgb,var(--neg) 30%,var(--border));}
.macro-tbl{width:100%;border-collapse:collapse;font-size:14px;}
.macro-tbl th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);font-weight:600;padding:0 12px 8px;}
.macro-tbl th.r,.macro-tbl td.r{text-align:right;}
.macro-tbl td{padding:10px 12px;border-top:1px solid var(--border);font-family:var(--mono);}
.macro-tbl td.name{font-family:var(--sans);font-weight:600;}
.macro-tbl tr:first-child td{border-top:none;}
.macro-tbl .as-of{font-family:var(--sans);font-size:11px;color:var(--ink-3);font-weight:400;}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--border);font-size:12px;color:var(--ink-3);
  display:flex;flex-wrap:wrap;gap:4px 14px;justify-content:space-between;}
footer .note{max-width:60%;}
""".strip()


def _pl_class(val: float | None) -> str:
    if val is None:
        return ""
    return " pos" if val >= 0 else " neg"


def _fmt_date(d: date) -> str:
    return d.strftime("%A, %b %-d %Y")


def _header(model: ReportModel) -> str:
    cur = model.base_currency
    total_pnl = sum(p.unrealized_pnl for p in model.positions if p.unrealized_pnl is not None)
    pnl_cls = "pos" if total_pnl >= 0 else "neg"
    return f"""
  <header>
    <p class="eyebrow">📈 Morning Brief</p>
    <div class="title-row">
      <h1>{escape(_fmt_date(model.as_of))}</h1>
      <span class="acct">Account {escape(model.account_id)}</span>
    </div>
    <div class="hero">
      <span class="big num">{escape(f"{model.net_liquidation:,.0f}" if model.net_liquidation is not None else "—")}</span>
      <span class="cur">{escape(cur)} net liq</span>
      <span class="sub">Cash <b class="num">{escape(f"{(model.total_cash or 0):,.0f}")}</b>
        &nbsp;·&nbsp; Invested <b class="num">{escape(f"{model.invested_value:,.0f}")}</b><br>
        Unrealized P/L <b class="num {pnl_cls}">{escape(signed_full_money(total_pnl, cur))}</b></span>
    </div>
  </header>"""


def _narrative(model: ReportModel) -> str:
    if not model.narrative:
        return ""
    return f'\n  <p class="lead">{escape(model.narrative.strip())}</p>'


def _risk(model: ReportModel) -> str:
    r = model.risk
    if r is None:
        return ""
    codes = {f.code for f in model.flags}
    dd_crit = " flag-crit" if "DRAWDOWN" in codes else ""
    dd_badge = '<span class="badge">breach</span>' if "DRAWDOWN" in codes else ""
    beta_crit = " flag-crit" if "HIGH_BETA" in codes else ""
    beta_badge = '<span class="badge">high</span>' if "HIGH_BETA" in codes else ""

    ann_vol = fmt_pct((r.annualized_vol or 0) * 100) if r.annualized_vol else "—"
    max_dd = fmt_pct((r.max_drawdown or 0) * 100) if r.max_drawdown is not None else "—"
    var = fmt_pct((r.var_pct or 0) * 100) if r.var_pct else "—"

    tiles = f"""
      <div class="tile{beta_crit}">{beta_badge}<div class="k">Beta</div><div class="v num">{escape(fmt_num(r.portfolio_beta))}</div><div class="n">market sensitivity</div></div>
      <div class="tile"><div class="k">Ann. Vol</div><div class="v num">{escape(ann_vol)}</div><div class="n">annualized</div></div>
      <div class="tile"><div class="k">Sharpe</div><div class="v num">{escape(fmt_num(r.sharpe))}</div><div class="n">risk-adjusted</div></div>
      <div class="tile{dd_crit}">{dd_badge}<div class="k">Max Drawdown</div><div class="v num">{escape(max_dd)}</div><div class="n">peak-to-trough</div></div>
      <div class="tile"><div class="k">1-Day VaR</div><div class="v num">{escape(var)}</div><div class="n">95% historical</div></div>
      <div class="tile"><div class="k">Eff. Positions</div><div class="v num">{escape(fmt_num(r.effective_positions, 1))}</div><div class="n">of {len(model.positions)} held</div></div>"""

    context_bits = []
    if r.top_position:
        sym, w = r.top_position
        context_bits.append(f"<span>Top position <b>{escape(sym)} {escape(fmt_pct(w * 100))}</b></span>")
    if r.sector_weights:
        top = sorted(r.sector_weights.items(), key=lambda x: -x[1])[:3]
        sec = " · ".join(f"{escape(s)} {escape(fmt_pct(w * 100, 0))}" for s, w in top)
        context_bits.append(f"<span>Sectors {sec}</span>")
    context = f'\n    <div class="context">{"".join(context_bits)}</div>' if context_bits else ""

    return f"""
  <section>
    <div class="sec-head"><h2>Portfolio Risk</h2><span class="rule"></span></div>
    <div class="tiles">{tiles}
    </div>{context}
  </section>"""


def _allocation(model: ReportModel) -> str:
    rows = [(p.symbol, (p.weight or 0) * 100) for p in model.positions if (p.weight or 0) > 0]
    if not rows:
        return ""
    slots = [f"var(--w{i})" for i in range(1, _ALLOC_SLOTS + 1)]
    segs, legend = [], []
    for i, (sym, pct) in enumerate(rows):
        color = slots[i] if i < len(slots) else "var(--w-rest)"
        segs.append(f'<span style="flex:{pct:.2f};background:{color}">{escape(sym)}</span>')
        legend.append(
            f'<span class="it"><span class="sw" style="background:{color}"></span>'
            f'{escape(sym)} <b>{escape(fmt_pct(pct))}</b></span>'
        )
    return f"""
  <section>
    <div class="sec-head"><h2>Allocation</h2><span class="rule"></span></div>
    <div class="alloc-bar">{"".join(segs)}</div>
    <div class="alloc-legend">{"".join(legend)}</div>
  </section>"""


def _flags(model: ReportModel) -> str:
    head = f'<span class="count">{len(model.flags)}</span>'
    if not model.flags:
        body = '<div class="flag sev-info"><span class="dot"></span><span class="msg">No flags — nothing breached your thresholds.</span></div>'
    else:
        items = []
        for f in sorted(model.flags, key=lambda x: _SEVERITY_ORDER.get(x.severity, 9)):
            sev = f.severity if f.severity in _SEVERITY_LABEL else "info"
            items.append(
                f'<div class="flag sev-{sev}"><span class="dot"></span>'
                f'<span class="msg">{escape(f.message)}</span>'
                f'<span class="tag">{escape(_SEVERITY_LABEL[sev])}</span></div>'
            )
        body = "".join(items)
    return f"""
  <section>
    <div class="sec-head"><h2>Flags</h2>{head}<span class="rule"></span></div>
    <div class="flags">{body}</div>
  </section>"""


def _holdings(model: ReportModel) -> str:
    if not model.positions:
        return ""
    top_w = max((p.weight or 0) for p in model.positions) or 1.0
    cards = []
    for p in model.positions:
        w = (p.weight or 0)
        fill = min(100.0, (w / top_w) * 100) if top_w else 0
        chips = []
        if p.unrealized_pnl is not None:
            chips.append(f'<span class="chip{_pl_class(p.unrealized_pnl)}">P/L {escape(signed_money(p.unrealized_pnl))}</span>')
        if p.pe is not None:
            chips.append(f'<span class="chip">PE {escape(fmt_num(p.pe))}</span>')
        if p.rsi14 is not None:
            chips.append(f'<span class="chip">RSI {escape(fmt_num(p.rsi14, 0))}</span>')
        if p.ret_1m is not None:
            cls = "up" if p.ret_1m >= 0 else "down"
            chips.append(f'<span class="chip {cls}">1m {escape(signed_pct(p.ret_1m))}</span>')
        if p.days_to_earnings is not None and p.days_to_earnings >= 0:
            chips.append(f'<span class="chip">earnings {p.days_to_earnings}d</span>')
        cards.append(f"""
      <div class="hold">
        <div class="id"><span class="sym">{escape(p.symbol)}</span><span class="wt">{escape(fmt_pct(w * 100))}</span></div>
        <div class="mv num">{escape(abbr_money(p.market_value))}</div>
        <div class="track"><div class="fill" style="width:{fill:.1f}%"></div></div>
        <div class="meta">{"".join(chips)}</div>
      </div>""")
    return f"""
  <section>
    <div class="sec-head"><h2>Holdings</h2><span class="rule"></span></div>
    <div class="holdings">{"".join(cards)}
    </div>
  </section>"""


def _macro(model: ReportModel) -> str:
    m = model.macro
    if m is None or not m.readings:
        return ""
    rows = []
    for reading in m.readings:
        name = _MACRO_NAMES.get(reading.key, reading.key.replace("_", " ").title())
        as_of = f' <span class="as-of">{escape(str(reading.latest_date))}</span>' if reading.latest_date else ""
        yoy = reading.yoy_pct
        if yoy is None:
            yoy_cell = "—"
        else:
            arrow = "▲" if yoy > 0.05 else ("▼" if yoy < -0.05 else "—")
            yoy_cell = f"{escape(signed_pct(yoy))} {arrow}"
        rows.append(
            f'<tr><td class="name">{escape(name)}{as_of}</td>'
            f'<td class="r">{escape(fmt_num(reading.latest))}</td>'
            f'<td class="r">{yoy_cell}</td></tr>'
        )
    return f"""
  <section>
    <div class="sec-head"><h2>Macro</h2><span class="rule"></span></div>
    <table class="macro-tbl">
      <thead><tr><th>Series</th><th class="r">Latest</th><th class="r">YoY</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </section>"""


def render_html(model: ReportModel) -> str:
    """Return a complete standalone HTML document for the brief."""
    body = "".join(
        part
        for part in (
            _header(model),
            _narrative(model),
            _risk(model),
            _allocation(model),
            _flags(model),
            _holdings(model),
            _macro(model),
        )
        if part
    )
    footer = """
  <footer>
    <span class="note">Rule-based risk flags — what to look at, not buy/sell calls.
      Figures computed deterministically; prose written over the numbers.</span>
    <span>Generated by quantbot</span>
  </footer>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Morning Brief — {escape(model.as_of.isoformat())}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">{body}{footer}
</div>
</body>
</html>"""
