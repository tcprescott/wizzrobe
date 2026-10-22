# Development Guide

_Local setup, the mock-Discord dev loop, fixtures, migrations, tests, and CI. Part of the [documentation index](README.md)._

## Prerequisites

- **Python 3.12+** — [`pyproject.toml`](../pyproject.toml) declares `python = "^3.12"`
- **Poetry** — dependency management and virtualenv
- **Docker + docker-compose** — for PostgreSQL 16 (and optionally the full containerized stack)
- **A Discord application** (bot token + OAuth client) — *optional*; local development works without one via [`MOCK_DISCORD`](#local-development-without-discord-mock_discord)

## First-time setup

1. Clone the repository and install dependencies:

   ```bash
   git clone <repo-url> wizzrobe
   cd wizzrobe
   poetry install
   ```

2. Create your `.env` from the template:

   ```bash
   cp .env.example .env
   ```

   For a local loop you need `DB_NAME`, `DB_USERNAME`, `DB_PASSWORD` and `STORAGE_SECRET`, plus `DB_HOST`/`DB_PORT` (e.g. `localhost` / `5432`) when the app runs on your host rather than in the container — compose injects those two only for the app container, and [`migrations/tortoise_config.py`](../migrations/tortoise_config.py) raises at startup without them. The template ships `ENVIRONMENT=production`; change it to `development` or use `./start.sh mock`, which forces the whole mock set for you. Everything else — including which variables are safe to leave unset — is in the [deployment guide's environment table](deployment.md#environment-variables).

3. Start PostgreSQL. Two options, both driven by [`docker-compose.yml`](../docker-compose.yml) (service names: `postgres`, `redis`, `wizzrobe`):

   **Database only** — app runs on your host (the usual dev loop):

   ```bash
   docker-compose up -d postgres
   ```

   > **Note:** as written, the `postgres` service does not publish a port to the host. To reach it from a host-run app, add a `ports: ["5432:5432"]` mapping to the `postgres` service (or point `DB_HOST`/`DB_PORT` at any other PostgreSQL you have running), then set `DB_HOST=localhost` and `DB_PORT=5432` in `.env`.

   **Full stack** — app and database both in containers:

   ```bash
   docker-compose up
   ```

   This builds the image, waits for the postgres and redis healthchecks, injects `DB_HOST=postgres` / `DB_PORT=5432` and `NICEGUI_REDIS_URL=redis://redis:6379/0` into the app container, and serves on port 8000. Data persists in the `postgres_data` and `redis_data` volumes.

4. Start the app on your host:

   ```bash
   ./start.sh mock   # or: ./start.sh dev
   ```

   [`start.sh`](../start.sh) changes to the repo root and sources `.env` with `set -a` (values with spaces survive), then runs uvicorn on port 8000. `dev` binds to localhost with `--reload`; `mock` adds the offline flags (see below); `validate` is `mock` with no reload at all, for browser runs; `stop` stops whichever of them is running; `prod` is the production variant. Full mode table: [deployment.md](deployment.md#startsh-modes).

   **Always start through `start.sh`.** It is the only place `.env` is sourced, so a hand-rolled `poetry run uvicorn main:app` gets none of it and dies validating a Discord token instead of falling back to `MOCK_DISCORD` — which is a `.env` setting, not a default. See [Running the server](#running-the-server-and-the-two-ways-it-bites) for that and the other two traps.

5. **First boot:** the FastAPI lifespan in [`main.py`](../main.py) runs Aerich `upgrade()` before initializing Tortoise, so all pending migrations apply automatically and the schema is created on an empty database. No manual `aerich upgrade` is needed just to boot.

6. Open `http://localhost:8000`. API docs: `http://localhost:8000/api/docs` (Swagger) and `/api/redoc`.

After first-time setup, the daily loop is just:

```bash
docker-compose up -d postgres   # if not already running
./start.sh mock                 # app with auto-reload on :8000, no external credentials
poetry run pytest               # before pushing
```

## Running the server, and the two ways it bites

Three traps, all of which have already cost a session real time. They are
written down because none of them announces itself — each looks like a bug in
the change you were making.

### Stopping it: never `pkill -f`

```bash
./start.sh stop     # not: pkill -f "uvicorn main:app"
```

`pkill -f` matches **full command lines**, and the shell running your `pkill`
has the whole command — pattern included — in its own. So it kills itself
(exit 143/144, no output at all) and the server keeps running. The pattern is
always present, because it *is* the argument, which makes this a certainty
rather than a risk. Quoting does not help. Neither does `uvi[c]orn`: the
character class still matches the literal text elsewhere in the same command,
and a heredoc that merely *mentions* the string is enough.

`./start.sh stop` keeps the pattern inside the script, where the caller's
command line cannot match it. `enforce_safe_commands.py` blocks the `pkill -f`
form and points here; `pgrep -f` on its own stays allowed, since looking is
harmless until you pipe it into `kill`.

### Validating in a browser: use `validate`, not `dev`

```bash
./start.sh validate   # mocked services, no --reload
```

`dev` and `mock` run with `--reload`, and the app writes into its own directory:
`.nicegui/` gets a `storage-user-<id>.json` **per session**, so every login
restarts the server. `start.sh` now excludes `.nicegui/`, `*.log` and
`.pytest_cache/` from the watcher, which stops the storm — but a reload mid-run
still tears down every open page, so a screenshot can catch a half-built DOM and
the log fills with teardown tracebacks that read like findings. For
[`/ui-validation`](#claude-code-hooks), the flag sweep, or any Playwright run,
not watching at all is the only way to be sure the log slice you are reading is
about your change. Restart it by hand after an edit.

### Reading the log: know what is already noisy

A `RuntimeError: The parent slot of the element has been deleted` from
`nicegui/timer.py` is **not** a finding. `Timer._run_in_loop` sleeps for its
interval, waits for the client, and only then enters `self.parent_slot` — while
its own `_should_stop` (which does check `is_deleted`) is evaluated inside that
context. Closing a board carrying the 5s roll-label tick before its socket
settles lands in that gap. Our timers use `theme.timer_teardown.PageTimer`, which
re-checks deletion after the wait, and `application/utils/timer_teardown.py`
filters any remaining instance out of the app's exception handler, the `nicegui`
logger and Sentry, so it should not reappear; if
something like it does, **check `main` before believing it is yours** — a stash,
a restart and a re-run is cheaper than the wrong diagnosis.

## Local development without Discord (MOCK_DISCORD)

The recommended dev loop — `./start.sh mock` sets `MOCK_DISCORD`, `MOCK_SEEDGEN`, `MOCK_CHALLONGE`, `MOCK_TWITCH`, `MOCK_RACETIME`, `MOCK_MATCHERINO`, `MOCK_STARTGG` and `ENVIRONMENT=development` in one command (`MOCK_MATCHERINO` serves canned venue badge sales to [event check-in](features/event-check-in.md), so the desk's Sync works offline; refused in production). With `MOCK_DISCORD`:

- `/login` renders a **user picker** instead of redirecting to Discord OAuth — pick any user in the database or use "Create test user". Picked and created users are real rows that persist across restarts.
- All `DiscordService` calls (DMs, guild member lookups, …) are stubbed no-ops, and the **bot does not start**, so `DISCORD_TOKEN` is not required.
- Discord *button* interactions (acknowledgment, crew signup, watch) need a live bot connection and cannot be exercised. Mock mode is a full auth bypass, so the app refuses to start while `ENVIRONMENT=production`.

Details: [features/discord.md](features/discord.md#mock-mode). Auth internals: [reference/authentication.md](reference/authentication.md).

## Account linking without provider credentials

Profile → **Connected accounts** hides any provider whose integration is not configured, so with no credentials the card does not render at all. Three mock switches make it appear and make the links complete:

| Switch | Makes appear | Canned identities (`MOCK_<PROVIDER>_IDENTITY`, `1`–`4`, default `1`) |
|---|---|---|
| `MOCK_CHALLONGE` | the Challonge row (also gated on `FeatureFlag.CHALLONGE`) plus the staff service connection on `/admin/settings/integrations` | `1001`…`1004` / `mockone`…`mockfour` |
| `MOCK_TWITCH` | the Twitch row | `20001`…`20004` / `MockTwitchOne`…`Four` |
| `MOCK_RACETIME` | the racetime.gg row | `mockrt0001`…`mockrt0004` / `MockRacerOne`…`Four` |
| `MOCK_STARTGG` | the start.gg row (also gated on `FeatureFlag.STARTGG`); linking any event link loads one canned event | `30001`…`30002` / `MockGGOne`…`MockGGTwo` |

`MOCK_<PROVIDER>_IDENTITY` is read per request, so changing it in the environment and re-linking binds a different identity — that is how a collision between two users is produced. `MOCK_RACETIME` also gates the racetime *bot* runtime, but that is separately gated by `RACETIME_BOT_ENABLED` (off by default), so turning the mock on starts no bot.

**Mock mode short-circuits the OAuth round trip entirely** — `is_mock()` is checked in the `/<provider>/link` page and the link is recorded on the spot, so the provider callback never runs. That covers the happy path, the section states and unlinking. The failure paths are reached by opening the callback routes directly:

| Failure | How to reach it |
|---|---|
| Consent denied / codeless callback | `/t/default/racetime/oauth/callback` with no query string, signed in |
| State mismatch | the same route with `?code=x&state=wrong` |
| Already linked to someone else | link two users at the same `MOCK_*_IDENTITY` — e.g. `player_three` then `player_four` under the default `MOCK_RACETIME_IDENTITY=1` |
| Wrong door (Challonge's one callback serves two flows) | `/t/default/challonge/oauth/callback` as a non-admin |
| Stale / replayed cross-host claim | `/oauth/link/claim?token=nope` |

Seeded starting states: `player_one` is linked to all three providers, `player_two` to racetime and Challonge, and **`player_three` / `player_four` are linked to nothing** — the fixtures every probe above starts from (`seed_dev.py` asserts they stay that way).

`setup_env.sh` only writes `.env` when one is absent, so an **existing** dev environment needs `MOCK_CHALLONGE=true`, `MOCK_TWITCH=true` and `MOCK_RACETIME=true` added by hand — or just use `./start.sh mock`, which exports them.

### Driving the cross-host link handoff

On a custom domain the provider callback lands on the platform host, whose session cannot see the domain's cookie. With `HOST_OAUTH_MODE=handoff` the OAuth runs on the platform host and the verified identity is handed back to the domain's `/oauth/link/claim` through a single-use, host-bound, browser-bound token. It needs two hostnames, and the seeded **`second`** tenant already carries `domain=second.localhost:8000`:

```bash
PLATFORM_HOST=platform.localhost:8000 HOST_OAUTH_MODE=handoff ./start.sh mock
```

Drive `http://second.localhost:8000/home/profile` (a custom domain serves its community at the root — no `/t/<slug>` prefix) and click **Link**. Under a provider mock the start leg redirects to its own callback with `?code=mock`, so the whole handoff — state match, exchange, mint, hand-back, browser binding, claim — runs for real against a mock exchange.

Two things to check first when it dead-ends:

- **Use `*.localhost` hostnames, not a wildcard-DNS domain.** `scheme_for_host` returns `http` only for `localhost`, `127.0.0.1` and `*.localhost`; anything else gets `https`, so the cross-host redirect fails TLS against a plain uvicorn. Browsers resolve `*.localhost` to loopback themselves, so no `/etc/hosts` edit is needed — but tools that go through the system resolver (`curl`, `getent`) may not.
- **The start leg allow-lists the target host against *active* tenant domains** and silently redirects to `/` when it does not match. Confirm the tenant's `domain` is exactly the host you are driving, port included.

## Dev data

[`scripts/seed_dev.py`](../scripts/seed_dev.py) populates a freshly migrated database with fixtures. The schema must already exist (boot the app once, or run `poetry run aerich upgrade`), then:

```bash
poetry run python scripts/seed_dev.py
```

The script loads `.env` itself and connects using the same Tortoise config as the app. Because the app is multitenant, it seeds **two fully-provisioned tenants** so leak tests and manual dev checks have cross-tenant data from day one, plus a third that is deliberately half-set-up:

- **Tenant A** reuses the migration's `default` slug (on a fresh DB the additive backfill creates it empty and the script adopts it); **Tenant B** is a second community. Each gets a different feature tier (A → Full Access with one feature switched off; B → Online Tournaments with one force-granted exception) so the [feature-flag](features/feature-flags.md) states are visible.
- **`fledgling`** ("Fledgling Community") is stopped part-way on purpose: `staff_user` holds STAFF and a membership, and there is **nothing else** — no tournament, no enrolment, no stage. Both other tenants pass the setup checklist, so without this one the Setup tab, `/platform`'s `1 of 3` readiness column and the disabled Tournament select would all be invisible in dev. Log in there to see the unready state; log in to `default` to confirm it disappears.
- **Global (tenant-agnostic):** users, the feature-flag groups (Default / Online Tournaments / Full Access), the racetime bots, saved table layouts (`seed_preferences.py`), and an MCP OAuth client with a read-only and a write dev bearer. Exactly one user — **`super_admin`** ("Platform Owner") — holds the global `SUPER_ADMIN` role and **no tenant role anywhere**, the fixture for `/platform` and for a platform admin acting inside a community they have no grants in. **Every per-tenant role has a holder who holds that role and nothing else** — `equip_manager`, `vc_user`, `preset_mgr`, `sync_user`, `qual_admin`, `proctor_only`, `sm_only`, `triforce_sub`, `volunteer_only`, plus `cc_user` as a crew coordinator with no role row at all (crew coordination is a per-tournament relation). `staff_user` holds `STAFF` **alone** for the same reason: it satisfies every predicate in the admin area, so a surface that gates on staff-ness where it means to gate on a capability looks correct until the delegate it was written for logs in. Drive admin checks as them, not only as staff — the rule is enforced in `tests/test_seed_coverage.py`. `local_only` holds a role in `default` and nothing in `second` (the fixture for per-community people scoping), and eight `racer_*` accounts exist only to fill a ten-entrant play-in start list, two of which also volunteer. Fixture discord ids are **derived from the username** (`scripts/seed_support.py`), never typed.
- **Per tenant (everything else is tenant-scoped):** stages, system config (every key `SystemConfigService` reads, including the venue hours, the volunteer reminder lead and the station-label format), four tournaments (below) with their matches + crew, volunteers and shifts, player availability, equipment, an API token (the printed **dev bearer** the `api-validation` skill uses), feedback, triforce texts, Discord role mappings, webhooks, and seeded audit-log + telemetry rows. Per-domain fixtures live in sibling modules called from inside the same `tenant_scope` — `seed_access.py`, `seed_venue.py`, `seed_matches.py`, `seed_match_day.py`, `seed_play_in.py`, `seed_crew.py`, `seed_volunteers.py`, `seed_equipment.py`, `seed_tokens.py`, `seed_discord.py`, `seed_observability.py`, `seed_brackets.py`, `seed_online.py`, `seed_qualifiers.py`, `seed_onsite.py`, `seed_challonge.py`, `seed_payouts.py`, with `seed_support.py` holding the fixture registry and the shared `backfill` helper — which is also how `seed_dev.py` stays under the 800-line budget.

**The rules these fixtures follow — naming, the coverage bar, derived ids, convergence, states-vs-volume — are written down in [reference/dev-seed.md](reference/dev-seed.md) and enforced in `tests/test_seed_coverage.py`.** Read that before adding a fixture.

### The fixture tournaments

`Tournament.is_racetime_enabled` (a racetime bot attached) hides every on-site proctor control — check-in, station assignment, start, finish — and the Proctor Station board drops those matches entirely (`exclude_racetime`). So the seed keeps the racetime fixtures and the proctor-lifecycle fixtures in **separate** tournaments per tenant:

| Tournament | Racetime bot | What it is for |
|---|---|---|
| **Wizzrobe Dev Tournament** (`seed_matches.py`) | none (on-premises) | The general-purpose fixture: matches across every lifecycle state (scheduled / checked-in / in-progress / finished / disputed / TBD / **untitled**) plus their crew, acknowledgments, watchers, generated seeds, notification preferences (one per level), triforce texts, Challonge mirror and native brackets. Also holds the two **group play-in races** (`seed_play_in.py`) — ten racers, eight placings and two forfeits in one match, the only rosters longer than two — because a play-in seeds this tournament's bracket. `seed_match_day.py` fills day one of its event window across the three stages. |
| **Wizzrobe Online Series** (`seed_online.py`) | ALTTPR Dev Bot | Everything racetime: the bot + auto-open config, a preset and room profile, `require_racetime_link` on, its own scheduled / in-progress / finished races, one `RacetimeRoom` per room state, the SpeedGaming event link + synced episode, and the Discord Events mirror. |
| **Wizzrobe Cup** (`seed_onsite.py`) | none (on-premises) | A second venue event with its own per-tournament "tournament days" override and one match per step of the proctor's workflow, ending in a result awaiting review and a finished match with no winner recorded. |
| **Wizzrobe Cup — Last Season** (`seed_onsite.py`) | none (on-premises) | An archived season: `is_active=False`, its roster and one confirmed grand final kept. The only inactive tournament, so the `is_active=True` filters (profile lists, notification preferences, admin selectors) have something to exclude. |

All of them carry the operational metadata a real tournament has — format, rules/bracket links, and `average_match_duration` / `max_match_duration`, which the schedule's suggestion engine and the reports' expected-average column read (both fall back to a hard-coded 90 minutes when NULL, so an unconfigured fixture cannot show the difference).

Every scoped create threads `tenant` through an explicit `tenant_scope`, mirroring production.

**Idempotency:** everything uses `get_or_create`, so re-running leaves existing records unchanged. The exceptions are fixtures whose whole point is a *specific* state something else also writes — the demo feature-flag groups and the two per-tenant overrides (a migration seeds an older version of those), and the race rooms / SpeedGaming link / mirrored Discord event, which `update_or_create` re-points onto the online tournament in a database seeded before the tournaments were split. Match timestamps are therefore *not* refreshed on re-run (matches match by title + tournament); delete the fixture matches or reset the database to regenerate relative scheduled times.

## Database migrations

```bash
poetry run aerich migrate    # generate a migration from model changes
poetry run aerich upgrade    # apply pending migrations
```

- Models live in the `models/` package (per-domain submodules, re-exported from `models/__init__.py`); migration files live in `migrations/models/`.
- Aerich is configured in the `[tool.aerich]` section of [`pyproject.toml`](../pyproject.toml), pointing at [`migrations/tortoise_config.py`](../migrations/tortoise_config.py).
- **Auto-upgrade caveat:** booting the app applies all pending migrations to whatever database `.env` points at (`main.py:init_db()`). Keep `.env` pointed at a dev database while working on schema changes — and note that anyone who pulls your branch applies your migration on their next boot.

## Running tests

```bash
poetry run pytest                                        # whole suite (parallel)
poetry run pytest tests/services/test_match_service.py   # one suite
poetry run pytest -k acknowledg                          # by keyword
poetry run pytest -n0 tests/services/test_match_service.py   # serial, for -s / pdb
```

Pytest is configured in [`pyproject.toml`](../pyproject.toml) with `asyncio_mode = "auto"` (async test functions need no `@pytest.mark.asyncio`) and `testpaths = ["tests"]`. No PostgreSQL or Discord connection is required.

**The suite runs in parallel by default** — `addopts = "-n auto --dist loadfile"` (pytest-xdist). `loadfile` keeps every test in a module on one worker, so module-level state stays as contained as it is in a serial run. Pass `-n0` to go serial when you need `-s`, a debugger, or readable live output from a single file.

### The `db` fixture stamps the tenant for you — and what that hides

Almost every scoped model in the suite is created bare (`Match.create(...)`), so
the `db` fixture in [`tests/conftest.py`](../tests/conftest.py) wraps each
tenant-scoped model's `.create` to stamp the ambient tenant when the caller
omits it. That keeps ~700 call sites free of per-call edits, and the production
contract — **never auto-stamp** — is untouched, because the wrapper exists only
in the harness.

The cost: **no DB-backed test can fail on an unstamped write.** A production
write that forgets `tenant_id` on a non-null FK raises `IntegrityError` against
Postgres and passes silently here. That is exactly how the enrolment write in
`UserService` shipped broken. Two things guard it now:

- `check_tenant_scoping.py` reads service modules as well as repositories
  (writes only) — see [`.claude/README.md`](../.claude/README.md).
- [`tests/tenancy/test_enrolment_tenant_stamping.py`](../tests/tenancy/test_enrolment_tenant_stamping.py)
  has an `unstamped_creates` fixture that restores Tortoise's own `Model.create`
  for one model, so the write is tested as production runs it. Copy that pattern
  when a write path's tenant stamping is the thing under test.

### Keeping it fast

Wall time is dominated by **per-test fixture setup**, not by assertions — test *count* is close to free, so do not thin out parametrized cases to save time. Two rules:

- **Never make an expensive, immutable object a function-scoped fixture.** `build_api_app()` is cached with `functools.cache` in [`tests/api_helpers.py`](../tests/api_helpers.py); use the shared `app` fixture from `tests/conftest.py` rather than re-pasting a local one.
- **Ask whether a test needs the `db` fixture at all.** Pure-function logic — bracket engines, timezone helpers, schema validation — should be tested without it.

Both are enforced mechanically, and the guard's error message explains the case it caught: [`tests/test_fixture_performance.py`](../tests/test_fixture_performance.py) (structural, never timing-based, runs in CI) and [`.claude/scripts/check_fixture_cost.py`](../.claude/scripts/check_fixture_cost.py) (same shapes, blocked at write time).

Five things are built once per worker process rather than per test, each of them the most expensive item in the run at the time it was found:

| Built once | Was costing | Where |
|---|---|---|
| The API app — `include_router` resolves every route's dependency graph and builds a Pydantic response model per endpoint | ~200ms × ~400 API tests | `build_api_app()`, `tests/api_helpers.py` |
| The `CREATE TABLE` script, rendered from model metadata | ~18ms × every DB-backed setup | `_schema_sql()`, `tests/conftest.py` |
| **The engine.** Most of `Tortoise.init()` is `_build_initial_querysets()` rebuilding a filter map for all ~70 models | ~15ms × ~1650 DB tests — 60s of a 190s profiled run | `_bootstrap_engine()`, `tests/conftest.py` |
| **The MCP tool catalogue.** `build_server()` derives a JSON schema per tool | ~230ms × ~106 sessions | `_catalogue()`, `tests/mcp/conftest.py` |
| **The dev seed.** Eight tests want `seed_all()`'s result, not its execution | ~1.5s × 8 | the `seeded_db` fixture, `tests/conftest.py` |

The database itself is the interesting one. A test still needs an *empty* database, and re-creating the schema is most of what the old fixture paid for. So the pristine state — schema plus the default tenant plus its feature flags — is snapshotted into a detached in-memory `sqlite3` connection, and each test restores it through SQLite's own backup API: one C-level page copy, ~1ms against ~31ms to re-init Tortoise and replay the DDL. The snapshot carries `sqlite_sequence` with it, so autoincrement ids restart at 1 exactly as they did with a brand-new database. `seeded_db` is the same mechanism with a second snapshot.

Two consequences worth knowing:

- **The connection outlives the test.** The `db` fixture pulls it back out of Tortoise's registry on teardown, so a test that forgot to request `db` still fails the way it always did — against a connection to an empty database that has never seen the schema, not against the previous test's leftovers. The stray connection that opens is closed on the next `db` setup; leave four of them behind and the interpreter cannot exit, because aiosqlite's worker thread is not a daemon.
- **SQLite runs on the event loop.** aiosqlite hands every statement to a background thread and waits on a future, which costs a queue put, a thread wake-up and a selector round trip on top of the query. Right for a server, wrong for an in-memory database where every statement returns in microseconds: skipping the hop halves the cost of every query in the suite. `tests/conftest.py` patches it out; the PostgreSQL path is untouched.

Layout:

| Path | Covers |
|---|---|
| `tests/api/` | REST API suites, one module per router family, plus cross-router read/write and error-path suites. Each mounts the API router on a minimal FastAPI app and calls it through an httpx `ASGITransport` client; `tests/api_helpers.py` holds the shared token/client setup |
| `tests/mcp/` | MCP server: tool catalogue, its OAuth authorization server, and consent |
| `tests/services/` | One suite per service, plus DB-backed gap-fill suites that exercise the async DB methods and error/notification branches the pure-function suites skip |
| `tests/tenancy/` | Tenant context, middleware, URLs and session, plus every `*isolation*.py` leak test and the `test_leak_test_coverage.py` ratchet that keeps that set complete |
| `tests/theme/` | Presentation logic that is pure Python (bracket layout maths, board access, slot-template strings, dialog copy) rather than rendered Vue/Quasar |
| `tests/pages/` | Page-module logic testable without a browser (MCP consent) |
| `tests/postgres/` | Tests that only mean something on PostgreSQL — row locking, the migration advisory lock; they skip on SQLite (see [below](#running-the-suite-against-postgresql)) |
| `tests/utils/` | `application/utils/` helpers (migration lock, seed provider) |
| `tests/*.py` | Cross-cutting utility suites: timezone, CSV export, repositories, infra plumbing, error handlers, rate limiting, security hardening, schema bounds, seed coverage, … |

Fixtures from the conftests:

- [`tests/conftest.py`](../tests/conftest.py) — a function-scoped `db` fixture backed by **in-memory SQLite**. Tortoise is initialised once per worker; each test then restores a pristine template database (schema, the default tenant, its feature flags) through SQLite's backup API, so it starts with no leakage from prior tests. `seeded_db` layers a second template holding everything `scripts/seed_dev.py` creates. (SQLite catches logic errors but not PostgreSQL-specific query behavior.) See [Keeping it fast](#keeping-it-fast) for why it works this way.
- An autouse `stub_discord_queue` fixture (suite-wide, in `tests/conftest.py`) that monkeypatches `discord_queue.enqueue` to capture (and later close) enqueued coroutines, so tests can assert that notifications were sent without a bot connection or "never awaited" warnings.

### Known gaps

Still not covered (intentionally — each needs live infra the SQLite suite can't provide):

- Discord bot interaction handlers (`discordbot/`) — require a live Discord connection.
- NiceGUI UI rendering (`pages/`, `theme/`) — no headless browser tests in pytest. The CI `browser` job (below) walks a fixed set of surfaces; the `ui-validation` skill drives any of them locally. That skill's `scripts/ui_flag_sweep.sh` also covers the rendering gap the flag system leaves: an ungated page or tab that calls a feature-gated service and dies for a community without the feature.
- OAuth flow (`middleware/auth.py`) — requires live Discord OAuth.
- The network-backed clients — `application/utils/clients/challonge_client.py`, `twitch_client.py`, and the HTTP randomizer paths in `seedgen_service.py` (`_generate_alttpr`/`_generate_smmap`/`_generate_ootr`) — hit real external APIs; only their local/mocked paths are covered.
- Most of `discord_service.py` — the parts that talk to a live bot connection.
- `application/utils/sentry.py` — instrumentation wiring, run only at process start.

**Coverage tooling:** `pytest-cov` is a dev dependency. Generate a report with:

```bash
poetry run pytest --cov=application --cov=api --cov=middleware --cov-report=term-missing
```

(`UserRepository.update_discord_info` used to accept non-field
`discriminator`/`avatar` arguments and silently drop them on save. It now takes
`username` and `avatar` — the two Discord-sourced fields the row stores — and
`tests/test_user_repositories_coverage.py` pins that a `discriminator` is still
rejected and that the avatar hash actually lands.)

## Continuous integration

[`.github/workflows/test.yml`](../.github/workflows/test.yml) runs these jobs, all on `ubuntu-latest` / Python 3.12. The wait is the slowest job, not the sum, so the browser work is split across two jobs with one app each:

| Job | What it runs |
|---|---|
| `lint` | `ruff check .` (**blocking**) then `scripts/mypy_ratchet.py` (blocking, but only on a file that *gains* errors — see below) |
| `guardrails` | `scripts/guardrails.py --changed` — the `.claude/scripts/` checks against the files this diff touches |
| `pytest` | `pytest -q` with coverage over `application`, `api`, `middleware` |
| `changes` | decides which subsystem-only jobs a PR needs from its changed paths. Anything that isn't a pull request (the `publish.yml` gate, a manual run) runs every job, and so does a PR whose diff errors or comes back empty: a skipped job reports as passed, so the job fails open |
| `swiss-crossvalidation` | builds bbpPairings (pinned to a commit) and runs `tests/services/test_bracket_swiss_crossvalidation.py` against it; the suite skips itself unless `BBPPAIRINGS_BIN` points at the executable, so it is opt-in locally. On a PR it runs only when `application/services/bracket_engines/`, the test, `tests/services/_trf.py`, `poetry.lock` or `test.yml` changes; otherwise it reports as skipped, which a required check counts as passed |
| `postgres` | re-runs `tests/postgres` and `tests/tenancy` against a real PostgreSQL 16 service. On a PR it skips when every changed path is UI, help or event-info content, docs, change notes or UI tests (`pages/`, `theme/`, `static/`, `application/help/`, `tests/theme/`…); `pytest` still runs the same tests on SQLite |
| `browser` | boots the real app against PostgreSQL with every integration mocked (that boot applies the full migration chain to an empty database, which is CI's migration check), seeds it, and walks the surfaces in [`scripts/ui_smoke_ci.json`](../scripts/ui_smoke_ci.json) in headless Chromium, then runs [`scripts/ui_leak_check.js`](../scripts/ui_leak_check.js) (opens and closes every page in `ui_a11y_ci.json` three times and fails if dead pages pile up in server memory, printing what holds them: [observability.md](features/observability.md#finding-a-leak-memory_census)) and [`scripts/ui_layout_shift.js`](../scripts/ui_layout_shift.js) ([style-guide.md](style-guide.md#hold-still)). The only job that executes `pages/` and `theme/` at all — the coverage figure above covers neither, and their Quasar slot templates run client-side where Python cannot reach them. The shared setup lives in [`.github/actions/boot-app`](../.github/actions/boot-app/action.yml). Screenshots upload as an artifact on pass or fail |
| `browser-a11y` | the same boot on its own app, then [`scripts/ui_a11y_scan.js`](../scripts/ui_a11y_scan.js) over [`scripts/ui_a11y_ci.json`](../scripts/ui_a11y_ci.json): axe's WCAG 2.1 AA rules and a tint-aware contrast check on each page, in light and dark ([style-guide.md](style-guide.md#checking-your-change)). On a PR it runs only when the UI layers or what they render change: `pages/`, `theme/`, `static/`, `application/{help,content,about,event_info}/`, `scripts/ui_*`, `scripts/seed_dev.py`, `main.py`, `middleware/`, the lockfile or `.github/`. A backend-only PR skips it; the `browser` smoke walk still renders every page |

Pull requests trigger it directly. Pushes to `main` and `v*` tags reach it
through `workflow_call` from [`publish.yml`](../.github/workflows/publish.yml),
which gates the container build on the whole suite — so what guards a release is
the same thing that guarded the PR, and a `main` push runs it once rather than
twice. Image publishing itself is described in [deployment.md](deployment.md).

CI runs Chromium only. WebKit (Safari's engine) and Gecko (Firefox's) were
dropped because they slowed every PR down; run them by hand when chasing a
Safari- or Firefox-specific problem, against a booted and seeded app:

```bash
npx playwright install --with-deps webkit firefox
ENGINE=webkit  node scripts/ui_engine_smoke.js scripts/ui_a11y_ci.json          # every a11y page, iPhone 13 + desktop Safari
ENGINE=firefox node scripts/ui_engine_smoke.js scripts/ui_a11y_ci.json          # every a11y page, Android-sized phone + desktop Firefox
HISTORY_BROWSERS=webkit,firefox node scripts/ui_history.js scripts/ui_history_ci.json  # Back/Forward/reload/deep links
```

Three more workflows run on their own schedule: `codeql.yml` (CodeQL code scanning for Python, JavaScript and the workflows themselves, on every push to `main` and weekly; not on PRs, so it doesn't take runner slots from PR checks, and a newer push to `main` cancels an older analysis), `security.yml` (pip-audit over the
runtime dependencies, on every push to `main`, weekly, and on a PR that changes `poetry.lock`, `pyproject.toml` or the workflow; any other PR reports the job green without auditing) and `guardrail-sweep.yml` (the
whole-tree guardrail run, weekly).

## Conventions & adding a feature

[../CLAUDE.md](../CLAUDE.md) is canonical for coding conventions and carries the step-by-step checklist for a new feature (feature-flag decision → model + migration → repository → service → exports → UI → dev seed). [refactoring-guide.md](refactoring-guide.md) shows the three-layer pattern with code examples. Read both before writing code.

Two steps that land after that checklist:

- Add tests under `tests/services/` (and `tests/api/` or `tests/` for API and utility code).
- Write or extend a feature doc in `docs/features/` and link it from the [documentation index](README.md).

## Claude Code hooks

`.claude/settings.json` wires two sets of hooks:

| Where | What |
|---|---|
| `.claude/hooks/` | Shell hooks for documentation discipline — `install-deps.sh` and `session-start.sh` (`SessionStart`: install deps, audit source dirs against their reference docs), `doc-reminder.sh` (`PostToolUse` Write/Edit: names the reference doc a changed source file needs), `doc-check.sh` (`Stop`: diffs `git HEAD` into a documentation checklist) |
| `.claude/scripts/` | Python guardrails run on `PreToolUse`/`PostToolUse` — architecture, async and datetime safety, migration safety, tenant scoping, audit/event conventions, feature-flag gating, fixture cost, table grids, and more |

Every hook sources `_repo.sh` for `$REPO` rather than deriving it from the working directory, because hooks inherit the session's shell cwd, which moves whenever a Bash call `cd`s elsewhere. The executable bit lives in the git index, so a normal checkout preserves it. Full contract, the complete inventory, and the test that enforces both: [`.claude/README.md`](../.claude/README.md).

### Running the suite against PostgreSQL

`poetry run pytest` uses in-memory SQLite: no service to start, a fresh schema
per test, and the whole suite in under a minute. Production is PostgreSQL, and
some of what it relies on is a **silent no-op** on SQLite — `SELECT … FOR UPDATE`
above all. `AsyncQualifierRepository.lock_user_for_draw` exists so two
simultaneous draw clicks cannot both open a run; on SQLite that lock does
nothing, so no SQLite test can tell whether it is there.

Point the same suite at a real database with `WIZZROBE_TEST_DB_URL`:

```bash
WIZZROBE_TEST_DB_URL=postgres://wizzrobe:wizzrobe@localhost:5432/wizzrobe_test \
    poetry run pytest tests/postgres tests/tenancy -q -n0
```

`-n0` is required: each test drops and recreates the schema, so xdist workers
would pull the tables out from under one another. `tests/postgres/` holds the
tests that *only* mean something here (they skip on SQLite); everything else
runs on both.

The `postgres` CI job runs exactly that pair on every PR — the row-locking tests
plus tenant isolation, which is where real constraint enforcement matters. Point
the variable at anything else locally to widen it. Adopting this surfaced one
harness bug real Postgres catches and SQLite hides: creating the default tenant
with an explicit `id=1` leaves the `SERIAL` sequence at 1, so the next
auto-assigned tenant collides. The `db` fixture now advances the sequence.

### Mypy is a ratchet, not a gate

`poetry run python scripts/mypy_ratchet.py` is blocking in CI, but it compares
against `scripts/mypy_baseline.json` (per-file error counts) and fails only when
a file *gains* errors. The tree carries ~1420, the large majority Tortoise
reverse relations and FK `*_id` attributes that mypy structurally cannot resolve
on a dynamically-built ORM model — not defects to fix. New errors still cannot
land.

Fix errors freely; the run tells you when the baseline can shrink, and
`--update` re-records it. Silence a genuine ORM false positive at the line with
a targeted `# type: ignore[code]` rather than re-recording a higher count.

### The same guardrails in CI

As hooks, those checks fire on a Write/Edit *inside a Claude session* and nowhere
else — a hand-written commit, a merge, or an edit from any other tool passes all
of them. `scripts/guardrails.py` replays the same scripts against files on disk,
so CI enforces them for everyone. It reimplements nothing: it synthesizes the
hook payload each check already parses and reports its exit status.

```bash
python scripts/guardrails.py --changed origin/main   # what CI runs on a PR
python scripts/guardrails.py --all                   # whole tree (~5 min)
python scripts/guardrails.py pages/admin.py          # one file
```

Two jobs use it: `guardrails` in `test.yml` (per PR, changed files only, and
blocking) and `guardrail-sweep.yml` (weekly, whole tree, non-blocking on merges).
The sweep exists because a changed-files check cannot see a violation that
entered by a route no diff covers — a rule tightened after the code was written,
or a check added later.

`scripts/guardrail_baseline.json` records accepted pre-existing hits as a
per-(check, file) count; a run fails only when a count *grows*. Deleting an entry
is always safe, so the baseline shrinks on its own and only grows when somebody
deliberately runs `--all --update-baseline`.

Five scripts are deliberately excluded (`EXCLUDED_CHECKS`): `run_full_tests` and
`run_related_tests` are test runners rather than checks, `enforce_safe_commands`
guards Bash tool calls, and `check_migration_drift` and `check_seed_coverage`
read the working-tree diff — which is empty in CI, where the `browser` job's app boot
proves the chain applies and `tests/test_seed_coverage.py` runs the real seed
instead.
Everything else in `.claude/scripts/` runs here.

## Repository hygiene notes

- `poetry.lock` is committed; keep it in sync with `pyproject.toml` when changing dependencies (the CI cache is keyed on its hash).
- `.gitignore` covers `.env*` (with an exception for `.env.example`), so local secrets stay out of version control. It also ignores `.nicegui/`, `test_data/`, and the usual Python build/cache artifacts.
