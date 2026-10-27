"""What the AI question helper is told about an event: its shape and a few totals.

Built only from structure staff set up (tournament, stage and position names,
formats, dates) and from counts and times. Never a person's name or handle,
never anything a player or volunteer typed (comments, reschedule reasons),
and never another community's data: every read is tenant-scoped and
:func:`build` takes one survey. The text built here is stored on the batch
verbatim, so staff can read exactly what was sent.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional, Sequence

from application.repositories.survey_context_repository import SurveyContextRepository
from application.services.survey import plan
from application.services.survey.results import QuestionResult
from models import Survey, SurveyQuestionKind, SurveySection

SYSTEM = (
    "You help the staff of a gaming-event community write a short post-event survey. "
    "Staff will review every question you draft before anyone sees it. Write plain, specific, "
    "neutral questions a player or volunteer can answer in a few seconds, in the same language as "
    "the section title. Don't ask for names, contact details or anything that identifies a person. "
    "Don't repeat a question the survey already has."
)
KIND_HELP = {
    'rating': '1 to 5, poor to great',
    'nps': '0 to 10, how likely to recommend',
    'yes_no': 'yes or no',
    'choice_one': 'pick one of 2 to 10 options (give them in "options")',
    'choice_many': 'pick any of 2 to 10 options (give them in "options")',
    'agree_scale': '2 to 10 statements (in "options"), each answered strongly disagree to strongly agree',
    'comment': 'short free text',
}


@dataclass
class EventFacts:
    name: str = ''
    dates: str = ''
    tournaments: List[str] = field(default_factory=list)
    stages: List[str] = field(default_factory=list)
    positions: List[str] = field(default_factory=list)
    totals: List[str] = field(default_factory=list)


def _count(n: int, one: str, many: str) -> str:
    return f'{n} {one if n == 1 else many}'


def _minutes(seconds: float) -> int:
    return round(seconds / 60)


async def load_facts(event_id: int) -> EventFacts:
    repo = SurveyContextRepository
    facts = EventFacts()
    event = await repo.event(event_id)
    if event is not None:
        facts.name = event.name
        if event.starts_on:
            facts.dates = event.starts_on.isoformat() + (
                f' to {event.ends_on.isoformat()}' if event.ends_on and event.ends_on != event.starts_on else '')
    matches = await repo.match_times(event_id)
    by_tournament: Dict[int, List[dict]] = defaultdict(list)
    for row in matches:
        by_tournament[row['tournament_id']].append(row)
    for t in await repo.tournaments(event_id):
        rows = by_tournament.get(t['id'], [])
        bits = [t['tournament_format'] or 'format not given', f"{t['players_per_match']} players per match"]
        if t['average_match_duration']:
            bits.append(f"about {t['average_match_duration']} min per match")
        bits.append('played in the tournament room' if t['uses_tournament_room'] else 'played online')
        bits.append(f"{_count(len(rows), 'match', 'matches')}, {sum(1 for r in rows if r['stage_id'])} on a stage")
        facts.tournaments.append(f"{t['short_name'] or t['name']}: {', '.join(bits)}")
    facts.stages = await repo.stage_names(event_id)

    delays = [(r['started_at'] - r['scheduled_at']).total_seconds() for r in matches
              if r['started_at'] and r['scheduled_at']]
    if delays:
        late = sum(1 for d in delays if d > 10 * 60)
        facts.totals.append(
            f'Match starts: median {_minutes(median(delays))} min after the scheduled time; '
            f'{round(late * 100 / len(delays))}% started more than 10 min late ({len(delays)} matches)')
    lengths = [(r['finished_at'] - r['started_at']).total_seconds() for r in matches
               if r['finished_at'] and r['started_at']]
    if lengths:
        facts.totals.append(f'Match length: median {_minutes(median(lengths))} min')
    reschedules: Dict[str, int] = defaultdict(int)
    for r in await repo.reschedule_counts(event_id):
        reschedules[f"{r['kind'].value} {r['status'].value}"] += 1
    if reschedules:
        facts.totals.append('Reschedule requests: ' + ', '.join(f'{n} {k}' for k, n in sorted(reschedules.items())))

    positions = {p['id']: p['name'] for p in await repo.positions(event_id)}
    assignments = await repo.assignment_times(event_id)
    hours: Dict[int, float] = defaultdict(float)
    shifts: Dict[int, int] = defaultdict(int)
    for a in assignments:
        shifts[a['position_id']] += 1
        if a['starts_at'] and a['ends_at']:
            hours[a['position_id']] += (a['ends_at'] - a['starts_at']).total_seconds() / 3600
    for pid, name in positions.items():
        if shifts.get(pid):
            facts.positions.append(f'{name}: {shifts[pid]} shifts filled, {round(hours[pid])} volunteer hours')
        else:
            facts.positions.append(f'{name}: no shifts filled')
    if assignments:
        volunteers = len({a['user_id'] for a in assignments})
        checked = sum(1 for a in assignments if a['checked_in_at'])
        facts.totals.append(
            f'Volunteers: {volunteers} people, {round(sum(hours.values()))} hours in all, '
            f'{round(checked * 100 / len(assignments))}% of shifts checked in')
    return facts


def _result_line(result: QuestionResult) -> Optional[str]:
    """One people-free line about last year's answers to a question; comments are left out entirely."""
    b = result.overall
    if result.kind is SurveyQuestionKind.COMMENT or b.hidden or not b.count:
        return None
    if result.kind is SurveyQuestionKind.AGREE_SCALE:
        shares = [f'"{s}" {b.agree_share(s)}% agree' for s in b.statements if b.agree_share(s) is not None]
        return '; '.join(shares) or None
    if b.score is not None:
        return f'recommend score {b.score:+d} ({b.count} answers)'
    if b.average is not None:
        return f'average {b.average} of 5 ({b.count} answers)'
    total = sum(b.counts.values()) or 1
    top = sorted(b.counts.items(), key=lambda kv: -kv[1])[:4]
    return ', '.join(f'{k} {round(v * 100 / total)}%' for k, v in top) + f' ({b.count} answers)'


