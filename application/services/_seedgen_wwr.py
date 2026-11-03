"""Wind Waker Randomizer permalinks, built locally.

wwrando has no hosted generator. Players build their own ISO from a
**permalink**: ``base64(VERSION + b"\\0" + seed_name + b"\\0" + packed option
bits)``, zlib-compressed first by the tanjo3 dev builds. Rolling a seed is
therefore a string operation: take the season's reference permalink, swap its
seed name for a fresh one, and encode it the way it came in. The version and
option bytes pass through untouched, so any build rolls, including ones
Wizzrobe has never seen.

Reading the *settings* is another matter. The option bits carry no names or
widths, and the layout changes every build, so :func:`summarize` only works for
builds vendored under ``_wwr_data/<version>/`` by ``scripts/vendor_wwr_build.py``.
The same directory carries the build's seed-name word lists.

The seed hash shown on the in-game file select is deliberately absent: with
"do not generate spoiler log" on, wwrando mixes a secret key from its official
builds into it, so a hash computed here would disagree with the players' screens.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import random
import string
import zlib
from pathlib import Path
from typing import NamedTuple, Optional

WWR_DATA_DIR = Path(__file__).resolve().parent / '_wwr_data'
WWR_DEFAULT_WORDS_BUILD = 'dev_tanjo3.1.10.7.3_d30d27c'

WWR_SEED_CHARACTERS = frozenset("-_'%." + string.ascii_letters + string.digits)
WWR_MAX_SEED_LENGTH = 42
_VERSION_CHARACTERS = frozenset('._-' + string.ascii_letters + string.digits)


class WWPermalink(NamedTuple):
    version: str
    seed: str
    option_bytes: bytes
    compressed: bool

    @property
    def build(self) -> str:
        return build_name(self.version)


def build_name(version: str) -> str:
    """The release a player downloads: the version minus the CI's ``_<commit>`` suffix."""
    head, sep, tail = version.rpartition('_')
    if sep and len(tail) == 7 and all(c in string.hexdigits for c in tail):
        return head
    return version


def decode_permalink(permalink: str) -> WWPermalink:
    """Split a permalink into its parts, raising ``ValueError`` for anything else."""
    text = (permalink or '').strip()
    if not text:
        raise ValueError('The Wind Waker permalink is blank.')
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("That isn't a Wind Waker permalink: it isn't valid base64.") from None
    try:
        payload, compressed = zlib.decompress(raw), True
    except zlib.error:
        payload, compressed = raw, False
    parts = payload.split(b'\0', 2)
    if len(parts) != 3 or not parts[0]:
        raise ValueError("That isn't a Wind Waker permalink: it has no randomizer version in it.")
    try:
        version, seed = parts[0].decode('ascii'), parts[1].decode('ascii')
    except UnicodeDecodeError:
        raise ValueError("That isn't a Wind Waker permalink: its version or seed isn't plain text.") from None
    if not version.isprintable() or not parts[2]:
        raise ValueError("That isn't a Wind Waker permalink: it carries no settings.")
    return WWPermalink(version, seed, parts[2], compressed)


def encode_permalink(parsed: WWPermalink) -> str:
    payload = parsed.version.encode('ascii') + b'\0' + parsed.seed.encode('ascii') + b'\0' + parsed.option_bytes
    if parsed.compressed:
        payload = zlib.compress(payload)
    return base64.b64encode(payload).decode('ascii')


def sanitize_seed(seed: str) -> str:
    """``WWRandomizer.sanitize_seed``: what the randomizer itself would keep."""
    return ''.join(c for c in str(seed).strip() if c in WWR_SEED_CHARACTERS)[:WWR_MAX_SEED_LENGTH]


def reseed(permalink: str, seed: str) -> str:
    """The same build and settings under a new seed name."""
    clean = sanitize_seed(seed)
    if not clean:
        raise ValueError('A Wind Waker seed name needs at least one letter or digit.')
    return encode_permalink(decode_permalink(permalink)._replace(seed=clean))


def _vendored_dir(version: str) -> Optional[Path]:
    # From a pasted permalink: must name a directory directly under _wwr_data and nothing else.
    if version in ('.', '..') or not all(c in _VERSION_CHARACTERS for c in version):
        return None
    path = WWR_DATA_DIR / version
    return path if path.is_dir() else None


def _word_lists(version: str) -> tuple[list[str], list[str], list[str]]:
    return _read_word_lists(version if _vendored_dir(version) else WWR_DEFAULT_WORDS_BUILD)


@functools.cache
def _read_word_lists(version: str) -> tuple[list[str], list[str], list[str]]:
    path = WWR_DATA_DIR / version

    def read(name: str) -> list[str]:
        return (path / name).read_text(encoding='utf-8').splitlines()

    return read('adjectives.txt'), read('nouns.txt'), read('names.txt')


