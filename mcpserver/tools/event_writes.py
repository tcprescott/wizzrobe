"""Event-day writes: the check-in desk, volunteer arrivals and join requests.

The second write module, beside ``match_writes``, and held to the same rule:
each tool mirrors one REST endpoint, here from ``api/routers/check_in.py``,
``api/routers/memberships.py`` and ``POST /volunteers/assignments/{id}/check-in``.
A write that belongs here belongs in the REST router too, and vice versa.

Every tool is registered at ``Gate.ACTOR`` with ``write=True``, mirroring
``require_write_actor``; the owning service makes the real permission check
(desk, staff, coordinator), so the two surfaces cannot disagree about who may
do what. Each declares its feature flag so a community with the feature off is
never offered it.
"""

from typing import Any, Callable, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.check_in import CheckInEntrantResponse, CheckInResultResponse, CheckInSyncResponse
from api.schemas.memberships import JoinRequestResponse
from api.schemas.volunteers import VolunteerAssignmentResponse
from application.api_scopes import ApiScope
from application.errors import require_found
from application.services import CheckInService, TenantMembershipService
from application.services.volunteer.volunteer_call_off_service import VolunteerCallOffService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import TenantArg
from models import CheckInEntrant, CheckInEvent, FeatureFlag


async def _entrant(service: CheckInService, entrant_id: int) -> CheckInEntrantResponse:
    entrant = require_found(await service.get_entrant(entrant_id), 'Roster entry')
    return CheckInEntrantResponse.from_row(entrant, await service.volunteer_user_ids())


# --- Check-in desk ----------------------------------------------------------


async def check_in_attendee(
    entrant_id: int,
    tenant: TenantArg = None,
    despite_withdrawn: bool = False,
) -> CheckInResultResponse:
    """Check someone in at the desk. `entrant_id` comes from `get_check_in_roster`.

    Doing it twice changes nothing and returns `already: true`. Someone whose
    row has `withdrawn_at` set (refunded, no badge left) is refused; only staff
    can pass `despite_withdrawn` to let them in anyway, and that is audited.
    Hand over the row's `lanyard`.
    """
    service = CheckInService()
    outcome = await service.check_in(
        current_actor().user, entrant_id, despite_withdrawn=despite_withdrawn,
    )
    return CheckInResultResponse(
        entrant=await _entrant(service, outcome.entrant.id), already=outcome.already,
    )


async def undo_attendee_check_in(
    entrant_id: int,
    tenant: TenantArg = None,
) -> CheckInEntrantResponse:
    """Undo a check-in recorded by mistake."""
    service = CheckInService()
    await service.undo_check_in(current_actor().user, entrant_id)
    return await _entrant(service, entrant_id)


