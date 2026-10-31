"""Values that do not fit their column fail as user errors, not as crashes.

The suite runs on SQLite, which enforces neither ``VARCHAR`` length nor 32-bit
integer range — so without the guards in ``models/column_guards.py`` these writes
pass here and blow up as a 500 against Postgres. These tests assert on the
*exception type* as much as the message: ``ValueError`` is the contract every
entry surface already handles (``ui.notify`` on the web, 400 over REST,
``invalid_request`` over MCP).
"""

import pytest
from tortoise.exceptions import ValidationError as ORMValidationError

from application.services.async_qualifier.async_qualifier_service import MAX_RUN_SECONDS
from application.services.user_service import UserService
from application.tenant_context import require_tenant_id
from models import FieldValueError, Tournament, User
from models.column_guards import IntRangeGuard, MaxLengthGuard


class TestGuardUnits:
    def test_max_length_guard_allows_the_boundary(self):
        guard = MaxLengthGuard(10, 'Name')
        guard(None)
        guard('x' * 10)
        with pytest.raises(FieldValueError) as exc:
            guard('x' * 11)
        assert 'at most 10 characters' in str(exc.value)

    def test_int_range_guard_allows_the_boundary(self):
        guard = IntRangeGuard(-5, 5, 'Count')
        guard(None)
        guard(5)
        guard(-5)
        with pytest.raises(FieldValueError):
            guard(6)
        with pytest.raises(FieldValueError):
            guard(-6)

    def test_int_range_guard_ignores_bools_and_non_ints(self):
        guard = IntRangeGuard(0, 1, 'Flag')
        guard(True)
        guard(False)
        guard('not an int')

    def test_field_value_error_is_a_value_error(self):
        assert issubclass(FieldValueError, ValueError)
        assert not issubclass(ORMValidationError, ValueError)


class TestInstalledOnModels:
    @pytest.mark.asyncio
    async def test_over_length_char_field_raises_valueerror(self, db):
        with pytest.raises(ValueError) as exc:
            await Tournament.create(name='x' * 256, tenant_id=require_tenant_id())
        assert 'at most 255 characters' in str(exc.value)

    @pytest.mark.asyncio
    async def test_out_of_range_int_field_raises_valueerror(self, db):
        with pytest.raises(ValueError) as exc:
            await Tournament.create(
                name='ok', tenant_id=require_tenant_id(), average_match_duration=2_147_483_648,
            )
        assert 'must be between' in str(exc.value)

    @pytest.mark.asyncio
    async def test_bigint_field_keeps_its_wider_range(self, db):
        user = await User.create(discord_id=9_000_000_000, username='wide')
        assert user.id

    @pytest.mark.asyncio
    async def test_values_that_fit_are_untouched(self, db):
        tournament = await Tournament.create(
            name='x' * 255, tenant_id=require_tenant_id(), average_match_duration=90,
        )
        assert tournament.id

    @pytest.mark.asyncio
    async def test_queryset_update_is_guarded_too(self, db):
        await Tournament.create(name='ok', tenant_id=require_tenant_id())
        with pytest.raises(ValueError):
            await Tournament.filter(name='ok').update(name='x' * 300)

    @pytest.mark.asyncio
    async def test_profile_edit_surfaces_as_a_user_error(self, db):
        """A logged-in user typing a long pronouns string is a 400, not a 500."""
        user = await User.create(discord_id=4242, username='player')
        with pytest.raises(ValueError):
            await UserService().update_user_personal_info(user, user, pronouns='x' * 51)


class TestQualifierFinishTimeBound:
    @pytest.mark.asyncio
    async def test_absurd_finish_time_is_rejected_with_a_readable_message(
        self, db, monkeypatch,
    ):
        from application.services.async_qualifier import async_qualifier_service as svc_mod

        service = svc_mod.AsyncQualifierService()
        run = object()

        async def _fake_require(user, run_id):
            return run

        monkeypatch.setattr(service, '_require_own_active_run', _fake_require)

        with pytest.raises(ValueError) as exc:
            await service.submit_run(
                User(id=1), 1, elapsed_seconds=MAX_RUN_SECONDS + 1,
            )
        assert 'longer than a week' in str(exc.value)
