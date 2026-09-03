# quantbot — slim Python image. No IB Gateway needed (Flex Web Service is token-based).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching — from the checked-in lockfile so builds are
# reproducible (yfinance et al. are moving targets). Bump requirements.lock
# deliberately (scripts/make_lock.py), not on every rebuild. The app itself is
# installed --no-deps so pip never re-resolves what the lock already pinned.
COPY requirements.lock ./
RUN pip install --upgrade pip && pip install -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

# config.yaml is baked in; .env and the data volume are mounted at runtime.
COPY config.yaml ./

# Run as a non-root user. UID/GID 1000 must own the mounted data volume; on a fresh
# host (or if the old root-owned volume exists) fix ownership once with:
#   docker compose exec -u root quantbot chown -R quantbot:quantbot /app/data
#   (or: docker run --rm -v deploy_quantbot-data:/data alpine chown -R 1000:1000 /data)
RUN groupadd -g 1000 quantbot && useradd -m -u 1000 -g quantbot quantbot \
    && mkdir -p /app/data /app/data/reports \
    && chown -R quantbot:quantbot /app
USER quantbot

# Cheap liveness probe: the interpreter can import the app. Smarter than
# restart: unless-stopped alone for catching a corrupted install / broken image
# (not a data-freshness check — a scheduler that hasn't fired yet is healthy).
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import quantbot, quantbot.service" || exit 1

# Default: stay up and run the service — the daily scheduler (07:00 Asia/Singapore,
# Tue-Sat by default) plus a Telegram command listener so you can trigger a run any
# time with /report. Deploy is just `docker compose up -d --build`. To run the pipeline
# once by hand instead, override the command:
#   docker compose run --rm quantbot python -m quantbot.pipeline --dry-run
CMD ["python", "-m", "quantbot.service"]
