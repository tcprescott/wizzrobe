"""Shared match detail + staff-reporting dialog for the bracket views.

Opened by clicking a match card on the public bracket page *or* in the admin
Results dialog's embedded bracket, so both surfaces get the identical visual
report/override flow (docs/features/brackets.md). Presentation-only: it renders
NiceGUI and calls ``BracketService`` for the write (the sanctioned
presentation → service call), rebinding ``tenant_scope`` because it fires from a
detached client event. The caller supplies the resolved lookups, the tenant id,
the actor, and an ``on_saved`` refresh callback.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Dict, Optional

from nicegui import app, background_tasks, context, ui
from tortoise import exceptions as tortoise_exceptions

from application.services import BracketService, MatchService, get_user_from_session
from application.services.match.match_status import MatchStatus
from application.services.match.match_status import label as status_label
from application.services.match.match_status import tone as status_tone
from application.tenant_context import tenant_scope
from application.utils.timezone import format_local_display
from models import BracketMatch, BracketMatchGameState, BracketMatchState, User
from theme.dialog._helpers import dialog_close_button, mobile_sheet
from theme.notify import notify_error


def _games_of(match: BracketMatch) -> list:
    """The series' game rows, or [] when the caller didn't prefetch them.

    ``BracketRepository.list_matches`` prefetches ``games``; a caller that built
    the match another way simply renders no series detail rather than raising.
    """
    related = getattr(match, 'games', None)
    try:
        return list(related) if related is not None else []
    except tortoise_exceptions.NoValuesFetched:
        return []


_GAME_BADGE = {
    BracketMatchGameState.SCHEDULED: 'positive',
    BracketMatchGameState.COMPLETE: 'primary',
    BracketMatchGameState.CANCELLED: 'grey',
}

# The derived status vocabulary's tones → Quasar badge colours. Mapped from the
# semantic name defined once in ``match_status.STATUS_TONES``, so this dialog,
# the card's pill, and the Discord embed all colour the same state the same way.
_TONE_COLOR = {
    'neutral': 'grey',
    'info': 'positive',
    'imminent': 'warning',
    'live': 'red',
    'done': 'blue-grey',
    'settled': 'primary',
    'cancelled': 'grey',
    'attention': 'warning',
}


def _render_games(
    games: list,
    entry_name: dict,
    best_of: int,
    *,
    bracket_match_id: int,
    matchup_label: str,
    is_staff: bool,
    tenant_id: int,
    on_saved: Callable[[], Awaitable[None]],
    game_status: Optional[dict] = None,
    watch_url: str = '',
) -> None:
    """The per-game breakdown of a series, with the admin's way into each game.

    A plain row stack rather than a ``ui.table``: three rows don't warrant a
    table, and every table needs a mobile grid (``check_table_grid``). Unscheduled
    games are shown as arithmetic — ``best_of`` minus the rows that exist — since
    game rows are created lazily at schedule time.

    Each scheduled game opens the ordinary :class:`AdminMatchDialog` for its
    ``Match``, so staff reschedule, assign a stage, manage crew, or cancel it
    with the same editor the schedule tab uses rather than a bracket-specific
    half-copy of it.
    """
    if best_of <= 1 and not games:
        return

    ui.separator()
    with ui.row().classes('items-center justify-between w-full'):
        ui.label(f'Best of {best_of}' if best_of > 1 else 'Scheduled match') \
            .classes('section-title')
        if is_staff:
            _schedule_button(
                games, best_of, bracket_match_id=bracket_match_id,
                matchup_label=matchup_label, tenant_id=tenant_id,
                on_saved=on_saved,
            )

    numbers = BracketService.numbers_by_time(games)
    for game in sorted(games, key=lambda g: numbers.get(g.id, g.game_number)):
        with ui.row().classes('items-center justify-between w-full q-py-xs'):
            with ui.row().classes('items-center gap-2 no-wrap'):
                if best_of > 1:
                    ui.label(f'Game {numbers.get(game.id, game.game_number)}').classes('text-caption')
                scheduled = _scheduled_label(game)
                if scheduled:
                    ui.label(scheduled).classes('text-caption text-grey')
                winner = entry_name.get(game.winner_entry_id)
                if winner:
                    ui.label(winner).classes('text-bold')
                    if game.forfeit:
                        ui.label('FF').classes('text-bold text-negative')
                elif game.cancelled_reason:
                    ui.label(game.cancelled_reason).classes('text-caption text-grey')
            with ui.row().classes('items-center gap-1 no-wrap'):
                # The derived status when the caller resolved it (U2) — so this
                # row says "Live" while the match is being raced instead of
                # "Scheduled" — falling back to the stored game state otherwise.
                status = (game_status or {}).get(game.id)
                if status is not None:
                    ui.badge(status_label(status), color=_TONE_COLOR.get(
                        status_tone(status), 'grey',
                    ))
                    if status == MatchStatus.LIVE and watch_url:
                        ui.link('Watch', watch_url, new_tab=True) \
                            .props('dense').classes('text-caption')
                else:
                    ui.badge(
                        game.state.value.title(),
                        color=_GAME_BADGE.get(game.state, 'grey'),
                    )
                if is_staff and game.match_id is not None:
                    ui.button(
                        icon='edit',
                        on_click=lambda _=None, mid=game.match_id: background_tasks.create(
                            _open_match_editor(mid, context.client, tenant_id, on_saved)
                        ),
                    ).props('flat dense round size=sm').tooltip('Edit the scheduled match')

    remaining = best_of - len({g.game_number for g in games})
    if best_of > 1 and remaining > 0:
        ui.label(
            f'{remaining} game(s) not yet scheduled'
        ).classes('text-caption text-grey')


def _scheduled_label(game) -> str:
    """The game's scheduled time, or '' when it has no live ``Match``."""
    match = getattr(game, 'match', None)
    if match is None or getattr(match, 'scheduled_at', None) is None:
        return ''
    return format_local_display(match.scheduled_at)


