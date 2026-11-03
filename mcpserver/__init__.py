"""Remote MCP server for Wizzrobe, served at ``/mcp``.

A fourth entry surface alongside the web UI, the REST API, and the Discord bot:
it calls services, never repositories, and holds no business logic of its own.
The tools are read-only and typed, and are meant for Claude Desktop / Claude
Code connected by a community's organizers.

``main.py`` sees only :func:`mount` and :func:`session_lifespan`.

Mounting is order-sensitive and the ordering is not obvious: ``mount()`` must run
**before** ``frontend.init(app)``. NiceGUI mounts itself at ``/``, which would
otherwise swallow ``/mcp`` before our route is ever consulted.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from starlette.routing import Route

from application.utils.environment import env_flag
from mcpserver.asgi import McpEndpoint
from mcpserver.server import build_server
from mcpserver.wellknown import register_wellknown

logger = logging.getLogger(__name__)

MCP_PATH = '/mcp'

_mcp: Optional[MCPServer] = None


def mount(app: FastAPI) -> None:
    """Register the MCP endpoint and its discovery routes on ``app``.

    Must be called before ``frontend.init(app)`` — see the module docstring.

    A ``Route`` rather than a ``Mount``: Starlette compiles a mount to
    ``^/mcp/(?P<path>.*)$``, so a bare ``POST /mcp`` — which is exactly what
    every MCP client sends — would only reach it via a 307 redirect, and clients
    do not follow redirects by default.
    """
    global _mcp
    if not env_flag('MCP_ENABLED', True):
        logger.info('MCP server disabled (MCP_ENABLED is off)')
        return

    _mcp = build_server()
    endpoint = McpEndpoint(_mcp.session_manager)
    methods = ['POST', 'GET', 'DELETE']
    # Register both spellings: NiceGUI at '/' catches '/mcp/' before redirect_slashes can, so it would 404 silently.
    for path in (MCP_PATH, f'{MCP_PATH}/'):
        app.router.routes.append(Route(path, endpoint=endpoint, methods=methods))
    register_wellknown(app)
    # Lazy import so the OAuth provider and its service graph are built only when the server is enabled.
    from mcpserver.oauth import register_oauth_routes

    register_oauth_routes(app)
    logger.info('MCP server mounted at %s', MCP_PATH)


@asynccontextmanager
async def session_lifespan():
    """Run the transport's session manager for the app's lifetime.

    Required even in stateless mode: ``handle_request`` starts each per-request
    server task on this task group and raises without it. A no-op when the
    server is disabled, so ``main.py`` needs no conditional.
    """
    if _mcp is None:
        yield
        return
    async with _mcp.session_manager.run():
        yield
