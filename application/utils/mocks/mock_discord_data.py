"""Fixture data for :class:`MockDiscordService` (development / test only).

This is the single place to define what the fake Discord API "knows about" —
servers (guilds), their roles, per-member role assignments, and who may manage
each server. ``MockDiscordService`` reads everything from here so its methods
stay consistent with one another (a role id returned by ``list_guild_roles`` is
the same id ``get_member_role_ids`` may hand back, etc.).

**Kept in sync with ``scripts/seed_dev.py``:** the two guild ids and the four
*mapped* role ids/names match the dev seed's ``TENANT_SPECS`` and
``role_mapping_specs``, so in ``MOCK_DISCORD`` mode a user's mock Discord roles
actually resolve against the seeded ``DiscordRoleMapping`` rows and login role
sync grants the expected app roles. Extra, unmapped roles are included so the
"Add mapping" UI has a realistic set of choices to pick from.

To enrich the mock: add guilds to :data:`MOCK_GUILDS`, add roles to a guild's
``roles`` list, pin a specific user's roles in :data:`MOCK_MEMBER_ROLES`, or add
a never-signed-in member to :data:`MOCK_GUILD_ROSTER`.
"""

from typing import Dict, List, Set, Union

# --- Guild ids (match seed_dev.TENANT_SPECS) --------------------------------
GUILD_WIZ_DEFAULT = 1000000000000000001
GUILD_SECOND = 1000000000000000002
DEFAULT_GUILD_ID = GUILD_WIZ_DEFAULT

# --- Role ids ---------------------------------------------------------------
# Mapped to app roles by seed_dev.role_mapping_specs — keep these ids/names.
ROLE_STAFF = 2000000000000000001
ROLE_PROCTOR = 2000000000000000002
ROLE_STREAM_MANAGER = 2000000000000000003
ROLE_VOLUNTEER = 2000000000000000004
# Unmapped extras — present in the server, available to map in the UI.
ROLE_ADMIN = 2000000000000000010
ROLE_COMMENTATOR = 2000000000000000011
ROLE_TRACKER = 2000000000000000012
ROLE_RESTREAMER = 2000000000000000013

# Unmapped roles that the outbound role sync's checklist can hand out.
ROLE_COMPETITOR = 2000000000000000030
ROLE_NOW_PLAYING = 2000000000000000031
ROLE_VIP = 2000000000000000032
ROLE_ATTENDEE = 2000000000000000033

# Not mappable: Discord's @everyone and a bot's own managed role, so the
# "Add mapping" picker has something to leave out.
ROLE_EVERYONE = 2000000000000000000
ROLE_BOT = 2000000000000000020

_STANDARD_ROLES: List[Dict[str, object]] = [
    # ``position`` is Discord's role order (higher outranks lower). The bot's own
    # role sits below Admin and Staff, so the role sync's picker has roles it
    # must refuse as "above the bot's role".
    {"id": ROLE_ADMIN, "name": "Admin", "color": 0xE74C3C, "mappable": True, "position": 14},
    {"id": ROLE_STAFF, "name": "Wizzrobe Staff", "color": 0x9B59B6, "mappable": True, "position": 13},
    {"id": ROLE_BOT, "name": "Wizzrobe", "color": 0x5865F2, "mappable": False, "position": 12},
    {"id": ROLE_PROCTOR, "name": "Proctors", "color": 0x3498DB, "mappable": True, "position": 11},
    {"id": ROLE_STREAM_MANAGER, "name": "Stream Managers", "color": 0xE67E22, "mappable": True, "position": 10},
    {"id": ROLE_VOLUNTEER, "name": "Volunteers", "color": 0x2ECC71, "mappable": True, "position": 9},
    {"id": ROLE_COMMENTATOR, "name": "Commentators", "color": 0, "mappable": True, "position": 8},
    {"id": ROLE_TRACKER, "name": "Trackers", "color": 0x1ABC9C, "mappable": True, "position": 7},
    {"id": ROLE_RESTREAMER, "name": "Restreamers", "color": 0xF1C40F, "mappable": True, "position": 6},
    {"id": ROLE_VIP, "name": "SGL VIP", "color": 0xF1C40F, "mappable": True, "position": 5},
    {"id": ROLE_ATTENDEE, "name": "SGL Attendee", "color": 0x1ABC9C, "mappable": True, "position": 4},
    {"id": ROLE_COMPETITOR, "name": "Competitor", "color": 0x3BA55D, "mappable": True, "position": 3},
    {"id": ROLE_NOW_PLAYING, "name": "Now Playing", "color": 0xE67E22, "mappable": True, "position": 2},
    {"id": ROLE_EVERYONE, "name": "@everyone", "color": 0, "mappable": False, "position": 0},
]

