"""One-tap results: who won a live two-player match, recorded and finished together.

The boards' winner buttons call :meth:`MatchReportService.check` the moment
they're tapped, so a refusal shows straight away, then hold
:meth:`MatchReportService.report` behind Undo (``application.pending_actions``).
Whoever may confirm the match confirms it in the same step: confirming your own
result was a second look by the same person.
"""

from dataclasses import dataclass

from application.errors import require_found
from application.services.auth_service import AuthService
from application.services.match.bracket_result_guard import assert_bracket_result_editable
from application.services.match.match_schedule_service import MatchScheduleService
from application.services.match.match_service import MatchService
from application.services.match.match_status import closes_without_winner
from models import Match, User


@dataclass(frozen=True)
class ReportCheck:
    match: Match
    winner_name: str
    confirms: bool


class MatchReportService:
    def __init__(self) -> None:
        self.match_service = MatchService()
        self.schedule_service = MatchScheduleService()

    async def check(self, match_id: int, winner_id: int, actor: User) -> ReportCheck:
        """Everything :meth:`report` would refuse, without writing anything.

        ``winner_id`` is a ``MatchPlayers`` row id.
        """
        match = require_found(await self.match_service.get_by_id(match_id), f'Match {match_id}')
        await AuthService.ensure(
            await AuthService.can_run_match(actor, match),
            f"User cannot record results for match {match_id}",
        )
        players = list(match.players)  # type: ignore[attr-defined]
        if not match.started_at:
            raise ValueError('This match hasn\'t started yet.')
        if match.finished_at:
            raise ValueError('This match is already finished. Open it to change the result.')
        if len(players) != 2 or closes_without_winner(players):
            raise ValueError('Open the match to record a result with more than two players.')
        winner = next((p for p in players if p.id == winner_id), None)
        if winner is None:
            raise ValueError("That player isn't part of this match.")

        from application.services.bracket_service import BracketService
        assert_bracket_result_editable(await BracketService().get_game_for_match(match.id))

        await winner.fetch_related('user')
        return ReportCheck(
            match=match,
            winner_name=winner.user.preferred_name,
            confirms=await AuthService.can_confirm_match(actor, match),
        )

    async def report(self, match_id: int, winner_id: int, actor: User, *, flag: bool = False) -> bool:
        """Record the winner and finish the match; confirm it too where the actor
        may and didn't ``flag`` it for review. True when it was confirmed."""
        checked = await self.check(match_id, winner_id, actor)
        match = await self.match_service.record_match_result(
            match_id=match_id, winner_id=winner_id, actor=actor,
        )
        confirm = checked.confirms and not flag
        await self.schedule_service.finish_match(match, actor=actor, confirm=confirm)
        if flag:
            await self.match_service.flag_for_review(match_id, actor)
        return confirm
