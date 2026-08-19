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
  border-left:3px solid var(--accent);border-radius:0 12px 12px 0;font-size:15px;line-height:1.55;
  display:flex;flex-direction:column;gap:9px;}
.lead>span:first-child{font-weight:600;color:var(--ink-1);}
.lead>span{color:var(--ink-2);}
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
.betting{margin-top:12px;padding:11px 14px;background:var(--surface-2);border:1px solid var(--border);
  border-radius:10px;font-size:14px;color:var(--ink-1);}
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
.chip.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 30%,var(--border));}
.macro-tbl{width:100%;border-collapse:collapse;font-size:14px;}
.macro-tbl th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);font-weight:600;padding:0 12px 8px;}
.macro-tbl th.r,.macro-tbl td.r{text-align:right;}
.macro-tbl td{padding:10px 12px;border-top:1px solid var(--border);font-family:var(--mono);}
.macro-tbl td.name{font-family:var(--sans);font-weight:600;}
.macro-tbl tr:first-child td{border-top:none;}
.macro-tbl .as-of{font-family:var(--sans);font-size:11px;color:var(--ink-3);font-weight:400;}
.today{background:var(--surface-1);border:1px solid var(--border);border-radius:14px;padding:16px 18px;box-shadow:var(--shadow);}
.today .move{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;}
.today .mv-ret{font-size:30px;font-weight:700;letter-spacing:-.02em;font-family:var(--mono);}
.today .mv-z{font-family:var(--mono);font-size:13px;color:var(--ink-2);}
.today .mv-z.unusual{color:var(--warn);font-weight:700;}
.today .drivers{margin-top:12px;display:flex;flex-wrap:wrap;gap:6px 8px;}
.whys{margin-top:14px;display:flex;flex-direction:column;gap:9px;}
.why{font-size:13.5px;line-height:1.45;color:var(--ink-1);}
.why .sym{font-family:var(--mono);font-weight:700;}
.why .kind{font-family:var(--mono);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
  padding:1px 7px;border-radius:5px;margin-left:6px;white-space:nowrap;}
.why .kind.systematic{color:var(--ink-2);background:var(--surface-2);}
.why .kind.mixed{color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent);}
.why .kind.idiosyncratic{color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent);}
.why .detail{color:var(--ink-2);font-family:var(--mono);font-size:12px;}
.why .cat{display:block;margin-top:3px;padding-left:14px;color:var(--ink-3);font-size:12px;font-style:italic;}
.chglist{display:flex;flex-direction:column;gap:8px;margin-top:14px;}
.chg{display:flex;align-items:flex-start;gap:10px;font-size:14px;}
.chg .lab{font-family:var(--mono);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
  padding:1px 7px;border-radius:5px;white-space:nowrap;flex:none;margin-top:2px;}
.chg.new .lab{color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,transparent);}
.chg.cleared .lab{color:var(--pos);background:color-mix(in srgb,var(--pos) 14%,transparent);}
.chg.ongoing .lab{color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent);}
.clusters{margin-top:12px;display:flex;flex-wrap:wrap;gap:6px 8px;}
.rc-tbl td.hi{color:var(--warn);font-weight:700;}
.ev-group{margin-top:14px;}
.ev-group .ev-lbl{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);font-weight:600;margin-bottom:7px;}
.ev-chips{display:flex;flex-wrap:wrap;gap:6px 8px;}
.ev{display:inline-flex;align-items:baseline;gap:7px;font-size:13px;background:var(--surface-1);
  border:1px solid var(--border);border-radius:8px;padding:5px 10px;}
.ev .sym{font-weight:700;letter-spacing:-.01em;}
.ev .when{font-family:var(--mono);font-size:11px;color:var(--ink-3);}
.ev .wt{font-family:var(--mono);font-size:11px;color:var(--ink-2);}
.ev.hi{border-color:color-mix(in srgb,var(--warn) 45%,var(--border));}
.ev.hi .when{color:var(--warn);font-weight:700;}
.ev .beta{font-family:var(--mono);font-size:11px;color:var(--accent);font-weight:600;}
/* inline glossary term: dotted underline, popover on hover (desktop) or tap/focus (mobile) */
.gl{position:relative;border-bottom:1px dotted currentColor;cursor:help;outline:none;-webkit-tap-highlight-color:transparent;}
.gl>.pop{position:absolute;left:50%;bottom:calc(100% + 9px);transform:translateX(-50%);
  width:max-content;max-width:230px;background:var(--ink-1);color:var(--ground);
  font-family:var(--sans);font-size:12px;font-weight:500;line-height:1.4;letter-spacing:normal;
  text-transform:none;text-align:left;padding:8px 11px;border-radius:8px;box-shadow:var(--shadow);
  opacity:0;visibility:hidden;transition:opacity .12s ease;z-index:30;pointer-events:none;}
