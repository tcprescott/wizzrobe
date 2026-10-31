from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.crew_service import CrewService
from models import NotificationRecipient, StaffRequestKind
from tests.factories import make_audit_double

pytestmark = pytest.mark.usefixtures("bypass_auth")
@pytest.fixture(autouse=True)
def bypass_transactions(monkeypatch):
    """Replace in_transaction() with a no-op async context manager for unit tests."""
    @asynccontextmanager
    async def noop_tx(*_args, **_kwargs):
        yield None

    monkeypatch.setattr('application.services.crew_service.in_transaction', noop_tx)


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        'application.services.tenant_membership_service.TenantMembershipService.is_member',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        'application.services.tenant_membership_service.TenantMembershipService.admit',
        AsyncMock(return_value=True),
    )
    svc = object.__new__(CrewService)
    svc.commentator_repository = MagicMock()
    svc.commentator_repository.acknowledge = AsyncMock(side_effect=lambda c: c)
    svc.commentator_repository.get_by_id = AsyncMock()
    svc.tracker_repository = MagicMock()
    svc.tracker_repository.acknowledge = AsyncMock(side_effect=lambda c: c)
    svc.tracker_repository.get_by_id = AsyncMock()
    svc.broadcaster_repository = MagicMock()
    svc.broadcaster_repository.acknowledge = AsyncMock(side_effect=lambda c: c)
    svc.broadcaster_repository.get_by_id = AsyncMock()
    svc.match_repository = MagicMock()
    svc.match_repository.get_by_id = AsyncMock()
    svc.match_repository.get_players = AsyncMock(return_value=[])
    svc.audit_service = make_audit_double()
    svc.audit_service.write_and_publish = AsyncMock()
    svc.discord_service = MagicMock()
    svc.discord_service.send_dm_with_crew_acknowledgment_button = AsyncMock(
        return_value=(True, 'sent')
    )
    svc.discord_service.send_dm = AsyncMock(return_value=(True, 'sent'))
    return svc


