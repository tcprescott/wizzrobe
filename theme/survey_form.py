"""The survey as a respondent sees it (layout C, Thomas 2026-10-27).

A short list of parts built from what the person did at the event. They open
any part in any order (a side sheet on a desktop, a bottom sheet on a phone),
answer it, and close it; however the sheet closes (Done, Back to parts, the
close button, Escape or a tap outside), what's in it is saved. "Send answers"
works with any parts done.
The staff preview draws the same form with nothing saved.

Everything the respondent typed is shown back as text, never as markup.
"""

import logging
from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from nicegui import background_tasks, ui

from application.services import MissingAnswer, RespondentView
from application.services.survey import AGREE_LEVELS, plan
from application.services.survey._validation import (
    LONG_COMMENT_MAX,
    OTHER,
    OTHER_TEXT_MAX,
    answer_values,
    limit_hint,
    pick_problem,
    split_other,
)
from application.services.survey.conditions import answer_entries, describe
from models import SurveyQuestion, SurveyQuestionKind
from theme.a11y import make_activatable
from theme.banner import banner
from theme.dialog._helpers import dialog_actions, dialog_header
from theme.notify import notify_error

logger = logging.getLogger(__name__)

SavePart = Callable[[str, Dict[int, Any]], Awaitable[RespondentView]]
Submit = Callable[[bool], Awaitable[None]]
OpenedPart = Callable[[str], Awaitable[None]]

RATING_ENDS = ('Poor', 'Great')
NPS_ENDS = ('Not likely', 'Very likely')
COMMENT_MAX = 1000
_SLIDER_KEYS = {'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', ' '}


def _plural(n: int, word: str) -> str:
    return f'{n} {word}' if n == 1 else f'{n} {word}s'


def _minutes(parts: List[plan.Part]) -> str:
    minutes = max(1, round(plan.seconds(parts) / 60))
    return f'About {_plural(minutes, "minute")} · {_plural(len(parts), "short part")}'


# quiet is for typing, which mustn't redraw the part under the cursor.
Changed = Callable[[Any, bool], None]


def _question_control(question: SurveyQuestion, value: Any, on_change: Changed, enabled: bool) -> None:
    """One question's answer control, bound to ``on_change``."""
    kind = question.kind
    if kind in (SurveyQuestionKind.RATING, SurveyQuestionKind.NPS):
        _scale_slider(question, value, lambda v: on_change(v, False), enabled)
        return
    if kind is SurveyQuestionKind.YES_NO:
        # url-state: exempt — an answer, not a view
        toggle = ui.toggle(['Yes', 'No'], value=value, clearable=True, on_change=lambda e: on_change(e.value, False)) \
            .props('no-caps unelevated').classes('wiz-segmented wiz-survey-pills')
        toggle._props['aria-label'] = question.prompt
        toggle.set_enabled(enabled)
        return
    if kind in (SurveyQuestionKind.CHOICE_ONE, SurveyQuestionKind.CHOICE_MANY):
        _choices(question, value, on_change, enabled)
        return
    if kind is SurveyQuestionKind.AGREE_SCALE:
        _agree_grid(question, value, lambda v: on_change(v, False), enabled)
        return
    _comment(question, value, on_change, enabled)


def _comment(question: SurveyQuestion, value: Any, on_change: Changed, enabled: bool) -> None:
    if not question.long_answer:
        area = ui.textarea(placeholder='Optional', value=value or '', on_change=lambda e: on_change(e.value, True)) \
            .props(f'outlined autogrow maxlength={COMMENT_MAX} counter').classes('w-full')
        area._props['aria-label'] = question.prompt
        area.set_enabled(enabled)
        return
    ui.label(f'Up to {LONG_COMMENT_MAX:,} characters.').classes('wiz-survey-question__hint')
    area = ui.textarea(value=value or '').props('outlined').classes('w-full wiz-survey-long')
    area._props['aria-label'] = question.prompt
    count = ui.label('').classes('wiz-survey-count').props('aria-live=polite')

    def counted(text: str) -> None:
        left = LONG_COMMENT_MAX - len(text or '')
        if left >= 0:
            count.set_text(f'You have {left:,} character{"" if left == 1 else "s"} left')
        else:
            count.set_text(f'You have {-left:,} character{"" if left == -1 else "s"} too many')
        count.classes(add='wiz-survey-count--over' if left < 0 else '', remove='' if left < 0 else 'wiz-survey-count--over')

    def typed(e) -> None:
        counted(e.value)
        on_change(e.value, True)

    area.on_value_change(typed)
    area.set_enabled(enabled)
    counted(area.value)


