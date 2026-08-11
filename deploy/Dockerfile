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

# Default: run the full pipeline once and exit (the scheduler invokes this).
ENTRYPOINT ["python", "-m", "quantbot.pipeline"]
CMD ["--stage", "all"]