def make_signup_match(**overrides):
    """A lightweight match stand-in for crew signup/undo tests."""
    defaults = dict(
        id=1,
        started_at=None,
        finished_at=None,
        scheduled_at=None,
        stage=None,
        title=None,
        tournament_id=None,
        commentators=[],
        trackers=[],
        broadcasters=[],
        tournament=SimpleNamespace(required_commentators=1, required_trackers=1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_signup(user_id=42, *, approved=False, approved_by_id=None):
    """A crew row as ``undo_crew_signup`` reads it: who, whether it was approved,
    and who approved it (the withdrawal DM's recipient)."""
    return SimpleNamespace(
        user_id=user_id,
        approved=approved,
        approved_by_id=approved_by_id,
        delete=AsyncMock(),
    )


def make_user(user_id=1, discord_id='12345'):
    user = MagicMock()
    user.id = user_id
    user.discord_id = discord_id
    user.preferred_name = 'Alice'
    return user


def make_match(match_id=42):
    match = MagicMock()
    match.id = match_id
    match.title = None
    match.scheduled_at = None
    match.stage = None
    match.players.all.return_value.prefetch_related = AsyncMock(return_value=[])
    return match


def make_crew(*, user, match, approved=True, acknowledged_at=None, crew_id=7):
    crew = MagicMock()
    crew.id = crew_id
    crew.user = user
    crew.user_id = user.id
    crew.match = match
    crew.match_id = match.id
    crew.approved = approved
    crew.acknowledged_at = acknowledged_at
    crew.fetch_related = AsyncMock()
    crew.refresh_from_db = AsyncMock()
    crew.save = AsyncMock()
    return crew


class TestAcknowledgeCrewAssignment:
    async def test_commentator_happy_path(self, service):
        user = make_user()
        match = make_match()
        crew = make_crew(user=user, match=match)
        service.commentator_repository.get_by_id.return_value = crew

        result = await service.acknowledge_crew_assignment(crew.id, 'commentator', user)

        assert result is crew
        service.commentator_repository.acknowledge.assert_awaited_once_with(crew)
        service.audit_service.write_log.assert_awaited_once()
        action_arg = service.audit_service.write_log.await_args.args[1]
        assert action_arg == 'crew.acknowledged'

    async def test_tracker_happy_path(self, service):
        user = make_user()
        match = make_match()
        crew = make_crew(user=user, match=match)
        service.tracker_repository.get_by_id.return_value = crew

        await service.acknowledge_crew_assignment(crew.id, 'tracker', user)

        service.tracker_repository.acknowledge.assert_awaited_once_with(crew)

    async def test_rejects_when_not_approved(self, service):
        user = make_user()
        crew = make_crew(user=user, match=make_match(), approved=False)
        service.commentator_repository.get_by_id.return_value = crew

        with pytest.raises(ValueError, match='not been approved'):
            await service.acknowledge_crew_assignment(crew.id, 'commentator', user)

        service.commentator_repository.acknowledge.assert_not_called()

    async def test_already_acknowledged_is_idempotent_noop(self, service):
        user = make_user()
        crew = make_crew(
            user=user, match=make_match(),
            approved=True, acknowledged_at=datetime.now(),
        )
        service.commentator_repository.get_by_id.return_value = crew

        result = await service.acknowledge_crew_assignment(crew.id, 'commentator', user)

        assert result is crew
        service.commentator_repository.acknowledge.assert_not_called()
        service.audit_service.write_log.assert_not_called()

    async def test_rejects_when_user_mismatch(self, service):
        other_user = make_user(user_id=99)
        crew = make_crew(user=make_user(user_id=1), match=make_match())
        service.commentator_repository.get_by_id.return_value = crew

        with pytest.raises(ValueError, match='only acknowledge your own'):
            await service.acknowledge_crew_assignment(crew.id, 'commentator', other_user)

    async def test_rejects_when_crew_missing(self, service):
        service.commentator_repository.get_by_id.return_value = None
        with pytest.raises(ValueError, match='not found'):
            await service.acknowledge_crew_assignment(123, 'commentator', make_user())


class TestUpdateCrewApproval:
    async def test_approve_triggers_dm(self, service):
        user = make_user()
        match = make_match()
        crew = make_crew(user=user, match=match, approved=False)

        await service.update_crew_approval(crew, 'commentator', approved=True, actor=user)

        assert crew.approved is True
        crew.save.assert_awaited_once()
        service.discord_service.send_dm_with_crew_acknowledgment_button.assert_called_once()
        kwargs_or_args = service.discord_service.send_dm_with_crew_acknowledgment_button.call_args.args
        assert kwargs_or_args[2] == 'commentator'
        assert kwargs_or_args[3] == crew.id

    async def test_approval_dm_links_the_crew_members_own_slot(self, service, monkeypatch):
        crew_match = AsyncMock(return_value='slot-link')
        monkeypatch.setattr(
            'application.services.notification_links.crew_match', crew_match,
        )
        crew = make_crew(user=make_user(), match=make_match(match_id=42), approved=False)

        await service.update_crew_approval(crew, 'commentator', approved=True, actor=crew.user)

        crew_match.assert_awaited_once_with(42)
        call = service.discord_service.send_dm_with_crew_acknowledgment_button.call_args
        assert call.kwargs['link'] == 'slot-link'

    async def test_unapprove_clears_acknowledgment_and_audits_previously_acknowledged(self, service):
        user = make_user()
        crew = make_crew(
            user=user, match=make_match(),
            approved=True, acknowledged_at=datetime.now(),
        )

        await service.update_crew_approval(crew, 'commentator', approved=False, actor=user)

        assert crew.approved is False
        assert crew.acknowledged_at is None
        service.discord_service.send_dm_with_crew_acknowledgment_button.assert_not_called()
        details = service.audit_service.write_log.await_args.args[2]
        assert details['approved'] is False
        assert details['previously_acknowledged'] is True

    async def test_unapprove_notifies_the_crew_member(self, service):
        user = make_user(discord_id='99887766')
        crew = make_crew(user=user, match=make_match(), approved=True)

        await service.update_crew_approval(crew, 'commentator', approved=False, actor=user)

        service.discord_service.send_dm.assert_called_once()
        args = service.discord_service.send_dm.call_args.args
        assert args[0] == 99887766
        assert 'taken off the' in args[1]

    async def test_unapprove_without_discord_id_sends_nothing(self, service):
        user = make_user(discord_id=None)
        crew = make_crew(user=user, match=make_match(), approved=True)

        await service.update_crew_approval(crew, 'commentator', approved=False, actor=user)

        assert crew.approved is False
        service.discord_service.send_dm.assert_not_called()

    async def test_idempotent_when_state_unchanged(self, service):
        user = make_user()
        crew = make_crew(user=user, match=make_match(), approved=True)

        await service.update_crew_approval(crew, 'commentator', approved=True, actor=user)

        service.discord_service.send_dm_with_crew_acknowledgment_button.assert_not_called()
        service.audit_service.write_log.assert_not_called()
        crew.save.assert_not_called()

    async def test_dm_failure_does_not_raise(self, service):
        user = make_user()
        crew = make_crew(user=user, match=make_match(), approved=False)
        service.discord_service.send_dm_with_crew_acknowledgment_button = AsyncMock(
            side_effect=RuntimeError('discord boom')
        )

        await service.update_crew_approval(crew, 'commentator', approved=True, actor=user)

        assert crew.approved is True
        service.audit_service.write_log.assert_awaited_once()


class TestSignupCrew:
    async def test_raises_for_invalid_role(self, service):
        with pytest.raises(ValueError, match="Invalid role"):
            await service.signup_crew(match_id=1, user=MagicMock(), role="referee")

    async def test_raises_when_match_not_found(self, service):
        service.match_repository.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await service.signup_crew(match_id=999, user=MagicMock(), role="commentator")

    async def test_raises_when_match_finished(self, service):
        match = make_signup_match(finished_at=datetime(2025, 1, 16, 20, 0))
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match="already finished"):
            await service.signup_crew(match_id=1, user=SimpleNamespace(id=42), role="commentator")

    async def test_raises_when_match_started(self, service):
        match = make_signup_match(started_at=datetime(2025, 1, 16, 20, 0))
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match="already started"):
            await service.signup_crew(match_id=1, user=SimpleNamespace(id=42), role="commentator")

    async def test_allows_signup_on_a_seated_match(self, service):
        match = make_signup_match(seated_at=datetime(2025, 1, 16, 20, 0))
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        service.commentator_repository.create = AsyncMock()
        await service.signup_crew(match_id=1, user=SimpleNamespace(id=42), role="commentator")
        service.commentator_repository.create.assert_awaited_once()

    async def test_raises_when_commentator_already_signed_up(self, service):
        user = SimpleNamespace(id=42)
        match = make_signup_match(commentators=[SimpleNamespace(user_id=42)])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match="already signed up"):
            await service.signup_crew(match_id=1, user=user, role="commentator")

    async def test_raises_when_tracker_already_signed_up(self, service):
        user = SimpleNamespace(id=42)
        match = make_signup_match(trackers=[SimpleNamespace(user_id=42)])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match="already signed up"):
            await service.signup_crew(match_id=1, user=user, role="tracker")

    async def test_creates_commentator_when_valid(self, service):
        user = SimpleNamespace(id=99)
        match = make_signup_match(commentators=[])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        service.commentator_repository.create = AsyncMock()
        await service.signup_crew(match_id=1, user=user, role="commentator")
        service.commentator_repository.create.assert_awaited_once_with(
            match=match, user=user, approved=False
        )

    async def test_creates_tracker_when_valid(self, service):
        user = SimpleNamespace(id=99)
        match = make_signup_match(trackers=[])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        service.tracker_repository.create = AsyncMock()
        await service.signup_crew(match_id=1, user=user, role="tracker")
        service.tracker_repository.create.assert_awaited_once_with(
            match=match, user=user, approved=False
        )

    async def test_invalid_role_check_precedes_db_lookup(self, service):
        service.match_repository.get_by_id = AsyncMock()
        with pytest.raises(ValueError, match="Invalid role"):
            await service.signup_crew(match_id=1, user=MagicMock(), role="judge")
        service.match_repository.get_by_id.assert_not_awaited()

    async def test_raises_when_user_is_a_player(self, service):
        user = SimpleNamespace(id=42)
        match = make_signup_match()
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        service.match_repository.get_players = AsyncMock(return_value=[SimpleNamespace(user_id=42)])
        with pytest.raises(ValueError, match="crew a match you're playing in"):
            await service.signup_crew(match_id=1, user=user, role="commentator")

    async def test_refuses_a_role_the_tournament_does_not_use(self, service):
        match = make_signup_match(
            tournament=SimpleNamespace(required_commentators=2, required_trackers=0),
        )
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        service.tracker_repository.create = AsyncMock()
        with pytest.raises(ValueError, match="does not use trackers"):
            await service.signup_crew(match_id=1, user=SimpleNamespace(id=99), role="tracker")
        service.tracker_repository.create.assert_not_awaited()

    async def test_allows_the_role_the_same_tournament_does_use(self, service):
        match = make_signup_match(
            tournament=SimpleNamespace(required_commentators=2, required_trackers=0),
        )
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        service.commentator_repository.create = AsyncMock()
        await service.signup_crew(match_id=1, user=SimpleNamespace(id=99), role="commentator")
        service.commentator_repository.create.assert_awaited_once()

    async def test_withdrawing_stays_open_for_an_unused_role(self, service):
        user = SimpleNamespace(id=42)
        signup = make_signup()
        match = make_signup_match(
            trackers=[signup],
            tournament=SimpleNamespace(required_commentators=1, required_trackers=0),
        )
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        await service.undo_crew_signup(match_id=1, user=user, role="tracker")
        signup.delete.assert_awaited_once()


