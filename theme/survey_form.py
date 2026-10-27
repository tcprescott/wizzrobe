"""The survey as a respondent sees it (layout C, Thomas 2026-10-27).

A short list of parts built from what the person did at the event. They open
any part in any order (a side sheet on a desktop, a bottom sheet on a phone),
answer it, and press Done to save it; "Send answers" works with any parts done.
The staff preview draws the same form with nothing saved.

Everything the respondent typed is shown back as text, never as markup.
"""

from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Optional

from nicegui import ui

from application.services import RespondentView
from application.services.survey import AGREE_LEVELS, plan
from models import SurveyQuestion, SurveyQuestionKind
from theme.a11y import make_activatable
from theme.banner import banner
from theme.dialog._helpers import dialog_actions, dialog_header
from theme.notify import notify_error

SavePart = Callable[[str, Dict[int, Any]], Awaitable[RespondentView]]
Submit = Callable[[bool], Awaitable[None]]

RATING_ENDS = ('Poor', 'Great')
NPS_ENDS = ('Not likely', 'Very likely')
COMMENT_MAX = 1000
_SLIDER_KEYS = {'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', ' '}


def _plural(n: int, word: str) -> str:
    return f'{n} {word}' if n == 1 else f'{n} {word}s'


def _minutes(parts: List[plan.Part]) -> str:
    minutes = max(1, round(plan.seconds(parts) / 60))
    return f'About {_plural(minutes, "minute")} · {_plural(len(parts), "short part")}'


def _question_control(question: SurveyQuestion, value: Any, on_change: Callable[[Any], None],
                      enabled: bool) -> None:
    """One question's answer control, bound to ``on_change``."""
    kind = question.kind
    if kind in (SurveyQuestionKind.RATING, SurveyQuestionKind.NPS):
        _scale_slider(question, value, on_change, enabled)
        return
    if kind is SurveyQuestionKind.YES_NO or (kind is SurveyQuestionKind.CHOICE_ONE and len(question.options or []) <= 4):
        options = ['Yes', 'No'] if kind is SurveyQuestionKind.YES_NO else list(question.options or [])
        # url-state: exempt — an answer, not a view
        toggle = ui.toggle(options, value=value, clearable=True, on_change=lambda e: on_change(e.value)) \
            .props('no-caps unelevated').classes('wiz-segmented wiz-survey-pills')
        toggle.props(f'aria-label="{question.prompt}"')
        toggle.set_enabled(enabled)
        return
    if kind is SurveyQuestionKind.CHOICE_ONE:
        radio = ui.radio(list(question.options or []), value=value, on_change=lambda e: on_change(e.value)) \
            .classes('wiz-survey-options')
        radio.props(f'aria-label="{question.prompt}"')
        radio.set_enabled(enabled)
        return
    if kind is SurveyQuestionKind.CHOICE_MANY:
        picked = set(value or [])

        def toggle_option(option: str, checked: bool) -> None:
            if checked:
                picked.add(option)
            else:
                picked.discard(option)
            on_change([o for o in question.options or [] if o in picked])

        with ui.column().classes('wiz-survey-options gap-0'):
            for option in question.options or []:
                ui.checkbox(option, value=option in picked,
                            on_change=lambda e, o=option: toggle_option(o, e.value)).set_enabled(enabled)
        return
    if kind is SurveyQuestionKind.AGREE_SCALE:
        _agree_grid(question, value, on_change, enabled)
        return
    area = ui.textarea(placeholder='Optional', value=value or '', on_change=lambda e: on_change(e.value)) \
        .props(f'outlined autogrow maxlength={COMMENT_MAX} counter').classes('w-full')
    area.props(f'aria-label="{question.prompt}"')
    area.set_enabled(enabled)


