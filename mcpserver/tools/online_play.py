"""Online-play plumbing: seed presets, racetime rooms, and SpeedGaming sync.

Gates mirror ``api/routers/presets.py``, ``api/routers/race_room_profiles.py``,
``api/routers/race_rooms.py`` and ``api/routers/speedgaming.py``. Several of
those sit at ``require_api_actor`` because the *service* carries the real check
(``can_manage_presets``, ``ensure_can_manage_sync``) — mirroring the router's
gate rather than guessing a stricter one keeps the two surfaces answering the
same question, and the service still refuses a caller who lacks the role.

``list_race_rooms`` follows the same rule: ``GET /race-rooms/open`` sits at
``require_api_actor`` and the service admits staff or a sync admin.
"""

from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from application.errors import require_found
from application.services import (
    MatchService,
    PresetService,
    RaceRoomProfileService,
    RacetimeRoomService,
    RandomizerCredentialService,
    SeedGenerationService,
    SpeedGamingSyncService,
    TriforceTextService,
)
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    PresetDetail,
    PresetSummary,
    RaceRoomInfo,
    RaceRoomProfileInfo,
    RandomizerInfo,
    SpeedGamingEpisode,
    SpeedGamingLink,
    TenantArg,
    TriforceTextEntry,
)
from models import FeatureFlag


def _room(room) -> RaceRoomInfo:
    return RaceRoomInfo(
        id=room.id,
        slug=room.slug,
        category=room.category,
        room_name=room.room_name,
        status=getattr(room.status, 'value', room.status),
        match_id=room.match_id,
        opened_at=room.opened_at,
    )


async def list_presets(
    tenant: TenantArg = None,
    randomizer: Optional[str] = None,
) -> List[PresetSummary]:
    """List the community's seed-rolling presets.

    Requires the preset-manager role (or staff). Returns names and randomizers
    only; use `get_preset` for one preset's settings.
    """
    presets = await PresetService().list_presets(current_actor().user)
    wanted = (randomizer or '').strip().lower()
    return [
        PresetSummary.model_validate(p, from_attributes=True)
        for p in presets
        if not wanted or p.randomizer.lower() == wanted
    ]


async def get_preset(
    preset_id: int,
    tenant: TenantArg = None,
) -> PresetDetail:
    """Get one preset, including the full settings payload sent to the randomizer.

    Requires the preset-manager role (or staff).
    """
    preset = await PresetService().get_preset(current_actor().user, preset_id)
    detail = PresetDetail.model_validate(preset, from_attributes=True)
    detail.settings = preset.settings
    return detail


async def list_race_room_profiles(
    tenant: TenantArg = None,
) -> List[RaceRoomProfileInfo]:
    """List the racetime.gg room configurations tournaments can roll rooms with."""
    profiles = await RaceRoomProfileService().list_profiles(current_actor().user)
    return [
        RaceRoomProfileInfo.model_validate(p, from_attributes=True) for p in profiles
    ]


async def list_race_rooms(
    tenant: TenantArg = None,
) -> List[RaceRoomInfo]:
    """List the community's open racetime.gg rooms — those not yet finished or cancelled.

    Requires staff or sync admin. Use `get_race_room` for the room attached to one match,
    including rooms that have already ended.
    """
    rooms = await RacetimeRoomService().list_open_rooms_for_current_tenant(current_actor().user)
    return [_room(room) for room in rooms]


async def get_race_room(
    match_id: int,
    tenant: TenantArg = None,
) -> RaceRoomInfo:
    """Get the racetime.gg room opened for one match, in whatever state it is in."""
    match = require_found(
        await MatchService().get_by_id(match_id, prefetch_relations=False), 'Match'
    )
    room = require_found(await RacetimeRoomService().get_for_match(match), 'Race room')
    return _room(room)


