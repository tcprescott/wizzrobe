"""The comments inbox: every typed answer to a survey, for staff to work through.

Thomas picked "A + B comments inbox" (2026-10-28): every comment and "Other"
write-in in one list, filtered by question, read marks and tags shared by all
staff. Respondents never see tags or read marks. Who wrote a comment follows
the results' rule: an anonymous one carries its group only when that group has
5 or more people.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories.survey_repository import SurveyRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.survey._validation import clean_text
from application.services.survey.results import group_sizes, who_for
from models import (
    FeatureFlag,
    Survey,
    SurveyAnswer,
    SurveyCommentMark,
    SurveyQuestionKind,
    User,
)

STARTER_TAGS = ('Wizzrobe', 'Venue', 'Schedule', 'Staff', 'Praise')
TAG_NAME_MAX = 40
MAX_TAGS = 20
WRITE_INS = 'other'


@dataclass
class InboxComment:
    answer_id: int
    question_id: int
    prompt: str
    section_title: str
    scope_label: str
    who: str
    group: str
    text: str
    write_in: bool = False
    read_at: Optional[datetime] = None
    read_by: str = ''
    tags: List[int] = field(default_factory=list)

    @property
    def read(self) -> bool:
        return self.read_at is not None

    @property
    def filter_key(self) -> str:
        return WRITE_INS if self.write_in else str(self.question_id)


@dataclass
class InboxQuestion:
    key: str
    prompt: str
    count: int


@dataclass
class InboxTag:
    id: int
    name: str
    count: int


@dataclass
class CommentInbox:
    survey: Survey
    comments: List[InboxComment]
    questions: List[InboxQuestion]
    tags: List[InboxTag]

    @property
    def unread(self) -> int:
        return sum(1 for c in self.comments if not c.read)


def _comment(answer: SurveyAnswer, mark: Optional[SurveyCommentMark], who: str, group: str,
             prompt: str, section_title: str, write_in: bool, tag_ids: set) -> InboxComment:
    return InboxComment(
        answer_id=answer.id, question_id=answer.question_id, prompt=prompt, section_title=section_title,
        scope_label=answer.scope_label, who=who, group=group, text=answer.text or '', write_in=write_in,
        read_at=mark.read_at if mark else None,
        read_by=mark.read_by.preferred_name if mark and mark.read_at and mark.read_by else '',
        tags=[t for t in (mark.tags if mark else []) if t in tag_ids],
    )


class SurveyCommentService:
    """Staff reading, tagging and marking a survey's comments."""

    def __init__(self) -> None:
        self.repository = SurveyRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only Staff can read survey comments.')

    async def _survey(self, survey_id: int) -> Survey:
        return require_found(await self.repository.get_full(survey_id), 'Survey')

    async def _tags(self, survey: Survey):
        tags = await self.repository.comment_tags(survey.id)
        if not tags:
            for position, name in enumerate(STARTER_TAGS):
                await self.repository.create_comment_tag(survey, name, position)
            tags = await self.repository.comment_tags(survey.id)
        return tags

    @requires_feature(FeatureFlag.SURVEYS)
    async def inbox(self, actor: User, survey_id: int) -> CommentInbox:
        """Every comment and "Other" write-in, newest first, with its read state and tags."""
        await self._ensure_staff(actor)
        survey = await self._survey(survey_id)
        tags = await self._tags(survey)
        tag_ids = {t.id for t in tags}
        responses = {r.id: r for r in await self.repository.submitted_responses(survey.id)}
        sizes = group_sizes(list(responses.values()))
        marks = await self.repository.comment_marks(survey.id)
        questions = {q.id: (q, s) for s in survey.sections for q in s.questions}
        order = list(questions)
        rows = sorted(
            (a for a in await self.repository.submitted_answers(survey.id)
             if (a.text or '').strip() and a.question_id in questions and a.response_id in responses),
            key=lambda a: (-a.response_id, order.index(a.question_id)),
        )
        comments = []
        for answer in rows:
            question, section = questions[answer.question_id]
            who, group = who_for(answer, responses[answer.response_id], sizes)
            comments.append(_comment(answer, marks.get(answer.id), who, group, question.prompt, section.title,
                                     question.kind is not SurveyQuestionKind.COMMENT, tag_ids))
        counts: Dict[str, int] = {}
        for c in comments:
            counts[c.filter_key] = counts.get(c.filter_key, 0) + 1
        listed = [InboxQuestion(str(qid), questions[qid][0].prompt, counts[str(qid)])
                  for qid in order if str(qid) in counts]
        if WRITE_INS in counts:
            listed.append(InboxQuestion(WRITE_INS, '“Other” write-ins', counts[WRITE_INS]))
        tag_counts = {t.id: 0 for t in tags}
        for c in comments:
            for t in c.tags:
                tag_counts[t] += 1
        return CommentInbox(survey=survey, comments=comments, questions=listed,
                            tags=[InboxTag(t.id, t.name, tag_counts[t.id]) for t in tags])

    @requires_feature(FeatureFlag.SURVEYS)
    async def mark(self, actor: User, survey_id: int, answer_id: int, *, read: Optional[bool] = None,
                   tags: Optional[Sequence[int]] = None) -> SurveyCommentMark:
        """Mark one comment read or unread, and/or set its tags. Shared by every staff member."""
        await self._ensure_staff(actor)
        survey = await self._survey(survey_id)
        answer = await self.repository.submitted_answer(answer_id)
        if answer is None or answer.response.survey_id != survey.id or not (answer.text or '').strip():
            raise ValueError('That comment isn’t on this survey.')
        fields: Dict[str, object] = {}
        if read is not None:
            fields.update(read_at=datetime.now(timezone.utc) if read else None, read_by_id=actor.id if read else None)
        if tags is not None:
            known = {t.id for t in await self._tags(survey)}
            unknown = [t for t in tags if t not in known]
            if unknown:
                raise ValueError('That tag isn’t on this survey.')
            fields['tags'] = sorted(set(tags))
        return await self.repository.save_comment_mark(survey.id, answer.id, **fields)

    @requires_feature(FeatureFlag.SURVEYS)
    async def add_tag(self, actor: User, survey_id: int, name: str) -> InboxTag:
        await self._ensure_staff(actor)
        survey = await self._survey(survey_id)
        name = clean_text(name, limit=TAG_NAME_MAX, what='A tag')
        tags = await self._tags(survey)
        if any(t.name.lower() == name.lower() for t in tags):
            raise ValueError(f'There’s already a “{name}” tag.')
        if len(tags) >= MAX_TAGS:
            raise ValueError(f'A survey can have up to {MAX_TAGS} tags.')
        tag = await self.repository.create_comment_tag(survey, name, max(t.position for t in tags) + 1)
        await self.audit_service.write_log(actor, AuditActions.SURVEY_COMMENT_TAG_CREATED,
                                           {'survey_id': survey.id, 'tag': name})
        return InboxTag(tag.id, tag.name, 0)

    @requires_feature(FeatureFlag.SURVEYS)
    async def delete_tag(self, actor: User, survey_id: int, tag_id: int) -> None:
        """Delete a tag; comments that carried it just lose it."""
        await self._ensure_staff(actor)
        survey = await self._survey(survey_id)
        tag = await self.repository.get_comment_tag(tag_id)
        if tag is None or tag.survey_id != survey.id:
            raise ValueError('That tag isn’t on this survey.')
        await self.repository.delete_comment_tag(tag)
        await self.audit_service.write_log(actor, AuditActions.SURVEY_COMMENT_TAG_DELETED,
                                           {'survey_id': survey.id, 'tag': tag.name})
