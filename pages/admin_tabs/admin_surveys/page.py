"""Admin → Surveys: the event's surveys, and one survey's builder or results.

``?survey=<id>`` opens one survey: a draft on its builder, a sent one on its
results with a Results / Questions switch (``?view=``).
"""

from typing import Any, Dict, List, Optional

from nicegui import app, context, ui

from application.event_context import event_scope, get_current_event_id
from application.services import (
    SurveyRow,
    SurveyService,
    SurveySuggestionService,
    get_user_from_session,
    notification_links,
)
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.app_links import survey_preview_url
from application.utils.timezone import format_local_display
from models import Survey, SurveyStatus
from pages.admin_tabs.admin_surveys import dialogs
from pages.admin_tabs.admin_surveys.builder import BuilderContext, render_builder
from pages.admin_tabs.admin_surveys.results import render_results
from pages.admin_tabs.admin_surveys.shared import STATUS_BADGES
from theme.a11y import make_activatable
from theme.clipboard import copy_to_clipboard
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, menu_link, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import wire_tab_refresh
from theme.url_state import url_param, url_state

TAB_LABEL = 'Surveys'
RESULTS, QUESTIONS = 'results', 'questions'


def _counts_line(row: SurveyRow) -> str:
    survey = row.survey
    if survey.status is SurveyStatus.DRAFT:
        return f"Draft · {sum(len(s.questions) for s in survey.sections)} questions" \
            if survey.sections else 'Draft'
    rate = f' ({round(row.answered * 100 / row.sent)}%)' if row.sent else ''
    return f'Sent to {row.sent} · {row.answered} answered{rate}'


