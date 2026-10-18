"""
Bracket sources - the shared contract for brackets that live on another site.

Each external bracket site a tournament can follow (start.gg today; Challonge
next) is one :class:`BracketSource`. Callers that touch "a linked bracket" loop
over :func:`sources` instead of naming a site, so a new site plugs in here
rather than adding another hand-written call at every spot:

* match confirmation calls :func:`on_match_confirmed`,
* My Schedule lists :func:`list_bookable_for_user` and books through
  :func:`schedule`,
* Admin → Tournaments syncs through :func:`sync_tournament` / :func:`sync_all`,
  links and unlinks through :func:`link` / :func:`unlink`, and counts entrants
  still waiting for a member through :func:`unmatched_entrant_counts`.

Challonge is registered for syncing only; its result push and My Schedule rows
still run through ``ChallongeService`` directly until it moves onto the rest of
the contract. Native brackets live in Wizzrobe, so they have nothing to sync.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol
from urllib.parse import urlparse

from application.services.challonge_service import ChallongeService
from application.services.feature_flag_service import FeatureFlagService
from application.services.round_schedule_service import RoundScheduleService
from application.services.startgg_service import StartggService
from models import FeatureFlag, Match, Tournament, User

logger = logging.getLogger(__name__)


@dataclass
class BookableMatch:
    """One bracket matchup a player can book now, whatever site it came from."""

    source: str
    source_label: str
    key: int
    tournament: Tournament
    round_label: str
    opponent_name: str
    player_ids: List[int] = field(default_factory=list)
    # "Book between Sat 6 pm and Sun 11 pm", from the tournament's round schedule.
    window_text: Optional[str] = None


@dataclass
class SyncResult:
    tournament_id: int
    tournament_name: str
    source_label: str
    summary: str
    error: Optional[str] = None


class BracketSource(Protocol):
    key: str
    label: str
    flag: FeatureFlag

    def linked(self, tournament: Tournament) -> bool: ...

    async def sync(self, tournament: Tournament, actor: User) -> str: ...

    async def list_bookable_for_user(self, user: User) -> List[BookableMatch]: ...

    async def schedule(
        self, key: int, scheduled_date: str, scheduled_time: str, actor: User,
    ) -> Match: ...

    async def on_match_confirmed(self, match: Match, actor: Optional[User]) -> None: ...

    async def link(self, tournament: Tournament, id_or_url: str, actor: User) -> str: ...

    async def unlink(self, tournament: Tournament, actor: User) -> None: ...

    async def count_unmatched(self, tournament_ids: List[int]) -> Dict[int, int]: ...


class StartggSource:
    key = 'startgg'
    label = 'start.gg'
    flag = FeatureFlag.STARTGG

    def linked(self, tournament: Tournament) -> bool:
        return bool(tournament.startgg_event_slug)

    async def sync(self, tournament: Tournament, actor: User) -> str:
        counts = await StartggService().sync_event(tournament.id, actor, force=True)
        return f"{counts.get('entrants', 0)} entrants, {counts.get('sets', 0)} matches"

    async def list_bookable_for_user(self, user: User) -> List[BookableMatch]:
        rows = await StartggService().list_unbooked_sets_for_user(user)
        rounds = RoundScheduleService()
        out = []
        for row in rows:
            me_first = row.entrant1.user_id == user.id
            opponent = row.entrant2 if me_first else row.entrant1
            out.append(BookableMatch(
                source=self.key, source_label=self.label, key=row.id,
                tournament=row.tournament,
                round_label=row.round_text or f'Round {row.round}',
                opponent_name=opponent.name,
                player_ids=[row.entrant1.user_id, row.entrant2.user_id],
                window_text=await rounds.describe_for_startgg_set(row),
            ))
        return out

    async def schedule(self, key: int, scheduled_date: str, scheduled_time: str, actor: User) -> Match:
        return await StartggService().schedule_set(key, scheduled_date, scheduled_time, actor)

    async def link(self, tournament: Tournament, id_or_url: str, actor: User) -> str:
        counts = await StartggService().link_event(tournament.id, id_or_url, actor)
        return f"{counts.get('entrants', 0)} entrants, {counts.get('sets', 0)} matches"

    async def unlink(self, tournament: Tournament, actor: User) -> None:
        await StartggService().unlink_event(tournament.id, actor)

    async def count_unmatched(self, tournament_ids: List[int]) -> Dict[int, int]:
        return await StartggService().count_unmatched_entrants(tournament_ids)

    async def on_match_confirmed(self, match: Match, actor: Optional[User]) -> None:
        await StartggService().report_result_if_linked(match, actor)


class ChallongeSource:
    """Sync only for now; see the module docstring."""

    key = 'challonge'
    label = 'Challonge'
    flag = FeatureFlag.CHALLONGE

    def linked(self, tournament: Tournament) -> bool:
        return bool(tournament.challonge_tournament_id)

    async def sync(self, tournament: Tournament, actor: User) -> str:
        counts = await ChallongeService().sync_bracket(tournament.id, actor, force=True)
        return f"{counts.get('participants', 0)} players, {counts.get('matches', 0)} matches"

    async def list_bookable_for_user(self, user: User) -> List[BookableMatch]:
        return []

    async def schedule(self, key: int, scheduled_date: str, scheduled_time: str, actor: User) -> Match:
        raise ValueError('Book Challonge matches from their own row.')

    async def link(self, tournament: Tournament, id_or_url: str, actor: User) -> str:
        await ChallongeService().link_tournament(tournament.id, id_or_url, actor)
        return 'linked and synced'

    async def unlink(self, tournament: Tournament, actor: User) -> None:
        await ChallongeService().unlink_tournament(tournament.id, actor)

    async def count_unmatched(self, tournament_ids: List[int]) -> Dict[int, int]:
        return await ChallongeService().count_unmatched_participants(tournament_ids)

    async def on_match_confirmed(self, match: Match, actor: Optional[User]) -> None:
        return None


_SOURCES: List[BracketSource] = [StartggSource(), ChallongeSource()]


def sources() -> List[BracketSource]:
    return list(_SOURCES)


def by_key(key: str) -> BracketSource:
    for source in _SOURCES:
        if source.key == key:
            return source
    raise ValueError(f'Unknown bracket site: {key}')


async def live_sources() -> List[BracketSource]:
    """The sources whose feature is on in the current community."""
    live = await FeatureFlagService().enabled_flags()
    return [s for s in _SOURCES if s.flag in live]


async def source_for(tournament: Tournament) -> Optional[BracketSource]:
    """The live site this tournament follows, if any."""
    return next((s for s in await live_sources() if s.linked(tournament)), None)


async def on_match_confirmed(match: Match, actor: Optional[User]) -> None:
    """Tell every site about a confirmed result. One site failing never stops the others."""
    for source in await live_sources():
        try:
            await source.on_match_confirmed(match, actor)
        except Exception:
            logger.exception('%s result hook failed for match %s', source.key, match.id)


async def list_bookable_for_user(user: User) -> List[BookableMatch]:
    out: List[BookableMatch] = []
    for source in await live_sources():
        out.extend(await source.list_bookable_for_user(user))
    return out


async def schedule(
    source_key: str, key: int, scheduled_date: str, scheduled_time: str, actor: User,
) -> Match:
    return await by_key(source_key).schedule(key, scheduled_date, scheduled_time, actor)


async def sync_tournament(tournament: Tournament, actor: User) -> SyncResult:
    source = await source_for(tournament)
    if source is None:
        raise ValueError(f"{tournament.name} isn't linked to a bracket site.")
    summary = await source.sync(tournament, actor)
    return SyncResult(tournament.id, tournament.name, source.label, summary)


async def sync_all(tournaments: List[Tournament], actor: User,
                   on_progress: Optional[Callable[[str], None]] = None) -> List[SyncResult]:
    """Sync every linked tournament in the list; a failure is reported, not raised.

    ``on_progress(name)`` is called before each tournament's sync.
    """
    results: List[SyncResult] = []
    for tournament in tournaments:
        source = await source_for(tournament)
        if source is None:
            continue
        if on_progress is not None:
            on_progress(tournament.name)
        try:
            summary = await source.sync(tournament, actor)
            results.append(SyncResult(tournament.id, tournament.name, source.label, summary))
        except (ValueError, PermissionError) as e:
            results.append(SyncResult(tournament.id, tournament.name, source.label, '', error=str(e)))
    return results


_SITE_HOSTS = {'challonge.com': 'challonge', 'start.gg': 'startgg'}


def site_for_link(value: str) -> Optional[str]:
    """The site key a pasted bracket link points at, or None for a bare id."""
    value = (value or '').strip()
    if not value:
        return None
    host = (urlparse(value if '://' in value else f'https://{value}').hostname or '').lower()
    for domain, key in _SITE_HOSTS.items():
        if host == domain or host.endswith('.' + domain):
            return key
    return None


async def link(source_key: str, tournament: Tournament, id_or_url: str, actor: User) -> str:
    """Link ``tournament`` to a bracket on ``source_key`` and mirror it."""
    return await by_key(source_key).link(tournament, id_or_url, actor)


async def unlink(tournament: Tournament, actor: User) -> str:
    """Detach ``tournament`` from whichever live site it follows; returns that site's label."""
    source = await source_for(tournament)
    if source is None:
        raise ValueError(f"{tournament.name} isn't linked to a bracket site.")
    await source.unlink(tournament, actor)
    return source.label


async def unmatched_entrant_counts(tournaments: List[Tournament]) -> Dict[int, int]:
    """Linked-bracket entrants still waiting for a Wizzrobe user, per tournament id.

    A soft read for the Tournaments board: a site that fails just shows no count.
    """
    counts: Dict[int, int] = {}
    for source in await live_sources():
        ids = [t.id for t in tournaments if source.linked(t)]
        if not ids:
            continue
        try:
            counts.update(await source.count_unmatched(ids))
        except Exception:
            logger.warning('Unmatched-entrant count failed for %s', source.label, exc_info=True)
    return counts


def site_labels() -> Dict[str, str]:
    return {s.key: s.label for s in _SOURCES}
