"""The post-event survey page and its staff preview.

``/survey/<id>`` is where the people a survey was sent to answer it (the DM's
button lands here). ``/survey/<id>/preview`` shows staff the same form as one
kind of person would see it, with a banner saying nothing is saved; a staff
member can share that link with other staff before sending.
"""

from typing import Any, Callable, Dict, Optional

from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.services import (
    AuthService,
    RespondentView,
    SurveyResponseService,
    SurveyService,
    TenantService,
    get_user_from_session,
)
from application.services.survey import coerce_answer, plan
from middleware.auth import protected_page
from middleware.event_routing import page_request, row_event_redirect
from models import FeatureFlag, User
from theme.a11y import make_activatable
from theme.banner import banner
from theme.base import BaseLayout
from theme.error_page import render_not_found
from theme.survey_form import SurveyForm
from theme.url_state import bind_url

__all__ = ['create']


async def _chrome(user: User, title: str) -> str:
    community = await TenantService.current_community_name() or 'Wizzrobe'
    ui.page_title(f'{title} — {community}')
    await BaseLayout(user=user, show_admin=await AuthService.can_open_admin(user)).render()
    return community


def create() -> None:
    @protected_page('/survey/{survey_id}', feature=FeatureFlag.SURVEYS, scope='row')
    async def survey_page(survey_id: int) -> Optional[RedirectResponse]:
        user = await get_user_from_session(app.storage.user)
        if user is None:
            ui.label('Your account could not be found.').classes('text-error')
            return None
        service = SurveyResponseService()
        try:
            view = await service.open_survey(user, survey_id)
        except (ValueError, PermissionError):
            await render_not_found(
                user=user, message="We couldn't find that survey. It may have been deleted, or it wasn't sent to you.",
            )
            return None
        redirect = await row_event_redirect(page_request(), view.survey.event_id)
        if redirect is not None:
            return redirect
        community = await _chrome(user, view.survey.title)

        async def save_part(part_key: str, answers: Dict[int, Any]) -> RespondentView:
            return await service.save_part(user, survey_id, part_key, answers)

        async def submit(anonymous: bool) -> None:
            await service.submit(user, survey_id, anonymous=anonymous)

        async def opened_part(part_key: str) -> None:
            await service.open_part(user, survey_id, part_key)

        SurveyForm(view, save_part=save_part, submit=submit, editable=view.open, community=community,
                   opened_part=opened_part)
        return None

    def _paths_table(rows: list, selected: str, pick: Callable[[str], None]) -> None:
        with ui.element('div').classes('wiz-survey-paths w-full'):
            with ui.element('div').classes('wiz-survey-paths__row is-head').props('aria-hidden=true'):
                for heading in ('Who', 'Parts', 'Questions', 'Time'):
                    ui.label(heading)
            for row in rows:
                classes = 'wiz-survey-paths__row' + (' is-on' if row.persona == selected else '')
                time = plan.format_duration(row.seconds)
                with ui.element('div').classes(classes) as line:
                    ui.label(row.label)
                    ui.label(str(row.parts))
                    ui.label(str(row.questions))
                    ui.label(time)
                line.on('click', lambda _, p=row.persona: pick(p))
                make_activatable(line, f'{row.label}: {row.parts} parts, {row.questions} questions, about {time}. '
                                       'Show the preview as them.')
                if row.persona == selected:
                    line.props('aria-current=true')

    @protected_page('/survey/{survey_id}/preview', feature=FeatureFlag.SURVEYS, scope='row')
    async def survey_preview_page(survey_id: int) -> Optional[RedirectResponse]:
        user = await get_user_from_session(app.storage.user)
        if user is None:
            ui.label('Your account could not be found.').classes('text-error')
            return None
        service = SurveyService()
        try:
            survey, _ = await service.preview(user, survey_id)
        except (ValueError, PermissionError):
            await render_not_found(user=user, message="We couldn't find that survey preview.")
            return None
        redirect = await row_event_redirect(page_request(), survey.event_id)
        if redirect is not None:
            return redirect
        community = await _chrome(user, f'Preview: {survey.title}')

        with ui.column().classes('page-container-narrow w-full gap-2 wiz-survey-preview'):
            with banner(
                "Answers here aren't saved, Send answers is turned off, and nothing is recorded. "
                'Only staff of this community can open this link.',
                title='Preview', tone='warning',
            ):
                pass
            paths = {row.persona: row for row in await service.preview_paths(user, survey_id)}
            paths_table = ui.refreshable(_paths_table)
            paths_table(list(paths.values()), 'everything', lambda persona: setattr(showing, 'value', persona))
            showing = ui.select({'everything': plan.PREVIEW_PERSONAS['everything'],
                                 **{p: row.label for p, row in paths.items()}}, value='everything', label='Showing') \
                .props('outlined dense').classes('wiz-survey-preview__showing')

        @ui.refreshable
        def form(parts: list, hidden: list) -> None:
            view = RespondentView(survey=survey, recipient=None, parts=parts)

            async def save_part(part_key: str, answers: Dict[int, Any]) -> RespondentView:
                part = view.part(part_key)
                if part is None:
                    raise ValueError("That part isn't in this preview.")
                by_id = {q.id: q for q in part.questions}
                for qid, raw in answers.items():
                    key = (qid, part.scope)
                    view.answers.pop(key, None)
                    view.texts.pop(key, None)
                    value, text = coerce_answer(by_id[qid], raw)
                    if value is not None:
                        view.answers[key] = value
                    if text:
                        view.texts[key] = text
                return view

            async def submit(_anonymous: bool) -> None:
                raise ValueError('Sending is turned off in the preview.')

            SurveyForm(view, save_part=save_part, submit=submit, editable=True, preview=True, community=community,
                       hidden=hidden)

        async def switch(persona: str) -> None:
            try:
                _, parts = await service.preview(user, survey_id, persona)
            except (ValueError, PermissionError):
                return
            row = paths.get(persona)
            paths_table.refresh(list(paths.values()), persona, lambda p: setattr(showing, 'value', p))
            form.refresh(parts, row.hidden if row else [])

        _, first_parts = await service.preview(user, survey_id, 'everything')
        form(first_parts, [])
        showing.on_value_change(lambda e: switch(e.value))
        bind_url('showing', showing)
        return None