# owner_id 1 matches the dev seed's first user; member_can_manage_guild treats
# the owner (and Admin/Manage-Server holders) as able to manage the server.
MOCK_GUILDS: Dict[int, dict] = {
    GUILD_WIZ_DEFAULT: {
        "name": "Wizzrobe Default",
        "owner_id": 1,
        "roles": [dict(r) for r in _STANDARD_ROLES],
    },
    GUILD_SECOND: {
        "name": "Second Community",
        "owner_id": 1,
        "roles": [dict(r) for r in _STANDARD_ROLES],
        # So dev shows Admin → Settings → Discord Events locked and paused.
        "bot_can_create_events": False,
    },
}

# Pin specific users' roles here (keyed by Discord user id) to script precise
# scenarios. Anyone not listed falls back to :func:`_default_member_roles`.
MOCK_MEMBER_ROLES: Dict[int, Set[int]] = {}

# Guild members who have never signed in to Wizzrobe (discord id -> username).
# "Sync All Users" provisions an account for each one holding a mapped role, so
# in MOCK_DISCORD mode the button visibly creates users. The ids sit well above
# ``seed_support.fixture_discord_id``'s block so they never collide with a
# seeded user. Their roles come from :func:`member_role_ids` like anyone else's.
MOCK_GUILD_ROSTER: Dict[int, str] = {
    3000000000000000002: 'guild_only_proctor',
    3000000000000000003: 'guild_only_staff',
    3000000000000000005: 'guild_only_stream_manager',
}

# More never-signed-in guild members, holding no mapped role, so Admin → Users ›
# Import from Discord has someone to bring in. One is a bot, which the import
# skips. Same id block as ``MOCK_GUILD_ROSTER``.
MOCK_GUILD_IMPORTABLE: Dict[int, str] = {
    3000000000000000101: 'aerinon',
    3000000000000000102: 'bigbadbeef',
    3000000000000000103: 'cassidoo_rando',
    3000000000000000104: 'darkwing99',
    3000000000000000105: 'echo.zelda',
    3000000000000000199: 'carl-bot',
}
MOCK_GUILD_BOTS: Set[int] = {3000000000000000199}


# Discord ids that are **not** in any mock server. Everyone else is, which is
# what the role-sync fixtures above already assume. ``outsider`` is the seeded
# account that belongs to no community (``seed_support.fixture_discord_id``,
# pinned by ``tests/test_seed_coverage.py``): keeping it out of every server is
# what keeps the join door reachable in dev when a community turns on Discord
# auto-join.
MOCK_NON_GUILD_MEMBERS: Set[int] = {100_000_000_093_103_805}


def guild(guild_id: int) -> dict:
    """The guild record for ``guild_id``, or the default guild if unknown.

    Never dead-ends: a fresh dev DB may link a tenant to some other id, and the
    mock should still return a plausible server rather than an error.
    """
    return MOCK_GUILDS.get(guild_id) or MOCK_GUILDS[DEFAULT_GUILD_ID]


def all_guilds() -> List[Dict[str, object]]:
    return [{"id": gid, "name": g["name"]} for gid, g in MOCK_GUILDS.items()]


def roles_for(guild_id: int) -> List[Dict[str, object]]:
    return [dict(r) for r in guild(guild_id)["roles"]]


def _default_member_roles(guild_id: int, user_id: int) -> Set[int]:
    """A deterministic, varied role set so mock role sync does real work.

    Everyone is a Volunteer; higher roles are handed out by simple arithmetic on
    the user id so different impersonated users exercise different mappings.
    """
    granted: Set[int] = {ROLE_VOLUNTEER}
    if user_id % 2 == 0:
        granted.add(ROLE_PROCTOR)
    if user_id % 3 == 0:
        granted.add(ROLE_STAFF)
    if user_id % 5 == 0:
        granted.add(ROLE_STREAM_MANAGER)
    valid = {r["id"] for r in roles_for(guild_id)}
    return granted & valid


