"""One survey's sections and questions, with the time box and "Who gets it" beside them.

Editable while the survey is a draft. Once sent only the wording can change
("Fix wording…" in each ⋮): the answers already given belong to these exact
questions, choices and conditions.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

from nicegui import ui

from application.services import BuilderSummary, SurveyService, SurveySuggestionService
from application.services.survey import conditions, plan
from application.services.survey.suggestions import pending_drafts, provider_label
from application.utils.timezone import format_local_date
from models import Survey, SurveyQuestion, SurveyQuestionKind, SurveySection, SurveyStatus, SurveySuggestionBatch, User
from pages.admin_tabs.admin_surveys import dialogs
from pages.admin_tabs.admin_surveys.shared import AUDIENCE_CHIPS, KIND_CHIPS, KIND_ICONS
from theme.banner import banner
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


async def _dropped(ctx: BuilderContext, drop: dict) -> None:
    try:
        question, section, index = int(drop['question']), int(drop['section']), int(drop['index'])
    except (KeyError, TypeError, ValueError):
        return
    await _run(ctx, ctx.service.place_question(ctx.user, question, section, index))


async def _reload(ctx: BuilderContext, _=None) -> None:
    await ctx.reload()


def _condition_chips(entries, prompts: Dict[int, str]) -> None:
    for entry in entries or []:
        with ui.element('span').classes('wiz-chip wiz-chip--pending wiz-survey-q__condition'):
            ui.icon('alt_route', size='14px').props('aria-hidden=true')
            ui.label(conditions.describe(entry, prompts))


def _question_row(ctx: BuilderContext, section: SurveySection, question: SurveyQuestion,
                  prompts: Dict[int, str], first: bool, last: bool) -> None:
    with ui.row().classes('wiz-survey-q no-wrap items-center w-full').props(f'data-qid={question.id}'):
        if ctx.editable:
            # static/js/survey-drag.js; the ⋮ menu's Move up/down is the keyboard path.
            ui.icon('drag_indicator').classes('wiz-survey-drag').props('aria-hidden=true') \
                .tooltip('Drag to move')
        ui.icon(KIND_ICONS[question.kind]).classes('wiz-survey-q__icon').props('aria-hidden=true')
        with ui.row().classes('wiz-survey-q__text col min-w-0 items-center gap-2'):
            ui.label(question.prompt).classes('wiz-survey-q__prompt')
            if question.reworded_at:
                ui.label('Edited').classes('wiz-chip wiz-chip--info') \
                    .tooltip(f'Was “{question.original_prompt}” for the first {question.reworded_after} answers')
            if question.required:
                ui.label('Required').classes('wiz-chip wiz-chip--neutral')
            _condition_chips(question.conditions, prompts)
        ui.label(KIND_CHIPS[question.kind]).classes('wiz-chip wiz-chip--neutral wiz-survey-q__kind')
        if ctx.editable:
            with more_menu(question.prompt):
                menu_action('edit', 'Edit…', lambda: dialogs.question_dialog(
                    ctx.service, ctx.user, ctx.survey, section, question, lambda _: _reload(ctx),
                    positions=ctx.summary.positions))
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
        else:
            with more_menu(question.prompt):
                menu_action('edit', 'Fix wording…', lambda: dialogs.question_wording_dialog(
                    ctx.service, ctx.user, question, ctx.summary.answered, lambda _: _reload(ctx)))


def _section_card(ctx: BuilderContext, section: SurveySection, prompts: Dict[int, str],
                  first: bool, last: bool) -> None:
    with ui.element('section').classes('wiz-survey-section w-full').props(f'data-sid={section.id}'):
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
                        ctx.service, ctx.user, ctx.survey, section, lambda _: _reload(ctx),
                        positions=ctx.summary.positions))
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
            else:
                with more_menu(section.title):
                    menu_action('edit', 'Fix wording…', lambda: dialogs.section_wording_dialog(
                        ctx.service, ctx.user, section, ctx.summary.answered, lambda _: _reload(ctx)))
        if section.description:
            ui.label(section.description).classes('wiz-survey-section__description')
        questions = list(section.questions)
        with ui.column().classes('wiz-survey-qlist w-full gap-0').props(f'data-section={section.id}'):
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
                    positions=ctx.summary.positions,
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
        typical, longest = summary.typical_seconds, summary.longest_seconds
        ui.label(f'Most people: about {plan.format_duration(typical)}').classes('wiz-survey-side__figure')
        ui.label(f'Longest: about {plan.format_duration(longest)} · {summary.longest_who}, '
                 f'{summary.longest_questions} questions').classes('wiz-survey-side__text')
        budget = plan.BUDGET_SECONDS

        def pct(seconds: int) -> float:
            return round(min(1.0, seconds / budget) * 100, 1)

        bar = ui.element('div').classes('wiz-survey-time' + (' is-over' if summary.over_budget else '')) \
            .props('role=img')
        bar._props['aria-label'] = (f'Most people about {plan.format_duration(typical)}, longest about '
                                    f'{plan.format_duration(longest)}, on a {budget // 60} minute scale')
        with bar:
            ui.element('span').classes('wiz-survey-time__longest').style(f'width: {pct(longest)}%')
            ui.element('span').classes('wiz-survey-time__typical').style(f'width: {pct(typical)}%')
            ui.element('span').classes('wiz-survey-time__tick').style(f'left: {pct(plan.TARGET_SECONDS)}%')
        with ui.row().classes('wiz-survey-time__scale w-full no-wrap').props('aria-hidden=true'):
            ui.label('0')
            ui.label(f'{plan.TARGET_SECONDS // 60} min')
            ui.label(str(budget // 60))
        if summary.over_budget:
            ui.label(f'The longest path is over {budget // 60} minutes. Trim a repeating section first; '
                     'it counts once per tournament.').classes('wiz-survey-side__warn')
        elif typical > plan.TARGET_SECONDS:
            ui.label(f'Most people take over {plan.TARGET_SECONDS // 60} minutes. Aim for under that.') \
                .classes('wiz-survey-side__warn')
        else:
            ui.label(f'Aim for most people under {plan.TARGET_SECONDS // 60}. '
                     f'Over {budget // 60} is too long for anyone.').classes('wiz-survey-side__text')


def _scroll_to(finding) -> None:
    selector = (f'[data-qid="{finding.question_id}"]' if finding.question_id is not None
                else f'[data-sid="{finding.section_id}"]')
    ui.run_javascript(f"document.querySelector('{selector}')?.scrollIntoView({{block: 'center', behavior: 'smooth'}})")


def _checks_card(ctx: BuilderContext) -> None:
    summary = ctx.summary
    findings = summary.checks
    with ui.element('section').classes('wiz-survey-side'):
        with ui.row().classes('items-center no-wrap w-full'):
            ui.label('Checks').classes('wiz-survey-side__title col')
            if findings:
                ui.label(f'{len(findings)} to look at').classes('wiz-chip wiz-chip--pending')
        for finding in findings:
            with ui.row().classes('wiz-survey-check no-wrap items-start w-full'):
                ui.icon('warning').classes('wiz-survey-check__icon is-warn').props('aria-hidden=true')
                with ui.element('div').classes('col min-w-0'):
                    if finding.link:
                        link = ui.link(finding.link, '#').classes('wiz-survey-check__link')
                        link.on('click.prevent', lambda _, f=finding: _scroll_to(f))
                    text = ui.label(finding.text).classes('wiz-survey-check__text')
                    text.tag = 'span'
        if summary.checks_passed:
            with ui.row().classes('wiz-survey-check no-wrap items-start w-full'):
                ui.icon('check_circle').classes('wiz-survey-check__icon is-ok').props('aria-hidden=true')
                ui.label(' · '.join(summary.checks_passed)).classes('wiz-survey-check__passed col min-w-0')


def _who_card(ctx: BuilderContext) -> None:
    counts = ctx.summary.counts
    rows = [('Players', counts.get('players', 0)), ('Tournament organizers', counts.get('organizers', 0)),
            ('Volunteers', counts.get('volunteers', 0)),
            ('Commentators', counts.get('crew_commentary', 0)), ('Trackers', counts.get('crew_tracking', 0)),
            ('Broadcast crew', counts.get('crew_broadcast', 0)),
            ('Checked in only', counts.get('checked_in_only', 0))]
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
    builder = ui.element('div').classes('wiz-survey-builder w-full')
    if ctx.editable:
        builder.on('survey-drop', lambda e: _dropped(ctx, (e.args or {}).get('detail') or {}), args=['detail'])
    with builder:
        with ui.column().classes('wiz-survey-builder__main gap-3'):
            if not ctx.editable:
                sent = f'Sent on {format_local_date(survey.sent_at)}.' if survey.sent_at else 'Sent.'
                with banner('You can fix wording; questions, choices, order and conditions are locked '
                            'so answers stay comparable.', title=sent, icon='lock'):
                    pass
            if survey.intro:
                ui.label(survey.intro).classes('wiz-survey-builder__intro')
            for i, section in enumerate(sections):
                _section_card(ctx, section, prompts, i == 0, i == len(sections) - 1)
            if ctx.editable:
                ui.button('Add section', icon='add', on_click=lambda: dialogs.section_dialog(
                    ctx.service, ctx.user, survey, None, lambda _: _reload(ctx), positions=ctx.summary.positions,
                )).props('outline no-caps color=primary')
        with ui.column().classes('wiz-survey-builder__side gap-3 items-stretch'):
            _budget_card(ctx)
            if ctx.editable:
                _checks_card(ctx)
            _who_card(ctx)