async def admin_surveys_page(survey: Optional[int] = None, view: Optional[str] = None) -> None:
    service = SurveyService()
    client = context.client
    tenant_id = require_tenant_id()
    event_id = get_current_event_id()
    state: Dict[str, Any] = {'rows': [], 'open': None, 'view': RESULTS}

    async def current():
        return await get_user_from_session(app.storage.user)

    async def load_list() -> None:
        with tenant_scope(tenant_id), event_scope(event_id):
            try:
                state['rows'] = await service.list_overview(await current())
            except (ValueError, PermissionError) as e:
                state['rows'] = []
                with client:
                    notify_error(e)

    async def show(survey_id: Optional[int], view_name: str = RESULTS) -> None:
        state['open'], state['view'] = survey_id, view_name
        async with open_param.changing(survey_id or ''):
            await body.refresh()

    async def restore(value: str) -> None:
        state['open'] = int(value) if value.isdecimal() else None
        await body.refresh()

    async def reload() -> None:
        await body.refresh()

    async def created(new: Survey) -> None:
        await load_list()
        await show(new.id)

    def confirm_delete(target: Survey) -> None:
        sent = target.status is not SurveyStatus.DRAFT
        message = (f'Delete “{target.title}” and every answer it collected? This can’t be undone.'
                   if sent else f'Delete the draft “{target.title}”?')

        async def delete() -> None:
            try:
                await service.delete_survey(await current(), target.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            await load_list()
            await show(None)

        ConfirmationDialog(message, title='Delete survey', confirm_text='Delete', on_confirm=delete,
                           require_phrase='delete' if sent else None).open()

    async def duplicate(target: Survey) -> None:
        try:
            copy = await service.duplicate(await current(), target.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        await created(copy)

    async def copy_preview(target: Survey) -> None:
        link = await notification_links.link_for('Preview', survey_preview_url(target.id))
        if link is None:
            notify_error(ValueError("Couldn't build the link."))
            return
        copy_to_clipboard(link.url, 'Preview link')

    async def remind(target: Survey) -> None:
        try:
            n = await service.remind(await current(), target.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(f'Reminded {n} people who haven’t answered.', color='positive')
        await reload()

    def confirm_close(target: Survey) -> None:
        async def close() -> None:
            try:
                await service.close(await current(), target.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            await reload()

        ConfirmationDialog(f'Stop taking answers to “{target.title}”? A closed survey doesn’t reopen.',
                           title='Close survey', confirm_text='Close survey', on_confirm=close).open()

    def list_view(rows: List[SurveyRow]) -> None:
        with page_header('Surveys', 'Ask the people who took part how it went. Each person gets the '
                         'questions that fit what they did at the event.'):
            ui.space()
            ui.button('New survey', icon='add', on_click=new) \
                .props('unelevated no-caps color=primary')
        if not rows:
            empty_panel('No surveys for this event yet. Start one from the starter questions.', icon='poll')
            return
        with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md w-full'):
            for row in rows:
                s = row.survey
                with ui.card().classes('wiz-survey-card q-pa-md'):
                    with ui.row().classes('no-wrap items-start w-full gap-2'):
                        with ui.column().classes('col min-w-0 gap-1'):
                            name = ui.label(s.title).classes('wiz-survey-card__title')
                            name.on('click', lambda _, sid=s.id: show(sid))
                            make_activatable(name, f'Open {s.title}')
                            ui.label(_counts_line(row)).classes('wiz-survey-card__meta')
                            if s.closes_at and s.status is SurveyStatus.OPEN:
                                ui.label(f'Open until {format_local_display(s.closes_at)}') \
                                    .classes('wiz-survey-card__meta')
                        text, color = STATUS_BADGES[s.status]
                        ui.badge(text, color=color)
                    with ui.row().classes('wiz-card-actions items-center justify-end w-full no-wrap'):
                        ui.button('Results' if s.status is not SurveyStatus.DRAFT else 'Edit',
                                  on_click=lambda _, sid=s.id: show(sid)).props('outline no-caps color=primary')
                        with more_menu(s.title):
                            menu_action('content_copy', 'Duplicate', lambda _, t=s: duplicate(t))
                            ui.separator()
                            menu_action('delete', 'Delete…', lambda _, t=s: confirm_delete(t), negative=True)

    async def new() -> None:
        dialogs.new_survey_dialog(service, await current(), created)

    async def detail_view(survey_id: int) -> None:
        user = await current()
        try:
            s = await service.get_survey(user, survey_id)
        except (ValueError, PermissionError):
            ui.link('← All surveys', '#').on('click.prevent', lambda: show(None)).classes('text-caption')
            empty_panel('That survey is gone. It may have been deleted.', icon='poll')
            return
        ui.link('← All surveys', '#').on('click.prevent', lambda: show(None)).classes('text-caption')
        draft = s.status is SurveyStatus.DRAFT
        with page_header(s.title):
            ui.space()
            text, color = STATUS_BADGES[s.status]
            ui.badge(text, color=color)
            with ui.button('Actions').props('flat no-caps color=primary icon-right=expand_more'):
                with ui.menu():
                    if draft:
                        menu_action('edit', 'Edit title and intro…', lambda: dialogs.details_dialog(
                            service, user, s, lambda _: reload()))
                    menu_action('link', 'Copy preview link', lambda: copy_preview(s))
                    menu_link('visibility', 'Open preview', survey_preview_url(s.id))
                    if s.status is SurveyStatus.OPEN:
                        menu_action('notifications', 'Remind people who haven’t answered', lambda: remind(s))
                    menu_action('content_copy', 'Duplicate', lambda: duplicate(s))
                    if s.status is SurveyStatus.OPEN:
                        menu_action('lock', 'Close survey…', lambda: confirm_close(s))
                    ui.separator()
                    menu_action('delete', 'Delete…', lambda: confirm_delete(s), negative=True)
            if draft:
                summary = await service.builder_summary(user, s.id)
                ui.button('Send…', on_click=lambda: dialogs.send_dialog(
                    service, user, s, summary.counts.get('everyone', 0), lambda _: _after_send(),
                )).props('unelevated no-caps color=primary')
        if s.status is not SurveyStatus.DRAFT:
            closes = f' · open until {format_local_display(s.closes_at)}' \
                if s.closes_at and s.status is SurveyStatus.OPEN else ''
            ui.label(f'Sent {format_local_display(s.sent_at)}{closes}').classes('text-caption text-grey-7')
            switch = ui.toggle({RESULTS: 'Results', QUESTIONS: 'Questions'}, value=state['view']) \
                .props('no-caps dense unelevated').classes('wiz-segmented')
            view_param = url_param('view', switch, default=RESULTS, value=state['view'],
                                   on_restore=lambda v: (state.update(view=v or RESULTS), body.refresh()))

            async def switch_view(value: str) -> None:
                state['view'] = value
                await body.refresh()
                view_param.set(value)

            switch.on_value_change(lambda e: switch_view(e.value))
            if state['view'] == RESULTS:
                render_results(await service.results(user, s.id))
                return
            summary = await service.builder_summary(user, s.id)
        ai_label, suggestions = None, {}
        if s.status is SurveyStatus.DRAFT:
            ai = SurveySuggestionService()
            ai_label = await ai.provider_label(user)
            suggestions = await ai.open_batches(user, s.id)
        render_builder(BuilderContext(service=service, user=user, survey=s, summary=summary, reload=reload,
                                      ai_label=ai_label, suggestions=suggestions))

    async def _after_send() -> None:
        await load_list()
        state['view'] = RESULTS
        await body.refresh()

    @steady_refreshable
    async def body() -> None:
        with tenant_scope(tenant_id), event_scope(event_id):
            if state['open'] is None:
                await load_list()
                list_view(state['rows'])
            else:
                await detail_view(state['open'])

    with ui.column().classes('page-container-wide w-full gap-3 wiz-surveys') as page:
        open_param = url_param('survey', page, default='', value='', on_restore=restore)
        # The address, not the arguments, once the viewer has moved about; the
        # arguments are the request's (a DM's or a bookmark's link).
        asked = url_state().requested('survey') or (str(survey) if survey else '')
        if asked.isdecimal():
            state['open'] = int(asked)
            state['view'] = QUESTIONS if (url_state().requested('view') or view) == QUESTIONS else RESULTS
        await body()
        if state['open'] is not None:
            open_param.set(state['open'], history='replace')

    wire_tab_refresh(TAB_LABEL, reload)
