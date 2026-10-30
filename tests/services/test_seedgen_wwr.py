"""Tests for the Wind Waker Randomizer backend (unit — no network, no wwrando checkout)."""

import base64
import random
import zlib

import pytest

from application.services import _seedgen_wwr as wwr
from application.services.seedgen_service import SeedGenerationService
from application.utils.seed_provider import SeedProviderInvalidRequest
from models import Preset

SGL26 = (
    'eJxLSS2LL0nMy8o31jPUMzTQM9czjk8xNkgxMk9mcGS4//1Tc+mHA/HnJOY/kNiv9INfkeHfX/4/wgYMDA5PT/3n/K/J/ceehUGg4Ub9f3v/'
    'nE32d0rsjf//L663/v+fg5UBBBy8liYysDEJKDRdnfGzJYFHgIGJkYkFIsUgzODAWMCgxMMBAMVjLfw='
)
# Season 6 on the 1.10.0 release, which base64s without compressing.
S6_1100 = 'MS4xMC4wAEEAFwMiAHPowgMMsACCcQ8AAMkHAAAA'


@pytest.fixture
def service():
    return SeedGenerationService()


def _preset(permalink):
    return Preset(name='SGL', randomizer='wwr', settings={'permalink': permalink})


class TestPermalinkFormat:
    @pytest.mark.parametrize('permalink', [SGL26, S6_1100])
    def test_round_trips_byte_for_byte(self, permalink):
        assert wwr.encode_permalink(wwr.decode_permalink(permalink)) == permalink

    def test_reads_the_sgl_build(self):
        parsed = wwr.decode_permalink(SGL26)
        assert parsed.version == 'dev_tanjo3.1.10.7.3_d30d27c'
        assert parsed.build == 'dev_tanjo3.1.10.7.3'
        assert parsed.seed == 'A'
        assert parsed.compressed is True
        assert len(parsed.option_bytes) == 105

    def test_a_release_version_has_no_commit_to_strip(self):
        parsed = wwr.decode_permalink(S6_1100)
        assert (parsed.version, parsed.build, parsed.compressed) == ('1.10.0', '1.10.0', False)

    def test_reseed_changes_only_the_seed(self):
        before = wwr.decode_permalink(SGL26)
        after = wwr.decode_permalink(wwr.reseed(SGL26, 'DirtySkilledGold'))
        assert after == before._replace(seed='DirtySkilledGold')

    def test_reseed_keeps_an_uncompressed_build_uncompressed(self):
        assert wwr.reseed(S6_1100, 'Fresh').startswith('MS4xMC4w')

    def test_reseed_sanitizes_like_the_randomizer(self):
        rolled = wwr.decode_permalink(wwr.reseed(SGL26, ' bad seed!/' + 'x' * 60))
        assert rolled.seed == ('badseed' + 'x' * 60)[:42]

    def test_reseed_refuses_an_empty_seed(self):
        with pytest.raises(ValueError):
            wwr.reseed(SGL26, '!!!')

    @pytest.mark.parametrize('bad', [
        '', '   ', 'not base64!!', 'aGVsbG8=',
        base64.b64encode(zlib.compress(b'no separators')).decode(),
        base64.b64encode(b'1.0\0A\0').decode(),
        base64.b64encode(b'\0A\0\x01').decode(),
    ])
    def test_rejects_what_isnt_a_permalink(self, bad):
        with pytest.raises(ValueError):
            wwr.decode_permalink(bad)

    def test_tolerates_surrounding_whitespace(self):
        assert wwr.decode_permalink(f'  {SGL26}\n').seed == 'A'


# Upstream ``seedgen.make_random_seed_name`` + ``sanitize_seed`` at tanjo3
# d30d27c with ``random.seed(k)`` in place of its ``random.seed(None)``.
UPSTREAM_SEED_NAMES = {
    0: 'MeaslyWondrousHollo', 1: 'DirtySkilledGold', 2: 'BreathtakingCorrectLink',
    3: 'FrigidSplendidLuck', 7: 'InsaneDumbBisht', 42: 'TenDashingHall',
    1234: 'OldDecentChamber', 99999: 'DeepImpossibleLetter',
}


class TestSeedName:
    @pytest.mark.parametrize('k', sorted(UPSTREAM_SEED_NAMES))
    def test_matches_the_randomizer_for_the_same_rng(self, k):
        assert wwr.make_seed_name(random.Random(k)) == UPSTREAM_SEED_NAMES[k]

    def test_names_are_valid_seeds(self):
        rng = random.Random(5)
        for _ in range(500):
            name = wwr.make_seed_name(rng)
            assert 0 < len(name) <= wwr.WWR_MAX_SEED_LENGTH
            assert set(name) <= wwr.WWR_SEED_CHARACTERS

    def test_a_build_without_word_lists_borrows_the_default(self):
        assert wwr.make_seed_name(random.Random(1), version='1.10.0') == UPSTREAM_SEED_NAMES[1]

    @pytest.mark.parametrize('version', ['..', '.', '../_wwr_data', '/etc', 'a/b'])
    def test_a_hostile_version_never_leaves_the_data_dir(self, version):
        assert wwr._vendored_dir(version) is None
        assert wwr.make_seed_name(random.Random(1), version=version) == UPSTREAM_SEED_NAMES[1]


