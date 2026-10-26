"""Unit tests for AuthService policy helpers.

These tests stub the model-layer queries (UserRole.filter, Tournament.filter,
user.admin_tournaments.all, etc.) so the policy logic is exercised without
needing a database. The goal is to lock in the role rules so refactors
don't accidentally widen or narrow them.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.services.auth_service import AuthService, get_user_from_discord_id
from models import Role

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ExistsQS:
    """A chainable fake queryset: ``.filter(...)`` is a no-op that returns self
    (the policy now tenant-filters these reverse relations), ``.exists()``
    resolves the fixed result, and awaiting it yields rows — ``tournament_scope``
    reads the ids rather than only asking whether any exist."""

    def __init__(self, result: bool, ids: tuple[int, ...] = ()):
        self._result = result
        self._ids = ids or ((1,) if result else ())

    def filter(self, **_kwargs):
        return self

    async def exists(self):
        return self._result

    def __await__(self):
        async def rows():
            return [SimpleNamespace(id=i) for i in self._ids]
        return rows().__await__()


def make_user(user_id: int = 1):
    """Build a minimal User stand-in.

    `admin_tournaments` and `crew_coordinated_tournaments` are accessed as
    `await user.<attr>.all().filter(tenant_id=...).exists()` in the policy code,
    so they're mocked as callables returning a chainable fake queryset.
    """
    user = SimpleNamespace(id=user_id)
    user.admin_tournaments = SimpleNamespace(all=lambda: _ExistsQS(False))
    user.crew_coordinated_tournaments = SimpleNamespace(all=lambda: _ExistsQS(False))
    return user


def make_match(tournament_id: int = 100, match_id: int = 1, stage_id: int | None = None):
    return SimpleNamespace(id=match_id, tournament_id=tournament_id, stage_id=stage_id)


def make_tournament(tournament_id: int = 100):
    return SimpleNamespace(id=tournament_id)


@pytest.fixture
def patch_roles(monkeypatch):
    """Fixture that lets a test declare what roles a user holds."""

    def _patch(roles_for_user: set[Role] | None = None):
        roles = set(roles_for_user or [])

        class FakeRoleQS:
            def __init__(self, has_match: bool):
                self._has_match = has_match

            async def values_list(self, *_args, **_kwargs):
                return [r.value for r in roles] if self._has_match else []

            async def exists(self):
                return bool(roles) if self._has_match else False

            def filter(self, *_args, **_kwargs):
                return self

        def fake_filter(*_q, user=None, role=None, **_kwargs):
            if user is None:
                return FakeRoleQS(False)
            has_match = role is None or role in roles
            return FakeRoleQS(has_match)

        from models import UserRole
        monkeypatch.setattr(UserRole, 'filter', staticmethod(fake_filter))

    return _patch


@pytest.fixture
def patch_tournament_membership(monkeypatch):
    """Mock Tournament.filter so the policy thinks the actor is/isn't TA/CC."""

    def _patch(admin_of: set[int] | None = None, cc_of: set[int] | None = None):
        admin_of = admin_of or set()
        cc_of = cc_of or set()

        class FakeTournamentQS:
            def __init__(self, exists_result):
                self._exists = exists_result

            def filter(self, **_kwargs):
                # The policy now tenant-filters this queryset; ignore it here.
                return self

            async def exists(self):
                return self._exists

        def fake_filter(*, id=None, admins__id=None, crew_coordinators__id=None, **_kwargs):
            if admins__id is not None:
                return FakeTournamentQS(id in admin_of)
            if crew_coordinators__id is not None:
                return FakeTournamentQS(id in cc_of)
            return FakeTournamentQS(False)

        from models import Tournament
        monkeypatch.setattr(Tournament, 'filter', staticmethod(fake_filter))

    return _patch


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class TestPrimitives:
    async def test_none_user_has_no_roles(self, patch_roles):
        patch_roles(set())
        assert await AuthService.is_staff(None) is False
        assert await AuthService.is_proctor(None) is False
        assert await AuthService.is_stream_manager(None) is False

    async def test_is_staff_true_when_user_has_staff_role(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.is_staff(make_user()) is True

    async def test_is_staff_false_when_user_has_other_role(self, patch_roles):
        patch_roles({Role.PROCTOR})
        assert await AuthService.is_staff(make_user()) is False

    async def test_get_roles_returns_set_of_role_enums(self, patch_roles):
        patch_roles({Role.PROCTOR, Role.STREAM_MANAGER})
        roles = await AuthService.get_roles(make_user())
        assert roles == {Role.PROCTOR, Role.STREAM_MANAGER}

    async def test_get_roles_empty_for_none_user(self):
        assert await AuthService.get_roles(None) == set()

    async def test_is_tournament_admin_none_user(self):
        assert await AuthService.is_tournament_admin(None, 1) is False

    async def test_is_tournament_admin_true_when_member(self, patch_tournament_membership):
        patch_tournament_membership(admin_of={42})
        assert await AuthService.is_tournament_admin(make_user(), 42) is True

    async def test_is_tournament_admin_false_when_other_tournament(self, patch_tournament_membership):
        patch_tournament_membership(admin_of={42})
        assert await AuthService.is_tournament_admin(make_user(), 99) is False

    async def test_is_crew_coordinator_uses_separate_m2m(self, patch_tournament_membership):
        patch_tournament_membership(admin_of=set(), cc_of={5})
        assert await AuthService.is_crew_coordinator_of(make_user(), 5) is True
        assert await AuthService.is_tournament_admin(make_user(), 5) is False


# ---------------------------------------------------------------------------
# Composite policies — Staff override
# ---------------------------------------------------------------------------


class TestStaffOverride:
    async def test_staff_can_crud_any_match(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STAFF})
        patch_tournament_membership()
        match = make_match(tournament_id=999)
        assert await AuthService.can_crud_match(make_user(), match) is True

    async def test_staff_can_run_any_match(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STAFF})
        patch_tournament_membership()
        assert await AuthService.can_run_match(make_user(), make_match()) is True

    async def test_staff_can_approve_any_crew(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STAFF})
        patch_tournament_membership()
        assert await AuthService.can_approve_crew(make_user(), make_match()) is True

    async def test_staff_can_manage_streams(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_manage_stages(make_user()) is True

    async def test_staff_can_grant_roles(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_grant_roles(make_user()) is True

    async def test_staff_can_edit_any_tournament(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STAFF})
        patch_tournament_membership()
        assert await AuthService.can_edit_tournament(make_user(), make_tournament(123)) is True


# ---------------------------------------------------------------------------
# can_crud_match vs can_run_match — Proctor distinction
# ---------------------------------------------------------------------------


class TestRunVsCrud:
    async def test_proctor_can_run_but_not_crud(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.PROCTOR})
        patch_tournament_membership()  # not TA of anything
        match = make_match()
        assert await AuthService.can_run_match(make_user(), match) is True
        assert await AuthService.can_crud_match(make_user(), match) is False

    async def test_ta_can_both_for_own_tournament(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={100})
        match = make_match(tournament_id=100)
        assert await AuthService.can_run_match(make_user(), match) is True
        assert await AuthService.can_crud_match(make_user(), match) is True

    async def test_ta_cannot_crud_other_tournament(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={100})
        match = make_match(tournament_id=200)
        assert await AuthService.can_crud_match(make_user(), match) is False
        assert await AuthService.can_run_match(make_user(), match) is False

    async def test_regular_user_cannot_run_or_crud(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership()
        match = make_match()
        assert await AuthService.can_run_match(make_user(), match) is False
        assert await AuthService.can_crud_match(make_user(), match) is False

    async def test_broadcast_tech_runs_a_match_on_a_stage(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STREAM_MANAGER})
        patch_tournament_membership()
        match = make_match(tournament_id=999, stage_id=3)
        assert await AuthService.can_run_match(make_user(), match) is True
        assert await AuthService.can_crud_match(make_user(), match) is False
        assert await AuthService.can_confirm_match(make_user(), match) is False

    async def test_broadcast_tech_cannot_run_an_off_stage_match(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STREAM_MANAGER})
        patch_tournament_membership()
        assert await AuthService.can_run_match(make_user(), make_match(stage_id=None)) is False


