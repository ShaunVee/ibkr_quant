# quantbot — Python image with headless Chromium for rendering the brief to a PNG.
# No IB Gateway needed (Flex Web Service is token-based).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
# Install with the image extra, then fetch Chromium + its system libraries. If you
# don't need image delivery, drop "[image]" and the playwright line to slim the build
# (delivery falls back to the text brief automatically).
RUN pip install --upgrade pip \
    && pip install ".[image]" \
    && python -m playwright install --with-deps chromium

# config.yaml is baked in; .env and the data volume are mounted at runtime.
COPY config.yaml ./

# Default: run the full pipeline once and exit (the scheduler invokes this).
ENTRYPOINT ["python", "-m", "quantbot.pipeline"]
CMD ["--stage", "all"]
