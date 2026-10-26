"""What an ALTTP item tracker board holds: items, dungeons and dungeon-item modes.

Pure data, no I/O. A board's state is ``{key: level}``; item keys are the item
names below, dungeon keys are ``'<dungeon>.<field>'`` (``'pod.boss'``,
``'mm.medallion'``). Sprites are file stems under ``static/item_tracker/``, from
OpenTracker (MIT, see ``static/item_tracker/LICENSE``), and every item has a
sprite per level, so level 0 is its dimmed "not found" art.
"""

from dataclasses import dataclass
from typing import Mapping, Optional

SPRITE_ROOT = 'item_tracker'


@dataclass(frozen=True)
class ItemSpec:
    key: str
    label: str
    sprites: tuple[str, ...]
    #: Names for each level, shown in the picker. ``None`` for an on/off item.
    levels: Optional[tuple[str, ...]] = None
    #: Level 0 is something every runner has (green mail), so it never dims.
    starts_owned: bool = False

    @property
    def max_level(self) -> int:
        return len(self.sprites) - 1

    @property
    def picker(self) -> bool:
        return self.levels is not None


def _toggle(key: str, label: str, stem: str) -> ItemSpec:
    return ItemSpec(key, label, (f'{stem}0', f'{stem}1'))


def _toggle2(key: str, label: str, stem: str) -> ItemSpec:
    return ItemSpec(key, label, (f'{stem}00', f'{stem}10'))


#: In board order, six to a row.
ITEMS: tuple[ItemSpec, ...] = (
    ItemSpec('bow', 'Bow', ('bow00', 'bow11', 'bow12'), ('None', 'Bow', 'Silver arrows')),
    ItemSpec('boomerang', 'Boomerang', ('boomerang00', 'boomerang10', 'boomerang01', 'boomerang11'),
             ('None', 'Blue', 'Red', 'Both')),
    _toggle('hookshot', 'Hookshot', 'hookshot'),
    _toggle2('bombs', 'Bombs', 'bomb'),
    ItemSpec('mushroom', 'Mushroom', ('mushroom0', 'mushroom1', 'mushroom2'),
             ('None', 'Mushroom', 'Handed in')),
    _toggle2('powder', 'Magic Powder', 'powder'),
    _toggle('fire_rod', 'Fire Rod', 'firerod'),
    _toggle('ice_rod', 'Ice Rod', 'icerod'),
    _toggle2('bombos', 'Bombos', 'bombos'),
    _toggle2('ether', 'Ether', 'ether'),
    _toggle2('quake', 'Quake', 'quake'),
    _toggle('lamp', 'Lamp', 'lamp'),
    _toggle('hammer', 'Hammer', 'hammer'),
    _toggle('shovel', 'Shovel', 'shovel'),
    ItemSpec('flute', 'Flute', ('flute00', 'flute10', 'flute11'), ('None', 'Flute', 'Activated')),
    _toggle('net', 'Bug Net', 'net'),
    _toggle('book', 'Book of Mudora', 'book'),
    ItemSpec('bottles', 'Bottles', ('bottle0', 'bottle1', 'bottle2', 'bottle3', 'bottle4'),
             ('None', '1', '2', '3', '4')),
    _toggle('somaria', 'Cane of Somaria', 'caneofsomaria'),
    _toggle('byrna', 'Cane of Byrna', 'caneofbyrna'),
    _toggle('cape', 'Magic Cape', 'cape'),
    _toggle('mirror', 'Magic Mirror', 'mirror'),
    _toggle('boots', 'Pegasus Boots', 'boots'),
    ItemSpec('gloves', 'Gloves', ('gloves0', 'gloves1', 'gloves2'), ('None', 'Power Glove', "Titan's Mitt")),
    _toggle('flippers', 'Flippers', 'flippers'),
    _toggle('moon_pearl', 'Moon Pearl', 'moonpearl'),
    ItemSpec('sword', 'Sword', ('sword1', 'sword2', 'sword3', 'sword4', 'sword5'),
             ('None', 'Fighter', 'Master', 'Tempered', 'Golden')),
    ItemSpec('shield', 'Shield', ('shield0', 'shield1', 'shield2', 'shield3'),
             ('None', 'Fighter', 'Fire', 'Mirror')),
    ItemSpec('mail', 'Mail', ('mail0', 'mail1', 'mail2'), ('Green', 'Blue', 'Red'), starts_owned=True),
    _toggle('half_magic', 'Half Magic', 'halfmagic'),
)
ITEMS_BY_KEY: Mapping[str, ItemSpec] = {i.key: i for i in ITEMS}

