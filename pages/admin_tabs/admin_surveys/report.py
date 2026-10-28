"""The results report, one section at a time (Thomas picked A, 2026-10-28).

Each question is one line: its prompt and count, a stacked bar, and its
headline number. A section that repeats per tournament or position is a
comparison table instead, one row per group, and a row 20 points under the
others is flagged. Comments collapse to a count that opens the comment sheet.
"""

from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from nicegui import ui

from application.services.survey.results import OTHER, Breakdown, QuestionResult, score_of
from application.utils.timezone import format_local_date
from models import SurveyAudience, SurveyQuestionKind
from pages.admin_tabs.admin_surveys.comment_sheet import CommentSheet

#: How far under the other groups' average a group is before it's flagged.
OUTLIER_POINTS = 20

_SECTION_WHO = {
    SurveyAudience.EVERYONE: 'everyone',
    SurveyAudience.EACH_TOURNAMENT: 'once per tournament',
    SurveyAudience.EACH_POSITION: 'once per position',
    SurveyAudience.CREW: 'match crew',
    SurveyAudience.COMMENTATORS: 'commentators',
    SurveyAudience.TRACKERS: 'trackers',
    SurveyAudience.BROADCASTERS: 'broadcast crew',
    SurveyAudience.CHECKED_IN: 'checked in',
}


def _plural(n: int, word: str) -> str:
    return f'{n} {word}' + ('' if n == 1 else 's')


def _pct(n: int, total: int) -> int:
    return round(n * 100 / total) if total else 0


def segments(kind: SurveyQuestionKind, b: Breakdown) -> List[Tuple[str, int, str]]:
    """``(label, count, colour class)`` for a stacked bar: red to green for a scale, accent shades for choices."""
    c = b.counts
    if kind is SurveyQuestionKind.RATING:
        return [(v, c.get(v, 0), f'wiz-agree-l{v}') for v in '12345']
    if kind is SurveyQuestionKind.NPS:
        return [('0–6', sum(c.get(str(i), 0) for i in range(7)), 'wiz-agree-l1'),
                ('7–8', c.get('7', 0) + c.get('8', 0), 'wiz-agree-l3'),
                ('9–10', c.get('9', 0) + c.get('10', 0), 'wiz-agree-l5')]
    if kind is SurveyQuestionKind.YES_NO:
        return [('Yes', c.get('Yes', 0), 'wiz-agree-l5'), ('No', c.get('No', 0), 'wiz-agree-l1')]
    return [(label, n, f'wiz-stack-c{min(i, 5)}') for i, (label, n) in enumerate(c.items(), 1)]


def _stack(kind: SurveyQuestionKind, b: Breakdown, *, legend: bool = True, label: str = '') -> None:
    parts = segments(kind, b)
    total = sum(n for _, n, _ in parts)
    spoken = ', '.join(f'{name} {_pct(n, total)}%' for name, n, _ in parts)
    with ui.element('div').classes('wiz-agree-bar wiz-stack w-full') \
            .props(f'role=img aria-label="{label}{": " if label else ""}{spoken}"'):
        for _, n, cls in parts:
            if n:
                ui.element('span').classes(cls).style(f'width:{n * 100 / total}%')
    if legend:
        with ui.row().classes('wiz-agree-key items-center').props('aria-hidden=true'):
            for name, n, cls in parts:
                if n or kind is not SurveyQuestionKind.RATING:
                    with ui.element('span'):
                        ui.element('span').classes(f'wiz-agree-key__swatch {cls}')
                        ui.label(f'{name}: {_pct(n, total)}%').style('display:inline')


def _choice_list(result: QuestionResult) -> None:
    """A pick-many's choices as one line: people can pick several, so they don't stack."""
    b = result.overall
    picks = [f'{option} {_pct(n, b.count)}%' for option, n in b.counts.items() if option != result.none_option]
    if result.none_option and result.none_option in b.counts:
        picks.append(f'({result.none_option} {_pct(b.counts[result.none_option], b.count)}%)')
    ui.label(' · '.join(picks)).classes('wiz-report__choices')


