"""Admin Async Qualifiers — the Live Races tab.

A live race runs one pool permalink synchronously on racetime and captures each
entrant's finish as an approved, par-scored run. It is the one tab backed by a
different service (:class:`AsyncQualifierLiveRaceService`) and the one that talks
to an external system, so it lives apart from the self-paced tabs.

Built as a factory rather than plain functions: the page owns ``state`` and the
tab loaders, and this tab needs to read the former and trigger the latter. The
factory takes those as parameters and returns the refreshable view — the same
closure-over-page-state shape the sibling tabs have inline, just in its own file.
"""

from typing import Any, Awaitable, Callable, Optional, Sequence

from nicegui import ui

from application.services.async_qualifier.async_qualifier_live_race_service import ManualResult
from application.utils.duration import parse_hms
from models import AsyncQualifierRunStatus
from pages.admin_tabs.admin_qualifiers.shared import (
    BOARD_TAB,
    LIVE_TAB,
    RUNS_TAB,
    enum_value,
    live_race_color,
    live_race_label,
)
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, more_menu

# The three outcomes a human may assert, worded as a person would say them rather
# than as the enum spells them.
_FINISHED = 'Finished'
_OUTCOMES = [_FINISHED, 'Forfeited', 'Disqualified']
_OUTCOME_STATUS = {
    _FINISHED: AsyncQualifierRunStatus.FINISHED,
    'Forfeited': AsyncQualifierRunStatus.FORFEIT,
    'Disqualified': AsyncQualifierRunStatus.DISQUALIFIED,
}


def missing_racer_message(positions: Sequence[int]) -> Optional[str]:
    """Refusal naming the record-dialog rows with no racer picked, or ``None`` if all are.

    A blank row used to be skipped silently and the remaining rows reported as a
    success, so a racer the admin meant to record just went missing. Module-level
    and pure so the wording is testable without building the dialog.
    """
    if not positions:
        return None
    which = ', '.join(str(p) for p in positions)
    if len(positions) == 1:
        return f'No racer picked on row {which}. Choose one, or remove the row.'
    return f'No racer picked on rows {which}. Choose them, or remove the rows.'