class TestSignupNotifiesCrewOwners:
    """A new signup tells the people who can approve it.

    Approval was the one wait in the volunteer's journey nothing announced.
    """

    @pytest.fixture
    def notifying(self, service, monkeypatch):
        owner = make_user(user_id=7, discord_id='555')
        owner.preferred_name = 'Coordinator'
        monkeypatch.setattr(
            'application.repositories.TournamentRepository.get_crew_owners',
            AsyncMock(return_value=[owner]),
        )
        monkeypatch.setattr(
            'application.services.tenant_service.TenantService.current_community_name',
            AsyncMock(return_value='Test Community'),
        )
        admin_match = AsyncMock(return_value='board-link')
        monkeypatch.setattr(
            'application.services.notification_links.admin_match', admin_match,
        )
        service.commentator_repository.create = AsyncMock()
        send = AsyncMock()
        monkeypatch.setattr('application.services.staff_request_dms.send', send)
        service.staff_dms = send
        return service, owner, admin_match

    @staticmethod
    def _match(**overrides):
        match = make_signup_match(tournament_id=3, **overrides)
        match.players = MagicMock()
        match.players.all.return_value.prefetch_related = AsyncMock(return_value=[])
        return match

    async def test_signup_dms_the_crew_owners_with_the_match_link(self, notifying):
        service, owner, admin_match = notifying
        service.match_repository.get_by_id = AsyncMock(return_value=self._match())
        volunteer = make_user(user_id=42, discord_id='111')

        await service.signup_crew(match_id=1, user=volunteer, role='commentator')

        service.staff_dms.assert_awaited_once()
        call = service.staff_dms.await_args
        kind, _crew_id, recipients, message = call.args
        assert kind is StaffRequestKind.COMMENTATOR_SIGNUP
        assert recipients == [(owner, NotificationRecipient.ADMINS)]
        assert 'Alice' in message and 'waiting for approval' in message
        assert call.kwargs['link'] == 'board-link'
        assert admin_match.await_args.args == (1,)

    async def test_an_owner_signing_up_is_not_told_about_themselves(self, notifying):
        service, owner, _ = notifying
        service.match_repository.get_by_id = AsyncMock(return_value=self._match())

        await service.signup_crew(match_id=1, user=owner, role='commentator')

        service.staff_dms.assert_not_awaited()

    async def test_owner_with_discord_off_still_reaches_send_dm(self, notifying):
        service, owner, _ = notifying
        owner.dm_notifications = False
        service.match_repository.get_by_id = AsyncMock(return_value=self._match())

        await service.signup_crew(match_id=1, user=make_user(user_id=42), role='commentator')

        # Discord off only drops the Discord copy, inside send_dm; devices still need the call.
        service.staff_dms.assert_awaited_once()

    async def test_a_failing_notice_never_blocks_the_signup(self, notifying, monkeypatch):
        service, _, _ = notifying
        monkeypatch.setattr(
            'application.repositories.TournamentRepository.get_crew_owners',
            AsyncMock(side_effect=RuntimeError('db down')),
        )
        service.match_repository.get_by_id = AsyncMock(return_value=self._match())

        await service.signup_crew(match_id=1, user=make_user(user_id=42), role='commentator')

        service.commentator_repository.create.assert_awaited_once()