async def link_attendee(
    entrant_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> CheckInEntrantResponse:
    """Link a roster row to a member's Wizzrobe account. `user_id` comes from `list_users`."""
    service = CheckInService()
    await service.link(current_actor().user, entrant_id, user_id)
    return await _entrant(service, entrant_id)


async def unlink_attendee(
    entrant_id: int,
    tenant: TenantArg = None,
) -> CheckInEntrantResponse:
    """Remove a wrong account link from a roster row. The sync won't re-link it on its own."""
    service = CheckInService()
    await service.unlink(current_actor().user, entrant_id)
    return await _entrant(service, entrant_id)


async def add_walk_up(
    event_id: int,
    tenant: TenantArg = None,
    user_id: Optional[int] = None,
    name: Optional[str] = None,
    check_in: bool = True,
) -> CheckInEntrantResponse:
    """Add someone who didn't buy a badge on Matcherino. Staff only.

    Give `user_id` for an existing member, or `name` for someone with no
    account. They're checked in as they're added unless `check_in` is false.
    """
    service = CheckInService()
    entrant = await service.add_walk_up(
        current_actor().user, event_id, user_id=user_id, name=name, check_in=check_in,
    )
    return await _entrant(service, entrant.id)


async def sync_check_in_event(
    event_id: int,
    tenant: TenantArg = None,
) -> CheckInSyncResponse:
    """Pull the latest badge sales and comps from Matcherino into the roster now.

    The roster also syncs on its own every few minutes while the event is open.
    """
    result = await CheckInService().sync_event(current_actor().user, event_id)
    return CheckInSyncResponse(**result.as_dict())


# --- Volunteers -------------------------------------------------------------


async def check_in_volunteer(
    assignment_id: int,
    tenant: TenantArg = None,
) -> VolunteerAssignmentResponse:
    """Record that a volunteer showed up for their shift. Coordinators and staff only.

    `assignment_id` is the assignment row, not the shift. Doing it twice keeps
    the first arrival time.
    """
    assignment = await VolunteerScheduleService().check_in(assignment_id, current_actor().user)
    return VolunteerAssignmentResponse.from_row(assignment)


async def call_off_volunteer(
    assignment_id: int,
    note: Optional[str] = None,
    tenant: TenantArg = None,
) -> VolunteerAssignmentResponse:
    """Tell one volunteer they aren't needed for their shift. Coordinators and staff only.

    They get a DM (with `note`, if given), the shift skips check-in, and it
    still counts as fully worked for hours, comps and attendance. Works on a
    shift that has started ("you can head out"). `assignment_id` is the
    assignment row, not the shift. Undo with `undo_volunteer_call_off`.
    """
    assignment = await VolunteerCallOffService().call_off(assignment_id, current_actor().user, note)
    return VolunteerAssignmentResponse.from_row(assignment)


async def call_off_volunteer_shift(
    shift_id: int,
    note: Optional[str] = None,
    tenant: TenantArg = None,
) -> list[VolunteerAssignmentResponse]:
    """Call off everyone on a shift, each with a DM. Coordinators and staff only.

    Drafts and anyone already called off are left alone; returns the
    assignments newly called off. Each still counts as fully worked.
    """
    done = await VolunteerCallOffService().call_off_shift(shift_id, current_actor().user, note)
    return [VolunteerAssignmentResponse.from_row(a) for a in done]


async def undo_volunteer_call_off(
    assignment_id: int,
    tenant: TenantArg = None,
) -> VolunteerAssignmentResponse:
    """Put a called-off volunteer back on their shift. Coordinators and staff only.

    They get a DM saying they're needed after all. Refused once the shift
    has finished.
    """
    assignment = await VolunteerCallOffService().undo_call_off(assignment_id, current_actor().user)
    return VolunteerAssignmentResponse.from_row(assignment)


# --- Join requests ----------------------------------------------------------


async def approve_join_request(
    request_id: int,
    tenant: TenantArg = None,
) -> JoinRequestResponse:
    """Let someone into this community. Staff only; they get a DM saying so.

    `request_id` comes from `list_join_requests`.
    """
    request = await TenantMembershipService().approve_request(current_actor().user, request_id)
    return JoinRequestResponse.from_row(request)


async def deny_join_request(
    request_id: int,
    tenant: TenantArg = None,
) -> JoinRequestResponse:
    """Decline a request to join this community. Staff only; they get a DM saying so."""
    request = await TenantMembershipService().deny_request(current_actor().user, request_id)
    return JoinRequestResponse.from_row(request)


#: Check-in rows: ``event_id`` here is a check-in event, not a Wizzrobe event.
_ENTRANT = {'entrant_id': CheckInEntrant}
_CHECK_IN_EVENT = {'event_id': CheckInEvent}


def register_tools(mcp: MCPServer) -> None:
    def write_tool(
        fn: Callable, title: str, feature: Optional[FeatureFlag], *,
        scope: ApiScope, destructive: bool = False, event: Any = False,
    ) -> None:
        register(
            mcp, fn, gate=Gate.ACTOR, scope=scope, feature=feature, write=True, title=title,
            destructive=destructive,
            event=event,
        )

    check_in = FeatureFlag.EVENT_CHECK_IN
    write_tool(check_in_attendee, 'Check in attendee', check_in, scope=ApiScope.STAFF_WRITE, event=_ENTRANT)
    write_tool(undo_attendee_check_in, 'Undo attendee check-in', check_in, destructive=True, scope=ApiScope.STAFF_WRITE, event=_ENTRANT)
    write_tool(link_attendee, 'Link attendee to account', check_in, scope=ApiScope.STAFF_WRITE, event=_ENTRANT)
    write_tool(unlink_attendee, 'Unlink attendee', check_in, destructive=True, scope=ApiScope.STAFF_WRITE, event=_ENTRANT)
    write_tool(add_walk_up, 'Add walk-up', check_in, scope=ApiScope.STAFF_WRITE, event=_CHECK_IN_EVENT)
    write_tool(sync_check_in_event, 'Sync check-in roster', check_in, scope=ApiScope.STAFF_WRITE, event=_CHECK_IN_EVENT)
    write_tool(check_in_volunteer, 'Check in volunteer', FeatureFlag.VOLUNTEERS, scope=ApiScope.STAFF_WRITE, event=True)
    write_tool(call_off_volunteer, 'Call off volunteer', FeatureFlag.VOLUNTEERS, scope=ApiScope.STAFF_WRITE, event=True)
    write_tool(call_off_volunteer_shift, 'Call off volunteer shift', FeatureFlag.VOLUNTEERS, scope=ApiScope.STAFF_WRITE, event=True)
    write_tool(undo_volunteer_call_off, 'Undo volunteer call-off', FeatureFlag.VOLUNTEERS, scope=ApiScope.STAFF_WRITE, event=True)
    write_tool(approve_join_request, 'Approve join request', None, scope=ApiScope.STAFF_WRITE)
    write_tool(deny_join_request, 'Deny join request', None, scope=ApiScope.STAFF_WRITE)
