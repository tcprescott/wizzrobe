"""Check-in desk endpoints: the roster, checking people in, links and walk-ups.

Gated by the ``EVENT_CHECK_IN`` feature flag at the router and again inside
``CheckInService`` (and ``CheckInDeskService`` for the reads). The HTTP layer takes the coarse read/write token gate and
lets the service answer who may do what, so the rules match the desk page
exactly: staff and Check-in Desk volunteers work the desk, only staff add
walk-ups or check in someone who no longer holds a badge, and a desk
volunteer sees only open events.

Event setup (venues, comp rules, badge lanyards) stays in Admin → Check-in.
"""

from typing import List

from fastapi import APIRouter, Depends, Query, Response, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.check_in import (
    CheckInEntrantResponse,
    CheckInEventResponse,
    CheckInRequest,
    CheckInResultResponse,
    CheckInSyncResponse,
    LinkEntrantRequest,
    WalkUpRequest,
)
from api.schemas.work import DeskMemberResponse, DeskResponse, DeskRowResponse
from application.errors import require_found
from application.services import CheckInDeskService, CheckInService, PlayerHomeService
from models import CheckInEntrant, CheckInEvent, User

router = APIRouter(prefix="/check-in", tags=["Check-in"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(event_id=CheckInEvent, entrant_id=CheckInEntrant))])


async def _entrant_response(service: CheckInService, entrant_id: int) -> CheckInEntrantResponse:
    """Re-read a row after a write so the response carries its badges and names."""
    entrant = require_found(await service.get_entrant(entrant_id), 'Roster entry')
    return CheckInEntrantResponse.from_row(entrant, await service.volunteer_user_ids())