PRIZES = ('Unknown', 'Green pendant', 'Pendant', 'Crystal', 'Crystal 5/6')
PRIZE_SPRITES = ('unknown1', 'greenpendant1', 'pendant1', 'crystal1', 'redcrystal1')
MEDALLIONS = ('Unknown', 'Bombos', 'Ether', 'Quake')
MEDALLION_SPRITES = ('unknown1', 'bombos10', 'ether10', 'quake10')


@dataclass(frozen=True)
class DungeonSpec:
    key: str
    name: str
    short: str
    #: ``(alive, beaten)`` sprites; ``None`` for a keys-only tile (Hyrule Castle).
    boss: Optional[tuple[str, str]]
    #: Items in the dungeon when no dungeon item is shuffled out of it.
    chests: int
    small_keys: int
    big_key: bool
    prize: bool = True
    medallion: bool = False
    #: Shown only when small keys are shuffled.
    keys_only: bool = False


DUNGEONS: tuple[DungeonSpec, ...] = (
    DungeonSpec('ep', 'Eastern Palace', 'EP', ('armos0', 'armos1'), 3, 0, True),
    DungeonSpec('dp', 'Desert Palace', 'DP', ('lanmolas0', 'lanmolas1'), 2, 1, True),
    DungeonSpec('toh', 'Tower of Hera', 'ToH', ('moldorm0', 'moldorm1'), 2, 1, True),
    DungeonSpec('pod', 'Palace of Darkness', 'PoD', ('helmasaurking0', 'helmasaurking1'), 5, 6, True),
    DungeonSpec('sp', 'Swamp Palace', 'SP', ('arrghus0', 'arrghus1'), 6, 1, True),
    DungeonSpec('sw', 'Skull Woods', 'SW', ('mothula0', 'mothula1'), 2, 3, True),
    DungeonSpec('tt', "Thieves' Town", 'TT', ('blind0', 'blind1'), 4, 1, True),
    DungeonSpec('ip', 'Ice Palace', 'IP', ('kholdstare0', 'kholdstare1'), 3, 2, True),
    DungeonSpec('mm', 'Misery Mire', 'MM', ('vitreous0', 'vitreous1'), 2, 3, True, medallion=True),
    DungeonSpec('tr', 'Turtle Rock', 'TR', ('trinexx0', 'trinexx1'), 5, 4, True, medallion=True),
    DungeonSpec('gt', "Ganon's Tower", 'GT', ('aga20', 'aga21'), 20, 4, True, prize=False),
    DungeonSpec('at', "Agahnim's Tower", 'AT', ('aga10', 'aga11'), 0, 2, False, prize=False),
    DungeonSpec('hc', 'Hyrule Castle', 'HC', None, 0, 1, False, prize=False, keys_only=True),
)
DUNGEONS_BY_KEY: Mapping[str, DungeonSpec] = {d.key: d for d in DUNGEONS}

#: Dungeon fields a board stores, with their highest level for one dungeon.
DUNGEON_FIELDS = ('boss', 'prize', 'medallion', 'chests', 'keys', 'big_key')


@dataclass(frozen=True)
class DungeonItems:
    """Which dungeon items are shuffled out of their dungeons."""

    mode: str
    maps_compasses: bool
    small_keys: bool
    big_keys: bool

    @property
    def label(self) -> str:
        return DUNGEON_ITEM_LABELS[self.mode]


DUNGEON_ITEM_LABELS = {
    'standard': 'Standard dungeon items',
    'mc': 'Maps and compasses',
    'mcs': 'Maps, compasses and small keys',
    'full': 'Keysanity',
}
DUNGEON_ITEM_MODES = tuple(DUNGEON_ITEM_LABELS)


