"""The survey builder's forms: new survey, details, section, question, send."""

from datetime import timedelta
from typing import Any, Awaitable, Callable, List, Mapping, Optional

from nicegui import ui

from application.services import SurveyService
from application.services.survey import AGREE_LEVELS, answer_values, conditions, plan
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
from theme.pickers import CHIP_PROPS

Done = Callable[[Any], Awaitable[None]]
CHOICE_KINDS = (SurveyQuestionKind.CHOICE_ONE, SurveyQuestionKind.CHOICE_MANY)


def _footer(dialog, label: str, on_save) -> None:
    with dialog_actions().classes('justify-end'):
        ui.button('Cancel', on_click=dialog.close).props('flat no-caps')
        ui.button(label, on_click=on_save).props('unelevated no-caps color=primary')
    submit_on_enter(dialog, on_save)


class ConditionEditor:
    """The "Only show if…" rows of a question or section: up to ``limit``, all must hold.

    ``earlier`` holds the questions an answer-based row may wait on; with none,
    that choice isn't offered.
    """

    def __init__(self, earlier: Mapping[int, SurveyQuestion], entries: Optional[List[dict]], *,
                 limit: int, noun: str) -> None:
        self.earlier = {qid: q for qid, q in earlier.items() if answer_values(q)}
        self.rows: List[dict] = [dict(e) for e in entries or []]
        self.limit = limit
        self.noun = noun
        self.choices = dict(conditions.choices())
        if not self.earlier:
            self.choices.pop(conditions.ANSWER, None)
        hint = 'all must be true' if limit > 1 else 'one condition'
        ui.label(f'Only show if… ({hint})').classes('wiz-survey-field-label')
        with ui.column().classes('wiz-survey-conditions w-full gap-2'):
            self._render = ui.refreshable(self._rows)
            self._render()

    def _rows(self) -> None:
        if not self.rows:
            ui.label(f'Always shows. Add a condition to show this {self.noun} to fewer people.') \
                .classes('text-caption text-grey-7')
        for index, row in enumerate(self.rows):
            with ui.row().classes('wiz-survey-condition no-wrap items-start w-full'):
                with ui.column().classes('col min-w-0 gap-2'):
                    ui.select(self.choices, value=row.get('key'), label=f'Condition {index + 1}',
                              on_change=lambda e, r=row: self._set_key(r, e.value)) \
                        .props('outlined dense').classes('w-full')
                    if row.get('key') == conditions.ANSWER:
                        self._answer_fields(row)
                ui.button(icon='close', on_click=lambda _, i=index: self._remove(i)) \
                    .props('flat round dense').tooltip('Remove this condition')
        if len(self.rows) < self.limit:
            ui.button('Add condition', icon='add', on_click=self._add).props('flat no-caps color=primary dense')

    def _answer_fields(self, row: dict) -> None:
        ui.select({qid: q.prompt for qid, q in self.earlier.items()}, value=row.get('question_id'),
                  label='Earlier question',
                  on_change=lambda e, r=row: self._set_target(r, e.value)) \
            .props('outlined dense').classes('w-full')
        picked = self.earlier.get(row.get('question_id') or 0)
        if picked is not None:
            ui.select({v: str(v) for v in answer_values(picked)}, multiple=True, value=list(row.get('values') or []),
                      label='Any of these answers',
                      on_change=lambda e, r=row: r.update(values=list(e.value or []))) \
                .props(f'outlined dense {CHIP_PROPS}').classes('w-full')

    def _set_key(self, row: dict, key: str) -> None:
        row.clear()
        row['key'] = key
        self._render.refresh()

    def _set_target(self, row: dict, question_id: int) -> None:
        row.update(question_id=question_id, values=[])
        self._render.refresh()

    def _add(self) -> None:
        self.rows.append({'key': next(iter(self.choices))})
        self._render.refresh()

    def _remove(self, index: int) -> None:
        self.rows.pop(index)
        self._render.refresh()

    @property
    def value(self) -> List[dict]:
        return [dict(r) for r in self.rows if r.get('key')]


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
            description = ui.textarea('Description (optional)', value=(section.description or '') if section else '') \
                .props('outlined autogrow maxlength=240 rows=2').classes('w-full')
            ui.label('A line or two under the title, saying what the part is about.') \
                .classes('text-caption text-grey-7')
            audience = ui.select(
                {a.value: AUDIENCE_CHIPS[a] for a in SurveyAudience},
                value=(section.audience if section else SurveyAudience.EVERYONE).value, label='Who sees it',
            ).props('outlined dense').classes('w-full')
            ui.label(
                'A repeating section shows once for every tournament a person played, or every '
                'position they worked, titled with its name. Keep those short.',
            ).classes('text-caption text-grey-7')
            earlier = plan.before_section(survey.sections, section.id) if section else \
                {q.id: q for s in survey.sections for q in s.questions}
            editor = ConditionEditor(earlier, section.conditions if section else None,
                                     limit=conditions.MAX_PER_SECTION, noun='section')

        async def save() -> None:
            try:
                if section is None:
                    await service.add_section(user, survey.id, title.value or '', SurveyAudience(audience.value),
                                              conditions=editor.value, description=description.value)
                else:
                    await service.update_section(user, section.id, title=title.value or '',
                                                 audience=SurveyAudience(audience.value), conditions=editor.value,
                                                 description=description.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            await on_done(None)

        _footer(dialog, 'Save' if section else 'Add', save)
    dialog.open()


def question_dialog(service: SurveyService, user: User, survey: Survey, section: SurveySection,
                    question: Optional[SurveyQuestion], on_done: Done) -> None:
    """Add or edit a question, with up to three "Only show if…" conditions."""
    initial_kind = (question.kind if question else SurveyQuestionKind.RATING).value

    with form_dialog('Edit question' if question else 'Add question') as dialog:
        with ui.column().classes('w-full gap-2'):
            prompt = ui.input('Question', value=question.prompt if question else '') \
                .props('outlined dense maxlength=280').classes('w-full')
            with ui.element('div').classes('form-pair w-full'):
                kind = ui.select({k.value: plan.KIND_LABELS[k] for k in SurveyQuestionKind}, value=initial_kind,
                                 label='Type').props('outlined dense')
                required = ui.switch('Required', value=bool(question.required) if question else False)
            options = ui.textarea(
                'Choices, one per line', value='\n'.join(question.options or []) if question else '',
            ).props('outlined autogrow').classes('w-full')
            scale_hint = ui.label('Each statement is answered ' + ' · '.join(AGREE_LEVELS) + '.') \
                .classes('text-caption text-grey-7')
            with ui.column().classes('wiz-survey-extras w-full gap-1') as choice_extras:
                other = ui.switch('Add “Other” with a write-in box', value=bool(question and question.other_option))
                with ui.column().classes('w-full gap-1') as many_extras:
                    none_on = ui.switch('Add “None of these”', value=bool(question and question.none_option))
                    none_label = ui.input('“None” choice says', value=(question.none_option if question and
                                                                       question.none_option else 'None of these')) \
                        .props('outlined dense maxlength=60').classes('wiz-survey-extras__sub')
                    limit_on = ui.switch('Limit how many they pick',
                                         value=bool(question and (question.min_picks or question.max_picks)))
                    with ui.row().classes('wiz-survey-extras__sub items-center no-wrap gap-2') as limits:
                        low = ui.number('At least', value=question.min_picks if question else None,
                                        min=1, max=11, step=1, format='%d').props('outlined dense').classes('w-24')
                        ui.label('to')
                        high = ui.number('At most', value=(question.max_picks if question and question.max_picks
                                                           else 3), min=1, max=11, step=1, format='%d') \
                            .props('outlined dense').classes('w-24')
            long_answer = ui.switch('Long answer', value=bool(question and question.long_answer))
            long_hint = ui.label('A taller box with a character count, for the one “anything else?” question.') \
                .classes('text-caption text-grey-7')
            editor = ConditionEditor(
                plan.earlier(survey.sections, section.id, question.id if question else None),
                question.conditions if question else None, limit=conditions.MAX_PER_QUESTION, noun='question',
            )

        def kind_changed(value: str) -> None:
            chosen = SurveyQuestionKind(value)
            agree = chosen is SurveyQuestionKind.AGREE_SCALE
            options.set_visibility(chosen in CHOICE_KINDS or agree)
            options.props(f'label="{"Statements, one per line (2 to 10)" if agree else "Choices, one per line"}"')
            scale_hint.set_visibility(agree)
            choice_extras.set_visibility(chosen in CHOICE_KINDS)
            many_extras.set_visibility(chosen is SurveyQuestionKind.CHOICE_MANY)
            comment = chosen is SurveyQuestionKind.COMMENT
            long_answer.set_visibility(comment)
            long_hint.set_visibility(comment)
            switches_changed()

        def switches_changed() -> None:
            none_label.set_visibility(bool(none_on.value))
            limits.set_visibility(bool(limit_on.value))

        kind.on_value_change(lambda e: kind_changed(e.value))
        none_on.on_value_change(lambda _: switches_changed())
        limit_on.on_value_change(lambda _: switches_changed())
        kind_changed(initial_kind)

        def extras() -> dict:
            limited = bool(limit_on.value)
            return {
                'other_option': bool(other.value),
                'none_option': (none_label.value or '').strip() or None if none_on.value else None,
                'min_picks': low.value if limited else None,
                'max_picks': high.value if limited else None,
                'long_answer': bool(long_answer.value),
            }

        async def save() -> None:
            chosen = SurveyQuestionKind(kind.value)
            lines = [line for line in (options.value or '').splitlines() if line.strip()]
            try:
                if question is None:
                    await service.add_question(
                        user, section.id, prompt.value or '', chosen, options=lines, required=bool(required.value),
                        conditions=editor.value, **extras(),
                    )
                else:
                    await service.update_question(
                        user, question.id, prompt=prompt.value or '', kind=chosen, options=lines,
                        required=bool(required.value), conditions=editor.value, **extras(),
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
