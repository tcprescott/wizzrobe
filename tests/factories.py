"""Canonical test factories/helpers shared across the suite.

Two helpers were copy-pasted (with drifting signatures) across dozens of test
modules; they live here now so a single definition is imported everywhere.

- ``utc(y, mo, d, h=0, mi=0)`` builds a UTC-aware ``datetime``. This is the
  majority signature; a couple of modules previously used incompatible
  positional conventions and have been converted to this one.
- ``make_user(...)`` creates a real ``User`` row (requires the ``db`` fixture).
  Many modules keep their own bespoke ``make_user``/``_user`` — some build
  ``SimpleNamespace``/``MagicMock`` stand-ins (no DB), some set extra fields
  like ``display_name`` or add roles. Those are intentionally left in place;
  this factory covers the plain "create a user row" case.
- ``make_audit_double()`` stands in for ``AuditService`` on a hand-built
  service under test, without silencing the event bus.
"""

from datetime import datetime, timezone
from functools import partial
from unittest.mock import AsyncMock, MagicMock

from models import User

UTC = timezone.utc


def utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """A timezone-aware UTC ``datetime`` for the given calendar fields."""
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


async def make_user(discord_id: int = 1, username: str | None = None, **kwargs) -> User:
    """Create and return a real ``User`` (needs the ``db`` fixture).

    ``username`` defaults to ``f'user{discord_id}'``. Extra model fields
    (``display_name``, ``is_active`` …) pass through via ``kwargs``.
    """
    if username is None:
        username = f'user{discord_id}'
    return await User.create(discord_id=discord_id, username=username, **kwargs)


async def join_community(*users: User) -> None:
    """Make ``users`` members of the ambient tenant, as real participants are.

    Roster writes (match players and crew, shift assignments, bracket entrant
    links) refuse anyone outside the community, so a fixture that means
    "a player here" needs the membership a bare ``User.create`` doesn't give.
    """
    from application.repositories._tenant import current_tenant_id
    from application.repositories.tenant_membership_repository import TenantMembershipRepository

    for user in users:
        await TenantMembershipRepository.add(user, current_tenant_id())


async def make_member(discord_id: int = 1, username: str | None = None, **kwargs) -> User:
    """:func:`make_user`, joined to the ambient tenant."""
    user = await make_user(discord_id, username, **kwargs)
    await join_community(user)
    return user


def make_audit_double() -> MagicMock:
    """An ``AuditService`` stand-in that still publishes on the real event bus.

    A blanket ``MagicMock()`` swallows ``write_and_publish`` whole, so a service
    converted from the hand-rolled ``write_log`` + ``event_bus.publish`` pair
    would stop emitting events *under test only* — the failure mode that kept
    the conversion backlogged. Here ``write_and_publish`` runs its real body
    bound to this double: it calls the mocked ``write_log`` (so the existing
    call-args assertions keep working, and nothing touches the database) and
    then publishes for real.
    """
    from application.services.audit_service import AuditService

    double = MagicMock()
    double.write_log = AsyncMock()
    double.write_and_publish = partial(AuditService.write_and_publish, double)
    return double


async def set_setting(name: str, value: str, tenant_id: int | None = None) -> None:
    """Store a setting where SystemConfigService reads it.

    An event's own setting (capacity, hours, dates…) goes on the community's
    default event; any other is a community row. Written raw, so a test can
    store what the service would refuse.
    """
    from datetime import date

    from application.services.system_config_service import EVENT_CONFIG_KEYS, EVENT_DATE_FIELDS
    from application.tenant_context import get_current_tenant_id
    from models import Event, SystemConfiguration

    tenant_id = tenant_id if tenant_id is not None else (get_current_tenant_id() or 1)
    if name in EVENT_DATE_FIELDS:
        await Event.filter(tenant_id=tenant_id, is_default=True).update(
            **{EVENT_DATE_FIELDS[name]: date.fromisoformat(value)},
        )
    elif name in EVENT_CONFIG_KEYS:
        event = await Event.get(tenant_id=tenant_id, is_default=True)
        await Event.filter(id=event.id).update(config={**(event.config or {}), name: value})
    else:
        await SystemConfiguration.create(name=name, value=value, tenant_id=tenant_id)
