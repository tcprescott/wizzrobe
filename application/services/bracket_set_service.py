"""Best-of sets on a Challonge or start.gg matchup - Business Logic Layer.

The bracket site keeps a best-of-3 as one match, but every game is played (and
crewed, seeded and staged) on its own, so Wizzrobe books a set as one ``Match``
per game. One game leads the set: it is the booking the bracket site's matchup
links to, exactly as a best-of-1 is, and the others point back at it through
``Match.set_first``. Players book the games one at a time and in any order, so
the lead is just whichever was booked first. Calling it off hands the lead (and
the bracket link) to another game (:meth:`BracketSetService.hand_off`).

A game's number ("Game 2 of 3") isn't stored: it is its place on the clock
among the set's games (:mod:`application.utils.game_order`), so moving a game
moves its number. ``Match.set_game`` only records the order they were booked in.

Three things follow from a set:

* the bracket site only hears the **set's** result, once someone has won
  enough games (:meth:`BracketSetService.standing`), with the game score;
* once the set is decided, the games nobody needs are called off
  (:meth:`BracketSetService.call_off_unneeded`), crew and all;
* until then, the matchup keeps offering its unbooked games
  (:meth:`BracketSetService.open_games`), the ones past a sweep marked "if needed".

How long a stage's sets run is set per stage in the round schedule
(``RoundScheduleService.best_of_for``); the booking dialog can change it for one
matchup.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from application.errors import require_found
from application.repositories.challonge_repository import ChallongeRepository
from application.repositories.match_repository import MatchRepository
from application.repositories.startgg_repository import StartggRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.notification_catalog import SERIES_GAME_CANCELLED
from application.services.round_schedule_service import RoundScheduleService
from application.utils.game_order import GameTimes, game_numbers, remaining_slots
from application.utils.timezone import format_local_date, format_local_time, parse_local_datetime
from models import ChallongeMatchState, Match, StartggSetState, User

logger = logging.getLogger(__name__)

MAX_BEST_OF = 7


@dataclass
class SetStanding:
    """Where a best-of set stands, read off its games' recorded winners."""

    first: Match
    games: List[Match]
    best_of: int
    wins: Dict[int, int] = field(default_factory=dict)

    @property
    def needed(self) -> int:
        return self.best_of // 2 + 1

    @property
    def winner_id(self) -> Optional[int]:
        return next((uid for uid, n in self.wins.items() if n >= self.needed), None)

    @property
    def decided(self) -> bool:
        return self.winner_id is not None

    def score(self, user_id: int) -> int:
        return self.wins.get(user_id, 0)

    def loser_id(self) -> Optional[int]:
        winner = self.winner_id
        players = {p.user_id for g in self.games for p in g.players}  # type: ignore[attr-defined]
        return next((uid for uid in sorted(players) if uid != winner), None)

    def summary(self) -> str:
        """``2–1``: the leader's games first."""
        counts = [*sorted(self.wins.values(), reverse=True), 0, 0]
        return f'{counts[0]}–{counts[1]}'


@dataclass
class OpenGames:
    """What's left to book of one matchup's set."""

    best_of: int
    booked: int
    slots: List[bool]
    first: Optional[Match] = None


@dataclass
class OpenBracketSet:
    """A bracket booking two players haven't finished, which a new game of theirs can join.

    Staff booking another game for the same pair by hand used to make a plain
    match beside it, so confirming game 1 sent that one game to the bracket site
    as the whole matchup. Joining the set keeps the site waiting for the set.
    """

    first: Match
    games: List[Match]
    best_of: int
    site: str
    round_label: Optional[str]

    @property
    def next_game(self) -> int:
        return len(self.games) + 1

    @property
    def best_of_after(self) -> int:
        """The set's length once the next game joins: grown to the next odd number if it must."""
        n = self.next_game
        return max(self.best_of, n if n % 2 else n + 1)


def validate_best_of(best_of: int) -> int:
    if best_of < 1 or best_of % 2 == 0 or best_of > MAX_BEST_OF:
        raise ValueError(f'A set is best of an odd number of games, up to {MAX_BEST_OF}.')
    return best_of


