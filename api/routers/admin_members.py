"""The app's Admin → Members screen: the member table, Add a member, Import from
Discord, ADA requests, the audited emergency-contact look and Send message.

Role grants, membership changes, profile edits and join requests keep their
existing routes under ``/users`` and ``/join-requests``.
"""

from typing import List

from fastapi import APIRouter, Depends, Query

from api._helpers import load_community_user_or_404
from api.dependencies import ServiceErrorRoute, event_binding, require_staff, require_staff_write
from api.schemas.admin_members import (
    AdaRequestResponse,
    AdaRequestUpdate,
    AddableAccountResponse,
    DiscordImportPreviewResponse,
    DiscordImportResultResponse,
    EmergencyContactViewResponse,
    MembersBoardResponse,
    StaffMessageRequest,
)
from api.schemas.detail import DetailResponse
from application.services import (
    AccommodationService,
    DiscordMemberImportService,
    EmergencyContactService,
    MembersAdminService,
    StaffMessageService,
)
from models import AccommodationRequest, User

router = APIRouter(
    prefix='/admin/members', tags=['Admin: members'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(request_id=AccommodationRequest))],
)


@router.get(
    '',
    response_model=MembersBoardResponse,
    summary='The member table',
    description=(
        "Every member of this community with their roles, how and when they joined, their "
        "linked Challonge account and any open ADA request, plus the counts on the screen's "
        "header and which optional columns this community has.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def members_board(actor: User = Depends(require_staff)) -> MembersBoardResponse:
    return MembersBoardResponse.model_validate(await MembersAdminService().board(actor), from_attributes=True)


@router.get(
    '/addable',
    response_model=List[AddableAccountResponse],
    summary='Accounts you can add as members',
    description=(
        "Accounts on the platform that aren't members here yet, for Add a member. Add one with "
        "`POST /users/{id}/membership`.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def addable(actor: User = Depends(require_staff)) -> List[AddableAccountResponse]:
    return [AddableAccountResponse.model_validate(a, from_attributes=True) for a in await MembersAdminService().addable(actor)]


@router.get(
    '/discord-import',
    response_model=DiscordImportPreviewResponse,
    summary='What Import from Discord would do',
    description=(
        "Counts the people in the community's linked Discord server: how many accounts and "
        "memberships an import would create, and who it skips. Fails with 400 when no server "
        "is linked or the bot can't read it.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def discord_import_preview(actor: User = Depends(require_staff)) -> DiscordImportPreviewResponse:
    return DiscordImportPreviewResponse.model_validate(
        await DiscordMemberImportService().preview(actor), from_attributes=True,
    )


@router.post(
    '/discord-import',
    response_model=DiscordImportResultResponse,
    summary='Import members from Discord',
    description=(
        "Adds everyone in the linked Discord server as a member, creating accounts as needed. "
        "No DMs are sent. Bots, people removed from this community and deactivated accounts "
        "are skipped. Safe to run again.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def discord_import_run(actor: User = Depends(require_staff_write)) -> DiscordImportResultResponse:
    return DiscordImportResultResponse.model_validate(
        await DiscordMemberImportService().run(actor), from_attributes=True,
    )


@router.post(
    '/{user_id}/emergency-contact',
    response_model=EmergencyContactViewResponse,
    summary="Open a member's emergency contact",
    description=(
        "Returns the member's emergency contact and logs that you looked, even when they "
        "haven't given one. A POST because every look is recorded. 404 when the community "
        "doesn't collect emergency contacts.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def open_emergency_contact(
    user_id: int, actor: User = Depends(require_staff_write),
) -> EmergencyContactViewResponse:
    member = await load_community_user_or_404(user_id, actor)
    contact = await EmergencyContactService().open_for(actor, member)
    if contact is None:
        return EmergencyContactViewResponse(found=False)
    return EmergencyContactViewResponse(
        found=True, name=contact.name, relationship=contact.relationship,
        phone=contact.phone, updated_at=contact.updated_at,
    )


@router.get(
    '/ada-requests',
    response_model=List[AdaRequestResponse],
    summary='ADA requests',
    description=(
        "Open ADA accommodation requests in this event, oldest first, with what each member "
        "asked for and the staff notes. `include_withdrawn` adds withdrawn ones. 404 when the "
        "community doesn't take ADA requests.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def ada_requests(
    include_withdrawn: bool = Query(False, description="Also list withdrawn requests."),
    actor: User = Depends(require_staff),
) -> List[AdaRequestResponse]:
    rows = await MembersAdminService().ada_requests(actor, include_withdrawn=include_withdrawn)
    return [AdaRequestResponse.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    '/ada-requests/{request_id}',
    response_model=AdaRequestResponse,
    summary='One ADA request',
    description="**Who can call this:** Staff.",
)
async def ada_request(request_id: int, actor: User = Depends(require_staff)) -> AdaRequestResponse:
    return AdaRequestResponse.model_validate(
        await MembersAdminService().ada_request(actor, request_id), from_attributes=True,
    )


@router.put(
    '/ada-requests/{request_id}',
    response_model=AdaRequestResponse,
    summary='Set an ADA request\'s status and notes',
    description=(
        "Saves the status and staff notes together; saving a request the member changed after "
        "it was arranged marks the change reviewed. Fails with 400 for `withdrawn` (only the "
        "member can withdraw) or to reopen a withdrawn request.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update_ada_request(
    request_id: int, body: AdaRequestUpdate, actor: User = Depends(require_staff_write),
) -> AdaRequestResponse:
    await AccommodationService().update_request(actor, request_id, body.status, body.staff_notes)
    return AdaRequestResponse.model_validate(
        await MembersAdminService().ada_request(actor, request_id), from_attributes=True,
    )


@router.post(
    '/{user_id}/message',
    response_model=DetailResponse,
    summary='Send a member a direct message',
    description=(
        "Sends your text to the member as a Discord DM (and their phone, if they get push), "
        "always pinging. Fails with 400 when the text is empty or over 2,000 characters, the "
        "member has no Discord linked, or Discord refuses it.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def message_member(
    user_id: int, body: StaffMessageRequest, actor: User = Depends(require_staff_write),
) -> DetailResponse:
    member = await load_community_user_or_404(user_id, actor)
    await StaffMessageService().send(actor, member, body.message)
    return DetailResponse(detail='Message sent')
