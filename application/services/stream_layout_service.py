"""Stream Layout Service — broadcast overlays OBS loads on top of its video.

Staff and Broadcast Techs design a layout per event: background art plus boxes
that each show one piece of match data (player names, round, commentators…) or
cut a transparent hole for a video feed. Each stage gets a secret overlay URL
that OBS loads as a browser source. The overlay follows the stage: it shows the
match on that stage now, or the next one, unless someone pins a match.

The overlay page is public by necessity (OBS can't sign in), so it is opened by
an unguessable token and shows only what the Watch page already shows anyone:
player names, tournament, round, crew, seed code. Art is never stored as sent;
it is decoded, bounded and re-encoded as a PNG here, like the community logo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from application.errors import NotFoundError
from application.event_context import get_current_event_id
from application.feature_flags import requires_feature
from application.repositories.stream_layout_repository import StreamLayoutRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.event_info_service import prize_fields
from application.services.stream_layout_obs import ObsHole, ObsScene, build_collection
from application.services.stream_layout_rules import (
    FIELDS_BY_KEY,
    KIND_FIELD,
    KIND_HOLE,
    MAX_CANVAS,
    clean_boxes,
    default_box,
    sample_values,
)
from application.services.stream_layout_starters import STARTERS_BY_KEY, clean_background, plain_background
from application.services.stream_layout_starters import H as STARTER_H
from application.services.stream_layout_starters import W as STARTER_W
from application.tenant_context import require_tenant_id, tenant_scope
from application.timezone_context import tz_scope
from application.utils.timezone import format_local_time, to_utc_aware
from application.utils.twitch_embed import twitch_channel
from models import FeatureFlag, Match, Role, Stage, StageOverlay, StreamLayout, User

logger = logging.getLogger(__name__)

MAX_ART_BYTES = 10 * 1024 * 1024
MIN_ART_WIDTH, MIN_ART_HEIGHT = 320, 180
ALLOWED_ART_FORMATS = frozenset({'PNG', 'JPEG', 'WEBP'})
ART_ACCEPT = '.png,.jpg,.jpeg,.webp'
TOKEN_PREFIX = 'ovl_'
# How far either side of now a stage's matches count as "today's": a late
# show runs past midnight, and a morning stage opens before the first match.
_WINDOW_BEHIND = timedelta(hours=12)
_WINDOW_AHEAD = timedelta(hours=18)
# Every OBS scene polls; a burst of them costs one build per stage.
_FEED_TTL = 2.0
_feed_cache: dict[int, tuple[float, 'OverlayFeed']] = {}

_NO_ACCESS = 'Only Staff and Broadcast Techs can manage stream layouts.'


def art_url(tenant_root: str, layout_id: int, sha256: str) -> str:
    return f'{tenant_root}/overlay-art/{layout_id}/{sha256}.png'


def overlay_path(token: str) -> str:
    """The tenant-relative path OBS loads for a stage."""
    return f'/overlay/{token}'


def prepare_art(data: bytes) -> tuple[bytes, int, int]:
    """Validate an upload; return ``(png bytes, width, height)``. Raises ``ValueError``.

    Re-encoding keeps the alpha channel (the transparent holes are the point)
    and drops anything else the file carried.
    """
    if not data:
        raise ValueError('That file is empty.')
    if len(data) > MAX_ART_BYTES:
        raise ValueError('Layout art can be up to 10 MB.')
    try:
        with Image.open(BytesIO(data)) as probe:
            fmt = probe.format
            width, height = probe.size
            probe.verify()
    except Image.DecompressionBombError:
        raise ValueError(f'Layout art can be up to {MAX_CANVAS} pixels wide.') from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValueError("That file isn't a PNG, JPEG or WebP image.") from None
    if fmt not in ALLOWED_ART_FORMATS:
        raise ValueError("That file isn't a PNG, JPEG or WebP image.")
    if max(width, height) > MAX_CANVAS:
        raise ValueError(f'Layout art can be up to {MAX_CANVAS} pixels wide.')
    if width < MIN_ART_WIDTH or height < MIN_ART_HEIGHT:
        raise ValueError(f'Layout art needs to be at least {MIN_ART_WIDTH}×{MIN_ART_HEIGHT} pixels.')
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            upright = ImageOps.exif_transpose(img).convert('RGBA')
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValueError("That image couldn't be read. Try saving it again as a PNG.") from None
    out = BytesIO()
    upright.save(out, 'PNG')
    return out.getvalue(), upright.width, upright.height


def has_transparency(png: bytes) -> bool:
    with Image.open(BytesIO(png)) as img:
        alpha = img.getchannel('A')
        low = alpha.getextrema()[0]
        return isinstance(low, (int, float)) and low < 255


@dataclass
class OverlayFeed:
    """What one overlay page draws: the layout and every box's current text."""

    layout_id: Optional[int]
    version: str
    width: int
    height: int
    art_sha256: Optional[str]
    boxes: list[dict] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)
    match_id: Optional[int] = None


