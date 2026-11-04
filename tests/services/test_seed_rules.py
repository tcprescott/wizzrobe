"""The pure seed-rule engine: checks on write, the pick at roll time, the text view.

What has to hold: the first rule that fits decides, a weighted roll follows its
weights, tweaks change a copy of the settings and never the preset, a bad rule
is refused with its number, and the YAML text view round-trips.
"""

import random
from collections import Counter
from datetime import datetime, timezone

import pytest

from application.services import seed_rules as sr
from application.services.seed_rules import MatchFacts

PRESETS = {1: 'Open', 2: 'Standard', 3: 'Inverted', 4: 'Hard'}
BY_NAME = {v: k for k, v in PRESETS.items()}


def _rules():
    return sr.clean_rules([
        {'name': 'Decider', 'when': {'round': ['w3', 'gf'], 'game': ['last']},
         'then': {'preset': 2}},
        {'name': 'Grand final mystery', 'when': {'round': 'gf'},
         'then': {'weighted': [{'preset': 1, 'weight': 50}, {'preset': 2, 'weight': 30},
                               {'preset': 3, 'weight': 20}]},
         'tweaks': {'hints': 'off'}},
        {'name': 'Game 2 swords', 'when': {'game': [2]}, 'then': {'preset': 2},
         'tweaks': {'item.swords': 'assured'}},
    ], PRESETS)


class TestClean:
    def test_normalises_and_keeps_order(self):
        rules = _rules()
        assert [r['name'] for r in rules] == ['Decider', 'Grand final mystery', 'Game 2 swords']
        assert rules[1]['when'] == {'round': ['gf']}
        assert all(len(r['id']) == 6 for r in rules)

    def test_keeps_a_rule_id_across_saves(self):
        first = _rules()
        again = sr.clean_rules(list(reversed(first)), PRESETS)
        assert [r['id'] for r in again] == [r['id'] for r in reversed(first)]

    def test_empty_is_no_rules(self):
        assert sr.clean_rules(None, PRESETS) == []
        assert sr.clean_rules([], PRESETS) == []

    @pytest.mark.parametrize('rule, message', [
        ({'then': {'preset': 99}}, "Rule 1: names a preset"),
        ({'then': {}}, 'Rule 1: needs an outcome'),
        ({'when': {'colour': 'red'}, 'then': {'preset': 1}}, "unknown condition 'colour'"),
        ({'when': {'game': [0]}, 'then': {'preset': 1}}, 'game numbers 1 to 9'),
        ({'then': {'weighted': [{'preset': 1, 'weight': 0}, {'preset': 2, 'weight': 0}]}},
         'at least one weight'),
        ({'then': {'weighted': [{'preset': 1, 'weight': 5}]}}, '2 to 16 presets'),
        ({'then': {'preset': 1}, 'tweaks': {'a..b': 1}}, 'not a setting path'),
        ({'when': {'from': '2026-11-20T10:00:00'}, 'then': {'preset': 1}}, 'needs a timezone'),
        ({'when': {'from': '2026-11-21T00:00:00+00:00', 'to': '2026-11-20T00:00:00+00:00'},
          'then': {'preset': 1}}, 'before "to"'),
    ])
    def test_refuses_a_bad_rule_by_number(self, rule, message):
        with pytest.raises(ValueError, match=message):
            sr.clean_rules([rule], PRESETS)

    def test_names_the_second_rule(self):
        with pytest.raises(ValueError, match=r'^Rule 2:'):
            sr.clean_rules([{'then': {'preset': 1}}, {'then': {'preset': 42}}], PRESETS)


