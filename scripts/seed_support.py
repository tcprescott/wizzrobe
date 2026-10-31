"""The fixture registry and the small helpers every dev-seed module shares.

Two things live here rather than in ``seed_dev.py``: the **identity registry**
(who the fixtures are and how their discord ids are derived), because
``tests/test_seed_coverage.py`` asserts against it and importing ``seed_dev``
from a test would drag the whole seed in; and ``backfill``, because four seed
modules need it.

The standard these serve is written down in
[docs/reference/dev-seed.md](../docs/reference/dev-seed.md).
"""

import zlib
from typing import Any

from tortoise.models import Model

# Ids are derived from the username, never hand-assigned: a duplicated id silently renamed a fixture.
USER_SPECS = [
    ("staff_user",   "Staff User"),
    ("proctor_user", "Proctor User"),
    ("sm_user",      "SM User"),
    ("player_one",   "Player One"),
    ("player_two",   "Player Two"),
    ("player_three", "Player Three"),
    ("player_four",  "Player Four"),
    ("outsider",     "Hopeful Outsider"),
    # No tenant role anywhere on purpose: platform authority with zero local grants.
    ("super_admin",  "Platform Owner"),
    ("local_only",   "Local Only"),
    # One holder per per-tenant role, holding nothing else: staff_user passes every gate, so it can't
    # catch a surface gating on staff-ness. tests/test_seed_coverage.py fails on a role with no holder.
    ("equip_manager", "Equipment Manager"),
    ("cc_user",       "Crew Coordinator"),
    ("vc_user",       "Volunteer Coordinator"),
    ("preset_mgr",    "Preset Manager"),
    ("sync_user",     "Sync Admin"),
    ("qual_admin",    "Qualifier Admin"),
    ("proctor_only",  "Proctor Only"),
    ("sm_only",       "Stream Manager Only"),
    ("triforce_sub",  "Triforce Submitter"),
    ("volunteer_only", "Volunteer Only"),
    ("checkin_desk",  "Check-in Desk"),
    ("removed_member", "Removed Member"),
]

RACER_SPECS = [
    ("racer_05", "Racer Five",   False),
    ("racer_06", "Racer Six",    False),
    ("racer_07", "Racer Seven",  True),
    ("racer_08", "Racer Eight",  False),
    ("racer_09", "Racer Nine",   False),
    ("racer_10", "Racer Ten",    True),
    ("racer_11", "Racer Eleven", False),
    ("racer_12", "Racer Twelve", False),
]

FULL_RACERS = tuple(name for name, _display, full in RACER_SPECS if full)

ALL_FIXTURE_USERNAMES = (
    [name for name, _display in USER_SPECS]
    + [name for name, _display, _full in RACER_SPECS]
)

# Reserved id block, far from any real account a developer might hold.
_ID_BLOCK = 100_000_000_000_000_000


def fixture_discord_id(username: str) -> str:
    """Derive a stable dev discord id from a fixture's username.

    Deterministic (``crc32``, not ``hash()``, whose seed varies per process), so
    a fixture keeps its id across runs and machines and a developer's
    ``app.storage.user`` survives a re-seed. Collisions are not merely unlikely:
    ``tests/test_seed_coverage.py`` asserts the derived set is unique, so a new
    fixture whose name happened to collide fails the suite instead of quietly
    renaming its neighbour — which is exactly what a hand-assigned id once did.
    """
    return str(_ID_BLOCK + zlib.crc32(username.encode()) % 100_000_000)


async def backfill(instance: Model, **values: Any) -> bool:
    """Set each attribute that is currently ``None``; save once if any changed.

    ``get_or_create`` defaults only apply on the *create*, so a field added to a
    fixture spec today stays NULL forever on every dev database that already
    holds the row — the same seed then means something different depending on
    when the database was made. This writes only where the value is still unset,
    which converges an older fixture without overwriting anything a developer
    changed by hand.

    Returns whether anything was written, so a caller can fold the save into its
    own dirty tracking. Deliberately ignores falsey-but-set values (``False``,
    ``0``, ``''``) — only ``None`` counts as "never filled in".
    """
    dirty = [
        key for key, value in values.items()
        if value is not None and getattr(instance, key) is None
    ]
    for key in dirty:
        setattr(instance, key, values[key])
    if dirty:
        await instance.save()
    return bool(dirty)