async def _open_match_editor(
    match_id: int, client, tenant_id: int, on_saved: Callable[[], Awaitable[None]],
) -> None:
    """Open the ordinary admin match editor for one game's scheduled ``Match``."""
    # Local import: the bracket dialog and the match dialog are peers, and only
    # this handler needs the dependency.
    from theme.dialog.match_dialog import AdminMatchDialog

    with client:
        with tenant_scope(tenant_id):
            match = await MatchService().get_by_id(match_id)
        if match is None:
            ui.notify('That match no longer exists', color='warning')
            await on_saved()
            return

        async def _after(_result) -> None:
            await on_saved()

        with tenant_scope(tenant_id):
            await AdminMatchDialog(match=match, on_submit=_after).open()


def _schedule_button(
    games: list,
    best_of: int,
    *,
    bracket_match_id: int,
    matchup_label: str,
    tenant_id: int,
    on_saved: Callable[[], Awaitable[None]],
) -> None:
    """'Schedule a game' — staff's route into ``schedule_bracket_match``.

    Players book the same matchups from their own dashboard, which is why the
    dialog is shared rather than bracket-local.
    """
    number = _next_game_number(games, best_of)
    if number is None:
        return
    label = 'Schedule a game' if best_of > 1 else 'Schedule match'
    ui.button(
        label, icon='event',
        on_click=lambda _=None: background_tasks.create(_schedule_dialog(
            number, best_of, bracket_match_id=bracket_match_id,
            matchup_label=matchup_label, tenant_id=tenant_id,
            client=context.client, on_saved=on_saved,
        )),
    ).props('flat dense size=sm color=primary')


def _next_game_number(games: list, best_of: int) -> Optional[int]:
    """Mirror of the service's slot allocation, for labelling only."""
    taken = {g.game_number for g in games}
    return next((n for n in range(1, best_of + 1) if n not in taken), None)


async def _schedule_dialog(
    number: int,
    best_of: int,
    *,
    bracket_match_id: int,
    matchup_label: str,
    tenant_id: int,
    client,
    on_saved: Callable[[], Awaitable[None]],
) -> None:
    """Open the shared schedule dialog in staff mode."""
    from theme.dialog.bracket_schedule_dialog import BracketScheduleDialog

    with client:
        with tenant_scope(tenant_id):
            actor = await get_user_from_session(app.storage.user)
        await BracketScheduleDialog(
            bracket_match_id, actor,
            matchup_label=matchup_label,
            game_number=number,
            best_of=best_of,
            tenant_id=tenant_id,
            on_submit=on_saved,
        ).open()


_STATE_BADGE = {
    BracketMatchState.PENDING: 'grey',
    BracketMatchState.OPEN: 'positive',
    BracketMatchState.COMPLETE: 'primary',
}


def slot_display_name(match: BracketMatch, slot: int, entry_name: Dict[int, str]) -> str:
    """A slot's name as the card labels it.

    An empty side of a finished match is a bye, not a player still to be
    decided — the card said BYE while the dialog it opens said TBD.
    """
    entry_id = match.entry1_id if slot == 1 else match.entry2_id  # type: ignore[attr-defined]
    other = match.entry2_id if slot == 1 else match.entry1_id  # type: ignore[attr-defined]
    if entry_id is not None:
        return entry_name.get(entry_id, 'TBD')
    if match.state == BracketMatchState.COMPLETE and other is not None:
        return 'BYE'
    return 'TBD'


