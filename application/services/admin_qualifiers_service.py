"""The app's Admin → Qualifiers screen: every qualifier the viewer runs, and one
qualifier with its review queue first, the top of its leaderboard, and setup.

Screen-shaped reads for the new app over :class:`AsyncQualifierService`, which keeps
every permission check; nothing here writes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Mapping, Optional

from application.feature_flags import requires_feature
from application.services.async_qualifier import AsyncQualifierLiveRaceService, AsyncQualifierService
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_pools import roll_refusal
from application.services.auth_service import AuthService
from application.services.player_home_service import PlayerHomeService
from application.services.user_service import UserService
from models import AsyncQualifier, AsyncQualifierRun, FeatureFlag, User

LEADERBOARD_TOP = 5

_LIVE_RACE_LABELS = {
    'scheduled': 'Scheduled',
    'pending': 'Room open',
    'in_progress': 'Racing',
    'finished': 'Finished',
    'cancelled': 'Cancelled',
}


@dataclass
class QualifierRow:
    id: int
    name: str
    state: str
    state_label: str
    opens_at: Optional[datetime]
    closes_at: Optional[datetime]
    to_review: int
    runs: int


@dataclass
class QualifierBoard:
    qualifiers: List[QualifierRow]
    can_create: bool
    zone: Optional[str]


@dataclass
class QueueCard:
    run_id: int
    runner: str
    runner_id: int
    pool: str
    claimed_seconds: Optional[int]
    measured_seconds: Optional[int]
    implausible: bool
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    permalink_url: Optional[str]
    vod_url: Optional[str]
    other_runs: str
    notes: List[str]
    held_by: Optional[str]
    held_by_me: bool


@dataclass
class BoardLine:
    rank: int
    name: str
    score: float
    slots: str


@dataclass
class SetupSummary:
    pools: int
    seeds: int
    live_races: int
    live_races_open: int
    reviewers: int


@dataclass
class QualifierSettings:
    description: Optional[str]
    event_name: Optional[str]
    runs_per_pool: int
    allowed_reattempts: int
    is_active: bool


@dataclass
class QualifierDetail:
    row: QualifierRow
    settings: QualifierSettings
    queue: List[QueueCard]
    leaderboard: List[BoardLine]
    leaderboard_total: int
    setup: SetupSummary


@dataclass
class PermalinkLine:
    id: int
    url: str
    notes: Optional[str]
    live_race: bool
    par_seconds: Optional[int]


@dataclass
class PoolLine:
    id: int
    name: str
    preset_id: Optional[int]
    preset_name: Optional[str]
    roll_refusal: Optional[str]
    permalinks: List[PermalinkLine]


@dataclass
class LiveRaceLine:
    id: int
    title: str
    pool: str
    status: str
    status_label: str
    racetime_slug: Optional[str]
    has_seed: bool


@dataclass
class Person:
    id: int
    name: str
    username: Optional[str]


@dataclass
class Reviewers:
    reviewers: List[Person]
    members: List[Person]


def _state(q: AsyncQualifier, now: datetime) -> tuple:
    if not q.is_active:
        return 'off', 'Off'
    if q.opens_at and now < q.opens_at:
        return 'upcoming', 'Opens soon'
    if q.closes_at and now >= q.closes_at:
        return 'closed', 'Closed'
    return 'open', 'Open'


def _other_runs(tally: Mapping[int, Mapping[str, int]], run: AsyncQualifierRun) -> str:
    """The runner's other attempts here, so two reviewers judge with the same context.
    The tally counts this run too, so its own outcome is taken back off."""
    counts = dict(tally.get(run.user_id, {}))
    own = 'voided' if run.reattempted else run.review_status.value
    if counts.get(own):
        counts[own] -= 1
    counts = {label: n for label, n in counts.items() if n > 0}
    total = sum(counts.values())
    if not total:
        return ''
    breakdown = ', '.join(f'{n} {label}' for label, n in sorted(counts.items()))
    return f"{total} other run{'' if total == 1 else 's'} here ({breakdown})"


class AdminQualifiersService:
    def __init__(self) -> None:
        self.qualifiers = AsyncQualifierService()
        self.live = AsyncQualifierLiveRaceService()

    async def _row(self, actor: User, q: AsyncQualifier, now: datetime) -> QualifierRow:
        state, label = _state(q, now)
        return QualifierRow(
            id=q.id,
            name=q.name,
            state=state,
            state_label=label,
            opens_at=q.opens_at,
            closes_at=q.closes_at,
            to_review=await self.qualifiers.count_pending_review(actor, q.id),
            runs=await self.qualifiers.count_runs(actor, q.id),
        )

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def board(self, actor: User) -> QualifierBoard:
        now = datetime.now(timezone.utc)
        qualifiers = await self.qualifiers.list_qualifiers(actor)
        rows = [await self._row(actor, q, now) for q in qualifiers]
        order = {'open': 0, 'upcoming': 1, 'closed': 2, 'off': 3}
        rows.sort(key=lambda r: (order[r.state], -r.to_review, r.name.lower()))
        return QualifierBoard(rows, await AuthService.can_admin_qualifier(actor), await PlayerHomeService.viewer_zone(actor))

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def detail(self, actor: User, qualifier_id: int) -> QualifierDetail:
        q = await self.qualifiers.get_qualifier(actor, qualifier_id)
        now = datetime.now(timezone.utc)
        queue = await self.qualifiers.list_review_queue(actor, qualifier_id)
        tally = await self.qualifiers.review_queue_context(actor, qualifier_id, queue)
        cards = []
        for run in queue:
            holder = run.review_claimed_by if rules.claim_is_live(run.review_claimed_at) else None
            cards.append(QueueCard(
                run_id=run.id,
                runner=rules.display_name(run.user),
                runner_id=run.user_id,
                pool=run.permalink.pool.name if run.permalink and run.permalink.pool else '',
                claimed_seconds=run.elapsed_seconds,
                measured_seconds=run.measured_seconds,
                implausible=rules.classify_claim(run.elapsed_seconds or 0, run.measured_seconds)
                is rules.ClaimVerdict.IMPLAUSIBLE,
                started_at=run.started_at,
                finished_at=run.finished_at,
                permalink_url=run.permalink.url if run.permalink else None,
                vod_url=run.runner_vod_url,
                other_runs=_other_runs(tally, run),
                notes=[n.note for n in run.review_notes if n.note],
                held_by=rules.display_name(holder) if holder else None,
                held_by_me=holder is not None and holder.id == actor.id,
            ))
        board = await self.qualifiers.get_leaderboard(actor, qualifier_id)
        pools = await self.qualifiers.list_pools(actor, qualifier_id)
        races = await self.live.list_live_races(actor, qualifier_id)
        reviewers = await self.qualifiers.list_admins(actor, qualifier_id)
        return QualifierDetail(
            row=await self._row(actor, q, now),
            settings=QualifierSettings(q.description, q.event_name, q.runs_per_pool, q.allowed_reattempts, q.is_active),
            queue=cards,
            leaderboard=[
                BoardLine(e.rank, e.username, round(e.actual, 1), f'{e.slots_filled}/{e.slots_total}')
                for e in board[:LEADERBOARD_TOP]
            ],
            leaderboard_total=len(board),
            setup=SetupSummary(
                pools=len(pools),
                seeds=sum(len(p.permalinks) for p in pools),
                live_races=len(races),
                live_races_open=sum(1 for r in races if r.status.value in ('scheduled', 'pending', 'in_progress')),
                reviewers=len(reviewers),
            ),
        )

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def pools(self, actor: User, qualifier_id: int) -> List[PoolLine]:
        pools = await self.qualifiers.list_pools(actor, qualifier_id)
        return [
            PoolLine(
                id=p.id,
                name=p.name,
                preset_id=p.preset_id,
                preset_name=p.preset.name if p.preset else None,
                roll_refusal=roll_refusal(p.preset),
                permalinks=[
                    PermalinkLine(link.id, link.url, link.notes, link.live_race, link.par_time)
                    for link in sorted(p.permalinks, key=lambda link: link.id)
                ],
            )
            for p in pools
        ]

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def live_races(self, actor: User, qualifier_id: int) -> List[LiveRaceLine]:
        races = await self.live.list_live_races(actor, qualifier_id)
        return [
            LiveRaceLine(
                id=r.id,
                title=r.match_title,
                pool=r.pool.name if r.pool else '',
                status=r.status.value,
                status_label=_LIVE_RACE_LABELS.get(r.status.value, r.status.value.replace('_', ' ').capitalize()),
                racetime_slug=r.racetime_slug,
                has_seed=r.permalink_id is not None,
            )
            for r in races
        ]

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def reviewers(self, actor: User, qualifier_id: int) -> Reviewers:
        """The qualifier's own reviewers, and the community's members to add from."""
        current = await self.qualifiers.list_admins(actor, qualifier_id)
        members = await UserService().get_community_people_summaries()
        return Reviewers(
            reviewers=sorted((Person(u.id, rules.display_name(u), u.username) for u in current), key=lambda p: p.name.lower()),
            members=[Person(m.id, m.preferred_name, m.username) for m in members],
        )
