"""Round schedule: when each stage of a Challonge or start.gg bracket may be booked.

Admin → Tournaments opens this from a linked tournament's ⋮. One row per stage
(group stage, each elimination round, finals) with its opens, closes, daily
hours and how many of its matches are booked. Tapping a row edits it in a side
sheet (a bottom sheet on a phone); nothing is stored until Save schedule.

Every time here is on the community's clock, not the viewer's: the daily hours
are read on that clock by Suggest a time and by booking, so the window has to be
typed on it too.
"""

from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional

from nicegui import app, ui

from application.services import (
    RoundScheduleService,
    RoundWindowError,
    ScheduleStage,
    TimezoneService,
    get_user_from_session,
)
from application.services.round_schedule_service import booked_summary
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.timezone_context import get_zone
from application.utils.timezone import parse_local_datetime, timezone_label
from models import Tournament
from theme.dialog._helpers import (
    dialog_actions,
    dialog_header,
    mobile_sheet,
    native_datetime_input,
    native_time_input,
)
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error

_INTRO = {
    'Challonge': 'Found on Challonge',
    'start.gg': 'Found on start.gg',
}


def confirm_outside_window(error: Exception, book_anyway: Callable[[], Awaitable[None]]) -> bool:
    """Offer staff "Book anyway" for a time outside a stage's window.

    Returns False when ``error`` isn't one staff may override, so the caller
    shows it as it would any other error.
    """
    if not isinstance(error, RoundWindowError) or not error.can_override:
        return False
    ConfirmationDialog(
        f'{error}\n\nPlayers can\'t book this, but you can because you run this tournament.',
        on_confirm=book_anyway, confirm_text='Book anyway', tone='primary',
        title='Outside the booking window', busy_text='Saving…',
    ).open()
    return True


def _local_input(dt: Optional[datetime], tz) -> str:
    return dt.astimezone(tz).strftime('%Y-%m-%dT%H:%M') if dt else ''


def _display(value: str) -> str:
    if not value:
        return ''
    parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M')
    return f"{parsed.strftime('%a')} {parsed.day} {parsed.strftime('%b, %H:%M')}"