@router.get(
    "/events",
    response_model=List[CheckInEventResponse],
    summary="List the check-in events you can work",
    description=(
        "Returns the check-in events you can work at the desk. Staff see every event, draft and closed ones included; Check-in desk volunteers see only open events.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def list_events(actor: User = Depends(require_api_actor)):
    return [CheckInEventResponse.from_row(e) for e in await CheckInDeskService().events_for(actor)]


@router.get(
    "/events/{event_id}/roster",
    response_model=List[CheckInEntrantResponse],
    summary="An event's roster, with each person's badges, lanyard and check-in state",
    description=(
        "Returns everyone on the event's roster: badge buyers, walk-ups and comps, with their badges, lanyard and check-in state. Check-in desk volunteers can read open events only.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def get_roster(event_id: int, actor: User = Depends(require_api_actor)):
    service = CheckInService()
    _, entrants = await CheckInDeskService().roster_for(actor, event_id)
    volunteers = await service.volunteer_user_ids()
    return [CheckInEntrantResponse.from_row(e, volunteers) for e in entrants]


@router.post(
    "/events/{event_id}/sync",
    response_model=CheckInSyncResponse,
    summary="Pull the latest badge sales and comps into the roster",
    description=(
        "Pulls the event's latest Matcherino badge sales and applies its comp rules: new buyers are added, refunded ones are marked withdrawn, and accounts that match are linked. If Matcherino can't be reached, nothing changes. Check-in desk volunteers can sync open events only.\n\n"
        "Fails with 400 when the event has no Matcherino venue and no comp rules, Matcherino returns an error, or the roster changed mid-sync (try again).\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def sync_event(event_id: int, actor: User = Depends(require_write_actor)):
    result = await CheckInService().sync_event(actor, event_id)
    return CheckInSyncResponse(**result.as_dict())


@router.post(
    "/events/{event_id}/walk-ups",
    response_model=CheckInEntrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add someone who didn't buy a badge (Staff only)",
    description=(
        "Adds a walk-up to the roster, either a member (`user_id`) or just a name, and checks them in unless `check_in` is false. Checking in a member with a linked Discord account sends them a test DM; the result shows in `dm_status`.\n\n"
        "Fails with 400 when neither a member nor a name is given, the member is already on this roster, or the account can't be linked.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def add_walk_up(event_id: int, body: WalkUpRequest, actor: User = Depends(require_write_actor)):
    service = CheckInService()
    entrant = await service.add_walk_up(
        actor, event_id, user_id=body.user_id, name=body.name, check_in=body.check_in,
    )
    return await _entrant_response(service, entrant.id)


@router.post(
    "/entrants/{entrant_id}/check-in",
    response_model=CheckInResultResponse,
    summary="Check someone in",
    description=(
        "Checks the person in and records who did it. Checking in someone with a linked Discord account sends them a test DM; the result shows in `dm_status`. Checking in someone already checked in changes nothing and returns `already: true`. Check-in desk volunteers can do this on open events only.\n\n"
        "Fails with 400 when the person no longer holds a badge (refunded, or no longer comped), unless a staff caller sends `despite_withdrawn: true`.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def check_in(
    entrant_id: int,
    body: CheckInRequest | None = None,
    actor: User = Depends(require_write_actor),
):
    service = CheckInService()
    outcome = await service.check_in(
        actor, entrant_id, despite_withdrawn=bool(body and body.despite_withdrawn),
    )
    return CheckInResultResponse(
        entrant=await _entrant_response(service, outcome.entrant.id), already=outcome.already,
    )


@router.delete(
    "/entrants/{entrant_id}/check-in",
    response_model=CheckInEntrantResponse,
    summary="Undo a check-in",
    description=(
        "Clears the check-in time and who did it. Undoing someone who isn't checked in changes nothing. Check-in desk volunteers can do this on open events only.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def undo_check_in(entrant_id: int, actor: User = Depends(require_write_actor)):
    service = CheckInService()
    await service.undo_check_in(actor, entrant_id)
    return await _entrant_response(service, entrant_id)


@router.put(
    "/entrants/{entrant_id}/user",
    response_model=CheckInEntrantResponse,
    summary="Link a roster row to a member's account",
    description=(
        "Links the roster row to a member's account, replacing any automatic match, and remembers their Matcherino account on it. Linking the account it already has changes nothing.\n\n"
        "Fails with 400 when the row is a comp, the member is already on this roster under another row, the row's Matcherino account is already linked to someone else, or the account can't be linked.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def link_entrant(
    entrant_id: int, body: LinkEntrantRequest, actor: User = Depends(require_write_actor),
):
    service = CheckInService()
    await service.link(actor, entrant_id, body.user_id)
    return await _entrant_response(service, entrant_id)


@router.delete(
    "/entrants/{entrant_id}/user",
    response_model=CheckInEntrantResponse,
    summary="Unlink a roster row from its account",
    description=(
        "Detaches the account from the roster row and clears the Matcherino account it recorded on the member. The sync won't link this row automatically again. A row with no account is left as it is.\n\n"
        "Fails with 400 when the row is a comp.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def unlink_entrant(entrant_id: int, actor: User = Depends(require_write_actor)):
    service = CheckInService()
    await service.unlink(actor, entrant_id)
    return await _entrant_response(service, entrant_id)


def _members(users: List[User]) -> List[DeskMemberResponse]:
    return [DeskMemberResponse(id=u.id, name=u.preferred_name, username=u.username) for u in users]


@router.get(
    "/events/{event_id}/desk",
    response_model=DeskResponse,
    summary="The check-in desk for one event",
    description=(
        "The event, its roster as the desk shows it (badge codes and Discord usernames for the search, how many likely accounts each unlinked row has), and whether you may do the staff-only things. Check-in desk volunteers can read open events only.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def get_desk(event_id: int, actor: User = Depends(require_api_actor)):
    view = await CheckInDeskService().desk(actor, event_id)
    return DeskResponse(
        zone=await PlayerHomeService.viewer_zone(actor),
        event=CheckInEventResponse.from_row(view.event),
        can_manage=view.can_manage,
        rows=[DeskRowResponse.from_desk_row(e, view.volunteer_ids, view.suggestions) for e in view.entrants],
    )


@router.get(
    "/events/{event_id}/members",
    response_model=List[DeskMemberResponse],
    summary="Find members to link or add as a walk-up",
    description=(
        "Community members not yet on this roster whose name, Discord, Twitch or Matcherino name contains `q` (two characters or more). With `for_link=true` it also offers people on the roster only as a comp, so a comped volunteer who bought a badge can be linked to it.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def find_members(
    event_id: int,
    q: str = Query(..., max_length=100, description="Part of their name."),
    for_link: bool = Query(False, description="Searching to link a roster row, not to add a walk-up."),
    actor: User = Depends(require_api_actor),
):
    return _members(await CheckInDeskService().find_members(actor, event_id, q, for_link=for_link))


@router.get(
    "/entrants/{entrant_id}/suggestions",
    response_model=List[DeskMemberResponse],
    summary="Likely accounts for a roster row",
    description=(
        "Members whose names look like this row's name, best first, for the desk to confirm with `PUT /check-in/entrants/{entrant_id}/user`. Never linked on its own.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def entrant_suggestions(entrant_id: int, actor: User = Depends(require_api_actor)):
    return _members(await CheckInDeskService().suggestions_for(actor, entrant_id))


@router.post(
    "/entrants/{entrant_id}/test-dm",
    response_model=CheckInEntrantResponse,
    summary="Send the check-in DM again",
    description=(
        "Re-sends the check-in test DM, for someone who has just opened their Discord DMs. The result shows in `dm_status`.\n\n"
        "Fails with 400 when they aren't checked in or have no Discord account linked.\n\n"
        "**Who can call this:** Staff or Check-in desk."
    ),
)
async def resend_test_dm(entrant_id: int, actor: User = Depends(require_write_actor)):
    await CheckInDeskService().send_test_dm(actor, entrant_id)
    return await _entrant_response(CheckInService(), entrant_id)


@router.delete(
    "/entrants/{entrant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a walk-up added by mistake (Staff only)",
    description=(
        "Deletes a walk-up row. Matcherino badge holders and comps come back on the next sync, so they can't be removed here.\n\n"
        "Fails with 400 for a Matcherino or comp row.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def remove_entrant(entrant_id: int, actor: User = Depends(require_write_actor)):
    await CheckInService().remove_entrant(actor, entrant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