class TestUndoCrewSignup:
    async def test_raises_for_invalid_role(self, service):
        with pytest.raises(ValueError, match="Invalid role"):
            await service.undo_crew_signup(match_id=1, user=MagicMock(), role="referee")

    async def test_raises_when_match_not_found(self, service):
        service.match_repository.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await service.undo_crew_signup(match_id=999, user=MagicMock(), role="commentator")

    async def test_raises_when_commentator_not_signed_up(self, service):
        user = SimpleNamespace(id=42)
        match = make_signup_match(commentators=[])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match="not signed up"):
            await service.undo_crew_signup(match_id=1, user=user, role="commentator")

    async def test_raises_when_tracker_not_signed_up(self, service):
        user = SimpleNamespace(id=42)
        match = make_signup_match(trackers=[])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match="not signed up"):
            await service.undo_crew_signup(match_id=1, user=user, role="tracker")

    async def test_deletes_commentator_when_found(self, service):
        user = SimpleNamespace(id=42)
        crew_member = make_signup()
        match = make_signup_match(commentators=[crew_member])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        await service.undo_crew_signup(match_id=1, user=user, role="commentator")
        crew_member.delete.assert_awaited_once()

    async def test_deletes_tracker_when_found(self, service):
        user = SimpleNamespace(id=42)
        crew_member = make_signup()
        match = make_signup_match(trackers=[crew_member])
        service.match_repository.get_by_id = AsyncMock(return_value=match)
        await service.undo_crew_signup(match_id=1, user=user, role="tracker")
        crew_member.delete.assert_awaited_once()

    async def test_invalid_role_check_precedes_db_lookup(self, service):
        service.match_repository.get_by_id = AsyncMock()
        with pytest.raises(ValueError, match="Invalid role"):
            await service.undo_crew_signup(match_id=1, user=MagicMock(), role="judge")
        service.match_repository.get_by_id.assert_not_awaited()


