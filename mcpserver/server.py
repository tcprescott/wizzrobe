"""MCP server construction.

Kept in a function rather than at module scope for a non-obvious reason:
``MCPServer.__init__`` calls ``configure_logging()``, which calls
``logging.basicConfig``. Constructing the server at import time would win the
race against ``main.py``'s own ``basicConfig`` and strip timestamps, levels and
logger names from every log line in the process. Building it from ``mount()``,
which runs after logging is configured, keeps our format.
"""

from typing import List, Set

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool as MCPTool

from application.services.auth_service import AuthService
from application.services.mcp_auth_service import grant_scopes
from application.utils.build_info import build_info
from mcpserver.auth import optional_actor
from mcpserver.registry import platform_tool_names, tools_outside, write_tool_names
from mcpserver.tools import register_all

INSTRUCTIONS = """\
Access to Wizzrobe, a tournament management platform. You can look up \
tournaments, matches, schedules, brackets and qualifiers, people, crew and \
volunteer assignments, equipment, seed presets, racetime and SpeedGaming \
integrations, audit history, engagement telemetry, and operational reports.

Most tools only read. If this connection was approved for write access you will \
also see tools that change data — scheduling and editing matches, running the \
match lifecycle, recording results, rolling seeds, and crew signups. They are \
the tools whose `readOnlyHint` is false. If you cannot see them, the person who \
connected this client approved it for reading only, and reconnecting is the \
only way to change that. Confirm with the user before a write that other people \
are relying on, and never guess at an id — read it back first.

Start with `whoami`. It reports which communities you belong to, what role you \
hold in each, which optional features each has enabled, and whether this \
connection may write — so you can avoid calling tools that would be refused.

Wizzrobe hosts many independent communities. Every tool except `whoami`, \
`list_tenants` and the platform tools takes a required `tenant` argument: the \
community's slug, as returned by `list_tenants`. A tool that reports `not_found` for a feature means \
that community has not enabled it, not that you asked incorrectly.

A super admin is also served the platform tools (`platform_*` and \
`runtime_stats`), which cover the /platform page: every community, inactive \
ones included, every account, feature groups, racetime bots, log levels and \
the platform health board. They take no `tenant`; the ones about one community \
take its slug as `community`. Passwords and new accounts stay on the page.

All times are returned in UTC. Each community chooses the timezone its \
members read them in, so convert before quoting a time to a person.

Discord ids (`discord_id`) are returned as strings: they are too large for a \
JSON number, so keep them as strings rather than converting.\
"""


def start_transport(mcp: MCPServer) -> None:
    """Build the streamable-HTTP session manager that mcpserver/asgi.py drives.

    Called for its side effect only. The Starlette app it returns is discarded —
    we need the bare path, and a Mount would answer /mcp with a 307 to /mcp/.
    Every call mints a fresh manager, whose ``run()`` may be entered once.
    """
    mcp.streamable_http_app(
        # Do not change: stateful mode runs a session's later requests under the first request's identity.
        stateless_http=True,
        # JSON, not SSE: BaseHTTPMiddleware around a long-lived SSE stream hangs on client disconnect.
        json_response=True,
        # We serve the path ourselves; the '/mcp' default would put the endpoint at '/mcp/mcp'.
        streamable_http_path='/',
        # Off: TLS and Host terminate at the proxy, and the default rejects every production request with 421.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )


class WizzrobeMCP(MCPServer):
    """MCPServer that hides the write tools from a read-only connection, and
    the platform tools from anyone who is not a super admin.

    Every write is refused at the gate regardless, so this is not the security
    boundary — it is a context one. A read-only client offered nineteen tools it
    can never call spends tokens on them and plans work it cannot do, and the
    refusal arrives a round-trip after the model has already told the user it
    would reschedule their match.

    Listing runs inside the request's context (see ``mcpserver/asgi.py``), so
    the actor is the one whose token is being served. Outside a request — the
    SDK builds the list once while wiring itself up — there is no actor and the
    full catalogue is returned.
    """

    async def list_tools(self) -> List[MCPTool]:
        tools = await super().list_tools()
        actor = optional_actor()
        if actor is None:
            return tools
        hidden: Set[str] = set()
        if actor.token.read_only:
            hidden |= write_tool_names()
        hidden |= tools_outside(grant_scopes(actor.token.scope))
        if not await AuthService.is_super_admin(actor.user):
            hidden |= platform_tool_names()
        return [tool for tool in tools if tool.name not in hidden]


def build_server() -> MCPServer:
    """Construct the MCP server with every tool registered."""
    mcp = WizzrobeMCP(name='Wizzrobe', instructions=INSTRUCTIONS, version=build_info().version)
    register_all(mcp)
    start_transport(mcp)
    return mcp
