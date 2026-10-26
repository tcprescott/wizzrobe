"""A community's events: list them, read their settings, and run their lifecycle.

Mirrors ``api/routers/events.py``. Every other event-day tool takes an
optional ``event`` slug (added by the registry); these name the event they act
on in ``slug``. A draft is not_found for anyone but staff and its own role
holders. The writes are staff-only, checked in ``EventService``.
"""

from datetime import date
from typing import Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from application.event_context import event_scope
from application.services import EventService, SystemConfigService
from application.services.event_service import EventRef
from application.services.system_config_service import is_event_key
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import EventInfo, OperationResult, TenantArg


class EventSetting(BaseModel):
    name: str
    value: str


class CreatedEvent(BaseModel):
    event: EventInfo
    copied: Dict[str, object] = Field(
        default_factory=dict, description="What was copied: a count per group, plus anything skipped and why.",
    )


def event_info(event) -> EventInfo:
    status = event.status.value if hasattr(event.status, 'value') else str(event.status)
    return EventInfo(
        slug=event.slug, name=event.name, status=status, is_default=event.is_default,
        starts_on=event.starts_on, ends_on=event.ends_on,
    )


async def _event_id(slug: str) -> int:
    event_id = await EventService.call_event_id(current_actor().user, slug)
    assert event_id is not None
    return event_id


async def _info(event_id: int) -> EventInfo:
    return event_info(await EventService.get(event_id))


async def list_events(tenant: TenantArg = None) -> List[EventInfo]:
    """List the community's events you can see: the default first, then live, drafts and archived.

    Pass an event's `slug` as `event` to any event-day tool to work in it.
    """
    refs: List[EventRef] = await EventService.visible_events(current_actor().user)
    return [event_info(e) for e in refs]


async def get_event(slug: str, tenant: TenantArg = None) -> EventInfo:
    """Get one event by its slug."""
    return await _info(await _event_id(slug))


async def get_event_settings(slug: str, tenant: TenantArg = None) -> List[EventSetting]:
    """List the settings stored on one event (Admin → Event settings). Staff only.

    Community-wide settings are under `get_system_config`.
    """
    with event_scope(await _event_id(slug)):
        rows = await SystemConfigService.list_event_entries(current_actor().user)
    return [EventSetting(name=name, value=value) for name, value in rows]


async def update_event_settings(slug: str, key: str, value: str, tenant: TenantArg = None) -> EventSetting:
    """Set one of an event's settings, validated and audited. Staff only.

    `key` must be an event setting, as `get_event_settings` lists them.
    """
    if not is_event_key(key):
        raise ValueError(f"'{key}' isn't an event setting; community settings aren't changed here.")
    with event_scope(await _event_id(slug)):
        entry = await SystemConfigService.set_validated(key, value, current_actor().user)
    return EventSetting(name=entry.name, value=entry.value)


async def create_event(
    name: str,
    tenant: TenantArg = None,
    slug: Optional[str] = None,
    starts_on: Optional[date] = None,
    ends_on: Optional[date] = None,
    copy_from: Optional[str] = None,
    copy: Optional[List[str]] = None,
) -> CreatedEvent:
    """Create a draft event, optionally copying setup from another one. Staff only.

    `copy` takes any of `tournaments`, `positions`, `stages`, `check_in` and
    `discord_roles`, copied from the event whose slug is `copy_from`. People,
    matches and shifts are never copied. The slug is made from the name when
    left out.
    """
    copy_from_id = await _event_id(copy_from) if copy_from else None
    event, copied = await EventService.create_event(
        current_actor().user, name=name, slug=slug, starts_on=starts_on, ends_on=ends_on,
        copy_from=copy_from_id, copy=frozenset(copy or ()),
    )
    return CreatedEvent(event=event_info(event), copied=copied)


async def update_event(
    slug: str,
    tenant: TenantArg = None,
    name: Optional[str] = None,
    new_slug: Optional[str] = None,
    starts_on: Optional[date] = None,
    ends_on: Optional[date] = None,
) -> EventInfo:
    """Rename an event or set its dates. Only a draft's slug can change. Staff only."""
    dates = {key: value for key, value in (('starts_on', starts_on), ('ends_on', ends_on)) if value is not None}
    event = await EventService.update_event(
        current_actor().user, await _event_id(slug), name=name, slug=new_slug, **dates,
    )
    return event_info(event)


async def go_live_event(slug: str, tenant: TenantArg = None) -> EventInfo:
    """Make a draft live, open to everyone. Staff only."""
    return event_info(await EventService.go_live(current_actor().user, await _event_id(slug)))


async def make_default_event(slug: str, tenant: TenantArg = None) -> EventInfo:
    """Make a live event the default: where bare links and tools without `event` land. Staff only."""
    return event_info(await EventService.make_default(current_actor().user, await _event_id(slug)))


async def archive_event(slug: str, tenant: TenantArg = None, new_default: Optional[str] = None) -> EventInfo:
    """Archive an event, making it read-only. Staff only.

    Its race rooms close, waiting seed rolls are dropped and the Discord roles
    its rules gave are taken back. Archiving the default needs `new_default`,
    the slug of a live event to take over.
    """
    new_default_id = await _event_id(new_default) if new_default else None
    event = await EventService.archive(current_actor().user, await _event_id(slug), new_default_id=new_default_id)
    return event_info(event)


async def unarchive_event(slug: str, tenant: TenantArg = None) -> EventInfo:
    """Make an archived event live and editable again. Staff only."""
    return event_info(await EventService.unarchive(current_actor().user, await _event_id(slug)))


async def delete_draft_event(slug: str, tenant: TenantArg = None) -> OperationResult:
    """Delete a draft event and everything in it. Live and archived events can't be deleted. Staff only."""
    await EventService.delete_draft(current_actor().user, await _event_id(slug))
    return OperationResult(detail=f"Deleted draft '{slug}'.")


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_events, gate=Gate.ACTOR, title='List events')
    register(mcp, get_event, gate=Gate.ACTOR, title='Get event')
    register(mcp, get_event_settings, gate=Gate.ACTOR, title='Get event settings')
    register(mcp, create_event, gate=Gate.ACTOR, write=True, title='Create event')
    register(mcp, update_event, gate=Gate.ACTOR, write=True, title='Update event')
    register(mcp, update_event_settings, gate=Gate.ACTOR, write=True, title='Update event setting')
    register(mcp, go_live_event, gate=Gate.ACTOR, write=True, title='Make event live')
    register(mcp, make_default_event, gate=Gate.ACTOR, write=True, title='Make event the default')
    register(mcp, archive_event, gate=Gate.ACTOR, write=True, title='Archive event')
    register(mcp, unarchive_event, gate=Gate.ACTOR, write=True, title='Unarchive event')
    register(mcp, delete_draft_event, gate=Gate.ACTOR, write=True, destructive=True, title='Delete draft event')
