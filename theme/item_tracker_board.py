"""The ALTTP item tracker board: the item grid and the dungeon row.

One renderer for the three places a board appears: the crew's tappable board,
its "On stream" preview, and the OBS overlay (``bare``, no tile ground). It is
built once per dungeon-item mode and repainted in place, so a mark changes a
picture's ``src`` rather than rebuilding the grid under the tracker's finger.
"""

from functools import lru_cache
from typing import Any, Callable, Mapping, Optional

from nicegui import ui

from application.services.item_tracker_catalog import (
    DUNGEONS_BY_KEY,
    ITEMS,
    MEDALLION_SPRITES,
    MEDALLIONS,
    PRIZE_SPRITES,
    PRIZES,
    SPRITE_ROOT,
    DungeonItems,
    DungeonSpec,
    ItemSpec,
    chest_total,
    visible_dungeons,
)
from theme.assets import asset_url

OnItem = Callable[[ItemSpec], Any]
OnDungeon = Callable[[DungeonSpec], Any]


@lru_cache(maxsize=256)
def sprite(stem: str) -> str:
    return asset_url(f'{SPRITE_ROOT}/{stem}.png')


def item_label(item: ItemSpec, level: int) -> str:
    if item.levels:
        return f'{item.label}: {item.levels[level]}'
    return f"{item.label}: {'found' if level else 'not found'}"


def dungeon_label(dungeon: DungeonSpec, state: Mapping[str, int], items: DungeonItems) -> str:
    parts = [dungeon.name]
    if dungeon.boss:
        parts.append('boss beaten' if state.get(f'{dungeon.key}.boss') else 'boss alive')
    if dungeon.prize:
        parts.append(f"prize {PRIZES[state.get(f'{dungeon.key}.prize', 0)]}")
    if dungeon.medallion:
        parts.append(f"medallion {MEDALLIONS[state.get(f'{dungeon.key}.medallion', 0)]}")
    total = chest_total(dungeon, items)
    if total:
        parts.append(f"{total - state.get(f'{dungeon.key}.chests', 0)} of {total} items left")
    if items.small_keys and dungeon.small_keys:
        parts.append(f"{state.get(f'{dungeon.key}.keys', 0)} of {dungeon.small_keys} small keys")
    if items.big_keys and dungeon.big_key:
        parts.append('big key found' if state.get(f'{dungeon.key}.big_key') else 'no big key')
    return ', '.join(parts)


class BoardView:
    """An item grid plus dungeon row, repainted from a ``{key: level}`` state."""

    def __init__(self, *, bare: bool = False, on_item: Optional[OnItem] = None,
                 on_dungeon: Optional[OnDungeon] = None, show_dungeons: bool = True) -> None:
        self.bare = bare
        self.on_item = on_item
        self.on_dungeon = on_dungeon
        self.show_dungeons = show_dungeons
        self.items: Optional[DungeonItems] = None
        self.root = ui.element('div').classes('itk-board' + (' itk-board--bare' if bare else ''))
        self._item_tiles: dict[str, tuple[ui.element, ui.element]] = {}
        self._dungeon_tiles: dict[str, tuple[ui.element, ui.element, ui.element]] = {}
        self._state: dict[str, int] = {}

    def _tile(self, label: str, on_click: Optional[Callable[[], None]]) -> ui.element:
        if on_click is None:
            return ui.element('div').classes('itk-tile').props(f'role=img aria-label="{label}"')
        tile = ui.element('button').classes('itk-tile').props(f'type=button aria-label="{label}"')
        tile.on('click', on_click)
        return tile

    def build(self, items: DungeonItems) -> None:
        """(Re)build for a dungeon-item mode; the next ``paint`` fills it in."""
        self.items = items
        self.root.clear()
        self._item_tiles.clear()
        self._dungeon_tiles.clear()
        with self.root:
            with ui.element('div').classes('itk-items'):
                on_item, on_dungeon = self.on_item, self.on_dungeon
                for item in ITEMS:
                    click = (lambda i=item: on_item(i)) if on_item else None
                    with self._tile(item.label, click) as tile:
                        img = ui.element('img').props(f'src="{sprite(item.sprites[0])}" alt=""')
                    self._item_tiles[item.key] = (tile, img)
            if self.show_dungeons:
                with ui.element('div').classes('itk-dungeons'):
                    for dungeon in visible_dungeons(items):
                        click = (lambda d=dungeon: on_dungeon(d)) if on_dungeon else None
                        with self._tile(dungeon.name, click).classes('itk-dtile') as tile:
                            ui.label(dungeon.short).classes('itk-dtile__abbr')
                            boss = ui.element('img').classes('itk-dtile__boss').props('alt=""')
                            if dungeon.boss is None:
                                boss.props(f'src="{sprite("smallkey1")}"')
                            meta = ui.element('div').classes('itk-dtile__meta')
                        self._dungeon_tiles[dungeon.key] = (tile, boss, meta)
        state, self._state = self._state, {}
        self.paint(state)

    def paint(self, state: Mapping[str, int]) -> None:
        if self.items is None:
            return
        state = dict(state)
        if state == self._state and self._state:
            return
        for item in ITEMS:
            tile, img = self._item_tiles[item.key]
            level = min(state.get(item.key, 0), item.max_level)
            img.props(f'src="{sprite(item.sprites[level])}"')
            label = item_label(item, level)
            tile.props(f'aria-label="{label}"')
        for key, (tile, boss, meta) in self._dungeon_tiles.items():
            self._paint_dungeon(key, state, tile, boss, meta)
        self._state = state

    def _paint_dungeon(self, key: str, state: Mapping[str, int], tile, boss, meta) -> None:
        dungeon = DUNGEONS_BY_KEY[key]
        items = self.items
        assert items is not None
        if dungeon.boss is not None:
            beaten = bool(state.get(f'{key}.boss'))
            boss.props(f'src="{sprite(dungeon.boss[1])}"')
            if beaten:
                boss.classes(remove='itk-dtile__boss--alive')
            else:
                boss.classes('itk-dtile__boss--alive')
        tile.props(f'aria-label="{dungeon_label(dungeon, state, items)}"')
        meta.clear()
        with meta:
            with ui.element('div').classes('itk-dtile__row'):
                if dungeon.prize:
                    ui.element('img').props(f'src="{sprite(PRIZE_SPRITES[state.get(f"{key}.prize", 0)])}" alt=""')
                if dungeon.medallion:
                    ui.element('img').props(
                        f'src="{sprite(MEDALLION_SPRITES[state.get(f"{key}.medallion", 0)])}" alt=""'
                    )
                total = chest_total(dungeon, items)
                if total:
                    left = max(total - state.get(f'{key}.chests', 0), 0)
                    ui.label(str(left)).classes('itk-count' + (' itk-count--done' if not left else ''))
            small = items.small_keys and dungeon.small_keys
            big = items.big_keys and dungeon.big_key
            if small or big:
                with ui.element('div').classes('itk-dtile__row'):
                    if small:
                        if dungeon.boss is not None:
                            ui.element('img').classes('itk-dtile__key').props(f'src="{sprite("smallkey1")}" alt=""')
                        ui.label(f"{state.get(f'{key}.keys', 0)}/{dungeon.small_keys}").classes('itk-count')
                    if big:
                        ui.element('img').props(
                            f'src="{sprite("bigkey1" if state.get(f"{key}.big_key") else "bigkey0")}" alt=""'
                        )