def _agree(b: Breakdown) -> None:
    for statement in b.statements:
        share = b.agree_share(statement)
        with ui.column().classes('wiz-report__statement w-full gap-1'):
            ui.label(f'{statement} · {share}% agree' if share is not None else statement) \
                .classes('wiz-survey-result__meta')
            levels = b.statements[statement]
            spoken = f'{statement}: ' + ', '.join(str(n) for n in levels)
            with ui.element('div').classes('wiz-agree-bar w-full').props(f'role=img aria-label="{spoken}"'):
                total = sum(levels) or 1
                for level, n in enumerate(levels, 1):
                    if n:
                        ui.element('span').classes(f'wiz-agree-l{level}').style(f'width:{n * 100 / total}%')


def headline(result: QuestionResult, b: Optional[Breakdown] = None) -> Tuple[str, str]:
    """``(number, under it)`` for the right-hand column of a line or a table cell."""
    b = b or result.overall
    kind = result.kind
    if b.hidden:
        return str(b.count), 'too few to show'
    if kind is SurveyQuestionKind.RATING and b.average is not None:
        return f'avg {b.average}', f'{b.top_share}% rated 4–5'
    if kind is SurveyQuestionKind.NPS and b.score is not None:
        low, _, high = b.nps_split
        # Under about 50 answers one person swings the score a lot; the split says more.
        return f'{b.score:+d}', f'{high}% gave 9–10, {low}% 0–6' if b.count < 50 else f'avg {b.average}'
    score = score_of(kind, b)
    if score is not None:
        return f'{score}%', 'said yes'
    if kind is SurveyQuestionKind.CHOICE_ONE and b.counts and b.count:
        top, n = max(b.counts.items(), key=lambda kv: kv[1])
        return f'{_pct(n, b.count)}%', top
    if kind is SurveyQuestionKind.AGREE_SCALE:
        shares = [s for s in (b.agree_share(st) for st in b.statements) if s is not None]
        if shares:
            return f'{round(mean(shares))}%', 'agree'
    return str(b.count), ''


OpenSurvey = Optional[Callable[[int], Any]]


def _trend(result: QuestionResult, before: Breakdown) -> str:
    then: Optional[float] = score_of(result.kind, before)
    now: Optional[float] = score_of(result.kind, result.overall)
    if then is None or now is None:
        then, now = before.average, result.overall.average
    if then is None or now is None or then == now:
        return 'trending_flat'
    return 'trending_up' if now > then else 'trending_down'


def _last_time(result: QuestionResult, on_open: OpenSurvey) -> None:
    last = result.last_time
    if last is None:
        return
    number, under = headline(result, last.overall)
    with ui.row().classes('wiz-survey-result__last no-wrap items-center gap-1'):
        ui.icon(_trend(result, last.overall)).props('aria-hidden=true')
        ui.label('Last time in')
        if on_open is not None:
            link = ui.link(last.survey_title, '#').classes('wiz-survey-result__last-link')
            link.on('click.prevent', lambda _, sid=last.survey_id: on_open(sid))
        else:
            ui.label(last.survey_title)
        ui.label(f'· {number}' + (f' {under}' if under else '') + f' · {_plural(last.overall.count, "answer")}')