def build_match_dialog(
    match: BracketMatch,
    entry_name: Dict[int, str],
    entry_seed: Dict[int, Optional[int]],
    records: Dict[int, tuple],
    number: Optional[int],
    *,
    best_of: int = 1,
    is_staff: bool,
    actor: Optional[User],
    tenant_id: int,
    service: BracketService,
    on_saved: Callable[[], Awaitable[None]],
    live: Optional[dict] = None,
) -> None:
    """Build and open the match dialog: entrants, scores/FF, state, reporting."""
    completed = match.state == BracketMatchState.COMPLETE

    def name_of(entry_id: Optional[int]) -> str:
        return entry_name.get(entry_id, 'TBD') if entry_id is not None else 'TBD'

    def slot_name(slot: int) -> str:
        return slot_display_name(match, slot, entry_name)

    with ui.dialog() as dialog, ui.card().classes('dialog-card'):  # style-guide: exempt — its header carries the match's status badge
        mobile_sheet(dialog)
        with ui.row().classes('dialog-header items-center q-pa-sm'):
            heading = ui.label(f'Match {number}' if number else 'Match').classes('text-h6 q-ma-none')  # style-guide: exempt — a dialog title, drawn like dialog_header
            heading.tag = 'h2'
            ui.space()
            status = (live or {}).get('status')
            if status is not None:
                ui.badge(status_label(status), color=_TONE_COLOR.get(
                    status_tone(status), 'grey',
                ))
            else:
                ui.badge(
                    match.state.value.title(),
                    color=_STATE_BADGE.get(match.state, 'grey'),
                )
            dialog_close_button(dialog)

        for slot, entry_id in ((1, match.entry1_id), (2, match.entry2_id)):
            is_winner = (
                completed and match.winner_id is not None and entry_id == match.winner_id
            )
            score = match.entry1_score if slot == 1 else match.entry2_score
            with ui.row().classes('items-center justify-between w-full q-py-xs'):
                with ui.row().classes('items-center gap-2 no-wrap'):
                    seed = entry_seed.get(entry_id) if entry_id is not None else None
                    if seed is not None:
                        ui.label(f'#{seed}').classes('text-caption text-grey')
                    ui.label(slot_name(slot)).classes('text-bold' if is_winner else '')
                    rec = records.get(entry_id) if entry_id is not None else None
                    if rec is not None and (rec[0] or rec[1]):
                        ui.label(f'({rec[0]}-{rec[1]})').classes('text-caption text-grey')
                if completed and match.forfeit and entry_id is not None and not is_winner:
                    ui.label('FF').classes('text-bold text-negative')
                elif score is not None:
                    ui.label(str(score)).classes('text-bold' if is_winner else '')
                elif is_winner:
                    ui.icon('emoji_events', size='xs').classes('text-primary')

        _render_games(
            _games_of(match), entry_name, best_of,
            bracket_match_id=match.id,
            matchup_label=f'{slot_name(1)} vs {slot_name(2)}',
            is_staff=is_staff, tenant_id=tenant_id, on_saved=on_saved,
            game_status=(live or {}).get('games'),
            watch_url=(live or {}).get('watch_url', ''),
        )

        can_report = (
            is_staff
            and match.state in (BracketMatchState.OPEN, BracketMatchState.COMPLETE)
            and match.entry1_id is not None
            and match.entry2_id is not None
        )
        if can_report:
            ui.separator()
            ui.label(
                'Report result' if match.state == BracketMatchState.OPEN
                else 'Override result'
            ).classes('section-title')
            with ui.row().classes('form-pair items-center gap-2'):
                s1 = ui.number(
                    label=name_of(match.entry1_id), value=match.entry1_score, min=0,
                ).props('dense inputmode=numeric')
                s2 = ui.number(
                    label=name_of(match.entry2_id), value=match.entry2_score, min=0,
                ).props('dense inputmode=numeric')
            ff = ui.switch('Forfeit', value=match.forfeit)

            async def save(winner_id: int) -> None:
                e1 = int(s1.value) if s1.value is not None else None
                e2 = int(s2.value) if s2.value is not None else None
                with tenant_scope(tenant_id):
                    try:
                        if match.state == BracketMatchState.OPEN:
                            await service.report_result(
                                actor, match.id, winner_id,
                                entry1_score=e1, entry2_score=e2, forfeit=ff.value,
                            )
                        else:
                            await service.override_result(
                                actor, match.id, winner_id,
                                entry1_score=e1, entry2_score=e2, forfeit=ff.value,
                            )
                    except (ValueError, PermissionError) as ex:
                        notify_error(ex)
                        return
                ui.notify('Result saved', color='positive')
                dialog.close()
                await on_saved()

            with ui.row().classes('gap-2 w-full q-mt-xs'):
                ui.button(
                    f'Winner: {name_of(match.entry1_id)}', icon='emoji_events',
                    on_click=lambda _=None, w=match.entry1_id: save(w),
                ).props('dense color=primary').classes('col min-w-0 ellipsis')
                ui.button(
                    f'Winner: {name_of(match.entry2_id)}', icon='emoji_events',
                    on_click=lambda _=None, w=match.entry2_id: save(w),
                ).props('dense color=primary').classes('col min-w-0 ellipsis')

    dialog.open()