# ---------------------------------------------------------------------------
# can_run_match vs can_confirm_match — confirming is the admin's step
# ---------------------------------------------------------------------------


class TestRunVsConfirm:
    """A proctor runs matches; only staff / the tournament's admin confirms."""

    async def test_proctor_can_run_but_not_confirm(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.PROCTOR})
        patch_tournament_membership()  # not TA of anything
        match = make_match()
        assert await AuthService.can_run_match(make_user(), match) is True
        assert await AuthService.can_confirm_match(make_user(), match) is False

    async def test_staff_can_confirm(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STAFF})
        patch_tournament_membership()
        assert await AuthService.can_confirm_match(make_user(), make_match()) is True

    async def test_ta_can_confirm_own_tournament(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={100})
        assert await AuthService.can_confirm_match(
            make_user(), make_match(tournament_id=100)) is True

    async def test_ta_cannot_confirm_other_tournament(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={100})
        assert await AuthService.can_confirm_match(
            make_user(), make_match(tournament_id=200)) is False

    async def test_regular_user_cannot_confirm(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership()
        assert await AuthService.can_confirm_match(make_user(), make_match()) is False


# ---------------------------------------------------------------------------
# can_approve_crew — TA OR CC of the match's tournament
# ---------------------------------------------------------------------------


class TestApproveCrew:
    async def test_ta_of_match_tournament_can_approve(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={42})
        match = make_match(tournament_id=42)
        assert await AuthService.can_approve_crew(make_user(), match) is True

    async def test_cc_of_match_tournament_can_approve(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of=set(), cc_of={42})
        match = make_match(tournament_id=42)
        assert await AuthService.can_approve_crew(make_user(), match) is True

    async def test_cc_of_other_tournament_cannot_approve(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of=set(), cc_of={42})
        match = make_match(tournament_id=99)
        assert await AuthService.can_approve_crew(make_user(), match) is False

    async def test_proctor_alone_cannot_approve_crew(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.PROCTOR})
        patch_tournament_membership()
        assert await AuthService.can_approve_crew(make_user(), make_match()) is False

    async def test_stream_manager_alone_cannot_approve_crew(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STREAM_MANAGER})
        patch_tournament_membership()
        assert await AuthService.can_approve_crew(make_user(), make_match()) is False


