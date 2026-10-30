"""Unit tests for TournamentService.

Permission gating, field stripping, audit logging, and role management
(admins / crew_coordinators) are exercised here. The repository and the
m2m manager methods are mocked.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.tournament_service import TournamentService
from models import TournamentGrant
from tests.factories import make_audit_double

pytestmark = pytest.mark.usefixtures("bypass_auth")
@pytest.fixture
def service():
    svc = object.__new__(TournamentService)
    svc.repository = MagicMock()
    svc.repository.create = AsyncMock()
    svc.repository.update = AsyncMock(side_effect=lambda t, **f: t)
    svc.repository.get_all = AsyncMock(return_value=[])
    svc.repository.get_by_id = AsyncMock()
    svc.repository.set_tournament_grant = AsyncMock()
    svc.repository.dependent_counts = AsyncMock(return_value={
        'matches': 2, 'players': 3, 'brackets': 0, 'triforce_texts': 0,
    })
    svc.preset_repository = MagicMock()
    svc.preset_repository.get_by_id = AsyncMock()
    svc.discord_grant_repository = MagicMock()
    svc.discord_grant_repository.remove = AsyncMock()
    svc.audit_service = make_audit_double()
    return svc


def make_user(user_id=1):
    return SimpleNamespace(id=user_id, username='alice')


def make_tournament(tournament_id=1, **overrides):
    admins = MagicMock()
    admins.add = AsyncMock()
    admins.remove = AsyncMock()
    coordinators = MagicMock()
    coordinators.add = AsyncMock()
    coordinators.remove = AsyncMock()

    defaults = dict(
        id=tournament_id,
        name='Tournament',
        is_active=True,
        preset_id=None,
        hard_preset_id=None,
        admins=admins,
        crew_coordinators=coordinators,
        delete=AsyncMock(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCreateTournament:
    async def test_happy_path(self, service):
        created = make_tournament(tournament_id=10, name='New Cup')
        service.repository.create = AsyncMock(return_value=created)

        result = await service.create_tournament(
            name=' New Cup ', description=' desc ', actor=make_user(),
        )

        assert result is created
        kwargs = service.repository.create.await_args.kwargs
        assert kwargs['name'] == 'New Cup'
        assert kwargs['description'] == 'desc'

        details = service.audit_service.write_log.await_args.args[2]
        assert details == {'tournament_id': 10, 'name': 'New Cup'}

    async def test_empty_name_raises(self, service):
        with pytest.raises(ValueError, match='name is required'):
            await service.create_tournament(name='   ', actor=make_user())
        service.repository.create.assert_not_called()

    async def test_seed_generator_string_none_normalized(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(
            name='X', seed_generator='None', actor=make_user(),
        )
        assert service.repository.create.await_args.kwargs['seed_generator'] is None

    async def test_optional_string_fields_stripped(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(
            name='X',
            bracket_url=' https://challonge.com/x ',
            rules_url=' https://r.x ',
            tournament_format=' DE ',
            triforce_access_message=' Buy access at https://shop.x ',
            randomizer_notes=' Preset: openboots \n',
            actor=make_user(),
        )
        kwargs = service.repository.create.await_args.kwargs
        assert kwargs['bracket_url'] == 'https://challonge.com/x'
        assert kwargs['rules_url'] == 'https://r.x'
        assert kwargs['tournament_format'] == 'DE'
        assert kwargs['triforce_access_message'] == 'Buy access at https://shop.x'
        assert kwargs['randomizer_notes'] == 'Preset: openboots'

    async def test_non_staff_actor_denied(self, service, monkeypatch):
        from application.services import auth_service

        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(auth_service.AuthService, 'is_staff', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', real_ensure)

        with pytest.raises(PermissionError):
            await service.create_tournament(name='X', actor=make_user())


class TestTournamentDays:
    async def test_create_passes_dates_and_hours_blob(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(
            name='X',
            event_start_date='2025-10-20',
            event_end_date='2025-10-22',
            tournament_hours={date(2025, 10, 20): ('09:00', '17:00')},
            actor=make_user(),
        )
        kw = service.repository.create.await_args.kwargs
        assert kw['event_start_date'] == date(2025, 10, 20)
        assert kw['event_end_date'] == date(2025, 10, 22)
        assert kw['tournament_hours'] == {'2025-10-20': {'open': '09:00', 'close': '17:00'}}

    async def test_create_end_before_start_raises(self, service):
        with pytest.raises(ValueError, match='end date cannot be before'):
            await service.create_tournament(
                name='X', event_start_date='2025-10-22', event_end_date='2025-10-20',
                actor=make_user(),
            )

    async def test_create_bad_date_raises(self, service):
        with pytest.raises(ValueError, match='YYYY-MM-DD'):
            await service.create_tournament(
                name='X', event_start_date='10/20/2025', actor=make_user(),
            )

    async def test_create_empty_hours_becomes_none(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(name='X', tournament_hours={}, actor=make_user())
        assert service.repository.create.await_args.kwargs['tournament_hours'] is None

    async def test_create_bad_hours_raises(self, service):
        with pytest.raises(ValueError, match='after open time'):
            await service.create_tournament(
                name='X',
                tournament_hours={date(2025, 10, 20): ('17:00', '09:00')},
                actor=make_user(),
            )

    async def test_update_sets_dates_and_hours(self, service):
        t = make_tournament(event_start_date=None, event_end_date=None)
        await service.update_tournament(
            t,
            event_start_date='2025-10-20',
            event_end_date='2025-10-22',
            tournament_hours={date(2025, 10, 20): ('09:00', '17:00')},
            actor=make_user(),
        )
        kw = service.repository.update.await_args.kwargs
        assert kw['event_start_date'] == date(2025, 10, 20)
        assert kw['event_end_date'] == date(2025, 10, 22)
        assert kw['tournament_hours'] == {'2025-10-20': {'open': '09:00', 'close': '17:00'}}

    async def test_update_clear_dates_and_hours(self, service):
        t = make_tournament(event_start_date=date(2025, 10, 20), event_end_date=date(2025, 10, 22))
        await service.update_tournament(
            t, event_start_date=None, event_end_date=None, tournament_hours=None,
            actor=make_user(),
        )
        kw = service.repository.update.await_args.kwargs
        assert kw['event_start_date'] is None
        assert kw['event_end_date'] is None
        assert kw['tournament_hours'] is None

    async def test_update_partial_validates_against_stored_bound(self, service):
        t = make_tournament(event_start_date=date(2025, 10, 1), event_end_date=date(2025, 10, 5))
        with pytest.raises(ValueError, match='end date cannot be before'):
            await service.update_tournament(t, event_start_date='2025-10-10', actor=make_user())

    async def test_update_omitting_days_leaves_them_unchanged(self, service):
        t = make_tournament()
        await service.update_tournament(t, name='Renamed', actor=make_user())
        kw = service.repository.update.await_args.kwargs
        assert 'event_start_date' not in kw
        assert 'event_end_date' not in kw
        assert 'tournament_hours' not in kw


class TestCrewRequirement:
    async def test_create_defaults_to_one_of_each(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(name='X', actor=make_user())
        kw = service.repository.create.await_args.kwargs
        assert kw['required_commentators'] == 1
        assert kw['required_trackers'] == 1

    async def test_create_coerces_the_uis_floats(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(
            name='X', required_commentators=2.0, required_trackers=0.0, actor=make_user(),
        )
        kw = service.repository.create.await_args.kwargs
        assert kw['required_commentators'] == 2
        assert kw['required_trackers'] == 0

    async def test_negative_requirement_rejected(self, service):
        with pytest.raises(ValueError, match='cannot be negative'):
            await service.create_tournament(
                name='X', required_trackers=-1, actor=make_user(),
            )

    async def test_update_sets_only_what_was_passed(self, service):
        tournament = make_tournament()
        await service.update_tournament(
            tournament, required_trackers=0, actor=make_user(),
        )
        kw = service.repository.update.await_args.kwargs
        assert kw['required_trackers'] == 0
        assert 'required_commentators' not in kw

    async def test_update_clears_randomizer_notes_with_an_empty_string(self, service):
        tournament = make_tournament()
        await service.update_tournament(tournament, randomizer_notes='', actor=make_user())
        assert service.repository.update.await_args.kwargs['randomizer_notes'] is None

    async def test_update_rejects_a_negative_requirement(self, service):
        with pytest.raises(ValueError, match='cannot be negative'):
            await service.update_tournament(
                make_tournament(), required_commentators=-2, actor=make_user(),
            )


class TestTournamentConfig:
    async def test_valid_config_normalized_and_passed_to_repo(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(
            name='X',
            config={'messaging_templates': {'welcome': 'hi {player}'}},
            actor=make_user(),
        )
        assert service.repository.create.await_args.kwargs['config'] == {
            'messaging_templates': {'welcome': 'hi {player}'}
        }

    async def test_none_config_passed_through(self, service):
        service.repository.create = AsyncMock(return_value=make_tournament())
        await service.create_tournament(name='X', actor=make_user())
        assert service.repository.create.await_args.kwargs['config'] is None

    async def test_unknown_key_raises_before_create(self, service):
        with pytest.raises(ValueError, match='Invalid tournament config'):
            await service.create_tournament(
                name='X', config={'bogus_key': 1}, actor=make_user(),
            )
        service.repository.create.assert_not_called()

    async def test_update_with_valid_config(self, service):
        t = make_tournament()
        await service.update_tournament(
            t, config={'messaging_templates': {}}, actor=make_user(),
        )
        assert service.repository.update.await_args.kwargs['config'] == {
            'messaging_templates': {}
        }

    async def test_update_unknown_key_raises(self, service):
        t = make_tournament()
        with pytest.raises(ValueError, match='Invalid tournament config'):
            await service.update_tournament(
                t, config={'nope': True}, actor=make_user(),
            )
        service.repository.update.assert_not_called()

    async def test_update_without_config_leaves_it_untouched(self, service):
        t = make_tournament()
        await service.update_tournament(t, name='Renamed', actor=make_user())
        assert 'config' not in service.repository.update.await_args.kwargs


class TestHardPreset:
    """The opt-in harder preset, and the two rules that keep a roll coherent."""

    async def test_create_with_a_matching_hard_preset_passes_the_id(self, service):
        service.preset_repository.get_by_id.side_effect = [
            SimpleNamespace(id=7, randomizer='alttpr'),
            SimpleNamespace(id=8, randomizer='alttpr'),
        ]
        await service.create_tournament(
            name='X', preset_id=7, hard_preset_id=8, actor=make_user(),
        )
        assert service.repository.create.await_args.kwargs['hard_preset_id'] == 8

    async def test_a_hard_preset_without_a_standard_one_is_refused(self, service):
        """There would be nothing to fall back to, and no randomizer to roll."""
        service.preset_repository.get_by_id.return_value = SimpleNamespace(
            id=8, randomizer='alttpr')
        with pytest.raises(ValueError, match='Set a seed preset before'):
            await service.create_tournament(
                name='X', hard_preset_id=8, actor=make_user(),
            )
        service.repository.create.assert_not_awaited()

    async def test_a_mismatched_randomizer_is_refused(self, service):
        """Otherwise agreeing to harder settings would change the game."""
        service.preset_repository.get_by_id.side_effect = [
            SimpleNamespace(id=7, randomizer='alttpr'),
            SimpleNamespace(id=8, randomizer='ootr'),
        ]
        with pytest.raises(ValueError, match='same randomizer'):
            await service.create_tournament(
                name='X', preset_id=7, hard_preset_id=8, actor=make_user(),
            )
        service.repository.create.assert_not_awaited()

    async def test_the_same_preset_twice_is_refused(self, service):
        service.preset_repository.get_by_id.side_effect = [
            SimpleNamespace(id=7, randomizer='alttpr'),
            SimpleNamespace(id=7, randomizer='alttpr'),
        ]
        with pytest.raises(ValueError, match='different preset'):
            await service.create_tournament(
                name='X', preset_id=7, hard_preset_id=7, actor=make_user(),
            )

    async def test_update_can_clear_the_hard_preset(self, service):
        t = make_tournament(hard_preset_id=8)
        await service.update_tournament(t, hard_preset_id=None, actor=make_user())
        assert service.repository.update.await_args.kwargs['hard_preset_id'] is None

    async def test_moving_the_standard_preset_under_a_hard_one_is_refused(self, service):
        """The pair must stay coherent even when only one side is being edited."""
        t = make_tournament(preset_id=7, hard_preset_id=8)
        service.preset_repository.get_by_id.side_effect = [
            SimpleNamespace(id=9, randomizer='ootr'),
            SimpleNamespace(id=8, randomizer='alttpr'),
        ]
        with pytest.raises(ValueError, match='no longer matches'):
            await service.update_tournament(t, preset_id=9, actor=make_user())

    async def test_update_without_either_leaves_them_untouched(self, service):
        t = make_tournament(preset_id=7, hard_preset_id=8)
        await service.update_tournament(t, name='Renamed', actor=make_user())
        assert 'hard_preset_id' not in service.repository.update.await_args.kwargs


class TestTournamentPreset:
    async def test_create_with_valid_preset_passes_id_to_repo(self, service):
        service.preset_repository.get_by_id.return_value = SimpleNamespace(
            id=7, randomizer='ootr')
        await service.create_tournament(name='X', preset_id=7, actor=make_user())
        assert service.repository.create.await_args.kwargs['preset_id'] == 7

    async def test_create_takes_the_randomizer_from_the_preset(self, service):
        service.preset_repository.get_by_id.return_value = SimpleNamespace(
            id=7, randomizer='ootr')
        await service.create_tournament(
            name='X', preset_id=7, seed_generator='alttpr', actor=make_user(),
        )
        assert service.repository.create.await_args.kwargs['seed_generator'] == 'ootr'

    async def test_create_with_unknown_preset_raises_before_create(self, service):
        service.preset_repository.get_by_id.return_value = None
        with pytest.raises(ValueError, match='Preset not found'):
            await service.create_tournament(name='X', preset_id=99, actor=make_user())
        service.repository.create.assert_not_awaited()

    async def test_create_without_preset_passes_none(self, service):
        await service.create_tournament(name='X', actor=make_user())
        assert service.repository.create.await_args.kwargs['preset_id'] is None
        service.preset_repository.get_by_id.assert_not_awaited()

    async def test_update_sets_preset_when_provided(self, service):
        service.preset_repository.get_by_id.return_value = SimpleNamespace(
            id=3, randomizer='smmap')
        t = make_tournament()
        await service.update_tournament(t, preset_id=3, actor=make_user())
        assert service.repository.update.await_args.kwargs['preset_id'] == 3
        assert service.repository.update.await_args.kwargs['seed_generator'] == 'smmap'

    async def test_update_clears_preset_with_explicit_none(self, service):
        t = make_tournament()
        await service.update_tournament(t, preset_id=None, actor=make_user())
        assert service.repository.update.await_args.kwargs['preset_id'] is None
        assert service.repository.update.await_args.kwargs['seed_generator'] is None
        service.preset_repository.get_by_id.assert_not_awaited()

    async def test_update_keeps_an_explicit_generator_when_clearing_the_preset(self, service):
        t = make_tournament()
        await service.update_tournament(
            t, preset_id=None, seed_generator='alttpr', actor=make_user(),
        )
        assert service.repository.update.await_args.kwargs['seed_generator'] == 'alttpr'

    async def test_update_without_preset_leaves_it_untouched(self, service):
        t = make_tournament()
        await service.update_tournament(t, name='Renamed', actor=make_user())
        assert 'preset_id' not in service.repository.update.await_args.kwargs

    async def test_update_unknown_preset_raises(self, service):
        service.preset_repository.get_by_id.return_value = None
        t = make_tournament()
        with pytest.raises(ValueError, match='Preset not found'):
            await service.update_tournament(t, preset_id=42, actor=make_user())


class TestUpdateTournament:
    async def test_only_provided_fields_written(self, service):
        t = make_tournament()
        await service.update_tournament(
            t, name='Renamed', actor=make_user(),
        )
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs == {'name': 'Renamed'}

    async def test_empty_name_string_raises(self, service):
        t = make_tournament()
        with pytest.raises(ValueError, match='name cannot be empty'):
            await service.update_tournament(t, name='   ', actor=make_user())

    async def test_triforce_access_message_stripped_and_cleared(self, service):
        t = make_tournament()
        await service.update_tournament(
            t, triforce_access_message=' how to buy ', actor=make_user(),
        )
        assert service.repository.update.await_args.kwargs['triforce_access_message'] == 'how to buy'

        await service.update_tournament(t, triforce_access_message='', actor=make_user())
        assert service.repository.update.await_args.kwargs['triforce_access_message'] is None

    async def test_seed_generator_explicit_value_passed_through(self, service):
        t = make_tournament()
        await service.update_tournament(
            t, seed_generator='alttpr', actor=make_user(),
        )
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs['seed_generator'] == 'alttpr'

    async def test_changed_fields_recorded_in_audit(self, service):
        t = make_tournament(tournament_id=7)
        await service.update_tournament(
            t,
            name='X',
            is_active=False,
            players_per_match=4,
            actor=make_user(),
        )
        details = service.audit_service.write_log.await_args.args[2]
        assert details['tournament_id'] == 7
        assert set(details['changed_fields']) == {'name', 'is_active', 'players_per_match'}

    async def test_none_for_string_field_means_no_update(self, service):
        t = make_tournament()
        await service.update_tournament(
            t, name='X', description=None, actor=make_user(),
        )
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs == {'name': 'X'}

    async def test_empty_string_for_optional_url_becomes_none(self, service):
        t = make_tournament()
        await service.update_tournament(t, bracket_url='', actor=make_user())
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs['bracket_url'] is None

    async def test_unauthorized_edit_denied(self, service, monkeypatch):
        from application.services import auth_service

        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(auth_service.AuthService, 'can_edit_tournament', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', real_ensure)

        with pytest.raises(PermissionError):
            await service.update_tournament(make_tournament(), name='X', actor=make_user())


class TestDeleteTournament:
    async def test_deletes_and_audits(self, service):
        t = make_tournament(tournament_id=33, is_active=False)
        await service.delete_tournament(
            t, actor=make_user(), confirmation='permanently delete',
        )
        t.delete.assert_awaited_once()
        details = service.audit_service.write_log.await_args.args[2]
        assert details == {
            'tournament_id': 33,
            'name': 'Tournament',
            'deleted': {'matches': 2, 'players': 3, 'brackets': 0, 'triforce_texts': 0},
        }

    async def test_accepts_confirmation_with_stray_case_and_spacing(self, service):
        t = make_tournament(is_active=False)
        await service.delete_tournament(
            t, actor=make_user(), confirmation='  Permanently Delete ',
        )
        t.delete.assert_awaited_once()

    @pytest.mark.parametrize('confirmation', [None, '', 'delete', 'permanently  delete'])
    async def test_rejects_wrong_confirmation(self, service, confirmation):
        t = make_tournament(is_active=False)
        with pytest.raises(ValueError, match='permanently delete'):
            await service.delete_tournament(t, actor=make_user(), confirmation=confirmation)
        t.delete.assert_not_awaited()
        service.audit_service.write_log.assert_not_awaited()

    async def test_refuses_while_active_even_with_the_phrase(self, service):
        t = make_tournament(is_active=True)
        with pytest.raises(ValueError, match='Deactivate'):
            await service.delete_tournament(
                t, actor=make_user(), confirmation='permanently delete',
            )
        t.delete.assert_not_awaited()
        service.audit_service.write_log.assert_not_awaited()

    async def test_requires_staff(self, service, monkeypatch):
        from application.services import auth_service

        async def deny(*_a, **_k):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(auth_service.AuthService, 'is_staff', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', real_ensure)

        t = make_tournament(is_active=False)
        with pytest.raises(PermissionError):
            await service.delete_tournament(
                t, actor=make_user(), confirmation='permanently delete',
            )
        t.delete.assert_not_awaited()

    async def test_preview_reports_dependent_counts(self, service):
        counts = await service.deletion_preview(make_tournament(tournament_id=7))
        service.repository.dependent_counts.assert_awaited_once_with(7)
        assert counts['matches'] == 2


class TestAdminAndCoordinator:
    @pytest.fixture(autouse=True)
    def _everyone_is_a_member(self, monkeypatch):
        from application.services.tenant_membership_service import TenantMembershipService

        monkeypatch.setattr(TenantMembershipService, 'require_community_users', AsyncMock())

    async def test_add_admin_calls_m2m_and_audits(self, service):
        t = make_tournament(tournament_id=5)
        target = make_user(user_id=99)
        await service.add_admin(t, target, actor=make_user(user_id=1))
        service.repository.set_tournament_grant.assert_awaited_once_with(
            t, target, TournamentGrant.TOURNAMENT_ADMIN, granted=True,
        )
        action = service.audit_service.write_log.await_args.args[1]
        assert action == 'tournament.admin_granted'

    async def test_remove_admin_calls_m2m_and_audits(self, service):
        t = make_tournament(tournament_id=5)
        target = make_user(user_id=99)
        await service.remove_admin(t, target, actor=make_user(user_id=1))
        service.repository.set_tournament_grant.assert_awaited_once_with(
            t, target, TournamentGrant.TOURNAMENT_ADMIN, granted=False,
        )
        action = service.audit_service.write_log.await_args.args[1]
        assert action == 'tournament.admin_revoked'

    async def test_add_admin_is_idempotent_at_orm_level(self, service):
        # Tortoise's M2M add silently dedupes, so the service shouldn't pre-check membership.
        t = make_tournament()
        target = make_user()
        await service.add_admin(t, target, actor=make_user())
        await service.add_admin(t, target, actor=make_user())
        assert service.repository.set_tournament_grant.await_count == 2

    async def test_manual_grant_clears_discord_provenance(self, service):
        t = make_tournament(tournament_id=5)
        target = make_user(user_id=99)
        await service.add_admin(t, target, actor=make_user(user_id=1))
        service.discord_grant_repository.remove.assert_awaited_once_with(
            target, 5, TournamentGrant.TOURNAMENT_ADMIN,
        )

    async def test_manual_revoke_clears_discord_provenance(self, service):
        t = make_tournament(tournament_id=5)
        target = make_user(user_id=99)
        await service.remove_crew_coordinator(t, target, actor=make_user(user_id=1))
        service.discord_grant_repository.remove.assert_awaited_once_with(
            target, 5, TournamentGrant.CREW_COORDINATOR,
        )

    async def test_add_crew_coordinator_calls_m2m_and_audits(self, service):
        t = make_tournament(tournament_id=5)
        target = make_user(user_id=99)
        await service.add_crew_coordinator(t, target, actor=make_user(user_id=1))
        service.repository.set_tournament_grant.assert_awaited_once_with(
            t, target, TournamentGrant.CREW_COORDINATOR, granted=True,
        )
        action = service.audit_service.write_log.await_args.args[1]
        assert action == 'tournament.crew_coordinator_granted'

    async def test_remove_crew_coordinator_calls_m2m_and_audits(self, service):
        t = make_tournament(tournament_id=5)
        target = make_user(user_id=99)
        await service.remove_crew_coordinator(t, target, actor=make_user(user_id=1))
        service.repository.set_tournament_grant.assert_awaited_once_with(
            t, target, TournamentGrant.CREW_COORDINATOR, granted=False,
        )
        action = service.audit_service.write_log.await_args.args[1]
        assert action == 'tournament.crew_coordinator_revoked'

    async def test_non_staff_cannot_grant_admin(self, service, monkeypatch):
        from application.services import auth_service

        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(auth_service.AuthService, 'can_grant_roles', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', real_ensure)

        with pytest.raises(PermissionError):
            await service.add_admin(make_tournament(), make_user(), actor=make_user())


class TestReadHelpers:
    async def test_get_all_passes_active_only(self, service):
        await service.get_all_tournaments(active_only=True)
        service.repository.get_all.assert_awaited_once_with(active_only=True)

    async def test_get_by_id_delegates(self, service):
        t = make_tournament(tournament_id=99)
        service.repository.get_by_id = AsyncMock(return_value=t)
        result = await service.get_tournament_by_id(99)
        assert result is t
        service.repository.get_by_id.assert_awaited_once_with(99)