def _scale_slider(question: SurveyQuestion, value: Any, on_change: Callable[[Any], None], enabled: bool) -> None:
    """A 1–5 or 0–10 answer as a slider (Thomas picked sliders, 2026-10-27).

    Unanswered, the slider has no handle, so a skipped question never reads as
    its lowest score; the first tap or drag answers it, and Clear takes it back.
    """
    rating = question.kind is SurveyQuestionKind.RATING
    low, high = (1, 5) if rating else (0, 10)
    ends = RATING_ENDS if rating else NPS_ENDS
    state: Dict[str, Any] = {'value': value}

    with ui.column().classes('wiz-survey-slider w-full gap-0'):
        with ui.row().classes('wiz-survey-slider__top w-full items-center no-wrap'):
            hint = ui.label('Not answered yet. Drag or tap the line.').classes('wiz-survey-slider__hint col')
            clear = ui.button('Clear', on_click=lambda: answer(None)).props('flat dense no-caps color=primary') \
                .classes('wiz-survey-slider__clear')
        slider = ui.slider(min=low, max=high, step=1, value=value if value is not None else (low + high) // 2) \
            .props('markers marker-labels snap label-always').classes('wiz-survey-slider__line')
        slider._props['aria-label'] = question.prompt
        with ui.row().classes('wiz-survey-slider__ends w-full justify-between no-wrap'):
            ui.label(ends[0])
            ui.label(ends[1])

    def show() -> None:
        empty = state['value'] is None
        slider.classes(add='is-empty' if empty else '', remove='' if empty else 'is-empty')
        hint.set_visibility(empty and enabled)
        clear.set_visibility(not empty and enabled)

    def answer(picked: Any) -> None:
        state['value'] = None if picked is None else int(picked)
        show()
        on_change(state['value'])

    def released(e) -> None:
        # ``change`` fires when a drag or tap ends; answering on every step of a
        # drag would redraw the slider under the finger.
        picked = e.args if isinstance(e.args, (int, float)) else slider.value
        if picked is not None and picked != state['value']:
            answer(picked)

    def touched(_=None) -> None:
        # A tap on the value the hidden handle already sits at changes nothing,
        # so the first touch answers with whatever the slider now shows.
        if state['value'] is None:
            answer(slider.value)

    def key(e) -> None:
        if (e.args or {}).get('key') in _SLIDER_KEYS:
            touched()

    slider.on('change', released)
    slider.on('pointerup', touched)
    slider.on('keydown', key, args=['key'])
    slider.set_enabled(enabled)
    show()


def _agree_grid(question: SurveyQuestion, value: Any, on_change: Callable[[Any], None], enabled: bool) -> None:
    """One row per statement: a table of radios when there's room, stacked full-word cards when there isn't."""
    statements = list(question.options or [])
    picked: Dict[str, int] = dict(value or {})

    def pick(statement: str, level: Optional[int]) -> None:
        if level is None:
            picked.pop(statement, None)
        else:
            picked[statement] = int(level)
        on_change({s: picked[s] for s in statements if s in picked} or None)

    group = ui.element('div').classes('wiz-agree')
    group._props.update(role='group')
    group._props['aria-label'] = question.prompt
    with group:
        with ui.element('div').classes('wiz-agree__head').props('aria-hidden=true'):
            ui.element('span')
            for label in AGREE_LEVELS:
                ui.label(label)
        for index, statement in enumerate(statements):
            with ui.element('div').classes('wiz-agree__row'):
                ui.label(f'{index + 1} of {len(statements)}').classes('wiz-agree__count')
                ui.label(statement).classes('wiz-agree__statement')
                radio = ui.radio(dict(enumerate(AGREE_LEVELS, 1)), value=picked.get(statement),
                                 on_change=lambda e, s=statement: pick(s, e.value)) \
                    .props('inline').classes('wiz-agree__choices')
                radio._props['aria-label'] = statement
                radio.set_enabled(enabled)


class PartSheet:
    """One part open for answering: its questions, then Back to parts and Done."""

    def __init__(self, form: 'SurveyForm') -> None:
        self.form = form
        self.part: Optional[plan.Part] = None
        self.draft: Dict[int, Any] = {}
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet wiz-survey-sheet') as self.card:
            self.title = dialog_header('Part', self.dialog)
            self.reason = ui.label('').classes('wiz-survey-sheet__reason')
            self.body = ui.refreshable(self._questions)
            self.body()
            with dialog_actions().classes('justify-end'):
                ui.button('Back to parts', on_click=self.dialog.close).props('outline no-caps')
                self.done_button = ui.button('Done', on_click=self._done).props('unelevated no-caps color=primary')

    def open(self, part: plan.Part) -> None:
        self.part = part
        self.draft = self.form.view.answers_in(part)
        self.title.set_text(part.title)
        # An agree scale wants a table's width; the sheet widens for the part that has one.
        wide = any(q.kind is SurveyQuestionKind.AGREE_SCALE for q in part.questions)
        self.card.classes(add='wiz-survey-sheet--wide' if wide else '', remove='' if wide else 'wiz-survey-sheet--wide')
        shown = self.form.view.shown_parts
        position = shown.index(part) + 1 if part in shown else 0
        where = f' · Part {position} of {len(shown)}' if position else ''
        self.reason.set_text(f'{part.reason}{where}')
        self.done_button.set_visibility(self.form.editable)
        self.body.refresh()
        self.dialog.open()

    def _answers_for_visibility(self) -> Dict[plan.AnswerKey, Any]:
        merged = dict(self.form.view.answers)
        if self.part is not None:
            for qid in [q.id for q in self.part.questions]:
                merged.pop((qid, self.part.scope), None)
            for qid, value in self.draft.items():
                if value not in (None, '', []):
                    merged[(qid, self.part.scope)] = value
        return merged

    def _set(self, question: SurveyQuestion, value: Any) -> None:
        self.draft[question.id] = value
        if question.kind is not SurveyQuestionKind.COMMENT:
            self.body.refresh()

    def _questions(self) -> None:
        part = self.part
        if part is None:
            return
        answers = self._answers_for_visibility()
        with ui.column().classes('wiz-survey-sheet__questions w-full gap-0'):
            for question in part.questions:
                if not plan.visible(question, part.scope, answers):
                    continue
                with ui.column().classes('wiz-survey-question w-full gap-2'):
                    with ui.row().classes('items-baseline gap-1'):
                        ui.label(question.prompt).classes('wiz-survey-question__prompt')
                        if question.required:
                            ui.label('Required').classes('wiz-survey-question__required')
                    _question_control(question, self.draft.get(question.id),
                                      partial(self._set, question), self.form.editable)

    async def _done(self) -> None:
        part = self.part
        if part is None:
            return
        answers = self._answers_for_visibility()
        showing = {q.id for q in part.questions if plan.visible(q, part.scope, answers)}
        # A follow-up that stopped showing loses its answer, so nothing hidden is saved.
        payload = {q.id: (self.draft.get(q.id) if q.id in showing else None) for q in part.questions}
        try:
            self.form.view = await self.form.save_part(part.key, payload)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        self.dialog.close()
        self.form.parts_list.refresh()


class SurveyForm:
    """The respondent's survey page body, shared with the staff preview."""

    def __init__(self, view: RespondentView, *, save_part: SavePart, submit: Submit,
                 editable: bool, preview: bool = False, community: str = '') -> None:
        self.view = view
        self.save_part = save_part
        self.submit = submit
        self.editable = editable
        self.preview = preview
        self.anonymous = False
        survey = view.survey
        with ui.column().classes('page-container-narrow w-full gap-3 wiz-survey'):
            ui.label(' · '.join(x for x in (community, 'Survey') if x)).classes('wiz-survey__kicker')
            heading = ui.label(survey.title).classes('wiz-survey__title')
            heading.tag = 'h1'
            ui.label(_minutes(view.parts)).classes('wiz-survey__meta')
            if survey.intro:
                ui.label(survey.intro).classes('wiz-survey__intro')
            self.status_slot = ui.column().classes('w-full gap-0')
            with self.status_slot:
                self._status()
            if survey.allow_anonymous and not view.submitted:
                with ui.row().classes('wiz-survey-anon no-wrap items-start w-full'):
                    switch = ui.switch(on_change=lambda e: setattr(self, 'anonymous', bool(e.value)))
                    switch.props('aria-label="Answer anonymously"')
                    switch.set_enabled(editable or preview)
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label('Answer anonymously').classes('wiz-survey-anon__title')
                        ui.label(
                            'Staff see only which tournament or role you answered about, not your name. '
                            'Groups under 5 people are hidden.',
                        ).classes('wiz-survey-anon__text')
            self.parts_list = ui.refreshable(self._parts)
            self.parts_list()
        self.sheet = PartSheet(self)

    def _status(self) -> None:
        if self.view.submitted:
            with banner("Your answers are in. Thanks for taking the time.", title='Sent', tone='ok'):
                pass
        elif not self.editable and not self.preview:
            with banner("This survey is closed, so it isn't taking answers any more.", tone='warning'):
                pass

    def _parts(self) -> None:
        view = self.view
        shown = view.shown_parts
        done = sum(1 for p in shown if view.done(p))
        with ui.element('div').classes('wiz-survey-parts w-full'):
            for part in shown:
                is_done = view.done(part)
                with ui.row().classes('wiz-survey-part no-wrap items-center') as row:
                    with ui.element('span').classes('wiz-survey-part__icon' + (' is-done' if is_done else '')):
                        ui.icon('check' if is_done else part.icon).props('aria-hidden=true')
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label(part.title).classes('wiz-survey-part__title')
                        count = len(view.showing(part))
                        ui.label(f'{_plural(count, "question")} · {part.reason[0].lower()}{part.reason[1:]}') \
                            .classes('wiz-survey-part__meta')
                    if is_done:
                        ui.badge('Done', color='positive')
                    else:
                        ui.icon('chevron_right').classes('text-grey-7').props('aria-hidden=true')
                row.on('click', lambda _, p=part: self.sheet.open(p))
                make_activatable(row, f'Open {part.title}')
            with ui.row().classes('wiz-survey-parts__foot items-center no-wrap w-full'):
                if view.submitted:
                    ui.label('Sent. Open a part to see what you answered.') \
                        .classes('wiz-survey-parts__count col min-w-0')
                    return
                ui.label(f'{done} of {_plural(len(shown), "part")} done · answers save as you go') \
                    .classes('wiz-survey-parts__count col min-w-0')
                send = ui.button('Send answers', on_click=self._send).props('unelevated no-caps color=primary')
                if self.preview:
                    send.disable()
                    send.tooltip("Turned off in the preview")
                elif not self.editable:
                    send.disable()

    async def _send(self) -> None:
        try:
            await self.submit(self.anonymous)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        self.view.submitted = True
        self.editable = False
        self.status_slot.clear()
        with self.status_slot:
            self._status()
        self.parts_list.refresh()
        ui.notify('Answers sent. Thank you!', color='positive')
