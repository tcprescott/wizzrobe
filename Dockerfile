# Dockerfile for Wizzrobe (FastAPI + NiceGUI)
FROM python:3.12-slim

# Set workdir
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

# Copy poetry files and install dependencies
COPY pyproject.toml poetry.lock ./
# --only main keeps test/dev tooling out of the runtime image (smaller image,
# smaller attack surface).
RUN pip install --no-cache-dir poetry && poetry config virtualenvs.create false && poetry install --only main --no-interaction --no-ansi

# Copy app code
COPY . .

# The commit this image was built from; Sentry reports it as the release so a
# regression points at the deploy that introduced it. Set by publish.yml.
# GIT_COMMIT_DATE dates the version (YYYY.MM.DD) and APP_VERSION, set on a tag
# build, overrides it; both surface on /about, /api/version and MCP.
ARG GIT_SHA=""
ARG GIT_COMMIT_DATE=""
ARG APP_VERSION=""
ENV GIT_SHA=${GIT_SHA} GIT_COMMIT_DATE=${GIT_COMMIT_DATE} APP_VERSION=${APP_VERSION}

# Run as an unprivileged user rather than root
RUN useradd --create-home --uid 10001 appuser \
    && chmod +x start.sh \
    && chown -R appuser:appuser /app
USER appuser

# Expose port (default: 8000)
EXPOSE 8000

# Liveness probe hitting the unauthenticated /api/health DB round-trip.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health').read()" || exit 1

# Entrypoint
CMD ["./start.sh", "prod"]
