"""Tenant and event resolution for the Discord DM interaction handlers.

A DM button press arrives with no guild and no tenant context
(``interaction.guild_id`` is None). Each handler resolves the referenced
entity's tenant here via a **global** model read — the sanctioned load-or-404
pattern, intentionally *unscoped* so it can DISCOVER which tenant the entity
belongs to — then wraps its scoped service work in ``tenant_scope(tenant_id)``.

The entity's event comes back with it: a DM outlives the page that sent it, so
the button runs in the event of the match or shift it is about, not the
community's default event.
"""

from typing import NamedTuple, Optional

from models import (
    Broadcaster,
    CommentaryInvite,
    Commentator,
    Match,
    MatchRescheduleRequest,
    Tracker,
    VolunteerAssignment,
)


class RowScope(NamedTuple):
    tenant_id: int
    event_id: Optional[int]


def _scope(rows: list, event_key: str) -> Optional[RowScope]:
    if not rows:
        return None
    return RowScope(rows[0]['tenant_id'], rows[0][event_key])


async def match_scope(match_id: int) -> Optional[RowScope]:
    return _scope(await Match.filter(id=match_id).values('tenant_id', 'event_id'), 'event_id')


async def crew_scope(crew_id: int, crew_type: str) -> Optional[RowScope]:
    model = {'commentator': Commentator, 'tracker': Tracker, 'broadcaster': Broadcaster}.get(crew_type, Tracker)
    return _scope(await model.filter(id=crew_id).values('tenant_id', 'match__event_id'), 'match__event_id')


async def assignment_scope(assignment_id: int) -> Optional[RowScope]:
    rows = await VolunteerAssignment.filter(id=assignment_id).values('tenant_id', 'shift__event_id')
    return _scope(rows, 'shift__event_id')


async def reschedule_request_scope(request_id: int) -> Optional[RowScope]:
    rows = await MatchRescheduleRequest.filter(id=request_id).values('tenant_id', 'match__event_id')
    return _scope(rows, 'match__event_id')


async def commentary_invite_scope(invite_id: int) -> Optional[RowScope]:
    rows = await CommentaryInvite.filter(id=invite_id).values('tenant_id', 'match__event_id')
    return _scope(rows, 'match__event_id')
