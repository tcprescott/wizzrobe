"""A qualifier's live race settings: the bot, room timings, lock and Discord post."""

from typing import Any, Dict, Mapping, Optional

from application.services._tournament_group_race import discord_snowflake
from application.tenant_context import require_tenant_id

LIVE_RACE_FIELDS = frozenset({
    'live_race_bot_id', 'live_race_room_open_minutes', 'live_race_seed_post_minutes',
    'live_race_lock_at_seed', 'live_race_announce_channel_id', 'live_race_announce_role_id',
})
_DISCORD_IDS = {
    'live_race_announce_channel_id': 'The announcement channel',
    'live_race_announce_role_id': 'The ping role',
}
MAX_LEAD_MINUTES = 24 * 60


async def live_race_updates(values: Mapping[str, Any], current: Optional[Any] = None) -> Dict[str, Any]:
    """The validated columns for the live race settings given; a ``None`` id or bot clears it."""
    from application.services.racetime_bot_service import RacetimeBotService

    unknown = set(values) - LIVE_RACE_FIELDS
    if unknown:
        raise TypeError(f'unexpected qualifier fields: {", ".join(sorted(unknown))}')
    out: Dict[str, Any] = {}
    for key, value in values.items():
        if key in _DISCORD_IDS:
            out[key] = discord_snowflake(value, _DISCORD_IDS[key])
        elif key == 'live_race_bot_id':
            if value is not None and not await RacetimeBotService().is_authorized_for_tenant(
                int(value), require_tenant_id(),
            ):
                raise ValueError("That racetime bot isn't available to this community")
            out[key] = value
        elif key == 'live_race_lock_at_seed':
            if value is not None:
                out[key] = bool(value)
        elif value is not None:
            minutes = int(value)
            if not 0 <= minutes <= MAX_LEAD_MINUTES:
                raise ValueError('Room and seed times must be between 0 and 1440 minutes before the start')
            out[key] = minutes
    open_minutes = out.get('live_race_room_open_minutes', getattr(current, 'live_race_room_open_minutes', 60))
    seed_minutes = out.get('live_race_seed_post_minutes', getattr(current, 'live_race_seed_post_minutes', 10))
    if seed_minutes > open_minutes:
        raise ValueError('The seed has to post after the room opens')
    return out