@dataclass
class StageOverlayRow:
    """One stage on the layouts page: its overlay, layout and what it shows now."""

    stage: Stage
    overlay: StageOverlay
    showing: Optional[Match]
    pinned: bool


def _names(signups) -> str:
    return ', '.join(c.user.preferred_name for c in (signups or []) if c.approved)


def _players(match: Match) -> list:
    return sorted(match.players, key=lambda p: p.id)  # type: ignore[attr-defined]


def _money(amount) -> str:
    whole = amount == amount.to_integral_value()
    return f'${amount:,.0f}' if whole else f'${amount:,.2f}'


class StreamLayoutService:
    """Design layouts, wire stages to them, and build the overlay feed."""

    def __init__(self) -> None:
        self.repository = StreamLayoutRepository()
        self.audit = AuditService()

    @staticmethod
    async def can_manage(user: Optional[User]) -> bool:  # feature-gate: exempt — a role check, no feature data
        return await AuthService.is_staff(user) or await AuthService.has_role(user, Role.STREAM_MANAGER)

    async def _ensure(self, actor: User) -> None:
        await AuthService.ensure(await self.can_manage(actor), _NO_ACCESS)

    async def _layout(self, layout_id: int) -> StreamLayout:
        layout = await self.repository.get_in_event(layout_id)
        if layout is None:
            raise NotFoundError('That layout no longer exists.')
        return layout

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def list_layouts(self, actor: User) -> list[StreamLayout]:
        await self._ensure(actor)
        return await self.repository.list_for_event()

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def get_layout(self, actor: User, layout_id: int) -> StreamLayout:
        await self._ensure(actor)
        return await self._layout(layout_id)

    async def _clean_name(self, name: str, exclude_id: Optional[int] = None) -> str:
        name = (name or '').strip()
        if not name:
            raise ValueError('Give the layout a name.')
        if len(name) > 80:
            raise ValueError('Layout names can be up to 80 characters.')
        if await self.repository.name_taken(name, exclude_id):
            raise ValueError(f'This event already has a layout called “{name}”.')
        return name

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def create_layout(
        self, actor: User, name: str, starter: Optional[str] = None, background: Optional[str] = None,
    ) -> StreamLayout:
        """A new layout, empty or from a built-in starter, on a plain background if one is given."""
        await self._ensure(actor)
        picked = STARTERS_BY_KEY.get(starter or 'blank')
        if picked is None:
            raise ValueError('That starter layout no longer exists.')
        colour = clean_background(background) if background is not None else None
        name = await self._clean_name(name)
        boxes = clean_boxes([dict(b) for b in picked.boxes], STARTER_W, STARTER_H)
        layout = await self.repository.create(
            name=name, width=STARTER_W, height=STARTER_H, boxes=boxes, created_by=actor,
        )
        if colour is not None:
            content = await asyncio.to_thread(plain_background, colour, STARTER_W, STARTER_H)
            await self._store_art(layout, content, STARTER_W, STARTER_H)
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_CREATED,
            {'layout_id': layout.id, 'name': layout.name, 'starter': picked.key, 'background': colour},
        )
        return layout

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def duplicate_layout(self, actor: User, layout_id: int) -> StreamLayout:
        await self._ensure(actor)
        source = await self._layout(layout_id)
        base = f'{source.name} (copy)'[:76]
        name, n = base, 2
        while await self.repository.name_taken(name):
            name, n = f'{base} {n}', n + 1
        copy = await self.repository.create(
            name=name, width=source.width, height=source.height,
            boxes=list(source.boxes or []), created_by=actor,
        )
        if source.art_sha256:
            content = await self.repository.get_art(require_tenant_id(), source.id, source.art_sha256)
            if content is not None:
                await self.repository.set_art(copy, content, source.art_sha256)
                await self.repository.update(copy, art_sha256=source.art_sha256)
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_CREATED,
            {'layout_id': copy.id, 'name': copy.name, 'copied_from': source.id},
        )
        return copy

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def rename_layout(self, actor: User, layout_id: int, name: str) -> StreamLayout:
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        old = layout.name
        await self.repository.update(layout, name=await self._clean_name(name, layout.id))
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_UPDATED,
            {'layout_id': layout.id, 'renamed_from': old, 'name': layout.name},
        )
        return layout

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def save_boxes(
        self, actor: User, layout_id: int, boxes: list, expected_updated_at: Optional[datetime] = None,
    ) -> StreamLayout:
        """Replace a layout's boxes. Refuses when someone else saved it first."""
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        if expected_updated_at is not None and to_utc_aware(layout.updated_at) != to_utc_aware(expected_updated_at):
            raise ValueError('Someone else saved this layout while you were editing. Reload it to see their changes.')
        cleaned = clean_boxes(boxes, layout.width, layout.height)
        await self.repository.update(layout, boxes=cleaned)
        _feed_cache.clear()
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_UPDATED, {'layout_id': layout.id, 'boxes': len(cleaned)},
        )
        return layout

    @staticmethod
    def new_box(layout: StreamLayout, kind: str, field_key: Optional[str] = None) -> dict:
        if kind not in (KIND_FIELD, KIND_HOLE) or (kind == KIND_FIELD and field_key not in FIELDS_BY_KEY):
            raise ValueError('Pick something for the box to show.')
        return default_box(kind, field_key, layout.width, layout.height)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def set_art(self, actor: User, layout_id: int, data: bytes) -> tuple[StreamLayout, bool]:
        """Store new art; returns the layout and whether the art has transparency.

        The canvas takes the art's size. Boxes are scaled to match, so a layout
        designed on a 1280×720 mock-up survives the real 1920×1080 export.
        """
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        content, width, height = await asyncio.to_thread(prepare_art, data)
        transparent = await asyncio.to_thread(has_transparency, content)
        sha256 = await self._store_art(layout, content, width, height)
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_UPDATED,
            {'layout_id': layout.id, 'art_sha256': sha256, 'width': width, 'height': height},
        )
        return layout, transparent

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def set_background(self, actor: User, layout_id: int, colour: str) -> StreamLayout:
        """Replace the art with one plain colour at the layout's size.

        Hole boxes stay, and so do the old art's transparent areas: plenty of
        art cuts its game frames with alpha rather than hole boxes.
        """
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        colour = clean_background(colour)
        old = (
            await self.repository.get_art(layout.tenant_id, layout.id, layout.art_sha256)
            if layout.art_sha256 else None
        )
        content = await asyncio.to_thread(plain_background, colour, layout.width, layout.height, old)
        sha256 = await self._store_art(layout, content, layout.width, layout.height)
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_UPDATED,
            {'layout_id': layout.id, 'art_sha256': sha256, 'background': colour},
        )
        return layout

    async def _store_art(self, layout: StreamLayout, content: bytes, width: int, height: int) -> str:
        sha256 = hashlib.sha256(content).hexdigest()
        boxes = list(layout.boxes or [])
        if boxes and (width, height) != (layout.width, layout.height):
            sx, sy = width / layout.width, height / layout.height
            for box in boxes:
                box['x'], box['w'] = round(box['x'] * sx), round(box['w'] * sx)
                box['y'], box['h'] = round(box['y'] * sy), round(box['h'] * sy)
                if box.get('kind') == KIND_FIELD:
                    box['size'] = round(box.get('size', 24) * sy)
            boxes = clean_boxes(boxes, width, height)
        await self.repository.set_art(layout, content, sha256)
        await self.repository.update(layout, art_sha256=sha256, width=width, height=height, boxes=boxes)
        _feed_cache.clear()
        return sha256

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def remove_art(self, actor: User, layout_id: int) -> StreamLayout:
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        await self.repository.delete_art(layout)
        await self.repository.update(layout, art_sha256=None)
        _feed_cache.clear()
        await self.audit.write_log(
            actor, AuditActions.STREAM_LAYOUT_UPDATED, {'layout_id': layout.id, 'art_removed': True},
        )
        return layout

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def delete_layout(self, actor: User, layout_id: int) -> None:
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        details = {'layout_id': layout.id, 'name': layout.name}
        await self.repository.delete(layout)
        _feed_cache.clear()
        await self.audit.write_log(actor, AuditActions.STREAM_LAYOUT_DELETED, details)

    async def _overlay(self, stage: Stage) -> StageOverlay:
        overlay = await self.repository.overlay_for_stage(stage.id)
        if overlay is None:
            overlay = await self.repository.create_overlay(stage, TOKEN_PREFIX + secrets.token_urlsafe(24))
        return overlay

    async def _stage(self, stage_id: int) -> Stage:
        stage = await self.repository.get_stage(stage_id)
        if stage is None:
            raise NotFoundError('That stage no longer exists.')
        return stage

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def stage_overlays(self, actor: User) -> list[StageOverlayRow]:
        """Every active stage with its overlay (created on first look)."""
        await self._ensure(actor)
        rows = []
        for stage in await self.repository.stages():
            overlay = await self._overlay(stage)
            showing, pinned = await self._showing(stage, overlay)
            rows.append(StageOverlayRow(stage=stage, overlay=overlay, showing=showing, pinned=pinned))
        return rows

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def obs_scene_collection(self, actor: User, stage_id: Optional[int] = None) -> tuple[str, bytes]:
        """An OBS scene collection (file name, JSON bytes) for one stage, or every stage with a layout."""
        from application.services.tenant_service import TenantService
        from application.utils.tenant_urls import tenant_url

        await self._ensure(actor)
        tenant = await TenantService.get_by_id(require_tenant_id())
        stages = [await self._stage(stage_id)] if stage_id is not None else await self.repository.stages()
        scenes = []
        for stage in stages:
            overlay = await self._overlay(stage)
            if overlay.layout_id is None:
                continue
            layout = await self._layout(overlay.layout_id)
            holes = tuple(
                ObsHole(b.get('label', ''), b['x'], b['y'], b['w'], b['h'])
                for b in layout.boxes or [] if b.get('kind') == KIND_HOLE
            )
            scenes.append(ObsScene(
                name=f'{stage.name} · {layout.name}', overlay_url=tenant_url(tenant, overlay_path(overlay.token)),
                width=layout.width, height=layout.height, holes=holes,
            ))
        if not scenes:
            raise ValueError(
                f'{stages[0].name} has no layout yet. Pick one first.' if stage_id is not None
                else 'No stage has a layout yet. Pick one on a stage first.',
            )
        event_name = await self._event_name(get_current_event_id()) or 'Wizzrobe'
        title = f'{event_name} · {stages[0].name}' if stage_id is not None else event_name
        collection = build_collection(title, scenes)
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'wizzrobe'
        return f'{slug}-obs-scenes.json', json.dumps(collection, indent=2).encode()

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def set_stage_layout(self, actor: User, stage_id: int, layout_id: Optional[int]) -> StageOverlay:
        await self._ensure(actor)
        stage = await self._stage(stage_id)
        overlay = await self._overlay(stage)
        layout = await self._layout(layout_id) if layout_id is not None else None
        await self.repository.update_overlay(overlay, layout=layout)
        _feed_cache.pop(overlay.id, None)
        await self.audit.write_log(
            actor, AuditActions.STAGE_OVERLAY_UPDATED,
            {'stage_id': stage.id, 'layout_id': layout.id if layout else None},
        )
        return overlay

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def renew_link(self, actor: User, stage_id: int) -> StageOverlay:
        """Retire a stage's overlay URL; OBS needs the new one pasted in."""
        await self._ensure(actor)
        stage = await self._stage(stage_id)
        overlay = await self._overlay(stage)
        _feed_cache.pop(overlay.id, None)
        await self.repository.update_overlay(overlay, token=TOKEN_PREFIX + secrets.token_urlsafe(24))
        await self.audit.write_log(actor, AuditActions.STAGE_OVERLAY_LINK_RENEWED, {'stage_id': stage.id})
        return overlay

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def pin_match(self, actor: User, match_id: int) -> StageOverlay:
        """Show this match on its stage's overlay until unpinned."""
        await self._ensure(actor)
        match = await self.repository.match_for_overlay(match_id)
        if match is None:
            raise NotFoundError('That match no longer exists.')
        if match.stage_id is None:  # type: ignore[attr-defined]
            raise ValueError('Put the match on a stage first; overlays follow stages.')
        overlay = await self._overlay(match.stage)  # type: ignore[arg-type]
        await self.repository.update_overlay(
            overlay, pinned_match=match, pinned_by=actor, pinned_at=datetime.now(timezone.utc),
        )
        _feed_cache.pop(overlay.id, None)
        await self.audit.write_log(
            actor, AuditActions.STAGE_OVERLAY_PINNED, {'stage_id': match.stage_id, 'match_id': match.id},  # type: ignore[attr-defined]
        )
        return overlay

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def unpin(self, actor: User, stage_id: int) -> StageOverlay:
        await self._ensure(actor)
        stage = await self._stage(stage_id)
        overlay = await self._overlay(stage)
        if overlay.pinned_match_id is not None:
            details = {'stage_id': stage.id, 'match_id': overlay.pinned_match_id}
            await self.repository.update_overlay(overlay, pinned_match=None, pinned_by=None, pinned_at=None)
            _feed_cache.pop(overlay.id, None)
            await self.audit.write_log(actor, AuditActions.STAGE_OVERLAY_UNPINNED, details)
        return overlay

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def pin_state(self, actor: User, match_id: int) -> Optional[bool]:
        """``True`` pinned on its stage, ``False`` pinnable, ``None`` not for this viewer/match."""
        if not await self.can_manage(actor):
            return None
        match = await self.repository.match_for_overlay(match_id)
        if match is None or match.stage_id is None:  # type: ignore[attr-defined]
            return None
        overlay = await self.repository.overlay_for_stage(match.stage_id)  # type: ignore[attr-defined]
        return overlay is not None and overlay.pinned_match_id == match.id

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def stage_matches(self, actor: User, stage_id: int) -> list[Match]:
        """The stage's unfinished matches around now, for the pin picker."""
        await self._ensure(actor)
        now = datetime.now(timezone.utc)
        return await self.repository.stage_matches_between(stage_id, now - _WINDOW_BEHIND, now + _WINDOW_AHEAD)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def preview_matches(self, actor: User) -> list[Match]:
        """Every stage's matches around now, for the editor's preview picker."""
        await self._ensure(actor)
        now = datetime.now(timezone.utc)
        out: list[Match] = []
        for stage in await self.repository.stages():
            out += await self.repository.stage_matches_between(stage.id, now - _WINDOW_BEHIND, now + _WINDOW_AHEAD)
        return sorted(out, key=lambda m: (m.scheduled_at, m.id))

    async def _showing(self, stage: Stage, overlay: StageOverlay) -> tuple[Optional[Match], bool]:
        """The match on the overlay now, and whether it was pinned there."""
        if overlay.pinned_match_id is not None:
            pinned = await self.repository.match_for_overlay(overlay.pinned_match_id)
            if pinned is not None:
                return pinned, True
        current, _ = await self._current_and_next(stage.id)
        return current, False

    async def _current_and_next(
        self, stage_id: int, current: Optional[Match] = None,
    ) -> tuple[Optional[Match], Optional[Match]]:
        """The stage's match on now (latest started or checked in, else the next
        booked) and the one after it."""
        now = datetime.now(timezone.utc)
        matches = await self.repository.stage_matches_between(stage_id, now - _WINDOW_BEHIND, now + _WINDOW_AHEAD)
        if current is None:
            underway = [m for m in matches if m.started_at or m.seated_at]
            if underway:
                current = underway[-1]
            else:
                upcoming = [m for m in matches if to_utc_aware(m.scheduled_at) >= now - timedelta(hours=1)]
                current = upcoming[0] if upcoming else None
        after = [
            m for m in matches
            if current is None or (m.id != current.id and not (m.started_at or m.seated_at)
                                   and (m.scheduled_at, m.id) > (current.scheduled_at, current.id))
        ]
        return current, (after[0] if after else None)

    async def _values(
        self, boxes: list[dict], match: Optional[Match], up_next: Optional[Match],
        stage: Optional[Stage], event_name: str,
    ) -> dict[str, str]:
        from application.services.match._dm_context import bracket_line_for
        from application.services.tenant_service import TenantService

        round_label = ''
        players: list = []
        if match is not None:
            players = _players(match)
            try:
                round_label = await bracket_line_for(match.id)
            except Exception:
                logger.debug('Overlay round label failed for match %s', match.id, exc_info=True)
            if not round_label and getattr(match, 'crew_hold_source', None):
                round_label = match.title or ''
        tournament = match.tournament if match is not None else None
        prize = prize_fields(tournament) if tournament is not None else {}

        def up_next_text() -> str:
            if up_next is None:
                return ''
            names = [p.user.preferred_name for p in _players(up_next)]
            who = ' vs '.join(names) if 0 < len(names) <= 2 else (up_next.title or up_next.tournament.name)
            when = format_local_time(up_next.scheduled_at) if up_next.scheduled_at else ''
            return ' · '.join(x for x in (when, who) if x)

        names = [p.user.preferred_name for p in players]
        channel = twitch_channel(stage.stream_url) if stage and stage.stream_url else ''
        shared = {
            'players_all': ' vs '.join(names) if len(names) <= 2 else ', '.join(names),
            'tournament': tournament.name if tournament else '',
            'tournament_short': tournament.short_label if tournament else '',
            'round': round_label,
            'tournament_round': ' · '.join(x for x in (tournament.name if tournament else '', round_label) if x),
            'match_title': (match.title or '') if match else '',
            'scheduled_time': format_local_time(match.scheduled_at) if match and match.scheduled_at else '',
            'seed_code': ' · '.join(match.generated_seed.seed_code or [])
            if match and match.generated_seed else '',
            'prize_pool': _money(prize['prize_total']) if prize else '',
            'stage_name': stage.name if stage else '',
            'channel': f'twitch.tv/{channel}' if channel else '',
            'commentators': _names(match.commentators) if match else '',  # type: ignore[attr-defined]
            'trackers': _names(match.trackers) if match else '',  # type: ignore[attr-defined]
            'up_next': up_next_text(),
            'event_name': event_name,
            'community_name': await TenantService.current_community_name(),
        }
        values: dict[str, str] = {}
        for box in boxes:
            if box.get('kind') != KIND_FIELD:
                continue
            key, which = box['field'], box.get('which', 1)
            player = players[which - 1] if 0 < which <= len(players) else None
            if key == 'player_name':
                values[box['id']] = player.user.preferred_name if player else ''
            elif key == 'player_pronouns':
                values[box['id']] = (player.user.pronouns or '') if player else ''
            elif key == 'custom_text':
                values[box['id']] = box.get('text', '')
            else:
                values[box['id']] = shared.get(key, '')
        return values

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def preview_values(
        self, actor: User, layout_id: int, match_id: Optional[int], boxes: Optional[list] = None,
    ) -> dict[str, str]:
        """Each box's text in the editor: sample text, or a real match's.

        ``boxes`` is the editor's unsaved copy; it is checked like a save.
        """
        await self._ensure(actor)
        layout = await self._layout(layout_id)
        boxes = clean_boxes(boxes, layout.width, layout.height) if boxes is not None else list(layout.boxes or [])
        if match_id is None:
            return sample_values(boxes)
        match = await self.repository.match_for_overlay(match_id)
        if match is None:
            return sample_values(boxes)
        _, up_next = await self._current_and_next(match.stage_id, match) if match.stage_id else (None, None)  # type: ignore[attr-defined]
        return await self._values(boxes, match, up_next, match.stage, await self._event_name(layout.event_id))

    @staticmethod
    async def _event_name(event_id: Optional[int]) -> str:
        from application.services.event_service import EventService

        for event in await EventService.list_events():
            if event.id == event_id:
                return event.name
        return ''

    @staticmethod
    async def resolve_token(token: str) -> Optional[StageOverlay]:  # feature-gate: exempt — overlay_feed enforces
        """The overlay a URL opens, or ``None``; no tenant needs to be bound."""
        if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 64:
            return None
        return await StreamLayoutRepository.overlay_by_token(token)

    @requires_feature(FeatureFlag.STREAM_LAYOUTS)
    async def overlay_feed(self, overlay: StageOverlay) -> OverlayFeed:
        """Everything the public overlay page draws. Tenant must be the overlay's."""
        if overlay.tenant_id != require_tenant_id():
            raise NotFoundError('Overlay not found')
        cached = _feed_cache.get(overlay.id)
        if cached and time.monotonic() - cached[0] < _FEED_TTL:
            return cached[1]
        from application.services.timezone_service import TimezoneService

        layout = overlay.layout
        stage = overlay.stage
        if layout is None:
            feed = OverlayFeed(layout_id=None, version='none', width=1920, height=1080, art_sha256=None)
        else:
            with tz_scope(await TimezoneService.tenant_timezone_name(overlay.tenant_id)):
                match, _ = await self._showing(stage, overlay)
                _, up_next = await self._current_and_next(stage.id, match)
                boxes = list(layout.boxes or [])
                values = await self._values(boxes, match, up_next, stage, await self._event_name(layout.event_id))
            feed = OverlayFeed(
                layout_id=layout.id,
                version=f'{layout.id}:{to_utc_aware(layout.updated_at).timestamp()}',
                width=layout.width, height=layout.height, art_sha256=layout.art_sha256,
                boxes=boxes, values=values, match_id=match.id if match else None,
            )
        _feed_cache[overlay.id] = (time.monotonic(), feed)
        return feed

    @staticmethod
    async def art_bytes(  # feature-gate: exempt — soft: checks the flag itself and answers None
        tenant_id: int, layout_id: int, sha256: str,
    ) -> Optional[bytes]:
        """A layout's art by content hash, for the public image route."""
        if len(sha256) != 64:
            return None
        with tenant_scope(tenant_id):
            from application.services.feature_flag_service import FeatureFlagService

            if not await FeatureFlagService().is_enabled(FeatureFlag.STREAM_LAYOUTS):
                return None
        return await StreamLayoutRepository.get_art(tenant_id, layout_id, sha256)
