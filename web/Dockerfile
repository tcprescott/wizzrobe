# The new app (web/) as a static site: nginx serving the Vite build.
# Build from the repo root, since the typed API client is generated from the
# backend's routers:  docker build -f web/Dockerfile .
#
# VITE_API_BASE / VITE_AUTH_ORIGIN point the app at the backend that serves
# /api and /oauth (empty = same origin). They are baked in at build time.

FROM python:3.12-slim AS schema
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry install --only main --no-root --no-interaction --no-ansi
COPY . .
RUN mkdir -p /out && python scripts/export_openapi.py /out/openapi.json

FROM node:22-slim AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
COPY --from=schema /out/openapi.json ./openapi.json
ARG VITE_API_BASE=""
ARG VITE_AUTH_ORIGIN=""
ARG VITE_OAUTH_CLIENT_ID=""
ENV VITE_API_BASE=${VITE_API_BASE} VITE_AUTH_ORIGIN=${VITE_AUTH_ORIGIN} VITE_OAUTH_CLIENT_ID=${VITE_OAUTH_CLIENT_ID}
RUN npx openapi-typescript openapi.json -o src/api/schema.d.ts && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
