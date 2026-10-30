"""Dialog for booking a matchup from a linked bracket site (start.gg, Challonge).

The site's bracket decides the tournament and both players, so this only
collects a date and time and books through :mod:`bracket_sources`, which hands
off to that site's service and the usual match-request flow. Same shape as the
Challonge dialog, written once for every site on the shared contract.

Two modes, as in ``bracket_schedule_dialog``: a player books their own matchup
("Schedule vs Bob"); staff pass ``matchup_label`` to book someone else's
("Book Alice vs Bob") from Match Schedule's "Not booked yet" view, and may book
outside the stage's window after a warning.

A stage the round schedule plays as a best-of set books one match per game.
A player books one game at a time: their list has a row per game left, and each
row opens this dialog for a single date and time (``single``). Staff booking a
matchup nobody has booked yet get every game's date and time at once,
prefilled back to back from the tournament's match length, with the games past
the ones a sweep needs marked "If needed", and can change how many games this
matchup plays. Once any game of a set is booked, staff add the rest one at a
time too.
"""

from datetime import datetime, timedelta
from typing import List, Tuple

from nicegui import context, ui

from application.services import bracket_sources
from application.services.bracket_sources import BookableMatch
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.timezone import next_whole_hour_local
from theme.dialog._helpers import (
    FieldError,
    booked_notice,
    booking_window_line,
    dialog_actions,
    dialog_header,
    matchup_line,
    mobile_sheet,
    native_date_input,
    native_time_input,
    render_suggest_time_button,
    submit_on_enter,
)
from theme.dialog.clash_warning import confirm_local_time, window_refusal
from theme.dialog.round_schedule_dialog import confirm_outside_window
from theme.help import resolve_help

_DEFAULT_LENGTH = 90
# Minutes between one game's expected end and the next game's start.
_TURNAROUND = 10
_BEST_OF_CHOICES = (1, 3, 5)