def _choices(question: SurveyQuestion, value: Any, on_change: Changed, enabled: bool) -> None:
    """Pick one or pick many, with the "Other" write-in, the exclusive "none" choice and pick limits.

    The write-in box is always there with its own label; typing in it picks
    Other. A problem (too many picks, Other left blank) shows in red under
    the question as it happens and never blocks a tap.
    """
    many = question.kind is SurveyQuestionKind.CHOICE_MANY
    raw, other_text = split_other(value)
    picked = list(raw or []) if many else ([] if raw in (None, '') else [raw])
    state: Dict[str, Any] = {'picked': picked, 'other': other_text, 'syncing': False}
    none = question.none_option
    regular = [o for o in answer_values(question) if o != none]
    widgets: Dict[str, Any] = {}

    def emit(quiet: bool) -> None:
        order = answer_values(question)
        chosen = [o for o in order if o in state['picked']]
        payload: Any = chosen if many else (chosen[0] if chosen else None)
        if question.other_option and state['other'].strip():
            payload = {'value': payload, 'other': state['other']}
        problem = pick_problem(question, chosen, state['other'])
        error.set_text(problem or '')
        error.set_visibility(bool(problem))
        on_change(payload, quiet)

    def pick_one(choice: Any) -> None:
        if state['syncing']:
            return
        state['picked'] = [] if choice in (None, '') else [choice]
        emit(False)

    def tick(option: str, checked: bool) -> None:
        if state['syncing']:
            return
        current = [o for o in state['picked'] if o != option]
        if checked:
            current = [option] if option == none else [o for o in current if o != none] + [option]
        state['picked'] = current
        emit(False)

    def typed(text: str) -> None:
        state['other'] = text or ''
        if state['other'].strip() and OTHER not in state['picked']:
            state['syncing'] = True
            try:
                if many:
                    state['picked'] = [o for o in state['picked'] if o != none] + [OTHER]
                    widgets[OTHER].set_value(True)
                    if none in widgets:
                        widgets[none].set_value(False)
                else:
                    state['picked'] = [OTHER]
                    widgets['one'].set_value(OTHER)
            finally:
                state['syncing'] = False
        emit(True)

    def other_box() -> None:
        with ui.column().classes('wiz-survey-other w-full gap-1'):
            box = ui.input('If other, tell us what', value=state['other'],
                           on_change=lambda e: typed(e.value)) \
                .props(f'outlined dense maxlength={OTHER_TEXT_MAX}').classes('w-full')
            box.set_enabled(enabled)

    hint = limit_hint(question) if many else ''
    if hint:
        ui.label(hint).classes('wiz-survey-question__hint')
    error = ui.label('').classes('wiz-survey-error').props('role=alert')
    error.set_visibility(False)
    if many:
        with ui.column().classes('wiz-survey-options gap-0'):
            for option in regular:
                widgets[option] = ui.checkbox(option, value=option in picked,
                                              on_change=lambda e, o=option: tick(o, e.value))
                widgets[option].set_enabled(enabled)
            if question.other_option:
                other_box()
            if none:
                ui.label('or').classes('wiz-survey-or')
                widgets[none] = ui.checkbox(none, value=none in picked, on_change=lambda e: tick(none, e.value))
                widgets[none].set_enabled(enabled)
                ui.label('Ticking this clears the others.').classes('wiz-survey-question__hint wiz-survey-none-hint')
    elif len(regular) <= 4:
        # url-state: exempt — an answer, not a view
        widgets['one'] = ui.toggle(regular, value=picked[0] if picked else None, clearable=True,
                                   on_change=lambda e: pick_one(e.value)) \
            .props('no-caps unelevated').classes('wiz-segmented wiz-survey-pills')
    else:
        widgets['one'] = ui.radio(regular, value=picked[0] if picked else None,
                                  on_change=lambda e: pick_one(e.value)).classes('wiz-survey-options')
    if 'one' in widgets:
        widgets['one']._props['aria-label'] = question.prompt
        widgets['one'].set_enabled(enabled)
    if question.other_option and not many:
        other_box()
    problem = pick_problem(question, state['picked'], state['other']) if state['picked'] else None
    error.set_text(problem or '')
    error.set_visibility(bool(problem))


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
        # On change, not every step: answering mid-drag redraws the slider under the finger.
        picked = e.args if isinstance(e.args, (int, float)) else slider.value
        if picked is not None and picked != state['value']:
            answer(picked)

    def touched(_=None) -> None:
        # A tap on the hidden handle's own value fires no change, so the first touch answers with what the slider shows.
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


