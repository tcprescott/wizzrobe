"""The "Only show if…" conditions a survey question or section can carry.

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

Three shapes are not collected: a *negation* (``negates=``) is the opposite of
another condition, a *parameterised* one (``check=``) is decided from the
recipient's facts and what the entry names (the positions condition), and the
answer-based condition is decided on the page from the respondent's own
earlier answer.

A question or section stores its conditions as a list of entries,
``{'key': 'on_stage'}``, ``{'key': 'positions', 'position_ids': [4, 7],
'names': ['Race Proctor', 'Admin Desk']}`` or ``{'key': 'answer',
'question_id': 12, 'values': [1, 2]}``. A positions entry's ``names`` are
copied from the event's schedule whenever it is saved, for the chips. A question takes up to :data:`MAX_PER_QUESTION` and a section up to
:data:`MAX_PER_SECTION`, and every one must hold: there is no OR and no
nesting, which is what keeps this a list and not a rules engine.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Union

if TYPE_CHECKING:
    from application.services.survey.participation import Participation

Collected = Dict[int, Union[bool, List[int]]]

ANSWER = 'answer'
POSITIONS = 'positions'
MAX_PER_QUESTION = 3
MAX_PER_SECTION = 1

Entry = Mapping[str, Any]


@dataclass(frozen=True)
class SurveyCondition:
    key: str
    label: str
    per_tournament: bool = False
    collect: Optional[Callable[['Participation'], Awaitable[Collected]]] = None
    negates: Optional[str] = None
    check: Optional[Callable[['Entry', Mapping[str, Any]], bool]] = None


CONDITIONS: Dict[str, SurveyCondition] = {}


def register(condition: SurveyCondition) -> SurveyCondition:
    assert condition.key not in CONDITIONS, condition.key
    shapes = (condition.collect, condition.negates, condition.check)
    assert sum(s is not None for s in shapes) == 1 or condition.key == ANSWER
    CONDITIONS[condition.key] = condition
    return condition


def choices() -> Dict[str, str]:
    """``key → label`` in registration order, for the builder's list."""
    return {c.key: c.label for c in CONDITIONS.values()}


