"""The graph's object types. Each maps a service result, never a raw model
lookup, so the visibility rules the service applies come with it.

An object that owns event-scoped children keeps its ``event_id`` and resolves
them inside ``event_scope`` of it: sibling fields resolve concurrently, so a
contextvar a parent set would never reach them.
"""

from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Annotated, List, Optional

import strawberry
from strawberry.types import Info

from api.graphql.context import GraphContext
from api.graphql.limits import MAX_PAGE
from api.graphql.pagination import decode_cursor, encode_cursor
from api.schemas.bracket_overview import TournamentBracketResponse
from application.event_context import event_scope
from application.services import BracketOverviewService, MatchService, StageService, TournamentService
from application.services.event_service import EventRef
from models import EventStatus, GeneratedSeeds, Match, MatchPlayers, Role, Stage, Tournament, User

if TYPE_CHECKING:
    from api.graphql.brackets import TournamentBracket

strawberry.enum(EventStatus)
strawberry.enum(Role)

MATCHES_HELP = (
    'Scheduled matches, soonest first. Unscheduled matches are left out. '
    '`first` is at most 100 (default 50); pass the last `endCursor` as `after` for the next page.'
)


async def _match_page(
    event_id: int,
    *,
    first: int,
    after: Optional[str],
    tournament_ids: Optional[List[int]] = None,
    stage_ids: Optional[List[int]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> 'MatchConnection':
    if first > MAX_PAGE:
        raise ValueError(f'first can be at most {MAX_PAGE}')
    with event_scope(event_id):
        rows, more = await MatchService().schedule_page(
            first=first, after=decode_cursor(after), tournament_ids=tournament_ids,
            stage_ids=stage_ids, start=start, end=end,
        )
    edges = [MatchEdge(cursor=encode_cursor(m.scheduled_at, m.id), node=MatchNode.of(m)) for m in rows]
    return MatchConnection(
        edges=edges,
        nodes=[e.node for e in edges],
        page_info=PageInfo(has_next_page=more, end_cursor=edges[-1].cursor if edges else None),
    )


@strawberry.type(name='User', description='A person, as any member of the community sees them.')
class UserNode:
    id: int
    username: str
    display_name: Optional[str]
    pronouns: Optional[str]
    discord_id: Optional[str] = strawberry.field(
        description='Too large for a JSON number, so sent as a string.',
    )

    @classmethod
    def of(cls, user: User) -> 'UserNode':
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            pronouns=user.pronouns,
            discord_id=str(user.discord_id) if user.discord_id is not None else None,
        )


@strawberry.type(description='Who this token acts as, in the community it belongs to.')
class Viewer:
    user: UserNode
    roles: List[Role] = strawberry.field(description='Roles held in this community.')
    is_staff: bool
    scopes: Optional[List[str]] = strawberry.field(
        description="An app grant's scopes. Null for a personal token, which is unscoped.",
    )


@strawberry.type(name='Stage', description='A stream or setup that matches are played on.')
class StageNode:
    id: int
    name: str
    stream_url: Optional[str]
    is_active: bool
    event_id: strawberry.Private[int]

    @classmethod
    def of(cls, stage: Stage) -> 'StageNode':
        return cls(
            id=stage.id, name=stage.name, stream_url=stage.stream_url,
            is_active=stage.is_active, event_id=stage.event_id,
        )

    @strawberry.field(description=MATCHES_HELP)
    async def matches(
        self, first: int = 50, after: Optional[str] = None,
        start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> 'MatchConnection':
        return await _match_page(self.event_id, first=first, after=after, stage_ids=[self.id], start=start, end=end)


@strawberry.type(name='Tournament', description="One of the event's tournaments.")
class TournamentNode:
    id: int
    name: str
    short_name: Optional[str] = strawberry.field(description='Shown where the full name will not fit.')
    description: Optional[str] = strawberry.field(description='Shown to players, as markdown.')
    format: Optional[str] = strawberry.field(description='Free-text format label, such as "Double elimination".')
    is_active: bool = strawberry.field(description='Inactive tournaments are only listed for admins.')
    is_test: bool = strawberry.field(description='A test tournament, left off the event guide.')
    players_per_match: int
    seed_generator: Optional[str] = strawberry.field(description='The randomizer it rolls seeds with, if any.')
    bracket_url: Optional[str]
    rules_url: Optional[str]
    average_match_duration: Optional[int] = strawberry.field(description='Typical match length, in minutes.')
    max_match_duration: Optional[int] = strawberry.field(description='Longest a match is expected to run, in minutes.')
    event_id: strawberry.Private[int]

    @classmethod
    def of(cls, t: Tournament) -> 'TournamentNode':
        return cls(
            id=t.id, name=t.name, short_name=t.short_name, description=t.description,
            format=t.tournament_format, is_active=t.is_active, is_test=t.is_test,
            players_per_match=t.players_per_match, seed_generator=t.seed_generator,
            bracket_url=t.bracket_url, rules_url=t.rules_url,
            average_match_duration=t.average_match_duration, max_match_duration=t.max_match_duration,
            event_id=t.event_id,
        )

    @strawberry.field(description=MATCHES_HELP)
    async def matches(
        self, first: int = 50, after: Optional[str] = None,
        start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> 'MatchConnection':
        return await _match_page(
            self.event_id, first=first, after=after, tournament_ids=[self.id], start=start, end=end,
        )

    @strawberry.field(description=(
        'The whole bracket, from whichever site runs it: Wizzrobe, start.gg or Challonge. '
        '`state` is pending, open or complete; `entrant` keeps the sets a name plays in. '
        'Null with a NOT_FOUND error when the community has that bracket source turned off.'
    ))
    async def bracket(
        self, info: Info[GraphContext, None],
        phase: Optional[str] = None, pool: Optional[str] = None,
        state: Optional[str] = None, entrant: Optional[str] = None,
    ) -> Optional[Annotated['TournamentBracket', strawberry.lazy('api.graphql.brackets')]]:
        from api.graphql.brackets import TournamentBracket

        with event_scope(self.event_id):
            view = await BracketOverviewService().get_tournament_bracket(
                info.context.actor, self.id, phase=phase, pool=pool, state=state, entrant=entrant,
            )
        return TournamentBracket.from_pydantic(  # type: ignore[attr-defined]
            TournamentBracketResponse.model_validate(view),
        )


@strawberry.type(description='A rolled seed.')
class Seed:
    url: str
    info: Optional[str] = strawberry.field(description='Extra text the randomizer returned, such as a hash.')
    randomizer: Optional[str]
    rolled_at: datetime

    @classmethod
    def of(cls, seed: GeneratedSeeds) -> 'Seed':
        return cls(url=seed.seed_url, info=seed.seed_info, randomizer=seed.randomizer, rolled_at=seed.created_at)


@strawberry.type(description="A player's seat in a match.")
class MatchPlayer:
    id: int = strawberry.field(description="The seat's id, not the user's.")
    user: UserNode
    finish_rank: Optional[int] = strawberry.field(description='1 for the winner; null before a result.')
    station: Optional[str]

    @classmethod
    def of(cls, p: MatchPlayers) -> 'MatchPlayer':
        return cls(id=p.id, user=UserNode.of(p.user), finish_rank=p.finish_rank, station=p.assigned_station)


@strawberry.enum
class CrewRole(Enum):
    COMMENTATOR = 'commentator'
    TRACKER = 'tracker'
    BROADCASTER = 'broadcaster'


@strawberry.type(description='An approved crew member. Pending signups are never listed.')
class CrewMember:
    role: CrewRole
    user: UserNode


@strawberry.type(name='Match', description='One scheduled match.')
class MatchNode:
    id: int
    title: Optional[str] = strawberry.field(description='Custom title; null means tournament and players.')
    scheduled_at: Optional[datetime]
    seated_at: Optional[datetime] = strawberry.field(description='When the players checked in.')
    finished_at: Optional[datetime]
    needs_review: bool = strawberry.field(description='A proctor flagged the result for an admin to check.')
    comment: Optional[str]
    tournament: TournamentNode
    stage: Optional[StageNode]
    seed: Optional[Seed]
    players: List[MatchPlayer]
    crew: List[CrewMember]

    @classmethod
    def of(cls, m: Match) -> 'MatchNode':
        return cls(
            id=m.id, title=m.title, scheduled_at=m.scheduled_at, seated_at=m.seated_at,
            finished_at=m.finished_at, needs_review=m.needs_review, comment=m.comment,
            tournament=TournamentNode.of(m.tournament),
            stage=StageNode.of(m.stage) if m.stage is not None else None,
            seed=Seed.of(m.generated_seed) if m.generated_seed is not None else None,
            players=[MatchPlayer.of(p) for p in m.players],  # type: ignore[attr-defined]
            crew=[CrewMember(role=CrewRole(role), user=UserNode.of(u)) for role, u in MatchService.approved_crew(m)],
        )


@strawberry.type
class PageInfo:
    has_next_page: bool
    end_cursor: Optional[str] = strawberry.field(description='Pass as `after` for the next page.')


@strawberry.type
class MatchEdge:
    cursor: str
    node: MatchNode


@strawberry.type(description='A page of matches. `nodes` and `edges` hold the same matches.')
class MatchConnection:
    edges: List[MatchEdge]
    nodes: List[MatchNode]
    page_info: PageInfo


@strawberry.type(description="One of the community's events, such as an SG Live weekend.")
class Event:
    id: int
    slug: str
    name: str
    status: EventStatus
    is_default: bool = strawberry.field(description='The event a query without a slug uses.')
    starts_on: Optional[date]
    ends_on: Optional[date]

    @classmethod
    def of(cls, ref: EventRef) -> 'Event':
        return cls(
            id=ref.id, slug=ref.slug, name=ref.name, status=ref.status,
            is_default=ref.is_default, starts_on=ref.starts_on, ends_on=ref.ends_on,
        )

    @strawberry.field(description='Inactive tournaments are only listed for admins.')
    async def tournaments(self, info: Info[GraphContext, None], active_only: bool = False) -> List[TournamentNode]:
        with event_scope(self.id):
            rows = await TournamentService().list_for_viewer(info.context.actor, active_only=active_only)
        return [TournamentNode.of(t) for t in rows]

    @strawberry.field
    async def stages(self, active_only: bool = False) -> List[StageNode]:
        with event_scope(self.id):
            rows = await StageService().get_all_stages(active_only=active_only)
        return [StageNode.of(s) for s in rows]

    @strawberry.field(description=MATCHES_HELP)
    async def matches(
        self, first: int = 50, after: Optional[str] = None,
        start: Optional[datetime] = None, end: Optional[datetime] = None,
        tournament_ids: Optional[List[int]] = None, stage_ids: Optional[List[int]] = None,
    ) -> 'MatchConnection':
        return await _match_page(
            self.id, first=first, after=after, tournament_ids=tournament_ids,
            stage_ids=stage_ids, start=start, end=end,
        )
