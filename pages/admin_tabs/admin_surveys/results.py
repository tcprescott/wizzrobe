"""A sent survey's results: headline numbers, the group filter, drop-off, then the report.

The report itself (one line per question, comparison tables for repeats) is
:mod:`.report`; a group under five answers never shows on its own, and the
filter offers only groups with five or more.
"""

from typing import Awaitable, Callable, Optional

from nicegui import ui

from application.services import SurveyResults
from application.services.survey import plan
from application.services.survey.dropoff import Dropoff
from models import SurveyQuestionKind
from pages.admin_tabs.admin_surveys.comment_sheet import CommentSheet
from pages.admin_tabs.admin_surveys.report import render_sections
from theme.empty_state import empty_panel


def _tile(label: str, value: str, caption: str = '') -> None:
    with ui.column().classes('wiz-survey-stat gap-0'):
        ui.label(label).classes('wiz-survey-stat__label')
        ui.label(value).classes('wiz-survey-stat__value')
        if caption:
            ui.label(caption).classes('wiz-survey-stat__caption')


def _pct(part: int, whole: int) -> str:
    return f'{round(part * 100 / whole)}% of ' if whole else ''


def _dropoff(d: Dropoff) -> None:
    """Sent, opened, started, submitted; then each part in survey order, opened against answered."""
    with ui.element('section').classes('wiz-survey-dropoff w-full'):
        with ui.row().classes('wiz-survey-dropoff__head items-center no-wrap'):
            title = ui.label('Drop-off').classes('wiz-survey-result__title')
            title.tag = 'h3'
            ui.badge('People, not answers', color='grey')
        with ui.element('div').classes('wiz-survey-stats wiz-survey-stats--flat w-full'):
            _tile('Sent', str(d.sent), f'{d.declined} said not interested' if d.declined else '')
            _tile('Opened', str(d.opened), _pct(d.opened, d.sent) + 'sent' if d.sent else '')
            _tile('Started', str(d.started), _pct(d.started, d.opened) + 'opened' if d.opened else '')
            _tile('Submitted', str(d.submitted), _pct(d.submitted, d.started) + 'started' if d.started else '')
        if d.median_seconds is not None:
            estimate = f' (estimate was {plan.format_duration(d.estimate_seconds)})' if d.estimate_seconds else ''
            ui.label(f'Median time to finish: {plan.format_duration(d.median_seconds)}{estimate}.') \
                .classes('wiz-survey-dropoff__median')
        if not d.parts:
            ui.label('Nobody has opened a part yet.').classes('wiz-survey-result__meta wiz-survey-dropoff__note')
            return
        stopped = sum(p.stopped for p in d.parts)
        caption = f'Each part, in survey order ({len(d.parts)})' + (f' · {stopped} stopped partway' if stopped else '')
        with ui.expansion(caption).props('dense').classes('wiz-survey-dropoff__parts w-full'):
            scale = max(d.opened, max(p.opened for p in d.parts)) or 1
            with ui.column().classes('w-full gap-0'):
                for part in d.parts:
                    with ui.row().classes('wiz-survey-dropoff__part no-wrap items-center w-full'):
                        ui.icon(part.icon).classes('wiz-survey-part__icon')
                        with ui.column().classes('col min-w-0 gap-1'):
                            with ui.row().classes('items-center no-wrap w-full gap-2'):
                                ui.label(part.title).classes('wiz-survey-dropoff__title col min-w-0')
                                if part.stopped:
                                    ui.badge(f'{part.stopped} stopped here', color='warning')
                            spoken = f'{part.title}: {part.opened} opened, {part.answered} answered'
                            with ui.element('div').classes('wiz-survey-dropoff__bar w-full') \
                                    .props(f'role=img aria-label="{spoken}"'):
                                ui.element('span').classes('wiz-survey-dropoff__opened') \
                                    .style(f'width:{part.opened * 100 / scale}%')
                                ui.element('span').classes('wiz-survey-dropoff__answered') \
                                    .style(f'width:{part.answered * 100 / scale}%')
                            ui.label(f'{part.opened} opened it · {part.answered} answered and moved on') \
                                .classes('wiz-survey-result__meta')
            ui.label('"Stopped here" counts people who opened this part last and haven\'t sent the survey. '
                     'Lighter bar: opened. Darker: answered.').classes('wiz-survey-result__meta wiz-survey-dropoff__note')


def _audiences(results: SurveyResults) -> None:
    if len(results.audiences) < 2:
        return
    with ui.row().classes('wiz-report__audiences items-center gap-2'):
        ui.label('Response rate').classes('wiz-survey-result__meta')
        for a in results.audiences:
            ui.badge(f'{a.label} {a.rate}% · {a.responded} of {a.sent}', color='grey-3') \
                .classes('wiz-report__audience')


def _filter(results: SurveyResults, on_group: Callable[[Optional[str]], Awaitable[None]]) -> None:
    if not results.groups:
        return
    options = {'': 'Everyone'} | {g.key: f'{g.label} · {g.responded}' for g in results.groups}
    with ui.row().classes('wiz-report__filter items-center no-wrap gap-2 w-full'):
        # url-state: exempt — page.py keeps the group in the address
        picker = ui.select(options, value=results.group.key if results.group else '', with_input=True) \
            .props('dense outlined options-dense').classes('wiz-report__picker')
        with picker.add_slot('prepend'):
            ui.icon('filter_list').props('aria-hidden=true')
        picker._props['aria-label'] = 'Show results for'
        picker.on_value_change(lambda e: on_group(e.value or None))
        ui.label('Only groups with 5 or more answers are listed.').classes('wiz-survey-result__meta gt-xs')


def _yes_tile(results: SurveyResults) -> None:
    question = results.headline_yes()
    if question is None or question.score is None:
        _tile('Anonymous', str(results.anonymous), 'of the responses')
        return
    counts = question.overall.counts
    rest = ' · '.join(f'{round(n * 100 / question.overall.count)}% {option.lower()}'
                      for option, n in list(counts.items())[1:] if n and question.overall.count)
    _tile(question.prompt, f'{question.score}%', 'yes' + (f' · {rest}' if rest else ''))


def render_results(results: SurveyResults, on_group: Callable[[Optional[str]], Awaitable[None]]) -> None:
    sent = results.recipients.get('sent', 0)
    nps = results.headline(SurveyQuestionKind.NPS)
    overall = results.headline(SurveyQuestionKind.RATING)
    with ui.element('div').classes('wiz-survey-stats w-full'):
        rate: Optional[int] = results.response_rate
        anon = f' · {results.anonymous} anonymous' if results.anonymous else ''
        _tile('Responses', str(results.responses),
              f'of {sent}' + (f' · {rate}%' if rate is not None else '') + anon)
        nps_value = '–'
        if nps and not nps.overall.hidden and nps.overall.score is not None:
            nps_value = f'{nps.overall.score:+d}'
        _tile('Recommend score', nps_value, _answers(nps.overall.count) if nps else '0–10 question')
        overall_value = '–'
        if overall and not overall.overall.hidden and overall.overall.average:
            overall_value = f'{overall.overall.average} / 5'
        _tile('Overall', overall_value, _answers(overall.overall.count) if overall else '')
        _yes_tile(results)
    if results.group is None:
        _audiences(results)
    _filter(results, on_group)
    if results.dropoff is not None and results.dropoff.sent:
        _dropoff(results.dropoff)
    if not results.responses:
        empty_panel('No answers yet. They show here as people send them.', icon='poll')
        return
    render_sections(results.questions, CommentSheet())


def _answers(n: int) -> str:
    return f'{n} answer' + ('' if n == 1 else 's')
