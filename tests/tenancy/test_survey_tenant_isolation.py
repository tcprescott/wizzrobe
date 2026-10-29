"""Cross-tenant isolation for post-event surveys.

A survey, its sections and questions, the people it went to and their answers
all belong to one community: staff in B can't read or edit A's, and a person
who answered A's can't reach B's by id.
"""

import pytest

from application.errors import NotFoundError
from application.repositories.survey_repository import SurveyRepository
from application.services import SurveyCommentService, SurveyResponseService, SurveyService
from application.tenant_context import tenant_scope
from models import (
    Role,
    Survey,
    SurveyAnswer,
    SurveyCommentMark,
    SurveyCommentTag,
    SurveyPartTally,
    SurveyQuestion,
    SurveyQuestionKind,
    SurveyRecipient,
    SurveyResponse,
    SurveyResponseStatus,
    SurveySection,
    SurveyStatus,
    UserRole,
    VolunteerPosition,
)
from tests.factories import make_user


async def _world(label: str, user):
    survey = await Survey.create(title=f'Survey {label}', status=SurveyStatus.OPEN)
    section = await SurveySection.create(survey=survey, title='The event')
    question = await SurveyQuestion.create(section=section, prompt='How was it?', kind=SurveyQuestionKind.RATING)
    recipient = await SurveyRecipient.create(survey=survey, user=user, facts={})
    response = await SurveyResponse.create(survey=survey, user=user, status=SurveyResponseStatus.SUBMITTED)
    answer = await SurveyAnswer.create(response=response, question=question, data={'v': 4}, text='Loud room')
    await SurveyPartTally.create(survey=survey, section=section, opened=1, answered=1)
    tag = await SurveyCommentTag.create(survey=survey, name='Venue')
    await SurveyCommentMark.create(survey=survey, answer=answer, tags=[tag.id])
    return survey, section, question, recipient, response, answer


async def test_survey_reads_are_isolated(two_tenants):
    a, b = two_tenants
    person = await make_user(9101, 'both')
    with tenant_scope(a.id):
        sa, *_ = await _world('A', person)
    with tenant_scope(b.id):
        sb, section_b, question_b, _, response_b, answer_b = await _world('B', person)
        tag_b = (await SurveyCommentTag.filter(survey_id=sb.id).first())

    repo = SurveyRepository()
    with tenant_scope(a.id):
        assert await repo.get_full(sb.id) is None
        assert await repo.get_section(section_b.id) is None
        assert await repo.get_question(question_b.id) is None
        assert await repo.get_recipient(sb.id, person.id) is None
        assert await repo.answers_for(response_b.id) == []
        assert await repo.submitted_responses(sb.id) == []
        assert (await repo.recipient_counts(sb.id))['sent'] == 0
        assert await repo.part_tallies(sb.id) == []
        assert await repo.progress_rows(sb.id) == []
        assert [s.id for s in await repo.list_for_event()] == [sa.id]
        assert await repo.comment_tags(sb.id) == []
        assert await repo.comment_marks(sb.id) == {}
        assert await repo.submitted_answer(answer_b.id) is None
        assert tag_b is not None and await repo.get_comment_tag(tag_b.id) is None


async def test_staff_in_one_community_cant_touch_anothers_survey(two_tenants):
    a, b = two_tenants
    person = await make_user(9102, 'player')
    staff_b = await make_user(9103, 'staff-b')
    with tenant_scope(a.id):
        sa, section_a, question_a, _, _, answer_a = await _world('A', person)
    with tenant_scope(b.id):
        await UserRole.create(user=staff_b, role=Role.STAFF)
        service = SurveyService()
        with pytest.raises(NotFoundError):
            await service.get_survey(staff_b, sa.id)
        with pytest.raises(NotFoundError):
            await service.update_question(staff_b, question_a.id, prompt='Changed')
        with pytest.raises(NotFoundError):
            await service.delete_section(staff_b, section_a.id)
        with pytest.raises(NotFoundError):
            await SurveyResponseService().open_survey(person, sa.id)
        comments = SurveyCommentService()
        with pytest.raises(NotFoundError):
            await comments.inbox(staff_b, sa.id)
        with pytest.raises(NotFoundError):
            await comments.mark(staff_b, sa.id, answer_a.id, read=True)


async def test_a_positions_condition_cant_name_another_communitys_position(two_tenants):
    a, b = two_tenants
    staff_a = await make_user(9104, 'staff-a')
    with tenant_scope(b.id):
        theirs = await VolunteerPosition.create(name='Their desk')
    with tenant_scope(a.id):
        await UserRole.create(user=staff_a, role=Role.STAFF)
        service = SurveyService()
        survey = await service.create_survey(staff_a, 'Ours', from_template=False)
        with pytest.raises(ValueError, match='volunteer schedule'):
            await service.add_section(staff_a, survey.id, 'Desk',
                                      conditions=[{'key': 'positions', 'position_ids': [theirs.id]}])
        choices = {c.key: c for c in await service.condition_choices(staff_a)}
        assert all(o['id'] != theirs.id for o in choices['positions'].options)
