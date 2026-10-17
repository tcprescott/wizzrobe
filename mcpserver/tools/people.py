"""People: users, their roles, and match crew.

Gates mirror ``api/routers/users.py`` and ``api/routers/crew.py``, with one
deliberate difference at :func:`list_match_crew` — documented at the tool.
"""

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from api._helpers import load_community_user_or_404
from api.schemas.memberships import JoinRequestResponse
from application.services import (
    AuthService,
    CrewService,
    PlayerAvailabilityService,
    ReportsService,
    TenantMembershipService,
    UserService,
)
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    AvailabilityWindow,
    CrewMemberInfo,
    MatchCrew,
    PlayerAvailability,
    TenantArg,
    UserDetail,
    UserSummary,
)
from mcpserver.tools._args import window as _window

# A scheduling question asks about a squad, not a census. The cap keeps one call
# from turning into a whole-community availability dump.
MAX_AVAILABILITY_USERS = 50


async def list_users(
    tenant: TenantArg = None,
    role: Optional[str] = None,
    has_discord: bool = False,
) -> List[UserSummary]:
    """List the community's users, optionally filtered to holders of one role.

    Requires staff access. `role` is a role name such as `staff` or `proctor`.
    """
    from models import Role

    parsed: Optional[Role] = None
    if role:
        try:
            parsed = Role(role.lower())
        except ValueError as exc:
            valid = ', '.join(r.value for r in Role)
            raise ValueError(f"Unknown role '{role}'. Valid roles: {valid}") from exc
    # The community's members, not the platform's user table — a session
    # resolves one tenant and every other tool here is community-scoped.
    users = await UserService().get_community_people(role=parsed, has_discord=has_discord)
    return [UserSummary.model_validate(u, from_attributes=True) for u in users]


async def get_user(
    user_id: int,
    tenant: TenantArg = None,
) -> UserDetail:
    """Get one user's profile and the roles they hold in this community.

    You may always read your own record; reading anyone else's requires staff
    access and only reaches people who belong to this community.
    """
    actor = current_actor()
    if user_id != actor.user.id and not await AuthService.is_staff(actor.user):
        raise PermissionError('Staff access required to view another user')
    # Scoped, like `list_users` above and the REST route this mirrors: `User` is
    # global, so a bare id lookup let staff of any one community read every
    # account on the platform by counting upwards.
    user = await load_community_user_or_404(user_id, actor.user)
    roles = await AuthService.get_roles(user)
    # Built field by field: `User.roles` is the ORM's reverse relation, which
    # `from_attributes` would try (and fail) to read as the list of role names.
    return UserDetail(
        **UserSummary.model_validate(user, from_attributes=True).model_dump(),
        roles=sorted(r.value for r in roles),
        pronouns=user.pronouns,
        twitch_username=user.twitch_username,
        racetime_username=user.racetime_username,
    )


async def list_match_crew(
    match_id: int,
    tenant: TenantArg = None,
) -> MatchCrew:
    """List a match's commentators and trackers, including pending signups.

    Staff, or an admin or crew coordinator of the match's tournament. `get_match`
    deliberately hides unapproved crew from everyone; this is the approver's
    view of who has actually volunteered, so it sits behind the same gate as
    approving them.
    """
    match = await CrewService().get_match_crew(match_id, current_actor().user)

    def to_info(row) -> CrewMemberInfo:
        return CrewMemberInfo(
            id=row.id,
            user=(
                UserSummary.model_validate(row.user, from_attributes=True)
                if row.user else None
            ),
            approved=row.approved,
            acknowledged_at=getattr(row, 'acknowledged_at', None),
        )

    return MatchCrew(
        match_id=match.id,
        commentators=[to_info(c) for c in match.commentators],
        trackers=[to_info(t) for t in match.trackers],
    )


async def crew_coverage_report(
    start: str,
    end: str,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
    approved_only: bool = False,
) -> Dict[str, Any]:
    """Crew coverage across a window: which matches have commentary and tracking.

    Requires admin access. `start` and `end` are ISO 8601 timestamps.
    """
    start_dt, end_dt = _window(start, end)
    return await ReportsService().crew_coverage(
        start=start_dt, end=end_dt,
        tournament_id=tournament_id, approved_only=approved_only, actor=current_actor().user,
    )


async def get_player_availability(
    start: str,
    end: str,
    tenant: TenantArg = None,
    user_ids: Optional[List[int]] = None,
) -> List[PlayerAvailability]:
    """What players said about playing in a window.

    Availability is **opt-out**: a player is available for any time they have
    not spoken for, so the windows here are the times they *cannot* play
    (`unavailable`) or would rather (`preferred`). An empty list means fully
    available, not unknown — a player is returned with one rather than omitted,
    so you can tell them apart from players you did not ask about.

    `start` and `end` are ISO 8601 timestamps. Defaults to your own windows;
    naming other players requires staff access, matching `get_user`.
    """
    actor = current_actor()
    start_dt, end_dt = _window(start, end)
    wanted = list(dict.fromkeys(user_ids or [actor.user.id]))
    if len(wanted) > MAX_AVAILABILITY_USERS:
        raise ValueError(
            f'user_ids is limited to {MAX_AVAILABILITY_USERS} players per call'
        )
    # Staff-or-self first, so a player naming strangers learns nothing about
    # who belongs here; the membership check then keeps staff inside the wall.
    windows_by_user = await PlayerAvailabilityService().availability_map_as(
        wanted, start_dt, end_dt, actor=actor.user,
    )
    await TenantMembershipService.require_community_users(
        [uid for uid in wanted if uid != actor.user.id], actor.user,
    )

    service = UserService()
    out: List[PlayerAvailability] = []
    for user_id in wanted:
        user = await service.get_user_by_id(user_id)
        out.append(PlayerAvailability(
            user_id=user_id,
            name=user.preferred_name if user else None,
            windows=[
                AvailabilityWindow(
                    starts_at=row.starts_at,
                    ends_at=row.ends_at,
                    status=getattr(row.status, 'value', row.status),
                    note=row.note,
                )
                for row in windows_by_user.get(user_id, [])
            ],
        ))
    return out


async def list_join_requests(tenant: TenantArg = None) -> List[JoinRequestResponse]:
    """Requests to join this community that are waiting on a decision, oldest first.

    Staff only. Decide each with `approve_join_request` or `deny_join_request`.
    """
    return [
        JoinRequestResponse.from_row(r)
        for r in await TenantMembershipService().list_pending_for(current_actor().user)
    ]


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_join_requests, gate=Gate.STAFF, title='List join requests')
    register(mcp, list_users, gate=Gate.STAFF, title='List users')
    register(mcp, get_user, gate=Gate.ACTOR, title='Get user')
    register(
        mcp, get_player_availability, gate=Gate.ACTOR,
        title='Get player availability',
    )
    register(mcp, list_match_crew, gate=Gate.ADMIN, title='List match crew')
    register(mcp, crew_coverage_report, gate=Gate.ADMIN, title='Crew coverage report')
