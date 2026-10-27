"""The survey builder's forms: new survey, details, section, question, send."""

from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, Optional

from nicegui import ui

from application.services import SurveyService
from application.services.survey import answer_values, conditions, plan
from application.utils.timezone import now_local
from models import Survey, SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveySection, User
from pages.admin_tabs.admin_surveys.shared import AUDIENCE_CHIPS
from theme.busy import busy_click
from theme.dialog._helpers import (
    dialog_actions,
    form_dialog,
    local_input_to_datetime,
    native_datetime_input,
    submit_on_enter,
)
from theme.notify import notify_error

Done = Callable[[Any], Awaitable[None]]
ALWAYS = ''
CHOICE_KINDS = (SurveyQuestionKind.CHOICE_ONE, SurveyQuestionKind.CHOICE_MANY)


def _footer(dialog, label: str, on_save) -> None:
    with dialog_actions().classes('justify-end'):
        ui.button('Cancel', on_click=dialog.close).props('flat no-caps')
        ui.button(label, on_click=on_save).props('unelevated no-caps color=primary')
    submit_on_enter(dialog, on_save)


def new_survey_dialog(service: SurveyService, user: User, on_done: Done) -> None:
    with form_dialog('New survey', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('w-full gap-2'):
            title = ui.input('Title', value='How was the event?').props('outlined dense maxlength=120') \
                .classes('w-full')
            template = ui.checkbox('Start from the starter questions', value=True)
            ui.label('You can change every question before you send it.').classes('text-caption text-grey-7')

        async def save() -> None:
            try:
                survey = await service.create_survey(user, title.value or '', from_template=bool(template.value))
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            await on_done(survey)

        _footer(dialog, 'Create', save)
    dialog.open()


def details_dialog(service: SurveyService, user: User, survey: Survey, on_done: Done) -> None:
    with form_dialog('Survey details') as dialog:
        with ui.column().classes('w-full gap-2'):
            title = ui.input('Title', value=survey.title).props('outlined dense maxlength=120').classes('w-full')
            intro = ui.textarea('Intro (optional)', value=survey.intro or '') \
                .props('outlined autogrow maxlength=2000').classes('w-full')
            ui.label('Shown above the parts. Plain text.').classes('text-caption text-grey-7')

        async def save() -> None:
            try:
                updated = await service.update_survey(user, survey.id, title=title.value or '', intro=intro.value or '')
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            await on_done(updated)

        _footer(dialog, 'Save', save)
    dialog.open()


def section_dialog(service: SurveyService, user: User, survey: Survey, section: Optional[SurveySection],
                   on_done: Done) -> None:
    with form_dialog('Edit section' if section else 'Add section') as dialog:
        with ui.column().classes('w-full gap-2'):
            title = ui.input('Title', value=section.title if section else '') \
                .props('outlined dense maxlength=120').classes('w-full')
            audience = ui.select(
                {a.value: AUDIENCE_CHIPS[a] for a in SurveyAudience},
                value=(section.audience if section else SurveyAudience.EVERYONE).value, label='Who sees it',
            ).props('outlined dense').classes('w-full')
            ui.label(
                'A repeating section shows once for every tournament a person played, or every '
                'position they worked, titled with its name. Keep those short.',
            ).classes('text-caption text-grey-7')

        async def save() -> None:
            try:
                if section is None:
                    await service.add_section(user, survey.id, title.value or '', SurveyAudience(audience.value))
                else:
                    await service.update_section(user, section.id, title=title.value or '',
                                                 audience=SurveyAudience(audience.value))
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            await on_done(None)

        _footer(dialog, 'Save' if section else 'Add', save)
    dialog.open()


def question_dialog(service: SurveyService, user: User, survey: Survey, section: SurveySection,
                    question: Optional[SurveyQuestion], on_done: Done) -> None:
    """Add or edit a question, with its one "Only show if…" condition."""
    earlier: Dict[int, SurveyQuestion] = {
        qid: q for qid, q in plan.earlier(survey.sections, section.id, question.id if question else None).items()
        if answer_values(q)
    }
    args = (question.condition_args or {}) if question else {}
    state: Dict[str, Any] = {
        'kind': (question.kind if question else SurveyQuestionKind.RATING).value,
        'condition': (question.condition or ALWAYS) if question else ALWAYS,
        'follow': args.get('question_id'),
        'values': list(args.get('values') or []),
    }

    with form_dialog('Edit question' if question else 'Add question') as dialog:
        with ui.column().classes('w-full gap-2'):
            prompt = ui.input('Question', value=question.prompt if question else '') \
                .props('outlined dense maxlength=280').classes('w-full')
            with ui.element('div').classes('form-pair w-full'):
                kind = ui.select({k.value: plan.KIND_LABELS[k] for k in SurveyQuestionKind}, value=state['kind'],
                                 label='Type').props('outlined dense')
                required = ui.switch('Required', value=bool(question.required) if question else False)
            options = ui.textarea(
                'Choices, one per line', value='\n'.join(question.options or []) if question else '',
            ).props('outlined autogrow').classes('w-full')

            ui.label('Only show if…').classes('wiz-survey-field-label')
            condition_choices = {ALWAYS: 'Always show', **conditions.choices()}
            if not earlier:
                condition_choices.pop(conditions.ANSWER, None)
            condition = ui.radio(condition_choices, value=state['condition']).classes('wiz-survey-conditions w-full')
            condition.props('aria-label="Only show if"')

            follow_box = ui.column().classes('w-full gap-2 wiz-survey-follow')

        @ui.refreshable
        def follow_fields() -> None:
            if state['condition'] != conditions.ANSWER:
                return
            target = ui.select({qid: q.prompt for qid, q in earlier.items()}, value=state['follow'],
                               label='Earlier question').props('outlined dense').classes('w-full')
            target.on_value_change(lambda e: (state.update(follow=e.value, values=[]), follow_fields.refresh()))
            picked = earlier.get(state['follow'])
            if picked is not None:
                values = answer_values(picked)
                ui.select({v: str(v) for v in values}, multiple=True, value=state['values'],
                          label='Answers that show this question',
                          on_change=lambda e: state.update(values=list(e.value or []))) \
                    .props('outlined dense use-chips').classes('w-full')

        with follow_box:
            follow_fields()

        def kind_changed(value: str) -> None:
            state['kind'] = value
            options.set_visibility(SurveyQuestionKind(value) in CHOICE_KINDS)

        kind.on_value_change(lambda e: kind_changed(e.value))
        kind_changed(state['kind'])
        condition.on_value_change(lambda e: (state.update(condition=e.value), follow_fields.refresh()))

        async def save() -> None:
            chosen = SurveyQuestionKind(state['kind'])
            lines = [line for line in (options.value or '').splitlines() if line.strip()]
            key = state['condition'] or None
            cargs = {'question_id': state['follow'], 'values': state['values']} if key == conditions.ANSWER else None
            try:
                if question is None:
                    await service.add_question(
                        user, section.id, prompt.value or '', chosen, options=lines, required=bool(required.value),
                        condition=key, condition_args=cargs,
                    )
                else:
                    await service.update_question(
                        user, question.id, prompt=prompt.value or '', kind=chosen, options=lines,
                        required=bool(required.value), condition=key, condition_args=cargs,
                    )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            await on_done(None)

        _footer(dialog, 'Save' if question else 'Add', save)
    dialog.open()


def send_dialog(service: SurveyService, user: User, survey: Survey, reach: int, on_done: Done) -> None:
    default_close = now_local().replace(hour=23, minute=59, second=0, microsecond=0) + timedelta(days=7)
    with form_dialog('Send survey', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('w-full gap-2'):
            ui.label(
                f'This DMs about {reach} people who took part, each with the parts that fit what they did. '
                "Once it's sent, the questions can't change."
            )
            closes = native_datetime_input('Open until', default_close.strftime('%Y-%m-%dT%H:%M')).classes('w-full')

        async def send() -> None:
            try:
                count = await service.send(user, survey.id, closes_at=local_input_to_datetime(closes.value))
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            ui.notify(f'Sent to {count} people.', color='positive')
            await on_done(None)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat no-caps')
            ui.button('Send survey', on_click=busy_click('Sending…', send)).props('unelevated no-caps color=primary')
    dialog.open()
