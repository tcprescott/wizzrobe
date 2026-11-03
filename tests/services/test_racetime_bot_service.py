"""Tests for RacetimeBotService — platform CRUD + tenant authorization grants.

The security-critical guarantees: only SUPER_ADMIN may manage bots, the
``client_secret`` is never handed back through :meth:`serialize` and is only
rewritten when a new value is supplied, and the tenant-facing authorization read
surfaces only categories a tenant was actively granted.
"""

import pytest

from application.services import RacetimeBotService
from application.services.audit_service import AuditActions
from application.tenant_context import tenant_scope
from models import AuditLog, Role, Tenant, User, UserRole


@pytest.fixture
async def super_admin(db):
    su = await User.create(discord_id=1000, username='root')
    await UserRole.create(user=su, role=Role.SUPER_ADMIN, tenant=None)
    return su


@pytest.fixture
async def plain_user(db):
    return await User.create(discord_id=1001, username='nobody')


@pytest.fixture
async def tenants(db):
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='Tenant B', slug='tenant-b')
    return a, b


class TestBotCrud:
    async def test_create_requires_super_admin(self, plain_user, db):
        with pytest.raises(PermissionError):
            await RacetimeBotService().create_bot(
                plain_user, category='alttpr', client_id='cid',
                client_secret='sec', name='ALTTPR Bot',
            )

    async def test_create_and_serialize_hides_secret(self, super_admin, db):
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='cid',
            client_secret='super-secret', name='ALTTPR Bot',
        )
        assert bot.client_secret == 'super-secret'
        serialized = service.serialize(bot)
        assert 'client_secret' not in serialized
        assert serialized['category'] == 'alttpr'
        assert await AuditLog.filter(action=AuditActions.RACETIME_BOT_CREATED).count() == 1

    async def test_create_rejects_duplicate_category(self, super_admin, db):
        service = RacetimeBotService()
        await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='s', name='A',
        )
        with pytest.raises(ValueError):
            await service.create_bot(
                super_admin, category='alttpr', client_id='c2', client_secret='s2', name='B',
            )

    async def test_blank_secret_on_update_keeps_existing(self, super_admin, db):
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='keep-me', name='A',
        )
        await service.update_bot(super_admin, bot.id, name='Renamed', client_secret='')
        await bot.refresh_from_db()
        assert bot.name == 'Renamed'
        assert bot.client_secret == 'keep-me'

    async def test_update_rewrites_secret_when_supplied(self, super_admin, db):
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='old', name='A',
        )
        await service.update_bot(super_admin, bot.id, client_secret='new')
        await bot.refresh_from_db()
        assert bot.client_secret == 'new'

    async def test_update_audit_never_logs_secret_value(self, super_admin, db):
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='old', name='A',
        )
        await service.update_bot(super_admin, bot.id, client_secret='hunter2')
        log = await AuditLog.filter(action=AuditActions.RACETIME_BOT_UPDATED).first()
        assert 'hunter2' not in (log.details or '')
        assert 'client_secret' in (log.details or '')


class TestGrants:
    async def test_grant_and_authorized_read(self, super_admin, tenants, db):
        a, b = tenants
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='s', name='A',
        )
        await service.grant_tenant(super_admin, bot.id, a.id)

        assert [x.id for x in await service.list_authorized_for_tenant(a.id)] == [bot.id]
        assert await service.list_authorized_for_tenant(b.id) == []
        assert await service.is_authorized_for_tenant(bot.id, a.id) is True
        assert await service.is_authorized_for_tenant(bot.id, b.id) is False
        assert await AuditLog.filter(action=AuditActions.RACETIME_BOT_GRANTED).count() == 1

    async def test_grant_is_idempotent_and_revoke_hides(self, super_admin, tenants, db):
        a, _ = tenants
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='s', name='A',
        )
        await service.grant_tenant(super_admin, bot.id, a.id)
        await service.grant_tenant(super_admin, bot.id, a.id)
        assert len(await service.list_grants(super_admin, bot.id)) == 1

        await service.revoke_tenant(super_admin, bot.id, a.id)
        assert await service.list_authorized_for_tenant(a.id) == []

    async def test_inactive_bot_not_authorized(self, super_admin, tenants, db):
        a, _ = tenants
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='s', name='A',
        )
        await service.grant_tenant(super_admin, bot.id, a.id)
        await service.update_bot(super_admin, bot.id, is_active=False)
        assert await service.list_authorized_for_tenant(a.id) == []


class TestTenantSelection:
    async def test_tournament_rejects_ungranted_bot(self, super_admin, tenants, db):
        from application.services import TournamentService

        a, b = tenants
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='s', name='A',
        )
        await service.grant_tenant(super_admin, bot.id, a.id)
        staff = await User.create(discord_id=2000, username='staff')
        await UserRole.create(user=staff, role=Role.STAFF, tenant=b)
        with tenant_scope(b.id):
            with pytest.raises(ValueError):
                await TournamentService().create_tournament(
                    name='B Cup', racetime_bot_id=bot.id, actor=staff,
                )

    async def test_tournament_accepts_granted_bot(self, super_admin, tenants, db):
        from application.services import TournamentService

        a, _ = tenants
        service = RacetimeBotService()
        bot = await service.create_bot(
            super_admin, category='alttpr', client_id='c', client_secret='s', name='A',
        )
        await service.grant_tenant(super_admin, bot.id, a.id)
        staff = await User.create(discord_id=2001, username='staff-a')
        await UserRole.create(user=staff, role=Role.STAFF, tenant=a)
        with tenant_scope(a.id):
            t = await TournamentService().create_tournament(
                name='A Cup', racetime_bot_id=bot.id,
                racetime_auto_create_rooms=True, racetime_default_goal='beat the game',
                actor=staff,
            )
        assert t.racetime_bot_id == bot.id
        assert t.racetime_auto_create_rooms is True
        assert t.racetime_default_goal == 'beat the game'