def game_winner(game: Match) -> Optional[int]:
    return next(
        (p.user_id for p in game.players if p.finish_rank == 1),  # type: ignore[attr-defined]
        None,
    )


def is_unplayed(game: Match) -> bool:
    return game.started_at is None and game.finished_at is None and game_winner(game) is None


class BracketSetService:
    def __init__(self) -> None:
        self.repository = MatchRepository()

    async def standing(self, match: Match) -> Optional[SetStanding]:
        """The set ``match`` is a game of, or None when it isn't one."""
        best_of = getattr(match, 'set_best_of', None)
        if not best_of or best_of <= 1:
            return None
        # Read afresh: calling off the lead game hands the set to another one.
        first_id = await self.repository.set_first_id_of(match.id) or match.id
        games = await self.repository.set_games(first_id)
        first = next((g for g in games if g.id == first_id), None)
        if first is None:
            return None
        wins: Dict[int, int] = {}
        for game in games:
            winner = game_winner(game)
            if winner is not None:
                wins[winner] = wins.get(winner, 0) + 1
        return SetStanding(first=first, games=games, best_of=best_of, wins=wins)

    async def call_off_unneeded(self, match: Match) -> List[int]:
        """Call off the games a decided set no longer needs. Returns their match ids.

        Called after every confirmed result, for every match; a no-op unless
        ``match`` is a game of a set that has just been won. Runs as the system
        user, as the native bracket's clinch does, so it doesn't depend on who
        confirmed the deciding game.
        """
        standing = await self.standing(match)
        if standing is None or not standing.decided:
            return []
        unneeded = [g for g in standing.games if is_unplayed(g)]
        if not unneeded:
            return []
        from application.services.match.match_service import MatchService
        from application.services.user_service import UserService

        system = await UserService().get_system_user()
        service = MatchService()
        reason = f'set won {standing.summary()}'
        cancelled: List[int] = []
        for game in unneeded:
            try:
                await service._cancel_match(game, system, reason=reason, notice=SERIES_GAME_CANCELLED)
                cancelled.append(game.id)
            except Exception:
                logger.exception('calling off unneeded set game %s failed', game.id)
        return cancelled

    @staticmethod
    def check_times(times: Sequence[Tuple[str, str]]) -> None:
        """Refuse a set whose game times are missing, unreadable or out of order."""
        validate_best_of(len(times))
        previous = None
        for number, (day, clock) in enumerate(times, start=1):
            if not (day and clock):
                raise ValueError(f'Choose a date and a time for game {number}.')
            try:
                at = parse_local_datetime(day, clock)
            except ValueError as e:
                raise ValueError(f"Game {number}'s date or time doesn't look right.") from e
            if previous is not None and at <= previous:
                raise ValueError(f'Game {number} has to start after game {number - 1}.')
            previous = at

    async def book_followers(
        self, first: Match, times: Sequence[Tuple[str, str]], actor: User,
    ) -> List[Match]:
        """Book games 2 and up of a set whose game 1 is ``first``, with game 1's players.

        ``times`` holds every game's ``(date, time)``, game 1's included, so its
        length is the set's best-of.
        """
        best_of = len(times)
        await self.repository.update(first, set_game=1, set_best_of=best_of)
        return [
            await self._book_one(first, day, clock, actor, number, best_of)
            for number, (day, clock) in enumerate(times[1:], start=2)
        ]

    async def _book_one(
        self, first: Match, day: str, clock: str, actor: User, number: int, best_of: int,
    ) -> Match:
        """One more game of ``first``'s set, with its players (or placeholders)."""
        from application.services.match.match_service import MatchService

        service = MatchService()
        players = [p.user_id for p in await self.repository.get_players(first)]  # type: ignore[attr-defined]
        tournament_id: int = first.tournament_id  # type: ignore[attr-defined]
        if first.bracket_placeholders is not None:
            game = await service.create_match(
                tournament_id=tournament_id, scheduled_date=day, scheduled_time=clock,
                player_ids=players, actor=actor, from_bracket=True,
                placeholders=list(first.bracket_placeholders),
            )
        else:
            game = await service.book_from_bracket(tournament_id, day, clock, players, actor)
        await self.repository.update(game, set_game=number, set_best_of=best_of, set_first=first)
        return game

    @staticmethod
    async def _booked_match_id(source_key: str, key: int) -> Optional[int]:
        """The match a Challonge or start.gg matchup links to, if one is booked."""
        if source_key == 'challonge':
            cm = await ChallongeRepository.get_match(key)
            return cm.match_id if cm is not None else None  # type: ignore[attr-defined]
        row = await StartggRepository.get_set(key)
        return row.match_id if row is not None else None  # type: ignore[attr-defined]

    async def open_games(self, first: Optional[Match], best_of: int) -> OpenGames:
        """What's left to book of a matchup whose set ``first`` leads (None: nothing booked).

        ``best_of`` is the stage's, from the round schedule; once a game is
        booked the set keeps the length it was booked with.
        """
        if first is None:
            return OpenGames(best_of=best_of, booked=0, slots=remaining_slots(best_of, 0, False) or [False])
        best_of = first.set_best_of or 1
        if best_of <= 1:
            return OpenGames(best_of=1, booked=1, slots=[], first=first)
        standing = await self.standing(first)
        booked = len(standing.games) if standing else 1
        decided = bool(standing and standing.decided)
        return OpenGames(
            best_of=best_of, booked=booked,
            slots=remaining_slots(best_of, booked, decided), first=first,
        )

    async def open_games_for(self, source_key: str, key: int, first_id: Optional[int]) -> OpenGames:
        """:meth:`open_games` for a Challonge or start.gg matchup, looked up by its row."""
        from application.services import bracket_sources

        first = (
            await self.repository.get_by_id(first_id, prefetch_relations=False)
            if first_id is not None else None
        )
        best_of = 1 if first is not None else await bracket_sources.best_of_for(source_key, key)
        return await self.open_games(first, best_of)

    async def book_game(
        self, source_key: str, key: int, day: str, clock: str, actor: User,
        *, allow_outside_round: bool = False,
    ) -> Match:
        """Book one game of a Challonge or start.gg matchup's best-of set.

        The first game booked leads the set and takes the bracket link; every
        later one joins it. Refused once every game is booked or the set is won.
        """
        from application.services import bracket_sources

        first_id = await self._booked_match_id(source_key, key)
        if first_id is None:
            best_of = await bracket_sources.best_of_for(source_key, key)
            game = await bracket_sources.schedule(
                source_key, key, day, clock, actor, allow_outside_round=allow_outside_round,
            )
            if best_of > 1:
                await self.repository.update(game, set_game=1, set_best_of=best_of)
            return game
        first = require_found(await self.repository.get_by_id(first_id, prefetch_relations=False), 'Match')
        if not first.set_best_of or first.set_best_of <= 1:
            raise ValueError('This match has already been scheduled.')
        left = await self.open_games(first, first.set_best_of)
        if not left.slots:
            standing = await self.standing(first)
            if standing is not None and standing.decided:
                raise ValueError(f'This set is already decided ({standing.summary()}).')
            raise ValueError('Every game of this set is booked already.')
        try:
            parse_local_datetime(day, clock)
        except ValueError as e:
            raise ValueError("That date or time doesn't look right.") from e
        if not allow_outside_round:
            ref = {'challonge_match_id': key} if source_key == 'challonge' else {'startgg_set_id': key}
            await RoundScheduleService().check_reference(parse_local_datetime(day, clock), actor, **ref)
        return await self._book_one(first, day, clock, actor, left.booked + 1, left.best_of)

    async def hand_off(self, first: Match) -> Optional[Match]:
        """Pass the lead of ``first``'s set to its earliest other game, before ``first`` goes.

        The bracket link moves with it, so calling off one game leaves the rest
        of the set booked. Returns the new lead, or None when ``first`` leads
        no set or is its only game (the matchup is then unbooked again).
        """
        if not getattr(first, 'set_best_of', None) or getattr(first, 'set_first_id', None):
            return None
        others = await self.repository.set_followers(first.id)
        if not others:
            return None
        numbers = game_numbers(
            GameTimes(g.id, g.started_at, g.scheduled_at, g.id) for g in others
        )
        lead = min(others, key=lambda g: numbers[g.id])
        await self.repository.repoint_set(first.id, lead)
        await ChallongeRepository.move_match_link(first.id, lead.id)
        await StartggRepository.move_match_link(first.id, lead.id)
        return lead

    async def extend_booking(
        self, match_id: int, later: Sequence[Tuple[str, str]], actor: User,
        *, allow_outside_round: bool = False,
    ) -> List[Match]:
        """Make a booked best-of-1 bracket match game 1 of a set, and book the rest.

        For a matchup booked before its stage became a best-of: the booking
        keeps its crew, seed, stage and comments, and ``later`` holds the
        ``(date, time)`` of games 2 and up. Returns every game, game 1 first.
        """
        match = require_found(await self.repository.get_by_id(match_id, prefetch_relations=False), 'Match')
        await AuthService.ensure(
            await AuthService.can_crud_match(actor, match),
            "You can't change this match.",
        )
        if getattr(match, 'set_best_of', None) or getattr(match, 'set_first_id', None):
            raise ValueError('This match is already part of a set.')
        if match.started_at is not None or match.finished_at is not None:
            raise ValueError("This match has already started, so it can't become a set.")
        if match.scheduled_at is None:
            raise ValueError("This match has no time, so it can't become a set.")
        ref: dict = {}
        cm = await ChallongeRepository.get_challonge_match_for_match(match)
        if cm is not None:
            ref = {'challonge_match_id': cm.id}
        else:
            sets = await StartggRepository.sets_for_match(match)
            if sets:
                ref = {'startgg_set_id': sets[0].id}
        if not ref:
            raise ValueError("Only a match booked from a Challonge or start.gg bracket can become a set.")
        times = [(format_local_date(match.scheduled_at), format_local_time(match.scheduled_at)), *later]
        self.check_times(times)
        if not allow_outside_round:
            rounds = RoundScheduleService()
            for day, clock in later:
                await rounds.check_reference(parse_local_datetime(day, clock), actor, **ref)
        games = await self.book_followers(match, times, actor)
        await AuditService().write_log(actor, AuditActions.MATCH_SET_EXTENDED, {
            'match_id': match.id, 'best_of': len(times), 'games': [g.id for g in games],
        })
        return [match, *games]

    async def open_set_for(
        self, tournament_id: int, player_ids: Sequence[int], *, actor: User,
    ) -> Optional[OpenBracketSet]:
        """The pair's latest bracket booking in this tournament that a new game could join.

        Only a match that backs a Challonge or start.gg matchup counts, and only
        while the bracket site hasn't heard its result: a best-of-1 that isn't
        confirmed yet, or a set nobody has won. None when there isn't one.
        """
        pair = {uid for uid in player_ids if uid is not None}
        if len(pair) != 2:
            return None
        seen: set = set()
        candidates = await self.repository.unconfirmed_for_player(tournament_id, min(pair))
        for match in sorted(candidates, key=lambda m: (m.scheduled_at is None, m.scheduled_at), reverse=True):
            first_id = getattr(match, 'set_first_id', None) or match.id
            if first_id in seen:
                continue
            seen.add(first_id)
            found = await self._open_set(first_id, pair, actor)
            if found is not None:
                return found
        return None

    async def _open_set(self, first_id: int, pair: set, actor: User) -> Optional[OpenBracketSet]:
        first = await self.repository.get_by_id(first_id, prefetch_relations=False)
        if first is None or not await AuthService.can_crud_match(actor, first):
            return None
        if {p.user_id for p in await self.repository.get_players(first)} != pair:  # type: ignore[attr-defined]
            return None
        best_of = getattr(first, 'set_best_of', None) or 1
        if best_of > 1:
            standing = await self.standing(first)
            if standing is None or standing.decided:
                return None
            games = standing.games
        else:
            if first.confirmed_at is not None:
                return None
            games = await self.repository.set_games(first.id)
        rounds = RoundScheduleService()
        cm = await ChallongeRepository.get_challonge_match_for_match(first)
        if cm is not None:
            if cm.state == ChallongeMatchState.COMPLETE:
                return None
            return OpenBracketSet(first, games, best_of, 'Challonge', await rounds.round_label_for_challonge_match(cm))
        sets = await StartggRepository.sets_for_match(first)
        if sets:
            if sets[0].state == StartggSetState.COMPLETE:
                return None
            return OpenBracketSet(first, games, best_of, 'start.gg', await rounds.round_label_for_startgg_set(sets[0]))
        return None

    async def join_set(self, match_id: int, first_id: int, actor: User) -> Match:
        """Make ``match_id`` the next game of the set ``first_id`` leads, growing the set if it must.

        For a game booked by hand after the matchup's first booking (a stage
        that turned out to be a best-of, or a replay): the bracket site then
        hears the set's result, not game 1's. Works after game 1 is played, as
        long as the bracket site hasn't been told yet.
        """
        match = require_found(await self.repository.get_by_id(match_id, prefetch_relations=False), 'Match')
        await AuthService.ensure(await AuthService.can_crud_match(actor, match), "You can't change this match.")
        if getattr(match, 'set_best_of', None) or getattr(match, 'set_first_id', None):
            raise ValueError('This match is already part of a set.')
        players = {p.user_id for p in await self.repository.get_players(match)}  # type: ignore[attr-defined]
        found = await self._open_set(first_id, players, actor)
        if found is None or found.first.tournament_id != match.tournament_id:  # type: ignore[attr-defined]
            raise ValueError(
                "That bracket match can't take another game: it has other players, "
                "or its result has already gone to the bracket site."
            )
        best_of = validate_best_of(found.best_of_after)
        if best_of != found.best_of:
            for game in found.games:
                await self.repository.update(game, set_best_of=best_of)
        await self.repository.update(found.first, set_game=1, set_best_of=best_of)
        await self.repository.update(match, set_game=found.next_game, set_best_of=best_of, set_first=found.first)
        await AuditService().write_log(actor, AuditActions.MATCH_SET_EXTENDED, {
            'match_id': found.first.id, 'best_of': best_of, 'games': [match.id], 'joined': True,
        })
        return match

    async def followers(self, match: Match) -> List[Match]:
        """Games 2 and up when ``match`` is game 1 of a set, else nothing."""
        if not getattr(match, 'set_best_of', None) or getattr(match, 'set_first_id', None):
            return []
        return await self.repository.set_followers(match.id)

    async def labels_for(self, matches: Sequence[Match]) -> Dict[int, str]:
        """``match id -> "Game 2 of 3"`` for the set games among ``matches``, in one query."""
        in_sets = [m for m in matches if (getattr(m, 'set_best_of', None) or 0) > 1]
        if not in_sets:
            return {}
        firsts = {getattr(m, 'set_first_id', None) or m.id for m in in_sets}
        by_set: Dict[int, List[Match]] = {}
        for game in await self.repository.games_of_sets(sorted(firsts)):
            by_set.setdefault(getattr(game, 'set_first_id', None) or game.id, []).append(game)
        numbers: Dict[int, int] = {}
        for games in by_set.values():
            numbers.update(game_numbers(  # type: ignore[arg-type]
                GameTimes(g.id, g.started_at, g.scheduled_at, g.id) for g in games
            ))
        return {
            m.id: f'Game {numbers[m.id]} of {m.set_best_of}'
            for m in in_sets if m.id in numbers
        }

    async def label_for(self, match: Match) -> Optional[str]:
        """``Game 2 of 3`` for a set's game, else None."""
        return (await self.labels_for([match])).get(match.id)
