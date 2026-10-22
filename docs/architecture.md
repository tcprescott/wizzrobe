# Wizzrobe System Architecture

Wizzrobe is a web application for running tournament events: scheduling matches, enrolling players, coordinating commentators and trackers, assigning stream stages, generating randomizer seeds, and notifying everyone over Discord. It is a single Python process that serves a NiceGUI web UI, a small public REST API, and an in-process Discord bot, backed by PostgreSQL.

This document is the starting point for understanding the system. Each section links to a deeper reference doc; see the [documentation index](README.md) for the full catalog.

## Tech stack

Versions come from [`pyproject.toml`](../pyproject.toml) (Poetry); only major/minor shown here.

| Layer | Technology |
|---|---|
| Language | Python ≥3.12 |
| Web framework | FastAPI ≥0.136 |
| UI framework | NiceGUI ≥3.12 |
| ASGI server | Uvicorn (via `start.sh`) |
| ORM | Tortoise ORM ≥0.24 (asyncpg driver) |
| Migrations | Aerich ≥0.8 |
| Database | PostgreSQL (16-alpine in docker-compose) |
| Discord bot | discord.py ≥2.7 |
| Discord OAuth | zenora |
| MCP server | `mcp` (MCPServer) ≥2.3 |
| Session store | Redis 7 via `NICEGUI_REDIS_URL` (optional; falls back to a local file) |
| Seed generation | pyz3r (ALTTPR) + HTTP APIs for other randomizers |
| Testing | pytest ≥9, pytest-asyncio (`asyncio_mode = "auto"`), pytest-xdist (parallel by default), aiosqlite (in-memory test DB) |
| Lint / types | ruff (blocking), mypy (per-file ratchet) |
| Packaging / runtime | Poetry, Docker + docker-compose |

## Process model and startup

Everything runs in **one Uvicorn worker** (`start.sh prod` passes `--workers 1`) because the process hosts the Discord bot connection, the in-memory DM queue and NiceGUI's per-client websocket state, and NiceGUI is single-worker by design. The full singleton inventory and the way out (a `web`/`worker` process split, not more uvicorn workers): [scaling-roadmap.md](scaling-roadmap.md).

[`main.py`](../main.py) builds the FastAPI app and wires everything together:

1. **Module load** — `init_sentry()` (no-op without `SENTRY_DSN`), then the FastAPI app (`docs_url="/api/docs"`, `redoc_url="/api/redoc"`, `openapi_url="/api/openapi.json"`), `FunFactMiddleware` and `SecurityHeadersMiddleware`, the REST routers from [`api/`](../api/) at the `/api` prefix ([reference/rest-api.md](reference/rest-api.md)), then `mcpserver.mount(app)` — **before** `frontend.init(app)`, because NiceGUI mounts itself at `/` and would swallow `/mcp` ([features/mcp-server.md](features/mcp-server.md)).
2. **Lifespan startup**
   1. `init_db()` — runs Aerich `upgrade()` under a PostgreSQL advisory lock (`application/utils/migration_lock.py`, so two overlapping container starts cannot race the chain), then `Tortoise.init()` with the config from [`migrations/tortoise_config.py`](../migrations/tortoise_config.py).
   2. `import discordbot` registers the interaction handlers and DM view factories, then `init_discord_bot()` starts the discord.py bot as a task using `DISCORD_TOKEN`. Skipped under `MOCK_DISCORD`; logs a warning and continues if the token is unset.
   3. `init_racetime_bot()` — the racetime bot runtime (a no-op unless `RACETIME_BOT_ENABLED`).
   4. Background workers. Env-gated (off by default): `race_room_worker` (with the racetime runtime), `speedgaming_sync_worker` (`SPEEDGAMING_SYNC_ENABLED`), `discord_event_worker` (`DISCORD_EVENTS_SYNC_ENABLED`). Always on: `service_health_worker` (mocked dependencies report `mock`), `async_qualifier_worker`, `seed_roll_worker`, `check_in_sync_worker` (skips tenants with `EVENT_CHECK_IN` off), `sglive_pool_worker` (idle until a tournament is linked; skips tenants with `PAYOUTS` off), `discord_queue` (serializes outbound DMs — [reference/discord-integration.md](reference/discord-integration.md)), `volunteer_reminder`, `stage_reminder`.
   5. The event bus: `event_dispatch_queue.start()`, then the webhook delivery and telemetry mirror subscribers ([features/event-system.md](features/event-system.md)).
   6. `mcpserver.session_lifespan()` wraps the `yield`, so `/mcp` stops accepting calls before the workers tear down.