def _line(result: QuestionResult, sheet: CommentSheet, on_open: OpenSurvey = None) -> None:
    b = result.overall
    with ui.element('div').classes('wiz-report__line'):
        with ui.column().classes('wiz-report__q gap-0 min-w-0'):
            with ui.row().classes('items-center gap-2 no-wrap'):
                prompt = ui.label(('↳ ' if result.condition else '') + result.prompt).classes('wiz-report__prompt')
                prompt.tag = 'h4'
                if result.reworded_at:
                    ui.label('Edited').classes('wiz-chip wiz-chip--info')
            if result.reworded_at:
                ui.label(f'Wording changed {format_local_date(result.reworded_at)} after '
                         f'{result.reworded_after} answers. It was “{result.original_prompt}”.') \
                    .classes('wiz-survey-result__meta')
            bits = [result.condition] if result.condition else []
            bits.append(_plural(b.count, 'comment' if result.kind is SurveyQuestionKind.COMMENT else 'answer'))
            ui.label(' · '.join(bits)).classes('wiz-survey-result__meta')
            _last_time(result, on_open)
        with ui.column().classes('wiz-report__viz gap-1 min-w-0'):
            if result.kind is SurveyQuestionKind.COMMENT:
                if result.comments:
                    _sheet_link(f'{_plural(len(result.comments), "comment")} ›', result, sheet, icon='chat_bubble_outline')
                else:
                    ui.label('No comments yet.').classes('wiz-survey-result__meta')
            elif b.hidden:
                ui.label(f'Too few answers to show for this group ({b.count}).').classes('wiz-survey-result__meta')
            elif not b.count:
                ui.label('No answers yet.').classes('wiz-survey-result__meta')
            elif result.kind is SurveyQuestionKind.AGREE_SCALE:
                _agree(b)
            elif result.kind is SurveyQuestionKind.CHOICE_MANY:
                _choice_list(result)
            else:
                _stack(result.kind, b, label=result.prompt)
            if result.comments and result.kind is not SurveyQuestionKind.COMMENT:
                _sheet_link(f'{_plural(len(result.comments), "“Other” write-in")} ›', result, sheet)
        with ui.column().classes('wiz-report__num gap-0'):
            if result.kind is not SurveyQuestionKind.COMMENT:
                number, under = headline(result)
                ui.label(number).classes('wiz-report__value')
                if under:
                    ui.label(under).classes('wiz-survey-result__meta')


def _sheet_link(text: str, result: QuestionResult, sheet: CommentSheet, *, group: Optional[str] = None,
                icon: str = '') -> None:
    button = ui.button(text, icon=icon or None, on_click=lambda: sheet.open(result.prompt, result.comments, group=group)) \
        .props('flat dense no-caps color=primary').classes('wiz-report__link')
    if group:
        button._props['aria-label'] = f'{text.rstrip(" ›")} about {group}: {result.prompt}'


def _row_labels(results: Sequence[QuestionResult]) -> List[str]:
    labels = {label for r in results for label in r.groups}
    labels |= {label for r in results for label in r.comment_counts}
    folded = {label for r in results for label in r.folded}
    shown = sorted((label for label in labels if label not in folded and label != OTHER), key=str.lower)
    return shown + ([OTHER] if folded else [])


def _flags(result: QuestionResult) -> Dict[str, int]:
    """Groups scoring :data:`OUTLIER_POINTS` or more under the average of the others, and by how much."""
    scores = {label: score_of(result.kind, b) for label, b in result.groups.items() if label != OTHER}
    known = {label: s for label, s in scores.items() if s is not None}
    if len(known) < 3:
        return {}
    flags = {}
    for label, s in known.items():
        others = mean(v for k, v in known.items() if k != label)
        if others - s >= OUTLIER_POINTS:
            flags[label] = round(others - s)
    return flags


def _compare(results: Sequence[QuestionResult], sheet: CommentSheet, noun: str) -> None:
    labels = _row_labels(results)
    flags = {r.question_id: _flags(r) for r in results}
    columns = len(results)
    style = f'grid-template-columns: minmax(10rem, 1.4fr) 5rem repeat({columns}, minmax(0, 1fr))'
    with ui.element('div').classes('wiz-compare w-full').props('role=table').style(style):
        with ui.element('div').classes('wiz-compare__row wiz-compare__head').props('role=row'):
            ui.label(noun).props('role=columnheader')
            ui.label('Answers').props('role=columnheader')
            for r in results:
                ui.label(r.prompt).classes('wiz-compare__qhead').props('role=columnheader')
        for label in labels:
            flagged = [(r, flags[r.question_id][label]) for r in results if label in flags[r.question_id]]
            count = max((r.groups[label].count for r in results if label in r.groups), default=0)
            folded = next((r.folded for r in results if r.folded), [])
            row = ui.element('div').classes('wiz-compare__row' + (' is-flagged' if flagged else '')).props('role=row')
            with row:
                with ui.column().classes('wiz-compare__name gap-0 min-w-0').props('role=rowheader'):
                    ui.label(f'{OTHER} ({len(folded)})' if label == OTHER else label).classes('wiz-compare__label')
                    if label == OTHER:
                        ui.label(', '.join(folded)).classes('wiz-survey-result__meta wiz-compare__folded')
                    for _, points in flagged[:1]:
                        ui.badge(f'{points} pts below the rest', color='warning').classes('wiz-compare__flag')
                with ui.element('div').classes('wiz-compare__cell wiz-compare__cell--count').props('role=cell'):
                    ui.label('answers').classes('wiz-compare__cell-label')
                    ui.label(str(count)).classes('wiz-compare__count')
                for r in results:
                    with ui.element('div').classes('wiz-compare__cell min-w-0').props('role=cell'):
                        ui.label(r.prompt).classes('wiz-compare__cell-label')
                        _cell(r, label, sheet, label in flags[r.question_id])
    if any(r.folded for r in results):
        ui.label(f'A {noun.lower()} with under 5 answers is counted in {OTHER}, so nobody in it can be picked out.') \
            .classes('wiz-survey-result__meta wiz-compare__note')


