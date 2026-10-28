"""The builder's Checks: named, fixable things worth a second look before sending.

A check registers itself here with :func:`register` and nowhere else; the
builder's Checks card lists every finding, then folds the checks that found
nothing into one line of their ``passed`` text. They're warnings: nothing here
blocks Send.

Checks run over the survey's sections (questions prefetched) and the paths of
the people who took part in its event (what each would see), so "nobody
reaches this part" and "someone sees 4 comment boxes" speak about real people.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from application.services.survey.plan import Part
from models import SurveyQuestionKind, SurveySection

COMMENT_LIMIT = 3
PART_LIMIT = 5
AGREE_LIMIT = 5

_SCORED = (SurveyQuestionKind.RATING, SurveyQuestionKind.NPS, SurveyQuestionKind.YES_NO)
_AND = re.compile(r'\b(and|&)\b', re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    """One thing to look at. ``link`` names the question or part; ``text`` follows it."""

    check: str
    text: str
    link: str = ''
    question_id: Optional[int] = None
    section_id: Optional[int] = None


@dataclass(frozen=True)
class Path:
    """What one person who took part would see: who they are, in a few words, and their parts."""

    who: str
    parts: Sequence[Part]


@dataclass(frozen=True)
class SurveyCheck:
    key: str
    passed: str
    run: Callable[[Sequence[SurveySection], Sequence[Path]], List[Finding]]


CHECKS: Dict[str, SurveyCheck] = {}


def register(key: str, passed: str):
    def wrap(run: Callable[[Sequence[SurveySection], Sequence[Path]], List[Finding]]):
        assert key not in CHECKS, key
        CHECKS[key] = SurveyCheck(key, passed, run)
        return run
    return wrap


def run_checks(sections: Sequence[SurveySection], paths: Sequence[Path]) -> Tuple[List[Finding], List[str]]:
    """Every finding in check order, and the ``passed`` text of the checks that found nothing."""
    findings: List[Finding] = []
    passed: List[str] = []
    for check in CHECKS.values():
        found = check.run(sections, paths)
        findings.extend(found)
        if not found:
            passed.append(check.passed)
    return findings, passed


def _questions(sections: Sequence[SurveySection]):
    for section in sections:
        for question in section.questions:
            yield section, question


@register('empty_part', 'No empty parts')
def _empty(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    return [Finding('empty_part', ' has no questions, so nobody sees it.', link=s.title, section_id=s.id)
            for s in sections if not list(s.questions)]


@register('unreachable', 'Every part reaches someone')
def _unreachable(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    if not paths:
        return []
    sections_seen = {p.section_id for path in paths for p in path.parts}
    questions_seen = {q.id for path in paths for p in path.parts for q in p.questions}
    found = []
    for section in sections:
        if not list(section.questions):
            continue
        if section.id not in sections_seen:
            found.append(Finding('unreachable', ': nobody who took part would see this part.',
                                 link=section.title, section_id=section.id))
            continue
        for question in section.questions:
            if question.id not in questions_seen:
                found.append(Finding('unreachable', ': nobody who took part matches its conditions.',
                                     link=question.prompt, question_id=question.id))
    return found


@register('two_in_one', 'No rating asks about two things')
def _two_in_one(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    return [Finding('two_in_one', ' has "and" in it. It may be two questions in one.',
                    link=q.prompt, question_id=q.id)
            for _, q in _questions(sections) if q.kind in _SCORED and _AND.search(q.prompt or '')]


@register('comment_boxes', f'Nobody sees more than {COMMENT_LIMIT} comment boxes')
def _comment_boxes(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    most, who = 0, ''
    for path in paths:
        count = sum(1 for p in path.parts for q in p.questions if q.kind is SurveyQuestionKind.COMMENT)
        if count > most:
            most, who = count, path.who
    if most <= COMMENT_LIMIT:
        return []
    return [Finding('comment_boxes', f'{who} sees {most} comment boxes. {COMMENT_LIMIT} is plenty.')]


@register('long_part', f'Every part has {PART_LIMIT} questions or fewer')
def _long_part(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    return [Finding('long_part', f' has {len(list(s.questions))} questions. Parts over {PART_LIMIT} feel long.',
                    link=s.title, section_id=s.id)
            for s in sections if len(list(s.questions)) > PART_LIMIT]


@register('long_agree', f'Agree scales have {AGREE_LIMIT} statements or fewer')
def _long_agree(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    return [Finding('long_agree', f' has {len(q.options or [])} statements. Past {AGREE_LIMIT}, people stop '
                    'reading them.', link=q.prompt, question_id=q.id)
            for _, q in _questions(sections)
            if q.kind is SurveyQuestionKind.AGREE_SCALE and len(q.options or []) > AGREE_LIMIT]


@register('required_comment', 'No required comments')
def _required_comment(sections: Sequence[SurveySection], paths: Sequence[Path]) -> List[Finding]:
    return [Finding('required_comment', ' is a required comment. People fill it with "n/a"; make it optional.',
                    link=q.prompt, question_id=q.id)
            for _, q in _questions(sections) if q.kind is SurveyQuestionKind.COMMENT and q.required]
