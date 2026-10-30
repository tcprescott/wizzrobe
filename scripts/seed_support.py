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

#: Fixture users, keyed by the username the whole seed refers to them by. Their
#: discord ids are **derived** from that username (:func:`fixture_discord_id`)
#: rather than hand-assigned: two fixtures once shared a hand-written id, and the
#: second one silently renamed the first — taking the membership gate's only
#: fixture with it. A derived id cannot be typed twice.
USER_SPECS = [
    ("staff_user",   "Staff User"),
    ("proctor_user", "Proctor User"),
    ("sm_user",      "SM User"),
    ("player_one",   "Player One"),
    ("player_two",   "Player Two"),
    ("player_three", "Player Three"),
    ("player_four",  "Player Four"),
    # A member of **no** community, with a pending join request against
    # 'fledgling' (see seed_fledgling): the fixture for the membership gate's
    # own door. Without it the only way to see the join page in dev is to
    # delete your own membership.
    ("outsider",     "Hopeful Outsider"),
    # Deliberately granted no *tenant* role anywhere (see seed_super_admin):
    # the dev fixture for "platform authority, zero local grants", which is
    # what /platform and the super-admin's in-tenant access need to be
    # exercised against.
    ("super_admin",  "Platform Owner"),
    # A member of the 'default' tenant only (see seed_for_tenant), holding a
    # role there and enrolled in nothing: the fixture that makes
    # per-community people scoping visible in the dev loop. They must appear
    # in tenant A's borrower/owner pickers and in neither of tenant B's.
    ("local_only",   "Local Only"),
    # One holder per per-tenant role, holding that role and nothing else.
    # staff_user satisfies every predicate in the admin area, so a surface that
    # gates on staff-ness where it means to gate on a capability looks correct
    # until one of these logs in. tests/test_seed_coverage.py fails when a new
    # Role arrives without a holder.
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
    # Removed from every community by staff, and kept out by that removal even
    # where the door would let anyone in (see seed_access): the fixture for the
    # join page's "staff removed you" state.
    ("removed_member", "Removed Member"),
]

#: Racers who exist to fill a group play-in start list — staff-run races of ten
#: or more, the one shape in this app that is not head-to-head. Deliberately
#: thin: a membership and an enrolment, so they are valid entrants and nothing
#: else, leaving the four named players as the fixtures every other surface is
#: built on. The two marked ``full`` also volunteer, so the assignable pool grows
#: without twelve people's worth of availability rows.
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

#: The racers given the full participant treatment (volunteer profile + declared
#: availability), named so ``seed_volunteers`` and the docs cannot drift apart.
FULL_RACERS = tuple(name for name, _display, full in RACER_SPECS if full)

ALL_FIXTURE_USERNAMES = (
    [name for name, _display in USER_SPECS]
    + [name for name, _display, _full in RACER_SPECS]
)

#: Base of the reserved id block. Real snowflakes encode a timestamp, so ids in
#: this range read as "created 2017-ish" — plausible enough for a dev fixture and
#: nowhere near an account a developer might also hold.
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
