"""Tests for PresetService (PR 1) — tenant-authored seed-rolling presets.

CRUD + validation + built-in import. The management gate is exercised via the
system user (``is_system`` short-circuits ``can_manage_presets``), keeping these
tests free of per-tenant role setup — the gate itself is covered in
``test_auth_service.py``.
"""

import json

import pytest

from application.services.preset_service import PresetService
from application.services.user_service import UserService
from application.tenant_context import require_tenant_id
from models import Preset, Tournament


@pytest.fixture
async def actor(db):
    """A management-authorized actor (the reserved system user)."""
    return await UserService().get_system_user()


@pytest.fixture
def service():
    return PresetService()


class TestCreatePreset:
    async def test_create_round_trips(self, service, actor):
        preset = await service.create_preset(
            actor, name='Open', randomizer='alttpr',
            settings={'mode': 'open'}, description='Open mode',
        )
        assert preset.id is not None
        assert preset.name == 'Open'
        assert preset.randomizer == 'alttpr'
        assert preset.settings == {'mode': 'open'}
        assert preset.description == 'Open mode'

    async def test_blank_name_raises(self, service, actor):
        with pytest.raises(ValueError, match='name is required'):
            await service.create_preset(actor, name='  ', randomizer='alttpr', settings={})

    async def test_unknown_randomizer_raises(self, service, actor):
        with pytest.raises(ValueError, match='Unknown randomizer'):
            await service.create_preset(actor, name='X', randomizer='nope', settings={})

    async def test_non_dict_settings_raises(self, service, actor):
        with pytest.raises(ValueError, match='must be a JSON object'):
            await service.create_preset(actor, name='X', randomizer='alttpr', settings=['nope'])

    async def test_duplicate_natural_key_raises(self, service, actor):
        await service.create_preset(actor, name='Dup', randomizer='alttpr', settings={})
        with pytest.raises(ValueError, match='already exists'):
            await service.create_preset(actor, name='Dup', randomizer='alttpr', settings={})

    async def test_same_name_different_randomizer_ok(self, service, actor):
        await service.create_preset(actor, name='Race', randomizer='alttpr', settings={})
        other = await service.create_preset(actor, name='Race', randomizer='ootr', settings={})
        assert other.id is not None


class TestUpdatePreset:
    async def test_update_changes_fields(self, service, actor):
        preset = await service.create_preset(actor, name='A', randomizer='alttpr', settings={'x': 1})
        updated = await service.update_preset(
            actor, preset.id, name='B', settings={'y': 2}, description='new',
        )
        assert updated.name == 'B'
        assert updated.settings == {'y': 2}
        assert updated.description == 'new'
        assert updated.randomizer == 'alttpr'

    async def test_update_missing_raises(self, service, actor):
        with pytest.raises(ValueError, match='Preset not found'):
            await service.update_preset(actor, 999999, name='X')

    async def test_update_to_existing_natural_key_raises(self, service, actor):
        await service.create_preset(actor, name='One', randomizer='alttpr', settings={})
        two = await service.create_preset(actor, name='Two', randomizer='alttpr', settings={})
        with pytest.raises(ValueError, match='already exists'):
            await service.update_preset(actor, two.id, name='One')


class TestDeletePreset:
    async def test_delete_removes_row(self, service, actor):
        preset = await service.create_preset(actor, name='Gone', randomizer='alttpr', settings={})
        await service.delete_preset(actor, preset.id)
        assert await Preset.filter(id=preset.id).count() == 0

    async def test_delete_missing_raises(self, service, actor):
        with pytest.raises(ValueError, match='Preset not found'):
            await service.delete_preset(actor, 999999)


class TestImportBuiltins:
    async def test_imports_committed_files(self, service, actor):
        created = await service.import_builtins(actor)
        names = {(p.randomizer, p.name) for p in created}
        assert ('alttpr', 'casualboots') in names
        assert ('alttpr', 'sglive2025') in names
        assert ('alttpr', 'openboots') in names
        assert ('alttpr', 'hmg') in names

    async def test_alttpr_import_stores_settings_subtree(self, service, actor):
        await service.import_builtins(actor)
        casual = await service.repository.get_by_natural_key('alttpr', 'casualboots')
        assert casual is not None
        assert isinstance(casual.settings, dict)
        assert 'goal' in casual.settings
        assert 'settings' not in casual.settings

    async def test_a_txt_beside_a_file_supplies_its_description(self, service, actor):
        await service.import_builtins(actor)
        hmg = await service.repository.get_by_natural_key('alttpr', 'hmg')
        ootr = await service.repository.get_by_natural_key('ootr', 'sgl2026')
        assert hmg.description.startswith('SGL 2026 HMG')
        assert ootr.description.startswith('SGL 2026 Tournament')
        assert 'description' not in ootr.settings

    async def test_a_file_description_beats_the_txt(self, service, actor):
        await service.import_builtins(actor)
        openboots = await service.repository.get_by_natural_key('alttpr', 'openboots')
        assert openboots.description == 'You get to start with Pegasus Boots!'

    async def test_import_is_idempotent(self, service, actor):
        first = await service.import_builtins(actor)
        assert first
        second = await service.import_builtins(actor)
        assert second == []


