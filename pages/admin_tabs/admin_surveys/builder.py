"""One survey's sections and questions, with the time box and "Who gets it" beside them.

Editable while the survey is a draft. Once sent it's the same view read-only:
the answers already given belong to these exact questions.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

from nicegui import ui

from application.services import BuilderSummary, SurveyService, SurveySuggestionService
from application.services.survey import conditions, plan
from application.services.survey.suggestions import pending_drafts, provider_label
from models import Survey, SurveyQuestion, SurveyQuestionKind, SurveySection, SurveyStatus, SurveySuggestionBatch, User
from pages.admin_tabs.admin_surveys import dialogs
from pages.admin_tabs.admin_surveys.shared import AUDIENCE_CHIPS, KIND_CHIPS, KIND_ICONS
from theme.busy import busy_click
from theme.dialog._helpers import form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error


@dataclass
class BuilderContext:
    service: SurveyService
    user: User
    survey: Survey
    summary: BuilderSummary
    reload: Callable[[], Awaitable[None]]
    #: The community's AI provider, when it has one; no "Suggest questions" without it.
    ai_label: Optional[str] = None
    #: Section id → its batch of drafts still waiting to be kept.
    suggestions: Dict[int, SurveySuggestionBatch] = field(default_factory=dict)

    @property
    def editable(self) -> bool:
        return self.survey.status is SurveyStatus.DRAFT


def _prompts(survey: Survey) -> Dict[int, str]:
    return {q.id: q.prompt for s in survey.sections for q in s.questions}


async def _run(ctx: BuilderContext, coro) -> None:
    try:
        await coro
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return
    await ctx.reload()


async def _reload(ctx: BuilderContext, _=None) -> None:
    await ctx.reload()


def _condition_chips(entries, prompts: Dict[int, str]) -> None:
    for entry in entries or []:
        with ui.element('span').classes('wiz-chip wiz-chip--pending wiz-survey-q__condition'):
            ui.icon('alt_route', size='14px').props('aria-hidden=true')
            ui.label(conditions.describe(entry, prompts))


def _question_row(ctx: BuilderContext, section: SurveySection, question: SurveyQuestion,
                  prompts: Dict[int, str], first: bool, last: bool) -> None:
    with ui.row().classes('wiz-survey-q no-wrap items-center w-full'):
        ui.icon(KIND_ICONS[question.kind]).classes('wiz-survey-q__icon').props('aria-hidden=true')
        with ui.row().classes('wiz-survey-q__text col min-w-0 items-center gap-2'):
            ui.label(question.prompt).classes('wiz-survey-q__prompt')
            if question.required:
                ui.label('Required').classes('wiz-chip wiz-chip--neutral')
            _condition_chips(question.conditions, prompts)
        ui.label(KIND_CHIPS[question.kind]).classes('wiz-chip wiz-chip--neutral wiz-survey-q__kind')
        if ctx.editable:
            with more_menu(question.prompt):
                menu_action('edit', 'Edit…', lambda: dialogs.question_dialog(
                    ctx.service, ctx.user, ctx.survey, section, question, lambda _: _reload(ctx)))
                if not first:
                    menu_action('arrow_upward', 'Move up',
                                lambda: _run(ctx, ctx.service.move_question(ctx.user, question.id, -1)))
                if not last:
                    menu_action('arrow_downward', 'Move down',
                                lambda: _run(ctx, ctx.service.move_question(ctx.user, question.id, 1)))
                ui.separator()
                menu_action('delete', 'Delete question…', lambda: ConfirmationDialog(
                    f'Delete “{question.prompt}”?', title='Delete question', confirm_text='Delete',
                    on_confirm=lambda: _run(ctx, ctx.service.delete_question(ctx.user, question.id)),
                ).open(), negative=True)


def _section_card(ctx: BuilderContext, section: SurveySection, prompts: Dict[int, str],
                  first: bool, last: bool) -> None:
    with ui.element('section').classes('wiz-survey-section w-full'):
        with ui.row().classes('wiz-survey-section__head no-wrap items-center w-full'):
            with ui.row().classes('col min-w-0 items-center gap-2'):
                heading = ui.label(section.title).classes('wiz-survey-section__title')
                heading.tag = 'h2'
                with ui.element('span').classes('wiz-chip wiz-chip--live'):
                    ui.icon('groups', size='14px').props('aria-hidden=true')
                    ui.label(AUDIENCE_CHIPS[section.audience])
                _condition_chips(section.conditions, prompts)
            if ctx.editable:
                with more_menu(section.title):
                    menu_action('edit', 'Edit section…', lambda: dialogs.section_dialog(
                        ctx.service, ctx.user, ctx.survey, section, lambda _: _reload(ctx)))
                    if not first:
                        menu_action('arrow_upward', 'Move up',
                                    lambda: _run(ctx, ctx.service.move_section(ctx.user, section.id, -1)))
                    if not last:
                        menu_action('arrow_downward', 'Move down',
                                    lambda: _run(ctx, ctx.service.move_section(ctx.user, section.id, 1)))
                    ui.separator()
                    menu_action('delete', 'Delete section…', lambda: ConfirmationDialog(
                        f'Delete “{section.title}” and its {len(section.questions)} questions?',
                        title='Delete section', confirm_text='Delete',
                        on_confirm=lambda: _run(ctx, ctx.service.delete_section(ctx.user, section.id)),
                    ).open(), negative=True)
        questions = list(section.questions)
        if not questions:
            ui.label('No questions yet.').classes('wiz-survey-section__empty')
        for i, question in enumerate(questions):
            _question_row(ctx, section, question, prompts, i == 0, i == len(questions) - 1)
        batch = ctx.suggestions.get(section.id) if ctx.editable else None
        if batch is not None:
            _suggested_rows(ctx, section, batch)
        if ctx.editable:
            with ui.row().classes('wiz-survey-section__foot w-full items-center gap-1'):
                ui.button('Add question', icon='add', on_click=lambda: dialogs.question_dialog(
                    ctx.service, ctx.user, ctx.survey, section, None, lambda _: _reload(ctx),
                )).props('flat no-caps color=primary')
                if ctx.ai_label:
                    ui.button('Suggest questions', icon='auto_awesome',
                              on_click=busy_click('Asking…', lambda: _suggest(ctx, section))) \
                        .props('flat no-caps').classes('wiz-survey-suggest-btn').style('color: var(--status-suggested)')


async def _suggest(ctx: BuilderContext, section: SurveySection) -> None:
    await _run(ctx, SurveySuggestionService().suggest(ctx.user, section.id))


def _sent_dialog(ctx: BuilderContext, batch: SurveySuggestionBatch) -> Callable[[], Awaitable[None]]:
    async def load() -> None:
        try:
            text = await SurveySuggestionService().sent_context(ctx.user, batch.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        with form_dialog('What was sent', card_classes='dialog-card dialog-card--lg') as dialog:
            with ui.column().classes('w-full gap-2'):
                ui.label(f'Everything {provider_label(batch)} ({batch.model}) was given for these suggestions. '
                         'No names, handles or anything people wrote.').classes('text-caption text-grey-7')
                ui.label(text).classes('wiz-survey-sent')
        dialog.open()

    return load


def _suggested_rows(ctx: BuilderContext, section: SurveySection, batch: SurveySuggestionBatch) -> None:
    service = SurveySuggestionService()
    for index, draft in pending_drafts(batch):
        kind = SurveyQuestionKind(draft['kind'])
        with ui.row().classes('wiz-survey-q wiz-survey-q--suggested no-wrap items-center w-full'):
            ui.icon(KIND_ICONS[kind]).classes('wiz-survey-q__icon').props('aria-hidden=true')
            with ui.row().classes('wiz-survey-q__text col min-w-0 items-center gap-2'):
                ui.label(draft['prompt']).classes('wiz-survey-q__prompt')
                ui.label('Suggested').classes('wiz-chip wiz-chip--suggested')
                if draft.get('options'):
                    ui.label(' · '.join(draft['options'])).classes('wiz-survey-q__options')
            ui.label(KIND_CHIPS[kind]).classes('wiz-chip wiz-chip--neutral wiz-survey-q__kind')
            keep = ui.button(icon='add_circle_outline', on_click=lambda i=index: _run(
                ctx, service.keep(ctx.user, batch.id, i))).props('flat round dense').classes('wiz-survey-q__keep')
            keep._props['aria-label'] = f'Keep “{draft["prompt"]}”'
            keep.tooltip('Keep this question')
    with ui.row().classes('wiz-survey-suggested-note w-full items-center gap-2'):
        ui.label(f'Suggested by {provider_label(batch)} from “{plan.AUDIENCE_LABELS[section.audience]}”. '
                 'Tap + to keep, or').classes('col-auto')
        ui.button('dismiss all', on_click=lambda: _run(ctx, service.dismiss(ctx.user, batch.id))) \
            .props('flat dense no-caps').classes('wiz-survey-suggested-note__link') \
            .style('color: var(--status-suggested)')
        ui.button('What was sent', icon='visibility', on_click=_sent_dialog(ctx, batch)) \
            .props('flat dense no-caps').classes('wiz-survey-suggested-note__link') \
            .style('color: var(--status-suggested)')


def _budget_card(ctx: BuilderContext) -> None:
    summary = ctx.summary
    with ui.element('section').classes('wiz-survey-side'):
        ui.label('Time to answer').classes('wiz-survey-side__title')
        if not summary.longest_seconds:
            ui.label('Nobody has taken part in this event yet, so there is no one to time it for.') \
                .classes('wiz-survey-side__text')
            return
        share = min(1.0, summary.longest_seconds / plan.BUDGET_SECONDS)
        ui.label(f'About {plan.format_duration(summary.longest_seconds)} at most') \
            .classes('wiz-survey-side__figure')
        ui.linear_progress(value=share, show_value=False, size='8px',
                           color='negative' if summary.over_budget else 'primary') \
            .props('rounded aria-label="Share of the 5 minute budget"')
        ui.label(f'{summary.longest_who}: {summary.longest_questions} questions.') \
            .classes('wiz-survey-side__text')
        if summary.over_budget:
            ui.label('That is over 5 minutes. Trim a repeating section first; it counts once per tournament.') \
                .classes('wiz-survey-side__warn')


def _who_card(ctx: BuilderContext) -> None:
    counts = ctx.summary.counts
    rows = [('Players', counts.get('players', 0)), ('Volunteers', counts.get('volunteers', 0)),
            ('Crew', counts.get('crew', 0)), ('Checked in only', counts.get('checked_in_only', 0))]
    top = max([n for _, n in rows] + [1])
    with ui.element('section').classes('wiz-survey-side'):
        ui.label('Who gets it').classes('wiz-survey-side__title')
        for label, n in rows:
            with ui.row().classes('wiz-survey-who no-wrap items-center w-full'):
                ui.label(label).classes('wiz-survey-who__label')
                ui.linear_progress(value=n / top, show_value=False, size='6px').classes('col') \
                    .props(f'rounded aria-label="{label}: {n}"')
                ui.label(str(n)).classes('wiz-survey-who__n')
        ui.label(f"{counts.get('everyone', 0)} people (some are in several groups)").classes('wiz-survey-side__text')
        anonymous = ui.switch('Let people answer anonymously', value=ctx.survey.allow_anonymous)
        anonymous.set_enabled(ctx.editable)

        async def toggle(value: bool) -> None:
            await _run(ctx, ctx.service.update_survey(ctx.user, ctx.survey.id, allow_anonymous=value))

        anonymous.on_value_change(lambda e: toggle(bool(e.value)))


def render_builder(ctx: BuilderContext) -> None:
    survey = ctx.survey
    prompts = _prompts(survey)
    sections = list(survey.sections)
    with ui.element('div').classes('wiz-survey-builder w-full'):
        with ui.column().classes('wiz-survey-builder__main gap-3'):
            if survey.intro:
                ui.label(survey.intro).classes('wiz-survey-builder__intro')
            for i, section in enumerate(sections):
                _section_card(ctx, section, prompts, i == 0, i == len(sections) - 1)
            if ctx.editable:
                ui.button('Add section', icon='add', on_click=lambda: dialogs.section_dialog(
                    ctx.service, ctx.user, survey, None, lambda _: _reload(ctx),
                )).props('outline no-caps color=primary')
        with ui.column().classes('wiz-survey-builder__side gap-3 items-stretch'):
            _budget_card(ctx)
            _who_card(ctx)