class RoundScheduleDialog:
    def __init__(
        self, tournament: Tournament,
        on_saved: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self.tournament = tournament
        self.on_saved = on_saved
        self.service = RoundScheduleService()
        self.stages: List[ScheduleStage] = []
        # key -> {'opens', 'closes'} as datetime-local strings on the community
        # clock, plus 'daily_from'/'daily_until' as HH:MM; '' means unset.
        self.drafts: Dict[str, Dict[str, str]] = {}
        self.tz = None
        self.tenant_id = get_current_tenant_id()

    async def open(self) -> None:
        actor = await get_user_from_session(app.storage.user)
        try:
            self.stages = await self.service.list_stages(self.tournament.id, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        self.tz = get_zone(await TimezoneService.tenant_timezone_name())
        for stage in self.stages:
            w = stage.window
            self.drafts[stage.key] = {
                'opens': _local_input(w.opens if w else None, self.tz),
                'closes': _local_input(w.closes if w else None, self.tz),
                'daily_from': w.daily_from.strftime('%H:%M') if w and w.daily_from else '',
                'daily_until': w.daily_until.strftime('%H:%M') if w and w.daily_until else '',
            }

        with ui.dialog() as self.dialog, ui.card().classes('dialog-card dialog-card--lg round-schedule'):
            mobile_sheet(self.dialog)
            dialog_header('Round schedule', self.dialog)
            with ui.column().classes('full-width gap-2'):
                source = self.service.source_label(self.tournament) or ''
                ui.label(f'{self.tournament.name} · {source}').classes('text-caption text-muted')
                with ui.row().classes('round-schedule__note no-wrap items-start gap-2'):
                    ui.icon('auto_awesome').classes('text-muted')
                    ui.label(
                        f"{_INTRO.get(source, 'Stages')}: {self._found_summary()}. "
                        f"Times are {timezone_label(self.tz)}. Players can only book a match that "
                        'fits wholly inside its stage; staff can still save outside it after a '
                        'warning. Leave a box blank for no limit.'
                    )
                self.rows = ui.refreshable(self._render_rows)
                self.rows()
            with dialog_actions():
                ui.space()
                ui.button('Cancel', on_click=self.dialog.close).props('flat')
                ui.button('Save schedule', on_click=self._save).props('color=primary')
        self._build_sheet()
        self.dialog.open()

    def _found_summary(self) -> str:
        real = [s.label.lower() if s.key == 'group' else s.label for s in self.stages if not s.placeholder]
        found = (', '.join(real[:-1]) + (' and ' if len(real) > 1 else '') + real[-1]) if real else 'no matches yet'
        if any(s.placeholder for s in self.stages):
            found += ". The elimination rounds aren't on the bracket yet, so the usual ones are listed to plan ahead"
        return found

    # --- Rows ------------------------------------------------------------

    def _render_rows(self) -> None:
        with ui.element('div').classes('round-schedule__table full-width'):
            with ui.element('div').classes('round-schedule__row round-schedule__row--head'):
                for label in ('Stage', 'Opens', 'Closes', 'Daily hours', 'Booked', ''):
                    ui.label(label)
            for stage in self.stages:
                self._render_row(stage)

    def _render_row(self, stage: ScheduleStage) -> None:
        draft = self.drafts[stage.key]
        hours = (f"{draft['daily_from']} – {draft['daily_until']}"
                 if draft['daily_from'] and draft['daily_until'] else '')
        chip_text, chip_tone = booked_summary(stage)
        row = ui.element('div').classes('round-schedule__row cursor-pointer')
        row.on('click', lambda _, s=stage: self._edit(s))
        with row:
            with ui.column().classes('gap-0 round-schedule__stage'):
                ui.label(stage.label).classes('text-weight-bold')
                ui.label(stage.detail).classes('text-caption text-muted')
            self._cell('Opens', _display(draft['opens']) or 'Any time', not draft['opens'], mono=True)
            self._cell('Closes', _display(draft['closes']) or 'No limit', not draft['closes'], mono=True)
            self._cell('Hours', hours or 'Tournament hours', not hours, mono=bool(hours))
            with ui.element('div').classes('round-schedule__cell round-schedule__booked'):
                ui.label(chip_text).classes('wiz-chip ' + ('wiz-chip--ok' if chip_tone else 'wiz-chip--neutral'))
            with ui.element('div').classes('round-schedule__menu').on('click.stop', lambda: None):
                with more_menu(stage.label):
                    menu_action('edit', 'Edit window…', lambda s=stage: self._edit(s))
                    menu_action('backspace', 'Clear window', lambda s=stage: self._clear(s))

    @staticmethod
    def _cell(label: str, value: str, unset: bool, *, mono: bool = False) -> None:
        with ui.element('div').classes('round-schedule__cell'):
            ui.label(label).classes('round-schedule__k')
            classes = 'text-muted' if unset else ('round-schedule__mono' if mono else '')
            ui.label(value).classes(classes)

    def _clear(self, stage: ScheduleStage) -> None:
        self.drafts[stage.key] = {'opens': '', 'closes': '', 'daily_from': '', 'daily_until': ''}
        self.rows.refresh()

    # --- Editor sheet ------------------------------------------------------

    def _build_sheet(self) -> None:
        # mobile_sheet's breakpoint, but a side sheet on desktop so the table
        # stays readable beside the stage being edited.
        position = ''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"'''
        with ui.dialog().props(position) as self.sheet:  # style-guide: exempt — _render_sheet draws the header
            with ui.card().classes('dialog-card match-sheet round-schedule__sheet'):
                self.sheet_body = ui.refreshable(self._render_sheet)
                self.sheet_body()
        self.editing: Optional[ScheduleStage] = None

    def _edit(self, stage: ScheduleStage) -> None:
        self.editing = stage
        self.sheet_body.refresh()
        self.sheet.open()

    def _render_sheet(self) -> None:
        stage = getattr(self, 'editing', None)
        if stage is None:
            return
        draft = self.drafts[stage.key]
        dialog_header(stage.label, self.sheet)
        with ui.column().classes('match-sheet__body full-width gap-2'):
            ui.label(stage.detail).classes('text-caption text-muted')
            opens = native_datetime_input('Opens', draft['opens'], tz=self.tz).props('outlined dense')
            closes = native_datetime_input('Closes', draft['closes'], tz=self.tz).props('outlined dense')
            daily = ui.switch('Only during set hours each day',
                              value=bool(draft['daily_from'] and draft['daily_until']))
            with ui.row().classes('form-pair items-center gap-2').bind_visibility_from(daily, 'value'):
                start = native_time_input('From', draft['daily_from'] or '18:00', tz=self.tz).props('outlined dense')
                until = native_time_input('Until', draft['daily_until'] or '23:00', tz=self.tz).props('outlined dense')
            ui.label(
                'Players can only book a match that fits wholly inside this. '
                'Staff can still save outside it after a warning.'
            ).classes('text-caption text-muted')
            error = ui.label('').classes('text-negative text-caption')
            error.set_visibility(False)

        def done() -> None:
            o, c = opens.value or '', closes.value or ''
            if o and c and c <= o:
                error.text = 'Closes has to be after Opens.'
                error.set_visibility(True)
                return
            if daily.value and (not start.value or not until.value or start.value == until.value):
                error.text = 'Daily hours need a start and a different end.'
                error.set_visibility(True)
                return
            self.drafts[stage.key] = {
                'opens': o, 'closes': c,
                'daily_from': start.value if daily.value else '',
                'daily_until': until.value if daily.value else '',
            }
            self.sheet.close()
            self.rows.refresh()

        with dialog_actions():
            ui.space()
            ui.button('Cancel', on_click=self.sheet.close).props('flat')
            ui.button('Done', on_click=done).props('color=primary')

    # --- Save ------------------------------------------------------------

    def _entries(self) -> Dict[str, dict]:
        def instant(value: str) -> Optional[datetime]:
            if not value:
                return None
            day, clock = value.split('T', 1)
            return parse_local_datetime(day, clock[:5], self.tz)

        return {
            key: {
                'opens': instant(d['opens']), 'closes': instant(d['closes']),
                'daily_from': d['daily_from'] or None, 'daily_until': d['daily_until'] or None,
            }
            for key, d in self.drafts.items()
        }

    async def _save(self) -> None:
        actor = await get_user_from_session(app.storage.user)
        try:
            with tenant_scope(self.tenant_id):
                await self.service.save_schedule(self.tournament.id, self._entries(), actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Round schedule saved.', color='positive')
        self.dialog.close()
        if self.on_saved:
            await self.on_saved()
