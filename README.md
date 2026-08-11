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

## Status

Full pipeline implemented end-to-end: ingest → store → analyze → report → deliver,
with 34 passing unit tests. Fill in `.env`, then:

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

The pipeline is a run-to-completion job, so there's no long-running server — schedule
it. Recommended: Docker + a systemd timer (files in [deploy/](deploy/)).

```bash
# On the VPS, from the repo root (e.g. /opt/ibkr_quant):
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm quantbot --dry-run   # verify

# Schedule the weekday-morning run:
sudo cp deploy/quantbot.service deploy/quantbot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quantbot.timer
sudo systemctl start quantbot.service   # test one run now
```

Edit `WorkingDirectory` in `quantbot.service` and `OnCalendar` in `quantbot.timer`
to match your VPS path and timezone. On any hard failure the pipeline sends a Telegram
failure ping, so a silent morning is noticed.

## Layout

See `src/quantbot/` — `ingestion/` (brokers, marketdata), `storage/`, `analysis/`,
`report/`, `delivery/`, and `pipeline.py` (orchestrator). Deployment assets in
`deploy/`. The full design and rationale live in the approved plan file.