.gl>.pop::after{content:"";position:absolute;top:100%;left:50%;transform:translateX(-50%);
  border:5px solid transparent;border-top-color:var(--ink-1);}
.gl:hover>.pop,.gl:focus>.pop,.gl:focus-within>.pop{opacity:1;visibility:visible;}
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


def _gl(text: str, tip: str) -> str:
    """An inline glossary term: shows `tip` on hover (desktop) or tap/focus (mobile)."""
    return (f'<span class="gl" tabindex="0">{escape(text)}'
            f'<span class="pop">{escape(tip)}</span></span>')


def _narrative(model: ReportModel) -> str:
    if not model.narrative:
        return ""
    from quantbot.report.formatter import split_sentences

    spans = "".join(f"<span>{escape(s)}</span>" for s in split_sentences(model.narrative))
    return f'\n  <div class="lead">{spans}</div>'


_KIND_PHRASE = {
    "systematic": "mostly the theme",
    "mixed": "theme + name",
    "idiosyncratic": "name-specific",
}


def _whys(model: ReportModel) -> str:
    """Driver-attribution rows for the Today card: why each notable name moved, plus any
    unconfirmed catalyst headlines. Rendered under the move/driver chips."""
    dv = model.drivers
    if dv is None or (not dv.attributions and not dv.catalysts):
        return ""

    intro = _gl("Why", "Each mover split into what its theme (a reference index/commodity) "
                       "explains — beta × the theme's move — versus a name-specific residual. "
                       "Headlines are possible, unconfirmed catalysts, not established causes.")
    rows: list[str] = []
    for a in dv.attributions:
        ret = f'<b class="num {"pos" if a.ret_pct >= 0 else "neg"}">{escape(signed_pct(a.ret_pct))}</b>'
        if not a.theme or a.explained_pct is None or a.driver_ret_pct is None:
            rows.append(f'<div class="why"><span class="sym">{escape(a.symbol)}</span> {ret} '
                        f'<span class="detail">— no theme reference</span></div>')
            continue
        beta = f'β{escape(fmt_num(a.beta, 2))}' if a.beta is not None else ""
        resid = ""
        if a.residual_pct is not None:
            resid = f' · residual {escape(signed_pct(a.residual_pct))}'
        kind = (f'<span class="kind {a.kind}">{escape(_KIND_PHRASE.get(a.kind, a.kind))}</span>'
                if a.kind in _KIND_PHRASE else "")
        detail = (f'<span class="detail">— {escape(a.theme)} '
                  f'{escape(signed_pct(a.driver_ret_pct))} ({beta}){resid}</span>')
        rows.append(f'<div class="why"><span class="sym">{escape(a.symbol)}</span> {ret} '
                    f'{detail}{kind}</div>')

    cats: list[str] = []
    for c in dv.catalysts:
        age = f"{c.days_ago}d ago" if c.days_ago is not None else "recent"
        src = f", {escape(c.source)}" if c.source else ""
        cats.append(f'<span class="cat">↳ {escape(c.symbol)}: “{escape(c.headline)}” '
                    f'({age}{src}) — possible catalyst, unconfirmed</span>')

    body = "".join(rows) + "".join(cats)
    return f'<div class="whys"><div class="why" style="color:var(--ink-3)">{intro}</div>{body}</div>'


