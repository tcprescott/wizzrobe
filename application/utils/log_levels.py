"""Runtime control of stdlib logger levels.

The pure half of the ``/platform`` log-level controls: which levels exist, which
loggers are worth offering by name, and applying a level to the live logging
tree. Persistence and authorization live in
:class:`~application.services.log_level_service.LogLevelService`.

Levels only gate what a logger *emits*. Where the record goes afterwards is
fixed elsewhere: stdout gets everything that passes, Sentry turns ERROR+ into
events and ships INFO+ to Sentry Logs (``application/utils/sentry.py``), so
turning a module up to DEBUG makes it chattier in the container log without
spending Sentry quota.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

TRACE = 5

LEVELS: tuple[str, ...] = ('TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

ROOT_LOGGER = ''

_LOGGER_NAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,254}$')


@dataclass(frozen=True)
class KnownLogger:
    name: str
    description: str


KNOWN_LOGGERS: tuple[KnownLogger, ...] = (
    KnownLogger(ROOT_LOGGER, 'Everything not set more specifically below'),
    KnownLogger('application', 'Services, workers and repositories'),
    KnownLogger('application.services.discord', 'Discord DMs, guild and role operations'),
    KnownLogger('application.services.match', 'Match scheduling, lifecycle and notifications'),
    KnownLogger('application.utils.seed_provider', 'Randomizer calls (seed generation)'),
    KnownLogger('application.utils.background_loop', 'Background worker ticks'),
    KnownLogger('application.services.webhook_service', 'Outbound webhook deliveries'),
    KnownLogger('discordbot', 'Discord button and interaction handlers'),
    KnownLogger('racetimebot', 'racetime.gg bot connections and race-room handlers'),
    KnownLogger('api', 'REST API routers'),
    KnownLogger('mcpserver', 'MCP server tools'),
    KnownLogger('pages', 'Web UI pages'),
    KnownLogger('middleware', 'Auth, tenant and error-page middleware'),
    KnownLogger('wizzrobe', 'Startup and UI event-handler backstop'),
    KnownLogger('discord', 'discord.py gateway and HTTP client'),
    KnownLogger('httpx', 'Outbound HTTP requests (every call at INFO)'),
    KnownLogger('tortoise', 'ORM; DEBUG logs every SQL query'),
    KnownLogger('nicegui', 'NiceGUI framework'),
    KnownLogger('uvicorn.access', 'One line per HTTP request'),
)


def register_trace_level() -> None:
    """Teach ``logging`` the TRACE name so records and ``getLevelName`` show it."""
    logging.addLevelName(TRACE, 'TRACE')


def level_number(level: str) -> int:
    """The numeric level for one of :data:`LEVELS`; raises ValueError otherwise."""
    name = (level or '').strip().upper()
    if name not in LEVELS:
        raise ValueError(f'Unknown log level {level!r}; pick one of {", ".join(LEVELS)}')
    return TRACE if name == 'TRACE' else logging.getLevelName(name)


def normalize_logger_name(name: Optional[str]) -> str:
    """Validate a dotted logger name, ``''`` meaning the root logger."""
    cleaned = (name or '').strip()
    if cleaned.lower() == 'root':
        return ROOT_LOGGER
    if cleaned and not _LOGGER_NAME_RE.match(cleaned):
        raise ValueError(
            'A logger name is a dotted module path like application.services.match',
        )
    return cleaned


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name or None)


def apply_level(name: str, level: Optional[str]) -> None:
    """Set ``name`` to ``level`` on the live tree; ``None`` restores ``default``."""
    logger = get_logger(name)
    if level is not None:
        logger.setLevel(level_number(level))
    elif name == ROOT_LOGGER:
        logger.setLevel(default_root_level())
    else:
        logger.setLevel(logging.NOTSET)


def default_root_level() -> int:
    """The root level the process booted with (``LOG_LEVEL``, else INFO)."""
    configured = (os.environ.get('LOG_LEVEL') or 'INFO').strip().upper()
    try:
        return level_number(configured)
    except ValueError:
        return logging.INFO


def effective_level_name(name: str) -> str:
    """The level ``name`` actually runs at, inherited from its parents if unset."""
    return level_label(get_logger(name).getEffectiveLevel())


def level_label(number: int) -> str:
    label = logging.getLevelName(number)
    return label if isinstance(label, str) and not label.startswith('Level ') else str(number)
