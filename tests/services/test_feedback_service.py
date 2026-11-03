from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.audit_service import AuditActions
from application.services.feedback_service import FeedbackService
from models import (
    AuditLog,
    Feedback,
    FeedbackCategory,
    FeedbackStatus,
    Role,
    TenantMembership,
    User,
    UserRole,
)
from tests.factories import make_audit_double

# The repository is mocked, but the `db` fixture is still needed: it turns on the feature flags
# each entry method's guard reads.
pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def make_staff():
    async def _make(discord_id: int = 101, username: str = 'staff'):
        user = await User.create(discord_id=discord_id, username=username)
        await UserRole.create(user=user, role=Role.STAFF)
        return user
    return _make


@pytest.fixture
def make_player():
    async def _make(discord_id: int = 202, username: str = 'player'):
        return await User.create(discord_id=discord_id, username=username)
    return _make


@pytest.fixture
def service():
    """A FeedbackService with mocked repository + audit for fast unit tests."""
    svc = object.__new__(FeedbackService)
    svc.repository = MagicMock()
    svc.repository.create = AsyncMock(side_effect=lambda **kwargs: _fake_feedback(**kwargs))
    svc.audit_service = make_audit_double()
    svc._ensure_member = AsyncMock()
    return svc


def _fake_feedback(**kwargs):
    fb = MagicMock()
    fb.id = 1
    for key, value in kwargs.items():
        setattr(fb, key, value)
    return fb


class TestSubmit:
    async def test_happy_path_creates_and_audits(self, service):
        actor = MagicMock(spec=User)
        feedback = await service.submit(
            actor=actor,
            category='bug',
            message='  something is broken  ',
            page_url='/admin/users',
        )

        service.repository.create.assert_awaited_once()
        kwargs = service.repository.create.await_args.kwargs
        assert kwargs['user'] is actor
        assert kwargs['category'] == FeedbackCategory.BUG
        assert kwargs['message'] == 'something is broken'
        assert kwargs['page_url'] == '/admin/users'

        service.audit_service.write_log.assert_awaited_once()
        args = service.audit_service.write_log.await_args.args
        assert args[0] is actor
        assert args[1] == AuditActions.FEEDBACK_SUBMITTED
        assert feedback.id == 1

    async def test_empty_message_raises(self, service):
        with pytest.raises(ValueError):
            await service.submit(actor=MagicMock(), category='bug', message='   ', page_url='/')
        service.repository.create.assert_not_awaited()

    async def test_unknown_category_coerces_to_other(self, service):
        await service.submit(actor=MagicMock(), category='nonsense', message='hi', page_url='/')
        assert service.repository.create.await_args.kwargs['category'] == FeedbackCategory.OTHER

    async def test_page_url_truncated_to_column_length(self, service):
        await service.submit(
            actor=MagicMock(), category='other', message='hi', page_url='/x' * 600,
        )
        assert len(service.repository.create.await_args.kwargs['page_url']) == 512


class TestSubmitWithDb:
    async def test_persists_to_database(self, db):
        actor = await User.create(discord_id=555, username='attendee')
        await TenantMembership.create(user=actor, tenant_id=1)
        feedback = await FeedbackService().submit(
            actor=actor,
            category='praise',
            message='great event!',
            page_url='/volunteer',
        )

        stored = await Feedback.get(id=feedback.id)
        assert stored.category == FeedbackCategory.PRAISE
        assert stored.status == FeedbackStatus.NEW
        assert stored.message == 'great event!'
        assert stored.page_url == '/volunteer'

    async def test_a_non_member_is_refused(self, db):
        """The queue is the community's: the drawer only offers the dialog to
        members, and the service has to agree or the REST route is a way in."""
        outsider = await User.create(discord_id=556, username='outsider')
        with pytest.raises(PermissionError, match='members'):
            await FeedbackService().submit(
                actor=outsider, category='bug', message='hi', page_url='/help',
            )
        assert await Feedback.filter(user=outsider).count() == 0

    async def test_a_super_admin_joins_first_like_everyone(self, db):
        admin = await User.create(discord_id=557, username='platform')
        await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
        with pytest.raises(PermissionError, match='members'):
            await FeedbackService().submit(
                actor=admin, category='other', message='hello', page_url='/',
            )


class TestSetReviewed:
    """Review is reversible, and each direction audits its own action.

    It used to be one-way: a mis-click dropped a submission out of the only
    queue anyone looks at, with no way back short of the database.
    """

    async def _submission(self, submitter):
        return await Feedback.create(
            user=submitter, category=FeedbackCategory.BUG,
            message='the thing is broken', page_url='/home',
        )

    async def test_marking_reviewed_sets_the_status_and_audits(self, db, make_staff, make_player):
        staff, player = await make_staff(), await make_player()
        feedback = await self._submission(player)

        await FeedbackService().set_reviewed(staff, feedback.id)

        await feedback.refresh_from_db()
        assert feedback.status == FeedbackStatus.REVIEWED
        assert await AuditLog.filter(action=AuditActions.FEEDBACK_REVIEWED).exists()

    async def test_reopening_puts_it_back_and_audits_the_reverse(self, db, make_staff, make_player):
        staff, player = await make_staff(), await make_player()
        feedback = await self._submission(player)
        await FeedbackService().set_reviewed(staff, feedback.id)

        await FeedbackService().set_reviewed(staff, feedback.id, reviewed=False)

        await feedback.refresh_from_db()
        assert feedback.status == FeedbackStatus.NEW
        assert await AuditLog.filter(action=AuditActions.FEEDBACK_REOPENED).exists()

    async def test_a_non_admin_cannot_review_or_reopen(self, db, make_player):
        player = await make_player()
        feedback = await self._submission(player)
        with pytest.raises(PermissionError):
            await FeedbackService().set_reviewed(player, feedback.id)
        with pytest.raises(PermissionError):
            await FeedbackService().set_reviewed(player, feedback.id, reviewed=False)

    async def test_mark_reviewed_still_works_for_existing_callers(self, db, make_staff, make_player):
        staff, player = await make_staff(), await make_player()
        feedback = await self._submission(player)
        await FeedbackService().mark_reviewed(staff, feedback.id)
        await feedback.refresh_from_db()
        assert feedback.status == FeedbackStatus.REVIEWED


class TestListMine:
    """A person sees their own submissions and nobody else's."""

    async def test_returns_only_the_actors_own_newest_first(self, db, make_player):
        mine, theirs = await make_player(), await make_player(discord_id=999, username='other')
        for text in ('first', 'second'):
            await Feedback.create(
                user=mine, category=FeedbackCategory.OTHER, message=text, page_url='/',
            )
        await Feedback.create(
            user=theirs, category=FeedbackCategory.OTHER, message='not mine', page_url='/',
        )

        rows = await FeedbackService().list_mine(mine)

        assert [r.message for r in rows] == ['second', 'first']

    async def test_someone_who_sent_nothing_gets_an_empty_list(self, db, make_player):
        assert await FeedbackService().list_mine(await make_player()) == []
