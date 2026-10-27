"""The "Only show if…" conditions a survey question can carry.

A condition registers itself here and nowhere else: the builder's list, the
send-time facts and the respondent's page all read :data:`CONDITIONS`. Adding
one is a single :func:`register` call with a label and a ``collect`` that
answers it for the whole event in one pass.

``collect`` runs once, when the survey is sent, over the event's
:class:`~application.services.survey.participation.Participation`. It returns,
per user id, either ``True`` (the condition holds) or the ids of the
tournaments it holds for, when the condition is about a tournament. The
answers are stored on the recipient, so what each person sees doesn't change
if the event's data does afterwards.

A per-tournament condition inside a section that repeats per tournament checks
that tournament; anywhere else it holds when it holds for any of them.

Two shapes are not collected: a *negation* (``negates=``) is the opposite of
another condition, and the answer-based condition is decided on the page from
the respondent's own earlier answer. Deliberately one condition per question,
with no AND/OR: that limit is what keeps this a list and not a rules engine.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Mapping, Optional, Union

if TYPE_CHECKING:
    from application.services.survey.participation import Participation

Collected = Dict[int, Union[bool, List[int]]]

ANSWER = 'answer'


@dataclass(frozen=True)
class SurveyCondition:
    key: str
    label: str
    per_tournament: bool = False
    collect: Optional[Callable[['Participation'], Awaitable[Collected]]] = None
    negates: Optional[str] = None


CONDITIONS: Dict[str, SurveyCondition] = {}


def register(condition: SurveyCondition) -> SurveyCondition:
    assert condition.key not in CONDITIONS, condition.key
    assert (condition.collect is None) != (condition.negates is None) or condition.key == ANSWER
    CONDITIONS[condition.key] = condition
    return condition


def choices() -> Dict[str, str]:
    """``key → label`` in registration order, for the builder's list."""
    return {c.key: c.label for c in CONDITIONS.values()}


def holds(
    key: Optional[str],
    facts: Mapping[str, Any],
    tournament_id: Optional[int] = None,
) -> bool:
    """Whether a send-time condition holds for these facts.

    The answer-based condition always "holds" here: it is decided against the
    respondent's answers by :func:`answer_matches`. An unknown key (a condition
    removed after a survey was built) holds, so the question shows rather than
    silently vanishing.
    """
    if not key or key == ANSWER:
        return True
    condition = CONDITIONS.get(key)
    if condition is None:
        return True
    if condition.negates:
        return not holds(condition.negates, facts, tournament_id)
    value = (facts.get('conditions') or {}).get(key)
    if not condition.per_tournament:
        return bool(value)
    ids = value if isinstance(value, list) else []
    return tournament_id in ids if tournament_id is not None else bool(ids)


def answer_matches(args: Optional[Mapping[str, Any]], answer: Any) -> bool:
    """Whether an earlier answer is one of the values an answer-based question waits for.

    ``args`` is ``{'question_id': 12, 'values': [1, 2]}``; a pick-many answer
    matches when any picked option is listed. No answer yet means it doesn't.
    """
    if not args:
        return True
    wanted = list(args.get('values') or [])
    if answer is None or not wanted:
        return False
    if isinstance(answer, list):
        return any(a in wanted for a in answer)
    return answer in wanted


def describe(key: Optional[str], args: Optional[Mapping[str, Any]] = None,
             prompts: Optional[Mapping[int, str]] = None) -> str:
    """A short chip label for the builder: "Only if on stage"."""
    if not key:
        return ''
    if key == ANSWER:
        prompt = (prompts or {}).get(int((args or {}).get('question_id') or 0), 'an earlier question')
        values = ', '.join(str(v) for v in (args or {}).get('values') or [])
        return f'Only if “{prompt}” is {values}' if values else f'Only if “{prompt}” is answered'
    condition = CONDITIONS.get(key)
    return f'Only if {condition.label[0].lower()}{condition.label[1:]}' if condition else ''


async def _on_stage(p: 'Participation') -> Collected:
    return {user_id: sorted(ids) for user_id, ids in p.on_stage.items() if ids}


async def _tournament_room(p: 'Participation') -> Collected:
    return {user_id: sorted(ids) for user_id, ids in p.room_tournaments.items() if ids}


async def _crewed(p: 'Participation') -> Collected:
    return {user_id: True for user_id in p.crew}


async def _checked_in(p: 'Participation') -> Collected:
    return {user_id: True for user_id in p.checked_in}


async def _rescheduled(p: 'Participation') -> Collected:
    collected: Dict[int, List[int]] = {}
    for user_id, tournament_id in await p.reschedules():
        collected.setdefault(user_id, []).append(tournament_id)
    return {user_id: sorted(set(ids)) for user_id, ids in collected.items()}


register(SurveyCondition('on_stage', 'Had a match on a stage', per_tournament=True, collect=_on_stage))
register(SurveyCondition('not_on_stage', 'Had no match on a stage', per_tournament=True, negates='on_stage'))
register(SurveyCondition(
    'tournament_room', 'Played in the tournament room', per_tournament=True, collect=_tournament_room,
))
register(SurveyCondition(
    'not_tournament_room', "Didn't play in the tournament room", per_tournament=True,
    negates='tournament_room',
))
register(SurveyCondition('crewed', 'Crewed a match (comms, tracking or broadcast)', collect=_crewed))
register(SurveyCondition('checked_in', 'Checked in at the desk', collect=_checked_in))
register(SurveyCondition('rescheduled', 'Asked to reschedule a match', per_tournament=True, collect=_rescheduled))
register(SurveyCondition(ANSWER, 'Answered an earlier question a certain way'))