class TestWithdrawalNotifiesCrewOwners:
    """Dropping an *approved* commitment tells the people who can refill it.

    The half of the audit's one-directional-notification finding that the
    volunteer side already fixed: approval DMs the crew member, so the reverse
    has to reach someone too.
    """

    @pytest.fixture
    def notifying(self, service, monkeypatch):
        owner = make_user(user_id=7, discord_id='555')
        owner.preferred_name = 'Coordinator'
        monkeypatch.setattr(
            'application.repositories.TournamentRepository.get_crew_owners',
            AsyncMock(return_value=[owner]),
        )
        monkeypatch.setattr(
            'application.services.tenant_service.TenantService.current_community_name',
            AsyncMock(return_value='Test Community'),
        )
        return service, owner

    @staticmethod
    def _match_with_players(**overrides):
        match = make_signup_match(tournament_id=3, **overrides)
        match.players = MagicMock()
        match.players.all.return_value.prefetch_related = AsyncMock(return_value=[])
        return match

    async def test_approved_withdrawal_dms_the_crew_owners(self, notifying):
        service, owner = notifying
        user = make_user(user_id=42, discord_id='111')
        match = self._match_with_players(commentators=[make_signup(approved=True)])
        service.match_repository.get_by_id = AsyncMock(return_value=match)

        await service.undo_crew_signup(match_id=1, user=user, role="commentator")

        service.discord_service.send_dm.assert_called_once()
        recipient_id, message = service.discord_service.send_dm.call_args.args[:2]
        assert recipient_id == int(owner.discord_id)
        assert 'Alice' in message and 'withdrawn as commentator' in message

    async def test_unapproved_withdrawal_is_silent(self, notifying):
        service, _ = notifying
        user = make_user(user_id=42, discord_id='111')
        match = self._match_with_players(commentators=[make_signup(approved=False)])
        service.match_repository.get_by_id = AsyncMock(return_value=match)

        await service.undo_crew_signup(match_id=1, user=user, role="commentator")

        service.discord_service.send_dm.assert_not_called()

    async def test_the_withdrawer_is_not_told_about_their_own_withdrawal(self, notifying):
        service, owner = notifying
        match = self._match_with_players(commentators=[make_signup(user_id=owner.id, approved=True)])
        service.match_repository.get_by_id = AsyncMock(return_value=match)

        await service.undo_crew_signup(match_id=1, user=owner, role="commentator")

        service.discord_service.send_dm.assert_not_called()

    async def test_owner_with_discord_off_still_reaches_send_dm(self, notifying):
        service, owner = notifying
        owner.dm_notifications = False
        user = make_user(user_id=42, discord_id='111')
        match = self._match_with_players(trackers=[make_signup(approved=True)])
        service.match_repository.get_by_id = AsyncMock(return_value=match)

        await service.undo_crew_signup(match_id=1, user=user, role="tracker")

        # Discord off only drops the Discord copy, inside send_dm; devices still need the call.
        service.discord_service.send_dm.assert_called_once()


