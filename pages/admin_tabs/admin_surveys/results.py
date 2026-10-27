"""A sent survey's results: headline numbers, then every question's answers.

A repeating section's question shows its answers per tournament or position;
a group under five answers shows only its count, so nobody in it can be picked
out. Comments are what respondents typed, drawn as plain text.
"""

from typing import Optional

from nicegui import ui

from application.services import SurveyResults
from application.services.survey.results import Breakdown, QuestionResult
from models import SurveyQuestionKind
from theme.empty_state import empty_panel


def _tile(label: str, value: str, caption: str = '') -> None:
    with ui.column().classes('wiz-survey-stat gap-0'):
        ui.label(label).classes('wiz-survey-stat__label')
        ui.label(value).classes('wiz-survey-stat__value')
        if caption:
            ui.label(caption).classes('wiz-survey-stat__caption')


def _bars(result: Breakdown) -> None:
    total = sum(result.counts.values()) or 1
    with ui.column().classes('wiz-survey-bars w-full gap-1'):
        for option, n in result.counts.items():
            share = n / total
            with ui.row().classes('wiz-survey-bar no-wrap items-center w-full'):
                ui.label(option).classes('wiz-survey-bar__label')
                ui.linear_progress(value=share, show_value=False, size='8px').classes('col') \
                    .props(f'rounded aria-label="{option}: {n}"')
                ui.label(f'{round(share * 100)}%').classes('wiz-survey-bar__pct')


def _summary_line(result: Breakdown) -> str:
    bits = [f'{result.count} answer' + ('' if result.count == 1 else 's')]
    if result.average is not None:
        bits.append(f'avg {result.average}')
    if result.score is not None:
        bits.append(f'recommend score {result.score:+d}')
    return ' · '.join(bits)


def _question_card(result: QuestionResult) -> None:
    with ui.element('section').classes('wiz-survey-result'):
        heading = ui.label(result.prompt).classes('wiz-survey-result__title')
        heading.tag = 'h3'
        ui.label(f'{result.section_title} · {_summary_line(result.overall)}').classes('wiz-survey-result__meta')
        if result.kind is SurveyQuestionKind.COMMENT:
            if not result.comments:
                ui.label('No comments yet.').classes('wiz-survey-result__meta')
            with ui.column().classes('wiz-survey-comments w-full gap-0'):
                for comment in result.comments[:50]:
                    with ui.column().classes('wiz-survey-comment gap-0 w-full'):
                        who = comment.who + (f' · {comment.group}' if comment.group and not
                                             comment.who.startswith('Anonymous ·') else '')
                        ui.label(who).classes('wiz-survey-comment__who')
                        ui.label(comment.text).classes('wiz-survey-comment__text')
                if len(result.comments) > 50:
                    ui.label(f'and {len(result.comments) - 50} more. Read them all over REST or MCP.') \
                        .classes('wiz-survey-result__meta')
            return
        if result.overall.count:
            _bars(result.overall)
        if result.groups:
            with ui.column().classes('wiz-survey-groups w-full gap-1'):
                for label, group in result.groups.items():
                    with ui.row().classes('wiz-survey-group no-wrap items-center w-full'):
                        ui.label(label).classes('wiz-survey-group__label col min-w-0')
                        text = (f'{group.count} answers, too few to show' if group.hidden
                                else _summary_line(group))
                        ui.label(text).classes('wiz-survey-group__value')


def render_results(results: SurveyResults) -> None:
    sent = results.recipients.get('sent', 0)
    nps = results.headline(SurveyQuestionKind.NPS)
    overall = results.headline(SurveyQuestionKind.RATING)
    with ui.element('div').classes('wiz-survey-stats w-full'):
        rate: Optional[int] = results.response_rate
        _tile('Responses', str(results.responses), f'of {sent}' + (f' · {rate}%' if rate is not None else ''))
        _tile('Recommend score', f'{nps.overall.score:+d}' if nps and nps.overall.score is not None else '–',
              '0–10 question')
        _tile('Overall', f'{overall.overall.average} / 5' if overall and overall.overall.average else '–',
              overall.prompt if overall else '')
        _tile('Anonymous', str(results.anonymous), 'of the responses')
    if not results.responses:
        empty_panel('No answers yet. They show here as people send them.', icon='poll')
        return
    with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md w-full'):
        for result in results.questions:
            _question_card(result)
