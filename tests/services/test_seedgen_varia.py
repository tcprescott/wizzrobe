"""Tests for the Super Metroid VARIA backend (unit — no network)."""

import base64
import json
import urllib.parse

import pytest

import application.services._seedgen_varia as varia
from application.services._seedgen_codes import VARIA_CODE_NAMES, mock_code, varia_code
from application.services.preset_service import PresetService
from application.services.seedgen_service import SeedGenerationService
from application.utils.seed_provider import SeedProviderBadResponse, SeedProviderInvalidRequest
from models import Preset

SEED_KEY = 'a81e3531-526f-4685-9a78-f30b24b78487'


def _ips(*records: bytes) -> str:
    return base64.b64encode(b'PATCH' + b''.join(records) + b'EOF').decode()


def _record(offset: int, data: bytes) -> bytes:
    return offset.to_bytes(3, 'big') + len(data).to_bytes(2, 'big') + data


def _rle(offset: int, run: int, value: int) -> bytes:
    return offset.to_bytes(3, 'big') + b'\0\0' + run.to_bytes(2, 'big') + bytes([value])


# Bytes from a real Season_Races roll: ZEBBO PUYO BULL VIOLA. The high bits are noise
# the ROM masks off, and the record starts early to check the offset arithmetic.
SEASON_IPS = _ips(_record(0x10, b'\x01\x02'), _record(0x2FFEFE, b'\xaa\xbb\x8a\xb8\xbf\xed\xcc'))


@pytest.fixture
def service(monkeypatch):
    monkeypatch.delenv('MOCK_SEEDGEN', raising=False)
    return SeedGenerationService()


def _preset(settings):
    return Preset(name='p', randomizer='smvaria', settings=settings)


class _FakeResp:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return json.dumps(self._body) if not isinstance(self._body, str) else self._body


class _FakeVaria:
    """Answers each VARIA endpoint by path, recording what was posted."""

    def __init__(self, result):
        self.result = result
        self.calls = []
        self.settings_presets = {
            'default': {'majorsSplit': 'Full', 'preset': 'regular', 'progressionSpeedMultiSelect': ['slow', 'fast']},
            'Season_Races': {'majorsSplit': 'Major', 'preset': 'Season_Races'},
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, **kwargs):
        path = urllib.parse.urlparse(url).path.lstrip('/')
        self.calls.append((path, data))
        if path == 'randoPresetWebService':
            name = data['randoPreset']
            if name not in self.settings_presets:
                return _FakeResp('Rando preset not found', status=400)
            return _FakeResp(dict(self.settings_presets[name]))
        if path == 'presetWebService':
            return _FakeResp({'Knows': {'Mockball': [True, 1]}, 'name': data['preset']})
        return _FakeResp(self.result)


@pytest.fixture
def varia_upstream(monkeypatch):
    import aiohttp

    def _install(result=None):
        fake = _FakeVaria(result if result is not None else {
            'status': 'OK', 'seedKey': SEED_KEY, 'ips': SEASON_IPS, 'errorMsg': '',
            'fileName': 'VARIA_Randomizer_MX1_Season_Races.sfc',
        })
        monkeypatch.setattr(aiohttp, 'ClientSession', lambda *a, **k: fake)
        return fake

    return _install


class TestVariaRoll:
    async def test_rolls_a_race_seed_from_the_named_presets(self, service, varia_upstream):
        upstream = varia_upstream()
        call = await service.generate_seed_call('smvaria', _preset({'settings_preset': 'Season_Races'}))

        assert call.value.url == f'https://randommetroidsolver.pythonanywhere.com/customizer/{SEED_KEY}'
        assert call.value.code == ['ZEBBO', 'PUYO', 'BULL', 'VIOLA']
        assert [path for path, _ in upstream.calls] == [
            'randoPresetWebService', 'randoPresetWebService', 'presetWebService', 'randomizerWebService',
        ]
        sent = upstream.calls[-1][1]
        assert sent['majorsSplit'] == 'Major'
        assert sent['progressionSpeedMultiSelect'] == 'slow,fast'
        assert (sent['raceMode'], sent['seed'], sent['preset']) == ('on', '0', 'Season_Races')
        assert json.loads(sent['paramsFileTarget'])['name'] == 'Season_Races'
        assert 'paramsFileTarget' not in call.value.settings
        assert call.value.settings['skills_preset'] == 'Season_Races'

    async def test_skills_and_overrides_win_over_the_settings_preset(self, service, varia_upstream):
        upstream = varia_upstream()
        await service.generate_seed('smvaria', _preset({
            'settings_preset': 'Season_Races', 'skills_preset': 'expert',
            'overrides': {'areaRandomization': 'light', 'objective': ['kill all G4', 'kill phantoon']},
        }))
        sent = upstream.calls[-1][1]
        assert (sent['preset'], sent['areaRandomization']) == ('expert', 'light')
        assert sent['objective'] == 'kill all G4,kill phantoon'
        assert upstream.calls[2] == ('presetWebService', {'preset': 'expert'})

    async def test_the_default_preset_is_fetched_once(self, service, varia_upstream):
        upstream = varia_upstream()
        await service.generate_seed('smvaria', _preset({'settings_preset': 'default'}))
        assert [path for path, _ in upstream.calls].count('randoPresetWebService') == 1
        assert upstream.calls[-1][1]['preset'] == 'regular'

    def test_rolls_once_because_every_roll_stores_a_seed(self):
        assert SeedGenerationService.PROVIDER_ATTEMPTS['smvaria'] == 1

    async def test_an_unknown_preset_is_refused_with_varias_reason(self, service, varia_upstream):
        varia_upstream()
        with pytest.raises(SeedProviderInvalidRequest, match='Rando preset not found'):
            await service.generate_seed('smvaria', _preset({'settings_preset': 'nosuch'}))

    async def test_a_failed_build_carries_varias_message(self, service, varia_upstream):
        varia_upstream({'status': 'NOK', 'errorMsg': 'Too many objectives<br/>for this split'})
        with pytest.raises(SeedProviderInvalidRequest) as info:
            await service.generate_seed('smvaria', _preset({'settings_preset': 'Season_Races'}))
        assert info.value.provider_message == 'Too many objectives\nfor this split'

    async def test_no_seed_key_means_no_seed(self, service, varia_upstream):
        varia_upstream({'status': 'OK', 'ips': SEASON_IPS})
        with pytest.raises(SeedProviderBadResponse, match='no link'):
            await service.generate_seed('smvaria', _preset({'settings_preset': 'Season_Races'}))

    async def test_a_missing_code_does_not_fail_the_roll(self, service, varia_upstream):
        varia_upstream({'status': 'OK', 'seedKey': SEED_KEY, 'ips': 'not base64!'})
        call = await service.generate_seed_call('smvaria', _preset({'settings_preset': 'Season_Races'}))
        assert call.value.code is None


