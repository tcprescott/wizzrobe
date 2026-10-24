"""Timezone Service — resolves which wall clock each viewer reads times on.

The app stores UTC and renders local. *Which* local is a two-tier decision:

**Tier 1 — the community decides whether it decides.** ``Tenant.config['timezone']``
holds ``{'mode': 'pinned'|'user', 'name': '<IANA>'}``. A ``pinned`` community
renders every time on one clock for everyone: correct for an on-site event, where
"the match is at 14:30" means 14:30 *at the venue* regardless of where the reader
lives. A ``user`` community defers to each viewer, which is what an online
tournament with players across the world needs.

**Tier 2 — the viewer.** When the community defers: the user's saved preference
first, then the zone their browser reported, then the community's own zone. A
signed-out or browser-less viewer therefore still gets a sensible clock rather
than an error.

``name`` is meaningful in both modes: pinned uses it as *the* clock, and ``user``
mode uses it as the floor everything falls back to — including every surface that
structurally cannot have a viewer (see below).

**Output that one viewer cannot own renders in the tenant's zone.** The test is
whether the artifact has a single identifiable reader:

- **No reader, or many** — a cached spectator page, a channel-wide post, a
  worker's tick, an operating-hours rule the whole community is judged against.
  These take :meth:`tenant_timezone_name` explicitly, never the ambient zone,
  because there is no one whose clock would be the right answer.
- **One reader, and it is the requester** — every page, report window, and
  export the viewer triggered and reads themselves. These use the ambient zone;
  that *is* the viewer's clock. An export additionally labels the zone it used
  (``volunteer_export_service``), because a CSV can be forwarded to someone who
  did not generate it and a bare "14:30" would then be unattributable.
- **One reader, and it is someone else** — a DM, a web push. Discord sidesteps
  this entirely with native ``<t:…>`` markup that each recipient's own client
  localizes, which is why no Discord path formats a literal time string.

Storage is ``Tenant.config['timezone']`` — the documented home for per-tenant
knobs, same as ``TenantThemeService`` — plus a nullable ``User.timezone`` column
for the personal preference. ``Tenant`` is the tenancy discriminator (never
tenant-scoped), so this reads/writes through the cross-tenant ``TenantRepository``.
"""

import logging
from typing import Optional

from application.errors import require_found
from application.repositories.tenant_repository import TenantRepository
from application.repositories.user_repository import UserRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import get_current_tenant_id, require_tenant_id
from application.timezone_context import (
    FALLBACK_TIMEZONE,
    HOUR_CYCLES,
    is_valid_timezone,
)
from models import User

logger = logging.getLogger(__name__)

# Config sub-key under Tenant.config where the timezone settings dict lives.
CONFIG_KEY = 'timezone'

# Community pins one clock for everyone; the per-user preference is ignored.
MODE_PINNED = 'pinned'
# Community defers to each viewer (profile → browser → community default).
MODE_USER = 'user'
MODES = (MODE_PINNED, MODE_USER)

# What a community with no stored setting gets. New communities follow the
# viewer, because getting this wrong for an online event is the hazard this
# feature exists to remove; a community that wants one fixed clock says so.
# Existing communities were migrated to an explicit pin, so this default only
# ever applies to new ones.
DEFAULT_MODE = MODE_USER

# A short, curated list for the admin/profile pickers. Not exhaustive — both
# pickers accept any IANA name — just the zones that cover most of the player
# base without making the dropdown unusable.
COMMON_TIMEZONES: tuple[str, ...] = (
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Phoenix',
    'America/Los_Angeles',
    'America/Anchorage',
    'Pacific/Honolulu',
    'America/Toronto',
    'America/Vancouver',
    'America/Mexico_City',
    'America/Bogota',
    'America/Sao_Paulo',
    'America/Argentina/Buenos_Aires',
    'UTC',
    'Europe/London',
    'Europe/Dublin',
    'Europe/Lisbon',
    'Europe/Madrid',
    'Europe/Paris',
    'Europe/Berlin',
    'Europe/Amsterdam',
    'Europe/Stockholm',
    'Europe/Warsaw',
    'Europe/Rome',
    'Europe/Athens',
    'Europe/Helsinki',
    'Europe/Kyiv',
    'Europe/Moscow',
    'Asia/Jerusalem',
    'Asia/Dubai',
    'Asia/Kolkata',
    'Asia/Bangkok',
    'Asia/Singapore',
    'Asia/Hong_Kong',
    'Asia/Shanghai',
    'Asia/Seoul',
    'Asia/Tokyo',
    'Australia/Perth',
    'Australia/Adelaide',
    'Australia/Brisbane',
    'Australia/Sydney',
    'Pacific/Auckland',
)


