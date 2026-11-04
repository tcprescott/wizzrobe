"""A tournament's group race settings: their validation and what a group race implies."""

from typing import Any, Dict, Mapping, Optional

GROUP_RACE_FIELDS = frozenset({
    'racetime_group_race', 'racetime_lock_at_seed',
    'racetime_announce_channel_id', 'racetime_announce_role_id',
})
_DISCORD_IDS = {
    'racetime_announce_channel_id': 'The announcement channel',
    'racetime_announce_role_id': 'The ping role',
}


def discord_snowflake(value: Any, what: str) -> Optional[str]:
    """A Discord id as digits, or ``None`` for blank."""
    text = str(value).strip() if value is not None else ''
    if not text:
        return None
    if not text.isdigit() or len(text) > 20:
        raise ValueError(f"{what} isn't a Discord id")
    return text


def group_race_updates(values: Mapping[str, Any]) -> Dict[str, Any]:
    """The validated columns for the group race settings given; a ``None`` id clears it."""
    unknown = set(values) - GROUP_RACE_FIELDS
    if unknown:
        raise TypeError(f'unexpected tournament fields: {", ".join(sorted(unknown))}')
    out: Dict[str, Any] = {}
    for key, value in values.items():
        if key in _DISCORD_IDS:
            out[key] = discord_snowflake(value, _DISCORD_IDS[key])
        elif value is not None:
            out[key] = bool(value)
    return out


# A group race has no player list to invite, so only the bot can open and run its rooms.
GROUP_RACE_IMPLIES = {'racetime_auto_create_rooms': True, 'racetime_bot_runs_room': True}