class BracketSourceScheduleDialog:
    def __init__(self, bookable: BookableMatch, actor, on_submit=None, *, matchup_label: str | None = None):
        self.bookable = bookable
        self.actor = actor
        self.on_submit = on_submit
        self.matchup_label = matchup_label
        self.dialog = None

    async def open(self):
        item = self.bookable
        # Captured while the opening handler's slot is alive: on_submit rebuilds
        # the section this dialog was opened from.
        client = context.client
        tenant_id = get_current_tenant_id()
        slot = next_whole_hour_local()
        staff_booking = bool(self.matchup_label)
        best_of = await bracket_sources.best_of_for(item.source, item.key) if item.source != 'bracket' else 1
        set_length = item.best_of if item.best_of > 1 else best_of
        # One game of a set: the player's every booking, and staff's once a game is in.
        single = set_length > 1 and (not staff_booking or item.games_booked > 0)
        if single:
            best_of = 1
        length = int(getattr(item.tournament, 'average_match_duration', None) or _DEFAULT_LENGTH)
        games: List[Tuple] = []
        state = {'best_of': best_of}

        def heading() -> str:
            if single:
                return 'Book a game' if staff_booking else f'Schedule a game vs {item.opponent_name}'
            if state['best_of'] > 1:
                return f"Book best of {state['best_of']}"
            return f'Book {self.matchup_label}' if self.matchup_label else f'Schedule vs {item.opponent_name}'

        def spread_from_first() -> None:
            if len(games) < 2 or not (games[0][0].value and games[0][1].value):
                return
            try:
                at = datetime.strptime(f'{games[0][0].value} {games[0][1].value}', '%Y-%m-%d %H:%M')
            except ValueError:
                return
            for date_in, time_in in games[1:]:
                at += timedelta(minutes=length + _TURNAROUND)
                date_in.value, time_in.value = at.strftime('%Y-%m-%d'), at.strftime('%H:%M')

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            title = dialog_header(
                heading(), dialog, help=await resolve_help(['suggest-time']),
            )
            with ui.column().classes('q-pa-md gap-2'):
                matchup_line(item.tournament.name, item.round_label)
                if self.matchup_label:
                    ui.label(self.matchup_label).classes('text-muted')
                elif best_of > 1:
                    ui.label(f'vs {item.opponent_name}').classes('text-muted')
                booking_window_line(item.window_text)
                if (staff_booking or best_of > 1) and not single:
                    with ui.row().classes('items-center gap-2'):
                        ui.label('Best of').classes('text-caption')
                        # url-state: exempt — a booking choice inside a dialog
                        best_of_toggle = ui.toggle(
                            {n: str(n) for n in _BEST_OF_CHOICES}, value=best_of,
                        ).props('dense unelevated').classes('wiz-segmented')
                        if not staff_booking:
                            best_of_toggle.disable()
                intro = ui.label('').classes('text-caption text-grey-7')
                first_heading = ui.label('Game 1').classes('text-weight-bold')
                with ui.row().classes('form-pair items-center gap-2'):
                    date = native_date_input('Date', slot.strftime('%Y-%m-%d'), required=True)
                    time = native_time_input('Time', slot.strftime('%H:%M'), required=True)
                games.append((date, time))
                error = FieldError(date, time)
                date.on_value_change(lambda _: spread_from_first())
                time.on_value_change(lambda _: spread_from_first())

                @ui.refreshable
                def later_games() -> None:
                    del games[1:]
                    count = state['best_of']
                    intro.text = (
                        f'Best of {set_length}. Each game is its own match, booked one at a time, '
                        'and the schedule numbers them in the order they\'re played.' if single else
                        'Each game is its own match on the schedule.' if count > 1 else
                        'Pick a time for this slot. The players fill in as the bracket decides them, '
                        'and each gets the usual "match scheduled" message then.' if item.ahead
                        else 'Pick a time both players can make.' if self.matchup_label
                        else 'Pick a time you both can play.'
                    )
                    first_heading.set_visibility(count > 1)
                    for number in range(2, count + 1):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(f'Game {number}').classes('text-weight-bold')
                            if number > count // 2 + 1:
                                ui.label('If needed').classes('wiz-chip wiz-chip--pending')
                        with ui.row().classes('form-pair items-center gap-2'):
                            games.append((
                                native_date_input('Date', required=True),
                                native_time_input('Time', required=True),
                            ))
                    spread_from_first()
                    if count > 1:
                        ui.label(
                            'Games nobody needs are called off by themselves once someone wins the set.'
                        ).classes('text-caption text-muted')

                later_games()
                if single and item.if_needed:
                    with ui.row().classes('items-center gap-2'):
                        ui.label('If needed').classes('wiz-chip wiz-chip--pending')
                        ui.label(
                            "Only played if the set isn't won by then. It's called off by itself otherwise."
                        ).classes('text-caption text-muted')
                # Suggest a time reads both players' calendars; a slot booked
                # ahead doesn't have both yet. It fills game 1, and the later
                # games follow it.
                if not item.ahead:
                    render_suggest_time_button(
                        dialog,
                        get_tournament_id=lambda: item.tournament.id,
                        get_player_ids=lambda: item.player_ids,
                        date=date,
                        time=time,
                        missing_message='Unable to suggest a time for this match.',
                        startgg_set_id=item.key if item.source == 'startgg' else None,
                        challonge_match_id=item.key if item.source == 'challonge' else None,
                    )

            async def submit(allow_outside_round: bool = False):
                times = [(d.value or '', t.value or '') for d, t in games]
                first_date, first_time = times[0]
                if not all(day and clock for day, clock in times):
                    error.show('Choose a date and a time.' if len(times) == 1 else 'Choose a date and a time for every game.')
                    return
                if not allow_outside_round:
                    with tenant_scope(tenant_id):
                        refusal = await window_refusal(
                            date_value=first_date, time_value=first_time, actor=self.actor,
                            startgg_set_id=item.key if item.source == 'startgg' else None,
                            challonge_match_id=item.key if item.source == 'challonge' else None,
                            bracket_match_id=item.key if item.source == 'bracket' else None,
                        )
                    if refusal:
                        error.show(refusal)
                        return
                # A "Book anyway" resubmit has already been past the clash sheet.
                if not allow_outside_round and not await confirm_local_time(
                    tournament_id=item.tournament.id, player_ids=item.player_ids,
                    date_value=first_date, time_value=first_time, actor=self.actor,
                ):
                    return
                try:
                    with tenant_scope(tenant_id):
                        if single:
                            await bracket_sources.book_set_game(
                                item.source, item.key, first_date, first_time, self.actor,
                                allow_outside_round=allow_outside_round,
                            )
                        elif len(times) > 1:
                            await bracket_sources.schedule_set(
                                item.source, item.key, times, self.actor,
                                allow_outside_round=allow_outside_round, ahead=item.ahead,
                            )
                        else:
                            book = bracket_sources.book_ahead if item.ahead else bracket_sources.schedule
                            await book(
                                item.source, item.key, first_date, first_time, self.actor,
                                allow_outside_round=allow_outside_round,
                            )
                except (PermissionError, ValueError) as e:
                    with dialog:
                        if confirm_outside_window(e, lambda: submit(True)):
                            return
                    error.show(str(e))
                    return
                with dialog:
                    if len(times) > 1:
                        who = self.matchup_label or f'vs {item.opponent_name}'
                        message = f'Booked {len(times)} games: {who}.'
                    elif single and self.matchup_label:
                        message = f'Booked a game: {self.matchup_label}.'
                    elif self.matchup_label:
                        message = f'Booked {self.matchup_label}.'
                    else:
                        message = booked_notice(
                            item.opponent_name,
                            bool(getattr(item.tournament, 'allow_reschedule_requests', False)),
                        )
                    ui.notify(message, color='positive', multi_line=True)
                    dialog.close()
                if self.on_submit:
                    with client, tenant_scope(tenant_id):
                        await self.on_submit()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                book_button = ui.button(
                    f'Book {best_of} games' if best_of > 1 else 'Schedule', icon='event', on_click=submit,
                ).props('color=primary')

            if (staff_booking or best_of > 1) and not single:
                def change_best_of(e) -> None:
                    state['best_of'] = int(e.value)
                    later_games.refresh()
                    book_button.text = f"Book {state['best_of']} games" if state['best_of'] > 1 else 'Schedule'
                    title.text = heading()

                best_of_toggle.on_value_change(change_best_of)

            submit_on_enter(dialog, submit)
            dialog.open()