def member_role_ids(guild_id: int, user_id: int) -> Set[int]:
    if user_id in MOCK_MEMBER_ROLES:
        return set(MOCK_MEMBER_ROLES[user_id]) & {r["id"] for r in roles_for(guild_id)}
    return _default_member_roles(guild_id, user_id)


def bot_access(guild_id: int) -> Dict[str, Union[bool, int]]:
    """The mock bot holds Manage Roles; its own role is ``ROLE_BOT``'s position."""
    top = next(int(str(r["position"])) for r in roles_for(guild_id) if r["id"] == ROLE_BOT)
    return {"manage_roles": True, "top_position": top}


def give_role(guild_id: int, user_id: int, role_id: int, add: bool) -> None:
    """Record a role the mock bot added or removed, so the next read sees it."""
    held = member_role_ids(guild_id, user_id)
    if add:
        held.add(role_id)
    else:
        held.discard(role_id)
    MOCK_MEMBER_ROLES[user_id] = held


def is_guild_member(guild_id: int, user_id: int) -> bool:
    return user_id not in MOCK_NON_GUILD_MEMBERS


def user_can_manage(guild_id: int, user_id: int) -> bool:
    """Owners manage their server; everyone else does in the mock (dev convenience).

    Kept permissive so an impersonated dev user can always exercise the connect
    flow. Pin a scenario by overriding this in a test if you need a denial.
    """
    return True


# --- Scheduled events (mock) ------------------------------------------------
# In-memory store so the Discord Events reconciler (PR 8) runs end-to-end under
# MOCK_DISCORD without a live bot. Keyed by a synthetic event id; ids increment
# from a fixed base so they look like snowflakes and stay deterministic within a
# process. Reset between tests via :func:`reset_scheduled_events`.
_SCHEDULED_EVENT_ID_BASE = 3000000000000000000
_scheduled_events: Dict[int, dict] = {}
_next_scheduled_event_id = _SCHEDULED_EVENT_ID_BASE


_guilds_without_event_permission: Set[int] = set()


def set_bot_can_create_events(guild_id: int, allowed: bool) -> None:
    """Test/dev hook: pretend the bot lacks Create Events in ``guild_id``."""
    if allowed:
        _guilds_without_event_permission.discard(guild_id)
    else:
        _guilds_without_event_permission.add(guild_id)


def bot_can_create_events(guild_id: int) -> bool:
    if guild_id in _guilds_without_event_permission:
        return False
    return guild_id not in MOCK_GUILDS or bool(MOCK_GUILDS[guild_id].get("bot_can_create_events", True))


def reset_scheduled_events() -> None:
    """Clear the mock scheduled-event store (test isolation)."""
    global _next_scheduled_event_id
    _scheduled_events.clear()
    _guilds_without_event_permission.clear()
    _next_scheduled_event_id = _SCHEDULED_EVENT_ID_BASE


def create_scheduled_event(guild_id: int, **fields: object) -> int:
    """Record a new mock scheduled event; return its synthetic id."""
    global _next_scheduled_event_id
    event_id = _next_scheduled_event_id
    _next_scheduled_event_id += 1
    _scheduled_events[event_id] = {'id': event_id, 'guild_id': guild_id, **fields}
    return event_id


def edit_scheduled_event(event_id: int, **fields: object) -> bool:
    """Update a mock scheduled event in place. False if it's unknown."""
    event = _scheduled_events.get(event_id)
    if event is None:
        return False
    event.update(fields)
    return True


def delete_scheduled_event(event_id: int) -> bool:
    """Remove a mock scheduled event. False if it was already gone."""
    return _scheduled_events.pop(event_id, None) is not None


def scheduled_events_for(guild_id: int) -> List[dict]:
    """Every mock scheduled event currently in a guild."""
    return [dict(e) for e in _scheduled_events.values() if e['guild_id'] == guild_id]