def build(survey: Survey, section: SurveySection, facts: EventFacts,
          previous: Optional[Survey], previous_results: Sequence[QuestionResult]) -> str:
    """The user message: the event, this survey so far, last year's numbers, and the ask."""
    lines: List[str] = [f'# Event: {facts.name or "(no name)"}']
    if facts.dates:
        lines.append(f'Dates: {facts.dates}')
    if facts.tournaments:
        lines += ['', '## Tournaments'] + [f'- {t}' for t in facts.tournaments]
    if facts.stages:
        lines += ['', '## Stream stages', '- ' + ', '.join(facts.stages)]
    if facts.positions:
        lines += ['', '## Volunteer positions'] + [f'- {p}' for p in facts.positions]
    if facts.totals:
        lines += ['', '## How it went (totals)'] + [f'- {t}' for t in facts.totals]

    lines += ['', f'## This survey: {survey.title}']
    for s in survey.sections:
        marker = '  <- the section to write for' if s.id == section.id else ''
        lines.append(f'- Section "{s.title}" ({plan.AUDIENCE_LABELS[s.audience]}){marker}')
        for q in s.questions:
            lines.append(f'  - {q.prompt} [{q.kind.value}]')
    if previous is not None:
        lines += ['', f'## Last time: {previous.title}']
        by_id = {r.question_id: r for r in previous_results}
        for s in previous.sections:
            for q in s.questions:
                result = by_id.get(q.id)
                summary = _result_line(result) if result else None
                lines.append(f'- {q.prompt} [{q.kind.value}]' + (f': {summary}' if summary else ''))

    kinds = '\n'.join(f'- {k}: {v}' for k, v in KIND_HELP.items())
    lines += [
        '', '## What to do',
        f'Draft 3 to 5 new questions for the section "{section.title}", who are: '
        f'{plan.AUDIENCE_LABELS[section.audience].lower()}. '
        'Lean on what the totals above say went well or badly, and on last time\'s weakest answers. '
        'Keep each question under 120 characters. Use these kinds:',
        kinds,
        'Give "options" as an empty list for kinds that take none.',
    ]
    return '\n'.join(lines)


SCHEMA = {
    'type': 'object',
    'properties': {
        'questions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'prompt': {'type': 'string'},
                    'kind': {'type': 'string', 'enum': list(KIND_HELP)},
                    'options': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['prompt', 'kind', 'options'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['questions'],
    'additionalProperties': False,
}
