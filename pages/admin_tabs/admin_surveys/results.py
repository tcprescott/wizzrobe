"""A sent survey's results: headline numbers, then every question's answers.

A repeating section's question shows its answers per tournament or position;
a group under five answers shows only its count, so nobody in it can be picked
out. Comments are what respondents typed, drawn as plain text.
"""

from typing import List, Optional

from nicegui import ui

from application.services import SurveyResults
from application.services.survey import AGREE_LEVELS
from application.services.survey.results import Breakdown, CommentRow, QuestionResult
from models import SurveyQuestionKind
from theme.empty_state import empty_panel


def _tile(label: str, value: str, caption: str = '') -> None:
    with ui.column().classes('wiz-survey-stat gap-0'):
        ui.label(label).classes('wiz-survey-stat__label')
        ui.label(value).classes('wiz-survey-stat__value')
        if caption:
            ui.label(caption).classes('wiz-survey-stat__caption')


def _bars(result: Breakdown, *, per_person: bool = False, set_apart: Optional[str] = None) -> None:
    """One bar per answer. A pick-many counts people (``per_person``), so its bars can total over 100%."""
    total = (result.count if per_person else sum(result.counts.values())) or 1
    with ui.column().classes('wiz-survey-bars w-full gap-1'):
        if per_person:
            ui.label('% of people who answered, so the bars can add up to more than 100%.') \
                .classes('wiz-survey-result__meta')
        for option, n in result.counts.items():
            share = n / total
            apart = ' wiz-survey-bar--apart' if option == set_apart else ''
            with ui.row().classes(f'wiz-survey-bar no-wrap items-center w-full{apart}'):
                ui.label(option).classes('wiz-survey-bar__label')
                ui.linear_progress(value=share, show_value=False, size='8px').classes('col') \
                    .props(f'rounded aria-label="{option}: {n}"')
                ui.label(f'{round(share * 100)}%').classes('wiz-survey-bar__pct')


def _agree(result: Breakdown) -> None:
    with ui.row().classes('wiz-agree-key items-center').props('aria-hidden=true'):
        for level, label in enumerate(AGREE_LEVELS, 1):
            with ui.element('span'):
                ui.element('span').classes(f'wiz-agree-key__swatch wiz-agree-l{level}')
                ui.label(label).style('display:inline')
    for statement, levels in result.statements.items():
        total = sum(levels)
        share = result.agree_share(statement)
        with ui.column().classes('wiz-agree-result w-full'):
            ui.label(statement).classes('wiz-agree-result__statement')
            spoken = ', '.join(f'{label} {n}' for label, n in zip(AGREE_LEVELS, levels, strict=True))
            with ui.element('div').classes('wiz-agree-bar w-full').props(f'role=img aria-label="{spoken}"'):
                for level, n in enumerate(levels, 1):
                    if n:
                        ui.element('span').classes(f'wiz-agree-l{level}').style(f'width:{n * 100 / total}%')
            ui.label(f'{share}% agree · {total} answers' if share is not None else 'No answers yet') \
                .classes('wiz-survey-result__meta')


SHOWN_COMMENTS = 50


def _comments(comments: List[CommentRow], prompt: str) -> None:
    """The answers people typed, newest first, as plain text, with a search over them."""
    state = {'q': ''}

    @ui.refreshable
    def listing() -> None:
        needle = state['q'].strip().lower()
        found = [c for c in comments if needle in c.text.lower()] if needle else comments
        if needle and not found:
            ui.label(f'Nothing matches “{state["q"].strip()}”.').classes('wiz-survey-result__meta')
        with ui.column().classes('wiz-survey-comments w-full gap-0'):
            for comment in found[:SHOWN_COMMENTS]:
                with ui.column().classes('wiz-survey-comment gap-0 w-full'):
                    who = comment.who + (f' · {comment.group}' if comment.group and not
                                         comment.who.startswith('Anonymous ·') else '')
                    ui.label(who).classes('wiz-survey-comment__who')
                    ui.label(comment.text).classes('wiz-survey-comment__text')
            if len(found) > SHOWN_COMMENTS:
                ui.label(f'and {len(found) - SHOWN_COMMENTS} more. Search to narrow them, '
                         'or read them all over REST or MCP.').classes('wiz-survey-result__meta')

    if len(comments) > 3:
        # url-state: exempt — a filter inside one card, not a view
        search = ui.input(placeholder='Search answers').props('dense outlined clearable') \
            .classes('wiz-survey-comments__search w-full')
        search._props['aria-label'] = f'Search answers to {prompt}'
        search.on_value_change(lambda e: (state.update(q=e.value or ''), listing.refresh()))
    listing()


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
            else:
                _comments(result.comments, result.prompt)
            return
        if result.kind is SurveyQuestionKind.AGREE_SCALE:
            _agree(result.overall)
        elif result.overall.count:
            _bars(result.overall, per_person=result.kind is SurveyQuestionKind.CHOICE_MANY,
                  set_apart=result.none_option)
        if result.comments:
            n = len(result.comments)
            with ui.expansion(f'{n} “Other” write-in' + ('' if n == 1 else 's')).props('dense') \
                    .classes('wiz-survey-writeins w-full'):
                _comments(result.comments, result.prompt)
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
