"""Player-facing Async Qualifier pages — list, run execution, and leaderboard.

- ``/qualifiers`` lists active qualifiers.
- ``/qualifiers/{qualifier_id}`` is the run surface: pick an eligible pool → an
  atomic draw reveals a spoiler-safe permalink and starts a server-timed run →
  submit finish time + VoD (→ review) or forfeit. It also shows the player's own
  runs and the leaderboard — which stays hidden until the qualifier closes
  (active-window information lockdown), except for staff.
"""

from datetime import datetime, timezone
from typing import Optional

from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.content import parse_blocks
from application.services import AsyncQualifierService, AuthService, TenantService, get_user_from_session
from application.services.async_qualifier.async_qualifier_rules import (
    ClaimVerdict,
    classify_claim,
    measure_elapsed,
    run_deadline,
)
from application.utils.duration import format_hms, parse_hms
from application.utils.timezone import format_local_display
from middleware.auth import protected_page
from middleware.event_routing import page_request, row_event_redirect
from models import FeatureFlag
from theme.base import BaseLayout
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.help.render import render_blocks
from theme.links import link_button
from theme.notify import notify_error
from theme.page_header import page_header, page_title
from theme.qualifier_copy import (
    BAND_EXPLAINER,
    BAND_LABELS,
    BOARD_EXPLAINER,
    REVIEW_LABELS,
    SCORE_EXPLAINER,
    STATUS_LABELS,
)
from theme.steady import steady_refreshable
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys, row_count_label, search_input, sticky_header
from theme.timer_teardown import PageTimer


def _fmt(dt) -> str:
    return format_local_display(dt) if dt else '—'


# Keep a pagination prop: without one Quasar renders every row on one page.
_RUNS_PAGE = {'rowsPerPage': 25, 'page': 1}
_BOARD_PAGE = {'rowsPerPage': 50, 'page': 1}

_MY_RUNS_ID = 'my-runs'

_REATTEMPT_ACTION = '''
    <q-btn v-if="props.row.reattemptable" flat dense icon="restart_alt" color="primary"
           label="Reattempt"
           @click="$parent.$emit('reattempt', props.row)">
        <q-tooltip>Void this run and free its pool slot</q-tooltip>
    </q-btn>
'''


def _seed_cell(run) -> str:
    """Which permalink this run played, shortened.

    On the reviewer's card since PR 9 and on no screen the runner could reach, so
    a runner disputing a verdict could not cite the seed they were given.
    """
    url = run.permalink_url or ''
    if not url:
        return '—'
    return url if len(url) <= 44 else f'{url[:44]}…'


def _review_cell(run) -> str:
    """What the Review column says, which is not always the review status.

    Two cases where the raw value misleads. An in-progress run is ``PENDING`` too,
    which read as "a reviewer has this" before anything had been submitted. And a
    voided run keeps whatever verdict it had — a forfeit is written approved — so a
    run that stopped counting was reading "Approved".
    """
    if run.reattempted:
        return 'Voided'
    if run.status.value in ('pending', 'in_progress'):
        return '—'
    return REVIEW_LABELS.get(run.review_status.value, run.review_status.value)


def _score_cell(run, exact: bool):
    """The exact score once results are public; the band while they are not.

    A run that never finished has no side of par, so it says what happened to it
    rather than borrowing a band.
    """
    if exact:
        return '' if run.score is None else round(run.score, 1)
    if run.status.value in ('forfeit', 'disqualified'):
        return STATUS_LABELS[run.status.value]
    if run.score_band is None:
        return ''
    return BAND_LABELS.get(run.score_band.value, run.score_band.value)


def _void_reason(run) -> str:
    """Why this run stopped counting, and which of the two voided it.

    A granted void's ``reattempt_reason`` went out by DM and never appeared on the
    page the runner comes back to, so it read as an unexplained "(reattempted)".
    A self-spent one is echoed back for the same reason a receipt is: the runner
    typed it, possibly weeks ago.
    """
    if not (run.reattempted and run.reattempt_reason):
        return ''
    who = 'Voided by a reviewer' if run.reattempt_was_granted else 'You voided this'
    return f'{who} — {run.reattempt_reason}'


