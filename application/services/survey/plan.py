"""Which parts of a survey one person sees, and how long it takes them.

Pure functions over a survey's sections and one recipient's frozen facts, so
the respondent page, the preview, the builder's time estimate and the results
all agree on the same answer. A *part* is one section as one person sees it:
a section that repeats per tournament becomes one part per tournament they
played (or ran, for an organizer's section).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from application.services.survey.conditions import (
    Entry,
    all_hold,
    answer_entries,
    answer_matches,
    describe,
    entry_holds,
)
from models import SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveySection

# Rough seconds to answer each kind, for the 10-minute budget. A comment is
# optional and most people skip it, but the budget assumes they don't.
SECONDS: Dict[SurveyQuestionKind, int] = {
    SurveyQuestionKind.RATING: 8,
    SurveyQuestionKind.NPS: 8,
    SurveyQuestionKind.YES_NO: 5,
    SurveyQuestionKind.CHOICE_ONE: 8,
    SurveyQuestionKind.CHOICE_MANY: 12,
    SurveyQuestionKind.COMMENT: 25,
    SurveyQuestionKind.AGREE_SCALE: 5,  # per statement
}

# Shown after someone sends their answers when staff wrote no line of their own.
DEFAULT_AFTER_SEND = "We'll share what we change because of your answers."

BUDGET_SECONDS = 10 * 60
# What most people's path should stay under; the budget above is the ceiling for anyone's.
TARGET_SECONDS = 5 * 60
REPEATING = (SurveyAudience.EACH_TOURNAMENT, SurveyAudience.EACH_TOURNAMENT_RUN, SurveyAudience.EACH_POSITION)

# A crew-role audience → the role in a recipient's ``crew_roles`` fact, and the part's reason line.
CREW_AUDIENCES: Dict[SurveyAudience, Tuple[str, str]] = {
    SurveyAudience.COMMENTATORS: ('commentary', 'You commentated a match on stage'),
    SurveyAudience.TRACKERS: ('tracking', 'You tracked a match on stage'),
    SurveyAudience.BROADCASTERS: ('broadcast', 'You crewed a broadcast on stage'),
}
CREW_ROLE_NAMES = ('commentary', 'tracking', 'broadcast')
PERSONA_ROLES = {'commentator': 'commentary', 'tracker': 'tracking', 'broadcaster': 'broadcast'}

AUDIENCE_LABELS: Dict[SurveyAudience, str] = {
    SurveyAudience.EVERYONE: 'Everyone',
    SurveyAudience.EACH_TOURNAMENT: 'Repeats for each tournament they played',
    SurveyAudience.EACH_TOURNAMENT_RUN: 'Repeats for each tournament they ran (tournament admins)',
    SurveyAudience.EACH_POSITION: 'Repeats for each volunteer position they worked',
    SurveyAudience.CREW: 'Match crew, any role (comms, tracking, broadcast)',
    SurveyAudience.COMMENTATORS: 'Commentators on stage matches',
    SurveyAudience.TRACKERS: 'Trackers on stage matches',
    SurveyAudience.BROADCASTERS: 'Broadcast crew on stage matches',
    SurveyAudience.CHECKED_IN: 'Everyone the desk checked in',
}

KIND_LABELS: Dict[SurveyQuestionKind, str] = {
    SurveyQuestionKind.RATING: 'Rating 1–5',
    SurveyQuestionKind.NPS: 'Recommend 0–10',
    SurveyQuestionKind.YES_NO: 'Yes / No',
    SurveyQuestionKind.CHOICE_ONE: 'Pick one',
    SurveyQuestionKind.CHOICE_MANY: 'Pick any',
    SurveyQuestionKind.COMMENT: 'Comment',
    SurveyQuestionKind.AGREE_SCALE: 'Agree scale',
}


@dataclass
class Part:
    """One section as one person sees it."""

    section_id: int
    title: str
    reason: str
    scope: str
    scope_label: str
    icon: str
    questions: List[SurveyQuestion] = field(default_factory=list)
    follows: List[Entry] = field(default_factory=list)  # the section's answer-based conditions
    description: str = ''

    @property
    def key(self) -> str:
        return f'{self.section_id}|{self.scope}'

    @property
    def tournament_id(self) -> Optional[int]:
        return int(self.scope[2:]) if self.scope.startswith('t:') else None


def _plural(n: int, word: str, plural: str = '') -> str:
    return f'{n} {word}' if n == 1 else f'{n} {plural or word + "s"}'


def _repeats(section: SurveySection, facts: Mapping[str, Any]) -> List[Tuple[str, str, str, str]]:
    """``(scope, scope label, title, reason)`` for each time ``section`` shows."""
    audience = section.audience
    if audience is SurveyAudience.EACH_TOURNAMENT:
        return [
            (f"t:{t['id']}", t['name'], t['name'],
             f"You played {_plural(int(t.get('matches') or 0), 'match', 'matches')}")
            for t in facts.get('tournaments') or []
        ]
    if audience is SurveyAudience.EACH_TOURNAMENT_RUN:
        return [(f"o:{t['id']}", t['name'], t['name'], 'You ran this tournament')
                for t in facts.get('organized') or []]
    if audience is SurveyAudience.EACH_POSITION:
        return [
            (f"p:{p['id']}", p['name'], p['name'], f"You worked {_plural(int(p.get('shifts') or 0), 'shift')}")
            for p in facts.get('positions') or []
        ]
    if audience is SurveyAudience.CREW:
        return [('', '', section.title, 'You crewed a match')] if facts.get('crew') else []
    if audience in CREW_AUDIENCES:
        role, reason = CREW_AUDIENCES[audience]
        return [('', '', section.title, reason)] if role in (facts.get('crew_roles') or []) else []
    if audience is SurveyAudience.CHECKED_IN:
        return [('', '', section.title, 'The desk checked you in')] if facts.get('checked_in') else []
    return [('', '', section.title, 'Everyone')]


_ICONS = {
    SurveyAudience.EVERYONE: 'public',
    SurveyAudience.EACH_TOURNAMENT: 'sports_esports',
    SurveyAudience.EACH_TOURNAMENT_RUN: 'emoji_events',
    SurveyAudience.EACH_POSITION: 'badge',
    SurveyAudience.CREW: 'mic',
    SurveyAudience.COMMENTATORS: 'record_voice_over',
    SurveyAudience.TRACKERS: 'checklist',
    SurveyAudience.BROADCASTERS: 'videocam',
    SurveyAudience.CHECKED_IN: 'how_to_reg',
}


def icon_for(section: SurveySection) -> str:
    return _ICONS[section.audience]


def parts_for(sections: Iterable[SurveySection], facts: Mapping[str, Any], *,
              ignore_conditions: bool = False) -> List[Part]:
    """The parts one person sees, in survey order; a part left with no questions is dropped.

    Sections must have their ``questions`` prefetched. ``ignore_conditions`` is
    the preview's "Every question" view: every repeat, every question. A part
    whose section waits on an answer is still listed; :func:`part_shown`
    decides on the page whether it shows.
    """
    parts: List[Part] = []
    for section in sections:
        questions = list(section.questions)
        for scope, label, title, reason in _repeats(section, facts):
            tournament_id = int(scope[2:]) if scope.startswith('t:') else None
            if not ignore_conditions and not all_hold(section.conditions, facts, tournament_id):
                continue
            shown = questions if ignore_conditions else [
                q for q in questions if all_hold(q.conditions, facts, tournament_id)
            ]
            if shown:
                parts.append(Part(section.id, title, reason, scope, label, _ICONS[section.audience], shown,
                                  [] if ignore_conditions else answer_entries(section.conditions),
                                  section.description or ''))
    return parts


_ONLY_FOR: Dict[SurveyAudience, str] = {
    SurveyAudience.EACH_TOURNAMENT: 'Only for people who played a tournament',
    SurveyAudience.EACH_TOURNAMENT_RUN: 'Only for tournament admins',
    SurveyAudience.EACH_POSITION: 'Only for volunteers who worked a position',
    SurveyAudience.CREW: 'Only for match crew',
    SurveyAudience.COMMENTATORS: 'Only for commentators',
    SurveyAudience.TRACKERS: 'Only for trackers',
    SurveyAudience.BROADCASTERS: 'Only for broadcast crew',
    SurveyAudience.CHECKED_IN: 'Only for people the desk checked in',
}


@dataclass(frozen=True)
class Hidden:
    """Something one kind of person won't see, and why: a whole part (``prompt`` None) or one question."""

    section_id: int
    title: str
    prompt: Optional[str]
    reason: str


