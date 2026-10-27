"""Turning submitted answers into the numbers staff read.

Pure: given the survey's questions and every answer on a submitted response,
it counts. Two rules protect anonymous respondents:

- a repeat (one tournament, one position) with fewer than
  :data:`MIN_GROUP` answers shows its count only, never its breakdown;
- an anonymous comment is labelled with its group only when that group has at
  least :data:`MIN_GROUP` respondents, otherwise just "Anonymous".
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from models import SurveyAnswer, SurveyAudience, SurveyQuestion, SurveyQuestionKind, SurveyResponse

MIN_GROUP = 5


@dataclass
class Breakdown:
    count: int
    average: Optional[float] = None
    score: Optional[int] = None  # recommend score, -100..100
    counts: Dict[str, int] = field(default_factory=dict)
    hidden: bool = False
    # Agree scale: statement → how many picked each level, Strongly disagree first.
    statements: Dict[str, List[int]] = field(default_factory=dict)

    def agree_share(self, statement: str) -> Optional[int]:
        """Percent of a statement's answers that were Agree or Strongly agree."""
        levels = self.statements.get(statement) or []
        total = sum(levels)
        return round((levels[3] + levels[4]) * 100 / total) if total else None


@dataclass
class CommentRow:
    text: str
    who: str
    group: str


@dataclass
class QuestionResult:
    question_id: int
    section_id: int
    section_title: str
    prompt: str
    kind: SurveyQuestionKind
    overall: Breakdown
    groups: Dict[str, Breakdown] = field(default_factory=dict)
    comments: List[CommentRow] = field(default_factory=list)


def _options(question: SurveyQuestion) -> List[str]:
    kind = question.kind
    if kind is SurveyQuestionKind.RATING:
        return ['1', '2', '3', '4', '5']
    if kind is SurveyQuestionKind.NPS:
        return [str(i) for i in range(11)]
    if kind is SurveyQuestionKind.YES_NO:
        return ['Yes', 'No']
    return [str(o) for o in (question.options or [])]


def _label(value: Any, kind: SurveyQuestionKind) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if kind is SurveyQuestionKind.YES_NO:
        return ['Yes' if value in (True, 'yes', 'Yes') else 'No']
    return [str(value)]


def breakdown(question: SurveyQuestion, values: Sequence[Any], *, protect: bool = False) -> Breakdown:
    """Count ``values`` (one per answer) for ``question``."""
    n = len(values)
    if protect and n < MIN_GROUP:
        return Breakdown(count=n, hidden=True)
    if question.kind is SurveyQuestionKind.AGREE_SCALE:
        statements = {str(st): [0] * 5 for st in question.options or []}
        for value in values:
            for statement, level in (value if isinstance(value, dict) else {}).items():
                if statement in statements and isinstance(level, int) and 1 <= level <= 5:
                    statements[statement][level - 1] += 1
        return Breakdown(count=n, statements=statements)
    counts: Counter = Counter()
    for value in values:
        counts.update(_label(value, question.kind))
    ordered = {option: counts.get(option, 0) for option in _options(question)}
    for extra, c in counts.items():
        ordered.setdefault(extra, c)
    result = Breakdown(count=n, counts=ordered)
    numbers = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if question.kind in (SurveyQuestionKind.RATING, SurveyQuestionKind.NPS) and numbers:
        result.average = round(sum(numbers) / len(numbers), 1)
    if question.kind is SurveyQuestionKind.NPS and numbers:
        promoters = sum(1 for v in numbers if v >= 9)
        detractors = sum(1 for v in numbers if v <= 6)
        result.score = round((promoters - detractors) * 100 / len(numbers))
    return result


def _group_sizes(responses: Sequence[SurveyResponse]) -> Counter:
    sizes: Counter = Counter()
    for response in responses:
        for group in response.groups or []:
            sizes[group.get('label', '')] += 1
    return sizes


def _who(answer: SurveyAnswer, response: SurveyResponse, sizes: Counter) -> tuple[str, str]:
    """``(who, group)`` to show beside a comment; a small group's name is withheld for an anonymous one."""
    if not response.anonymous and response.user is not None:
        return response.user.preferred_name, answer.scope_label
    group = _group_label_for(answer)
    if group and sizes.get(group, 0) >= MIN_GROUP:
        return f'Anonymous · {group}', answer.scope_label
    return 'Anonymous', ''


def _group_label_for(answer: SurveyAnswer) -> str:
    if answer.scope.startswith('t:'):
        return f'{answer.scope_label} player'
    return answer.scope_label


def summarize(
    questions: Sequence[SurveyQuestion],
    sections: Dict[int, Any],
    answers: Sequence[SurveyAnswer],
    responses: Sequence[SurveyResponse],
) -> List[QuestionResult]:
    """One :class:`QuestionResult` per question, in survey order.

    ``sections`` maps section id → section; ``answers`` carry their response
    (prefetched), ``responses`` are every submitted response with ``user`` loaded.
    """
    by_question: Dict[int, List[SurveyAnswer]] = defaultdict(list)
    for answer in answers:
        by_question[answer.question_id].append(answer)
    response_by_id = {r.id: r for r in responses}
    sizes = _group_sizes(responses)
    results: List[QuestionResult] = []
    for question in questions:
        section = sections.get(question.section_id)
        rows = by_question.get(question.id, [])
        result = QuestionResult(
            question_id=question.id, section_id=question.section_id,
            section_title=section.title if section else '', prompt=question.prompt, kind=question.kind,
            overall=breakdown(question, [a.value for a in rows if a.value is not None]),
        )
        if question.kind is SurveyQuestionKind.COMMENT:
            result.overall = Breakdown(count=sum(1 for a in rows if (a.text or '').strip()))
        if section is not None and section.audience in (SurveyAudience.EACH_TOURNAMENT, SurveyAudience.EACH_POSITION):
            per_scope: Dict[str, List[Any]] = defaultdict(list)
            for a in rows:
                if a.value is not None:
                    per_scope[a.scope_label].append(a.value)
            result.groups = {
                label: breakdown(question, values, protect=True)
                for label, values in sorted(per_scope.items(), key=lambda kv: kv[0].lower())
            }
        if question.kind is SurveyQuestionKind.COMMENT:
            for a in sorted(rows, key=lambda a: -a.response_id):
                response = response_by_id.get(a.response_id)
                if response is None or not (a.text or '').strip():
                    continue
                who, group = _who(a, response, sizes)
                result.comments.append(CommentRow(text=a.text or '', who=who, group=group))
        results.append(result)
    return results
