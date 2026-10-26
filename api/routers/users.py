"""User endpoints (read)."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api._helpers import load_community_user_or_404, load_user_or_404
from api.dependencies import (
    ServiceErrorRoute,
    require_api_actor,
    require_staff,
    require_write_actor,
)
from api.schemas.detail import DetailResponse
from api.schemas.member_details import MemberDetailsResponse
from api.schemas.user_actions import (
    RoleRequest,
    TournamentEnrollmentUpdate,
    UserAdminUpdate,
    UserCreateRequest,
    UserProfileUpdate,
    UserSelfUpdate,
)
from api.schemas.users import UserDetailResponse, UserListItem
from application.services import (
    EventService,
    MemberDetailsService,
    TenantMembershipService,
    TournamentService,
    UserService,
)
from application.services.auth_service import AuthService
from models import Role, User

router = APIRouter(prefix="/users", tags=["Users"], route_class=ServiceErrorRoute)


async def _to_detail(user: User) -> UserDetailResponse:
    roles = await AuthService.get_roles(user)
    return UserDetailResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        discord_id=user.discord_id,
        pronouns=user.pronouns,
        is_active=user.is_active,
        dm_notifications=user.dm_notifications,
        roles=sorted(r.value for r in roles),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get(
    "",
    response_model=List[UserListItem],
    summary="List users",
    description=(
        "Returns the members of this community, not every account on the platform. Filter by "
        "a role held here with role.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_users(
    role: Optional[Role] = Query(None, description="Filter to users holding this role in this community"),
    actor: User = Depends(require_staff),
):
    # Community members, not every account on the platform. A token belongs to
    # one tenant, so returning the platform's whole user table was a leak; there
    # is deliberately no ?scope=all, which would re-open it behind a parameter.
    return await UserService().get_community_people(role=role)


@router.get(
    "/me",
    response_model=UserDetailResponse,
    summary="Get the current user",
    description=(
        "Returns the account the token belongs to, with the roles it holds in this community.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_me(actor: User = Depends(require_api_actor)):
    return await _to_detail(actor)


@router.get(
    "/{user_id}",
    response_model=UserDetailResponse,
    summary="Get a user",
    description=(
        "Returns one member of this community, with the roles they hold here. Someone outside "
        "the community reads as not found.\n\n"
        "**Who can call this:** Yourself or Staff."
    ),
)
async def get_user(user_id: int, actor: User = Depends(require_api_actor)):
    if actor.id != user_id and not await AuthService.is_staff(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return await _to_detail(await load_community_user_or_404(user_id, actor))


@router.get(
    "/{user_id}/details",
    response_model=MemberDetailsResponse,
    summary="Get everything about a member",
    description=(
        "The data behind clicking a name on Admin → Members, for troubleshooting: roles, tournaments, "
        "upcoming matches and shifts, how DMs reach them, every message they can get with the community's "
        "setting, their own choice and what they actually get, and the last DMs sent to them here. "
        "Someone outside the community reads as not found. Email is reported as a state, never the "
        "address, and the emergency contact is not included.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def get_member_details(user_id: int, actor: User = Depends(require_staff)):
    return MemberDetailsResponse.build(await MemberDetailsService().get(user_id, actor=actor))


# --- Writes -----------------------------------------------------------------


@router.post(
    "",
    response_model=UserDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user (Staff only)",
    description=(
        "Creates an account and makes it a member of this community, ready for the person to "
        "sign in with Discord. Fails with 400 when the username is blank, a name contains links "
        "or Discord mentions, or the Discord ID already has an account.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def create_user(body: UserCreateRequest, actor: User = Depends(require_write_actor)):
    user = await UserService().create_user(
        username=body.username,
        actor=actor,
        display_name=body.display_name,
        pronouns=body.pronouns,
        discord_id=body.discord_id,
    )
    return await _to_detail(user)


@router.patch(
    "/me",
    response_model=UserDetailResponse,
    summary="Update your own profile",
    description=(
        "Changes only the fields you send. Fails with 400 when a name contains links or Discord "
        "mentions.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def update_me(body: UserSelfUpdate, actor: User = Depends(require_write_actor)):
    await UserService().update_user_personal_info(
        user=actor,
        actor=actor,
        display_name=body.display_name,
        pronouns=body.pronouns,
        dm_notifications=body.dm_notifications,
    )
    return await _to_detail(actor)


@router.patch(
    "/{user_id}",
    response_model=UserDetailResponse,
    summary="Update a user's profile (self or Staff)",
    description=(
        "Changes the person's display name and pronouns, which show in every community they "
        "belong to. So Staff may only change someone who belongs to no other community, and "
        "only a super admin may change a super admin. Fails with 400 when a name contains links "
        "or Discord mentions.\n\n"
        "**Who can call this:** Yourself or Staff."
    ),
)
async def update_user(user_id: int, body: UserProfileUpdate, actor: User = Depends(require_write_actor)):
    target = await load_community_user_or_404(user_id, actor)
    await UserService().update_user_profile(
        user=target, actor=actor, display_name=body.display_name, pronouns=body.pronouns,
    )
    return await _to_detail(target)


@router.patch(
    "/{user_id}/admin",
    response_model=UserDetailResponse,
    summary="Update admin-managed fields (Staff only)",
    description=(
        "Activates or deactivates the account. Deactivating locks the person out of every "
        "community, so Staff may only do it to someone who belongs to no other community; for "
        "anyone else, remove them from this community instead. Only a super admin may change "
        "a super admin.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update_user_admin(user_id: int, body: UserAdminUpdate, actor: User = Depends(require_write_actor)):
    target = await load_community_user_or_404(user_id, actor)
    await UserService().update_user_admin_fields(user=target, actor=actor, is_active=body.is_active)
    return await _to_detail(target)


@router.put(
    "/{user_id}/tournaments",
    response_model=DetailResponse,
    summary="Replace a user's tournament enrollments (self or Staff)",
    description=(
        "Sets the full list of tournaments the person is signed up for, adding and removing as "
        "needed. Staff editing anyone skip the signup window. When you edit your own list, "
        "each change follows the same rules as POST and DELETE /tournaments/{id}/signup, "
        "including the window, a Challonge bracket and the confirmation DM.\n\n"
        "**Who can call this:** Yourself or Staff."
    ),
)
async def update_user_tournaments(
    user_id: int, body: TournamentEnrollmentUpdate, actor: User = Depends(require_write_actor),
):
    if not await AuthService.is_staff(actor):
        if actor.id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
        # A player editing their own list goes through the same signup rules
        # as the Tournaments tab: window, active, Challonge-managed rosters.
        wanted = set(body.tournament_ids)
        current = {
            tp.tournament_id  # type: ignore[attr-defined]
            for tp in await UserService().get_user_tournament_registrations(actor)
        }
        tournaments = TournamentService()
        for tournament_id in sorted(current - wanted):
            await tournaments.self_withdraw(tournament_id, actor)
        for tournament_id in sorted(wanted - current):
            await tournaments.self_enroll(tournament_id, actor)
        return {"detail": "Enrollments updated"}
    target = await load_community_user_or_404(user_id, actor)
    await UserService().manage_tournament_enrollments(
        user=target, actor=actor, tournament_ids=set(body.tournament_ids), is_update=True,
    )
    return {"detail": "Enrollments updated"}


@router.post(
    "/{user_id}/roles",
    response_model=DetailResponse,
    summary="Grant a community role (Staff only)",
    description=(
        "Gives the person a role in this community and makes them a member if they weren't "
        "one. A join request they have waiting is closed as approved, and they get a DM saying "
        "they're in. A per-event role (proctor, volunteer and the other on-site roles) is "
        "granted in `event`, by default the community's default event. super_admin is refused "
        "with 403: it's a platform role, granted only on the platform.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def grant_role(user_id: int, body: RoleRequest, actor: User = Depends(require_write_actor)):
    target = await load_user_or_404(user_id)
    await UserService().grant_role(target, body.role, actor=actor, event_id=await EventService.id_for_slug(body.event))
    return {"detail": f"Granted {body.role.value}"}


@router.delete(
    "/{user_id}/roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a community role (Staff only)",
    description=(
        "Takes a role away in this community. The person stays a member. A per-event role "
        "is taken away in `event`, by default the community's default event. super_admin is "
        "refused with 403: it's a platform role, revoked only on the platform.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def revoke_role(
    user_id: int, role: Role,
    event: Optional[str] = Query(None, description="Event slug, for a per-event role. Default: the default event."),
    actor: User = Depends(require_write_actor),
):
    target = await load_user_or_404(user_id)
    await UserService().revoke_role(target, role, actor=actor, event_id=await EventService.id_for_slug(event))


@router.post(
    "/{user_id}/membership",
    response_model=DetailResponse,
    summary="Add someone to this community (Staff only)",
    description=(
        "Makes the person a member of this community, the same as Add member on the Users tab. "
        "A join request they have waiting is closed as approved, and they get a DM saying "
        "they're in. Adding someone who is already a member changes nothing.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def add_member(user_id: int, actor: User = Depends(require_write_actor)):
    target = await load_user_or_404(user_id)
    await TenantMembershipService().add_member(actor, target)
    return {"detail": "Member added"}


@router.delete(
    "/{user_id}/membership",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove someone from this community (Staff only)",
    description=(
        "Takes the person out of this community. Fails with 400 while they hold a role here or "
        "admin or coordinate a tournament; take those away first.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def remove_member(user_id: int, actor: User = Depends(require_write_actor)):
    target = await load_community_user_or_404(user_id, actor)
    await TenantMembershipService().remove_member(actor, target)