def _conflict_match(match_id, when, minutes=90, players=('Alice', 'Bob')):
    return SimpleNamespace(
        id=match_id,
        scheduled_at=when,
        tournament=SimpleNamespace(average_match_duration=minutes),
        players=[SimpleNamespace(user=SimpleNamespace(preferred_name=n)) for n in players],
        fetch_related=AsyncMock(),
    )


@pytest.fixture
def conflict_service(service):
    service.match_repository.commitments_for_user = AsyncMock(return_value=[])
    return service


async def test_a_match_with_no_time_cannot_clash(conflict_service):
    subject = _conflict_match(1, None)
    assert await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject) == []


async def test_a_clear_diary_produces_no_warning(conflict_service):
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0))
    assert await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject) == []


async def test_an_overlapping_commitment_is_named_with_its_players_and_time(
    conflict_service,
):
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0))
    conflict_service.match_repository.commitments_for_user = AsyncMock(return_value=[
        _conflict_match(2, datetime(2026, 8, 1, 19, 30), players=('Cy', 'Dee')),
    ])
    clashes = await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject)
    assert len(clashes) == 1
    assert 'Cy vs Dee' in clashes[0]


async def test_a_match_that_ends_before_this_one_starts_is_not_a_clash(
    conflict_service,
):
    """The query widens the window both ways, so the service has to do the
    actual overlap arithmetic — otherwise every neighbouring match warns."""
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0))
    conflict_service.match_repository.commitments_for_user = AsyncMock(return_value=[
        _conflict_match(2, datetime(2026, 8, 1, 17, 0), minutes=60),
    ])
    assert await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject) == []


async def test_a_match_starting_earlier_and_still_running_is_a_clash(
    conflict_service,
):
    """The overlap that matters most, and the one a forward-only window misses."""
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0))
    conflict_service.match_repository.commitments_for_user = AsyncMock(return_value=[
        _conflict_match(2, datetime(2026, 8, 1, 18, 30), minutes=120),
    ])
    assert len(await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject)) == 1


async def test_a_match_starting_after_this_one_ends_is_not_a_clash(conflict_service):
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0), minutes=60)
    conflict_service.match_repository.commitments_for_user = AsyncMock(return_value=[
        _conflict_match(2, datetime(2026, 8, 1, 22, 0)),
    ])
    assert await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject) == []


async def test_the_match_being_approved_is_excluded_from_its_own_conflicts(
    conflict_service,
):
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0))
    await conflict_service.find_scheduling_conflicts(SimpleNamespace(id=7), subject)
    _args, kwargs = conflict_service.match_repository.commitments_for_user.call_args
    assert kwargs['exclude_match_id'] == 1


async def test_a_tournament_without_a_duration_falls_back_to_the_default(
    conflict_service,
):
    subject = _conflict_match(1, datetime(2026, 8, 1, 19, 0), minutes=None)
    conflict_service.match_repository.commitments_for_user = AsyncMock(return_value=[
        _conflict_match(2, datetime(2026, 8, 1, 19, 30), minutes=None),
    ])
    assert len(await conflict_service.find_scheduling_conflicts(
        SimpleNamespace(id=7), subject)) == 1
