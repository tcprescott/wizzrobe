"""ALTTPR mystery weightsets, in SahasrahBot's format.

A mystery preset stores a *weightset*: for each setting, the odds of each
value (``world_state: {open: 35, standard: 35, inverted: 20, retro: 10}``).
A roll picks one value per setting and sends the result upstream with the
settings hidden, so players learn the mode by playing it. The format is
SahasrahBot's, so its files import unchanged:

* ``subweights``: named alternatives with a ``chance``, each overlaying its own
  ``weights`` on the base set (picked repeatedly until none is).
* ``customizer``: eq/timed-ohko/... sections pyz3r turns into customizer settings.
* ``rules``: ``conditions`` on rolled values that trigger ``actions``.
* ``custom_instructions``: weighted text shown to players with the seed.
* ``preset``: weighted names of whole presets (Wizzrobe's built-ins), or
  ``none`` to roll the weights instead.
* door, pottery, shopsanity and similar settings: a roll that turns any on goes
  to the Door Randomizer (``options.force_doors`` forces it).

The non-door path is pyz3r's own ``generate_random_settings``, the code
SahasrahBot rolled with; the door path is ported from SahasrahBot's
``mysterydoors`` module.
"""

from __future__ import annotations

import contextlib
import copy
import io
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from pyz3r.mystery import generate_random_settings, get_random_option

MYSTERY_RANDOMIZER = 'alttprmystery'
DRY_ROLLS = 5
MAX_DEPTH = 12
MAX_SUBWEIGHT_STEPS = 32

# (randomizer, settings) of a built-in preset by name, or None.
PresetLookup = Callable[[str], Optional[Tuple[str, Dict[str, Any]]]]


@dataclass
class MysteryRoll:
    """What one roll of a weightset came to."""

    settings: Dict[str, Any]
    customizer: bool = False
    doors: bool = False
    branch: Optional[str] = None
    instructions: Optional[str] = None
    preset: Optional[str] = None


BASE_DOORS_PAYLOAD: Dict[str, Any] = {
    'retro': False,
    'mode': 'open',
    'logic': 'noglitches',
    'goal': 'ganon',
    'crystals_gt': '7',
    'crystals_ganon': '7',
    'swords': 'random',
    'difficulty': 'normal',
    'item_functionality': 'normal',
    'accessibility': 'items',
    'algorithm': 'balanced',
    'openpyramid': False,
    'shuffleganon': True,
    'shuffle': 'vanilla',
    'shufflepots': False,
    'shuffleenemies': 'none',
    'shufflebosses': 'none',
    'enemy_damage': 'default',
    'enemy_health': 'default',
    'keydropshuffle': False,
    'dropshuffle': False,
    'pottery': 'none',
    'colorizepots': False,
    'mapshuffle': False,
    'compassshuffle': False,
    'keyshuffle': False,
    'bigkeyshuffle': False,
    'keysanity': False,
    'timer': 'none',
    'door_shuffle': 'vanilla',
    'intensity': 2,
    'experimental': False,
    'dungeon_counters': 'default',
    'collection_rate': False,
    'bombbag': False,
    'triforce_pool': 0,
    'triforce_goal': 0,
    'restrict_boss_items': 'none',
    'shufflelinks': False,
    'overworld_map': 'default',
    'pseudoboots': False,
    'hints': True,
    'startinventory': '',
    'beemizer': 0,
    'startinventoryarray': {},
}

_SWORDS = {'randomized': 'random', 'assured': 'assured', 'vanilla': 'vanilla', 'swordless': 'swordless'}


def _resolve_subweights(weights: Dict[str, Any]) -> Dict[str, Any]:
    for _ in range(MAX_SUBWEIGHT_STEPS):
        name = get_random_option({k: v['chance'] for (k, v) in weights.get('subweights', {}).items()})
        if name is None:
            return weights
        sub = dict(weights.get('subweights', {}).get(name, {}).get('weights', {}))
        sub['subweights'] = sub.get('subweights', {})
        weights = {**weights, **sub}
    raise ValueError('The weightset\'s subweights keep picking more subweights. Make sure they end.')


