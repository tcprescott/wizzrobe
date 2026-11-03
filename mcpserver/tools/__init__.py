"""Tool modules, grouped by domain.

Each module exposes ``register_tools(mcp)`` and registers only through
``mcpserver.registry.register``, so every tool carries an explicit gate.

``match_writes``, ``event_writes``, ``tournament_writes``, ``admin_writes`` and
``platform_writes`` are the modules that change data, kept
apart from the read modules rather than filed by domain: whether a tool writes is the property that
decides its annotation, its gate and whether a read-only connection is even
shown it, so it is the property the layout should make obvious. ``exports`` is
the one mixed module: queuing an export only writes the request row, never
community data, so its two write tools sit beside the reads that poll them.
``events`` is mixed too: the community's events and their lifecycle, read and
run from one place as Admin → Events does. So is ``surveys``: building a survey
and reading its results are one staff job, and nothing it writes reaches anyone
until staff send it from the app.
"""

from mcp.server.mcpserver import MCPServer

from mcpserver.tools import (
    admin_writes,
    analytics,
    check_in,
    competition,
    equipment,
    event_writes,
    events,
    exports,
    match_writes,
    matches,
    observability,
    online_play,
    orientation,
    people,
    platform,
    platform_writes,
    surveys,
    tournament_writes,
    tournaments,
    volunteers,
)


def register_all(mcp: MCPServer) -> None:
    """Register every tool module onto the server."""
    orientation.register_tools(mcp)
    events.register_tools(mcp)
    tournaments.register_tools(mcp)
    matches.register_tools(mcp)
    people.register_tools(mcp)
    volunteers.register_tools(mcp)
    equipment.register_tools(mcp)
    observability.register_tools(mcp)
    analytics.register_tools(mcp)
    competition.register_tools(mcp)
    online_play.register_tools(mcp)
    check_in.register_tools(mcp)
    match_writes.register_tools(mcp)
    event_writes.register_tools(mcp)
    tournament_writes.register_tools(mcp)
    admin_writes.register_tools(mcp)
    platform.register_tools(mcp)
    platform_writes.register_tools(mcp)
    exports.register_tools(mcp)
    surveys.register_tools(mcp)