def _money(model: ReportModel) -> str:
    """The plain-English 'Your Money' hero (analysis #8): dollars first, no jargon.
    Placed right under the narrative — the first real section the owner reads."""
    m = model.money
    if m is None:
        return ""
    cur = model.base_currency

    tiles = ""
    for w in m.windows:
        cls = _pl_class(w.pnl)
        sub = escape(signed_pct(w.pct))
        if w.bench_pct is not None and w.bench_label:
            sub += f" · {escape(w.bench_label)} {escape(signed_pct(w.bench_pct))}"
        tiles += (f'<div class="tile"><div class="k">{escape(w.label)}</div>'
                  f'<div class="v num{cls}">{escape(signed_money(w.pnl))}</div>'
                  f'<div class="n">{sub}</div></div>')
    tiles_block = f'<div class="tiles">{tiles}</div>' if tiles else ""

    # Per-holding standing — the broker-app "am I up or down on this one?" view.
    stand = ""
    if m.winners or m.losers:
        ranked = sorted(m.winners + m.losers, key=lambda h: -abs(h.pnl))[:10]
        chips = "".join(
            f'<span class="chip {"up" if h.pnl >= 0 else "down"}">'
            f'{escape(h.symbol)} {escape(signed_money(h.pnl))}</span>'
            for h in ranked
        )
        total = ""
        if m.total_unrealized is not None:
            total = (f' · <b class="num{_pl_class(m.total_unrealized)}">'
                     f'{escape(signed_money(m.total_unrealized))}</b> unrealized')
        stand = (f'<div class="ev-group"><div class="ev-lbl">Where you stand — '
                 f'{len(m.winners)} up · {len(m.losers)} underwater{total}</div>'
                 f'<div class="drivers">{chips}</div></div>')

    bits: list[str] = []
    if m.vs_index_pnl is not None and m.bench_symbol:
        cls = _pl_class(m.vs_index_pnl)
        verb = "ahead of" if m.vs_index_pnl >= 0 else "behind"
        label = escape((m.vs_index_label or "").lower())
        bits.append(
            f'<span>vs {escape(m.bench_symbol)} ({label}) '
            f'<b class="num{cls}">{escape(signed_money(m.vs_index_pnl))}</b> — {verb} '
            f'just buying the index</span>'
        )
    if m.recovery is not None:
        bits.append(
            f'<span>Down <b class="num neg">{escape(fmt_pct(m.recovery.drawdown_pct))}</b> '
            f'from your peak — needs <b class="num">'
            f'{escape(signed_pct(m.recovery.gain_needed_pct))}</b> to get back to even</span>'
        )
    if m.best_day is not None and m.worst_day is not None:
        bits.append(
            f'<span>Best day <b class="num pos">{escape(signed_money(m.best_day.pnl))}</b> · '
            f'Worst day <b class="num neg">{escape(signed_money(m.worst_day.pnl))}</b></span>'
        )
    context = f'<div class="context">{"".join(bits)}</div>' if bits else ""

    betting = ""
    if m.betting_on:
        betting = f'<div class="betting">{escape(m.betting_on)}</div>'

    title = _gl("Your Money", "Money your current holdings would have made or lost over each "
                              "window — the book held constant over history, not a realized "
                              "track record. Deposits and withdrawals are excluded.")
    return f"""
  <section>
    <div class="sec-head"><h2>{title}</h2><span class="rule"></span></div>
    {tiles_block}{stand}{context}{betting}
  </section>"""


def _today(model: ReportModel) -> str:
    mv = model.moves
    new = [c for c in model.flag_changes if c.status == "new"]
    cleared = [c for c in model.flag_changes if c.status == "cleared"]
    ongoing = [
        c for c in model.flag_changes
        if c.status == "persistent" and c.streak >= 2 and c.flag.severity in ("high", "warn")
    ]
    has_move = mv is not None and mv.port_ret_pct is not None
    if not has_move and not (new or cleared or ongoing):
        return ""

    inner = ""
    if has_move:
        ret_cls = "pos" if mv.port_ret_pct >= 0 else "neg"
        z_html = ""
        if mv.port_z is not None:
            unusual = " unusual" if mv.unusual else ""
            tag = "unusual" if mv.unusual else "normal range"
            sigma = _gl("σ", "How far today's move sits from this book's own typical day, "
                             "in standard deviations. Beyond 2σ is unusually large — up or down.")
            z_html = f'<span class="mv-z{unusual}">{abs(mv.port_z):.1f}{sigma} · {escape(tag)}</span>'
        drivers = ""
        if mv.top_contributors:
            chips = "".join(
                f'<span class="chip {"up" if m.contribution_pp >= 0 else "down"}">'
                f'{escape(m.symbol)} {m.contribution_pp:+.1f}pp</span>'
                for m in mv.top_contributors
            )
            drivers = f'<div class="drivers">{chips}</div>'
        inner += (
            f'<div class="move"><span class="mv-ret {ret_cls}">'
            f'{escape(signed_pct(mv.port_ret_pct))}</span>{z_html}</div>{drivers}'
        )
        inner += _whys(model)

    rows = []
    for c in new:
        rows.append(f'<div class="chg new"><span class="lab">new</span>'
                    f'<span>{escape(c.flag.message)}</span></div>')
    for c in cleared:
        rows.append(f'<div class="chg cleared"><span class="lab">cleared</span>'
                    f'<span>{escape(c.flag.message)}</span></div>')
    for c in sorted(ongoing, key=lambda c: -c.streak):
        rows.append(f'<div class="chg ongoing"><span class="lab">day {c.streak}</span>'
                    f'<span>{escape(c.flag.message)}</span></div>')
    if rows:
        inner += f'<div class="chglist">{"".join(rows)}</div>'

    return f"""
  <section>
    <div class="sec-head"><h2>Today</h2><span class="rule"></span></div>
    <div class="today">{inner}</div>
  </section>"""


