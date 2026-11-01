"""The always-on bot service: the Discord gateway and the racetime room bot.

Everything else in Wizzrobe can run on request-driven instances that scale to
zero, but two things need a connection held open around the clock:

- the **Discord gateway**, for member updates and removals (inbound role sync)
  and slash-command sync. Button presses don't need it once Discord's
  Interactions Endpoint URL points at ``/discord/interactions`` on the API.
- the **racetime room bot**, which talks to racetime.gg over websockets, and
  the auto-open worker that creates its rooms.

Run it with ``uvicorn bots:app`` beside the API started with
``DISCORD_GATEWAY=off`` and ``RACETIME_BOT_ENABLED=off``, so exactly one
process holds each connection. It serves only ``/api/health``, so a platform
that wants an HTTP port (Cloud Run) has one to probe. The API applies
migrations; this process only connects to the database.

The single-process deployment (``main.py`` with both switches on) keeps
working without this file.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from tortoise import Tortoise

from api.routers import health
from application import bot_runtime
from application.utils.log_levels import default_root_level, register_trace_level
from application.utils.sentry import flush_sentry, init_sentry
from migrations.tortoise_config import TORTOISE_ORM

# This process is the gateway, whatever the API's environment says.
os.environ['DISCORD_GATEWAY'] = 'on'

register_trace_level()
logging.basicConfig(
    level=default_root_level(),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('wizzrobe.bots')


async def _apply_stored_log_levels() -> None:
    from application.services import LogLevelService
    try:
        await LogLevelService().apply_persisted()
    except Exception:
        logger.exception('Could not apply stored log levels; continuing with defaults')


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from application import pending_actions
    from application.services import race_room_worker
    from application.utils.environment import racetime_bot_enabled
    from racetimebot import close_racetime_bot, init_racetime_bot

    await Tortoise.init(config=TORTOISE_ORM)
    await _apply_stored_log_levels()
    import discordbot  # noqa: F401
    bot_runtime.start_delivery()
    await bot_runtime.start_discord(gateway=True)
    await init_racetime_bot()
    if racetime_bot_enabled():
        race_room_worker.start()
    logger.info('bot service: started')
    try:
        yield
    finally:
        try:
            await pending_actions.flush_all()
            await race_room_worker.stop()
            await close_racetime_bot()
            await bot_runtime.stop_delivery()
            await bot_runtime.close_discord()
            await Tortoise.close_connections()
        finally:
            await flush_sentry()


init_sentry()

app = FastAPI(title='Wizzrobe bot service', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(health.router, prefix='/api')
