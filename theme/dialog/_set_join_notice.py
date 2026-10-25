"""New match's offer to count a hand-booked game as the next game of a bracket set.

Split out of ``match_dialog`` to keep it under the file-length guideline.

Staff booking another game for two players who already have an unfinished
Challonge or start.gg booking used to get a plain match beside it. Confirming
game 1 then sent that single game to the bracket site as the whole matchup (SG
Live 2026, the SM Map Rando semifinal). The notice names the booking and offers
to join its set, ticked by default; unticking keeps a separate match.
"""

from typing import Callable, Dict, List, Optional

from nicegui import ui

from application.services import BracketSetService
from application.services.bracket_set_service import OpenBracketSet, game_winner
from application.tenant_context import tenant_scope
from application.utils.timezone import format_local_datetime
from theme.banner import banner


def progress(found: OpenBracketSet, names: Dict[int, str]) -> str:
    """``V2Nitro won game 1``, ``1–1``, or ``not played yet``."""
    winners = [w for w in (game_winner(g) for g in found.games) if w is not None]
    if not winners:
        return 'not played yet'
    if len(found.games) == 1:
        return f'{names.get(winners[0], "Someone")} won game 1'
    counts = [*sorted((winners.count(uid) for uid in set(winners)), reverse=True), 0]
    return f'{counts[0]}–{counts[1]} so far'


class SetJoinNotice:
    """The notice under Date and Time, redrawn whenever the tournament or players change."""

    def __init__(self, tenant_id: int, actor, names: Dict[int, str]) -> None:
        self.tenant_id = tenant_id
        self.actor = actor
        self.names = names
        self.found: Optional[OpenBracketSet] = None
        self.checkbox: Optional[ui.checkbox] = None
        self.container = ui.column().classes('w-full gap-1')
        self.container.set_visibility(False)

    async def refresh(self, tournament_id: Optional[int], player_ids: List[int]) -> None:
        found = None
        if tournament_id and self.actor is not None:
            with tenant_scope(self.tenant_id):
                found = await BracketSetService().open_set_for(tournament_id, player_ids, actor=self.actor)
        self.found = found
        self.checkbox = None
        self.container.clear()
        self.container.set_visibility(found is not None)
        if found is None:
            return
        pair = ' and '.join(self.names.get(uid, 'Someone') for uid in sorted(set(player_ids)))
        when = format_local_datetime(found.first.scheduled_at, '%a %H:%M') if found.first.scheduled_at else 'no time set'
        round_name = found.round_label or 'bracket'
        with self.container:
            with banner(
                f'{pair} already have a {round_name} match on {found.site} '
                f'({when}, {progress(found, self.names)}, not confirmed).',
                tone='warning', icon='layers',
            ):
                pass
            self.checkbox = ui.checkbox(
                f'Count this as game {found.next_game} of that set', value=True,
            )
            ui.label(
                f"{found.site} hears the set's result, not one game's. "
                f'Best of {found.best_of_after}.'
            ).classes('text-caption text-grey q-ml-xl')

    def joining(self) -> Optional[int]:
        """Game 1's match id when the new match should join its set, else None."""
        if self.found is None or self.checkbox is None or not self.checkbox.value:
            return None
        return self.found.first.id

    def watch(self, *elements, get_state: Callable[[], tuple]) -> None:
        """Redraw on any change to ``elements``; ``get_state`` gives (tournament_id, player_ids)."""
        from nicegui import background_tasks

        for element in elements:
            element.on(
                'update:model-value',
                lambda: background_tasks.create(self.refresh(*get_state())),
            )