class TimezoneService:
    """Read/write the community's timezone policy and resolve a viewer's clock."""

    # --- tenant settings ---------------------------------------------------

    @staticmethod
    def _clean(stored: Optional[dict]) -> dict:
        """Normalize a stored settings blob to a full, valid ``{mode, name}``.

        Total by design: a missing key, an unknown mode, or a zone this system's
        tzdata cannot load all degrade to the defaults. The settings are read on
        every page build, so a hand-edited or stale blob must not be able to take
        the app down.
        """
        stored = stored if isinstance(stored, dict) else {}
        mode = stored.get('mode')
        if mode not in MODES:
            mode = DEFAULT_MODE
        name = stored.get('name')
        if not is_valid_timezone(name):
            name = FALLBACK_TIMEZONE
        return {'mode': mode, 'name': name}

    @staticmethod
    async def get_settings(tenant_id: Optional[int] = None) -> dict:
        """The community's ``{mode, name}``, defaults applied."""
        tenant_id = tenant_id if tenant_id is not None else get_current_tenant_id()
        if tenant_id is None:
            return {'mode': DEFAULT_MODE, 'name': FALLBACK_TIMEZONE}
        tenant = await TenantRepository.get_by_id(tenant_id)
        stored = (tenant.config or {}).get(CONFIG_KEY) if tenant else None
        return TimezoneService._clean(stored)

    @staticmethod
    async def tenant_timezone_name(tenant_id: Optional[int] = None) -> str:
        """The community's own clock — the zone for anything not viewer-specific.

        Use for shared or cached output, for tenant-anchored rules (tournament
        operating hours), and as the fallback when no viewer can be identified.
        Never raises: an unresolvable tenant yields :data:`FALLBACK_TIMEZONE`.
        """
        try:
            return (await TimezoneService.get_settings(tenant_id))['name']
        except Exception:
            logger.warning(
                'Falling back to %s: tenant timezone unreadable', FALLBACK_TIMEZONE, exc_info=True,
            )
            return FALLBACK_TIMEZONE

    @staticmethod
    async def set_settings(actor: User, mode: str, name: str) -> dict:
        """Persist the community's timezone policy (STAFF only)."""
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only Staff can change the community timezone.',
        )
        if mode not in MODES:
            raise ValueError('Timezone mode must be either "pinned" or "user".')
        name = (name or '').strip()
        if not is_valid_timezone(name):
            raise ValueError(
                f'"{name}" is not a recognised timezone. Use an IANA name like '
                'America/New_York or Europe/London.'
            )

        tenant_id = require_tenant_id()
        tenant = require_found(await TenantRepository.get_by_id(tenant_id), 'Tenant')
        settings = {'mode': mode, 'name': name}

        config = dict(tenant.config or {})
        config[CONFIG_KEY] = settings
        await TenantRepository.update(tenant, config=config)

        await AuditService().write_log(
            actor,
            AuditActions.TIMEZONE_UPDATED,
            {'tenant_id': tenant_id, 'mode': mode, 'timezone': name},
        )
        return settings

    # --- per-user preference -----------------------------------------------

    @staticmethod
    async def set_user_timezone(actor: User, name: Optional[str]) -> Optional[str]:
        """Save (or clear) ``actor``'s own timezone preference.

        Clearing returns them to browser detection. Self-service only — there is
        no path for one user to set another's clock, so no role check: the actor
        is the subject.
        """
        name = (name or '').strip() or None
        if name is not None and not is_valid_timezone(name):
            raise ValueError(
                f'"{name}" is not a recognised timezone. Use an IANA name like '
                'America/New_York or Europe/London.'
            )
        await UserRepository.set_timezone(actor, name)
        await AuditService().write_log(
            actor, AuditActions.USER_TIMEZONE_UPDATED, {'timezone': name},
        )
        return name

    @staticmethod
    async def set_user_hour_cycle(actor: User, hour_cycle: Optional[str]) -> Optional[str]:
        """Save (or clear) ``actor``'s own 12/24-hour preference.

        ``'12'`` or ``'24'``; empty or ``None`` returns them to browser
        detection. Self-service only, like :meth:`set_user_timezone`.
        """
        hour_cycle = (hour_cycle or '').strip() or None
        if hour_cycle is not None and hour_cycle not in HOUR_CYCLES:
            raise ValueError('Pick 12-hour, 24-hour or Detect automatically.')
        await UserRepository.set_hour_cycle(actor, hour_cycle)
        await AuditService().write_log(
            actor, AuditActions.USER_HOUR_CYCLE_UPDATED, {'hour_cycle': hour_cycle},
        )
        return hour_cycle

    # --- resolution --------------------------------------------------------

    @staticmethod
    def pick(
        settings: dict,
        user: Optional[User] = None,
        browser_timezone: Optional[str] = None,
    ) -> str:
        """The zone a viewer reads times on, given already-loaded ``settings``.

        ``pinned`` short-circuits everything — that is the point of pinning. In
        ``user`` mode the order is profile → browser → community default, so an
        explicit choice always beats detection and detection always beats a
        guess. Pure and sync so the page builder can re-run it after the user
        loads without paying for a second tenant read.
        """
        if settings.get('mode') == MODE_PINNED:
            return settings['name']

        preferred = getattr(user, 'timezone', None) if user is not None else None
        if is_valid_timezone(preferred):
            return preferred
        if is_valid_timezone(browser_timezone):
            return browser_timezone
        return settings['name']

    @staticmethod
    async def resolve(
        user: Optional[User] = None,
        browser_timezone: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ) -> str:
        """The zone this viewer should read times on. Never raises."""
        try:
            settings = await TimezoneService.get_settings(tenant_id)
        except Exception:
            logger.warning(
                'Falling back to %s: tenant timezone unreadable', FALLBACK_TIMEZONE, exc_info=True,
            )
            return FALLBACK_TIMEZONE
        return TimezoneService.pick(settings, user, browser_timezone)
