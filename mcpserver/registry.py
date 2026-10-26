"""The single registration path for MCP tools.

Every tool is registered through :func:`register`, never through
``@mcp.tool()`` directly. That is the point: a tool cannot ship without an
explicit gate, because there is no other way to get one onto the server.
``tests/mcp/test_mcp_catalogue.py`` asserts the listed tools and
:data:`TOOL_SPECS` agree, so a tool added the wrong way fails the suite rather
than quietly serving unauthenticated data.

The wrapper is also where the tenant is bound and where service exceptions are
translated — both need to happen inside the tool call, and doing them here means
no individual tool can forget either.

An event-owned tool declares ``event=True``: the wrapper adds an optional
``event`` slug to its schema and binds the event the call works in, the row's
own when the tool is about one (``ARG_ROWS`` names which arguments are rows).
Community tools leave it off and work across events as before.

A tool that changes data declares ``write=True``. That one flag drives three
things at once — the ``readOnlyHint`` clients read, the read-only-token refusal
in :func:`~mcpserver.auth.authorize`, and whether the tool is listed to a
read-only connection at all — so a write tool cannot be half-declared.
"""

import functools
import inspect
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Union

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from tortoise.models import Model

from application.event_context import event_scope
from application.services.event_service import EventService
from application.services.telemetry_service import TelemetryEventType, TelemetryService
from application.services.timezone_service import TimezoneService
from application.tenant_context import tenant_scope
from application.timezone_context import tz_scope
from application.utils.sentry import sentry_tags
from mcpserver.auth import Gate, authorize, current_actor, optional_actor, resolve_tenant
from mcpserver.errors import map_service_error
from mcpserver.schemas import EventArg
from models import (
    AsyncQualifier,
    Bracket,
    BracketMatch,
    FeatureFlag,
    Match,
    SpeedGamingEventLink,
    Stage,
    Tournament,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)

#: Tool arguments that name an event-owned row, so the call works in its event.
#: A tool whose argument means something else here passes ``event={...}``.
ARG_ROWS: Dict[str, type[Model]] = {
    'match_id': Match,
    'tournament_id': Tournament,
    'stage_id': Stage,
    'shift_id': VolunteerShift,
    'position_id': VolunteerPosition,
    'assignment_id': VolunteerAssignment,
    'qualifier_id': AsyncQualifier,
    'bracket_id': Bracket,
    'bracket_match_id': BracketMatch,
    'link_id': SpeedGamingEventLink,
}


@dataclass(frozen=True)
class ToolSpec:
    """How a tool is gated, as declared at registration."""

    gate: Gate
    feature: Optional[FeatureFlag] = None
    write: bool = False
    destructive: bool = False
    #: The row arguments of an event-owned tool; None for a community tool.
    event_rows: Optional[Mapping[str, type[Model]]] = None


# name -> spec. Read by the catalogue test and by the tool listing.
TOOL_SPECS: Dict[str, ToolSpec] = {}


def write_tool_names() -> FrozenSet[str]:
    """Tools that change data, by name."""
    return frozenset(name for name, spec in TOOL_SPECS.items() if spec.write)


def platform_tool_names() -> FrozenSet[str]:
    """Super-admin tools, by name."""
    return frozenset(name for name, spec in TOOL_SPECS.items() if spec.gate is Gate.PLATFORM)


async def _record_call(name: str, tenant_id: Optional[int], outcome: str) -> None:
    actor = optional_actor()
    if actor is None:
        return
    # Platform and orientation tools (whoami, list_tenants) name no community,
    # so they land as platform rows (tenant NULL).
    with tenant_scope(tenant_id):
        await TelemetryService().track_api_call(
            event_type=TelemetryEventType.MCP_TOOL_CALL,
            target=name,
            path='/mcp',
            user_id=actor.user.id,
            username=actor.user.username,
            outcome=outcome,
        )


def _ids(value: object) -> List[int]:
    """A row argument's ids: one, a list of them (a filter), or none."""
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, int)]
    return [value] if isinstance(value, int) else []