class TestSettings:
    def test_decodes_the_sgl_settings(self):
        s = wwr.decode_settings(SGL26)
        assert s['num_required_bosses'] == 3
        assert s['sword_mode'] == 'No Starting Sword'
        assert s['randomize_dungeon_entrances'] is True
        assert s['randomize_starting_island'] is True
        assert s['boss_soul_shuffle'] is True
        assert (s['num_path_hints'], s['num_barren_hints'], s['num_location_hints']) == (3, 5, 6)
        assert s['kreeb_hints_bows'] is True
        assert s['do_not_generate_spoiler_log'] is True
        assert 'Telescope' in s['starting_gear']
        assert len(s['enabled_tricks']) == 6

    def test_summary(self):
        assert wwr.summarize(SGL26) == [
            'No Starting Sword',
            '3 required bosses',
            'Randomized dungeon entrances',
            'Random starting island',
            'Boss souls shuffled',
            'KoRL hints: 3 path, 5 barren, 6 location',
            'Kreeb hints the bows',
            'Small keys: Own Dungeon',
            'Big keys: Own Dungeon',
            'Maps and compasses: Start With',
            '21 of 27 progression categories',
            '6 logic tricks enabled',
            'No spoiler log',
        ]

    def test_a_reseeded_permalink_summarizes_the_same(self):
        assert wwr.summarize(wwr.reseed(SGL26, 'Other')) == wwr.summarize(SGL26)

    def test_an_unvendored_build_has_no_summary(self):
        assert wwr.decode_settings(S6_1100) is None
        assert wwr.summarize(S6_1100) is None

    def test_settings_too_short_for_the_build_are_refused(self):
        parsed = wwr.decode_permalink(SGL26)
        truncated = wwr.encode_permalink(parsed._replace(option_bytes=parsed.option_bytes[:20]))
        with pytest.raises(ValueError, match="don't fit build"):
            wwr.summarize(truncated)


class TestGenerator:
    async def test_rolls_the_preset_with_a_fresh_seed(self, service):
        call = await service.generate_seed_call('wwr', _preset(SGL26))
        rolled = wwr.decode_permalink(call.value.url)
        reference = wwr.decode_permalink(SGL26)
        assert rolled.version == reference.version
        assert rolled.option_bytes == reference.option_bytes
        assert rolled.seed != 'A'
        assert call.value.settings == {
            'reference_permalink': SGL26,
            'version': 'dev_tanjo3.1.10.7.3_d30d27c',
            'seed_name': rolled.seed,
        }

    async def test_two_rolls_differ(self, service):
        assert await service.generate_seed('wwr', _preset(SGL26)) != await service.generate_seed('wwr', _preset(SGL26))

    async def test_no_preset_is_an_actionable_error(self, service):
        with pytest.raises(SeedProviderInvalidRequest, match='needs a preset with a permalink'):
            await service.generate_seed('wwr')

    async def test_a_broken_permalink_is_an_actionable_error(self, service):
        with pytest.raises(SeedProviderInvalidRequest, match="isn't a Wind Waker permalink"):
            await service.generate_seed('wwr', _preset('garbage!!'))

    async def test_mock_mode_still_rolls_a_real_permalink(self, service, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', 'development')
        monkeypatch.setenv('MOCK_SEEDGEN', 'true')
        rolled = await service.generate_seed('wwr', _preset(SGL26))
        assert wwr.decode_permalink(rolled).version == 'dev_tanjo3.1.10.7.3_d30d27c'


class TestPresetSettings:
    def test_normalizes_whitespace(self):
        assert SeedGenerationService.normalize_preset_settings('wwr', {'permalink': f' {SGL26}\n'}) == {'permalink': SGL26}

    @pytest.mark.parametrize('settings', [{}, {'permalink': ''}, {'permalink': 5}, {'permalink': 'nope'}])
    def test_refuses_a_missing_or_broken_permalink(self, settings):
        with pytest.raises(ValueError):
            SeedGenerationService.normalize_preset_settings('wwr', settings)

    def test_refuses_extra_keys(self):
        with pytest.raises(ValueError, match='only a permalink'):
            SeedGenerationService.normalize_preset_settings('wwr', {'permalink': SGL26, 'seed': 'x'})

    def test_other_randomizers_pass_through(self):
        settings = {'anything': 1}
        assert SeedGenerationService.normalize_preset_settings('alttpr', settings) is settings

    def test_describe(self):
        info = SeedGenerationService.describe_wwr_permalink(SGL26)
        assert (info.build, info.version) == ('dev_tanjo3.1.10.7.3', 'dev_tanjo3.1.10.7.3_d30d27c')
        assert info.summary[0] == 'No Starting Sword'
        assert SeedGenerationService.describe_wwr_permalink(S6_1100).summary is None


class TestPasteInstructions:
    def test_wwr_names_the_build(self):
        text = SeedGenerationService.paste_instructions('wwr', {'version': 'dev_tanjo3.1.10.7.3_d30d27c'})
        assert '**dev_tanjo3.1.10.7.3**' in text

    def test_z1r(self):
        assert 'Zelda 1 Randomizer' in SeedGenerationService.paste_instructions('z1r', {'seed': '1', 'flags': 'x'})

    def test_link_randomizers_have_none(self):
        assert SeedGenerationService.paste_instructions('alttpr', {}) is None