def hidden_from(sections: Iterable[SurveySection], facts: Mapping[str, Any]) -> List[Hidden]:
    """What :func:`parts_for` leaves out for these facts, with the audience or condition that leaves it out.

    For the preview. Answer-based conditions aren't here: the page shows and
    hides those as the answers change.
    """
    sections = list(sections)
    prompts = {q.id: q.prompt for s in sections for q in s.questions}
    hidden: List[Hidden] = []
    for section in sections:
        questions = list(section.questions)
        if not questions:
            continue
        repeats = _repeats(section, facts)
        if not repeats:
            hidden.append(Hidden(section.id, section.title, None, _ONLY_FOR.get(section.audience, 'Not for them')))
            continue
        tournaments = [int(scope[2:]) if scope.startswith('t:') else None for scope, *_ in repeats]
        failing = next((e for e in section.conditions or []
                        if not any(entry_holds(e, facts, t) for t in tournaments)), None)
        if failing is not None:
            hidden.append(Hidden(section.id, section.title, None, describe(failing, prompts)))
            continue
        for question in questions:
            failing = next((e for e in question.conditions or []
                            if not any(entry_holds(e, facts, t) for t in tournaments)), None)
            if failing is not None:
                hidden.append(Hidden(section.id, section.title, question.prompt, describe(failing, prompts)))
    return hidden