def _trend_tile(md: object) -> str:
    """One metric-drift tile: current value big, prior value as a footnote."""
    k = md.key
    if k == "annualized_vol":
        return (f'<div class="tile"><div class="k">Ann. Vol</div>'
                f'<div class="v num">{escape(fmt_pct(md.end * 100))}</div>'
                f'<div class="n">was {escape(fmt_pct(md.start * 100))}</div></div>')
    if k == "portfolio_beta":
        return (f'<div class="tile"><div class="k">Beta</div>'
                f'<div class="v num">{escape(fmt_num(md.end, 2))}</div>'
                f'<div class="n">was {escape(fmt_num(md.start, 2))}</div></div>')
    if k == "herfindahl":
        eff_e = fmt_num(1 / md.end, 1) if md.end else "—"
        eff_s = fmt_num(1 / md.start, 1) if md.start else "—"
        return (f'<div class="tile"><div class="k">Eff. Positions</div>'
                f'<div class="v num">{escape(eff_e)}</div>'
                f'<div class="n">was {escape(eff_s)} · higher = less concentrated</div></div>')
    if k == "flag_count":
        return (f'<div class="tile"><div class="k">Flags</div>'
                f'<div class="v num">{int(md.end)}</div>'
                f'<div class="n">was {int(md.start)}</div></div>')
    return ""


def _trends(model: ReportModel) -> str:
    tr = model.trends
    if tr is None:
        return ""

    tiles = "".join(_trend_tile(md) for md in tr.metric_drifts)
    tiles_block = f'<div class="tiles">{tiles}</div>' if tiles else ""

    context_bits = []
    if tr.value_start is not None and tr.value_end is not None:
        chg = tr.value_end - tr.value_start
        cls = "pos" if chg >= 0 else "neg"
        val = _gl("Account value", "Net liquidation across the tracked window. Includes any "
                                   "deposits or withdrawals — not a pure investment return.")
        context_bits.append(
            f'<span>{val} <b class="num">{escape(abbr_money(tr.value_start))}</b> → '
            f'<b class="num">{escape(abbr_money(tr.value_end))}</b> '
            f'<b class="num {cls}">{escape(signed_money(chg))}</b></span>'
        )
    if tr.drawdown_from_peak is not None and tr.drawdown_from_peak < -0.001:
        context_bits.append(
            f'<span>Down <b class="num neg">{escape(fmt_pct(abs(tr.drawdown_from_peak) * 100))}</b> '
            f'from window peak</span>'
        )
    context = f'<div class="context">{"".join(context_bits)}</div>' if context_bits else ""

    shifts = ""
    if tr.weight_drifts:
        chips = "".join(
            f'<span class="chip {"up" if d.delta >= 0 else "down"}">'
            f'{escape(d.symbol)} {d.delta * 100:+.1f}pp</span>'
            for d in tr.weight_drifts
        )
        shifts = ('<div class="ev-group"><div class="ev-lbl">Weight shifts</div>'
                  f'<div class="drivers">{chips}</div></div>')

    if not (tiles_block or context or shifts):
        return ""
    return f"""
  <section>
    <div class="sec-head"><h2>Trends</h2><span class="count">{tr.sessions} sessions · {tr.span_days}d</span><span class="rule"></span></div>
    {tiles_block}{context}{shifts}
  </section>"""


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
    realized_dd = fmt_pct((r.realized_drawdown or 0) * 100) if r.realized_drawdown is not None else "—"
    realized_note = (
        "worst peak-to-trough of your account"
        if r.realized_drawdown is not None
        else "needs a few days of history"
    )
    sim_dd = fmt_pct((r.max_drawdown or 0) * 100) if r.max_drawdown is not None else "—"
    var = fmt_pct((r.var_pct or 0) * 100) if r.var_pct else "—"

    tiles = f"""
      <div class="tile{beta_crit}">{beta_badge}<div class="k">Beta</div><div class="v num">{escape(fmt_num(r.portfolio_beta))}</div><div class="n">1.0 = moves with market</div></div>
      <div class="tile"><div class="k">Ann. Vol</div><div class="v num">{escape(ann_vol)}</div><div class="n">yearly swing · lower = calmer</div></div>
      <div class="tile{dd_crit}">{dd_badge}<div class="k">Max Drawdown</div><div class="v num">{escape(realized_dd)}</div><div class="n">{escape(realized_note)}</div></div>
      <div class="tile"><div class="k">Simulated DD</div><div class="v num">{escape(sim_dd)}</div><div class="n">if you'd held today's weights · hypothetical</div></div>
      <div class="tile"><div class="k">1-Day VaR</div><div class="v num">{escape(var)}</div><div class="n">a typical bad day (95%)</div></div>
      <div class="tile"><div class="k">Eff. Positions</div><div class="v num">{escape(fmt_num(r.effective_positions, 1))}</div><div class="n">of {len(model.positions)} held · higher = less concentrated</div></div>"""

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


