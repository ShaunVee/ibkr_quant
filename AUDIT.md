# Codebase Audit — 2026-09-03

Overall: this is a well-built codebase. Clean layering (ingestion → storage → analysis → report → delivery), graceful degradation everywhere, honest docstrings explaining *why*, and 155 passing tests. The findings below are mostly polish-level; nothing is on fire.

---

## 1. Correctness issues (fix first)

### 1.1 `_catalysts` uses the wrong clock (minor bug)
`pipeline.py::_catalysts` calls `date.today()` — host/UTC time — while the rest of the pipeline deliberately uses `_local_today(config)`. `days_ago` on catalyst headlines can be off by one around midnight SGT.
**Fix:** pass the run date in (or call `_local_today(config)`).

### 1.2 `--dry-run` is not side-effect free
`stage_analyze` persists snapshots, flags, and prices *before* the dry-run check in `_run_full`. A dry run mutates flag streaks / change memory / the equity curve with that day's data. Same-day re-runs are idempotent (upserts), so damage is limited, but a dry run still "spends" the day's memory layer.
**Fix:** either document it, or add a `--no-store` flag threading a read-only/ephemeral store through `stage_analyze`.

### 1.3 `reports` table grows duplicates
`Store.save_report` is a plain INSERT with no UNIQUE constraint; every same-day re-run adds another row per format. The schema comment says format is `'markdown' | 'text'` but the code stores `'html'` — stale comment too.
**Fix:** `UNIQUE(snapshot_date, format)` + upsert; update the comment.

### 1.4 SQLite robustness
`Store._conn` sets no `busy_timeout` and no WAL mode. The `/status` listener opens its own connection while the pipeline subprocess writes. The run lock makes collisions unlikely, but `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` are one-line insurance. Also add an explicit `conn.rollback()` on exception for clarity.

### 1.5 Dead code in `scheduler.run_pipeline`
The `except subprocess.TimeoutExpired` branch is unreachable — `_stream_pipeline` handles the watchdog kill internally and never raises it. Remove it (or move watchdog ownership up).

---

## 2. Resilience opportunities

### 2.1 The price cache is write-only (biggest untapped win)
`Store.get_prices` is never called. Every run re-fetches `history_days=400` calendar days for every holding, benchmark, rate proxy, and driver ticker — even though yesterday's full series is sitting in SQLite. Two easy wins:
- **Fallback:** when the price provider returns empty (yfinance is flaky — you already know), fall back to the cached series (mark it stale in the log/report) instead of silently dropping the section.
- **Delta fetch:** read the latest cached date per symbol and request only the gap from the provider. Cuts API load ~90% on normal days and reduces yfinance rate-limit exposure.

### 2.2 No timeout on the Claude call
`narrative._call_claude` creates `anthropic.Anthropic(api_key=...)` with no explicit timeout. A hung API call eats into the 900s run budget. Pass `timeout=60` (and `max_retries=2`) to the client.

### 2.3 Docker hardening
The container runs as root. Add a non-root USER. A HEALTHCHECK (e.g. "did snapshot_summary update in the last N days" is overkill — even just `python -c "import quantbot"` or a heartbeat file) would make restart-on-failure smarter than `restart: unless-stopped` alone.

### 2.4 Reproducible builds
No lockfile: every `docker compose up --build` re-resolves dependencies (yfinance is a moving target). Pin with `uv`/`pip-compile` (a checked-in `requirements.lock`) and rebuild deliberately.

---

## 3. Code structure / maintainability

- **`stage_analyze` is a ~150-line god function.** The `_benchmark`/`_drivers`/`_events`/`_stress` helpers are the right instinct — go further: bundle the shared inputs (portfolio, market, store, config, price_frames, fundamentals) into a small `AnalysisContext` dataclass and split the pipeline into `compute_risk_layer`, `compute_context_layer`, etc. This is the one place future analyses will keep adding spaghetti.
- **`service.py` imports the private `_DEFAULT_DB`** from `pipeline.py`. Promote it to a public constant (or better, a config key `report.db_path`).
- **yfinance calls are sequential and re-instantiate `Ticker`** per symbol (and `fundamentals` + `next_earnings` can each build one). A `ThreadPoolExecutor` over symbols, or `yf.download([...], group_by="ticker")` for the price batch, would meaningfully shorten the run.
- **Typing/lint in CI:** ruff is a dev dependency but nothing enforces it, and there's no `.github/`. A 20-line GitHub Actions workflow (`pytest -q` + `ruff check`) protects a live-production bot from regressions. Consider `mypy` on the dataclass-heavy modules too.
- **Observability:** add one log line per run with per-stage durations. When a morning brief is late, you'll want to know whether it was Flex, yfinance, or Claude without re-running.

