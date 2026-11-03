"""Racetime bot endpoints — global / platform (super-admin) administration.

A :class:`~models.RacetimeBot` is **global** (no ``tenant`` FK). Reads require
the global ``SUPER_ADMIN`` role; mutations additionally reject read-only tokens.
Responses are always built from ``RacetimeBotService.serialize(bot)`` so the
privileged ``client_secret`` is never serialized.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    ServiceErrorRoute,
    require_super_admin,
    require_super_admin_write,
)
from api.schemas.racetime_bots import (
    RacetimeBotCreateRequest,
    RacetimeBotGrantRequest,
    RacetimeBotGrantResponse,
    RacetimeBotResponse,
    RacetimeBotUpdateRequest,
)
from application.errors import require_found
from application.services import RacetimeBotService
from models import RacetimeBot, User

router = APIRouter(prefix="/racetime-bots", tags=["Racetime bots"], route_class=ServiceErrorRoute)


async def _load_bot_or_404(bot_id: int) -> RacetimeBot:
    """Global load-or-404 (no tenant filter) for a documented 404 on missing ids."""
    return require_found(await RacetimeBot.get_or_none(id=bot_id), "Racetime bot")


@router.get(
    "",
    response_model=List[RacetimeBotResponse],
    summary="List racetime bots",
    description=(
        "Lists every racetime.gg bot on the platform with its connection health. Client "
        "secrets are never returned.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def list_bots(actor: User = Depends(require_super_admin)):
    service = RacetimeBotService()
    bots = await service.list_bots(actor)
    return [service.serialize(b) for b in bots]


@router.get(
    "/active",
    response_model=List[RacetimeBotResponse],
    summary="List active racetime bots",
    description=(
        "Lists the bots that are switched on.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def list_active_bots(actor: User = Depends(require_super_admin)):
    service = RacetimeBotService()
    bots = await service.list_active_bots()
    return [service.serialize(b) for b in bots]


@router.get(
    "/{bot_id}",
    response_model=RacetimeBotResponse,
    summary="Get a racetime bot",
    description=(
        "Returns one bot. The client secret is never returned.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def get_bot(bot_id: int, actor: User = Depends(require_super_admin)):
    await _load_bot_or_404(bot_id)
    service = RacetimeBotService()
    return service.serialize(await service.get_bot(actor, bot_id))


@router.post(
    "",
    response_model=RacetimeBotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a racetime bot",
    description=(
        "Registers a bot for a racetime.gg category. Communities can't use it until they're "
        "granted access. Fails with 400 when the category, name, client id or client secret "
        "is missing, or the category already has a bot.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def create_bot(payload: RacetimeBotCreateRequest, actor: User = Depends(require_super_admin_write)):
    service = RacetimeBotService()
    bot = await service.create_bot(actor, **payload.model_dump())
    return service.serialize(bot)


@router.patch(
    "/{bot_id}",
    response_model=RacetimeBotResponse,
    summary="Update a racetime bot",
    description=(
        "Edits a bot; omitted fields keep their value, and the stored secret is replaced only "
        "when you send a new one. Fails with 400 when the category, name or client id is set "
        "to empty, or the category belongs to another bot.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def update_bot(bot_id: int, payload: RacetimeBotUpdateRequest, actor: User = Depends(require_super_admin_write)):
    await _load_bot_or_404(bot_id)
    service = RacetimeBotService()
    bot = await service.update_bot(actor, bot_id, **payload.model_dump(exclude_unset=True))
    return service.serialize(bot)


@router.delete(
    "/{bot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a racetime bot",
    description=(
        "Removes a bot. Rooms it opened keep their history; tournaments that used it are left "
        "without a bot and can't open rooms until another is set.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def delete_bot(bot_id: int, actor: User = Depends(require_super_admin_write)):
    await _load_bot_or_404(bot_id)
    await RacetimeBotService().delete_bot(actor, bot_id)


@router.post(
    "/{bot_id}/restart",
    response_model=RacetimeBotResponse,
    summary="Restart a bot's connection",
    description=(
        "Drops the bot's racetime.gg connection and reconnects it, for every community using it. "
        "An inactive bot is disconnected and stays off. Fails with 400 when the racetime bot "
        "runtime is switched off on this server.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def restart_bot(bot_id: int, actor: User = Depends(require_super_admin_write)):
    await _load_bot_or_404(bot_id)
    service = RacetimeBotService()
    return service.serialize(await service.restart_connection(actor, bot_id))


@router.get(
    "/{bot_id}/grants",
    response_model=List[RacetimeBotGrantResponse],
    summary="List tenant grants for a bot",
    description=(
        "Lists the communities allowed to use this bot.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def list_grants(bot_id: int, actor: User = Depends(require_super_admin)):
    await _load_bot_or_404(bot_id)
    return await RacetimeBotService().list_grants(actor, bot_id)


@router.post(
    "/{bot_id}/grants",
    response_model=RacetimeBotGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a tenant access to a bot",
    description=(
        "Lets a community use this bot for its tournaments' race rooms. Granting again "
        "re-activates a suspended grant instead of adding a second one.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def grant_tenant(bot_id: int, payload: RacetimeBotGrantRequest, actor: User = Depends(require_super_admin_write)):
    await _load_bot_or_404(bot_id)
    return await RacetimeBotService().grant_tenant(actor, bot_id, payload.tenant_id)


@router.delete(
    "/{bot_id}/grants/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a tenant's access to a bot",
    description=(
        "Stops a community using this bot. Does nothing if the community had no grant.\n\n"
        "**Who can call this:** Super admins only (platform)."
    ),
)
async def revoke_tenant(bot_id: int, tenant_id: int, actor: User = Depends(require_super_admin_write)):
    await _load_bot_or_404(bot_id)
    await RacetimeBotService().revoke_tenant(actor, bot_id, tenant_id)