async def _note_opened(opened_part: OpenedPart, part_key: str) -> None:
    """Record that a part was opened, for the drop-off card. Never worth an error on the page."""
    try:
        await opened_part(part_key)
    except Exception:
        logger.warning('Could not record an opened survey part', exc_info=True)


def _still_missing(question: SurveyQuestion, value: Any) -> bool:
    if question.kind is SurveyQuestionKind.AGREE_SCALE:
        return len(value or {}) < len(question.options or [])
    value, other = split_other(value)
    return (value.strip() if isinstance(value, str) else value) in (None, '', []) and not other


def _hidden_line(prompt: str, reason: str) -> None:
    """The preview's note for a question this person won't see (or not yet)."""
    with ui.row().classes('wiz-survey-hidden no-wrap items-start w-full'):
        ui.icon('visibility_off').props('aria-hidden=true')
        ui.label(f'Hidden: {prompt} · {reason}').classes('col min-w-0')


def _missing_message(question: SurveyQuestion) -> str:
    if question.kind is SurveyQuestionKind.AGREE_SCALE:
        return 'Answer every statement to send your answers.'
    if question.kind is SurveyQuestionKind.COMMENT:
        return 'Write an answer to send your answers.'
    return 'Pick an answer to send your answers.'


class PartSheet:
    """One part open for answering: its questions, then Back to parts and Done."""

    def __init__(self, form: 'SurveyForm') -> None:
        self.form = form
        self.part: Optional[plan.Part] = None
        self.draft: Dict[int, Any] = {}
        self.unsaved: Dict[str, Dict[int, Any]] = {}
        self.saving = False
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet wiz-survey-sheet') as self.card:
            self.title = dialog_header('Part', self.dialog)
            self.reason = ui.label('').classes('wiz-survey-sheet__reason')
            self.description = ui.label('').classes('wiz-survey-sheet__description')
            self.body = ui.refreshable(self._questions)
            self.body()
            with dialog_actions().classes('justify-end'):
                ui.button('Back to parts', on_click=self.dialog.close).props('outline no-caps')
                self.done_button = ui.button('Done', on_click=self._done).props('unelevated no-caps color=primary')
        self.dialog.on_value_change(self._closed)

    def open(self, part: plan.Part, focus: Optional[int] = None) -> None:
        """Open ``part``; ``focus`` is a question to scroll to (one Send found missing)."""
        self.part = part
        self.draft = dict(self.unsaved.get(part.key) or self.form.view.answers_in(part))
        self.title.set_text(part.title)
        wide = any(q.kind is SurveyQuestionKind.AGREE_SCALE for q in part.questions)
        self.card.classes(add='wiz-survey-sheet--wide' if wide else '', remove='' if wide else 'wiz-survey-sheet--wide')
        shown = self.form.view.shown_parts
        position = shown.index(part) + 1 if part in shown else 0
        where = f' · Part {position} of {len(shown)}' if position else ''
        self.reason.set_text(f'{part.reason}{where}')
        self.description.set_text(part.description)
        self.description.set_visibility(bool(part.description))
        self.done_button.set_visibility(self.form.editable)
        if self.form.editable and self.form.opened_part is not None:
            background_tasks.create(_note_opened(self.form.opened_part, part.key))
        self.body.refresh()
        self.dialog.open()
        if focus is not None:
            ui.run_javascript(f'setTimeout(() => document.getElementById("survey-q-{focus}")'
                              f'?.scrollIntoView({{block: "center"}}), 300)')

    def _answers_for_visibility(self) -> Dict[plan.AnswerKey, Any]:
        merged = dict(self.form.view.answers)
        if self.part is not None:
            for qid in [q.id for q in self.part.questions]:
                merged.pop((qid, self.part.scope), None)
            choices = {q.id for q in self.part.questions
                       if q.kind in (SurveyQuestionKind.CHOICE_ONE, SurveyQuestionKind.CHOICE_MANY)}
            for qid, value in self.draft.items():
                if qid in choices:
                    value, _ = split_other(value)
                if value not in (None, '', []):
                    merged[(qid, self.part.scope)] = value
        return merged

    def _set(self, question: SurveyQuestion, value: Any, quiet: bool = False) -> None:
        self.draft[question.id] = value
        if not quiet:
            self.body.refresh()

    def _questions(self) -> None:
        part = self.part
        if part is None:
            return
        answers = self._answers_for_visibility()
        flagged = {m.question_id: m for m in self.form.flagged if m.part_key == part.key}
        with ui.column().classes('wiz-survey-sheet__questions w-full gap-0'):
            for question in part.questions:
                if not plan.visible(question, part.scope, answers):
                    if self.form.preview:
                        entry = next(iter(answer_entries(question.conditions)), None)
                        _hidden_line(question.prompt, describe(entry, self.form.prompts))
                    continue
                missing = flagged.get(question.id)
                if missing is not None and not _still_missing(question, self.draft.get(question.id)):
                    missing = None
                classes = 'wiz-survey-question w-full gap-2' + (' wiz-survey-question--error' if missing else '')
                with ui.column().classes(classes).props(f'id=survey-q-{question.id}'):
                    with ui.row().classes('items-baseline gap-1'):
                        ui.label(question.prompt).classes('wiz-survey-question__prompt')
                        if question.required:
                            ui.label('Required').classes('wiz-survey-question__required')
                    if missing is not None:
                        with ui.row().classes('wiz-survey-question__error no-wrap items-center gap-1'):
                            ui.icon('error').props('aria-hidden=true')
                            ui.label(_missing_message(question))
                    _question_control(question, self.draft.get(question.id),
                                      partial(self._set, question), self.form.editable)
            for item in self.form.hidden:
                if item.section_id == part.section_id and item.prompt:
                    _hidden_line(item.prompt, item.reason)

    def _payload(self, part: plan.Part) -> Dict[int, Any]:
        answers = self._answers_for_visibility()
        showing = {q.id for q in part.questions if plan.visible(q, part.scope, answers)}
        # A hidden follow-up loses its answer, so nothing hidden is saved.
        return {q.id: (self.draft.get(q.id) if q.id in showing else None) for q in part.questions}

    async def _save(self, part: plan.Part) -> bool:
        payload = self._payload(part)
        if payload == self._payload_saved(part):
            self.unsaved.pop(part.key, None)
            return True
        try:
            self.form.view = await self.form.save_part(part.key, payload)
        except (ValueError, PermissionError) as e:
            self.unsaved[part.key] = dict(self.draft)
            notify_error(e)
            return False
        self.unsaved.pop(part.key, None)
        return True

    def _payload_saved(self, part: plan.Part) -> Dict[int, Any]:
        saved = self.form.view.answers_in(part)
        return {q.id: saved.get(q.id) for q in part.questions}

    async def _done(self) -> None:
        part = self.part
        if part is None:
            return
        self.saving = True
        try:
            if not await self._save(part):
                return
            self.dialog.close()
        finally:
            self.saving = False
        self.form.answers_changed()

    async def _closed(self, event) -> None:
        """Back to parts, the close button, Escape or a tap outside: keep what's there."""
        part = self.part
        if event.value or self.saving or part is None or not self.form.editable:
            return
        await self._save(part)
        self.form.answers_changed()


