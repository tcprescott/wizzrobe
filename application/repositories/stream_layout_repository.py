"""
Stream Layout Repository - Data Access Layer

Layouts, their background art, each stage's overlay row, and the match reads
the overlay feed needs.
"""

from datetime import datetime
from typing import List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._event import current_event_id, event_scoped
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import Match, Stage, StageOverlay, StreamLayout, StreamLayoutArt

_OVERLAY_MATCH_PREFETCH = (
    'tournament', 'stage', 'generated_seed', 'players', 'players__user',
    'commentators', 'commentators__user', 'trackers', 'trackers__user',
)


class StreamLayoutRepository(TenantScopedRepository[StreamLayout]):
    """Repository for stream layouts and stage overlays."""

    model = StreamLayout

    @classmethod
    async def create(cls, **fields) -> StreamLayout:
        return await StreamLayout.create(
            tenant_id=current_tenant_id(), event_id=current_event_id(), **fields,
        )

    @staticmethod
    async def list_for_event() -> List[StreamLayout]:
        return await event_scoped(StreamLayout.all()).order_by('name')

    @staticmethod
    async def get_in_event(layout_id: int) -> Optional[StreamLayout]:
        return await event_scoped(StreamLayout.filter(id=layout_id)).first()

    @staticmethod
    async def name_taken(name: str, exclude_id: Optional[int] = None) -> bool:
        qs = event_scoped(StreamLayout.filter(name__iexact=name))
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        return await qs.exists()

    # ── art ────────────────────────────────────────────────────────────

    @staticmethod
    async def set_art(layout: StreamLayout, content: bytes, sha256: str) -> None:
        art = await StreamLayoutArt.get_or_none(layout_id=layout.id, tenant_id=current_tenant_id())
        if art is None:
            await StreamLayoutArt.create(
                tenant_id=current_tenant_id(), layout_id=layout.id, content=content, sha256=sha256,
            )
            return
        art.content, art.sha256 = content, sha256
        await save_fields(art, 'content', 'sha256')

    @staticmethod
    async def delete_art(layout: StreamLayout) -> None:
        await StreamLayoutArt.filter(layout_id=layout.id, tenant_id=current_tenant_id()).delete()

    @staticmethod
    async def get_art(tenant_id: int, layout_id: int, sha256: str) -> Optional[bytes]:
        """The art bytes for this exact layout and hash; read with no tenant bound."""
        art = await StreamLayoutArt.get_or_none(tenant_id=tenant_id, layout_id=layout_id, sha256=sha256)
        return art.content if art else None

    # ── stage overlays ─────────────────────────────────────────────────

    @staticmethod
    async def stages() -> List[Stage]:
        return await scoped(Stage.filter(is_active=True)).order_by('name')

    @staticmethod
    async def get_stage(stage_id: int) -> Optional[Stage]:
        return await scoped(Stage.filter(id=stage_id)).first()

    @staticmethod
    async def overlays() -> List[StageOverlay]:
        return await scoped(StageOverlay.all()).prefetch_related('layout', 'pinned_match')

    @staticmethod
    async def overlay_for_stage(stage_id: int) -> Optional[StageOverlay]:
        return await scoped(StageOverlay.filter(stage_id=stage_id)).first()

    @staticmethod
    async def create_overlay(stage: Stage, token: str) -> StageOverlay:
        return await StageOverlay.create(
            tenant_id=current_tenant_id(), event_id=getattr(stage, 'event_id', None),
            stage_id=stage.id, token=token,
        )

    @staticmethod
    async def overlay_by_token(token: str) -> Optional[StageOverlay]:
        """The overlay a URL names, across every community: the token decides."""
        return await StageOverlay.get_or_none(token=token).prefetch_related('stage', 'layout', 'event')

    # ── matches ────────────────────────────────────────────────────────

    @staticmethod
    async def match_for_overlay(match_id: int) -> Optional[Match]:
        return await scoped(Match.filter(id=match_id)).prefetch_related(*_OVERLAY_MATCH_PREFETCH).first()

    @staticmethod
    async def stage_matches_between(stage_id: int, start: datetime, end: datetime) -> List[Match]:
        """Unfinished matches booked on one stage in ``[start, end)``, earliest first."""
        return await scoped(Match.filter(
            stage_id=stage_id, finished_at=None, scheduled_at__gte=start, scheduled_at__lt=end,
        )).prefetch_related(*_OVERLAY_MATCH_PREFETCH).order_by('scheduled_at', 'id')