def _stress_hist_bit(h: object, cur: str) -> str:
    pnl = f" ({escape(signed_money(h.pnl))})" if h.pnl is not None else ""
    day = f' <span class="as-of">{escape(h.day.isoformat())}</span>' if h.day is not None else ""
    return (f'<span>{escape(h.label)} <b class="num neg">{escape(signed_pct(h.ret_pct))}</b>'
            f'{pnl}{day}</span>')


def _stress(model: ReportModel) -> str:
    st = model.stress
    if st is None:
        return ""
    cur = model.base_currency

    rows = ""
    for s in st.scenarios:
        cls = "neg" if s.port_ret_pct < 0 else "pos"
        pnl = signed_money(s.pnl) if s.pnl is not None else "—"
        rows += (f'<tr><td class="name">{escape(s.label)}</td>'
                 f'<td class="r {cls}">{escape(signed_pct(s.port_ret_pct))}</td>'
                 f'<td class="r {cls}">{escape(pnl)}</td></tr>')
    table = ""
    if rows:
        scen_h = _gl("Scenario", "A hypothetical shock run through this book's own beta — "
                                 "the modelled move, not a forecast. Rate rows use the "
                                 "book's beta to the bond-ETF proxy.")
        table = ('<table class="macro-tbl">'
                 f'<thead><tr><th>{scen_h}</th><th class="r">Portfolio</th>'
                 f'<th class="r">P/L</th></tr></thead>'
                 f'<tbody>{rows}</tbody></table>')

    bits = []
    if st.worst_day is not None:
        bits.append(_stress_hist_bit(st.worst_day, cur))
    if st.worst_week is not None:
        bits.append(_stress_hist_bit(st.worst_week, cur))
    if st.cvar_pct is not None:
        cvar_gl = _gl("CVaR", "Expected shortfall: across the worst ~5% of days, the "
                              "average loss — how bad a bad day gets, beyond the VaR line.")
        pnl = f" ({escape(signed_money(st.cvar_pnl))})" if st.cvar_pnl is not None else ""
        bits.append(f'<span>{cvar_gl} 95% '
                    f'<b class="num neg">-{escape(fmt_pct(st.cvar_pct * 100))}</b>{pnl}</span>')
    context = f'<div class="context">{"".join(bits)}</div>' if bits else ""

    if not (table or context):
        return ""
    return f"""
  <section>
    <div class="sec-head"><h2>Stress Test</h2><span class="rule"></span></div>
    {table}{context}
  </section>"""


