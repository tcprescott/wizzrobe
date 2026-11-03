"""Tests for RandomizerCredentialService — a community's own randomizer keys.

CRUD + the management gate + the secrecy contract: the stored value must never
appear in a status listing or an audit entry. The management gate is exercised
via the system user (``is_system`` short-circuits ``can_manage_presets``), the
same shortcut ``test_preset_service.py`` uses.
"""

import json

import pytest

from application.services.randomizer_credential_service import RandomizerCredentialService
from application.services.seedgen_service import SeedGenerationService
from application.services.user_service import UserService
from models import AuditLog, RandomizerCredential, User

_SECRET = 'zzz-community-issued-value-zzz'


@pytest.fixture
async def actor(db):
    """A management-authorized actor (the reserved system user)."""
    return await UserService().get_system_user()


@pytest.fixture
def service():
    return RandomizerCredentialService()


class TestSetCredential:
    async def test_set_round_trips(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', _SECRET)
        assert await service.resolve('ootr', 'api_key') == _SECRET

    async def test_set_replaces_rather_than_duplicating(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', 'first')
        await service.set_credential(actor, 'ootr', 'api_key', 'second')
        assert await RandomizerCredential.filter(randomizer='ootr', key='api_key').count() == 1
        assert await service.resolve('ootr', 'api_key') == 'second'

    async def test_value_is_stripped(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', f'  {_SECRET}\n')
        assert await service.resolve('ootr', 'api_key') == _SECRET

    async def test_blank_value_raises(self, service, actor):
        with pytest.raises(ValueError, match='value is required'):
            await service.set_credential(actor, 'ootr', 'api_key', '   ')

    async def test_unknown_credential_raises(self, service, actor):
        with pytest.raises(ValueError, match='Unknown randomizer credential'):
            await service.set_credential(actor, 'alttpr', 'api_key', 'x')

    async def test_requires_preset_management(self, service, db):
        plain = await User.create(discord_id=778001, username='nobody')
        with pytest.raises(PermissionError):
            await service.set_credential(plain, 'ootr', 'api_key', 'x')


class TestClearCredential:
    async def test_clear_removes_the_row(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', _SECRET)
        await service.clear_credential(actor, 'ootr', 'api_key')
        assert await service.resolve('ootr', 'api_key') is None

    async def test_clearing_an_unset_credential_is_a_no_op(self, service, actor):
        await service.clear_credential(actor, 'ootr', 'api_key')
        assert not await AuditLog.filter(action='randomizer_credential.cleared').exists()

    async def test_requires_preset_management(self, service, db):
        plain = await User.create(discord_id=778002, username='nobody2')
        with pytest.raises(PermissionError):
            await service.clear_credential(plain, 'ootr', 'api_key')


class TestListStatus:
    async def test_lists_every_declared_credential(self, service, actor):
        rows = await service.list_status(actor)
        assert {(r['randomizer'], r['key']) for r in rows} == {
            ('ootr', 'api_key'), ('smmap', 'spoiler_token'), ('dk64r', 'api_key'),
        }
        assert all(r['configured'] is False for r in rows)

    async def test_configured_flips_once_set(self, service, actor):
        await service.set_credential(actor, 'dk64r', 'api_key', _SECRET)
        rows = {(r['randomizer'], r['key']): r for r in await service.list_status(actor)}
        assert rows[('dk64r', 'api_key')]['configured'] is True
        assert rows[('ootr', 'api_key')]['configured'] is False

    async def test_never_exposes_the_stored_value(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', _SECRET)
        rendered = json.dumps(await service.list_status(actor), default=str)
        assert _SECRET not in rendered

    async def test_requires_preset_management(self, service, db):
        plain = await User.create(discord_id=778003, username='nobody3')
        with pytest.raises(PermissionError):
            await service.list_status(plain)


class TestAuditNeverCarriesTheSecret:
    async def test_set_and_clear_audit_only_the_credential_name(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', _SECRET)
        await service.clear_credential(actor, 'ootr', 'api_key')
        entries = await AuditLog.filter(action__startswith='randomizer_credential.')
        assert {e.action for e in entries} == {
            'randomizer_credential.set', 'randomizer_credential.cleared',
        }
        for entry in entries:
            assert _SECRET not in entry.details
            details = json.loads(entry.details)
            assert details['randomizer'] == 'ootr'
            assert details['key'] == 'api_key'


class TestConfiguredRandomizers:
    async def test_empty_without_credentials(self, service, actor):
        assert await service.configured_randomizers() == set()

    async def test_reports_only_fully_configured_randomizers(self, service, actor):
        await service.set_credential(actor, 'smmap', 'spoiler_token', _SECRET)
        assert await service.configured_randomizers() == {'smmap'}

    async def test_drives_the_selector_filter(self, service, actor):
        await service.set_credential(actor, 'ootr', 'api_key', _SECRET)
        available = SeedGenerationService.available_randomizers(
            await service.configured_randomizers()
        )
        assert 'ootr' in available
        assert 'dk64r' not in available and 'smmap' not in available
