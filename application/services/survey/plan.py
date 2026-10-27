"""Which parts of a survey one person sees, and how long it takes them.

Pure functions over a survey's sections and one recipient's frozen facts, so
the respondent page, the preview, the builder's time estimate and the results
all agree on the same answer. A *part* is one section as one person sees it:
a section that repeats per tournament becomes one part per tournament they
played.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from application.services.survey.conditions import ANSWER, answer_matches, holds
from models import SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveySection

# Rough seconds to answer each kind, for the 5-minute budget. A comment is
# optional and most people skip it, but the budget assumes they don't.
SECONDS: Dict[SurveyQuestionKind, int] = {
    SurveyQuestionKind.RATING: 8,
    SurveyQuestionKind.NPS: 8,
    SurveyQuestionKind.YES_NO: 5,
    SurveyQuestionKind.CHOICE_ONE: 8,
    SurveyQuestionKind.CHOICE_MANY: 12,
    SurveyQuestionKind.COMMENT: 25,
}
BUDGET_SECONDS = 5 * 60
REPEATING = (SurveyAudience.EACH_TOURNAMENT, SurveyAudience.EACH_POSITION)

AUDIENCE_LABELS: Dict[SurveyAudience, str] = {
    SurveyAudience.EVERYONE: 'Everyone',
    SurveyAudience.EACH_TOURNAMENT: 'Repeats for each tournament they played',
    SurveyAudience.EACH_POSITION: 'Repeats for each volunteer position they worked',
    SurveyAudience.CREW: 'Match crew (comms, tracking, broadcast)',
    SurveyAudience.CHECKED_IN: 'Everyone the desk checked in',
}

KIND_LABELS: Dict[SurveyQuestionKind, str] = {
    SurveyQuestionKind.RATING: 'Rating 1–5',
    SurveyQuestionKind.NPS: 'Recommend 0–10',
    SurveyQuestionKind.YES_NO: 'Yes / No',
    SurveyQuestionKind.CHOICE_ONE: 'Pick one',
    SurveyQuestionKind.CHOICE_MANY: 'Pick any',
    SurveyQuestionKind.COMMENT: 'Comment',
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
    if audience is SurveyAudience.EACH_POSITION:
        return [
            (f"p:{p['id']}", p['name'], p['name'], f"You worked {_plural(int(p.get('shifts') or 0), 'shift')}")
            for p in facts.get('positions') or []
        ]
    if audience is SurveyAudience.CREW:
        return [('', '', section.title, 'You crewed a match')] if facts.get('crew') else []
    if audience is SurveyAudience.CHECKED_IN:
        return [('', '', section.title, 'The desk checked you in')] if facts.get('checked_in') else []
    return [('', '', section.title, 'Everyone')]


_ICONS = {
    SurveyAudience.EVERYONE: 'public',
    SurveyAudience.EACH_TOURNAMENT: 'sports_esports',
    SurveyAudience.EACH_POSITION: 'badge',
    SurveyAudience.CREW: 'mic',
    SurveyAudience.CHECKED_IN: 'how_to_reg',
}


def parts_for(sections: Iterable[SurveySection], facts: Mapping[str, Any], *,
              ignore_conditions: bool = False) -> List[Part]:
    """The parts one person sees, in survey order; a part left with no questions is dropped.

    Sections must have their ``questions`` prefetched. ``ignore_conditions`` is
    the preview's "Every question" view: every repeat, every question.
    """
    parts: List[Part] = []
    for section in sections:
        questions = list(section.questions)
        for scope, label, title, reason in _repeats(section, facts):
            tournament_id = int(scope[2:]) if scope.startswith('t:') else None
            shown = questions if ignore_conditions else [
                q for q in questions if holds(q.condition, facts, tournament_id)
            ]
            if shown:
                parts.append(Part(section.id, title, reason, scope, label, _ICONS[section.audience], shown))
    return parts


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


def visible(question: SurveyQuestion, scope: str, answers: Mapping[AnswerKey, Any]) -> bool:
    """Whether an answer-based question shows, given the respondent's answers so far."""
    if question.condition != ANSWER:
        return True
    args = question.condition_args or {}
    return answer_matches(args, lookup_answer(answers, int(args.get('question_id') or 0), scope))


def seconds(parts: Sequence[Part]) -> int:
    """How long ``parts`` take, counting answer-based follow-ups as shown (worst case)."""
    return sum(SECONDS.get(q.kind, 8) for part in parts for q in part.questions)


def question_count(parts: Sequence[Part]) -> int:
    return sum(len(part.questions) for part in parts)


def format_duration(total_seconds: int) -> str:
    minutes, secs = divmod(int(total_seconds), 60)
    if minutes and secs:
        return f'{minutes} min {secs} s'
    return f'{minutes} min' if minutes else f'{secs} s'


def preview_facts(persona: str, tournaments: Sequence[dict], positions: Sequence[dict]) -> Dict[str, Any]:
    """Made-up facts for one kind of person, for the preview's "Showing" switch.

    Uses the event's real tournament and position names, up to two of each, so
    the repeating sections read as they will.
    """
    two_tournaments = [dict(t, matches=2) for t in tournaments[:2]] or [
        {'id': 0, 'name': 'A tournament', 'matches': 2},
    ]
    one_position = [dict(p, shifts=3) for p in positions[:1]] or [{'id': 0, 'name': 'A position', 'shifts': 3}]
    ids = [t['id'] for t in two_tournaments]
    base: Dict[str, Any] = {'tournaments': [], 'positions': [], 'crew': False, 'checked_in': True,
                            'conditions': {'checked_in': True}}
    if persona in ('player', 'player_on_stage'):
        base['tournaments'] = two_tournaments
        base['conditions']['tournament_room'] = ids
        if persona == 'player_on_stage':
            base['conditions']['on_stage'] = ids[:1]
    elif persona == 'volunteer':
        base['positions'] = one_position
    elif persona == 'crew':
        base['crew'] = True
        base['conditions']['crewed'] = True
    elif persona == 'everything':
        base.update(tournaments=two_tournaments[:1], positions=one_position, crew=True)
    return base


PREVIEW_PERSONAS: Dict[str, str] = {
    'everything': 'Every question',
    'player': 'Player',
    'player_on_stage': 'Player on stage',
    'volunteer': 'Volunteer',
    'crew': 'Crew',
    'checked_in': 'Checked in only',
}