def _structure(model: ReportModel) -> str:
    d = model.diversification
    rc = model.contribution
    if d is None and rc is None:
        return ""

    blocks = ""
    if d is not None:
        tiles = ""
        if d.effective_bets is not None:
            tiles += (f'<div class="tile"><div class="k">Effective Bets</div>'
                      f'<div class="v num">{escape(fmt_num(d.effective_bets, 1))}</div>'
                      f'<div class="n">of {d.coverage} names · higher = more independent</div></div>')
        if d.top_factor_share is not None:
            hi = " flag-crit" if d.top_factor_share >= 0.6 else ""
            tiles += (f'<div class="tile{hi}"><div class="k">Top Factor</div>'
                      f'<div class="v num">{escape(fmt_pct(d.top_factor_share * 100, 0))}</div>'
                      f'<div class="n">one theme\'s share · lower better</div></div>')
        clusters = ""
        if d.clusters:
            items = "".join(
                f'<span class="chip">{escape(", ".join(c.symbols))} · '
                f'corr {escape(fmt_num(c.avg_corr, 2))} · {escape(fmt_pct(c.weight * 100, 0))}</span>'
                for c in d.clusters
            )
            clusters = f'<div class="clusters">{items}</div>'
        blocks += f'<div class="tiles">{tiles}</div>{clusters}'

    if rc is not None and rc.contributions:
        rows = ""
        for c in rc.contributions:
            ratio = c.ratio
            cls = "r hi" if (ratio is not None and ratio >= 1.2) else "r"
            rows += (
                f'<tr><td class="name">{escape(c.symbol)}</td>'
                f'<td class="r">{escape(fmt_pct(c.weight * 100, 1))}</td>'
                f'<td class="{cls}">{escape(fmt_pct(c.risk_pct * 100, 1))}</td>'
                f'<td class="r">{escape(fmt_num(ratio, 1)) if ratio is not None else "—"}</td></tr>'
            )
        risk_h = _gl("Risk", "Share of the portfolio's total volatility this position "
                             "drives — its real risk footprint, not its dollar size.")
        ratio_h = _gl("×", "Risk share ÷ weight. 1 = pulls its weight; above 1 = drives "
                           "more risk than its size; below 1 = less.")
        blocks += (
            '<table class="macro-tbl rc-tbl" style="margin-top:14px;">'
            f'<thead><tr><th>Position</th><th class="r">Weight</th>'
            f'<th class="r">{risk_h}</th><th class="r">{ratio_h}</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )

    return f"""
  <section>
    <div class="sec-head"><h2>Structure</h2><span class="rule"></span></div>
    {blocks}
  </section>"""


def _benchmark(model: ReportModel) -> str:
    bm = model.benchmark
    if bm is None:
        return ""

    # Excess return / tracking error / capture / R² are folded into "Your Money" as dollars.
    # Here we keep only the one read that isn't about P/L: how much of the ride is just market.
    tiles = ""
    if bm.beta is not None:
        tiles += (f'<div class="tile"><div class="k">Beta vs {escape(bm.symbol)}</div>'
                  f'<div class="v num">{escape(fmt_num(bm.beta, 2))}</div>'
                  f'<div class="n">1.0 = you basically are the index</div></div>')
    if bm.alpha_annual_pct is not None:
        cls = "pos" if bm.alpha_annual_pct >= 0 else "neg"
        tiles += (f'<div class="tile"><div class="k">Alpha</div>'
                  f'<div class="v num {cls}">{escape(signed_pct(bm.alpha_annual_pct))}</div>'
                  f'<div class="n">return beyond the market · higher better</div></div>')

    drivers = ""

    drift = ""
    if bm.drifts:
        rows = "".join(
            f'<div class="chg ongoing"><span class="lab">drift</span>'
            f'<span>{escape(d.symbol)} {escape(fmt_pct(d.weight * 100, 0))} vs '
            f'{escape(fmt_pct(d.target * 100, 0))} target '
            f'({escape(signed_pct(d.drift * 100, 0))})</span></div>'
            for d in bm.drifts
        )
        drift = f'<div class="chglist">{rows}</div>'

    tiles_block = f'<div class="tiles">{tiles}</div>' if tiles else ""
    if not (tiles or drift):
        return ""
    return f"""
  <section>
    <div class="sec-head"><h2>vs {escape(bm.symbol)}</h2><span class="rule"></span></div>
    {tiles_block}{drivers}{drift}
  </section>"""


def _events(model: ReportModel) -> str:
    ev = model.events
    if ev is None:
        return ""

    tiles = ""
    if ev.earnings and ev.earnings_weight > 0:
        hi = " flag-crit" if ev.earnings_weight >= 0.30 else ""
        tiles += (f'<div class="tile{hi}"><div class="k">Earnings Load</div>'
                  f'<div class="v num">{escape(fmt_pct(ev.earnings_weight * 100, 0))}</div>'
                  f'<div class="n">of book reports in {ev.horizon_days}d</div></div>')
    if ev.rate_sensitive:
        tiles += (f'<div class="tile"><div class="k">Rate Exposure</div>'
                  f'<div class="v num">{escape(fmt_pct(ev.rate_sensitive_weight * 100, 0))}</div>'
                  f'<div class="n">moves with {escape(ev.rate_proxy or "rates")}</div></div>')
    tiles_block = f'<div class="tiles">{tiles}</div>' if tiles else ""

    groups = ""
    if ev.earnings:
        chips = "".join(
            f'<span class="ev{" hi" if e.days_away <= 2 else ""}"><span class="sym">{escape(e.symbol)}</span>'
            f'<span class="when">{escape(e.day.strftime("%a"))} · {e.days_away}d</span>'
            f'<span class="wt">{escape(fmt_pct(e.weight * 100, 0))}</span></span>'
            for e in ev.earnings
        )
        groups += f'<div class="ev-group"><div class="ev-lbl">Earnings ahead</div><div class="ev-chips">{chips}</div></div>'
    if ev.macro:
        chips = "".join(
            f'<span class="ev{" hi" if (m.impact or "").lower() == "high" else ""}">'
            f'<span class="sym">{escape(m.event)}</span>'
            f'<span class="when">{escape(m.day.strftime("%a"))} · {m.days_away}d</span>'
            + (f'<span class="wt">{escape(m.impact)}</span>' if m.impact else "")
            + '</span>'
            for m in ev.macro[:6]
        )
        groups += f'<div class="ev-group"><div class="ev-lbl">Macro calendar</div><div class="ev-chips">{chips}</div></div>'
    if ev.rate_sensitive:
        proxy = ev.rate_proxy or "rates"
        beta_gl = _gl("β", f"Beta to {proxy}: how hard this name moves when rates move "
                           f"(measured via the {proxy} bond ETF). Bigger magnitude = more "
                           "rate-sensitive; sign shows direction.")
        chips = "".join(
            f'<span class="ev"><span class="sym">{escape(s.symbol)}</span>'
            f'<span class="beta">{beta_gl}{s.beta:+.2f}</span>'
            f'<span class="wt">{escape(fmt_pct(s.weight * 100, 0))}</span></span>'
            for s in ev.rate_sensitive[:6]
        )
        groups += (f'<div class="ev-group"><div class="ev-lbl">Rate-sensitive names '
                   f'(β vs {escape(ev.rate_proxy or "rates")})</div>'
                   f'<div class="ev-chips">{chips}</div></div>')

    return f"""
  <section>
    <div class="sec-head"><h2>Event Radar</h2><span class="count">next {ev.horizon_days}d</span><span class="rule"></span></div>
    {tiles_block}{groups}
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


# Technical-signal tone -> chip colour class (see analysis.technical.derive_signals).
_SIGNAL_TONE_CLASS = {"bull": "up", "bear": "down", "warn": "warn", "neutral": ""}


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
            rsi_gl = _gl("RSI", "Relative Strength Index (0–100): momentum gauge. "
                                "Above 70 = overbought, below 30 = oversold.")
            chips.append(f'<span class="chip">{rsi_gl} {escape(fmt_num(p.rsi14, 0))}</span>')
        if p.ret_1m is not None:
            cls = "up" if p.ret_1m >= 0 else "down"
            chips.append(f'<span class="chip {cls}">1m {escape(signed_pct(p.ret_1m))}</span>')
        for sig in p.signals:
            cls = _SIGNAL_TONE_CLASS.get(sig.tone, "")
            chips.append(f'<span class="chip {cls}">{escape(sig.label)}</span>')
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
            _money(model),
            _today(model),
            _trends(model),
            _risk(model),
            _stress(model),
            _structure(model),
            _benchmark(model),
            _events(model),
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
