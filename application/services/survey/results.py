"""Turning submitted answers into the numbers staff read.

Pure: given the survey's questions and every answer on a submitted response,
it counts. Two rules protect anonymous respondents:

- a repeat (one tournament, one position) with fewer than
  :data:`MIN_GROUP` answers is folded into one "Other" group, and the
  smallest shown groups join it until it has :data:`MIN_GROUP` too, so the
  overall minus the shown groups never singles anyone out;
- an anonymous comment is labelled with its group only when that group has at
  least :data:`MIN_GROUP` respondents, otherwise just "Anonymous".
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from application.services.survey._validation import answer_values
from application.services.survey.conditions import describe
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

    def share(self, *options: str) -> Optional[int]:
        """Percent of the counted answers that were one of ``options``."""
        total = sum(self.counts.values())
        return round(sum(self.counts.get(o, 0) for o in options) * 100 / total) if total else None

    @property
    def top_share(self) -> Optional[int]:
        """A rating's "% rated 4–5"."""
        return self.share('4', '5')

    @property
    def nps_split(self) -> tuple[int, int, int]:
        """Percent of detractors (0–6), passives (7–8) and promoters (9–10)."""
        total = sum(self.counts.values()) or 1
        low = sum(self.counts.get(str(i), 0) for i in range(7))
        mid = self.counts.get('7', 0) + self.counts.get('8', 0)
        return round(low * 100 / total), round(mid * 100 / total), round((total - low - mid) * 100 / total)


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
    #: A comment's answers, or a choice question's "Other" write-ins.
    comments: List[CommentRow] = field(default_factory=list)
    #: A pick-many's exclusive "none" choice, drawn apart from the real ones.
    none_option: Optional[str] = None
    #: Set when the prompt was reworded after answers came in: the first wording and how many saw it.
    original_prompt: Optional[str] = None
    reworded_at: Optional[datetime] = None
    reworded_after: Optional[int] = None
    #: Who the question's section is for.
    audience: SurveyAudience = SurveyAudience.EVERYONE
    #: Asked once per tournament or position, so ``groups`` compares them.
    repeats: bool = False
    #: "Only if …", for a question that only some people were asked.
    condition: str = ''
    #: Groups folded into ``OTHER``, by name.
    folded: List[str] = field(default_factory=list)
    #: Comments per group (scope label), for a repeating comment question.
    comment_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def score(self) -> Optional[int]:
        """The one number a comparison lines groups up on, or ``None`` when there isn't one."""
        return score_of(self.kind, self.overall)


OTHER = 'Other'


def score_of(kind: SurveyQuestionKind, result: Breakdown) -> Optional[int]:
    """A 0–100 (recommend score: -100–100) number that goes up when things went well."""
    if result.hidden or not result.count:
        return None
    if kind is SurveyQuestionKind.RATING:
        return result.top_share
    if kind is SurveyQuestionKind.NPS:
        return result.score
    if kind is SurveyQuestionKind.YES_NO:
        return result.share('Yes')
    if kind is SurveyQuestionKind.CHOICE_ONE and next(iter(result.counts), '') == 'Yes':
        return result.share('Yes')
    return None


def fold_small(question: SurveyQuestion, per_scope: Dict[str, List[Any]]) -> tuple[Dict[str, Breakdown], List[str]]:
    """Per-group breakdowns, with every group under :data:`MIN_GROUP` folded into one ``OTHER``.

    When the folded ones still come to fewer than :data:`MIN_GROUP`, the smallest
    shown groups join them, since otherwise the overall minus the shown groups
    would give the small groups' answers away.
    """
    ordered = sorted(per_scope.items(), key=lambda kv: kv[0].lower())
    small = [(label, values) for label, values in ordered if len(values) < MIN_GROUP]
    big = [(label, values) for label, values in ordered if len(values) >= MIN_GROUP]
    if small:
        big.sort(key=lambda kv: len(kv[1]))
        while big and sum(len(v) for _, v in small) < MIN_GROUP:
            small.append(big.pop(0))
    shown = {label for label, _ in big}
    groups = {label: breakdown(question, values) for label, values in ordered if label in shown}
    folded = [label for label, _ in ordered if label not in shown]
    if folded:
        values = [v for _, vs in small for v in vs]
        groups[OTHER] = breakdown(question, values, protect=True)
    return groups, folded


def _options(question: SurveyQuestion) -> List[str]:
    kind = question.kind
    if kind is SurveyQuestionKind.RATING:
        return ['1', '2', '3', '4', '5']
    if kind is SurveyQuestionKind.NPS:
        return [str(i) for i in range(11)]
    if kind is SurveyQuestionKind.YES_NO:
        return ['Yes', 'No']
    return [str(o) for o in answer_values(question)]


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
    *,
    protect: bool = False,
) -> List[QuestionResult]:
    """One :class:`QuestionResult` per question, in survey order.

    ``sections`` maps section id → section; ``answers`` carry their response
    (prefetched), ``responses`` are every submitted response with ``user`` loaded.
    ``protect`` hides an overall breakdown under :data:`MIN_GROUP`, for a report
    already narrowed to one group.
    """
    by_question: Dict[int, List[SurveyAnswer]] = defaultdict(list)
    for answer in answers:
        by_question[answer.question_id].append(answer)
    response_by_id = {r.id: r for r in responses}
    sizes = _group_sizes(responses)
    prompts = {q.id: q.prompt for q in questions}
    results: List[QuestionResult] = []
    for question in questions:
        section = sections.get(question.section_id)
        rows = by_question.get(question.id, [])
        result = QuestionResult(
            question_id=question.id, section_id=question.section_id,
            section_title=section.title if section else '', prompt=question.prompt, kind=question.kind,
            none_option=question.none_option, original_prompt=question.original_prompt,
            reworded_at=question.reworded_at, reworded_after=question.reworded_after,
            overall=breakdown(question, [a.value for a in rows if a.value is not None], protect=protect),
            condition=describe((question.conditions or [None])[0], prompts),
            audience=section.audience if section else SurveyAudience.EVERYONE,
        )
        if question.kind is SurveyQuestionKind.COMMENT:
            result.overall = Breakdown(count=sum(1 for a in rows if (a.text or '').strip()))
        if section is not None and section.audience in (SurveyAudience.EACH_TOURNAMENT, SurveyAudience.EACH_POSITION):
            per_scope: Dict[str, List[Any]] = defaultdict(list)
            for a in rows:
                if a.value is not None:
                    per_scope[a.scope_label].append(a.value)
            result.repeats = True
            result.groups, result.folded = fold_small(question, per_scope)
            if question.kind is SurveyQuestionKind.COMMENT:
                result.comment_counts = dict(Counter(a.scope_label for a in rows if (a.text or '').strip()))
        # A comment's text, or the "Other" write-ins of a choice question.
        if question.kind is SurveyQuestionKind.COMMENT or question.other_option:
            for a in sorted(rows, key=lambda a: -a.response_id):
                response = response_by_id.get(a.response_id)
                if response is None or not (a.text or '').strip():
                    continue
                who, group = _who(a, response, sizes)
                result.comments.append(CommentRow(text=a.text or '', who=who, group=group))
        results.append(result)
    return results