def _cell(result: QuestionResult, label: str, sheet: CommentSheet, flagged: bool) -> None:
    if result.kind is SurveyQuestionKind.COMMENT:
        n = result.comment_counts.get(label, 0) if label != OTHER else \
            sum(result.comment_counts.get(f, 0) for f in result.folded)
        if n and label != OTHER:
            _sheet_link(f'{n} ›', result, sheet, group=label)
        else:
            ui.label(str(n)).classes('wiz-compare__count')
        return
    b = result.groups.get(label)
    if b is None:
        ui.label('–').classes('wiz-survey-result__meta')
        return
    number, under = headline(result, b)
    score = score_of(result.kind, b)
    with ui.row().classes('wiz-compare__metric items-center no-wrap w-full gap-2'):
        if score is not None and result.kind is not SurveyQuestionKind.NPS:
            ui.linear_progress(value=score / 100, show_value=False, size='6px') \
                .classes('col' + (' wiz-compare__bar--low' if flagged else '')) \
                .props(f'rounded aria-label="{label}: {number}"')
        elif not b.hidden and b.count and result.kind is SurveyQuestionKind.CHOICE_ONE:
            with ui.column().classes('col gap-0 min-w-0'):
                _stack(result.kind, b, legend=False, label=label)
        ui.label(number).classes('wiz-compare__value')
    if under and ((result.kind is SurveyQuestionKind.CHOICE_ONE and score is None) or b.hidden):
        ui.label(under).classes('wiz-survey-result__meta')


def render_sections(questions: Sequence[QuestionResult], sheet: CommentSheet, on_open: OpenSurvey = None) -> None:
    """Every section in survey order: lines, or a comparison table for a repeat.

    ``on_open`` opens another survey by id, for the "Last time" link to the one a question was copied from.
    """
    order: List[int] = []
    by_section: Dict[int, List[QuestionResult]] = {}
    for q in questions:
        if q.section_id not in by_section:
            order.append(q.section_id)
            by_section[q.section_id] = []
        by_section[q.section_id].append(q)
    for section_id in order:
        rows = by_section[section_id]
        first = rows[0]
        answered = max((r.overall.count for r in rows), default=0)
        with ui.element('section').classes('wiz-report w-full'):
            with ui.row().classes('wiz-report__head items-center'):
                title = ui.label(first.section_title).classes('wiz-report__title col min-w-0')
                title.tag = 'h3'
                if first.repeats:
                    noun = 'tournament' if first.audience is SurveyAudience.EACH_TOURNAMENT else 'position'
                    shown = len([label for label in _row_labels(rows) if label != OTHER])
                    meta = f'{_SECTION_WHO[first.audience]} · {_plural(shown, noun)}'
                else:
                    meta = f'{_SECTION_WHO.get(first.audience, "")} · {answered} answered'
                ui.label(meta).classes('wiz-report__meta')
            if first.repeats:
                _compare(rows, sheet, 'Tournament' if first.audience is SurveyAudience.EACH_TOURNAMENT else 'Position')
            else:
                for r in rows:
                    _line(r, sheet, on_open)
