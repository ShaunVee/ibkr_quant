# quantbot — slim Python image. No IB Gateway needed (Flex Web Service is token-based).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# config.yaml is baked in; .env and the data volume are mounted at runtime.
COPY config.yaml ./

# Default: stay up and run the service — the daily scheduler (07:00 Asia/Singapore,
# Tue-Sat by default) plus a Telegram command listener so you can trigger a run any
# time with /report. Deploy is just `docker compose up -d --build`. To run the pipeline
# once by hand instead, override the command:
#   docker compose run --rm quantbot python -m quantbot.pipeline --dry-run
CMD ["python", "-m", "quantbot.service"]