def holds(
    key: Optional[str],
    facts: Mapping[str, Any],
    tournament_id: Optional[int] = None,
    entry: Optional[Entry] = None,
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
    if condition.check:
        return condition.check(entry or {}, facts)
    value = (facts.get('conditions') or {}).get(key)
    if not condition.per_tournament:
        return bool(value)
    ids = value if isinstance(value, list) else []
    return tournament_id in ids if tournament_id is not None else bool(ids)


def all_hold(entries: Optional[Sequence[Entry]], facts: Mapping[str, Any],
             tournament_id: Optional[int] = None) -> bool:
    """Whether every send-time condition in ``entries`` holds (answer-based ones are left to the page)."""
    return all(entry_holds(e, facts, tournament_id) for e in entries or [])


def entry_holds(entry: Entry, facts: Mapping[str, Any], tournament_id: Optional[int] = None) -> bool:
    return holds(entry.get('key'), facts, tournament_id, entry)


def answer_entries(entries: Optional[Sequence[Entry]]) -> List[Entry]:
    return [e for e in entries or [] if e.get('key') == ANSWER]


def answer_matches(entry: Optional[Entry], answer: Any) -> bool:
    """Whether an earlier answer is one of the values an answer-based condition waits for.

    ``entry`` is ``{'key': 'answer', 'question_id': 12, 'values': [1, 2]}``; a
    pick-many answer matches when any picked option is listed. No answer yet
    means it doesn't.
    """
    if not entry:
        return True
    wanted = list(entry.get('values') or [])
    if answer is None or not wanted:
        return False
    if isinstance(answer, list):
        return any(a in wanted for a in answer)
    return answer in wanted


def follows(entries: Optional[Sequence[Entry]], question_id: int) -> bool:
    """Whether one of ``entries`` waits on ``question_id``'s answer."""
    return any(int(e.get('question_id') or 0) == question_id for e in answer_entries(entries))


def remap(entries: Optional[Sequence[Entry]], ids: Mapping[int, int]) -> Optional[List[dict]]:
    """``entries`` with answer-based question ids swapped through ``ids``, for a copied survey."""
    if not entries:
        return None
    copied = []
    for e in entries:
        e = dict(e)
        if e.get('key') == ANSWER and e.get('question_id') in ids:
            e['question_id'] = ids[e['question_id']]
        copied.append(e)
    return copied


def describe(entry: Optional[Entry], prompts: Optional[Mapping[int, str]] = None) -> str:
    """A short chip label for the builder: "Only if on stage"."""
    entry = entry or {}
    key = entry.get('key')
    if not key:
        return ''
    if key == ANSWER:
        prompt = (prompts or {}).get(int(entry.get('question_id') or 0), 'an earlier question')
        values = ', '.join(str(v) for v in entry.get('values') or [])
        return f'Only if “{prompt}” is {values}' if values else f'Only if “{prompt}” is answered'
    if key == POSITIONS:
        names = [str(n) for n in entry.get('names') or []]
        return f'Only if worked as {_either(names)}' if names else 'Only if worked a picked position'
    condition = CONDITIONS.get(key)
    return f'Only if {condition.label[0].lower()}{condition.label[1:]}' if condition else ''


def _either(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


async def _on_stage(p: 'Participation') -> Collected:
    return {user_id: sorted(ids) for user_id, ids in p.on_stage.items() if ids}


async def _tournament_room(p: 'Participation') -> Collected:
    return {user_id: sorted(ids) for user_id, ids in p.room_tournaments.items() if ids}


def _any(per_tournament: Callable[['Participation'], Awaitable[Collected]]
         ) -> Callable[['Participation'], Awaitable[Collected]]:
    async def collect(p: 'Participation') -> Collected:
        return {user_id: True for user_id, held in (await per_tournament(p)).items() if held}
    return collect


async def _crewed(p: 'Participation') -> Collected:
    return {user_id: True for user_id in p.crew}


async def _checked_in(p: 'Participation') -> Collected:
    return {user_id: True for user_id in p.checked_in}


async def _volunteered(p: 'Participation') -> Collected:
    return {user_id: True for user_id, worked in p.positions.items() if worked}


def _worked_one_of(entry: Entry, facts: Mapping[str, Any]) -> bool:
    wanted = {int(i) for i in entry.get('position_ids') or []}
    return any(int(p.get('id') or 0) in wanted for p in facts.get('positions') or [])


async def _ran_tournament(p: 'Participation') -> Collected:
    return {user_id: True for user_id, ran in p.organized.items() if ran}


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
# The same two facts, ignoring which tournament: inside a section that repeats
# per tournament they still ask about the whole event (Thomas, 2026-10-27).
register(SurveyCondition('on_stage_any', 'Had a match on a stage (any tournament)', collect=_any(_on_stage)))
register(SurveyCondition('not_on_stage_any', 'Had no match on a stage (any tournament)', negates='on_stage_any'))
register(SurveyCondition(
    'tournament_room_any', 'Played in the tournament room (any tournament)', collect=_any(_tournament_room),
))
register(SurveyCondition(
    'not_tournament_room_any', "Didn't play in the tournament room (any tournament)",
    negates='tournament_room_any',
))
register(SurveyCondition('crewed', 'Crewed a match on stage (comms, tracking or broadcast)', collect=_crewed))
register(SurveyCondition('checked_in', 'Checked in at the desk', collect=_checked_in))
register(SurveyCondition('rescheduled', 'Asked to reschedule a match', per_tournament=True, collect=_rescheduled))
register(SurveyCondition('volunteered', 'Volunteered (any position)', collect=_volunteered))
register(SurveyCondition(POSITIONS, 'Worked one of these positions', check=_worked_one_of))
register(SurveyCondition('ran_tournament', 'Ran a tournament (tournament admin)', collect=_ran_tournament))
register(SurveyCondition('not_ran_tournament', "Didn't run a tournament", negates='ran_tournament'))
register(SurveyCondition(ANSWER, 'Answered an earlier question a certain way'))