class SurveyForm:
    """The respondent's survey page body, shared with the staff preview."""

    def __init__(self, view: RespondentView, *, save_part: SavePart, submit: Submit,
                 editable: bool, preview: bool = False, community: str = '',
                 opened_part: Optional[OpenedPart] = None, hidden: Sequence[plan.Hidden] = ()) -> None:
        self.view = view
        self.hidden = list(hidden)
        self.prompts = {q.id: q.prompt for part in view.parts for q in part.questions}
        self.save_part = save_part
        self.opened_part = opened_part
        self.submit = submit
        self.editable = editable
        self.preview = preview
        self.anonymous = False
        self.flagged: List[MissingAnswer] = []
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
                            'Groups under 5 people are hidden. We cannot guarantee anonymity based on '
                            'your answers: what you write may still identify you.',
                        ).classes('wiz-survey-anon__text')
            self.parts_list = ui.refreshable(self._parts)
            self.parts_list()
        self.sheet = PartSheet(self)

    def _status(self) -> None:
        view = self.view
        if self.flagged and not view.submitted:
            self._error_summary()
        if view.submitted:
            how = 'Sent' if view.anonymous is None else ('Sent anonymously' if view.anonymous else 'Sent with your name')
            with banner(f'What happens next: {view.after_send}',
                        title='Your answers are in. Thanks for taking the time.', tone='ok',
                        footnote=f"{how} · You can't change answers now."):
                pass
        elif not self.editable and not self.preview:
            with banner("This survey is closed, so it isn't taking answers any more.", tone='warning'):
                pass

    def _error_summary(self) -> None:
        count = len(self.flagged)
        heading = ('1 answer is needed' if count == 1 else f'{count} answers are needed') + ' before you can send'
        with ui.element('div').classes('wiz-survey-errors w-full') \
                .props('id=survey-errors role=alert tabindex=-1'):
            title = ui.label(heading).classes('wiz-survey-errors__title')
            title.tag = 'h2'
            for missing in self.flagged:
                label = f'{missing.prompt} ({missing.part_title})'
                if missing.every_statement:
                    label = f'{missing.prompt}: every statement ({missing.part_title})'
                ui.button(label, on_click=lambda _, m=missing: self._open_missing(m)) \
                    .props('flat dense no-caps align=left').classes('wiz-survey-errors__link')

    def _open_missing(self, missing: MissingAnswer) -> None:
        part = next((p for p in self.view.shown_parts if p.key == missing.part_key), None)
        if part is not None:
            self.sheet.open(part, focus=missing.question_id)

    def _show_status(self) -> None:
        self.status_slot.clear()
        with self.status_slot:
            self._status()

    def answers_changed(self) -> None:
        """A part saved: drop what's now answered from the Send errors, then redraw."""
        if self.flagged:
            before = {(m.part_key, m.question_id) for m in self.flagged}
            self.flagged = [m for m in self.view.missing() if (m.part_key, m.question_id) in before]
            self._show_status()
        self.parts_list.refresh()

    def _parts(self) -> None:
        view = self.view
        shown = view.shown_parts
        done = sum(1 for p in shown if view.done(p))
        with ui.element('div').classes('wiz-survey-parts w-full'):
            needs = {m.part_key for m in self.flagged}
            for part in shown:
                is_done = view.done(part)
                needed = part.key in needs
                icon_state = ' is-error' if needed else ' is-done' if is_done else ''
                with ui.row().classes('wiz-survey-part no-wrap items-center') as row:
                    with ui.element('span').classes('wiz-survey-part__icon' + icon_state):
                        ui.icon('priority_high' if needed else 'check' if is_done else part.icon) \
                            .props('aria-hidden=true')
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label(part.title).classes('wiz-survey-part__title')
                        count = len(view.showing(part))
                        ui.label(f'{_plural(count, "question")} · {part.reason[0].lower()}{part.reason[1:]}') \
                            .classes('wiz-survey-part__meta')
                    if needed:
                        ui.label('Needs an answer').classes('wiz-chip wiz-chip--cancelled')
                    elif is_done:
                        ui.badge('Done', color='positive')
                    else:
                        ui.icon('chevron_right').classes('text-grey-7').props('aria-hidden=true')
                row.on('click', lambda _, p=part: self.sheet.open(p))
                make_activatable(row, f'Open {part.title}')
            shown_ids = {p.section_id for p in shown}
            for item in self.hidden:
                if item.prompt is None and item.section_id not in shown_ids:
                    with ui.row().classes('wiz-survey-part wiz-survey-part--hidden no-wrap items-center'):
                        with ui.element('span').classes('wiz-survey-part__icon'):
                            ui.icon('visibility_off').props('aria-hidden=true')
                        with ui.column().classes('gap-0 col min-w-0'):
                            ui.label(item.title).classes('wiz-survey-part__title')
                            ui.label(f'Hidden for this person · {item.reason}').classes('wiz-survey-part__meta')
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
        missing = self.view.missing()
        if missing and not self.preview:
            self.flagged = missing
            self._show_status()
            self.parts_list.refresh()
            ui.run_javascript('const box = document.getElementById("survey-errors");'
                              'box?.scrollIntoView({block: "center"}); box?.focus({preventScroll: true});')
            return
        self.flagged = []
        try:
            await self.submit(self.anonymous)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        self.view.submitted = True
        self.view.anonymous = self.anonymous
        self.editable = False
        self.status_slot.clear()
        with self.status_slot:
            self._status()
        self.parts_list.refresh()
        ui.notify('Answers sent. Thank you!', color='positive')