class TestResolve:
    def test_first_rule_that_fits_wins(self):
        hit = sr.resolve(_rules(), MatchFacts(round='gf', game=3, best_of=3))
        assert (hit.name, hit.preset_id, hit.index) == ('Decider', 2, 0)

    def test_falls_through_to_a_later_rule(self):
        hit = sr.resolve(_rules(), MatchFacts(round='gf', game=1, best_of=3), rng=random.Random(1))
        assert hit.name == 'Grand final mystery'
        assert hit.draw['picked'] in (1, 2, 3)
        assert hit.tweaks == {'hints': 'off'}

    def test_no_rule_fits(self):
        assert sr.resolve(_rules(), MatchFacts(round='w1', game=1, best_of=3)) is None
        assert sr.resolve(_rules(), MatchFacts()) is None

    def test_a_game_condition_never_fits_a_single_game(self):
        rules = sr.clean_rules([{'when': {'game': ['last']}, 'then': {'preset': 1}}], PRESETS)
        assert sr.resolve(rules, MatchFacts(game=None)) is None

    def test_weighted_draw_follows_its_weights(self):
        rules = sr.clean_rules([{'then': {'weighted': [
            {'preset': 1, 'weight': 75}, {'preset': 2, 'weight': 25}, {'preset': 3, 'weight': 0},
        ]}}], PRESETS)
        rng = random.Random(7)
        counts = Counter(sr.resolve(rules, MatchFacts(), rng=rng).preset_id for _ in range(4000))
        assert counts[3] == 0
        assert 0.71 < counts[1] / 4000 < 0.79

    def test_skips_a_rule_whose_preset_is_gone(self):
        rules = sr.clean_rules([
            {'name': 'Gone', 'then': {'preset': 4}},
            {'name': 'Still here', 'then': {'preset': 1}},
        ], PRESETS)
        hit = sr.resolve(rules, MatchFacts(), usable=lambda pid: pid != 4)
        assert hit.name == 'Still here'

    def test_title_stage_and_dates(self):
        rules = sr.clean_rules([{'when': {
            'title': ['Final'], 'on_stage': True,
            'from': '2026-11-20T00:00:00+00:00', 'to': '2026-11-23T00:00:00+00:00',
        }, 'then': {'preset': 3}}], PRESETS)
        inside = datetime(2026, 11, 21, 18, tzinfo=timezone.utc)
        assert sr.resolve(rules, MatchFacts(title='Grand FINAL', on_stage=True, scheduled_at=inside))
        assert not sr.resolve(rules, MatchFacts(title='Grand final', on_stage=False, scheduled_at=inside))
        assert not sr.resolve(rules, MatchFacts(
            title='Final', on_stage=True, scheduled_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
        ))

    def test_record_says_why(self):
        hit = sr.resolve(_rules(), MatchFacts(game=2, best_of=3))
        record = hit.record()
        assert record['source'] == 'rule'
        assert record['rule_number'] == 3
        assert record['tweaks'] == {'item.swords': 'assured'}


class TestTweaks:
    def test_sets_nested_paths_on_a_copy(self):
        settings = {'item': {'pool': 'normal'}, 'mode': 'open'}
        out = sr.apply_tweaks(settings, {'item.swords': 'assured', 'mode': 'standard'})
        assert out == {'item': {'pool': 'normal', 'swords': 'assured'}, 'mode': 'standard'}
        assert settings == {'item': {'pool': 'normal'}, 'mode': 'open'}

    def test_no_tweaks_is_the_same_object(self):
        settings = {'a': 1}
        assert sr.apply_tweaks(settings, {}) is settings


class TestText:
    def test_round_trips(self):
        rules = _rules()
        text = sr.to_yaml(rules, PRESETS)
        assert 'Grand final mystery' in text and 'Standard: 30' in text
        back = sr.clean_rules(sr.from_yaml(text, BY_NAME), PRESETS)
        strip = [{k: v for k, v in r.items() if k != 'id'} for r in rules]
        assert [{k: v for k, v in r.items() if k != 'id'} for r in back] == strip

    def test_dates_round_trip(self):
        rules = sr.clean_rules([{'when': {'from': '2026-11-20T10:00:00+00:00'}, 'then': {'preset': 1}}], PRESETS)
        back = sr.clean_rules(sr.from_yaml(sr.to_yaml(rules, PRESETS), BY_NAME), PRESETS)
        assert back[0]['when'] == rules[0]['when']

    def test_bad_yaml_names_the_line(self):
        with pytest.raises(ValueError, match=r'^Line 3 of the text'):
            sr.from_yaml('rules:\n  - name: a\n   then: x\n', BY_NAME)

    def test_unknown_preset_name(self):
        with pytest.raises(ValueError, match="no preset named 'Nope'"):
            sr.from_yaml('rules:\n  - then: {preset: Nope}\n', BY_NAME)

    def test_empty_text_is_no_rules(self):
        assert sr.from_yaml('', BY_NAME) == []
