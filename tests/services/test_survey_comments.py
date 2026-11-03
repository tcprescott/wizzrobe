"""The comments inbox: shared read marks, staff-only tags, write-ins."""

import pytest

from application.services import SurveyCommentService, SurveyResponseService
from application.services.survey.comments import STARTER_TAGS, WRITE_INS
from models import AuditLog, Role, UserRole
from tests.factories import make_member
from tests.services.test_survey_service import _answer_everything, _sent


async def _answered(world):
    survey = await _sent(world)
    for user in (world['alice'], world['bob']):
        await _answer_everything(user, survey.id, 2)
        await SurveyResponseService().submit(user, survey.id, anonymous=user is world['bob'])
    return survey


async def test_inbox_lists_every_comment_with_starter_tags(event_world):
    survey = await _answered(event_world)
    inbox = await SurveyCommentService().inbox(event_world['staff'], survey.id)
    assert [t.name for t in inbox.tags] == list(STARTER_TAGS)
    assert inbox.comments and inbox.unread == len(inbox.comments)
    assert {c.who for c in inbox.comments} >= {'Anonymous', event_world['alice'].preferred_name}
    assert sum(q.count for q in inbox.questions) == len(inbox.comments)
    assert all(c.text == 'Great <b>event</b>' for c in inbox.comments if not c.write_in)
    again = await SurveyCommentService().inbox(event_world['staff'], survey.id)
    assert len(again.tags) == len(STARTER_TAGS)


async def test_read_marks_and_tags_are_shared_by_staff(event_world):
    survey = await _answered(event_world)
    service = SurveyCommentService()
    staff = event_world['staff']
    other_staff = await make_member(101, 'other-staff')
    await UserRole.create(user=other_staff, role=Role.STAFF)
    inbox = await service.inbox(staff, survey.id)
    first = inbox.comments[0]
    venue = next(t for t in inbox.tags if t.name == 'Venue')
    await service.mark(staff, survey.id, first.answer_id, read=True, tags=[venue.id])
    seen = await service.inbox(other_staff, survey.id)
    comment = next(c for c in seen.comments if c.answer_id == first.answer_id)
    assert comment.read and comment.read_by == staff.preferred_name and comment.tags == [venue.id]
    assert next(t for t in seen.tags if t.id == venue.id).count == 1
    assert seen.unread == len(seen.comments) - 1
    await service.mark(other_staff, survey.id, first.answer_id, read=False)
    comment = next(c for c in (await service.inbox(staff, survey.id)).comments if c.answer_id == first.answer_id)
    assert not comment.read and comment.tags == [venue.id]


async def test_only_staff_reach_the_inbox(event_world):
    survey = await _answered(event_world)
    service = SurveyCommentService()
    inbox = await service.inbox(event_world['staff'], survey.id)
    with pytest.raises(PermissionError):
        await service.inbox(event_world['bob'], survey.id)
    with pytest.raises(PermissionError):
        await service.mark(event_world['alice'], survey.id, inbox.comments[0].answer_id, read=True)
    with pytest.raises(PermissionError):
        await service.add_tag(event_world['vic'], survey.id, 'Mine')


async def test_marks_refuse_other_surveys_and_unknown_tags(event_world):
    survey = await _answered(event_world)
    service = SurveyCommentService()
    staff = event_world['staff']
    inbox = await service.inbox(staff, survey.id)
    other = await _sent(event_world)
    with pytest.raises(ValueError, match='isn’t on this survey'):
        await service.mark(staff, other.id, inbox.comments[0].answer_id, read=True)
    other_tags = (await service.inbox(staff, other.id)).tags
    with pytest.raises(ValueError, match='tag isn’t on this survey'):
        await service.mark(staff, survey.id, inbox.comments[0].answer_id, tags=[other_tags[0].id])


async def test_staff_add_and_delete_tags(event_world):
    survey = await _answered(event_world)
    service = SurveyCommentService()
    staff = event_world['staff']
    tag = await service.add_tag(staff, survey.id, '  Food  ')
    assert tag.name == 'Food'
    with pytest.raises(ValueError, match='already'):
        await service.add_tag(staff, survey.id, 'food')
    inbox = await service.inbox(staff, survey.id)
    await service.mark(staff, survey.id, inbox.comments[0].answer_id, tags=[tag.id])
    await service.delete_tag(staff, survey.id, tag.id)
    inbox = await service.inbox(staff, survey.id)
    assert 'Food' not in [t.name for t in inbox.tags]
    assert all(tag.id not in c.tags for c in inbox.comments)
    actions = set(await AuditLog.all().values_list('action', flat=True))
    assert {'survey.comment_tag_created', 'survey.comment_tag_deleted'} <= actions


async def test_write_ins_have_their_own_filter(event_world):
    survey = await _answered(event_world)
    inbox = await SurveyCommentService().inbox(event_world['staff'], survey.id)
    keys = [q.key for q in inbox.questions]
    if any(c.write_in for c in inbox.comments):
        assert keys[-1] == WRITE_INS