def dungeon_items(mode: Optional[str]) -> DungeonItems:
    mode = mode if mode in DUNGEON_ITEM_LABELS else 'standard'
    return DungeonItems(
        mode,
        maps_compasses=mode in ('mc', 'mcs', 'full'),
        small_keys=mode in ('mcs', 'full'),
        big_keys=mode == 'full',
    )


def mode_from_settings(settings: Optional[Mapping]) -> Optional[str]:
    """The dungeon-item mode an alttpr.com settings payload was rolled with.

    ``None`` when the payload doesn't say (not an ALTTPR seed, or none rolled),
    so the caller can fall back to standard and say it's a guess.
    """
    if not isinstance(settings, Mapping):
        return None
    raw = settings.get('custom')
    custom: Mapping = raw if isinstance(raw, Mapping) else {}
    wild = {k: bool(custom.get(f'region.wild{k}')) for k in ('Maps', 'Compasses', 'Keys', 'BigKeys')}
    if settings.get('customizer') or any(wild.values()):
        if wild['BigKeys']:
            return 'full'
        if wild['Keys']:
            return 'mcs'
        if wild['Maps'] or wild['Compasses']:
            return 'mc'
    mode = settings.get('dungeon_items')
    if mode in DUNGEON_ITEM_LABELS:
        return mode
    return 'standard' if settings.get('customizer') else None


def chest_total(dungeon: DungeonSpec, items: DungeonItems) -> int:
    """Item locations a dungeon counts down from, under ``items``.

    Every dungeon holds its own map, compass and keys; whatever is shuffled
    out of it is replaced by a pool item, which the counter then has to count.
    """
    if dungeon.keys_only or dungeon.boss is None:
        return 0
    total = dungeon.chests
    if dungeon.key == 'at':
        return dungeon.small_keys if items.small_keys else 0
    if items.maps_compasses:
        total += 2
    if items.small_keys:
        total += dungeon.small_keys
    if items.big_keys and dungeon.big_key:
        total += 1
    return total


def field_max(key: str, items: DungeonItems) -> Optional[int]:
    """Highest level ``key`` can take, or ``None`` for a key the board doesn't hold."""
    if key in ITEMS_BY_KEY:
        return ITEMS_BY_KEY[key].max_level
    dungeon_key, _, field = key.partition('.')
    dungeon = DUNGEONS_BY_KEY.get(dungeon_key)
    if dungeon is None:
        return None
    if field == 'boss':
        return 1 if dungeon.boss else None
    if field == 'prize':
        return len(PRIZES) - 1 if dungeon.prize else None
    if field == 'medallion':
        return len(MEDALLIONS) - 1 if dungeon.medallion else None
    if field == 'chests':
        return chest_total(dungeon, dungeon_items('full')) or None
    if field == 'keys':
        return dungeon.small_keys or None
    if field == 'big_key':
        return 1 if dungeon.big_key else None
    return None


def visible_dungeons(items: DungeonItems) -> tuple[DungeonSpec, ...]:
    return tuple(d for d in DUNGEONS if not d.keys_only or items.small_keys)


def describe(key: str, level: int) -> str:
    """A change as the log reads it: ``'Sword: Master'``, ``'Palace of Darkness boss beaten'``."""
    item = ITEMS_BY_KEY.get(key)
    if item is not None:
        if item.levels:
            return f'{item.label}: {item.levels[level]}'
        return item.label if level else f'{item.label} removed'
    dungeon_key, _, field = key.partition('.')
    dungeon = DUNGEONS_BY_KEY.get(dungeon_key)
    name = dungeon.name if dungeon else dungeon_key
    if field == 'boss':
        return f'{name} boss beaten' if level else f'{name} boss alive'
    if field == 'prize':
        return f'{name} prize: {PRIZES[level]}'
    if field == 'medallion':
        return f'{name} medallion: {MEDALLIONS[level]}'
    if field == 'chests':
        return f'{name}: {level} found'
    if field == 'keys':
        return f'{name} small keys: {level}'
    if field == 'big_key':
        return f'{name} big key' if level else f'{name} big key removed'
    return key
