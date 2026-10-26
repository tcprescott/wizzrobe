"""Tenant resolution for the Discord DM interaction handlers.

A DM button press arrives with no guild and no tenant context
(``interaction.guild_id`` is None). Each handler resolves the referenced
entity's tenant here via a **global** model read — the sanctioned load-or-404
pattern, intentionally *unscoped* so it can DISCOVER which tenant the entity
belongs to — then wraps its scoped service work in ``tenant_scope(tenant_id)``.
"""

from typing import Optional

from models import CommentaryInvite, Commentator, Match, MatchRescheduleRequest, Tracker, VolunteerAssignment


async def match_tenant_id(match_id: int) -> Optional[int]:
    match = await Match.get_or_none(id=match_id)
    return match.tenant_id if match else None


async def crew_tenant_id(crew_id: int, crew_type: str) -> Optional[int]:
    model = Commentator if crew_type == 'commentator' else Tracker
    row = await model.get_or_none(id=crew_id)
    return row.tenant_id if row else None


async def assignment_tenant_id(assignment_id: int) -> Optional[int]:
    assignment = await VolunteerAssignment.get_or_none(id=assignment_id)
    return assignment.tenant_id if assignment else None


async def reschedule_request_tenant_id(request_id: int) -> Optional[int]:
    request = await MatchRescheduleRequest.get_or_none(id=request_id)
    return request.tenant_id if request else None  # type: ignore[attr-defined]


async def commentary_invite_tenant_id(invite_id: int) -> Optional[int]:
    invite = await CommentaryInvite.get_or_none(id=invite_id)
    return invite.tenant_id if invite else None