def _doors_settings(weights: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    branch = weights.get('options', {}).get('branch', 'stable')

    for key in (
        'dungeon_items', 'accessibility', 'goals', 'ganon_open', 'tower_open', 'world_state', 'hints',
        'weapons', 'item_pool', 'item_functionality', 'boss_shuffle', 'enemy_shuffle', 'enemy_damage',
        'enemy_health', 'entrance_shuffle',
    ):
        options[key] = get_random_option(weights[key])
    options['glitches'] = get_random_option(weights['glitches_required'])
    defaults = {
        'pot_shuffle': 'off', 'algorithm': 'balanced', 'colorizepots': False, 'mapshuffle': False,
        'compassshuffle': False, 'keyshuffle': False, 'bigkeyshuffle': False, 'timer': 'none',
        'experimental': False, 'dungeon_counters': 'default', 'triforce_pool': 0, 'triforce_goal': 0,
        'restrict_boss_items': 'none', 'shufflelinks': False, 'overworld_map': 'default',
        'pseudoboots': False, 'intensity': 2, 'beemizer': 0,
    }
    for key, default in defaults.items():
        options[key] = get_random_option(weights.get(key, default))
    if branch == 'volatile':
        options['keyshuffle'] = get_random_option(weights.get('keyshuffle', 'none'))
        options['dropshuffle'] = get_random_option(weights.get('dropshuffle', 'none'))

    startitems = []
    for item in weights.get('startinventory', {}):
        count = get_random_option(weights['startinventory'][item])
        if count > 0:
            startitems += count * [item]
    limit = get_random_option(weights.get('startinventorylimit', None))
    if limit is not None and len(startitems) > limit:
        startitems = random.sample(startitems, k=limit)
    options['startinventory'] = ','.join(startitems)

    # Swordless standard with shuffled or tougher enemies can't be beaten.
    if options['weapons'] not in ('vanilla', 'assured') and options['world_state'] == 'standard' and (
        options['enemy_shuffle'] != 'none'
        or options['enemy_damage'] != 'default'
        or options['enemy_health'] != 'default'
    ):
        options['weapons'] = 'assured'

    for rule in weights.get('rules', {}):
        match = all(
            options[c['Key']] == c['Value']
            for c in rule.get('conditions', {})
            if c.get('MatchType', 'exact') == 'exact'
        )
        if match:
            for key, value in rule.get('actions', {}).items():
                options[key] = get_random_option(value)

    s = copy.deepcopy(BASE_DOORS_PAYLOAD)
    s['retro'] = options['world_state'] == 'retro'
    s['mode'] = 'open' if options['world_state'] == 'retro' else options['world_state']
    s['goal'] = {'fast_ganon': 'crystals', 'triforce-hunt': 'triforcehunt'}.get(options['goals'], options['goals'])
    s['crystals_gt'] = options['tower_open']
    s['crystals_ganon'] = options['ganon_open']
    s['swords'] = _SWORDS[options['weapons']]
    s['difficulty'] = options['item_pool']
    s['item_functionality'] = options['item_functionality']
    s['accessibility'] = options['accessibility']
    s['shopsanity'] = options['shopsanity'] == 'on'
    s['algorithm'] = options['algorithm']
    s['shuffle'] = 'vanilla' if options['entrance_shuffle'] == 'none' else options['entrance_shuffle']
    s['openpyramid'] = (
        s['goal'] in ('crystals', 'trinity')
        if s['shuffle'] in ('vanilla', 'dungeonsfull', 'dungeonssimple') else False
    )
    s['shufflepots'] = options['pot_shuffle'] == 'on'
    s['shuffleenemies'] = options['enemy_shuffle']
    s['shufflebosses'] = options['boss_shuffle']
    s['enemy_damage'] = options['enemy_damage']
    s['enemy_health'] = options['enemy_health']
    s['keydropshuffle'] = options['keydropshuffle'] == 'on'
    s['dropshuffle'] = options['dropshuffle'] == 'on'
    s['pottery'] = options['pottery']
    s['colorizepots'] = options['colorizepots'] == 'on'
    items = options['dungeon_items']
    s['mapshuffle'] = options['mapshuffle'] == 'on' if 'mapshuffle' in weights else items in ('mc', 'mcs', 'full')
    s['compassshuffle'] = (
        options['compassshuffle'] == 'on' if 'compassshuffle' in weights else items in ('mc', 'mcs', 'full')
    )
    s['keyshuffle'] = options['keyshuffle'] == 'on' if 'keyshuffle' in weights else items in ('mcs', 'full')
    s['bigkeyshuffle'] = options['bigkeyshuffle'] == 'on' if 'bigkeyshuffle' in weights else items == 'full'
    s['keysanity'] = options.get('keysanity') == 'on' if 'keysanity' in weights else items == 'full'
    for key in (
        'timer', 'collection_rate', 'bombbag', 'triforce_pool', 'triforce_goal', 'restrict_boss_items',
        'shufflelinks', 'overworld_map', 'pseudoboots', 'door_shuffle', 'intensity', 'beemizer', 'startinventory',
        'dungeon_counters',
    ):
        s[key] = options[key]
    s['experimental'] = options['experimental'] == 'on'
    s['hints'] = options['hints'] == 'on'
    s['usestartinventory'] = len(startitems) > 0
    if branch == 'volatile':
        s['keyshuffle'] = options['keyshuffle']
        s['dropshuffle'] = options['dropshuffle']
    return s


def _roll_weights(weights: Dict[str, Any]) -> MysteryRoll:
    weights = _resolve_subweights(weights)
    options = {
        'door_shuffle': get_random_option(weights.get('door_shuffle', 'vanilla')),
        'keydropshuffle': get_random_option(weights.get('keydropshuffle', False)),
        'dropshuffle': get_random_option(weights.get('dropshuffle', False)),
        'pottery': get_random_option(weights.get('pottery', 'none')),
        'shopsanity': get_random_option(weights.get('shopsanity', False)),
        'collection_rate': get_random_option(weights.get('collection_rate', False)),
        'bombbag': get_random_option(weights.get('bombbag', False)),
    }
    instructions = get_random_option(weights.get('custom_instructions', None))
    doors = (
        options['door_shuffle'] != 'vanilla'
        or options['keydropshuffle'] == 'on'
        or options['dropshuffle'] == 'on'
        or options['pottery'] != 'none'
        or options['shopsanity'] == 'on'
        or options['collection_rate'] == 'on'
        or options['bombbag'] == 'on'
        or weights.get('options', {}).get('force_doors', False)
    )
    if doors:
        return MysteryRoll(
            settings=_doors_settings(weights, options), doors=True,
            branch=weights.get('options', {}).get('branch', 'stable'), instructions=_text(instructions),
        )
    with contextlib.redirect_stdout(io.StringIO()):  # pyz3r prints each subweight it picks
        settings, customizer = generate_random_settings(weights, tournament=True, spoilers='mystery')
    return MysteryRoll(settings=settings, customizer=bool(customizer), instructions=_text(instructions))


MAX_INSTRUCTIONS_CHARS = 500


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value).strip()[:MAX_INSTRUCTIONS_CHARS] or None