def before_section(sections: Iterable[SurveySection], section_id: int) -> Dict[int, SurveyQuestion]:
    """The questions in the sections before ``section_id``, which that section may wait on."""
    found: Dict[int, SurveyQuestion] = {}
    for section in sections:
        if section.id == section_id:
            return found
        for q in section.questions:
            found[q.id] = q
    return found


def earlier(sections: Iterable[SurveySection], section_id: int,
            before_question_id: Optional[int]) -> Dict[int, SurveyQuestion]:
    """The questions before a point in the survey, which an answer-based question may follow.

    ``before_question_id`` is the question being edited; ``None`` means a new
    question at the end of ``section_id``.
    """
    found: Dict[int, SurveyQuestion] = {}
    for section in sections:
        for q in section.questions:
            if q.id == before_question_id:
                return found
            found[q.id] = q
        if section.id == section_id:
            return found
    return found


AnswerKey = Tuple[int, str]


def lookup_answer(answers: Mapping[AnswerKey, Any], question_id: int, scope: str) -> Any:
    """The answer to ``question_id`` in ``scope``, else in the part with no scope."""
    if (question_id, scope) in answers:
        return answers[(question_id, scope)]
    return answers.get((question_id, ''))


def _matches(entries: Sequence[Entry], scope: str, answers: Mapping[AnswerKey, Any]) -> bool:
    return all(
        answer_matches(e, lookup_answer(answers, int(e.get('question_id') or 0), scope)) for e in entries
    )


def visible(question: SurveyQuestion, scope: str, answers: Mapping[AnswerKey, Any]) -> bool:
    """Whether a question shows, given the respondent's answers so far (its answer-based conditions)."""
    return _matches(answer_entries(question.conditions), scope, answers)


def part_shown(part: Part, answers: Mapping[AnswerKey, Any]) -> bool:
    """Whether a part shows, given the respondent's answers so far (its section's answer-based conditions)."""
    return _matches(part.follows, part.scope, answers)


