"""The app's Admin → Stream Layouts screen: every stage with what its overlay shows
now, every layout, and one layout open in the box editor.

Screen-shaped reads and thin writes over :class:`StreamLayoutService`, which keeps
every permission check, validation and audit row.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from application.feature_flags import requires_feature
from application.services.player_home_service import PlayerHomeService
from application.services.stream_layout_rules import (
    ALIGNS,
    FIELDS,
    FONTS,
    MAX_BOXES,
    MAX_LABEL,
    MAX_PLAYERS,
    MAX_TEXT,
    WEIGHTS,
)
from application.services.stream_layout_service import (
    MAX_ART_BYTES,
    OverlayFeed,
    StreamLayoutService,
    art_url,
    overlay_path,
)
from application.services.stream_layout_starters import BACKGROUNDS, DEFAULT_STARTER, STARTERS
from application.services.stream_layout_starters import H as STARTER_H
from application.services.stream_layout_starters import W as STARTER_W
from application.services.tenant_service import TenantService
from application.tenant_context import require_tenant_id
from application.utils.tenant_urls import tenant_url
from application.utils.twitch_embed import twitch_channel
from models import FeatureFlag, Match, StreamLayout, User


@dataclass
class OverlayMatch:
    id: int
    who: str
    tournament: str
    scheduled_at: Optional[datetime]
    started_at: Optional[datetime]


@dataclass
class StageLine:
    id: int
    name: str
    channel: Optional[str]
    layout_id: Optional[int]
    layout_name: Optional[str]
    showing: Optional[OverlayMatch]
    pinned: bool
    up_next: Optional[OverlayMatch]
    obs_url: str


@dataclass
class LayoutLine:
    id: int
    name: str
    width: int
    height: int
    box_count: int
    art_url: Optional[str]
    stages: List[str]
    updated_at: datetime


@dataclass
class StreamLayoutBoard:
    stages: List[StageLine]
    layouts: List[LayoutLine]
    zone: Optional[str]


@dataclass
class OverlayPreview:
    width: int
    height: int
    art_url: Optional[str]
    boxes: List[dict]
    values: dict


@dataclass
class LayoutDetail:
    layout: LayoutLine
    boxes: List[dict]


@dataclass
class FieldOption:
    key: str
    label: str
    icon: str
    group: str
    per_player: bool


@dataclass
class StarterOption:
    key: str
    label: str
    blurb: str
    group: str
    boxes: List[dict]


@dataclass
class Swatch:
    label: str
    colour: str


@dataclass
class EditorOptions:
    fields: List[FieldOption]
    fonts: List[str]
    weights: List[int]
    aligns: List[str]
    max_players: int
    max_boxes: int
    max_text: int
    max_label: int
    max_art_bytes: int
    starters: List[StarterOption]
    default_starter: str
    starter_width: int
    starter_height: int
    backgrounds: List[Swatch] = field(default_factory=list)


def overlay_match(match: Optional[Match]) -> Optional[OverlayMatch]:
    if match is None:
        return None
    names = [p.user.preferred_name for p in sorted(match.players, key=lambda p: p.id)]  # type: ignore[attr-defined]
    who = ' vs '.join(names) if 0 < len(names) <= 2 else (match.title or f'{len(names)} players')
    tournament = match.tournament.short_label if match.tournament else ''
    return OverlayMatch(match.id, who, tournament, match.scheduled_at, match.started_at)


class AdminStreamLayoutsService:
    def __init__(self) -> None:
        self.layouts = StreamLayoutService()

    @staticmethod
    async def _root() -> Any:
        return await TenantService.get_by_id(require_tenant_id())

    @staticmethod
    def _art(tenant: Any, layout: StreamLayout) -> Optional[str]:
        return tenant_url(tenant, art_url('', layout.id, layout.art_sha256)) if layout.art_sha256 else None

    def _layout_line(self, tenant: Any, layout: StreamLayout, stages: List[str]) -> LayoutLine:
        return LayoutLine(
            id=layout.id, name=layout.name, width=layout.width, height=layout.height,
            box_count=len(layout.boxes or []), art_url=self._art(tenant, layout),
            stages=stages, updated_at=layout.updated_at,
        )

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def board(self, actor: User) -> StreamLayoutBoard:
        tenant = await self._root()
        rows = await self.layouts.stage_overlays(actor)
        layouts = await self.layouts.list_layouts(actor)
        names = {lay.id: lay.name for lay in layouts}
        using: dict = {}
        stages = []
        for row in rows:
            layout_id = row.overlay.layout_id
            if layout_id is not None:
                using.setdefault(layout_id, []).append(row.stage.name)
            stages.append(StageLine(
                id=row.stage.id,
                name=row.stage.name,
                channel=(f'twitch.tv/{c}' if (c := twitch_channel(row.stage.stream_url or '')) else None),
                layout_id=layout_id,
                layout_name=names.get(layout_id),
                showing=overlay_match(row.showing),
                pinned=row.pinned,
                up_next=overlay_match(row.up_next),
                obs_url=tenant_url(tenant, overlay_path(row.overlay.token)),
            ))
        return StreamLayoutBoard(
            stages=stages,
            layouts=[self._layout_line(tenant, lay, using.get(lay.id, [])) for lay in layouts],
            zone=await PlayerHomeService.viewer_zone(actor),
        )

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def stage_preview(self, actor: User, stage_id: int) -> OverlayPreview:
        feed: OverlayFeed = await self.layouts.stage_preview(actor, stage_id)
        tenant = await self._root()
        art = tenant_url(tenant, art_url('', feed.layout_id, feed.art_sha256)) if feed.layout_id and feed.art_sha256 else None
        return OverlayPreview(feed.width, feed.height, art, list(feed.boxes), dict(feed.values))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def stage_matches(self, actor: User, stage_id: int) -> List[OverlayMatch]:
        return [m for m in (overlay_match(x) for x in await self.layouts.stage_matches(actor, stage_id)) if m]

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def preview_matches(self, actor: User) -> List[OverlayMatch]:
        return [m for m in (overlay_match(x) for x in await self.layouts.preview_matches(actor)) if m]

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def pin(self, actor: User, stage_id: int, match_id: int) -> None:
        if not any(m.id == match_id for m in await self.layouts.stage_matches(actor, stage_id)):
            raise ValueError("That match isn't booked on this stage around now.")
        await self.layouts.pin_match(actor, match_id)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def unpin(self, actor: User, stage_id: int) -> None:
        await self.layouts.unpin(actor, stage_id)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def set_stage_layout(self, actor: User, stage_id: int, layout_id: Optional[int]) -> None:
        await self.layouts.set_stage_layout(actor, stage_id, layout_id)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def renew_link(self, actor: User, stage_id: int) -> str:
        overlay = await self.layouts.renew_link(actor, stage_id)
        return tenant_url(await self._root(), overlay_path(overlay.token))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def obs_scenes(self, actor: User, stage_id: Optional[int]) -> tuple[str, str]:
        filename, data = await self.layouts.obs_scene_collection(actor, stage_id)
        return filename, data.decode()

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def editor_options(self, actor: User) -> EditorOptions:
        await self.layouts.list_layouts(actor)
        return EditorOptions(
            fields=[FieldOption(f.key, f.label, f.icon, f.group, f.per_player) for f in FIELDS],
            fonts=list(FONTS),
            weights=list(WEIGHTS),
            aligns=list(ALIGNS),
            max_players=MAX_PLAYERS,
            max_boxes=MAX_BOXES,
            max_text=MAX_TEXT,
            max_label=MAX_LABEL,
            max_art_bytes=MAX_ART_BYTES,
            starters=[StarterOption(s.key, s.label, s.blurb, s.group, [dict(b) for b in s.boxes]) for s in STARTERS],
            default_starter=DEFAULT_STARTER,
            starter_width=STARTER_W,
            starter_height=STARTER_H,
            backgrounds=[Swatch(label, colour) for label, colour in BACKGROUNDS],
        )

    async def _detail(self, layout: StreamLayout) -> LayoutDetail:
        tenant = await self._root()
        return LayoutDetail(self._layout_line(tenant, layout, []), list(layout.boxes or []))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def layout(self, actor: User, layout_id: int) -> LayoutDetail:
        return await self._detail(await self.layouts.get_layout(actor, layout_id))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def create_layout(self, actor: User, name: str, starter: Optional[str], background: Optional[str]) -> LayoutDetail:
        return await self._detail(await self.layouts.create_layout(actor, name, starter, background))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def duplicate_layout(self, actor: User, layout_id: int) -> LayoutDetail:
        return await self._detail(await self.layouts.duplicate_layout(actor, layout_id))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def rename_layout(self, actor: User, layout_id: int, name: str) -> LayoutDetail:
        return await self._detail(await self.layouts.rename_layout(actor, layout_id, name))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def delete_layout(self, actor: User, layout_id: int) -> None:
        await self.layouts.delete_layout(actor, layout_id)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def save_boxes(self, actor: User, layout_id: int, boxes: list, expected: Optional[datetime]) -> LayoutDetail:
        return await self._detail(await self.layouts.save_boxes(actor, layout_id, boxes, expected))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def preview_values(self, actor: User, layout_id: int, match_id: Optional[int], boxes: Optional[list]) -> dict:
        return await self.layouts.preview_values(actor, layout_id, match_id, boxes)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def set_art(self, actor: User, layout_id: int, data: bytes) -> tuple[LayoutDetail, bool]:
        layout, transparent = await self.layouts.set_art(actor, layout_id, data)
        return await self._detail(layout), transparent

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def set_background(self, actor: User, layout_id: int, colour: str) -> LayoutDetail:
        return await self._detail(await self.layouts.set_background(actor, layout_id, colour))

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def remove_art(self, actor: User, layout_id: int) -> LayoutDetail:
        return await self._detail(await self.layouts.remove_art(actor, layout_id))