---

## 4. Daily brief summary (narrative + caption) — suggestions

The prompt in `narrative.py` is genuinely good (money-first, change-first, unconfirmed-catalyst guardrails). Refinements:

1. **Resolve the length contradiction.** The prompt asks for "a tight 2-4 sentence summary" but then mandates ~7 content areas (money lead, changes, why/attribution, concentration, benchmark, events, stress). The model resolves this tension unpredictably — some days 2 sentences, some days a wall. Either raise the stated budget honestly (e.g. "5–8 sentences, ≈120 words max") or add an explicit priority order: *money lead + what changed + why are mandatory; concentration/events/stress only when notable (new flag, unusual σ, ≥30% of book affected).*
2. **Add a novelty rule.** Persistent flags (streak data is already in the "What Changed" section as "day N") get re-narrated fresh every morning. Add one line to the system prompt: *"If nothing changed versus the previous run (same flags, normal-range move, no new catalysts), say so in one clause and keep the whole brief short — do not re-explain a standing situation."* This is the difference between a brief you read and one you skim past by week three.
3. **Make the caption deterministic where it matters.** `format_caption`'s narrative teaser depends on Claude leading with money (usually it does). Prepend two deterministic lines — today's move + σ tag and the vs-index dollar figure from `model.money` — before the narrative excerpt, so the phone-notification preview is always informative even on a fallback-template day.
4. **Cache the narrative per day.** A same-day `/report` re-runs pays for a fresh Claude call on identical numbers. Store the narrative in the `reports` table (it's nearly there already) and reuse it when `report_date` matches.
5. **Feed yesterday's brief for continuity (optional).** The DB already holds everything; adding the prior day's summary to the prompt enables genuinely useful phrasing ("the CONCENTRATION flag we've been watching cleared today"). Cheap token-wise, big coherence win.
6. **Fallback narrative polish.** It joins flag `message`s verbatim (they can be long sentences) — cap to code + first clause. It also never uses `best_day`/`worst_day`/stress money figures even when they exist; the "sleep-at-night number" from your own backlog is one `f"-${cvar_pnl:,.0f} in a rough day"` line away.
7. **Cost lever.** `effort: high` + `max_tokens: 4000` for a few sentences of summary is generous. `medium` effort likely produces indistinguishable output for this bounded task — worth an A/B.

Note: your backlog's "persistent-problem tracker" is effectively already shipped via flag streaks in "What Changed" — you can strike it or upgrade it to a dedicated "tripped N days running" flag.

---

## 5. Tests

Solid suite (155, fast, offline). Gaps worth closing when convenient:
- `Store.save_report` upsert behaviour (once 1.3 is fixed).
- `narrative._fallback` output bounds (it can currently produce a very long string).
- `telegram._post` retry/429/AmbiguousDeliveryError paths (verify existing coverage).
- `scheduler._stream_pipeline` marker forwarding (pure function given a fake proc).

---

## Priority order

| # | Item | Effort | Value | Status |
|---|------|--------|-------|--------|
| 1 | Price-cache fallback + delta fetch (2.1) | M | High | ✅ Done 2026-09-03 (`_fetch_prices` in pipeline.py) |
| 2 | Brief: novelty rule + length fix (4.1–4.2) | S | High | ✅ Done 2026-09-03 (narrative.py system prompt) |
| 3 | CI workflow (pytest + ruff) (3) | S | High | ✅ Done 2026-09-03 (.github/workflows/ci.yml) |
| 4 | Claude client timeout + narrative cache (2.2, 4.4) | S | Med | |
| 5 | reports upsert + comment fix (1.3) | S | Med | |
| 6 | Deterministic caption lines (4.3) | S | Med | |
| 7 | WAL/busy_timeout, non-root Docker, lockfile (1.4, 2.3–2.4) | S | Med | |
| 8 | `_catalysts` date fix (1.1), dry-run doc/flag (1.2), dead code (1.5) | S | Low | `_catalysts` fixed alongside #1 |
| 9 | Refactor `stage_analyze`, publicize `_DEFAULT_DB` (3) | M | Low | |