class TestImportRemotePresets:
    """Importing a randomizer's own published presets.

    Driven through the DK64R mock queue (``MOCK_SEEDGEN`` + ``mock_dk64``), which
    serves ``get_presets`` without a credential — the same path a dev sandbox
    takes.
    """

    @pytest.fixture(autouse=True)
    def _mock_env(self, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', 'development')
        monkeypatch.setenv('MOCK_SEEDGEN', 'true')

    async def test_lists_the_upstream_catalogue(self, service, actor):
        catalogue = await service.list_remote_presets(actor, 'dk64r', branch='stable')
        assert catalogue
        entry = next(p for p in catalogue if p.name == 'Season 5 Race Settings')
        assert entry.settings['_branch'] == 'stable'
        assert entry.settings['settings_string']

    async def test_dev_branch_has_its_own_catalogue(self, service, actor):
        stable = {p.name for p in await service.list_remote_presets(actor, 'dk64r', branch='stable')}
        dev = {p.name for p in await service.list_remote_presets(actor, 'dk64r', branch='dev')}
        assert dev - stable

    async def test_randomizer_without_a_catalogue_raises(self, service, actor):
        with pytest.raises(ValueError, match='does not publish a preset catalogue'):
            await service.list_remote_presets(actor, 'alttpr')

    async def test_import_saves_the_chosen_presets(self, service, actor):
        created = await service.import_remote_presets(
            actor, 'dk64r', ['Season 5 Race Settings', 'Hell Mode'], branch='stable',
        )
        assert {p.name for p in created} == {'Season 5 Race Settings', 'Hell Mode'}
        stored = await service.repository.get_by_natural_key('dk64r', 'Hell Mode')
        assert stored is not None
        assert stored.settings['_branch'] == 'stable'
        assert stored.description

    async def test_import_is_idempotent(self, service, actor):
        await service.import_remote_presets(actor, 'dk64r', ['Hell Mode'])
        assert await service.import_remote_presets(actor, 'dk64r', ['Hell Mode']) == []

    async def test_unknown_preset_name_raises(self, service, actor):
        with pytest.raises(ValueError, match='no longer offers'):
            await service.import_remote_presets(actor, 'dk64r', ['Not A Preset'])

    async def test_empty_selection_raises(self, service, actor):
        with pytest.raises(ValueError, match='at least one preset'):
            await service.import_remote_presets(actor, 'dk64r', ['  '])

    async def test_unknown_branch_raises(self, service, actor):
        with pytest.raises(ValueError, match='Unknown DK64R branch'):
            await service.list_remote_presets(actor, 'dk64r', branch='nightly')

    async def test_non_manager_cannot_import(self, service, db):
        from models import User

        user = await User.create(discord_id=556, username='nobody-import')
        with pytest.raises(PermissionError, match='manage presets'):
            await service.import_remote_presets(user, 'dk64r', ['Hell Mode'])


class TestManagementGate:
    async def test_non_manager_cannot_create(self, service, db):
        from models import User
        user = await User.create(discord_id=555, username='nobody')
        with pytest.raises(PermissionError, match='manage presets'):
            await service.create_preset(user, name='X', randomizer='alttpr', settings={})


class TestTestRoll:
    @pytest.fixture(autouse=True)
    def _mock_env(self, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', 'development')
        monkeypatch.setenv('MOCK_SEEDGEN', 'true')

    async def _audits(self):
        from application.services.audit_service import AuditActions
        from models import AuditLog

        return [
            json.loads(row.details)
            for row in await AuditLog.filter(action=AuditActions.PRESET_TEST_ROLLED)
        ]

    async def test_rolls_a_seed_and_audits_it_without_storing_a_match_seed(self, service, actor):
        from models import GeneratedSeeds

        preset = await service.create_preset(actor, name='Open', randomizer='alttpr', settings={})
        result = await service.test_roll(actor, preset.id)

        assert result.seed_url.startswith('https://mock.seedgen.local/alttpr/')
        assert result.used_preset_settings is True
        [entry] = await self._audits()
        assert entry['ok'] is True
        assert entry['seed_url'] == result.seed_url
        assert entry['preset_id'] == preset.id
        assert await GeneratedSeeds.all().count() == 0

    async def test_flags_a_randomizer_that_ignores_the_preset(self, service, actor):
        preset = await service.create_preset(actor, name='SGL', randomizer='ootr', settings={})
        result = await service.test_roll(actor, preset.id)
        assert result.used_preset_settings is False

    async def test_a_failed_roll_is_audited_and_raised(self, service, actor, monkeypatch):
        from application.services.seedgen_service import SeedGenerationService

        async def boom(self, *args, **kwargs):
            raise ValueError('upstream is down')

        monkeypatch.setattr(SeedGenerationService, 'generate_seed_call', boom)
        preset = await service.create_preset(actor, name='Open', randomizer='alttpr', settings={})
        with pytest.raises(ValueError, match='upstream is down'):
            await service.test_roll(actor, preset.id)
        [entry] = await self._audits()
        assert entry['ok'] is False
        assert entry['error'] == 'upstream is down'

    async def test_non_manager_cannot_test_roll(self, service, actor, db):
        from models import User

        preset = await service.create_preset(actor, name='Open', randomizer='alttpr', settings={})
        user = await User.create(discord_id=557, username='nobody-roll')
        with pytest.raises(PermissionError, match='manage presets'):
            await service.test_roll(user, preset.id)
        assert await self._audits() == []

    async def test_missing_preset_raises(self, service, actor):
        with pytest.raises(ValueError):
            await service.test_roll(actor, 999999)


class TestWindWakerPresets:
    """A ``wwr`` preset is one permalink, checked when it's saved, not at the desk."""

    SGL26 = (
        'eJxLSS2LL0nMy8o31jPUMzTQM9czjk8xNkgxMk9mcGS4//1Tc+mHA/HnJOY/kNiv9INfkeHfX/4/wgYMDA5PT/3n/K/J/ceehUGg4Ub9f3v/'
        'nE32d0rsjf//L663/v+fg5UBBBy8liYysDEJKDRdnfGzJYFHgIGJkYkFIsUgzODAWMCgxMMBAMVjLfw='
    )

    async def test_create_stores_the_trimmed_permalink(self, service, actor):
        preset = await service.create_preset(
            actor, name='SGL', randomizer='wwr', settings={'permalink': f'  {self.SGL26}\n'},
        )
        assert preset.settings == {'permalink': self.SGL26}

    async def test_create_refuses_a_broken_permalink(self, service, actor):
        with pytest.raises(ValueError, match="isn't a Wind Waker permalink"):
            await service.create_preset(actor, name='SGL', randomizer='wwr', settings={'permalink': 'garbage!!'})
        assert await Preset.filter(randomizer='wwr').count() == 0

    async def test_update_refuses_a_broken_permalink(self, service, actor):
        preset = await service.create_preset(actor, name='SGL', randomizer='wwr', settings={'permalink': self.SGL26})
        with pytest.raises(ValueError, match='needs a "permalink"'):
            await service.update_preset(actor, preset.id, settings={})
        await preset.refresh_from_db()
        assert preset.settings == {'permalink': self.SGL26}

    async def test_a_rename_leaves_old_settings_unjudged(self, service, actor):
        stale = await Preset.create(name='Old', randomizer='wwr', settings={})
        renamed = await service.update_preset(actor, stale.id, name='Older')
        assert (renamed.name, renamed.settings) == ('Older', {})

    async def test_switching_a_preset_to_wwr_checks_its_settings(self, service, actor):
        preset = await service.create_preset(actor, name='Was ALttPR', randomizer='alttpr', settings={'mode': 'open'})
        with pytest.raises(ValueError, match='needs a "permalink"'):
            await service.update_preset(actor, preset.id, randomizer='wwr')

    async def test_the_sgl_permalink_is_a_builtin(self, service, actor):
        created = await service.import_builtins(actor)
        [sgl] = [p for p in created if (p.randomizer, p.name) == ('wwr', 'sgl2026')]
        assert sgl.settings == {'permalink': self.SGL26}
        assert 'dev_tanjo3.1.10.7.3' in sgl.description

    async def test_test_roll_returns_a_permalink_for_the_same_build(self, service, actor, monkeypatch):
        from application.services._seedgen_wwr import decode_permalink

        monkeypatch.delenv('MOCK_SEEDGEN', raising=False)
        preset = await service.create_preset(actor, name='SGL', randomizer='wwr', settings={'permalink': self.SGL26})
        result = await service.test_roll(actor, preset.id)
        assert result.used_preset_settings is True
        assert decode_permalink(result.seed_url).version == 'dev_tanjo3.1.10.7.3_d30d27c'


class TestUsage:
    async def test_counts_main_and_hard_presets(self, service, actor):
        main = await service.create_preset(actor, name='Main', randomizer='alttpr', settings={})
        hard = await service.create_preset(actor, name='Hard', randomizer='alttpr', settings={})
        unused = await service.create_preset(actor, name='Spare', randomizer='alttpr', settings={})
        tenant_id = require_tenant_id()
        await Tournament.create(name='Weekly', tenant_id=tenant_id, preset=main)
        await Tournament.create(name='Finals', tenant_id=tenant_id, preset=main, hard_preset=hard)

        usage = await service.usage(actor)

        assert usage[main.id] == ['Finals', 'Weekly']
        assert usage[hard.id] == ['Finals']
        assert unused.id not in usage
