<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="quantbot" src="assets/logo.png" width="420">
  </picture>
</p>

Personal quant bot for an IBKR portfolio. Each morning it pulls your holdings, runs
fundamental + technical + risk analysis plus macro context (CPI, upcoming events), and
sends a brief to Telegram. Built to expand to other brokers later.

- **Broker access:** IBKR **Flex Web Service** — token-based, retail-friendly, no gateway
  to babysit, no daily 2FA. (See the plan for why this beats the live Web API for a
  retail account.)
- **Data:** free tiers first (yfinance, FRED, Finnhub) behind a swappable provider
  interface — upgrade to paid feeds without touching analysis code.
- **Report:** everything computed deterministically; Claude writes prose *over* the
  numbers (never invents them). Falls back to a template if the API is unavailable.
- **Delivery:** a styled, theme-aware, **self-contained HTML brief** sent to Telegram as
  a document — tap it to open full-quality in Telegram's in-app browser (no image
  scaling, no hosting). A short text caption carries the headline + flag summary.
  Set `report.delivery: text` in `config.yaml` for a plain-text message instead.
- **"Recommendations":** rule-based **risk flags** (concentration, high beta, RSI
  extremes, earnings-soon, drawdown) — what to look at, not buy/sell calls.

## Analyses

Each is a self-contained module in `analysis/` that degrades gracefully — a failed data
provider drops its section rather than the run. All numbers are computed here; the
narrative only summarizes them.

1. **Diversification structure** — *"your book is secretly one bet"*: effective number of
   bets, top-factor share of variance, and correlated clusters hiding on the positions screen.
2. **Risk contribution** — *"risk is not weight"*: each name's share of portfolio risk vs its
   weight, so an outsized risk driver is visible even at a modest weight.
3. **Move context** — *"is this even notable?"*: today's move z-scored against the book's own
   trailing volatility and attributed to the names that drove it.
4. **Event radar** — forward earnings + macro calendar (CPI/FOMC) weighted by your exposure,
   plus a rate-sensitivity read — the risk you're walking into blind.
5. **Benchmark-relative** — excess return over windows, regressed beta / annualized alpha / R²,
   up/down capture, and drift from your target weights (a rebalance cue).
6. **Scenario stress test** — the book's risk in dollars: modelled loss under named market/rate
   shocks, its worst historical day/week, and CVaR (expected shortfall).
7. **Driver attribution** — *why* each notable mover moved: the move split into what its theme
   (silver, bitcoin, comm-services, …; broad-market fallback) explains via beta vs a
   name-specific residual, classified systematic / mixed / idiosyncratic. For abnormal
   single-stock movers, recent headlines are attached as *possible, unconfirmed* catalysts.
   The theme map is tunable in `config.yaml` (`drivers.map`).
8. **Your money, in plain English** — *"cut the jargon"*: the same analytics re-expressed as
   money and framed against a decision. What the holdings made/lost today / past month / past
   3 months **in dollars**, the single best and worst day, **where you stand on each holding**
   (winners vs. underwater names since you bought them, from Flex cost basis), whether you're
   **ahead of or behind just buying the index** (in dollars, not alpha), how far **underwater**
   you are and the gain needed to break even, and a one-line *"what are you really betting on?"*.
   It leads the brief.

