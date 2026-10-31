"""The root of the graph and the schema built from it."""

from typing import List, Optional

import strawberry
from strawberry.types import Info

from api.graphql.context import GraphContext
from api.graphql.limits import limit_extensions
from api.graphql.types import Event, MatchNode, StageNode, TournamentNode, UserNode, Viewer
from application.errors import NotFoundError, require_found
from application.event_context import event_scope
from application.services import AuthService, MatchService, StageService, TournamentService
from application.services.event_service import EventService
from application.tenant_context import require_tenant_id
from models import Match, Stage, Tournament


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

    @strawberry.field(description='One tournament by id, in whichever event it belongs to.')
    async def tournament(self, info: Info[GraphContext, None], id: int) -> Optional[TournamentNode]:
        with event_scope(await _row_event(info, Tournament, id)):
            found = await TournamentService().get_for_viewer(info.context.actor, id)
        return TournamentNode.of(require_found(found, 'Tournament'))

    @strawberry.field(description='One stage by id, in whichever event it belongs to.')
    async def stage(self, info: Info[GraphContext, None], id: int) -> Optional[StageNode]:
        with event_scope(await _row_event(info, Stage, id)):
            found = await StageService().get_stage_by_id(id)
        return StageNode.of(require_found(found, 'Stage'))

    @strawberry.field(description='One match by id, in whichever event it belongs to.')
    async def match(self, info: Info[GraphContext, None], id: int) -> Optional[MatchNode]:
        with event_scope(await _row_event(info, Match, id)):
            found = await MatchService().get_by_id(id)
        return MatchNode.of(require_found(found, 'Match'))


async def _row_event(info: Info[GraphContext, None], model: type, row_id: int) -> Optional[int]:
    """The row's own event; a draft the viewer can't open is a not-found."""
    return await EventService.call_event_id(info.context.actor, None, [(model, row_id)])


class WizzrobeSchema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None) -> None:
        # Return None: Strawberry would log refused fields too; errors.format_errors logs the real ones.
        return None


schema = WizzrobeSchema(query=Query, extensions=limit_extensions())
