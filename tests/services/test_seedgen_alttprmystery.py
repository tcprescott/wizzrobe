"""ALTTPR mystery weightsets: the roller ported from SahasrahBot, rolling, and hiding the result."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

import application.services._seedgen_alttprmystery as mystery
from application.services._seedgen_alttprmystery import builtin_preset
from application.services.preset_library_service import PresetLibraryService
from application.services.seedgen_service import SeedGenerationService


def _builtin(name):
    with open(f'presets/alttprmystery/{name}.yaml', encoding='utf-8') as f:
        return yaml.safe_load(f)['settings']


@pytest.fixture(autouse=True)
def _real_rolls(monkeypatch):
    monkeypatch.delenv('MOCK_SEEDGEN', raising=False)


def test_friendly_rolls_within_its_weights():
    weights = _builtin('friendly')
    rolls = [mystery.roll_mystery(weights, builtin_preset) for _ in range(2000)]
    assert not any(r.doors for r in rolls)
    modes = Counter(r.settings['mode'] for r in rolls)
    assert set(modes) <= set(weights['world_state'])
    total = sum(weights['world_state'].values())
    for mode, weight in weights['world_state'].items():
        share = weight / total
        assert abs(modes[mode] / len(rolls) - share) < 0.04, (mode, modes)
    goals = Counter(r.settings['goal'] for r in rolls)
    assert 'triforce-hunt' in goals and goals['triforce-hunt'] < goals['fast_ganon']
    zero = [k for k, v in weights['glitches_required'].items() if v == 0]
    assert not {r.settings['glitches'] for r in rolls} & set(zero)
    assert all(r.settings['spoilers'] == 'mystery' and r.settings['tournament'] for r in rolls)


def test_every_builtin_weightset_rolls():
    import glob
    import os

    for path in glob.glob('presets/alttprmystery/*.yaml'):
        name = os.path.basename(path)[:-5]
        for _ in range(5):
            mystery.roll_mystery(_builtin(name), builtin_preset)


def test_subweights_rules_and_instructions():
    base = _builtin('friendly')
    weights = {
        **base,
        'custom_instructions': {'Race to the pedestal.': 1},
        'subweights': {
            'inverted_only': {'chance': 1, 'weights': {'world_state': {'inverted': 1}}},
        },
    }
    rolled = mystery.roll_mystery(weights, builtin_preset)
    assert rolled.settings['mode'] == 'inverted'
    assert rolled.instructions == 'Race to the pedestal.'


def test_door_settings_roll_on_the_door_randomizer():
    weights = {**_builtin('friendly'), 'door_shuffle': {'basic': 1}, 'shopsanity': {'on': 1}}
    rolled = mystery.roll_mystery(weights, builtin_preset)
    assert rolled.doors and rolled.settings['door_shuffle'] == 'basic' and rolled.settings['shopsanity'] is True
    assert rolled.branch == 'stable'


def test_a_weightset_can_pick_a_builtin_preset():
    rolled = mystery.roll_mystery({'preset': {'openboots': 1}}, builtin_preset)
    assert rolled.preset == 'openboots' and not rolled.doors
    assert rolled.settings['spoilers'] == 'mystery'
    doors = mystery.roll_mystery({'preset': {'crossdoors': 1}}, builtin_preset)
    assert doors.doors and doors.preset == 'crossdoors'
    with pytest.raises(ValueError, match='no built-in preset'):
        mystery.roll_mystery({'preset': {'nope': 1}}, builtin_preset)


@pytest.mark.parametrize('weights, problem', [
    ({}, 'weightset'),
    ([], 'weightset'),
    ({'world_state': {'open': 1}}, 'glitches_required'),
    ({**yaml.safe_load(open('presets/alttprmystery/friendly.yaml'))['settings'], 'hints': {'on': 'lots'}}, "isn't a number"),
    ({'a': {'b': {'c': {'d': {'e': {'f': {'g': {'h': {'i': {'j': {'k': {'l': {'m': 1}}}}}}}}}}}}}, 'nests'),
])
def test_bad_weightsets_say_what_is_wrong(weights, problem):
    with pytest.raises(ValueError, match=problem):
        SeedGenerationService.normalize_preset_settings('alttprmystery', weights)


def test_subweights_that_never_end_are_refused():
    loop = {'subweights': {'again': {'chance': 1, 'weights': {}}}}
    loop['subweights']['again']['weights']['subweights'] = loop['subweights']
    with pytest.raises(ValueError, match='subweights'):
        mystery.roll_mystery({**_builtin('friendly'), **loop}, builtin_preset)


def test_check_reads_yaml_and_gives_odds():
    text = yaml.safe_dump(_builtin('friendly'))
    checked = PresetLibraryService.check_mystery(text)
    rows = {r.setting: dict(r.options) for r in checked.odds}
    assert rows['world_state']['open'] == 35.0
    assert rows['entrance_shuffle'] == {'none': 100.0}
    for bad in ('a: &x [1]\nb: *x', 'x: [', 'x' * (65 * 1024)):
        with pytest.raises(ValueError):
            PresetLibraryService.check_mystery(bad)


async def test_a_mystery_roll_hides_its_settings():
    preset = SimpleNamespace(name='friendly', randomizer='alttprmystery', settings={
        **_builtin('friendly'), 'custom_instructions': {'Good luck.': 1},
    })
    gen = AsyncMock(return_value=SimpleNamespace(url='https://alttpr.com/h/x', data={}))
    with patch('application.services.seedgen_service.ALTTPR.generate', gen):
        call = await SeedGenerationService().generate_seed_call('alttprmystery', preset)
    sent = gen.await_args.kwargs
    assert sent['settings']['spoilers'] == 'mystery'
    snapshot = call.value.settings
    assert snapshot['mystery']['weightset'] == 'friendly' and snapshot['settings'] == sent['settings']
    public = SeedGenerationService.public_settings('alttprmystery', snapshot)
    assert public == {'mystery': {'weightset': 'friendly', 'instructions': 'Good luck.'}}
    assert SeedGenerationService.seed_instructions('alttprmystery', snapshot) == 'Good luck.'
    assert SeedGenerationService.public_settings('alttpr', {'mode': 'open'}) == {'mode': 'open'}