async def list_speedgaming_links(
    tenant: TenantArg = None,
) -> List[SpeedGamingLink]:
    """List the SpeedGaming events synced into this community's tournaments.

    Requires the sync-admin role (or staff). `last_status` and `last_error`
    report how the most recent sync went.
    """
    links = await SpeedGamingSyncService().list_links(current_actor().user)
    return [
        SpeedGamingLink(
            id=link.id,
            tournament_id=link.tournament_id,
            event_slug=link.event_slug,
            content_type=link.content_type,
            active=link.active,
            last_synced_at=link.last_synced_at,
            last_status=link.last_status,
            last_error=link.last_error,
        )
        for link in links
    ]


async def list_speedgaming_episodes(
    link_id: int,
    tenant: TenantArg = None,
) -> List[SpeedGamingEpisode]:
    """List the episodes pulled from one SpeedGaming event link, and how each synced.

    Requires the sync-admin role (or staff). `link_id` comes from
    `list_speedgaming_links`.
    """
    episodes = await SpeedGamingSyncService().list_episodes(
        current_actor().user, link_id
    )
    return [
        SpeedGamingEpisode(
            id=ep.id,
            sg_episode_id=ep.sg_episode_id,
            title=ep.title,
            scheduled_at=ep.scheduled_at,
            sync_status=getattr(ep.sync_status, 'value', ep.sync_status),
            synced_at=ep.synced_at,
            sync_error=ep.sync_error,
        )
        for ep in episodes
    ]


async def list_randomizers(
    tenant: TenantArg = None,
) -> List[RandomizerInfo]:
    """List the randomizers this community can roll seeds from.

    A backend that needs an API key appears only once the community has supplied
    that credential, so this is what a preset may actually name — not the full
    catalogue the app knows about.
    """
    configured = await RandomizerCredentialService().configured_randomizers()
    return [
        RandomizerInfo(
            randomizer=name,
            supports_triforce_texts=SeedGenerationService.supports_triforce_texts(name),
        )
        for name in SeedGenerationService.available_randomizers(configured)
    ]


async def list_triforce_texts(
    tournament_id: int,
    tenant: TenantArg = None,
    status: Optional[str] = None,
) -> List[TriforceTextEntry]:
    """List a tournament's submitted triforce texts — the moderation queue.

    `status` filters to `pending`, `approved` or `rejected`; omit it for
    everything submitted.

    Requires staff, or being an admin of this tournament. That check is
    per-tournament, so `TriforceTextService` makes it rather than the registry gate.
    """
    rows = await TriforceTextService().list_for_moderation(
        tournament_id, status=status, actor=current_actor().user,
    )
    return [
        TriforceTextEntry(
            id=row.id,
            tournament_id=row.tournament_id,
            text=row.text,
            author=row.author,
            # ``approved`` is tri-state on the model: null is still pending.
            status=(
                'pending' if row.approved is None
                else 'approved' if row.approved else 'rejected'
            ),
            submitted_by=row.user.preferred_name if row.user else None,
            created_at=row.created_at,
        )
        for row in rows
    ]


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_presets, gate=Gate.ACTOR, title='List presets')
    register(mcp, get_preset, gate=Gate.ACTOR, title='Get preset')
    register(mcp, list_randomizers, gate=Gate.ACTOR, title='List randomizers')
    register(
        mcp, list_triforce_texts, gate=Gate.ACTOR,
        feature=FeatureFlag.TRIFORCE_TEXTS, title='List triforce texts',
    )
    register(
        mcp, list_race_room_profiles, gate=Gate.ACTOR,
        feature=FeatureFlag.RACETIME_ROOMS, title='List race room profiles',
    )
    register(
        mcp, list_race_rooms, gate=Gate.ACTOR,
        feature=FeatureFlag.RACETIME_ROOMS, title='List open race rooms',
    )
    register(
        mcp, get_race_room, gate=Gate.ACTOR,
        feature=FeatureFlag.RACETIME_ROOMS, title='Get a match race room',
    )
    register(
        mcp, list_speedgaming_links, gate=Gate.ACTOR,
        feature=FeatureFlag.SPEEDGAMING_ETL, title='List SpeedGaming links',
    )
    register(
        mcp, list_speedgaming_episodes, gate=Gate.ACTOR,
        feature=FeatureFlag.SPEEDGAMING_ETL, title='List SpeedGaming episodes',
    )