def register(
    mcp: MCPServer,
    fn: Callable,
    *,
    gate: Gate,
    feature: Optional[FeatureFlag] = None,
    title: Optional[str] = None,
    write: bool = False,
    destructive: bool = False,
    event: Union[bool, Mapping[str, type[Model]]] = False,
) -> None:
    """Register ``fn`` as a gated MCP tool.

    The wrapper resolves the actor, resolves and binds the community named by
    the call's ``tenant`` argument, authorizes inside that binding, and maps any
    service exception to a machine-readable tool error.

    Binding happens here — around the tool body, in one ``with`` — rather than
    in the ASGI layer, because an OAuth token is platform-wide and each call
    picks its own community. Using ``tenant_scope`` means set and reset are the
    same statement, so no tool can leak a community into the next call.

    ``destructive`` narrows ``write``: it marks the writes that remove something
    rather than adding or amending it, which is the distinction a client uses to
    decide how hard to ask the user before proceeding.
    """
    name = fn.__name__
    signature = inspect.signature(fn)
    rows: Optional[Dict[str, type[Model]]] = None
    if event:
        assert 'event' not in signature.parameters, f'{name}: the registry adds the event argument'
        rows = {arg: model for arg, model in ARG_ROWS.items() if arg in signature.parameters}
        if isinstance(event, Mapping):
            rows.update(event)

    @functools.wraps(fn)
    async def wrapped(**kwargs):
        tenant_id: Optional[int] = None
        outcome = 'ok'
        try:
            actor = current_actor()
            if gate.names_no_community:
                await authorize(actor, gate, feature, write=write)
                return await fn(**kwargs)
            slug = kwargs.pop('event', None) if rows is not None else None
            tenant = await resolve_tenant(kwargs.get('tenant'))
            tenant_id = tenant.id
            with tenant_scope(tenant.id):
                event_id = await EventService.default_event_id(tenant.id)
                with event_scope(event_id):
                    await authorize(actor, gate, feature, slug=tenant.slug, write=write)
                if rows is not None:
                    pairs = [(model, row_id) for arg, model in rows.items() for row_id in _ids(kwargs.get(arg))]
                    event_id = await EventService.call_event_id(actor.user, slug, pairs) or event_id
            with tenant_scope(tenant.id), event_scope(event_id):
                # An MCP caller has no browser and is not "a viewer", so there is
                # no personal clock to resolve — but tools still take and derive
                # calendar dates (``get_schedule(date=…)``), and those must land
                # on the community's day. Bound here, alongside the tenant, so no
                # tool can be written that forgets it.
                with tz_scope(await TimezoneService.tenant_timezone_name(tenant.id)):
                    return await fn(**kwargs)
        except Exception as exc:
            with sentry_tags(mcp_tool=name, tenant_slug=kwargs.get('tenant')):
                error = map_service_error(exc, tool=name)
            outcome = str(error).split(':', 1)[0]
            raise error from exc
        finally:
            await _record_call(name, tenant_id, outcome)

    # func_metadata builds the JSON schema from inspect.signature(func,
    # eval_str=True), which follows __wrapped__ back to fn — so the schema comes
    # from the real signature even though the callable takes **kwargs. Assert it
    # rather than trusting it: a functools change here would silently publish an
    # empty schema and every tool would start accepting anything.
    if rows is not None:
        wrapped.__signature__ = signature.replace(parameters=[  # type: ignore[attr-defined]
            *signature.parameters.values(),
            inspect.Parameter('event', inspect.Parameter.KEYWORD_ONLY, default=None, annotation=EventArg),
        ])
    else:
        assert inspect.signature(wrapped).parameters == signature.parameters

    TOOL_SPECS[name] = ToolSpec(
        gate=gate, feature=feature, write=write, destructive=destructive, event_rows=rows,
    )
    mcp.add_tool(
        wrapped,
        name=name,
        title=title,
        # The hint a client reads before deciding whether to prompt. A read tool
        # never needs confirmation; a write one usually does, and a destructive
        # one always does.
        annotations=ToolAnnotations(
            read_only_hint=not write, destructive_hint=destructive
        ),
    )
