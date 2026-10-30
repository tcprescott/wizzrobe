#!/usr/bin/env python3
"""Main entry point for the Wizzrobe FastAPI application.

Initializes the database, sets up API and frontend routes, and manages application lifespan.
"""

import time

# Taken before the heavy imports below so the lifespan can report how long
# importing the app took. A fresh container has no bytecode cache, so this phase
# alone can run several seconds and is otherwise invisible in the log.
_IMPORT_STARTED = time.perf_counter()

import asyncio
import logging
import os
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Iterator, Optional

from aerich import Command
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from tortoise import Tortoise

import api
import frontend

# Safe to import at module scope: mcpserver builds its MCPServer instance inside
# mount(), not at import time. MCPServer.__init__ calls logging.basicConfig, which
# would otherwise win the race against the basicConfig below and strip the log
# format for the whole process.
import mcpserver
from api import oauth_server
from api.openapi_docs import api_description, warm_openapi
from api.openapi_docs import install as install_openapi_docs
from application.events import dispatch_queue as event_dispatch_queue
from application.events import event_bus, webhook_queue
from application.services import PersonalWebhookService, TelemetryService, WebhookService, web_push_service
from application.services.discord import discord_queue
from application.services.discord.discord_service import get_discord_bot
from application.services.match import stage_reminder
from application.services.personal_webhook_service import SUBSCRIBABLE_EVENT_TYPES
from application.services.volunteer import volunteer_reminder
from application.utils import element_cycles, gc_monitor, memory_census, metrics_sampler, worker_watchdog
from application.utils.easter_eggs import random_fact
from application.utils.environment import env_flag, loop_watchdog_enabled
from application.utils.http_headers import header_safe
from application.utils.log_levels import default_root_level, register_trace_level
from application.utils.loop_watchdog import watchdog as loop_watchdog
from application.utils.migration_lock import migration_lock
from application.utils.mocks.mock_discord import is_mock_discord
from application.utils.sentry import flush_sentry, init_sentry
from middleware.app_cors import AppCorsMiddleware
from middleware.inflight_requests import InFlightRequestLogMiddleware
from middleware.request_metrics import RequestMetricsMiddleware
from middleware.security_headers import SecurityHeadersMiddleware
from migrations.tortoise_config import DB_URL, TORTOISE_ORM

