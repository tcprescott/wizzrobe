#!/usr/bin/env python3
"""Main entry point for the Wizzrobe FastAPI application.

Initializes the database, sets up API and frontend routes, and manages application lifespan.
"""

import time

_IMPORT_STARTED = time.perf_counter()

import asyncio
import logging
import os
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Iterator

from aerich import Command
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from tortoise import Tortoise

import api
import frontend

# mcpserver must not build MCPServer at import: its logging.basicConfig would beat ours.
import mcpserver
from api import inbound_webhooks, oauth_server, worker_ticks
from api.openapi_docs import api_description, warm_openapi
from api.openapi_docs import install as install_openapi_docs
from application import bot_runtime, worker_registry
from application.utils import element_cycles, gc_monitor, memory_census, metrics_sampler, worker_watchdog
from application.utils.easter_eggs import random_fact
from application.utils.environment import discord_gateway_enabled, env_flag, loop_watchdog_enabled
from application.utils.http_headers import header_safe
from application.utils.log_levels import default_root_level, register_trace_level
from application.utils.loop_watchdog import watchdog as loop_watchdog
from application.utils.migration_lock import migration_lock
from application.utils.sentry import flush_sentry, init_sentry
from middleware.app_cors import AppCorsMiddleware
from middleware.inflight_requests import InFlightRequestLogMiddleware
from middleware.request_metrics import RequestMetricsMiddleware
from middleware.security_headers import SecurityHeadersMiddleware
from migrations.tortoise_config import DB_URL, TORTOISE_ORM

register_trace_level()
logging.basicConfig(
    level=default_root_level(),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('wizzrobe.main')

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
        import discordbot  # noqa: F401
        await bot_runtime.start_discord(gateway=discord_gateway_enabled())
    from racetimebot import close_racetime_bot, init_racetime_bot
    with _startup_step('racetime bot'):
        await init_racetime_bot()
    with _startup_step('background workers'):
        worker_registry.start_in_process()
        bot_runtime.start_delivery()
    # Started after boot so migrations and the first imports don't read as stalls.
    if loop_watchdog_enabled():
        gc_monitor.install()
        loop_watchdog.start()
    warm_openapi(app)
    metrics_sampler.start()
    worker_watchdog.start()
    element_cycles.install()
    if env_flag('GC_FREEZE', default=True):
        logger.info('startup: froze %d objects out of garbage collection', gc_monitor.freeze())
        if env_flag('GC_REFREEZE', default=True):
            thaw_minutes = float(os.environ.get('GC_THAW_MINUTES') or gc_monitor.THAW_SECONDS / 60)
            gc_monitor.refreeze_after_full_passes(asyncio.get_running_loop(), thaw_minutes * 60)
    if census_dump := os.environ.get('MEMORY_CENSUS_DUMP'):
        memory_census.dump_on_signal(census_dump)
    # Entered around the yield so /mcp stops taking calls before the workers below tear down.
    async with mcpserver.session_lifespan():
        logger.info('startup: complete in %.2fs (lifespan %.2fs)',
                    time.perf_counter() - _IMPORT_STARTED, time.perf_counter() - lifespan_started)
        yield
    try:
        # Must flush before the workers that carry its DMs and webhooks stop.
        from application import pending_actions
        await pending_actions.flush_all()
        await worker_registry.stop_all()
        await close_racetime_bot()
        await bot_runtime.stop_delivery()
        await bot_runtime.close_discord()
        await close_db()
    finally:
        await worker_watchdog.stop()
        await metrics_sampler.stop()
        loop_watchdog.stop()
        await flush_sentry()

# Init before the app is constructed so Sentry's instrumentation wraps the request path.
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
# itself at '/' and would swallow /mcp, /oauth and /internal. Register those routes first.
mcpserver.mount(app)
oauth_server.mount(app)
inbound_webhooks.mount(app)
worker_ticks.mount(app)

frontend.init(app)

if __name__ == '__main__':
    print('Please start the app with the "uvicorn" command as shown in the start.sh script')
