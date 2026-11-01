"""Calls about the token's owner that span communities.

Wizzrobe's own app signs in once and picks a community per call, so it needs
the list to pick from before it can name one. Personal tokens may call this
too; a third-party app's grant may not (see ``require_identity``).
"""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_identity
from api.schemas.me import MyCommunity, MyCommunityEvent
from application.services import AuthService, EventService
from application.services.tenant_service import TenantService
from application.tenant_context import tenant_scope
from models import User

router = APIRouter(prefix='/me', tags=['Users'], route_class=ServiceErrorRoute)


@router.get(
    '/communities',
    response_model=List[MyCommunity],
    summary='List my communities',
    description=(
        "The active communities you belong to, with your roles and the events you can open in "
        "each. A platform admin gets every active community. Needs no community header.\n\n"
        "**Who can call this:** Any signed-in person, with Wizzrobe's own app or a personal token."
    ),
)
async def my_communities(actor: User = Depends(require_identity)) -> List[MyCommunity]:
    out: List[MyCommunity] = []
    for tenant in await TenantService.communities_for(actor):
        with tenant_scope(tenant.id):
            roles = await AuthService.get_roles(actor)
            events = await EventService.visible_events(actor)
        out.append(MyCommunity(
            slug=tenant.slug,
            name=tenant.name,
            roles=sorted(role.value for role in roles),
            events=[
                MyCommunityEvent(
                    slug=e.slug, name=e.name, status=getattr(e.status, 'value', str(e.status)),
                    is_default=e.is_default, starts_on=e.starts_on, ends_on=e.ends_on,
                )
                for e in events
            ],
        ))
    return out
