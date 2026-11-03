"""The Discord Roles from Wizzrobe pane of Admin → Event settings.

Each call goes through ``DiscordRoleSyncService``, the same one the website's
pane uses, so checks, audit rows and the sync itself match. Rules belong to the
event ``?event=`` names (the default event when left out). 404 when the
community hasn't turned on Discord role sync.
"""

from typing import Optional

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, event_binding, require_feature, require_staff, require_staff_write
from api.schemas.discord_role_sync import (
    RoleSyncLine,
    RoleSyncOverviewResponse,
    RoleSyncResultResponse,
    RoleSyncRole,
    RoleSyncRuleUpdate,
    RoleSyncUnreachable,
)
from application.services import DiscordRoleSyncService
from application.services.discord.discord_role_sync_service import NO_DISCORD, RoleSyncResult
from models import FeatureFlag, User

router = APIRouter(
    prefix="/event-settings/discord-roles",
    tags=["Event settings"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding()), Depends(require_feature(FeatureFlag.DISCORD_ROLE_SYNC))],
)

STAFF_ONLY = "**Who can call this:** Staff."


def _result(result: RoleSyncResult) -> RoleSyncResultResponse:
    return RoleSyncResultResponse(
        added=result.added, removed=result.removed, in_place=result.in_place, error=result.error,
    )


def _snowflake(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if not value.isdigit():
        raise ValueError("That isn't a Discord role id")
    return int(value)


@router.get(
    "",
    response_model=RoleSyncOverviewResponse,
    summary="Get the Discord roles Wizzrobe gives",
    description=(
        "The last sync, the server's roles with any reason the bot can't give one, and every line a role "
        "can be set on: players, badges (while check-in is on) and this event's tournaments. Asks Discord "
        "for the roles, so it can take a moment.\n\n" + STAFF_ONLY
    ),
)
async def overview(actor: User = Depends(require_staff)):
    data, lines = await DiscordRoleSyncService().checklist(actor)
    state = data.state
    return RoleSyncOverviewResponse(
        last_synced_at=state.last_synced_at if state else None,
        last_error=state.last_error if state else None,
        in_place=state.in_place if state else 0,
        unreachable=[
            RoleSyncUnreachable(
                name=str(p.get('name') or ''),
                reason='no_discord' if p.get('reason') == NO_DISCORD else 'not_in_server',
            )
            for p in ((state.unreachable or []) if state else [])
        ],
        roles=[
            RoleSyncRole(id=str(r.id), name=r.name, color=f'#{r.color:06x}' if r.color else None, problem=r.problem)
            for r in data.roles
        ],
        roles_error=data.roles_error,
        lines=[
            RoleSyncLine(
                group=line.group, kind=line.kind, tournament_id=line.tournament_id, lanyard=line.lanyard,
                label=line.label, caption=line.caption, active=line.active,
                discord_role_id=str(line.rule.discord_role_id) if line.rule else None,
                discord_role_name=line.rule.discord_role_name if line.rule else None,
                holding=line.rule.holding if line.rule else None,
                problem=line.rule.problem if line.rule else None,
            )
            for line in lines
        ],
    )


@router.put(
    "/rules",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set the role for one line",
    description=(
        "Gives a Discord role to everyone the line covers, or turns the line off with a null role (Wizzrobe "
        "takes the role back on the next sync). The role is checked with Discord first: 400 when the bot "
        "can't give it, with the reason. Recorded in the audit log.\n\n" + STAFF_ONLY
    ),
)
async def set_rule(body: RoleSyncRuleUpdate, actor: User = Depends(require_staff_write)):
    await DiscordRoleSyncService().set_rule(
        actor, body.kind, _snowflake(body.discord_role_id),
        tournament_id=body.tournament_id, lanyard=body.lanyard,
    )


@router.post(
    "/sync",
    response_model=RoleSyncResultResponse,
    summary="Sync Discord roles now",
    description="Runs the sync now instead of waiting for the next one, and says what it did.\n\n" + STAFF_ONLY,
)
async def sync_now(actor: User = Depends(require_staff_write)):
    return _result(await DiscordRoleSyncService().sync_now(actor))


@router.post(
    "/clear",
    response_model=RoleSyncResultResponse,
    summary="Remove every role Wizzrobe gave",
    description=(
        "Turns every line off and takes back every Discord role Wizzrobe gave, now. Roles a moderator "
        "handed out by hand stay. Recorded in the audit log.\n\n" + STAFF_ONLY
    ),
)
async def clear_all(actor: User = Depends(require_staff_write)):
    return _result(await DiscordRoleSyncService().clear_all(actor))