def make_seed_name(rng: Optional[random.Random] = None, version: str = WWR_DEFAULT_WORDS_BUILD) -> str:
    """A seed name exactly as wwrando's "New seed" button makes one.

    Ports ``seedgen.make_random_seed_name`` followed by ``sanitize_seed``: two
    distinct adjectives, then a noun or a community member's name, each with its
    first letter capitalized. The call order on ``rng`` is upstream's, which is
    what lets the tests pin this against upstream output for a fixed seed.
    """
    rng = rng or random.Random()
    adjectives, nouns, names = _word_lists(version)
    words = rng.sample(adjectives, 2)
    words.append(rng.choice(nouns if rng.choice(['nouns.txt', 'names.txt']) == 'nouns.txt' else names))
    capitalized = []
    for word in words:
        first = next((i for i, c in enumerate(word) if c in string.ascii_letters), None)
        capitalized.append(word if first is None else word[:first] + word[first].upper() + word[first + 1:])
    return sanitize_seed(''.join(capitalized))


@functools.lru_cache(maxsize=8)
def _spec(version: str) -> Optional[dict]:
    path = _vendored_dir(version)
    if path is None or not (path / 'permalink_spec.json').is_file():
        return None
    return json.loads((path / 'permalink_spec.json').read_text(encoding='utf-8'))


def decode_settings(permalink: str) -> Optional[dict]:
    """Every option in the permalink by name, or ``None`` for a build not vendored.

    Mirrors ``WWRandomizer.decode_permalink``: fields are read in order from a
    little-endian bit stream, so one wrong width garbles everything after it.
    Raises ``ValueError`` when the bytes don't fit the vendored layout.
    """
    parsed = decode_permalink(permalink)
    spec = _spec(parsed.version)
    if spec is None:
        return None
    bits = int.from_bytes(parsed.option_bytes, 'little')
    total = len(parsed.option_bytes) * 8
    offset = 0

    def read(width: int) -> int:
        nonlocal offset
        if offset + width > total:
            raise ValueError(f"This permalink's settings don't fit build {parsed.version}.")
        value = (bits >> offset) & ((1 << width) - 1)
        offset += width
        return value

    settings: dict = {}
    for field in spec['fields']:
        kind = field['kind']
        if kind == 'bool':
            settings[field['name']] = bool(read(1))
        elif kind == 'enum':
            values = field['values']
            index = read((len(values) - 1).bit_length())
            if index >= len(values):
                raise ValueError(f"This permalink's settings don't fit build {parsed.version}.")
            settings[field['name']] = values[index]
        elif kind == 'int':
            value = read((field['max'] - field['min']).bit_length()) + field['min']
            settings[field['name']] = min(max(value, field['min']), field['max'])
        elif kind == 'starting_gear':
            gear = [item for item in field['regular'] if read(1)]
            for item in field['progressive']:
                gear.extend([item] * read(field['progressive_bits']))
            settings[field['name']] = gear
        elif kind == 'flags':
            settings[field['name']] = [name for name in field['values'] if read(1)]
        else:
            raise ValueError(f'Unknown field kind {kind!r} in the vendored spec for {parsed.version}.')
    return settings


def _entrances(s: dict) -> Optional[str]:
    kinds = [
        label for key, label in (
            ('randomize_dungeon_entrances', 'dungeon'),
            ('randomize_miniboss_entrances', 'miniboss'),
            ('randomize_boss_entrances', 'boss'),
            ('randomize_secret_cave_entrances', 'secret cave'),
            ('randomize_secret_cave_inner_entrances', 'inner cave'),
            ('randomize_fairy_fountain_entrances', 'fairy fountain'),
        ) if s.get(key)
    ]
    if not kinds:
        return None
    return f"Randomized {', '.join(kinds)} entrances"


def summarize(permalink: str) -> Optional[list[str]]:
    """The settings a staff member checks a preset against, one short line each.

    Headline settings only, not all ~100 options: enough to tell this season's
    preset from last season's at a glance. ``None`` when the build isn't vendored.
    """
    s = decode_settings(permalink)
    if s is None:
        return None
    lines = [s['sword_mode']] if 'sword_mode' in s else []
    if s.get('required_bosses'):
        count = s.get('num_required_bosses')
        lines.append(f"{count} required boss{'es' if count != 1 else ''}")
    if entrances := _entrances(s):
        lines.append(entrances)
    if s.get('randomize_starting_island'):
        lines.append('Random starting island')
    if s.get('boss_soul_shuffle'):
        lines.append('Boss souls shuffled')
    if s.get('korl_hints'):
        counts = [
            f"{s[f'num_{kind}_hints']} {kind}" for kind in ('path', 'barren', 'location', 'item')
            if s.get(f'num_{kind}_hints')
        ]
        if counts:
            lines.append(f"KoRL hints: {', '.join(counts)}")
    if s.get('kreeb_hints_bows'):
        lines.append('Kreeb hints the bows')
    keys = [
        f"{label}: {s[key]}" for key, label in (
            ('shuffle_small_keys', 'Small keys'), ('shuffle_big_keys', 'Big keys'),
            ('shuffle_maps_and_compasses', 'Maps and compasses'),
        ) if s.get(key) not in (None, 'Vanilla')
    ]
    lines.extend(keys)
    progression = [k for k in s if k.startswith('progression_')]
    if progression:
        lines.append(f"{sum(bool(s[k]) for k in progression)} of {len(progression)} progression categories")
    if s.get('enabled_tricks'):
        lines.append(f"{len(s['enabled_tricks'])} logic tricks enabled")
    lines.append('No spoiler log' if s.get('do_not_generate_spoiler_log') else 'Spoiler log on')
    return lines
