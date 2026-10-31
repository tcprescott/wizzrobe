# Deployment & Operations Guide

_The operations reference for deploying and running Wizzrobe: container topology, the authoritative environment-variable table, startup behavior, and runbook. Part of the [documentation index](README.md)._

Wizzrobe ships as a single application container plus PostgreSQL and a Redis session store, orchestrated by [`docker-compose.yml`](../docker-compose.yml). This page documents the current setup and operations.

## Topology

[`docker-compose.yml`](../docker-compose.yml) defines three services and two named volumes:

```
                        host :8000
                            │
┌───────────────────────────▼───────────────────────────────┐
│ wizzrobe    build: . → image wizzrobe:latest              │
│             ./start.sh prod → uvicorn main:app --workers 1│
│             FastAPI + NiceGUI + Discord bot + workers     │
└──────────────┬─────────────────────────────┬──────────────┘
               │ DB_HOST=postgres:5432       │ NICEGUI_REDIS_URL=redis://redis:6379/0
               │ (compose network only)      │ (compose network only)
┌──────────────▼──────────────┐ ┌────────────▼──────────────┐
│ postgres  postgres:16-alpine│ │ redis  redis:7-alpine     │
│ volume postgres_data        │ │ volume redis_data → /data │
└─────────────────────────────┘ └───────────────────────────┘
```

### `postgres` service

| Setting | Value |
|---|---|
| Image | `postgres:16-alpine` |
| Database | `POSTGRES_DB=${DB_NAME:-wizzrobe}` |
| Credentials | `POSTGRES_USER`/`POSTGRES_PASSWORD` from `DB_USERNAME`/`DB_PASSWORD`; compose fails fast (`:?` interpolation) if either is missing from `.env` |
| Ports | none published — reachable only as `postgres:5432` on the compose network |
| Volume | `postgres_data`, mounted at `/var/lib/postgresql/data` |
| Healthcheck | `pg_isready -U ${DB_USERNAME} -d ${DB_NAME:-wizzrobe}` — interval 5s, timeout 5s, retries 10 |
| Restart | `unless-stopped` |

### `redis` service

| Setting | Value |
|---|---|
| Image | `redis:7-alpine` |
| Purpose | Holds `app.storage.user`, so a redeploy does not sign everyone out |
| Command | `redis-server --save 60 1 --appendonly no` (periodic RDB snapshot, no AOF) |
| Ports | none published |
| Volume | `redis_data`, mounted at `/data` |
| Healthcheck | `redis-cli ping` — interval 5s, timeout 5s, retries 10 |
| Restart | `unless-stopped` |

### `wizzrobe` service

| Setting | Value |
|---|---|
| Image | built from [`Dockerfile`](../Dockerfile) (`build: .`), tagged `wizzrobe:latest` |
| Environment | everything in `.env` via `env_file`, plus `DB_HOST=postgres`, `DB_PORT=5432` and `NICEGUI_REDIS_URL=redis://redis:6379/0` injected by compose |
| Ports | `8000:8000` |
| Startup order | `depends_on` `postgres` and `redis`, both `condition: service_healthy` — the app only starts after both healthchecks pass |
| Healthcheck | `GET http://localhost:8000/api/health` via `python -c urllib…` — interval 30s, timeout 5s, retries 3, start period 30s |
| Restart | `unless-stopped` |

## Container image

### Dockerfile

[`Dockerfile`](../Dockerfile) is a single stage on `python:3.12-slim`:

1. `apt-get install build-essential libpq-dev` — compiler toolchain and PostgreSQL client headers for native wheels.
2. `COPY pyproject.toml poetry.lock` then `pip install poetry`, `poetry config virtualenvs.create false`, `poetry install --only main --no-interaction --no-ansi`. **No virtualenv** (both `poetry run …` and plain `python` work in the container) and `--only main` (no pytest/ipython in the runtime image). Lockfiles are copied first so the dependency layer stays cached until `poetry.lock` changes.
3. `COPY . .` — application code last, so code changes do not invalidate the dependency layer.
4. Creates an unprivileged `appuser` (uid 10001), owns `/app` to it and runs as that user.
5. `EXPOSE 8000`; a `HEALTHCHECK` polling `/api/health` (same cadence as the compose one); `CMD ["./start.sh", "prod"]`.

### GHCR publishing

[`.github/workflows/publish.yml`](../.github/workflows/publish.yml) builds and pushes the image to GitHub Container Registry:

- **Triggers:** paused. It runs only by hand (`workflow_dispatch` from the Actions tab), because production deploys are a `git pull` + restart and never use the image. To resume, restore the `push` trigger (`main` and `v*` tags) and remove `push` from `test.yml`.
- **Gate:** the `build-and-push` job `needs` a `test` job that calls [`test.yml`](../.github/workflows/test.yml) via `workflow_call`, so an image is only built once the full CI suite passes ([development.md](development.md#continuous-integration)).
- **Image:** `ghcr.io/<owner>/<repo>` — registry `ghcr.io`, name taken from `github.repository` (currently `ghcr.io/tcprescott/wizzrobe`; images published before the repository was renamed from `sglman` live under `ghcr.io/tcprescott/sglman`).
- **Tag scheme** (`docker/metadata-action`):

| Event | Resulting tags |
|---|---|
| Push to `main` | `main`, `latest` |
| Push tag `v1.2.3` | `1.2.3`, `1.2`, `1` |

- Authenticates with the workflow `GITHUB_TOKEN` (`packages: write`); uses the GitHub Actions build cache (`cache-from`/`cache-to: type=gha`). No `platforms` are specified, so the image is built for the runner's platform (linux/amd64).

The compose file builds locally. To deploy a published image instead, replace `build: .` / `image: wizzrobe:latest` in the `wizzrobe` service with `image: ghcr.io/tcprescott/wizzrobe:<tag>`.

## Environment variables

Template: [`.env.example`](../.env.example). Three loading paths:

- **Docker:** compose passes `.env` to the app via `env_file` and uses it for its own `${…}` interpolation.
- **`./start.sh`:** sources `.env` with `set -a` ([`start.sh`](../start.sh)), so values containing spaces are preserved.
- **Aerich CLI / scripts:** [`migrations/tortoise_config.py`](../migrations/tortoise_config.py) calls `load_dotenv()`, so `poetry run aerich …` picks up `.env` without exporting.

Every boolean variable below uses one grammar (`env_flag` in `application/utils/environment.py`): true iff the stripped, lowercased value is `1`, `true`, `yes` or `on`. Every variable the application reads:

| Variable | Required | Default | Consumed by | Notes |
|---|---|---|---|---|
| `DB_HOST` | yes | — | `migrations/tortoise_config.py` | Set to `postgres` by compose. Missing → `ValueError` at import. |
| `DB_PORT` | yes | — | `migrations/tortoise_config.py` | Set to `5432` by compose. Same import-time check. |
| `DB_NAME` | yes | — | `migrations/tortoise_config.py`, `docker-compose.yml` | Compose defaults server-side `POSTGRES_DB` to `wizzrobe`; the app still needs `DB_NAME`. |
| `DB_USERNAME` | production: yes | `''` | `migrations/tortoise_config.py`, `application/utils/environment.py`, `docker-compose.yml` | Compose refuses to start when unset (`:?`); blank aborts a production boot. |
| `DB_PASSWORD` | production: yes | `''` | same as `DB_USERNAME` | Same enforcement. URL-encoded into the DSN, so special characters are safe. |
| `ENVIRONMENT` | no | `development` | `application/utils/environment.py`, `frontend.py`, `migrations/tortoise_config.py` | `production` (stripped, lowercased) enables the strict checks below. `./start.sh prod` force-exports `production` and `./start.sh mock` forces `development`, overriding `.env`. No-cache static headers only when exactly `development`. |
| `STORAGE_SECRET` | **yes, always** | — | `application/utils/environment.py`, `frontend.py`, `pages/auth.py`, `application/services/oauth_handoff_service.py` | Signs the NiceGUI session holding auth state. Blank aborts startup in any environment; production also requires ≥32 characters. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `NICEGUI_REDIS_URL` | production: recommended | unset | `nicegui.storage`, `application/utils/environment.py`, `frontend.py` | Moves `app.storage.user` from a file inside the container into Redis, so a redeploy no longer signs every user out mid-event. Set by compose to `redis://redis:6379/0`. Unset → file-backed (`.nicegui/`), and a production boot logs a warning saying so. Set but unreachable → startup aborts rather than failing at the first login. |
| `NICEGUI_REDIS_KEY_PREFIX` | no | `nicegui:` | `nicegui.storage` | Key namespace, so one Redis can serve more than one deployment. |
| `LOG_LEVEL` | no | `INFO` | `main.py`, `application/utils/log_levels.py` | Root level for the application logger (`TRACE` accepted); separate from uvicorn's `--log-level`, which `start.sh` pins to `info`. Per-module levels set on `/platform` → Logging override it and persist across restarts. See [observability.md](features/observability.md). |
| `DISCORD_TOKEN` | yes, unless mock | — | `main.py`, `pages/auth.py`, `application/services/service_health_service.py` | Bot token, also used by the OAuth API client. Unset → bot skipped with a warning and Discord features dead. |
| `DISCORD_PUBLIC_KEY` | no | — | `discordbot/http_interactions.py`, `api/inbound_webhooks.py` | The application's public key (Developer Portal → General Information). Turns on `POST /discord/interactions`; unset → that path answers 404. Only takes effect once the portal's **Interactions Endpoint URL** is set to `<BASE_URL>/discord/interactions`, after which Discord sends button presses and slash commands there instead of down the gateway. See [discord.md](features/discord.md#interactions-over-http). |
| `DISCORD_GATEWAY` | no | on | `application/utils/environment.py`, `application/bot_runtime.py` | Off → this process signs in for REST only: DMs and edits still send, but there are no member events, no slash-command sync and no gateway button presses. For the API beside `bots.py`, which always runs the gateway; needs `DISCORD_PUBLIC_KEY` and the endpoint URL so buttons keep working. |
| `DISCORD_CLIENT_ID` | yes, for real OAuth | — | `pages/auth.py` | Discord application client ID; derives `OAUTH_URL`. |
| `DISCORD_CLIENT_SECRET` | yes, for real OAuth | — | `pages/auth.py` | OAuth authorization-code exchange. |
| `BASE_URL` | no | `http://localhost:8000` | `application/utils/environment.py` | Public base URL (trailing `/` stripped) for deep links, and the fallback for `PLATFORM_HOST`. **Not** the source of the OAuth callback. Set it in production — a stale value bakes an unreachable host into printed equipment QR labels, which is why the label sheet names the host it encodes and warns when that is not the host you are browsing ([frontend.md](reference/frontend.md#encoded-host-check)). |
| `PLATFORM_HOST` | no | host of `BASE_URL` | `application/utils/environment.py`, `middleware/tenant.py` | Bare `host[:port]` serving the landing page, `/platform`, path-mode tenants and the shared OAuth callbacks; logged at startup. See [multitenancy.md](features/multitenancy.md#addressing-path-mode). |
| `HOST_OAUTH_MODE` | no | `local` | `application/utils/environment.py`, `pages/auth.py`, `application/services/oauth_handoff_service.py` | Custom-domain login strategy: `local` (a redirect URI per domain) or `handoff` (one platform URI plus a signed session handoff). See [authentication.md](reference/authentication.md#custom-domain-login-design-a-vs-design-b). |
| `TRUST_FORWARDED_HOST` | no | off | `application/utils/hostname.py` | Honor `X-Forwarded-Host`/`-Proto` for host/scheme resolution — **only** behind a trusted proxy. Required for host-mode routing when the proxy rewrites `Host`. |
| `REDIRECT_URL` | no | `{scheme}://{PLATFORM_HOST}/oauth/callback` | `pages/auth.py` | Discord callback, built per request from `PLATFORM_HOST` (not `BASE_URL`) with `https` forced off localhost. Host mode additionally needs each custom domain registered. Override only for non-standard callbacks. |
| `OAUTH_URL` | no | derived | `pages/auth.py` | Discord authorize URL built per request from `DISCORD_CLIENT_ID` + `REDIRECT_URL`, `scope=identify`, `prompt=none` (skips Discord's Authorize screen for returning users). |
| `DISCORD_BOT_PERMISSIONS` | no | `268435456` (Manage Roles) | `application/services/discord/discord_link_service.py` | Permissions integer requested by the "Connect Discord server" flow; widen if the bot needs more than role application. |
| `DISCORD_CONNECT_REDIRECT_URL` | no | `{BASE_URL}/oauth/discord/connect/callback` | `application/services/discord/discord_link_service.py` | Bot-authorization callback, built per request. One URI serves every tenant; register it in the Discord Developer Portal. |
| `MOCK_DISCORD` | no | off | `application/utils/mocks/mock_discord.py` | Bypasses OAuth and stubs `DiscordService` — see [discord.md](features/discord.md#mock-mode). **Refused when `ENVIRONMENT=production`.** |
| _(randomizer API keys)_ | — | — | `application/services/seedgen_service.py` | **Not environment variables.** Each community supplies its own on Admin → Randomizer Keys. See [seed-generation.md](reference/seed-generation.md#per-tenant-credentials). |
| `MOCK_SEEDGEN` | no | off | `application/utils/mocks/mock_seedgen.py` | Short-circuits `SeedGenerationService.generate_seed` to a fake `mock.seedgen.local` permalink — **except `dk64r`**, which simulates its task queue instead (below), and `wwr`/`z1r`, which are built locally and roll for real. **Refused in production.** |
| `MOCK_DK64_SECONDS` | no | `20` | `application/utils/mocks/mock_dk64.py` | Wall-clock seconds a simulated DK64 roll takes, so the waiting UI can be exercised. Only read when `MOCK_SEEDGEN` is on; the test suite pins it to `0`. |
| `MOCK_DK64_OUTCOME` | no | `finished` | `application/utils/mocks/mock_dk64.py` | Ending the simulated DK64 task reaches: `finished`, `failed`, `http_error`, or `stuck` (never finishes, for the generation-timeout branch). |
| `MOCK_DK64_BROKEN_STAGE` | no | unset | `application/utils/mocks/mock_dk64.py` | Fails a pre-poll DK64 call instead: `convert` or `submit`. |
| `SENTRY_DSN` | no | `''` | `application/utils/sentry.py` | Enables Sentry error reporting; events tagged with `ENVIRONMENT`, the release, the tenant and the logged-in user. No-op when blank. Also handed to the browser SDK on every page (a DSN is public by design), so browser errors land in the same project; see [Browser errors](features/observability.md#browser-errors-platform--monitoring). |
| `SENTRY_TRACES_SAMPLE_RATE` | no | `0.1` in production, else `0` | `application/utils/sentry.py` | Fraction (`0`–`1`) of requests traced for performance. Only read when `SENTRY_DSN` is set. |
| `SENTRY_LOGS_LEVEL` | no | `INFO` | `application/utils/sentry.py` | Minimum level shipped to Sentry Logs; `off` disables them. Access and outbound-HTTP lines are never shipped. |
| `SENTRY_PROFILES_SAMPLE_RATE` | no | `1.0` in production, else `0` | `application/utils/sentry.py` | Share of *traced* requests also profiled, so a slow trace shows which functions burned the CPU. Relative to `SENTRY_TRACES_SAMPLE_RATE`: the defaults profile 10% of requests. |
| `SENTRY_RELEASE` | no | `wizzrobe@<GIT_SHA>` | `application/utils/sentry.py` | Release name on every Sentry event. Defaults from `GIT_SHA`; unset when neither is present. |
| `SENTRY_METRICS` | no | on | `application/utils/sentry_metrics.py` | `off` stops application metrics (load every 30s, per-route request counts and timings, worker tick times). Only read when `SENTRY_DSN` is set. See [observability.md](features/observability.md#metrics). |
| `GIT_SHA` | no | baked in by `publish.yml` | `Dockerfile` | The commit the image was built from (a build arg). Feeds the Sentry release, `/about`, `GET /api/version` and the MCP `server_version` tool. |
| `GIT_COMMIT_DATE` | no | baked in by `publish.yml` | `application/utils/build_info.py` | ISO time of that commit (a build arg). Dates the version as `YYYY.MM.DD` (UTC) when `APP_VERSION` is unset. |
| `APP_VERSION` | no | the tag on a `v*` tag build | `application/utils/build_info.py` | Overrides the version outright (a leading `v` is dropped). With neither this nor `GIT_COMMIT_DATE`, the version reads `dev`. |
| `TELEMETRY_ENABLED` | no | `true` | `application/utils/environment.py` | Runtime kill-switch for telemetry **capture**; reads and reports are unaffected. See [telemetry.md](features/telemetry.md). |
| `CHALLONGE_CLIENT_ID` | no | — | `application/services/challonge_service.py` | Challonge OAuth client ID. The integration counts as configured only when this and the secret are both set. |
| `CHALLONGE_CLIENT_SECRET` | no | — | `application/services/challonge_service.py` | Used for the service-account and per-player authorization-code exchanges. |
| `CHALLONGE_REDIRECT_URI` | no | `{BASE_URL}/challonge/oauth/callback` | `application/services/challonge_service.py` | Must match the URI registered with the Challonge OAuth app. Override only for non-standard callbacks. |
| `CHALLONGE_SCOPES` | no | `me tournaments:read tournaments:write matches:read matches:write participants:read participants:write communities:manage` | `application/services/challonge_service.py` | Scopes for the shared **service-account** connection. `communities:manage` is what lets it reach tournaments on an organization subdomain (`<org>.challonge.com`); the connected Challonge account must administer that community, and adding the scope means reconnecting. `tournaments:write` lets staff advance a bracket's stage (start, finalize groups, finalize) from Admin → Challonge; a connection made before it was added shows those buttons disabled until staff reconnect. `participants:write` lets players sign themselves up for a Challonge bracket from the Tournaments tab; until staff reconnect, Admin → Challonge says so and those sign-ups are refused. Per-player linking always uses `me` only. |
| `MOCK_CHALLONGE` | no | off | `application/utils/mocks/mock_challonge.py` | Swaps in a stub Challonge client/connection for local dev. **Refused in production.** |
| `MOCK_CHALLONGE_IDENTITY` | no | `1` | `application/utils/clients/challonge_client.py` | Which canned mock identity the mock player-OAuth flow returns. Only read under `MOCK_CHALLONGE`. |
| `MOCK_AI` | no | off | `application/utils/mocks/mock_ai.py` | Survey "Suggest questions" returns canned drafts instead of calling the community's AI provider. A saved key starting `mock-` does the same without the variable. **Ignored in production.** |
| `STARTGG_API_TOKEN` | no | — | `application/services/startgg_connection_service.py` | Optional platform-wide start.gg Personal Access Token. A community with no start.gg sign-in (Settings → Integrations) syncs and reports with it; one that has signed in uses its own account instead. |
| `STARTGG_CLIENT_ID` | no | — | `application/services/startgg_link_service.py` | start.gg OAuth client ID for the player **identity link** (`user.identity`) and the staff **community sign-in** (`user.identity tournament.reporter`). Both share one redirect URI. |
| `STARTGG_CLIENT_SECRET` | no | — | `application/services/startgg_link_service.py` | Code exchange for both flows. A player link's token is discarded; a community sign-in's tokens are stored in `StartggConnection` and refreshed. |
| `STARTGG_REDIRECT_URI` | no | `{BASE_URL}/startgg/oauth/callback` | `application/services/startgg_link_service.py` | Must match the URI registered with the start.gg OAuth app. |
| `MOCK_STARTGG` | no | off | `application/utils/mocks/mock_startgg.py` | Serves one canned event for any linked start.gg event and fakes the identity link. Set by `./start.sh mock` and `validate`. **Refused in production.** |
| `MOCK_STARTGG_IDENTITY` | no | `1` | `application/utils/clients/startgg_client.py` | Which canned mock identity the mock link flow returns. Only read under `MOCK_STARTGG`. |
| `RACETIME_CLIENT_ID` | no | — | `application/services/racetime_service.py` | racetime.gg **identity-link** OAuth client ID (read scope). Distinct from the per-category race-room bot credentials on `RacetimeBot` rows. |
| `RACETIME_CLIENT_SECRET` | no | — | `application/services/racetime_service.py` | Used once per link for the code exchange, then discarded (no token stored). |
| `RACETIME_REDIRECT_URI` | no | `{BASE_URL}/racetime/oauth/callback` | `application/services/racetime_service.py` | Must match the URI registered with the racetime OAuth app. |
| `MOCK_RACETIME` | no | off | `application/utils/mocks/mock_racetime.py` | Fakes a verified racetime identity, drives the bot runtime against a scripted event-emitting fake, **and** opens race rooms without calling racetime.gg (their `mock-room-…` slugs don't exist there). **Refused in production.** |
| `RACETIME_BOT_ENABLED` | no | off | `application/utils/environment.py` | Master switch for the racetime bot runtime (`racetimebot/`). Off → lifespan skips it, no outbound connections. On → one connection per active `RacetimeBot` category. |
| `MOCK_RACETIME_IDENTITY` | no | `1` | `application/utils/clients/racetime_client.py` | Which canned mock identity the mock link flow returns. Only read under `MOCK_RACETIME`. |
| `TWITCH_CLIENT_ID` | no | — | `application/services/twitch_service.py`, `application/services/service_health_service.py` | Twitch OAuth client ID. The account-link feature counts as configured only when this and the secret are both set. |
| `TWITCH_CLIENT_SECRET` | no | — | `application/services/twitch_service.py`, `application/services/service_health_service.py` | Used once per link for the code exchange, then discarded (no token stored). |
| `TWITCH_REDIRECT_URI` | no | `{BASE_URL}/twitch/oauth/callback` | `application/services/twitch_service.py` | Must match the URI registered with the Twitch app. |
| `MOCK_TWITCH` | no | off | `application/utils/mocks/mock_twitch.py` | Records a fake verified Twitch identity so link/unlink works without a real OAuth app. **Refused in production.** |
| `MOCK_TWITCH_IDENTITY` | no | `1` | `application/utils/clients/twitch_client.py` | Which canned mock identity the mock link flow returns. Only read under `MOCK_TWITCH`. |
| `LOOP_WATCHDOG_ENABLED` | no | on | `application/utils/environment.py`, `main.py` | Kill switch for the event-loop watchdog thread ([observability.md](features/observability.md#blocked-event-loop-watchdog)). |
| `GC_FREEZE` | no | on | `main.py` | Freezes every object alive at the end of startup out of garbage collection (`gc.freeze()`), so full collections skip them ([observability.md](features/observability.md#garbage-collection-pauses)). `false` turns it off. |
| `GC_REFREEZE` | no | on | `main.py` | Freezes the survivors of each full collection too, handing them back every `GC_THAW_MINUTES`, so pauses stay short as tabs pile up ([observability.md](features/observability.md#garbage-collection-pauses)). `false` turns it off; off whenever `GC_FREEZE` is. |
| `GC_THAW_MINUTES` | no | 10 | `main.py` | How often the frozen survivors are handed back for one ordinary full collection, which frees any garbage cycles among them ([observability.md](features/observability.md#garbage-collection-pauses)). Longer means fewer slow passes but more memory held between them. |
| `MEMORY_CENSUS_DUMP` | no | unset | `main.py` | CI only. A file path: each `SIGUSR1` writes a `memory_census` there as JSON (the pid goes to `<path>.pid`), for `scripts/ui_leak_check.js`. Leave unset in production. |
| `SPEEDGAMING_SYNC_ENABLED` | no | off | `application/utils/environment.py` | Master switch for the SpeedGaming ETL sync worker. Off → no outbound polls. On → each active `SpeedGamingEventLink` is polled on its cadence and materialized into `Match` rows. |
| `MOCK_SPEEDGAMING` | no | off | `application/utils/clients/speedgaming_client.py` | Scripted fake returning canned episodes, so the ETL is exercisable without speedgaming.org. **Refused in production.** |
| `MOCK_MATCHERINO` | no | off | `application/utils/clients/matcherino_client.py` | Dev only. Swaps in `MockMatcherinoClient`, whose canned venue badge sales line up with the dev-seed fixtures, so event check-in syncs without calling Matcherino's unofficial API. Set by `./start.sh mock` and `validate`. **Refused in production.** |
| `DISCORD_EVENTS_SYNC_ENABLED` | no | off | `application/utils/environment.py` | Master switch for the Discord Scheduled Events reconciler worker. Off → nothing runs on a timer; the admin **Discord Events** tab still reconciles on demand. |
| `SERVICE_HEALTH_ALERT_DM` | no | off | `application/utils/environment.py` | Opt in to DMing every super-admin on an unhealthy transition. Transitions always publish a `service_health.alert` event and capture to Sentry regardless. |
| `VAPID_PRIVATE_KEY` | no | — | `application/services/web_push_service.py` | Enables web push. Base64url raw 32-byte P-256 key from `scripts/generate_vapid_keys.py`. **Keep secret and stable** — rotating it invalidates every subscription. See [web-push.md](features/web-push.md). |
| `VAPID_SUBJECT` | no | `BASE_URL` when https | `application/services/web_push_service.py` | `mailto:` or `https:` contact sent to push services (RFC 8292). Without a usable value web push stays off even with a key. |
| `SENDGRID_API_KEY` | no | — | `application/utils/clients/sendgrid_client.py` | Enables email notifications (with `EMAIL_FROM_ADDRESS`). A SendGrid key with Mail Send access. See [notification-channels.md](features/notification-channels.md). |
| `EMAIL_FROM_ADDRESS` | no | — | `application/utils/clients/sendgrid_client.py` | Sender address; its domain must be authenticated in SendGrid (SPF/DKIM) or mail lands in spam. |
| `EMAIL_FROM_NAME` | no | `Wizzrobe` | `application/utils/clients/sendgrid_client.py` | Sender display name. |
| `TELEGRAM_BOT_TOKEN` | no | — | `application/utils/clients/telegram_client.py`, `main.py` | Enables Telegram notifications and starts the long-polling bot worker. The token @BotFather gives for `/newbot`. **Keep secret.** |
| `TELEGRAM_WEBHOOK_SECRET` | no | — | `application/utils/clients/telegram_client.py`, `application/services/telegram_bot_worker.py`, `api/inbound_webhooks.py` | Switches the Telegram bot from long polling to a webhook: at boot it registers `<BASE_URL>/telegram/webhook` with this secret and doesn't poll; Telegram echoes the secret in `X-Telegram-Bot-Api-Secret-Token` on each update. 1–256 of `A-Z a-z 0-9 _ -`. Unset → polling, which first deletes any webhook left behind. Required when the API runs on instances that scale to zero. **Keep secret.** |
| `DISCORD_EMAIL_REDIRECT_URI` | no | `{BASE_URL}/discord/email/callback` | `application/services/discord/discord_email_service.py` | Callback for "Use my Discord email". Must be added to the Discord application's OAuth2 redirects. |
| `API_RATE_LIMIT_PER_MIN` | no | `120` | `api/rate_limit.py` | Per-client budget per minute, keyed by token (or client IP). Shared by the REST API and MCP, so one credential gets one budget. |
| `API_APP_RATE_LIMIT_PER_MIN` | no | `600` | `api/rate_limit.py` | Ceiling per third-party app across all of its users' grants, on top of each grant's own budget. |
| `MCP_ENABLED` | no | on | `mcpserver/__init__.py` | Kill switch for the remote MCP server: off removes `/mcp`, its OAuth and discovery routes, and the lifespan hook. See [mcp-server.md](features/mcp-server.md). |
| `TRUST_PROXY_FORWARDED_FOR` | no | off | `api/rate_limit.py` | Rate limiter trusts `X-Forwarded-For` for the client IP — only behind a trusted proxy. |

See [reference/authentication.md](reference/authentication.md) for how the OAuth variables are wired into the login flow.

**Reverse proxy:** `/mcp` and `/.well-known/*` must be passed through unrewritten.
`BASE_URL` must be the public URL — it is the MCP resource identifier and the
OAuth issuer, both of which clients compare against what they requested.
MCP's DNS-rebinding protection is deliberately disabled because `Host` and TLS
terminate at the proxy; see [features/mcp-server.md](features/mcp-server.md).

### Startup refusals (fail-fast)

Four independent checks abort the process before it serves a request:

1. **[`migrations/tortoise_config.py`](../migrations/tortoise_config.py)** (import time): `DB_HOST`, `DB_PORT`, `DB_NAME` are always required; when `ENVIRONMENT=production`, blank `DB_USERNAME` or `DB_PASSWORD` raises `ValueError`.
2. **`validate_security_config()`** in `application/utils/environment.py` (called from `frontend.init()`): blank `STORAGE_SECRET` raises `RuntimeError` in every environment; in production a `STORAGE_SECRET` shorter than 32 characters, or a blank `DB_USERNAME`/`DB_PASSWORD`, also raises.
3. **`validate_session_storage()`** in `application/utils/environment.py` (called from `frontend.init()`): `NICEGUI_REDIS_URL` set but Redis unreachable (a one-shot `PING`, 5s timeout) raises `RuntimeError`.
4. **`is_mock_discord()`** in `application/utils/mocks/mock_discord.py` (evaluated at import of `pages/auth.py`, and again in the lifespan): `MOCK_DISCORD` truthy while `ENVIRONMENT=production` raises `RuntimeError` — the mock layer is a complete authentication bypass and must never run in production.

## Startup behavior

Boot sequence (`main.py` lifespan, after the import-time checks above):

1. **Migrations auto-apply** — `init_db()` runs Aerich `upgrade()` against `./migrations` under a PostgreSQL advisory lock (so two overlapping container starts cannot apply the chain twice), then `Tortoise.init()`. Every boot applies any pending migrations; deploys need no manual `aerich upgrade` step.
2. **Discord bot** — `bot_runtime.start_discord()` starts the bot with `DISCORD_TOKEN` as a background task on the shared event loop: the gateway by default, a REST-only sign-in with `DISCORD_GATEWAY=off`. Skipped entirely under `MOCK_DISCORD`; without a token it logs a warning and continues.
3. **Racetime bot runtime and workers** — the racetime runtime and the env-gated workers (`RACETIME_BOT_ENABLED`, `SPEEDGAMING_SYNC_ENABLED`, `DISCORD_EVENTS_SYNC_ENABLED`) start only when switched on; the service-health probe loop, the async-qualifier expiry, seed-roll polling, volunteer and stage reminders always start.
4. **DM queue and event bus** — `bot_runtime.start_delivery()`: `discord_queue.start()` launches a single in-process worker that drains queued Discord notification coroutines sequentially; the event dispatch queue starts and the webhook and telemetry subscribers register.

Each step is timed in the log (`wizzrobe.main` at INFO), so a slow restart names its own culprit:

```
startup: module imports took 2.69s
startup: database starting
startup: aerich init took 0.21s
Waiting for the migration lock (key=...)...
Holding the migration lock (key=...) after waiting 0.00s.
startup: migration upgrade took 0.09s
...
startup: complete in 3.12s (lifespan 0.43s)
```

Every step logs a `starting` line before it runs, so a step that hangs is the last `starting` with no matching `took`. The usual suspects: `module imports` runs several seconds longer in a fresh container (no bytecode cache); a long `after waiting` on the migration lock means the boot queued behind another process still holding it (up to the 300s timeout); a long `migration upgrade` comes with an `Applied N migration(s)` line.

**Shutdown has a 10s deadline.** uvicorn waits for every open request before it runs the lifespan shutdown below, and without a deadline a single request that never finishes holds the process until the supervisor's stop timeout (systemd: 90s) SIGKILLs it, so the shutdown steps never run at all. `start.sh` passes `--timeout-graceful-shutdown 10`. After that uvicorn cancels whatever is left (`Cancel N running task(s), timeout graceful shutdown exceeded`) and `middleware/inflight_requests.py` logs each one as `Request cancelled while still open: GET /path (open 12.3s)` on `wizzrobe.shutdown`. A host supervisor's stop timeout must stay above 10s.

Shutdown reverses this: the workers stop, the queue workers are cancelled (any still-queued DMs are counted, logged, and dropped), the bot connection closes, then DB connections close. See [architecture.md](architecture.md#process-model-and-startup) for the full sequence.

### Single worker — required

`start.sh prod` runs `uvicorn main:app --workers 1`. **Do not raise the worker count** — bots, queues, background loops, NiceGUI client state, the OAuth handoff nonce store and the per-match seed lock are all in-process singletons, and Aerich runs `upgrade()` at boot. Scale vertically; the full inventory and the phased `web`/`worker` split that would lift the constraint are in [scaling-roadmap.md](scaling-roadmap.md).

### `start.sh` modes

| | `./start.sh dev` | `./start.sh mock` | `./start.sh validate` | `./start.sh prod` |
|---|---|---|---|---|
| Reload | `--reload` | `--reload` | **off** | off |
| Host binding | uvicorn default (`127.0.0.1`) | uvicorn default (`127.0.0.1`) | uvicorn default (`127.0.0.1`) | `0.0.0.0` (required in a container) |
| Workers | n/a (reload implies one process) | n/a | one | `--workers 1` |
| Port | 8000 | 8000 | 8000 | 8000 |
| Forced env | — | `ENVIRONMENT=development`, `MOCK_DISCORD`, `MOCK_SEEDGEN`, `MOCK_CHALLONGE`, `MOCK_TWITCH`, `MOCK_RACETIME`, `MOCK_MATCHERINO`, `MOCK_STARTGG` | same as `mock` | `ENVIRONMENT=production` |

`mock` is the one-command offline dev loop — no Discord, Challonge, Twitch, racetime.gg, Matcherino or randomizer credentials needed. `validate` is `mock` without the watcher, for browser runs where a mid-run reload would tear down open pages and fill the log with teardown tracebacks; see [development.md](development.md#running-the-server-and-the-two-ways-it-bites).

`./start.sh stop` stops whichever of them is running. Use it rather than `pkill -f`, which reliably kills its own shell instead — the reasoning is in [development.md](development.md#stopping-it-never-pkill--f), and `enforce_safe_commands.py` blocks the form.

The reload modes exclude `.nicegui/`, `*.log` and `.pytest_cache/` from the watcher. Without that, the per-session `storage-user-<id>.json` the app writes into `.nicegui/` restarts the server on every login.

Any other argument prints an error and exits 1; the script `cd`s to its own directory first, so it can be started from anywhere.

## Operations

First deploy:

```bash
cp .env.example .env            # fill in credentials (see table above)
docker compose up -d
docker compose logs -f wizzrobe   # watch migrations apply and uvicorn start
```

- **Logs:** `docker compose logs -f wizzrobe` (uvicorn, bot, and queue output) and `docker compose logs -f postgres`.
- **Restart app only:** `docker compose restart wizzrobe` — the database keeps running.
- **Upgrade:**

  ```bash
  git pull
  docker compose build wizzrobe     # or: docker compose pull wizzrobe, if deploying a GHCR image
  docker compose up -d wizzrobe     # recreates the container; pending migrations apply on boot
  ```

- **psql access:** the DB is not published to the host; use `docker compose exec postgres psql -U wizzrobe -d wizzrobe` (names from your `.env`).
- **Manual migration commands** (rarely needed — boot does this automatically):

  ```bash
  docker compose exec wizzrobe poetry run aerich upgrade
  ```

  Schema and migration details: [reference/data-model.md](reference/data-model.md).

### Tenant and role bootstrap

The [multitenancy migration](reference/data-model.md#migrations) is additive: it backfills a `default` tenant and moves existing rows into it, so no manual step is needed at upgrade time. These scripts stand up the platform layer afterwards — all idempotent, and each target user must have logged in at least once so their `User` row exists:

```bash
# Grant yourself the global SUPER_ADMIN role (manages tenants at /platform):
docker compose exec wizzrobe poetry run python scripts/grant_super_admin.py <discord_id>

# Create additional communities going forward (also bootstraps their first admin):
docker compose exec wizzrobe poetry run python scripts/seed_tenant.py \
    --name "Wizzrobe Live" --slug wizzrobe [--guild-id <discord_guild_id>] \
    [--operator-discord-id <discord_id>]

# Grant STAFF within a specific tenant (defaults to the `default` tenant):
docker compose exec wizzrobe poetry run python scripts/grant_staff.py <discord_id> [tenant_slug]
```

### Importing SGL tournaments from the planning sheet

`scripts/import_sgl_tournaments.py` reads the published SGL planning workbook (the `Onsite` tab's tournament table) and creates or updates the community's tournaments to match, then links each to its Challonge bracket. Writes go through `TournamentService`/`ChallongeService` as the `--actor` staff member, so they're permission-checked and audited. It's a dry run unless you pass `--apply`, matches existing tournaments by name, never blanks a field from an empty cell, and leaves an already-linked Challonge bracket alone, so re-running is safe.

```bash
docker compose exec wizzrobe poetry run python scripts/import_sgl_tournaments.py --actor <discord_id>          # preview
docker compose exec wizzrobe poetry run python scripts/import_sgl_tournaments.py --actor <discord_id> --apply  # write
# --tenant (default sgl26), --source <pubhtml URL or .xlsx path>, --sheet (default Onsite), --skip-challonge
```

| Sheet column | Tournament field |
|---|---|
| Tournament | `name` (the match key) |
| Format | `tournament_format` (lines joined with `; `) |
| Ave Time / WCS Time | `average_match_duration` / `max_match_duration` (a range uses its midpoint) |
| Rules (hyperlink) | `rules_url` |
| Brackets link, else Challonge link | `bracket_url` |
| Challonge (hyperlink) | Challonge link via `ChallongeService.link_tournament`, skipped when the Brackets column is filled in (the bracket runs elsewhere, e.g. start.gg) |
| Deadline | `signups_close_at`, read on the community's clock |
| Admin, Qualifiers, Trophies, Seed Generation | `description` |
| Tournament name (keyword table in the script) | `seed_generator` |

Admins are only recorded in the description; the script doesn't grant tournament-admin membership.

`scripts/seed_dev.py` seeds two tenants of fixtures for local dev. See [features/multitenancy.md](features/multitenancy.md) for the addressing model and how a request resolves to a tenant.

### Backup and restore

Logical dumps are the recommended backup. `POSTGRES_USER`/`POSTGRES_DB` are already set inside the container, so these are copy-pasteable as-is:

```bash
# Backup (custom format)
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c' > wizzrobe-$(date +%F).dump

# Restore
docker compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < wizzrobe-YYYY-MM-DD.dump
```

A filesystem-level alternative is archiving the `postgres_data` volume (named `<project>_postgres_data`, e.g. `wizzrobe_postgres_data`) — only with the stack stopped (`docker compose stop`), never while postgres is running.

### Rollback caveat

Migrations are **forward-applied at every boot**. Rolling back to an older image does **not** undo schema changes: the old code will run against the newer schema, and any image you boot will still auto-upgrade to the newest migration present in its `migrations/` directory. To truly revert a schema change, restore the database from a dump taken before the upgrade. **Take a backup before deploying any release that contains migrations.**

## Health & monitoring

`GET /api/health` (`api/routers/health.py`) is the liveness/readiness probe: unauthenticated, it runs `SELECT 1` and returns `{"status": "ok"}`, or `503` when the database is unreachable. Both the Dockerfile `HEALTHCHECK` and the compose healthcheck poll it.

| Probe | What it proves |
|---|---|
| `GET /api/health` | The app is serving **and** can reach PostgreSQL |
| `GET /` | UI is up (NiceGUI page render, no login required) |
| `GET /api/docs` | FastAPI is serving (no DB involved) |

```bash
curl -fsS http://localhost:8000/api/health > /dev/null && echo OK
```

- **Liveness** inside compose is covered by the healthchecks — `pg_isready` and `redis-cli ping` (5s interval, 10 retries) and the app's `/api/health` (30s); `docker compose ps` shows the `healthy` state.
- **Crash recovery:** all three services use `restart: unless-stopped`, so crashed containers come back automatically.
- **Logs to watch:** everything goes to stdout/stderr at `--log-level info`. Notable lines: `DISCORD_TOKEN not set. Discord features will not work.` (app runs but Discord features are dead), `discord_queue worker error` with a traceback (a queued Discord notification failed), `discord_queue stopping with N item(s) still queued` at shutdown (those DMs were dropped), and the `Session storage:` line at boot, which warns in production when sessions are file-backed.