3. **Lifespan shutdown** — stops every worker and the racetime runtime, then `event_dispatch_queue` → `discord_queue` → the web-push HTTP client → `close_discord_bot()` → `Tortoise.close_connections()`.

[`frontend.py`](../frontend.py) then attaches the UI:

- `validate_security_config()` refuses to start with an insecure configuration (missing `STORAGE_SECRET`; missing DB credentials in production), and `validate_session_storage()` refuses a configured-but-unreachable `NICEGUI_REDIS_URL`; the startup log then says which session store is live. Separately, `is_mock_discord()` raises at startup if `MOCK_DISCORD` is enabled while `ENVIRONMENT=production`.
- The `static/` directory is mounted at `/static` through `VersionedStaticFiles`: development sends `no-store` so CSS/JS edits show up on refresh, and production caches a stamped URL (`?v=<mtime>`, written by `theme.assets.asset_url`) for a year and revalidates everything else. Link static assets through `asset_url` or a deploy will not reach clients that already have the old file — see [reference/frontend.md](reference/frontend.md#static-assets--styling-static).
- Three middlewares are registered at import time (Starlette runs the last-added outermost, so the effective order is session → `TransportPrefixMiddleware` → `TenantMiddleware` → `AuthMiddleware`): `AuthMiddleware` (Discord OAuth session enforcement), `TenantMiddleware` (resolves the tenant from `/t/<slug>` or a custom `Host` and rewrites the ASGI scope — see [features/multitenancy.md](features/multitenancy.md)), and `TransportPrefixMiddleware` (un-prefixes NiceGUI/asset transport paths). The resolved `PLATFORM_HOST` is logged at startup.
- `/robots.txt`, the root-scoped `/sw.js` service worker and the `/brand-logo/…` community-logo route are registered, then the OAuth routes and every page module imported in [`frontend.py`](../frontend.py), then `ui.run_with(fastapi_app, storage_secret=...)` mounts NiceGUI onto the FastAPI app at the root path. `PublicCacheMiddleware` is added after that (outside the session middleware, so it can strip `Set-Cookie` from the cacheable spectator responses), and `register_error_handlers()` installs the error pages.

`main.py` is not run directly — `start.sh dev|mock|validate|prod` sources `.env` and launches Uvicorn (`dev` and `mock` add `--reload`; `validate` is `mock` without it), and `start.sh stop` stops it.

## Three-layer pattern

The codebase follows a strict three-layer separation; the rules and rationale live in [refactoring-guide.md](refactoring-guide.md) and the conventions section of [`CLAUDE.md`](../CLAUDE.md).

```
Presentation  (pages/, theme/)          NiceGUI components, user interaction, ui.notify
      ↓ calls
Service       (application/services/)   business rules, validation, audit logs, Discord sends
      ↓ calls
Repository    (application/repositories/)  pure ORM data access
      ↓ uses
Models        (models/)                 Tortoise ORM models + enums
```

**Entry surfaces (`api/`, `discordbot/`, `mcpserver/`) obey the presentation rule.** The REST routers, the Discord interaction handlers and the MCP tools are additional presentation layers alongside the web UI: they call services and may do read-only *load-or-404* model lookups, but never import `application.repositories` or reach through a service's internal `.repository`. `.claude/scripts/enforce_architecture.py` classifies all three as presentation and enforces this on every edit.

## Component diagram

```mermaid
flowchart LR
    subgraph Browser
        UI[NiceGUI web UI]
    end
    subgraph "Wizzrobe process (uvicorn, 1 worker)"
        FE["pages/ + theme/<br/>(presentation)"]
        SVC["application/services/<br/>(business logic)"]
        REPO["application/repositories/<br/>(data access)"]
        API["api/ routers<br/>REST API (token auth)"]
        AUTH["middleware/auth.py<br/>Discord OAuth"]
        BOT["Discord bot (discord.py)<br/>+ discordbot/ handlers"]
        Q["discord_queue<br/>(async DM worker)"]
    end
    PG[(PostgreSQL)]
    DISCORD[Discord API]
    SEED["Randomizer APIs<br/>(alttpr.com, ootrandomizer.com, …)"]

    UI <--> FE
    UI -->|/login, /oauth/callback| AUTH
    FE --> SVC
    API --> SVC
    API -->|read-only load-or-404 lookups| PG
    AUTH --> SVC
    SVC --> REPO
    REPO --> PG
    SVC --> Q
    Q --> BOT
    BOT <--> DISCORD
    AUTH <--> DISCORD
    SVC -->|seed generation| SEED
    DISCORD -->|button interactions| BOT
    BOT --> SVC
```

Notes on the arrows:

- The REST API (the [`api/`](../api/) package) is a full read/write API authenticated by personal access tokens (`Authorization: Bearer …`, see [reference/rest-api.md](reference/rest-api.md)). A few routers (e.g. `api/routers/matches.py`) still read models directly with a tenant-scoped `filter`, the sanctioned read-only shape.
- Discord button interactions (crew signup, match acknowledgment, unwatch) arrive at the bot and call back into the service layer. See [reference/discord-integration.md](reference/discord-integration.md).
- Outbound DMs are never sent inline from request handlers; services enqueue them onto `discord_queue` so UI interactions don't block on Discord.

## Authentication in one paragraph

Users log in with Discord OAuth: `/login` redirects to Discord with a CSRF `state` token, `/oauth/callback` exchanges the code (via zenora), upserts the `User` row, and stores `discord_id` in NiceGUI's signed session storage. `AuthMiddleware` redirects unauthenticated requests to protected routes; the `protected_page` decorator adds optional role and feature-flag requirements per page. Authorization runs through `AuthService` over the `Role` enum (canonical list: `models.Role`) plus per-tournament admin/crew-coordinator membership — roles are evaluated **within the current tenant**, and the one global `SUPER_ADMIN` bypasses that gate. Mechanics and role semantics: [reference/authentication.md](reference/authentication.md); tenant resolution: [features/multitenancy.md](features/multitenancy.md). For local development without a Discord app, `MOCK_DISCORD=true` swaps in a user-picker login page and stubs all Discord calls ([features/discord.md](features/discord.md#mock-mode)).

## Directory map

Every top-level entry in the repository, with the doc that covers it:

| Path | What it is | Reference |
|---|---|---|
| `main.py` | App entry point: lifespan, DB init, bot init, router mounting | this doc |
| `frontend.py` | NiceGUI ↔ FastAPI integration, static files, page registration | [reference/frontend.md](reference/frontend.md) |
| `api/` | Public REST API (routers, Pydantic schemas, token auth, rate limiting) | [reference/rest-api.md](reference/rest-api.md) |
| `models/` | All Tortoise ORM models and enums, split into per-domain submodules | [reference/data-model.md](reference/data-model.md) |
| `application/services/` | Business-logic layer: flat `<domain>_service.py` modules plus a subpackage per outgrown domain — `match/`, `discord/`, `volunteer/`, `async_qualifier/`, `bracket_engines/`, `tournament_strategies/`, `_bracket/`. The top-level barrel re-exports everything, so `from application.services import X` is layout-independent | [reference/services.md](reference/services.md) |
| `application/repositories/` | Data-access layer: one repository per model family | [reference/data-model.md](reference/data-model.md) |
| `application/tenant_context.py`, `application/timezone_context.py`, `application/table_preferences_context.py`, `application/feature_flags.py` | Request-time tenant / viewer-timezone / table-layout context + the feature-flag registry | [features/multitenancy.md](features/multitenancy.md), [features/feature-flags.md](features/feature-flags.md) |
| `application/events/` | In-process event bus (`event_bus`, `EventType`, the async dispatch queue) and the narrow `match_live` / `check_in_live` UI-refresh channels | [features/event-system.md](features/event-system.md) |
| `application/help/`, `application/event_info/`, `application/content/` | In-repo Markdown for `/help` and `/event-info` and the shared closed-block loader | [features/help.md](features/help.md), [features/event-information.md](features/event-information.md) |
| `application/changelog/` | `changelog.md` (the `/changelog` "What's new" entries) and its loader | [features/changelog.md](features/changelog.md) |
| `application/errors.py`, `application/randomizer_credentials.py` | Shared service error types (`NotFoundError`, `FeatureDisabledError`, …); the per-randomizer credential registry | [reference/services.md](reference/services.md), [reference/seed-generation.md](reference/seed-generation.md) |
| `application/utils/` | Helpers (timezone, environment validation, CSV export, QR codes, web push, Sentry, host/URL/session), plus `clients/` (Challonge/Matcherino/Twitch/racetime/SpeedGaming/OAuth-identity HTTP clients) and `mocks/` (the `MOCK_*` flags and their offline stand-ins) | [reference/services.md](reference/services.md), [timezone-handling.md](timezone-handling.md) |
| `middleware/` | `auth.py` (`protected_page` / `public_page` + `AuthMiddleware`), `tenant.py` (`TenantMiddleware` + `TransportPrefixMiddleware`), `public_cache.py`, `error_handlers.py`, `security_headers.py` | [reference/authentication.md](reference/authentication.md), [features/multitenancy.md](features/multitenancy.md) |
| `discordbot/` | Discord interaction handlers (buttons for signup/ack/watch, crew & volunteer acknowledgment) | [reference/discord-integration.md](reference/discord-integration.md) |
| `mcpserver/` | Remote MCP server at `/mcp` (peer of `api/`): typed tools over the same services — reads for every connection, match writes for one whose consent screen approved them — with its own OAuth 2.1 authorization server. Mounted before NiceGUI; its transport session manager runs for the app's lifetime | [features/mcp-server.md](features/mcp-server.md) |
| `racetimebot/` | Racetime bot runtime (peer of `discordbot/`): one lifespan-managed connection per active `RacetimeBot` category, health tracking, tenant-routed room-event handlers; gated by `RACETIME_BOT_ENABLED`, mockable via `MOCK_RACETIME` | [reference/services.md](reference/services.md) |
| `pages/` | NiceGUI pages: home, admin (role-gated tabs), volunteer, equipment, the check-in desk, brackets, qualifiers, super-admin `platform.py`, and the OAuth login/link pages | [reference/frontend.md](reference/frontend.md), [reference/authentication.md](reference/authentication.md) |
| `theme/` | `base.py` layout shell, `dialog/` (dialogs), `tables/` (table views), `brackets/` (bracket renderer + static spectator view), `help/`, `realtime.py`, and shared widgets | [reference/frontend.md](reference/frontend.md) |
| `static/` | CSS (and other static assets) served at `/static` | [reference/frontend.md](reference/frontend.md) |
| `presets/` | Built-in randomizer preset files (alttpr/, dk64r/, ootr/, smmap/) | [reference/seed-generation.md](reference/seed-generation.md) |
| `migrations/` | Tortoise connection config + Aerich migration files | [reference/data-model.md](reference/data-model.md), [deployment.md](deployment.md) |
| `scripts/` | Idempotent dev fixtures (`seed_dev.py` and the online/Challonge/bracket seeds), tenant and role bootstrap (`seed_tenant.py`, `grant_super_admin.py`, `grant_staff.py`), the CI drivers (`guardrails.py`, `mypy_ratchet.py`, `ui_smoke.js`), dev drivers (`run_worker_tick.py`, `set_feature_flags.py` + `ui_flag_sweep.sh`), `loadtest/`, and one-off utilities | [development.md](development.md), [deployment.md](deployment.md) |
| `tests/` | pytest suite: `services/`, `api/`, `mcp/`, `tenancy/` (tenant context + leak tests), `theme/` and `pages/` (pure presentation logic), `postgres/` (PostgreSQL-only), `utils/`, plus cross-cutting utility suites at the root | [development.md](development.md) |
| `docs/` | This documentation | [README.md](README.md) |
| `start.sh` | Uvicorn launcher (`dev`/`mock`/`validate`/`prod`, plus `stop`); sources `.env` | [deployment.md](deployment.md) |
| `Dockerfile`, `docker-compose.yml` | Container build and postgres+app stack | [deployment.md](deployment.md) |
| `.github/workflows/` | CI (`test.yml`), GHCR image publishing (`publish.yml`), `security.yml` (pip-audit), `codeql.yml` (code scanning, weekly) and `guardrail-sweep.yml` (weekly whole-tree guardrails) | [development.md](development.md), [deployment.md](deployment.md) |
| `pyproject.toml`, `poetry.lock` | Poetry dependencies, pytest and Aerich config | [development.md](development.md) |
| `.env.example` | Annotated template for all environment variables | [deployment.md](deployment.md) |
| `CLAUDE.md`, `AGENTS.md`, `.claude/` | Conventions and AI-assistant development guides; `.claude/` holds the hooks, guardrail scripts and skills | [`.claude/README.md`](../.claude/README.md) |
| `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` | Contribution guide, vulnerability reporting, licence | — |
| `README.md` | Quick-start readme | — |

## Key design decisions

- **UTC in the database, viewer-local in the UI.** All datetimes are stored UTC; every user-facing render goes through `application/utils/timezone.py`, on a clock resolved per request (tenant pin, else the viewer's profile/browser zone). See [timezone-handling.md](timezone-handling.md).
- **Role-based access, no permission bits on `User`.** Roles live in the `UserRole` junction table and are **per-tenant** (nullable `tenant` FK; the one global `SUPER_ADMIN` uses `tenant=NULL`); tournament-scoped authority comes from `Tournament.admins` / `Tournament.crew_coordinators` M2M membership. See [reference/authentication.md](reference/authentication.md#roles).
- **Logically multitenant, no auto-scoping manager.** One process and DB serve many communities; the `tenant` FK on nearly every model a community owns is scoped explicitly through `application/repositories/_tenant.py`, and `require_tenant_id()` raising is the safety net. Users are global. See [features/multitenancy.md](features/multitenancy.md).
- **The bot and the web UI are peers.** Both call the same service layer, so a crew signup from a Discord button and one from the web page follow identical business rules and audit logging.
- **Migrations apply on boot.** `init_db()` runs `aerich upgrade` every start; deploys are "pull new image, restart". The flip side: rollbacks need care, since an old image won't un-apply a new migration ([deployment.md](deployment.md)).
- **Mockable Discord boundary.** Every Discord touchpoint (OAuth, DMs, bot) sits behind `MOCK_DISCORD` so the full app is developable with no Discord credentials ([features/discord.md](features/discord.md#mock-mode)).
- **Single squashed initial migration.** The schema history was consolidated into one init migration (`migrations/models/0_20260608213149_init.py`); new schema changes add migrations on top.