def build_live_tab(
    *,
    state: dict,
    service: Any,
    current: Callable[[], Awaitable[Any]],
    reload_tabs: Callable[..., Awaitable[None]],
    placeholder: Callable[[str], bool],
    notify_error: Callable[[Exception], None],
) -> Callable[[], None]:
    """Return the refreshable Live Races view, wired to the page's state and loaders."""

    @ui.refreshable
    def live_view() -> None:
        if state.get('shell') is None or placeholder(LIVE_TAB):
            return
        pools = state['pools']
        with ui.row().classes('items-center'):
            ui.button('New Live Race', icon='add',
                      on_click=lambda: _open_live_race_dialog(pools)
                      ).props('color=primary').classes('wiz-write')
        ui.label(
            'A live race runs a pool permalink synchronously on racetime; each '
            'entrant\'s result is captured as an approved, par-scored run.'
        ).classes('text-caption text-grey')
        if not pools:
            empty_panel('Add a pool first, then schedule a live race for it.', icon='flag')
            return
        if not state['live_races']:
            empty_panel("No live races scheduled. Start one when you're ready.", icon='flag')
        with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
            for lr in state['live_races']:
                with ui.card():
                    with ui.row().classes('items-center full-width'):
                        ui.label(lr.match_title).classes('text-subtitle1')
                        ui.badge(live_race_label(lr.status), color=live_race_color(lr.status))
                        ui.badge(f'pool: {lr.pool.name}', color='grey')
                        ui.space()
                        cancelled = enum_value(lr.status) == 'cancelled'
                        can_open = not lr.racetime_slug and not cancelled
                        # The next step is the one button; the step after it waits in ⋮.
                        # A cancelled race is over: offering to open a room for it invites
                        # a click that only produces a refusal.
                        if can_open:
                            ui.button('Open room', icon='meeting_room',
                                      on_click=busy_click('Opening room…', lambda lid=lr.id: _open_room(lid))
                                      ).props('flat color=primary')
                        elif not cancelled:
                            _result_action(lr, as_button=True)
                        if not cancelled:
                            with more_menu(lr.match_title):
                                if can_open:
                                    _result_action(lr, as_button=False)
                                    ui.separator()
                                menu_action('cancel', 'Cancel race',
                                            lambda race=lr: _cancel_live_race(race), negative=True)
                    if lr.racetime_slug:
                        ui.label(f'racetime: {lr.racetime_slug}').classes('text-caption text-grey')
                    if lr.permalink_id is None:
                        ui.label('No permalink assigned, so results cannot be scored. '
                                 'Set one before the race runs.'
                                 ).classes('text-caption text-orange')
                    # The to-do the audit log used to keep to itself: these racers came out
                    # of the room with no run, and nothing said so.
                    if lr.unmatched_handles:
                        with ui.column().classes('gap-0'):
                            with ui.row().classes('items-center text-caption text-orange'):
                                ui.icon('person_off')
                                ui.label(f'{len(lr.unmatched_handles)} racer(s) could not be '
                                         'recorded: ' + ', '.join(lr.unmatched_handles))
                            # Two causes, and the admin cannot tell them apart from the
                            # handle alone: no linked account, or racetime calling them
                            # finished without sending a time. Both remedies named.
                            # Only the racer can link their own racetime account
                            # (Profile → Connected accounts); no admin screen does it.
                            ui.label("Either they haven't linked racetime.gg here (each racer "
                                     'does that from their own Profile; then record the race '
                                     'again), or racetime finished them without a time. '
                                     'Record results lets you type it in for either.'
                                     ).classes('text-caption text-grey')

    def _open_live_race_dialog(pools) -> None:
        with form_dialog('New Live Race', card_classes='dialog-card dialog-card--sm') as dialog:
            title_in = ui.input('Race title').classes('w-full')
            pool_options = {p.id: p.name for p in pools}
            pool_in = ui.select(pool_options, label='Pool',
                                value=pools[0].id if pools else None).classes('w-full')
            permalink_options = {None: '(assign later)'}
            for p in pools:
                for pl in p.permalinks:
                    permalink_options[pl.id] = f'{p.name}: {pl.url[:48]}'
            permalink_in = ui.select(permalink_options, label='Permalink (optional)',
                                     value=None).classes('w-full')

            async def submit():
                try:
                    await service.create_live_race(
                        await current(), int(pool_in.value),
                        match_title=title_in.value, permalink_id=permalink_in.value,
                    )
                    ui.notify('Live race scheduled', color='positive')
                    dialog.close()
                    await reload_tabs(LIVE_TAB)
                except (ValueError, PermissionError) as e:
                    notify_error(e)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Create', icon='add', on_click=submit).props('color=primary')
        dialog.open()

    def _open_record_dialog(race) -> None:
        """Type in a race's results, when racetime never told us them.

        One row per racer: who, what happened, and — for a finisher — their time.
        Deliberately the same three outcomes racetime reports, so a hand-recorded
        race is indistinguishable downstream from one the room delivered.
        """
        rows: list = []

        with form_dialog(f'Record results — {race.match_title}', card_classes='dialog-card dialog-card--md') as dialog:
            ui.label('For when the racetime event never arrived. Each racer is recorded '
                     'as an approved run, par-scored exactly as the room’s own results '
                     'would have been.').classes('text-caption text-grey')
            if race.unmatched_handles:
                # Named as a picker question rather than "cannot be recorded", which
                # was only ever true of the unlinked half: a racer racetime finished
                # without timing is linked, in the picker, and typing their time in
                # here is exactly the remedy.
                ui.label('The picker lists members of this community. If one of the '
                         "outstanding racers isn't there, they need to sign in and join "
                         'first.'
                         ).classes('text-caption text-orange')
            holder = ui.element('div').classes('wiz-edit-list')

            def add_row() -> None:
                with holder:
                    with ui.element('div').classes('wiz-edit-row') as row:
                        person = ui.select(state['racer_options'], label='Racer',
                                           with_input=True).classes('wiz-edit-row__lead')
                        ui.button(icon='close', on_click=lambda r=row: _drop(r)
                                  ).props('flat round dense color=grey') \
                            .classes('wiz-edit-row__fixed').tooltip('Remove this racer')
                        outcome = ui.select(_OUTCOMES, label='Outcome',
                                            value=_FINISHED).classes('wiz-edit-row__half')
                        time_in = ui.input('Finish time', placeholder='1:23:45') \
                            .classes('wiz-edit-row__half')
                        # A forfeit or a DQ has no time; hiding the field is clearer
                        # than accepting a value the service will discard.
                        time_in.bind_visibility_from(outcome, 'value',
                                                     lambda v: v == _FINISHED)
                rows.append({'row': row, 'person': person,
                             'outcome': outcome, 'time': time_in})

            def _drop(row) -> None:
                for entry in list(rows):
                    if entry['row'] is row:
                        rows.remove(entry)
                row.delete()

            add_row()
            with ui.row().classes('items-center'):
                ui.button('Add racer', icon='person_add', on_click=add_row).props('flat')

            async def submit():
                refusal = missing_racer_message(
                    [i for i, entry in enumerate(rows, start=1) if not entry['person'].value])
                if refusal:
                    notify_error(ValueError(refusal))
                    return
                results = []
                for entry in rows:
                    finished = entry['outcome'].value == _FINISHED
                    seconds = None
                    if finished:
                        try:
                            seconds = parse_hms(entry['time'].value)
                        except ValueError as e:
                            notify_error(ValueError(f'Finish time: {e}'))
                            return
                    results.append(ManualResult(
                        user_id=int(entry['person'].value),
                        status=_OUTCOME_STATUS[entry['outcome'].value],
                        elapsed_seconds=seconds,
                    ))
                if not results:
                    notify_error(ValueError('Pick at least one racer.'))
                    return
                try:
                    captured = await service.record_manual_finish(
                        await current(), race.id, results)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(f'Recorded {len(captured)} run(s)', color='positive')
                dialog.close()
                # The runs are new rows and the board's totals move with them.
                await reload_tabs(LIVE_TAB, RUNS_TAB, BOARD_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Record', icon='save', on_click=submit).props('color=primary')
        dialog.open()

    def _open_permalink_dialog(race) -> None:
        """Assign the seed a race is run on — the "later" that "(assign later)" promised."""
        options = {pl.id: pl.url[:64] for p in state['pools'] if p.id == race.pool_id
                   for pl in p.permalinks}

        with form_dialog(f'Set permalink — {race.match_title}', card_classes='dialog-card dialog-card--sm') as dialog:
            if not options:
                ui.label('This pool has no permalinks yet. Add one on the Pools tab '
                         'first.').classes('text-caption text-orange')
            else:
                ui.label('Results are scored against this seed’s par, so it has to be '
                         'the one the racers actually play.').classes('text-caption text-grey')
            permalink_in = ui.select(options, label='Permalink').classes('w-full')

            async def submit():
                if not permalink_in.value:
                    notify_error(ValueError('Pick a permalink.'))
                    return
                try:
                    await service.assign_permalink(
                        await current(), race.id, int(permalink_in.value))
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Permalink assigned', color='positive')
                dialog.close()
                await reload_tabs(LIVE_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save', icon='save', on_click=submit).props('color=primary')
        dialog.open()

    async def _open_room(live_race_id: int) -> None:
        try:
            await service.open_room(await current(), live_race_id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Room opened', color='positive')
        await reload_tabs(LIVE_TAB)

    def _result_action(lr, *, as_button: bool) -> None:
        # The capture refuses outright without a permalink, and "(assign later)"
        # makes that a normal state, so offer the control that fixes it instead
        # of a Record action that can only end in a refusal. Recording is offered
        # on a finished race too: it's idempotent, and a race whose results came
        # in with someone unmatched is exactly the case that needs it again.
        if lr.permalink_id is None:
            icon, label, handler = 'link', 'Set permalink', lambda race=lr: _open_permalink_dialog(race)
        else:
            icon, label, handler = 'edit_note', 'Record results', lambda race=lr: _open_record_dialog(race)
        if as_button:
            ui.button(label, icon=icon, on_click=handler).props('flat color=primary')
        else:
            menu_action(icon, label, handler)


    def _cancel_live_race(race) -> None:
        async def do_cancel() -> None:
            try:
                await service.cancel_live_race(await current(), race.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Live race cancelled', color='positive')
            await reload_tabs(LIVE_TAB)

        ConfirmationDialog(
            message=f'Cancel {race.match_title}?',
            on_confirm=do_cancel, confirm_text='Cancel race', cancel_text='Keep it',
            title='Cancel live race?',
        ).open()

    return live_view
