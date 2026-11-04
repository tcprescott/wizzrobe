"""Async-qualifier services — self-serve qualifying runs against a seed pool.

``AsyncQualifierService`` is the lifecycle surface; ``async_qualifier_draw``
hands a player a permalink from the pool, ``async_qualifier_scoring`` ranks the
submitted runs, ``async_qualifier_access`` decides who may see a run before the
window closes, ``async_qualifier_config``/``_rules`` validate the per-qualifier
settings, ``async_qualifier_pools`` authors the pools and permalinks runs are
drawn from, ``async_qualifier_review`` is the reviewer's queue (claim, decide,
notes), and ``async_qualifier_expiry`` + ``async_qualifier_worker`` between them
forfeit a run the player drew and abandoned. ``LiveRaceRoomService`` runs a
scheduled live race's room (open, post, permalink, lock) with the settings
``async_qualifier_live_race_settings`` validates. Gated by
``FeatureFlag.ASYNC_QUALIFIERS``.
"""

from application.services.async_qualifier import (
    async_qualifier_access,
    async_qualifier_expiry,
    async_qualifier_live_race_settings,
    async_qualifier_notifications,
    async_qualifier_pools,
    async_qualifier_reads,
    async_qualifier_review,
    async_qualifier_scoring,
    async_qualifier_worker,
)
from application.services.async_qualifier.async_qualifier_config import (
    AsyncQualifierConfig,
    validate_async_qualifier_config,
)
from application.services.async_qualifier.async_qualifier_draw import AsyncQualifierDraw
from application.services.async_qualifier.async_qualifier_live_race_room import LiveRaceRoomService
from application.services.async_qualifier.async_qualifier_live_race_service import (
    AsyncQualifierLiveRaceService,
)
from application.services.async_qualifier.async_qualifier_rules import (
    validate_counts,
    validate_window,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService

__all__ = [
    'AsyncQualifierConfig',
    'AsyncQualifierDraw',
    'AsyncQualifierLiveRaceService',
    'AsyncQualifierService',
    'LiveRaceRoomService',
    'async_qualifier_access',
    'async_qualifier_expiry',
    'async_qualifier_live_race_settings',
    'async_qualifier_notifications',
    'async_qualifier_pools',
    'async_qualifier_reads',
    'async_qualifier_review',
    'async_qualifier_scoring',
    'async_qualifier_worker',
    'validate_async_qualifier_config',
    'validate_counts',
    'validate_window',
]