def _words(seconds: int) -> str:
    """``5025`` → ``'1 hour, 23 minutes, 45 seconds'`` — display copy for the echo."""
    parts = []
    for value, unit in ((seconds // 3600, 'hour'), (seconds % 3600 // 60, 'minute'),
                        (seconds % 60, 'second')):
        if value:
            parts.append(f'{value} {unit}' + ('' if value == 1 else 's'))
    return ', '.join(parts) or '0 seconds'


def create() -> None:
    service = AsyncQualifierService()

    @protected_page('/qualifiers', feature=FeatureFlag.ASYNC_QUALIFIERS)
    async def qualifiers_list() -> None:
        ui.page_title(f'Async Qualifiers — {await TenantService.current_community_name() or "Wizzrobe"}')
        user = await get_user_from_session(app.storage.user)
        show_admin = await AuthService.can_open_admin(user)
        await BaseLayout(user=user, show_admin=show_admin).render()

        with ui.column().classes('page-container'):
            with page_header('Async Qualifiers'):
                await help_icon('qualifiers-start', 'qualifiers-submit', 'qualifiers-scoring', user=user)
            qualifiers = await service.list_open_qualifiers()
            if not qualifiers:
                empty_panel('No qualifiers are open right now. Check back soon.', icon='timer')
                return
            with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
                for q in qualifiers:
                    with ui.card():
                        with ui.row().classes('items-center full-width'):
                            ui.label(q.name).classes('section-title')
                            ui.space()
                            link_button('Open', f'/qualifiers/{q.id}', icon='arrow_forward', new_tab=False) \
                                .props('flat color=primary')
                        ui.label(f'Window: {_fmt(q.opens_at)} → {_fmt(q.closes_at)}').classes(
                            'text-caption text-grey')
                        if q.description:
                            with ui.element('div').classes('wiz-help-prose'):
                                render_blocks(parse_blocks(q.description))

    @protected_page('/qualifiers/{qualifier_id}', feature=FeatureFlag.ASYNC_QUALIFIERS, scope='row')
    async def qualifier_detail(qualifier_id: int, focus: str | None = None) -> Optional[RedirectResponse]:
        ui.page_title(f'Async Qualifier — {await TenantService.current_community_name() or "Wizzrobe"}')
        user = await get_user_from_session(app.storage.user)
        qualifier = None
        error = 'You must be logged in.'
        if user is not None:
            try:
                qualifier = await service.get_qualifier_for_player(qualifier_id)
            except ValueError as e:
                error = str(e)
        if qualifier is not None:
            redirect = await row_event_redirect(page_request(), qualifier.event_id)
            if redirect is not None:
                return redirect

        show_admin = await AuthService.can_open_admin(user)
        await BaseLayout(user=user, show_admin=show_admin).render()

        if user is None or qualifier is None:
            ui.label(error).classes('text-error')
            return None

        container = ui.column().classes('page-container')

        @steady_refreshable
        async def render() -> None:
            container.clear()
            with container:
                with ui.row().classes('items-center full-width'):
                    ui.button(icon='arrow_back', on_click=lambda: ui.navigate.to('/qualifiers')
                              ).props('flat round')
                    page_title(qualifier.name)
                is_public = service.is_results_public(qualifier)
                open_now = _window_open(qualifier)
                ui.label(f'Window: {_fmt(qualifier.opens_at)} → {_fmt(qualifier.closes_at)}  ·  '
                         f'{"Open" if open_now else "Closed"}').classes('text-caption text-grey')
                ui.separator()

                active = await service.get_active_run(user, qualifier_id)
                if active is not None:
                    await _render_active_run(active)
                else:
                    await _render_start(user)

                ui.separator()
                await _render_my_runs(user)
                ui.separator()
                await _render_leaderboard(user, is_public)

        async def _render_start(current) -> None:
            availability = await service.get_run_availability(current, qualifier_id)
            ui.label('Start a run').classes('text-subtitle1')
            if not availability.pools:
                ui.label(availability.message).classes('text-grey')
                return
            ui.label('Pick a pool. A permalink is drawn and revealed only when your '
                     'run starts — and your timer begins immediately.').classes('text-caption text-grey')
            eligible = {p.id for p in availability.pools}
            for usage in availability.usage:
                with ui.row().classes('items-center'):
                    if usage.pool_id in eligible:
                        ui.button(f'Start: {usage.name}', icon='play_arrow',
                                  on_click=lambda pid=usage.pool_id: _start(pid)
                                  ).props('color=primary')
                    else:
                        ui.label(usage.name).classes('text-grey')
                    detail = f'{usage.used} of {usage.allowed} runs used'
                    if usage.slots_left and not usage.has_candidates:
                        detail += ' · no unplayed seeds left'
                    ui.label(detail).classes('text-caption text-grey')

        async def _start(pool_id: int) -> None:
            try:
                await service.start_run(user, qualifier_id, pool_id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Run started — good luck!', color='positive')
            await render.refresh()

        async def _render_active_run(run) -> None:
            with ui.card().classes('w-full'):
                ui.label('Your run is in progress').classes('text-subtitle1 text-positive')
                pool_name = run.permalink.pool.name if run.permalink and run.permalink.pool else '—'
                ui.label(f'Pool: {pool_name}').classes('text-caption text-grey')
                if run.permalink:
                    ui.link('Your permalink (open the seed)', run.permalink.url, new_tab=True)
                elapsed_label = ui.label('Elapsed: 0:00:00').classes('text-h6')  # style-guide: exempt — a running clock, not a title
                deadline = run_deadline(qualifier, run.started_at)
                remaining_label = None
                if deadline is not None:
                    ui.label(f'Auto-forfeits at {_fmt(deadline)} if you have not '
                             'submitted or forfeited by then.').classes('text-caption text-warning')
                    remaining_label = ui.label('').classes('text-caption text-warning')

                def _tick():
                    measured = measure_elapsed(run.started_at)
                    if measured is None:
                        return
                    elapsed_label.text = f'Elapsed: {format_hms(measured)}'
                    if remaining_label is not None and deadline is not None:
                        left = int((deadline - datetime.now(timezone.utc)).total_seconds())
                        remaining_label.text = (
                            f'{format_hms(left)} left' if left > 0
                            else 'Past the deadline — it will be forfeited on the next check.'
                        )

                PageTimer(1.0, _tick)

                ui.separator()
                ui.label('Submit your result').classes('text-subtitle2')
                time_in = ui.input('Finish time (H:MM:SS)', placeholder='1:23:45').classes('w-full')
                echo = ui.label('').classes('text-caption text-grey')
                vod_in = ui.input('VoD URL (optional)').classes('w-full')

                def _echo() -> None:
                    try:
                        seconds = parse_hms(time_in.value)
                    except ValueError:
                        echo.text = ''
                        return
                    echo.text = f'Submitting {format_hms(seconds)} — {_words(seconds)}'

                time_in.on_value_change(lambda _: _echo())

                async def _do_submit(seconds: int) -> None:
                    try:
                        await service.submit_run(user, run.id, elapsed_seconds=seconds,
                                                 runner_vod_url=vod_in.value)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify('Submitted for review!', color='positive')
                    await render.refresh()

                def _ask_about_drift(seconds: int, measured: int) -> None:
                    async def _confirmed() -> None:
                        await _do_submit(seconds)

                    confirm = ConfirmationDialog(
                        title='Is that the right time?',
                        message=(f'Your timer says {format_hms(measured)}. '
                                 f'You typed {format_hms(seconds)}.\n\n'
                                 'If you finished a while ago and are only submitting now, '
                                 'your time is fine — submit it. If you dropped a segment, '
                                 'go back and fix it.'),
                        confirm_text=f'Submit {format_hms(seconds)}',
                        cancel_text='Let me fix it',
                        tone='primary',
                        on_confirm=_confirmed,
                    )
                    confirm.open()

                async def _submit():
                    try:
                        seconds = parse_hms(time_in.value)
                    except ValueError as e:
                        notify_error(e)
                        return
                    measured = measure_elapsed(run.started_at)
                    if classify_claim(seconds, measured) is ClaimVerdict.IMPLAUSIBLE:
                        _ask_about_drift(seconds, measured)
                        return
                    await _do_submit(seconds)

                async def _forfeit():
                    try:
                        await service.forfeit_run(user, run.id)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify('This run is marked forfeited.', color='info')
                    await render.refresh()

                async def _confirm_forfeit() -> None:
                    allowance = await service.get_reattempt_allowance(user, qualifier_id)
                    remedy = ''
                    if allowance.remaining:
                        plural = '' if allowance.remaining == 1 else 's'
                        remedy = (f'\n\nYou would have {allowance.remaining} reattempt{plural} '
                                  f'left, which starts a new run on a new permalink.')
                    confirm = ConfirmationDialog(
                        title='Forfeit this run?',
                        message=('Forfeiting ends this attempt now.\n\n'
                                 'The run scores 0, the pool slot is spent, and this '
                                 'cannot be undone.' + remedy),
                        confirm_text='Forfeit run',
                        tone='negative',
                        on_confirm=_forfeit,
                    )
                    confirm.open()

                with ui.row().classes('justify-end w-full'):
                    ui.button('Forfeit', icon='flag',
                              on_click=_confirm_forfeit).props('flat color=negative')
                    ui.button('Submit', icon='send', on_click=_submit).props('color=primary')

        async def _render_my_runs(current) -> None:
            runs = await service.list_user_runs(current, qualifier_id)
            allowance = await service.get_reattempt_allowance(current, qualifier_id)
            ui.label('My runs').classes('text-subtitle1').props(f'id={_MY_RUNS_ID}')
            window_open = _window_open(qualifier)
            if allowance.allowed and window_open:
                ui.label(f'Reattempts: {allowance.remaining} of {allowance.allowed} remaining. '
                         'Spending one voids a finished or forfeited run and frees its pool '
                         'slot for a new draw.').classes('text-caption text-grey')
            if not runs:
                ui.label('You have no runs yet.').classes('text-grey')
                return
            can_reattempt = window_open and allowance.remaining > 0
            exact_scores = service.is_results_public(qualifier)
            columns: list[dict] = [
                {'name': 'pool', 'label': 'Pool', 'field': 'pool', 'align': 'left',
                 'sortable': True},
                {'name': 'seed', 'label': 'Seed', 'field': 'seed', 'align': 'left'},
                {'name': 'status', 'label': 'Status', 'field': 'status', 'sortable': True},
                {'name': 'review', 'label': 'Review', 'field': 'review', 'sortable': True},
                # HH:MM:SS is zero-padded, so a lexical sort is a chronological one.
                {'name': 'time', 'label': 'Time', 'field': 'time', 'sortable': True},
                {'name': 'score', 'label': 'Score' if exact_scores else 'Against par',
                 'field': 'score', 'sortable': True},
                {'name': 'note', 'label': 'Reviewer note', 'field': 'note', 'align': 'left',
                 'style': 'max-width: 20rem; white-space: normal'},
            ]
            if can_reattempt:
                columns.append({'name': 'actions', 'label': '', 'field': 'actions'})
            rows = []
            for r in runs:
                status = STATUS_LABELS.get(r.status.value, r.status.value)
                if r.was_expired:
                    status = 'Forfeited (ran out of time)'
                rows.append({
                    'id': r.id,
                    'pool': r.pool_name + (' (voided)' if r.reattempted else ''),
                    'seed': _seed_cell(r),
                    'status': status,
                    'review': _review_cell(r),
                    'time': format_hms(r.elapsed_seconds),
                    'score': _score_cell(r, exact_scores),
                    'note': _void_reason(r) or r.latest_note,
                    'reattemptable': r.is_reattemptable,
                })
            table = ui.table(columns=columns, rows=rows, row_key='id',
                             pagination=_RUNS_PAGE).classes('w-full wiz-table')
            if can_reattempt:
                table.add_slot('body-cell-actions',
                               f'<q-td :props="props">{_REATTEMPT_ACTION}</q-td>')
                table.on('reattempt', lambda e: _open_reattempt_dialog(e.args.get('id')))
                enable_mobile_grid(table, columns, actions=_REATTEMPT_ACTION,
                                   table_key=TableKeys.QUALIFIERS_LIST)
            else:
                enable_mobile_grid(table, columns, table_key=TableKeys.QUALIFIERS_LIST)
            ui.label(SCORE_EXPLAINER if exact_scores else BAND_EXPLAINER).classes(
                'text-caption text-grey')

        def _open_reattempt_dialog(run_id) -> None:
            with form_dialog('Use a reattempt?', card_classes='dialog-card dialog-card--sm') as dialog:
                ui.label('This voids that run — it stops counting and its score is '
                         'discarded — and frees the pool slot so you can draw a new '
                         'permalink. It does not restore the run you already played.'
                         ).classes('text-caption text-grey')
                reason_in = ui.textarea('Why? (required)').classes('w-full').props('rows=2')

                async def submit() -> None:
                    try:
                        await service.reattempt_run(user, int(run_id), reason=reason_in.value)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify('Reattempt used — that pool is open again.', color='positive')
                    dialog.close()
                    await render.refresh()

                with dialog_actions().classes('justify-end'):
                    ui.button('Cancel', on_click=dialog.close).props('flat')
                    spend = ui.button('Use reattempt', icon='restart_alt', on_click=submit)
                    spend.props('color=primary')
                    spend.bind_enabled_from(reason_in, 'value', lambda v: bool((v or '').strip()))
            dialog.open()

        async def _render_leaderboard(current, is_public) -> None:
            ui.label('Leaderboard').classes('text-subtitle1')
            try:
                entries = await service.get_leaderboard(current, qualifier_id)
            except PermissionError:
                ui.label('The leaderboard is hidden until this qualifier closes.').classes('text-grey')
                return
            if not entries:
                ui.label('No scored runs yet.').classes('text-grey')
                return
            columns: list[dict] = [
                {'name': 'rank', 'label': '#', 'field': 'rank', 'sortable': True},
                {'name': 'user', 'label': 'Player', 'field': 'user', 'align': 'left',
                 'sortable': True},
                {'name': 'actual', 'label': 'Score', 'field': 'actual', 'sortable': True},
                {'name': 'estimate', 'label': 'Estimate', 'field': 'estimate',
                 'sortable': True},
                {'name': 'slots', 'label': 'Slots', 'field': 'slots'},
            ]
            # Rank comes from the scoring function, not position: ties share it.
            rows = [
                {'rank': e.rank, 'user': e.username, 'actual': e.actual,
                 'estimate': e.estimate, 'slots': f'{e.slots_filled}/{e.slots_total}'}
                for e in entries
            ]
            # Built before the table so it renders above it; filled once the table exists.
            toolbar = ui.row().classes('items-center w-full')
            # Keyed by player, not rank: ranks are shared on a tie.
            table = ui.table(columns=columns, rows=rows, row_key='user',
                             pagination=_BOARD_PAGE).classes('w-full wiz-table')
            with toolbar:
                search_input(table, placeholder='Find a player…')
                ui.space()
                row_count_label(table, 'players')
            sticky_header(table)
            enable_mobile_grid(table, columns, table_key=TableKeys.QUALIFIERS_LEADERBOARD)
            ui.label(BOARD_EXPLAINER).classes('text-caption text-grey')

        await render()
        if focus == 'runs':
            ui.run_javascript(
                f"setTimeout(() => document.getElementById('{_MY_RUNS_ID}')"
                "?.scrollIntoView({behavior: 'smooth', block: 'start'}), 400)"
            )
        return None


def _window_open(qualifier) -> bool:
    if not qualifier.is_active:
        return False
    now = datetime.now(timezone.utc)
    if qualifier.opens_at is not None and now < qualifier.opens_at:
        return False
    if qualifier.closes_at is not None and now >= qualifier.closes_at:
        return False
    return True