def roll_mystery(weights: Dict[str, Any], lookup: PresetLookup) -> MysteryRoll:
    """Roll one set of settings from a weightset, or ``ValueError`` saying what's wrong with it."""
    weights = copy.deepcopy(weights)
    try:
        if 'preset' in weights:
            picked = get_random_option(weights['preset'])
            if picked != 'none':
                return _from_preset(str(picked), weights, lookup)
        return _roll_weights(weights)
    except ValueError:
        raise
    except KeyError as e:
        raise ValueError(f'The weightset has no weights for {e.args[0]}, which a roll needs.') from None
    except TypeError as e:
        raise ValueError(f'The weightset has a weight that isn\'t a number ({e}).') from None
    except (AttributeError, IndexError) as e:
        raise ValueError(f'Part of the weightset isn\'t in the shape a roll expects ({e}).') from None


def _from_preset(name: str, weights: Dict[str, Any], lookup: PresetLookup) -> MysteryRoll:
    found = lookup(name)
    if found is None:
        raise ValueError(f'The weightset can pick the preset {name}, but Wizzrobe has no built-in preset by that name.')
    randomizer, settings = found
    instructions = _text(get_random_option(weights.get('custom_instructions', None)))
    if randomizer == 'alttprdr':
        return MysteryRoll(
            settings=copy.deepcopy(settings.get('settings', {})), doors=True, branch='stable',
            instructions=instructions, preset=name,
        )
    settings = copy.deepcopy(settings)
    settings.pop('name', None)
    settings.pop('notes', None)
    settings['spoilers'] = 'mystery'
    settings['tournament'] = True
    return MysteryRoll(settings=settings, customizer=True, instructions=instructions, preset=name)