class TestVariaSettings:
    def test_normalizes_names_and_drops_empty_overrides(self):
        assert SeedGenerationService.normalize_preset_settings(
            'smvaria', {'settings_preset': ' Season_Races ', 'overrides': {}},
        ) == {'settings_preset': 'Season_Races'}

    @pytest.mark.parametrize('settings', [
        None, {}, {'settings_preset': ''}, {'settings_preset': '../x'}, {'settings_preset': 'a b'},
        {'settings_preset': 'x', 'skills_preset': 'bad name'},
        {'settings_preset': 'x', 'extra': 1},
        {'settings_preset': 'x', 'overrides': []},
        {'settings_preset': 'x', 'overrides': {'seed': '42'}},
        {'settings_preset': 'x', 'overrides': {'raceMode': 'off'}},
        {'settings_preset': 'x', 'overrides': {'bad key': 'on'}},
        {'settings_preset': 'x', 'overrides': {'hud': True}},
        {'settings_preset': 'x', 'overrides': {'hud': {'nested': 'on'}}},
    ])
    async def test_refuses_a_bad_preset_before_calling_upstream(self, service, settings):
        with pytest.raises(SeedProviderInvalidRequest):
            await service.generate_seed('smvaria', _preset(settings) if settings is not None else None)

    def test_registered_as_a_preset_aware_randomizer(self):
        assert 'smvaria' in SeedGenerationService.AVAILABLE_RANDOMIZERS
        assert 'smvaria' in SeedGenerationService.PRESET_AWARE_RANDOMIZERS
        assert SeedGenerationService.randomizer_name('smvaria') == 'Super Metroid VARIA'

    def test_built_in_presets_are_valid(self):
        builtins = [b for b in PresetService._load_builtins() if b['randomizer'] == 'smvaria']
        assert {b['name'] for b in builtins} >= {'season_races', 'varia_weekly', 'sgl23online', 'default'}
        for b in builtins:
            SeedGenerationService.normalize_preset_settings('smvaria', b['settings'])


class TestVariaCode:
    def test_reads_the_four_seed_bytes(self):
        assert varia_code(SEASON_IPS) == ['ZEBBO', 'PUYO', 'BULL', 'VIOLA']

    def test_reads_an_rle_record(self):
        assert varia_code(_ips(_rle(0x2FFF00, 4, 0x00))) == ['GEEMER'] * 4

    @pytest.mark.parametrize('ips', [
        None, '', 'not base64!', base64.b64encode(b'NOTIPS').decode(),
        _ips(_record(0x2FFF00, b'\x01\x02')),
        _ips(_record(0x100, b'\x01\x02\x03\x04')),
        base64.b64encode(b'PATCH' + (0x2FFF00).to_bytes(3, 'big') + b'\x00').decode(),
    ])
    def test_anything_else_is_no_code(self, ips):
        assert varia_code(ips) is None

    def test_the_table_is_the_roms(self):
        assert len(VARIA_CODE_NAMES) == 32
        assert VARIA_CODE_NAMES[0] == 'GEEMER' and VARIA_CODE_NAMES[-1] == 'BULL'

    def test_mock_mode_shows_enemy_names(self):
        assert len(mock_code('smvaria')) == 4


def test_module_base_url_is_https():
    assert varia.VARIA_BASE_URL.startswith('https://')