# ---------------------------------------------------------------------------
# can_assign_match_stream — Stream Manager globally OR TA of match's tournament
# ---------------------------------------------------------------------------


class TestAssignMatchStream:
    async def test_stream_manager_can_assign_any_match(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.STREAM_MANAGER})
        patch_tournament_membership()
        match = make_match(tournament_id=999)
        assert await AuthService.can_assign_match_stream(make_user(), match) is True

    async def test_ta_can_assign_own_tournament_match(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={42})
        match = make_match(tournament_id=42)
        assert await AuthService.can_assign_match_stream(make_user(), match) is True

    async def test_ta_cannot_assign_other_tournament_match(self, patch_roles, patch_tournament_membership):
        patch_roles(set())
        patch_tournament_membership(admin_of={42})
        match = make_match(tournament_id=99)
        assert await AuthService.can_assign_match_stream(make_user(), match) is False

    async def test_proctor_alone_cannot_assign_streams(self, patch_roles, patch_tournament_membership):
        patch_roles({Role.PROCTOR})
        patch_tournament_membership()
        assert await AuthService.can_assign_match_stream(make_user(), make_match()) is False


# ---------------------------------------------------------------------------
# can_grant_roles — Staff only
# ---------------------------------------------------------------------------


class TestGrantRoles:
    async def test_non_staff_cannot_grant_roles(self, patch_roles):
        patch_roles({Role.PROCTOR, Role.STREAM_MANAGER})
        assert await AuthService.can_grant_roles(make_user()) is False

    async def test_only_staff_can_grant_roles(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_grant_roles(make_user()) is True


# ---------------------------------------------------------------------------
# can_submit_triforce_text — paid global role + generator capability
# ---------------------------------------------------------------------------


def make_triforce_tournament(is_active=True, seed_generator='alttpr'):
    return SimpleNamespace(id=1, is_active=is_active, seed_generator=seed_generator)


class TestTriforceSubmit:
    async def test_is_triforce_submitter(self, patch_roles):
        patch_roles({Role.TRIFORCE_SUBMITTER})
        assert await AuthService.is_triforce_submitter(make_user()) is True
        patch_roles({Role.PROCTOR})
        assert await AuthService.is_triforce_submitter(make_user()) is False

    async def test_submitter_can_submit_to_supported_active_tournament(self, patch_roles):
        patch_roles({Role.TRIFORCE_SUBMITTER})
        assert await AuthService.can_submit_triforce_text(
            make_user(), make_triforce_tournament()
        ) is True

    async def test_staff_override_can_submit(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_submit_triforce_text(
            make_user(), make_triforce_tournament()
        ) is True

    async def test_no_role_cannot_submit(self, patch_roles):
        patch_roles(set())
        assert await AuthService.can_submit_triforce_text(
            make_user(), make_triforce_tournament()
        ) is False

    async def test_unsupported_generator_blocks_submit(self, patch_roles):
        patch_roles({Role.TRIFORCE_SUBMITTER})
        assert await AuthService.can_submit_triforce_text(
            make_user(), make_triforce_tournament(seed_generator='ootr')
        ) is False

    async def test_inactive_tournament_blocks_submit(self, patch_roles):
        patch_roles({Role.TRIFORCE_SUBMITTER})
        assert await AuthService.can_submit_triforce_text(
            make_user(), make_triforce_tournament(is_active=False)
        ) is False


# ---------------------------------------------------------------------------
# ensure() raises PermissionError
# ---------------------------------------------------------------------------


class TestEnsure:
    async def test_ensure_raises_when_disallowed(self):
        with pytest.raises(PermissionError):
            await AuthService.ensure(False, "nope")

    async def test_ensure_silent_when_allowed(self):
        await AuthService.ensure(True)  # should not raise


# ---------------------------------------------------------------------------
# can_view_admin — admin global roles or TA/CC membership (NOT proctor/volunteer)
# ---------------------------------------------------------------------------


def make_ta_user(user_id: int = 1):
    """User whose admin_tournaments.all().filter(...).exists() resolves True."""
    user = make_user(user_id)
    user.admin_tournaments = SimpleNamespace(all=lambda: _ExistsQS(True))
    return user


class TestViewAdmin:
    async def test_none_user_cannot_view_admin(self):
        assert await AuthService.can_view_admin(None) is False

    async def test_staff_can_view_admin(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_view_admin(make_user()) is True

    async def test_proctor_alone_cannot_view_admin(self, patch_roles):
        patch_roles({Role.PROCTOR})
        assert await AuthService.can_view_admin(make_user()) is False

    async def test_broadcast_tech_alone_cannot_view_admin(self, patch_roles):
        patch_roles({Role.STREAM_MANAGER})
        assert await AuthService.can_view_admin(make_user()) is False

    @pytest.mark.parametrize('role', [Role.EQUIPMENT_MANAGER, Role.VOLUNTEER_COORDINATOR])
    async def test_a_single_tab_admin_role_cannot_view_admin(self, patch_roles, role):
        """Held to their own Admin tabs, not the reports and crew reads."""
        patch_roles({role})
        assert await AuthService.can_view_admin(make_user()) is False

    async def test_a_volunteer_coordinator_reads_the_volunteer_admin_data(self, patch_roles):
        patch_roles({Role.VOLUNTEER_COORDINATOR})
        assert await AuthService.can_view_volunteer_admin(make_user()) is True

    async def test_an_equipment_manager_does_not_read_volunteer_admin_data(self, patch_roles):
        patch_roles({Role.EQUIPMENT_MANAGER})
        assert await AuthService.can_view_volunteer_admin(make_user()) is False

    async def test_volunteer_alone_cannot_view_admin(self, patch_roles):
        patch_roles({Role.VOLUNTEER})
        assert await AuthService.can_view_admin(make_user()) is False

    async def test_ta_membership_grants_admin_view(self, patch_roles):
        patch_roles(set())
        assert await AuthService.can_view_admin(make_ta_user()) is True

    async def test_ensure_message_propagates(self):
        with pytest.raises(PermissionError, match="custom message"):
            await AuthService.ensure(False, "custom message")


# ---------------------------------------------------------------------------
# can_view_schedule_board — the predicate the reports resolve their drill-out
# links against, so a link is never offered to someone the board would refuse.
# ---------------------------------------------------------------------------


def make_cc_user(user_id: int = 1):
    """User whose crew_coordinated_tournaments…exists() resolves True."""
    user = make_user(user_id)
    user.crew_coordinated_tournaments = SimpleNamespace(all=lambda: _ExistsQS(True))
    return user


class TestViewScheduleBoard:
    async def test_none_user_cannot(self):
        assert await AuthService.can_view_schedule_board(None) is False

    async def test_staff_can(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_view_schedule_board(make_user()) is True

    async def test_tournament_admin_can(self, patch_roles):
        patch_roles(set())
        assert await AuthService.can_view_schedule_board(make_ta_user()) is True

    async def test_crew_coordinator_can(self, patch_roles):
        patch_roles(set())
        assert await AuthService.can_view_schedule_board(make_cc_user()) is True

    async def test_volunteer_coordinator_alone_cannot(self, patch_roles):
        """Reaches Vol. Schedule, not the match board — and so gets the
        volunteer report's links and none of the board ones."""
        patch_roles({Role.VOLUNTEER_COORDINATOR})
        assert await AuthService.can_view_schedule_board(make_user()) is False

    async def test_proctor_alone_cannot(self, patch_roles):
        """They run matches from the Proctor Station, not the admin board."""
        patch_roles({Role.PROCTOR})
        assert await AuthService.can_view_schedule_board(make_user()) is False

    async def test_stream_manager_can(self, patch_roles):
        """Assigning a stage is a match-board action and this is the only board
        that offers it — the role had no surface that did its job."""
        patch_roles({Role.STREAM_MANAGER})
        assert await AuthService.can_view_schedule_board(make_user()) is True


class TestScheduleBoardScope:
    """Which matches a per-match "Open in Schedule" link may be offered for."""

    async def test_none_user_opens_nothing(self):
        assert await AuthService.schedule_board_scope(None) == frozenset()

    async def test_staff_opens_every_match(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.schedule_board_scope(make_user()) is None

    async def test_stream_manager_opens_every_match(self, patch_roles):
        patch_roles({Role.STREAM_MANAGER})
        assert await AuthService.schedule_board_scope(make_user()) is None

    async def test_operator_opens_only_their_tournaments(self, patch_roles):
        patch_roles(set())
        user = make_user()
        user.admin_tournaments = SimpleNamespace(all=lambda: _ExistsQS(True, (3,)))
        user.crew_coordinated_tournaments = SimpleNamespace(all=lambda: _ExistsQS(True, (9,)))
        assert await AuthService.schedule_board_scope(user) == frozenset({3, 9})

    async def test_equipment_manager_opens_nothing(self, patch_roles):
        """An admin, but one with no Schedule tab to land on."""
        patch_roles({Role.EQUIPMENT_MANAGER})
        assert await AuthService.schedule_board_scope(make_user()) == frozenset()


class TestTournamentScope:
    """The id sets a non-staff operator's surfaces narrow to."""

    async def test_none_user_operates_nothing(self):
        assert await AuthService.tournament_scope(None) == (set(), set())

    async def test_the_two_relations_stay_distinct(self):
        user = make_user()
        user.admin_tournaments = SimpleNamespace(all=lambda: _ExistsQS(True, (3, 4)))
        user.crew_coordinated_tournaments = SimpleNamespace(all=lambda: _ExistsQS(True, (4, 9)))
        admin_ids, cc_ids = await AuthService.tournament_scope(user)
        assert admin_ids == {3, 4}
        assert cc_ids == {4, 9}

    async def test_an_operator_of_nothing_gets_empty_sets(self):
        assert await AuthService.tournament_scope(make_user()) == (set(), set())


# ---------------------------------------------------------------------------
# Online-tournament foundation roles (PR 0)
# ---------------------------------------------------------------------------


def make_system_user(user_id: int = 7):
    """The reserved automation actor — marked ``is_system``."""
    return SimpleNamespace(id=user_id, is_system=True)


class TestOnlineTournamentRoles:
    async def test_is_system_field_check(self):
        assert AuthService.is_system(make_system_user()) is True
        assert AuthService.is_system(make_user()) is False  # no is_system attr → False
        assert AuthService.is_system(None) is False

    async def test_preset_manager_role_grants(self, patch_roles):
        patch_roles({Role.PRESET_MANAGER})
        assert await AuthService.can_manage_presets(make_user()) is True

    async def test_staff_grants_preset_management(self, patch_roles):
        patch_roles({Role.STAFF})
        assert await AuthService.can_manage_presets(make_user()) is True

    async def test_unrelated_role_denies_preset_management(self, patch_roles):
        patch_roles({Role.VOLUNTEER})
        assert await AuthService.can_manage_presets(make_user()) is False

    async def test_system_user_bypasses_preset_gate(self, patch_roles):
        # No roles at all, but the system actor is always authorized.
        patch_roles(set())
        assert await AuthService.can_manage_presets(make_system_user()) is True

    async def test_sync_admin_role_grants(self, patch_roles):
        patch_roles({Role.SYNC_ADMIN})
        assert await AuthService.can_manage_sync(make_user()) is True

    async def test_unrelated_role_denies_sync_management(self, patch_roles):
        patch_roles({Role.PRESET_MANAGER})
        assert await AuthService.can_manage_sync(make_user()) is False

    async def test_system_user_bypasses_sync_gate(self, patch_roles):
        patch_roles(set())
        assert await AuthService.can_manage_sync(make_system_user()) is True

    async def test_qualifier_admin_role_grants(self, patch_roles):
        patch_roles({Role.QUALIFIER_ADMIN})
        assert await AuthService.can_admin_qualifier(make_user()) is True

    async def test_unrelated_role_denies_qualifier_admin(self, patch_roles):
        patch_roles({Role.PRESET_MANAGER})
        assert await AuthService.can_admin_qualifier(make_user()) is False

    async def test_per_entity_admin_grants_qualifier_admin(self, patch_roles):
        # No global qualifier role, but the user is listed on this qualifier's
        # admins M2M — the forward-compatible per-entity path.
        patch_roles(set())
        qualifier = SimpleNamespace(admins=SimpleNamespace(filter=lambda **_: _ExistsQS(True)))
        assert await AuthService.can_admin_qualifier(make_user(), qualifier) is True

    async def test_non_admin_of_specific_qualifier_denied(self, patch_roles):
        patch_roles(set())
        qualifier = SimpleNamespace(admins=SimpleNamespace(filter=lambda **_: _ExistsQS(False)))
        assert await AuthService.can_admin_qualifier(make_user(), qualifier) is False

    async def test_system_user_bypasses_qualifier_gate(self, patch_roles):
        patch_roles(set())
        assert await AuthService.can_admin_qualifier(make_system_user()) is True


class TestGetUserFromDiscordId:
    async def test_none_discord_id_returns_none(self):
        assert await get_user_from_discord_id(None) is None

    async def test_active_user_resolved(self, monkeypatch):
        from application.services import auth_service
        user = SimpleNamespace(id=1, is_active=True)
        monkeypatch.setattr(auth_service.User, 'get_or_none', AsyncMock(return_value=user))
        assert await get_user_from_discord_id('123') is user

    async def test_inactive_user_treated_as_logged_out(self, monkeypatch):
        # A deactivated account must resolve to None so it loses page/role access.
        from application.services import auth_service
        user = SimpleNamespace(id=1, is_active=False)
        monkeypatch.setattr(auth_service.User, 'get_or_none', AsyncMock(return_value=user))
        assert await get_user_from_discord_id('123') is None