# Configure application logging once, at import of the entrypoint. Without this
# app loggers fall through to Python's lastResort handler: INFO is dropped and
# WARNING+ has no timestamps. basicConfig is a no-op if handlers already exist.
register_trace_level()
logging.basicConfig(
    level=default_root_level(),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('wizzrobe.main')

# Reference to the Discord bot's background task, kept so it is not garbage
# collected and so shutdown can cancel it cleanly.
_bot_task: Optional[asyncio.Task] = None


@contextmanager
def _startup_step(name: str) -> Iterator[None]:
    """Log how long one boot step took, so a slow restart names its culprit.

    The "starting" line matters as much as the duration: a step that hangs
    never reaches its "took" line, and the last "starting" is the one stuck.
    """
    logger.info('startup: %s starting', name)
    started = time.perf_counter()
    yield
    logger.info('startup: %s took %.2fs', name, time.perf_counter() - started)

async def init_db() -> None:
    """
    Initialize the database using Aerich migrations and Tortoise ORM.

    The upgrade runs under an advisory lock so two overlapping container starts
    cannot apply the same migration chain concurrently — see
    ``application/utils/migration_lock.py``.
    """
    command = Command(tortoise_config=TORTOISE_ORM, app='models', location='./migrations')
    with _startup_step('aerich init'):
        await command.init()
    async with migration_lock(DB_URL) as locked:
        if not locked:
            logger.info('Migration lock skipped: backend is not PostgreSQL.')
        with _startup_step('migration upgrade'):
            applied = await command.upgrade()
        if applied:
            logger.info('Applied %d migration(s), newest %s', len(applied), applied[-1])
    with _startup_step('tortoise init'):
        await Tortoise.init(config=TORTOISE_ORM)

async def close_db() -> None:
    """
    Close all database connections managed by Tortoise ORM.
    """
    await Tortoise.close_connections()

def _log_bot_task_result(task: asyncio.Task) -> None:
    """Surface a bot startup/gateway failure immediately instead of letting it
    sit unobserved in a GC-able task (bad token, intents, gateway crash)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error('Discord bot task exited with an exception', exc_info=exc)


_BOT_RETRY_SECONDS = 30


async def _run_discord_bot(bot, token: str) -> None:
    """Keep trying to bring the bot up until it connects.

    discord.py reconnects on its own once logged in, but ``login()`` runs outside
    that loop: one failed attempt (Discord down while the container restarts) left
    the bot dead, and every DM and button with it, until the next restart.
    """
    import discord
    while True:
        try:
            await bot.start(token)
            return
        except discord.LoginFailure:
            raise
        except Exception:
            logger.warning('Discord bot could not connect; retrying in %ss',
                           _BOT_RETRY_SECONDS, exc_info=True)
        await bot.close()
        bot.clear()
        await asyncio.sleep(_BOT_RETRY_SECONDS)


async def init_discord_bot() -> None:
    """
    Initialize the Discord bot.
    """
    global _bot_task
    if is_mock_discord():
        logger.info('MOCK_DISCORD enabled — skipping Discord bot start.')
        return
    token = os.environ.get('DISCORD_TOKEN')
    bot = get_discord_bot()
    if token:
        _bot_task = asyncio.create_task(_run_discord_bot(bot, token))
        _bot_task.add_done_callback(_log_bot_task_result)
    else:
        logger.warning('DISCORD_TOKEN not set. Discord features will not work.')

async def close_discord_bot() -> None:
    """
    Close the Discord bot connection.
    """
    global _bot_task
    if is_mock_discord():
        return
    bot = get_discord_bot()
    await bot.close()
    if _bot_task is not None:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception('Discord bot task errored during shutdown')
        _bot_task = None

async def _apply_stored_log_levels() -> None:
    """Reapply the log levels a super-admin set on /platform before a restart.

    A diagnostic setting must never stop the app booting, so any failure here
    is reported and boot carries on at the default levels.
    """
    from application.services import LogLevelService
    try:
        await LogLevelService().apply_persisted()
    except Exception:
        logger.exception('Could not apply stored log levels; continuing with defaults')

async def _load_browser_monitoring() -> None:
    """Load the browser Sentry settings a super-admin set on /platform.

    Same rule as the log levels: a failure leaves the defaults in place rather
    than stopping boot.
    """
    from application.services import BrowserMonitoringService
    try:
        await BrowserMonitoringService().load()
    except Exception:
        logger.exception('Could not load browser monitoring settings; continuing with defaults')

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Context manager for FastAPI lifespan events.
    Initializes and tears down the database and Discord bot on application startup and shutdown.
    """
    lifespan_started = time.perf_counter()
    logger.info('startup: module imports took %.2fs', lifespan_started - _IMPORT_STARTED)
    with _startup_step('database'):
        await init_db()
    with _startup_step('stored log levels'):
        await _apply_stored_log_levels()
        await _load_browser_monitoring()
    with _startup_step('discord bot'):
        # Importing discordbot registers its interaction handlers and DM view
        # factories with discord_service's registries.
        import discordbot  # noqa: F401
        await init_discord_bot()
    # Gated by RACETIME_BOT_ENABLED (off by default) and mockable via
    # MOCK_RACETIME; a no-op when the switch is off, so it is always safe to call.
    from racetimebot import close_racetime_bot, init_racetime_bot
    with _startup_step('racetime bot'):
        await init_racetime_bot()
    with _startup_step('background workers'):
        from application.services import race_room_worker
        from application.utils.environment import racetime_bot_enabled
        if racetime_bot_enabled():
            race_room_worker.start()
        from application.services import speedgaming_sync_worker
        from application.utils.environment import speedgaming_sync_enabled
        if speedgaming_sync_enabled():
            speedgaming_sync_worker.start()
        from application.services.discord import discord_event_worker
        from application.utils.environment import discord_events_sync_enabled
        if discord_events_sync_enabled():
            discord_event_worker.start()
        # Always on; a dependency in mock mode reports "mock" and is not contacted.
        from application.services import service_health_worker
        service_health_worker.start()
        # Async-qualifier run expiry: forfeits runs a player drew and abandoned, after
        # one warning. Ungated by an env switch — it only ever touches tenants that
        # have the qualifier feature on, and a tenant without it has no runs to find.
        from application.services.async_qualifier import async_qualifier_worker
        async_qualifier_worker.start()
        # Seed-roll polling: advances every in-flight ProviderTask, which is the only
        # thing that finishes a task-queue roll (DK64). Ungated — with no async
        # randomizer in use there is simply nothing in the queue to poll, and gating
        # it would mean a restart silently stranding rolls that were already running.
        from application.services import seed_roll_worker
        seed_roll_worker.start()
        # Check-in roster sync: polls each open check-in event's Matcherino venue
        # on its cadence. No env switch — it only touches events staff have opened,
        # and skips tenants with EVENT_CHECK_IN off.
        from application.services import check_in_sync_worker
        check_in_sync_worker.start()
        from application.services import sglive_pool_worker
        sglive_pool_worker.start()
        from application.services import startgg_sync_worker
        startgg_sync_worker.start()
        from application.services import challonge_sync_worker
        challonge_sync_worker.start()
        from application.services import data_export_worker
        data_export_worker.start()
        from application.services import discord_role_sync_worker
        discord_role_sync_worker.start()
        from application.services import telegram_bot_worker
        from application.utils.clients import telegram_client
        from application.utils.mocks.mock_discord import is_mock_discord
        if telegram_client.is_configured() and not is_mock_discord():
            telegram_bot_worker.start()
        discord_queue.start()
        volunteer_reminder.start()
        # Pre-match stage reminder: DMs a stage match's players and approved crew
        # shortly before it starts. Ungated — a community with no staged matches has
        # nothing for it to find.
        stage_reminder.start()
        from application.services import community_event_reminder
        community_event_reminder.start()
        event_dispatch_queue.start()
        webhook_queue.start()
        event_bus.subscribe_async(WebhookService().deliver_event)
        # Members' own webhooks: only the catalogue events, each checked against
        # what the owner can see when it is sent.
        event_bus.subscribe_async(
            PersonalWebhookService().deliver_event, event_types=SUBSCRIBABLE_EVENT_TYPES,
        )
        event_bus.subscribe_async(TelemetryService().record_event)
        # The racetime room bot posts a seed that lands after its set time (an
        # async roll, or one !forceroll asked for) the moment it's rolled.
        from application.events import EventType
        from application.services.race_room_bot_service import on_seed_rolled_event
        event_bus.subscribe_async(on_seed_rolled_event, event_types=[EventType.MATCH_SEED_ROLLED])
    # Started after boot so migrations and the first imports don't read as stalls.
    if loop_watchdog_enabled():
        gc_monitor.install()
        loop_watchdog.start()
    warm_openapi(app)
    metrics_sampler.start()
    worker_watchdog.start()
    from application.services import active_users_metrics, event_retention, telemetry_retention
    active_users_metrics.start()
    telemetry_retention.start()
    event_retention.start()
    # Everything alive now lives as long as the process; freezing it keeps every
    # full collection from re-walking it (a third off each pause, SGLMAN-20/22).
    element_cycles.install()
    if env_flag('GC_FREEZE', default=True):
        logger.info('startup: froze %d objects out of garbage collection', gc_monitor.freeze())
        # Live page trees grow every pass with each open tab; freeze what each
        # full pass leaves so the next one skips it.
        if env_flag('GC_REFREEZE', default=True):
            thaw_minutes = float(os.environ.get('GC_THAW_MINUTES') or gc_monitor.THAW_SECONDS / 60)
            gc_monitor.refreeze_after_full_passes(asyncio.get_running_loop(), thaw_minutes * 60)
    if census_dump := os.environ.get('MEMORY_CENSUS_DUMP'):
        memory_census.dump_on_signal(census_dump)
    # The MCP transport's session manager owns a task group that each per-request
    # server task is started on; entering it here (and exiting it around the
    # yield) means /mcp stops accepting calls before the workers below tear down,
    # rather than after.
    async with mcpserver.session_lifespan():
        logger.info('startup: complete in %.2fs (lifespan %.2fs)',
                    time.perf_counter() - _IMPORT_STARTED, time.perf_counter() - lifespan_started)
        yield
    try:
        # A result or approval still inside its Undo window was meant: save it
        # before the workers that carry its DMs and webhooks stop.
        from application import pending_actions
        await pending_actions.flush_all()
        await active_users_metrics.stop()
        await telemetry_retention.stop()
        await event_retention.stop()
        await race_room_worker.stop()
        await seed_roll_worker.stop()
        await check_in_sync_worker.stop()
        await sglive_pool_worker.stop()
        await startgg_sync_worker.stop()
        await challonge_sync_worker.stop()
        await data_export_worker.stop()
        await discord_role_sync_worker.stop()
        await telegram_bot_worker.stop()
        await speedgaming_sync_worker.stop()
        await discord_event_worker.stop()
        await service_health_worker.stop()
        await close_racetime_bot()
        await async_qualifier_worker.stop()
        await volunteer_reminder.stop()
        await stage_reminder.stop()
        await community_event_reminder.stop()
        await event_dispatch_queue.stop()
        await webhook_queue.stop()
        await discord_queue.stop()
        await web_push_service.aclose_http_client()
        await close_discord_bot()
        await close_db()
    finally:
        await worker_watchdog.stop()
        await metrics_sampler.stop()
        loop_watchdog.stop()
        await flush_sentry()

# Initialize Sentry before the app/middleware are constructed so its
# instrumentation wraps the request path. No-op when SENTRY_DSN is unset.
init_sentry()

app: FastAPI = FastAPI(
    title="Wizzrobe API",
    description=api_description(),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

class FunFactMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers['X-Fun-Fact'] = header_safe(random_fact())
        return response

app.add_middleware(FunFactMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestMetricsMiddleware)
app.add_middleware(AppCorsMiddleware)
# Added last so it is outermost and sees every request the shutdown cancels.
app.add_middleware(InFlightRequestLogMiddleware)

app.include_router(
    api.router,
    prefix='/api',
)
install_openapi_docs(app)

# ORDER IS LOAD-BEARING: frontend.init() hands the app to NiceGUI, which mounts
# itself at '/' and would swallow /mcp and /oauth. Register those routes first.
mcpserver.mount(app)
oauth_server.mount(app)

frontend.init(app)

if __name__ == '__main__':
    print('Please start the app with the "uvicorn" command as shown in the start.sh script')