def seconds(parts: Sequence[Part]) -> int:
    """How long ``parts`` take, counting answer-based follow-ups as shown (worst case)."""
    return sum(question_seconds(q) for part in parts for q in part.questions)


def question_seconds(question: SurveyQuestion) -> int:
    each = SECONDS.get(question.kind, 8)
    if question.kind is SurveyQuestionKind.AGREE_SCALE:
        return each * max(1, len(question.options or []))
    if question.long_answer:
        return each * 2
    return each


def question_count(parts: Sequence[Part]) -> int:
    return sum(len(part.questions) for part in parts)


def format_duration(total_seconds: int) -> str:
    minutes, secs = divmod(int(total_seconds), 60)
    if minutes and secs:
        return f'{minutes} min {secs} s'
    return f'{minutes} min' if minutes else f'{secs} s'


POSITION_PERSONA = 'position:'


def preview_facts(persona: str, tournaments: Sequence[dict], positions: Sequence[dict]) -> Dict[str, Any]:
    """Made-up facts for one kind of person, for the preview's "Showing" switch.

    Uses the event's real tournament and position names, up to two of each, so
    the repeating sections read as they will. ``position:<id>`` is someone who
    worked that one position.
    """
    two_tournaments = [dict(t, matches=2) for t in tournaments[:2]] or [
        {'id': 0, 'name': 'A tournament', 'matches': 2},
    ]
    one_position = [dict(p, shifts=3) for p in positions[:1]] or [{'id': 0, 'name': 'A position', 'shifts': 3}]
    ids = [t['id'] for t in two_tournaments]
    base: Dict[str, Any] = {'tournaments': [], 'organized': [], 'positions': [], 'crew': False,
                            'checked_in': True, 'conditions': {'checked_in': True}}
    if persona in ('player', 'player_on_stage'):
        base['tournaments'] = two_tournaments
        base['conditions'].update(tournament_room=ids, tournament_room_any=True)
        if persona == 'player_on_stage':
            base['conditions'].update(on_stage=ids[:1], on_stage_any=True)
    elif persona == 'organizer':
        base['organized'] = [{'id': t['id'], 'name': t['name']} for t in two_tournaments]
        base['conditions']['ran_tournament'] = True
    elif persona == 'volunteer':
        base['positions'] = one_position
        base['conditions']['volunteered'] = True
    elif persona.startswith(POSITION_PERSONA):
        wanted = persona[len(POSITION_PERSONA):]
        base['positions'] = [dict(p, shifts=3) for p in positions if str(p['id']) == wanted][:1] or one_position
        base['conditions']['volunteered'] = True
    elif persona == 'crew':
        base.update(crew=True, crew_roles=list(CREW_ROLE_NAMES))
        base['conditions']['crewed'] = True
    elif persona in PERSONA_ROLES:
        base.update(crew=True, crew_roles=[PERSONA_ROLES[persona]])
        base['conditions']['crewed'] = True
    elif persona == 'everything':
        base.update(tournaments=two_tournaments[:1], positions=one_position, crew=True,
                    crew_roles=list(CREW_ROLE_NAMES),
                    organized=[{'id': t['id'], 'name': t['name']} for t in two_tournaments[:1]])
    return base


PREVIEW_PERSONAS: Dict[str, str] = {
    'everything': 'Every question',
    'player': 'Player',
    'player_on_stage': 'Player on stage',
    'organizer': 'Tournament organizer',
    'volunteer': 'Volunteer',
    'crew': 'Crew, every role',
    'commentator': 'Commentator',
    'tracker': 'Tracker',
    'broadcaster': 'Broadcast crew',
    'checked_in': 'Checked in only',
}


def preview_personas(positions: Sequence[dict]) -> Dict[str, str]:
    """:data:`PREVIEW_PERSONAS` plus one per volunteer position, so a part for one position can be checked."""
    personas = dict(PREVIEW_PERSONAS)
    for p in positions:
        personas[f"{POSITION_PERSONA}{p['id']}"] = p['name']
    return personas