Plus a **history & trends** readout — how account value, beta, vol, concentration and flags
have drifted over the recorded snapshot window (IBKR is stateless; the daily snapshots aren't).

The brief leads with #8 (money, plain terms) and demotes the quant metrics beneath it. Pure
reference noise a layman never acts on (Sharpe, tracking error, up/down capture, R², raw
average-correlation) has been cut; the metrics that remain carry a one-line "what this means
for you" gloss.

### Next steps (backlog)

Layman-first ideas not yet built, roughly in value order:

- **Dividend "paycheck" view** — "these holdings pay you ≈ $X/year (~Y%)", from yfinance yields.
- **Sleep-at-night number** — vol/CVaR restated as "a normal bad day ≈ −$X; a rough one ≈ −$Y".
- **Breadth tally** — "6 of 9 holdings green this month, but 80% of the gain is one name".
- **Persistent-problem tracker** — a flag that has tripped N days running vs. one-day noise
  (the snapshot history already supports this).

## Status

**Live in production.** Deployed on the VPS as a long-lived container (see [Deploy](#deploy-vps))
and sending scheduled morning briefs — 07:00 Asia/Singapore, Tue–Sat — to Telegram, plus
on-demand `/report`. Real IBKR Flex ingest, market data, Claude narrative, and Telegram
HTML-document delivery are all confirmed working against live credentials.

> **Note for anyone (or any agent) inspecting a local checkout:** production runs on the VPS,
> not from this working copy. Local `data/portfolio.db` and `data/reports/` are dev leftovers
> and are **not** evidence of production state — do not infer that the bot "hasn't run" from
> them. Check the VPS container / its logs instead.

Full pipeline end-to-end (ingest → store → analyze → report → deliver) with a passing unit
suite (`pytest -q`). To run the pipeline locally against your own `.env`:

- `python -m quantbot.pipeline --stage ingest` — pull + print holdings (verifies Flex).
- `python -m quantbot.pipeline --dry-run` — full run, prints the report, no Telegram.
- `python -m quantbot.pipeline` — full run, sends the brief to Telegram.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # then fill in the values below
```

### Secrets (`.env`)

| Variable | Where to get it |
|---|---|
| `IBKR_FLEX_TOKEN` | Client Portal → Performance & Reports → Flex Queries → **Flex Web Service Configuration** → generate token |
| `IBKR_FLEX_QUERY_ID` | The Query ID of a Flex Query that includes **Open Positions** + **Account Information** |
| `FINNHUB_KEY` | https://finnhub.io (free) |
| `FRED_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html (free) |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | Message your bot once, then `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` and read `message.chat.id` |
| `ANTHROPIC_API_KEY` | Optional — enables the Claude narrative; without it, a deterministic template is used |

### Configure the IBKR Flex Query (one time)

1. Client Portal → **Performance & Reports → Flex Queries**.
2. Create an **Activity Flex Query** (or Custom). Include sections: **Open Positions**
   and **Account Information** (add Cash Report / NAV for cash + net-liq figures).
3. Save; note the **Query ID** → `IBKR_FLEX_QUERY_ID`.
4. **Flex Web Service Configuration** → enable → generate token → `IBKR_FLEX_TOKEN`.

## Run

```bash
python -m quantbot.pipeline --stage ingest     # pull + print holdings (Phase 1)
python -m quantbot.pipeline --dry-run          # full run, print report, no Telegram (later phases)
pytest -q                                       # tests
```

## Deploy (VPS)

One long-lived container that runs the **service** ([src/quantbot/service.py](src/quantbot/service.py)) —
no host cron or systemd. It does two things at once:

- **Scheduled:** fires the pipeline at **07:00 Asia/Singapore, Tue–Sat** (each brief caps the prior US session; Sat covers Fri's close, then the next run is Tue).
- **On demand:** listens on Telegram — send **`/report`** any time to run the brief now
  (also `/status` for the next run + state, `/help`). An on-demand `/report` streams live
  stage progress back to the chat (“📥 Pulling… → 📊 Crunching… → 📤 Delivering… → ✅”), so
  you can see it’s working, not hung. Commands are accepted only from your configured
  `TELEGRAM_CHAT_ID`. Runs are serialized, so a `/report` fired during a run is told to
  wait rather than colliding on the SQLite writer.

Deploy is one command:

```bash
# On the VPS, from the repo root, with .env in place:
docker compose -f deploy/docker-compose.yml up -d --build     # deploy / update
docker compose -f deploy/docker-compose.yml logs -f           # watch it (look for "scheduler armed" / "command listener ready")
```

Then just message the bot **`/report`** to verify a real end-to-end run — no need to wait
for the morning. (Or a one-off from the shell, overriding the service command:
`docker compose -f deploy/docker-compose.yml run --rm quantbot python -m quantbot.pipeline --dry-run`.)

Retune the schedule without rebuilding via env vars (`QUANTBOT_SCHEDULE_DAYS`,
`QUANTBOT_SCHEDULE_HOUR`, `QUANTBOT_SCHEDULE_MINUTE`, `TZ`) — see the scheduler module.
`restart: unless-stopped` survives crashes and reboots; a run missed while the host was
down still fires on wake (misfire grace window). On any hard failure the pipeline sends
a Telegram failure ping, so a silent morning is noticed. Do **not** run more than one
instance — two pollers on the same bot token steal each other's commands.

## Layout

See `src/quantbot/` — `ingestion/` (brokers, marketdata), `storage/`, `analysis/`,
`report/`, `delivery/`, and `pipeline.py` (orchestrator). Deployment assets in
`deploy/`. The full design and rationale live in the approved plan file.
