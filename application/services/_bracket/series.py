"""Best-of-N series: game numbering, standings, clinch, and the auto-open hold.

A bracket match spans ``best_of`` games and **every game is its own scheduled
``Match``** (:class:`~models.BracketMatchGame`). A best-of-1 is a series with one
game, so this mixin is on the path for every bracket match, not just long ones.

Two rules shape everything here:

* **The bracket match stays OPEN for the whole series** and completes only on the
  clinch, which delegates to :meth:`AdvancementMixin._record_result`. Every
  downstream behaviour — ``winner_to``/``loser_to`` pointer-following, the
  grand-final reset, walkover settling, stage completion — is therefore reused
  byte-for-byte rather than reimplemented per-game.
* **Settling a game is a compare-and-swap.** Two independent paths settle games:
  ``RaceRoomService.record_finish`` on the racetimebot's task, and
  ``advance_if_linked`` on the serial ``discord_queue``. Counting one game's win
  twice would let a Bo3 "clinch" 2-0 off a single game.

``SeriesMixin`` is composed into :class:`BracketService`; its methods reach
siblings, ``self.repository``, and ``self.audit_service`` through that class.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from application.events import EventType
from application.repositories._save import save_fields
from application.services._bracket._composed import ComposedBracketService
from application.services.audit_service import AuditActions
from application.services.bracket_config import validate_best_of
from application.services.notification_catalog import SERIES_GAME_CANCELLED
from application.utils.game_order import GameTimes, game_numbers
from models import (
    Bracket,
    BracketMatch,
    BracketMatchGame,
    BracketMatchGameState,
    Match,
    User,
)

logger = logging.getLogger(__name__)


class SeriesMixin(ComposedBracketService):
    @staticmethod
    def resolve_best_of(bracket: Bracket, bracket_match: BracketMatch) -> int:
        """The series length for one matchup: override → round → stage → 1.

        The stage-wide ``default_best_of`` sits between the per-round value and
        the bare fallback because the per-round editor is derived from the
        generated graph and so cannot be filled in before Start — without a
        stage-wide default, every stage is unavoidably Bo1 for the window in
        which its opening round is announced.

        ``Bracket.config`` is stored raw JSON and predates its schema, so every
        access is ``.get``-defensive rather than trusting the validated shape.
        Round keys are strings and may be negative (losers bracket).
        """
        if bracket_match.best_of is not None:
            return bracket_match.best_of
        config = bracket.config or {}
        rounds = config.get('rounds') or {}
        entry = rounds.get(str(bracket_match.round)) or {}
        return entry.get('best_of') or config.get('default_best_of') or 1

    @staticmethod
    def numbers_by_time(games: Sequence[BracketMatchGame]) -> Dict[int, int]:
        """``game id -> "Game n"`` by the clock, as players see it.

        ``game_number`` is the slot a game was booked into, which says nothing
        about when it's played once games can be booked in any order. This is
        :func:`~application.utils.game_order.game_numbers` over the games'
        matches; a game whose match is gone (called off) sorts last. Needs
        ``match`` prefetched on each game, and falls back to ``game_number``
        when it isn't.
        """
        times = []
        for game in games:
            match = getattr(game, 'match', None) if game.match_id else None  # type: ignore[attr-defined]
            if game.match_id is not None and not isinstance(match, Match):  # type: ignore[attr-defined]
                return {g.id: g.game_number for g in games}
            times.append(GameTimes(
                game.id,
                getattr(match, 'started_at', None),
                getattr(match, 'scheduled_at', None),
                game.game_number,
            ))
        return game_numbers(times)  # type: ignore[return-value]

    @staticmethod
    def wins_needed(best_of: int) -> int:
        """Games one side must win to take a best-of-``best_of`` series."""
        return best_of // 2 + 1

    async def set_best_of(
        self, actor: Optional[User], bracket_match_id: int, best_of: Optional[int]
    ) -> BracketMatch:
        """Override one matchup's series length. Staff-gated.

        Rejected once any game exists: the scheduled games carry "Game 2 of 3" in
        their title, on the Discord event and the race room name, and re-titling
        them mid-series is machinery we would rather not own. Set it before
        scheduling.
        """
        from application.services.auth_service import AuthService

        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        from application.errors import require_found

        bracket_match = require_found(
            await self.repository.get_match(bracket_match_id), "Bracket match"
        )
        validate_best_of(best_of)
        live = await self._live_games(bracket_match_id)
        if live:
            raise ValueError(
                "This match already has scheduled games; cancel them before "
                "changing the series length."
            )
        bracket_match = await self.repository.update_match(
            bracket_match, best_of=best_of,
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.BRACKET_UPDATED,
            {
                'bracket_id': bracket_match.bracket_id,
                'match_id': bracket_match.id,
                'changed': ['best_of'],
                'best_of': best_of,
            },
        )
        return bracket_match

    async def _live_games(self, bracket_match_id: int) -> List[BracketMatchGame]:
        games = await self.repository.list_games(bracket_match_id)
        return [g for g in games if g.state != BracketMatchGameState.CANCELLED]

    async def series_standing(self, bracket_match: BracketMatch) -> Tuple[int, int]:
        """Games won by ``entry1`` and ``entry2`` respectively."""
        return self._standing_from(
            await self.repository.list_games(bracket_match.id), bracket_match
        )

    @staticmethod
    def _standing_from(
        games: Sequence[BracketMatchGame], bracket_match: BracketMatch
    ) -> Tuple[int, int]:
        complete = [g for g in games if g.state == BracketMatchGameState.COMPLETE]
        return (
            sum(1 for g in complete if g.winner_entry_id == bracket_match.entry1_id),
            sum(1 for g in complete if g.winner_entry_id == bracket_match.entry2_id),
        )

    def is_decided(self, bracket_match: BracketMatch, games, best_of: int) -> bool:
        e1, e2 = self._standing_from(games, bracket_match)
        return max(e1, e2) >= self.wins_needed(best_of)

    async def next_game_number(
        self, bracket_match: BracketMatch, best_of: int
    ) -> int:
        """The lowest unused game slot, or raise when the series is full.

        Slots are allocated by the service, never supplied by a caller. A
        cancelled game's number stays taken so a re-schedule can't silently reuse
        it and collide with the audit trail.
        """
        games = await self.repository.list_games(bracket_match.id)
        taken = {g.game_number for g in games}
        for number in range(1, best_of + 1):
            if number not in taken:
                return number
        raise ValueError(
            f"All {best_of} game(s) of this series are already scheduled."
        )

    @staticmethod
    def _winner_from_ranks(
        match: Match, bracket_match: BracketMatch
    ) -> Tuple[Optional[object], bool]:
        """Map a match's rank-1 finisher to its :class:`BracketEntry`.

        Returns ``(winner_entry, forfeit)``. ``forfeit`` is True when exactly one
        player finished and every other has a null rank — what
        ``RaceRoomService._map_results`` produces for a no-show / DQ.
        """
        ranked = [p for p in match.players if p.finish_rank == 1]
        if len(ranked) != 1:
            return None, False
        winner_player = ranked[0]
        others = [p for p in match.players if p.id != winner_player.id]
        forfeit = bool(others) and all(p.finish_rank is None for p in others)
        winner_entry = next(
            (
                entry
                for entry in (bracket_match.entry1, bracket_match.entry2)
                if entry is not None
                and entry.entrant is not None
                and entry.entrant.user_id == winner_player.user_id
            ),
            None,
        )
        return winner_entry, forfeit

    async def settle_game_if_linked(self, match: Match, actor: Optional[User]) -> bool:
        """Record a finished ``Match`` as its series game, then clinch or continue.

        Guard-and-skip: False when there is no actor or the match backs no game.
        True whenever the match is linked, including when it was already settled.
        """
        if actor is None:
            return False
        game = await self.repository.get_game_for_match(match.id)
        if game is None:
            return False
        if game.state != BracketMatchGameState.SCHEDULED:
            return True

        bracket_match = game.bracket_match
        await match.fetch_related('players')
        winner_entry, forfeit = self._winner_from_ranks(match, bracket_match)
        if winner_entry is None:
            return True

        if not await self.repository.settle_game(
            game.id,
            state=BracketMatchGameState.COMPLETE,
            winner_entry_id=winner_entry.id,
            forfeit=forfeit,
        ):
            return True

        bracket = await self._require_bracket(bracket_match.bracket_id)
        details = {
            'bracket_id': bracket.id,
            'match_id': bracket_match.id,
            'game_id': game.id,
            'game_number': game.game_number,
            'winner_entry_id': winner_entry.id,
            'forfeit': forfeit,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_GAME_COMPLETED, details,
            EventType.BRACKET_GAME_COMPLETED,
        )

        best_of = self.resolve_best_of(bracket, bracket_match)
        games = await self.repository.list_games(bracket_match.id)
        e1, e2 = self._standing_from(games, bracket_match)
        if max(e1, e2) >= self.wins_needed(best_of):
            await self._clinch(actor, bracket_match, winner_entry, games, e1, e2)
        else:
            await self.release_next_game(bracket_match)
        return True

    async def _clinch(
        self, actor, bracket_match, winner_entry, games, e1: int, e2: int
    ) -> None:
        """Close out a decided series.

        Cancelling the remaining game rows is the **first** write, before the
        bracket advances or any Match is torn down: it is one cheap update that
        makes the auto-open hold skip those games immediately, closing the race
        with the 60s poll. The teardown I/O comes last, each game isolated, so a
        Discord or racetime failure can't leave the bracket un-advanced.
        """
        completed = [g for g in games if g.state == BracketMatchGameState.COMPLETE]
        pending = [g for g in games if g.state == BracketMatchGameState.SCHEDULED]
        reason = f'series clinched {max(e1, e2)}-{min(e1, e2)}'

        await self._mark_games_cancelled(actor, bracket_match, pending, reason)

        from models import BracketMatchState

        if bracket_match.state == BracketMatchState.OPEN:
            await self._record_result(
                actor, bracket_match.id, winner_entry.id,
                entry1_score=e1, entry2_score=e2,
                forfeit=bool(completed) and all(g.forfeit for g in completed),
            )

        await self._teardown_games(actor, pending, reason)

    async def _mark_games_cancelled(
        self, actor, bracket_match, games: Sequence[BracketMatchGame], reason: str
    ) -> None:
        for game in games:
            game.state = BracketMatchGameState.CANCELLED
            game.cancelled_reason = reason
            await save_fields(game, 'state', 'cancelled_reason')
            await self.audit_service.write_and_publish(
                actor, AuditActions.BRACKET_GAME_CANCELLED,
                {
                    'bracket_id': bracket_match.bracket_id,
                    'match_id': bracket_match.id,
                    'game_id': game.id,
                    'game_number': game.game_number,
                    'reason': reason,
                },
                EventType.BRACKET_GAME_CANCELLED,
            )

    @staticmethod
    async def _teardown_games(
        actor, games: Sequence[BracketMatchGame], reason: str
    ) -> None:
        """Cancel the scheduled ``Match`` behind each unneeded game. Best-effort."""
        from application.services.match.match_service import MatchService

        service = MatchService()
        for game in games:
            if game.match_id is None:
                continue
            try:
                match = await service.get_by_id(game.match_id)
                if match is not None:
                    await service._cancel_match(
                        match, actor, reason=reason, notice=SERIES_GAME_CANCELLED,
                    )
            except Exception:
                logger.exception(
                    'clinch cancel failed for match %s (game %s)',
                    game.match_id, game.id,
                )

    async def cancel_remaining_games(
        self, actor: Optional[User], bracket_match_id: int, reason: str
    ) -> None:
        """Cancel every not-yet-played game of a series and tear down its Matches.

        Called when a result is settled outside the per-game path — staff using
        ``report_result`` / ``override_result`` — so a forced 2-0 can't strand a
        pre-scheduled game 3 with a live Discord event and a room that would
        auto-open for a decided series.
        """
        bracket_match = await self.repository.get_match(bracket_match_id)
        if bracket_match is None:
            return
        games = await self.repository.list_games(bracket_match_id)
        pending = [g for g in games if g.state == BracketMatchGameState.SCHEDULED]
        if not pending:
            return
        await self._mark_games_cancelled(actor, bracket_match, pending, reason)
        await self._teardown_games(actor, pending, reason)

    async def held_match_ids(self, match_ids: Sequence[int]) -> Set[int]:
        """Which of ``match_ids`` must NOT have a room opened for them yet.

        A later game of a series waits for the earlier one to finish: opening
        game 2's room while game 1 is still being raced puts the players in two
        rooms at once. Two queries regardless of how many candidates the worker
        is weighing — the auto-open poll runs this every 60 seconds over a 7-day
        scan window.

        The predicate keys on **``Match.finished_at``, not on game state**. A
        double forfeit, or a race where no entrant maps to a rank-1 finish, never
        produces a COMPLETE game row; keying on game state would deadlock that
        series forever with nothing surfaced anywhere. ``finished_at`` means "the
        earlier game physically ended", which is the actual requirement.

        A prior game whose ``Match`` was deleted (SET_NULL leaves ``match_id``
        null) is treated as **not** blocking — a deliberate fail-open. Failing
        closed there produces a game whose room never opens, silently, every 60
        seconds; the cost of failing open is bounded (one game opening early, and
        a series cannot clinch off a later game alone).
        """
        if not match_ids:
            return set()
        own = await self.repository.games_for_matches(list(match_ids))
        if not own:
            return set()

        by_match = {g.match_id: g for g in own}
        siblings = await self.repository.games_for_series(
            list({g.bracket_match_id for g in own})
        )
        per_series: Dict[int, List[BracketMatchGame]] = {}
        for game in siblings:
            per_series.setdefault(game.bracket_match_id, []).append(game)

        held: Set[int] = set()
        for match_id, game in by_match.items():
            if game.state == BracketMatchGameState.CANCELLED:
                held.add(match_id)
                continue
            for prior in per_series.get(game.bracket_match_id, []):
                if prior.game_number >= game.game_number:
                    continue
                if self._is_outstanding(prior):
                    held.add(match_id)
                    logger.info(
                        'auto-open held for match %s: game %s of the series has '
                        'not finished', match_id, prior.game_number,
                    )
                    break
        return held

    @staticmethod
    def _is_outstanding(game: BracketMatchGame) -> bool:
        if game.state != BracketMatchGameState.SCHEDULED:
            return False
        if game.match_id is None:
            logger.error(
                'series game %s is unresolved but has no scheduled match; '
                'not holding later games', game.id,
            )
            return False
        return game.match is not None and game.match.finished_at is None

    async def series_matches_due(self, window_start, window_end) -> List[Match]:
        """Slipped series games the auto-open poll's own window would miss."""
        return await self.repository.series_matches_due(window_start, window_end)

    async def release_next_game(self, bracket_match: BracketMatch) -> None:
        """Try to open the next game's room now that the previous one ended.

        The 60s poll alone is not enough: its scan window starts at
        ``now - GRACE_MINUTES`` (15 minutes), so a game whose slot slipped further
        than that while the previous game ran long has dropped out of the
        candidate set permanently. Without this push, "defer" would silently mean
        "never open".

        Reuses ``RaceRoomService.auto_open_if_eligible``, which keeps the
        lead-window guard — so this is a no-op when the next game is still
        legitimately far out (the poll opens it at its proper time) and opens
        immediately when its slot has already passed. Best-effort: a racetime
        failure must not abort settling.
        """
        try:
            games = await self.repository.list_games(bracket_match.id)
            nxt = next(
                (
                    g for g in sorted(games, key=lambda g: g.game_number)
                    if g.state == BracketMatchGameState.SCHEDULED
                    and g.match_id is not None
                ),
                None,
            )
            if nxt is None or await self.held_match_ids([nxt.match_id]):
                return

            from application.services.match.match_service import MatchService
            from application.services.race_room_service import RaceRoomService
            from application.services.user_service import UserService

            match = await MatchService().get_by_id(nxt.match_id)
            if match is None:
                return
            await RaceRoomService().auto_open_if_eligible(
                match,
                now=datetime.now(timezone.utc),
                actor=await UserService().get_system_user(),
            )
        except Exception:
            logger.exception(
                'release_next_game failed for bracket match %s', bracket_match.id
            )
