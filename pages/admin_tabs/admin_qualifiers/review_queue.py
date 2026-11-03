"""Admin Async Qualifiers — the Review Queue tab.

The reviewer's working surface: one card per submitted run carrying the claimed and
measured times, the seed played, the runner's other attempts in this qualifier, any
notes already on it, and the claim lock.

Since F5 the claim is a real lock rather than a badge — a card another reviewer
holds offers Release and *no verdict buttons at all*, because a `disable()`d Quasar
flat button keeps its colour at 0.7 opacity and reads as live.

A factory for the same reason as ``live_races`` and ``reviewers``: the page owns
``state``, the captured client and the loaders; this tab reads and drives them.
"""

from typing import Any, Awaitable, Callable

from nicegui import ui

from application.services.async_qualifier.async_qualifier_rules import (
    ClaimVerdict,
    classify_claim,
)
from application.utils.duration import format_hms
from application.utils.safe_url import http_url_or_empty
from pages.admin_tabs.admin_qualifiers.shared import (
    BOARD_TAB,
    QUEUE_PAGE_SIZE,
    QUEUE_TAB,
    RUNS_TAB,
    claim_holder,
    existing_notes,
    fmt,
    other_runs_summary,
    short_url,
)
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, more_menu


def build_queue_tab(
    *,
    state: dict,
    service: Any,
    current: Callable[[], Awaitable[Any]],
    reload_tabs: Callable[..., Awaitable[None]],
    reload_queue: Callable[[], Awaitable[None]],
    placeholder: Callable[[str], bool],
    notify_error: Callable[[Exception], None],
    client: Any,
) -> Callable[[], None]:
    """Return the refreshable Review Queue view."""

    @ui.refreshable
    def queue_view() -> None:
        if state.get('shell') is None or placeholder(QUEUE_TAB):
            return
        queue = state['queue']
        if not queue:
            empty_panel('No runs awaiting review.', icon='fact_check')
            return
        shown = min(state['queue_shown'], len(queue))
        with ui.row().classes('items-center w-full'):
            ui.label(f'{len(queue)} awaiting review').classes('text-caption text-grey-7')
        me = state.get('viewer_id')
        with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
            for run in queue[:shown]:
                runner = run.user.display_name or run.user.username
                pool_name = run.permalink.pool.name if run.permalink and run.permalink.pool else '—'
                holder = claim_holder(run)
                mine = run.review_claimed_by_id == me
                with ui.card():
                    with ui.row().classes('items-center full-width'):
                        ui.label(runner).classes('text-subtitle1')
                        ui.badge(format_hms(run.elapsed_seconds), color='blue')
                        ui.badge(pool_name, color='grey')
                        if holder:
                            ui.badge('you have this' if mine else f'{holder} has this',
                                     color='orange' if mine else 'negative')
                        ui.space()
                        if holder and not mine:
                            ui.button('Release', icon='lock_open',
                                      on_click=lambda r=run: _release_claim(r)
                                      ).props('flat color=warning').tooltip(
                                f'{holder} is reviewing this run. Release it if they are done.')
                        elif not holder:
                            ui.button('Claim', icon='lock',
                                      on_click=lambda r=run: _claim_run(r)
                                      ).props('flat color=primary')
                            with more_menu(runner):
                                menu_action('check', 'Approve', lambda r=run: _open_review_dialog(r, True))
                                menu_action('close', 'Reject', lambda r=run: _open_review_dialog(r, False))
                        else:
                            ui.button('Reject', icon='close',
                                      on_click=lambda r=run: _open_review_dialog(r, False)
                                      ).props('flat color=negative')
                            ui.button('Approve', icon='check',
                                      on_click=lambda r=run: _open_review_dialog(r, True)
                                      ).props('flat color=primary')
                            with more_menu(runner):
                                menu_action('lock_open', 'Release claim', lambda r=run: _release_claim(r))
                    with ui.row().classes('items-center gap-2'):
                        ui.label(f'Claimed {format_hms(run.elapsed_seconds)}  ·  '
                                 f'Timed {format_hms(run.measured_seconds)}').classes(
                            'text-caption text-grey')
                        if classify_claim(run.elapsed_seconds or 0,
                                          run.measured_seconds) is ClaimVerdict.IMPLAUSIBLE:
                            drift = run.measured_seconds - (run.elapsed_seconds or 0)
                            ui.badge(f'drift {format_hms(drift)}', color='orange').tooltip(
                                'The runner confirmed this time against their own timer.')
                    ui.label(f'Started {fmt(run.started_at)} · Finished {fmt(run.finished_at)}').classes(
                        'text-caption text-grey')
                    if run.permalink:
                        ui.link(f'Permalink played: {short_url(run.permalink.url)}',
                                run.permalink.url, new_tab=True).classes('text-caption')
                    if http_url_or_empty(run.runner_vod_url):
                        ui.link('VoD', http_url_or_empty(run.runner_vod_url), new_tab=True).classes('text-caption')
                    others = other_runs_summary(state['queue_context'], run)
                    if others:
                        ui.label(others).classes('text-caption text-grey')
                    for note in existing_notes(run):
                        ui.label(f'Note — {note}').classes('text-caption text-italic')
        if shown < len(queue):
            ui.button(f'Show {min(QUEUE_PAGE_SIZE, len(queue) - shown)} more',
                      icon='expand_more', on_click=_show_more_queue).props('flat color=primary')

    def _show_more_queue() -> None:
        state['queue_shown'] += QUEUE_PAGE_SIZE
        queue_view.refresh()

    async def _claim_run(run) -> None:
        try:
            await service.claim_run(await current(), run.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
        await reload_queue()

    async def _release_claim(run) -> None:
        try:
            await service.release_claim(await current(), run.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
        await reload_queue()

    def _open_review_dialog(run, approved: bool) -> None:
        """One dialog for both verdicts so the two paths cannot drift.

        A rejection's reason is required — the service refuses one without, and
        what the reviewer types is what the runner is told.
        """
        verb = 'Approve' if approved else 'Reject'
        runner = run.user.display_name or run.user.username
        with form_dialog(f'{verb} {runner}\u2019s run', card_classes='dialog-card dialog-card--sm') as dialog:
            note_in = ui.textarea(
                'Note (optional)' if approved else 'Reason (shown to the runner)'
            ).classes('w-full').props('rows=3')

            async def submit() -> None:
                try:
                    await service.review_run(
                        await current(), run.id, approved=approved, note=note_in.value,
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Run approved' if approved else 'Run rejected', color='positive')
                dialog.close()
                state['queue'] = [r for r in state['queue'] if r.id != run.id]
                with client:
                    queue_view.refresh()
                await reload_tabs(BOARD_TAB, RUNS_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                confirm = ui.button(f'{verb} run', icon='check' if approved else 'close',
                                    on_click=submit)
                confirm.props(f'color={"positive" if approved else "negative"}')
                if not approved:
                    confirm.bind_enabled_from(note_in, 'value', lambda v: bool((v or '').strip()))
        dialog.open()

    return queue_view