def check_weights(weights: Any, lookup: PresetLookup) -> Dict[str, Any]:
    """A weightset as stored, after a few dry rolls show it rolls; ``ValueError`` otherwise."""
    if not isinstance(weights, dict) or not weights:
        raise ValueError('A mystery preset is a weightset: each setting with the odds of each value.')
    if _depth(weights) > MAX_DEPTH:
        raise ValueError(f'The weightset nests deeper than {MAX_DEPTH} levels.')
    weights = _string_keys(weights)
    picks = weights.get('preset')
    if isinstance(picks, dict):
        for name in picks:
            if str(name) != 'none' and lookup(str(name)) is None:
                raise ValueError(f'The weightset can pick the preset {name}, but Wizzrobe has no built-in preset by that name.')
    for _ in range(DRY_ROLLS):
        roll_mystery(weights, lookup)
    return weights


def _key(k: Any) -> str:
    return str(k).lower() if isinstance(k, bool) else str(k)


def _string_keys(value: Any) -> Any:
    """YAML reads ``on``/``2`` keys as booleans and numbers, which JSON can't store; rolls read them back the same."""
    if isinstance(value, dict):
        return {_key(k): _string_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_string_keys(v) for v in value]
    return value


def _depth(value: Any) -> int:
    deepest, stack = 0, [(value, 1)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        if level > MAX_DEPTH:
            break
        if isinstance(node, dict):
            stack.extend((v, level + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, level + 1) for v in node)
    return deepest


def public_snapshot(snapshot: Optional[dict]) -> Optional[dict]:
    """What a mystery seed's stored settings show outside staff tools: the weightset and instructions only."""
    if not isinstance(snapshot, dict):
        return snapshot
    meta = snapshot.get('mystery')
    if not isinstance(meta, dict):
        return None
    return {'mystery': {k: meta.get(k) for k in ('weightset', 'instructions')}}


def instructions_of(snapshot: Optional[dict]) -> Optional[str]:
    meta = snapshot.get('mystery') if isinstance(snapshot, dict) else None
    value = meta.get('instructions') if isinstance(meta, dict) else None
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class SettingOdds:
    setting: str
    options: Tuple[Tuple[str, float], ...]


def _numeric(table: Any) -> bool:
    return isinstance(table, dict) and bool(table) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in table.values()
    )


def _percentages(table: Dict[Any, Any]) -> Tuple[Tuple[str, float], ...]:
    total = sum(table.values())
    return tuple(
        (str(k), round(100 * v / total, 1) if total else 0.0)
        for k, v in sorted(table.items(), key=lambda kv: -kv[1])
    )


def odds(weights: Any) -> list:
    """The chance of each value per setting, in the order the weightset lists them, for the editor."""
    if not isinstance(weights, dict):
        return []
    found: list = []

    def visit(prefix: str, value: Any) -> None:
        if _numeric(value):
            found.append(SettingOdds(prefix, _percentages(value)))
        elif isinstance(value, dict):
            for k, v in value.items():
                visit(f'{prefix}.{k}' if prefix else str(k), v)
        elif not isinstance(value, list) and value is not None:
            found.append(SettingOdds(prefix, ((str(value), 100.0),)))

    for key, value in weights.items():
        if key in ('description', 'rules', 'options'):
            continue
        if key == 'subweights' and isinstance(value, dict):
            chances = {k: v.get('chance', 0) for k, v in value.items() if isinstance(v, dict)}
            if _numeric(chances):
                found.append(SettingOdds('subweights', _percentages(chances)))
            continue
        visit(str(key), value)
    return found
