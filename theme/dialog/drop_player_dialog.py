"""Drop a player from a tournament: one choice for every match they still owe.

Opened from the tournament's Players dialog (Remove). It reads the plan first,
so it can say exactly which matches are affected before anything changes.
"""

from typing import Awaitable, Callable, Optional

from nicegui import app, ui

from application.services import TournamentService, get_user_from_session
from models import ForfeitReason, Tournament, User
from theme.banner import banner
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet
from theme.notify import notify_error

_FORFEIT = 'forfeit'
_CALL_OFF = 'call_off'


def _opponent(match, target: User) -> str:
    return next(
        (p.user.preferred_name for p in match.players if p.user_id != target.id), 'their opponent',
    )


class DropPlayerDialog:
    def __init__(
        self, tournament: Tournament, target: User,
        on_done: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.tournament = tournament
        self.target = target
        self.on_done = on_done

    async def open(self) -> None:
        service = TournamentService()
        actor = await get_user_from_session(app.storage.user)
        if actor is None:
            return
        try:
            plan = await service.plan_drop(self.tournament, self.target, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        name = self.target.preferred_name
        matches = plan.head_to_head

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm drop-dialog'):
            mobile_sheet(dialog)
            dialog_header(f'Drop {name}', dialog)
            with ui.column().classes('q-pa-md gap-3 full-width'):
                ui.label(f'From {self.tournament.name}').classes('text-caption text-muted')
                ui.label('Why').classes('drop-dialog__label')
                # url-state: exempt — a form choice inside a dialog
                reason = ui.toggle(
                    {r: r.label for r in ForfeitReason}, value=ForfeitReason.WITHDREW,
                ).props('no-caps unelevated spread toggle-color=primary aria-label="Why"') \
                    .classes('wiz-segmented')

                outcome = None
                if matches:
                    count = len(matches)
                    ui.label(
                        f"Their {count} open {'match' if count == 1 else 'matches'}"
                    ).classes('drop-dialog__label')
                    tally: dict[str, int] = {}
                    for m in matches:
                        who = _opponent(m, self.target)
                        tally[who] = tally.get(who, 0) + 1
                    named = [f'{who} ({n} matches)' if n > 1 else who for who, n in tally.items()]
                    opponents = (', '.join(named[:-1]) + ' and ' + named[-1]) if len(named) > 1 else named[0]
                    outcome = ui.radio({
                        _FORFEIT: 'Opponents win by forfeit',
                        _CALL_OFF: 'Call them off',
                    }, value=_FORFEIT).props('aria-label="What happens to their matches"')
                    explain = ui.label('').classes('text-caption text-muted drop-dialog__explain')

                    def refresh_explain() -> None:
                        explain.text = (
                            f'{opponents} {"advances" if len(tally) == 1 else "advance"}.'
                            if outcome.value == _FORFEIT
                            else 'The matches are deleted and nobody advances.'
                        )
                    outcome.on_value_change(lambda _: refresh_explain())
                    refresh_explain()
                else:
                    ui.label('They have no open matches here.').classes('text-body2 text-muted')
                if plan.group:
                    ui.label(
                        f"They also come off {len(plan.group)} larger "
                        f"{'match' if len(plan.group) == 1 else 'matches'}."
                    ).classes('text-caption text-muted')

                told = f'{name} and each opponent get a DM.' if matches else f'{name} gets a DM.'
                if plan.on_bracket_site:
                    message = (f'{name} is also taken out on Challonge, which forfeits every '
                               f'unplayed bracket match they have. {told}')
                else:
                    message = told
                with banner(message):
                    pass

            async def do_drop() -> None:
                try:
                    result = await service.drop_player(  # style-guide: exempt — this dialog is the confirmation
                        self.tournament, self.target, actor,
                        reason=ForfeitReason(reason.value),
                        forfeit_matches=outcome is None or outcome.value == _FORFEIT,
                    )
                except (ValueError, PermissionError) as e:
                    with dialog:
                        notify_error(e)
                    return
                done = []
                if result.forfeited:
                    done.append(f'{result.forfeited} forfeited')
                if result.called_off:
                    done.append(f'{result.called_off} called off')
                tail = f" ({', '.join(done)})" if done else ''
                ui.notify(f'{name} dropped{tail}.', color='positive')
                dialog.close()
                if self.on_done:
                    await self.on_done()

            with dialog_actions():
                ui.space()
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button(f'Drop {name}', on_click=busy_click('Dropping…', do_drop)).props('color=negative')
        dialog.open()
