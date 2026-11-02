"""Schemas for the app's survey builder: the time box, checks, AI suggestions and comment tags."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from application.services.survey.suggestions import pending_drafts
from application.services.survey.survey_service import BuilderSummary
from models import SurveySuggestionBatch


class CheckFinding(BaseModel):
    check: str = Field(..., description='Which rule found it.')
    link: str = Field('', description='The question or part it names.')
    text: str = Field(..., description='What to look at, following the link text.')
    question_id: Optional[int] = None
    section_id: Optional[int] = None


class SuggestionDraft(BaseModel):
    index: int = Field(..., description='Pass to `keep`.')
    prompt: str
    kind: str
    options: Optional[List[str]] = None


class SuggestionBatchResponse(BaseModel):
    id: int
    section_id: int
    model: str = Field('', description='The model that wrote them.')
    drafts: List[SuggestionDraft] = Field(..., description='Drafts nobody has kept or dismissed yet.')


class BuilderResponse(BaseModel):
    typical_seconds: int = Field(..., description='How long most people take: the median over everyone who took part.')
    longest_seconds: int
    longest_questions: int
    longest_who: str = Field(..., description='Who has the most to answer, in words.')
    target_seconds: int = Field(..., description='Aim for most people under this.')
    budget_seconds: int = Field(..., description='The longest path warns over this.')
    counts: Dict[str, int] = Field(..., description='How many people fall in each audience.')
    answered: int = Field(..., description='Responses sent so far (0 on a draft).')
    checks: List[CheckFinding] = Field(default_factory=list, description='Things to look at; never block Send.')
    checks_passed: List[str] = Field(default_factory=list, description='Checks that found nothing.')
    ai_provider: Optional[str] = Field(None, description='The AI provider set up for Suggest questions, or null for none.')
    suggestions: List[SuggestionBatchResponse] = Field(
        default_factory=list, description='Per section, its newest batch with drafts still waiting.',
    )


class PlaceRequest(BaseModel):
    section_id: int = Field(..., description='The section it lands in (may be another one).')
    index: int = Field(..., ge=0, description='Its place in that section, from 0.')


class TagRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)


class KeepRequest(BaseModel):
    index: int = Field(..., ge=0)


class SentContextResponse(BaseModel):
    context: str = Field(..., description='Exactly what the AI provider was sent.')


def batch_out(batch: SurveySuggestionBatch) -> SuggestionBatchResponse:
    return SuggestionBatchResponse(
        id=batch.id, section_id=batch.section_id, model=batch.model or '',
        drafts=[
            SuggestionDraft(index=i, prompt=d.get('prompt', ''), kind=d.get('kind', ''), options=d.get('options'))
            for i, d in pending_drafts(batch)
        ],
    )


def builder_out(
    summary: BuilderSummary, provider: Optional[str], batches: List[SurveySuggestionBatch],
    target: int, budget: int,
) -> BuilderResponse:
    return BuilderResponse(
        typical_seconds=summary.typical_seconds, longest_seconds=summary.longest_seconds,
        longest_questions=summary.longest_questions, longest_who=summary.longest_who,
        target_seconds=target, budget_seconds=budget, counts=summary.counts, answered=summary.answered,
        checks=[CheckFinding(**f.__dict__) for f in summary.checks], checks_passed=summary.checks_passed,
        ai_provider=provider, suggestions=[batch_out(b) for b in batches],
    )


class SendRequest(BaseModel):
    closes_at: Optional[datetime] = Field(None, description='When it stops taking answers (UTC); leave out to keep it open.')


class SendResult(BaseModel):
    people: int = Field(..., description='How many people it went to (send) or how many DMs were queued (remind).')
