"""The root of the graph and the schema built from it."""

from typing import List, Optional

import strawberry
from strawberry.types import Info

from api.graphql.context import GraphContext
from api.graphql.limits import limit_extensions
from api.graphql.types import Event, UserNode, Viewer
from application.errors import NotFoundError
from application.services import AuthService
from application.services.event_service import EventService
from application.tenant_context import require_tenant_id


@strawberry.type
class Query:
    @strawberry.field(description='Who this token acts as.')
    async def viewer(self, info: Info[GraphContext, None]) -> Viewer:
        ctx = info.context
        roles = await AuthService.get_roles(ctx.actor)
        scopes = ctx.scopes
        return Viewer(
            user=UserNode.of(ctx.actor),
            roles=sorted(roles, key=lambda r: r.value),
            is_staff=await AuthService.is_staff(ctx.actor),
            scopes=sorted(s.value for s in scopes) if scopes is not None else None,
        )

    @strawberry.field(description=(
        'The events you can see: drafts only for staff and the people holding a role in them.'
    ))
    async def events(self, info: Info[GraphContext, None]) -> List[Event]:
        return [Event.of(ref) for ref in await EventService.visible_events(info.context.actor)]

    @strawberry.field(description=(
        "One event by its slug, or the community's default event when the slug is left out. "
        'A draft you cannot see is a not-found.'
    ))
    async def event(self, info: Info[GraphContext, None], slug: Optional[str] = None) -> Optional[Event]:
        tenant_id = require_tenant_id()
        if slug is None:
            ref = await EventService.default_event(tenant_id)
        else:
            ref = await EventService.ref_by_slug(tenant_id, slug)
        if ref is None or not await EventService.can_view(info.context.actor, ref):
            raise NotFoundError(f"Event '{slug}' not found" if slug else 'This community has no default event')
        return Event.of(ref)


class WizzrobeSchema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None) -> None:
        # Strawberry logs every error, a refused field included; only the
        # unexpected ones are worth a Sentry issue, and errors.format_errors
        # logs those.
        return None


schema = WizzrobeSchema(query=Query, extensions=limit_extensions())
