"""Checking what staff build and what respondents send.

Staff are trusted, but a survey still has to be answerable, so a question's
shape is checked here. Respondents are not trusted: every answer is coerced to
its question's kind, and a comment is plain text, stripped of control
characters and capped, rendered only ever as text.
"""

import re
from typing import Any, List, Mapping, Optional, Sequence

from application.services.survey.conditions import ANSWER, CONDITIONS
from models import SurveyQuestion, SurveyQuestionKind

TITLE_MAX = 120
PROMPT_MAX = 280
INTRO_MAX = 2000
OPTION_MAX = 60
OPTIONS_MIN, OPTIONS_MAX = 2, 10
COMMENT_MAX = 1000

CHOICE_KINDS = (SurveyQuestionKind.CHOICE_ONE, SurveyQuestionKind.CHOICE_MANY)
YES, NO = 'Yes', 'No'

# Control characters and the bidi/zero-width marks that can disguise text,
# keeping newlines so a comment's paragraphs survive.
_CONTROL = re.compile(r'[\x00-\x09\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]')


def clean_text(value: Optional[str], *, limit: int, what: str, required: bool = True) -> str:
    text = _CONTROL.sub('', value or '').strip()
    if required and not text:
        raise ValueError(f'{what} is required.')
    if len(text) > limit:
        raise ValueError(f'{what} is limited to {limit} characters.')
    return text


def clean_options(kind: SurveyQuestionKind, options: Optional[Sequence[str]]) -> Optional[List[str]]:
    """The choices of a pick-one or pick-many question; ``None`` for any other kind."""
    if kind not in CHOICE_KINDS:
        return None
    cleaned: List[str] = []
    for option in options or []:
        text = clean_text(option, limit=OPTION_MAX, what='Each choice', required=False)
        if text and text not in cleaned:
            cleaned.append(text)
    if not OPTIONS_MIN <= len(cleaned) <= OPTIONS_MAX:
        raise ValueError(f'Give between {OPTIONS_MIN} and {OPTIONS_MAX} different choices.')
    return cleaned


def answer_values(question: SurveyQuestion) -> List[Any]:
    """Every answer ``question`` can take, for an answer-based condition's picker."""
    kind = question.kind
    if kind is SurveyQuestionKind.RATING:
        return [1, 2, 3, 4, 5]
    if kind is SurveyQuestionKind.NPS:
        return list(range(11))
    if kind is SurveyQuestionKind.YES_NO:
        return [YES, NO]
    if kind in CHOICE_KINDS:
        return list(question.options or [])
    return []


def check_condition(
    condition: Optional[str], args: Optional[Mapping[str, Any]], earlier: Mapping[int, SurveyQuestion],
) -> Optional[dict]:
    """Validate a question's condition; return the args to store.

    ``earlier`` holds the questions that come before this one in the survey:
    an answer-based question can only wait on one of those.
    """
    if not condition:
        return None
    if condition not in CONDITIONS:
        raise ValueError('Pick a condition from the list.')
    if condition != ANSWER:
        return None
    question_id = int((args or {}).get('question_id') or 0)
    target = earlier.get(question_id)
    if target is None:
        raise ValueError('Pick an earlier question for this one to follow.')
    allowed = answer_values(target)
    if not allowed:
        raise ValueError("A follow-up can't depend on a comment. Pick a question with set answers.")
    values = [v for v in (args or {}).get('values') or [] if v in allowed]
    if not values:
        raise ValueError('Pick at least one answer that shows this question.')
    return {'question_id': question_id, 'values': values}


def coerce_answer(question: SurveyQuestion, value: Any) -> tuple[Any, Optional[str]]:
    """``(value, text)`` to store for ``value``, or ``(None, None)`` for no answer.

    Raises ``ValueError`` for something the question can't take: a crafted
    request is refused, not stored.
    """
    kind = question.kind
    if value is None or value == '' or value == []:
        return None, None
    if kind is SurveyQuestionKind.COMMENT:
        text = clean_text(str(value), limit=COMMENT_MAX, what='A comment', required=False)
        return (None, text) if text else (None, None)
    if kind in (SurveyQuestionKind.RATING, SurveyQuestionKind.NPS):
        low, high = (1, 5) if kind is SurveyQuestionKind.RATING else (0, 10)
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'“{question.prompt}” takes a number.') from None
        if isinstance(value, bool) or not low <= number <= high:
            raise ValueError(f'“{question.prompt}” takes {low} to {high}.')
        return number, None
    if kind is SurveyQuestionKind.YES_NO:
        if value in (True, YES, 'yes'):
            return YES, None
        if value in (False, NO, 'no'):
            return NO, None
        raise ValueError(f'“{question.prompt}” takes Yes or No.')
    options = list(question.options or [])
    if kind is SurveyQuestionKind.CHOICE_ONE:
        if value not in options:
            raise ValueError(f'Pick one of the choices for “{question.prompt}”.')
        return value, None
    picked = value if isinstance(value, list) else [value]
    if any(p not in options for p in picked):
        raise ValueError(f'Pick from the choices for “{question.prompt}”.')
    return [o for o in options if o in picked], None
