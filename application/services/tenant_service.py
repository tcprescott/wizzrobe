"""Tenant Service — tenancy machinery for resolution, CRUD, and membership.

Resolves a tenant from a URL slug (middleware), a custom domain, or a Discord
guild id (bot routing), and backs the ``/platform`` super-admin surface. Lookups
are cached in-process (one worker) and the cache is dropped on any write.

CRUD and grant operations are **super-admin gated** and run with *no* tenant
context (the platform surface), so they pass explicit ids to the repositories
rather than relying on the ambient tenant. Their audit rows are therefore
platform-level (``tenant=NULL``).
"""

import re
from typing import List, Optional

from tortoise.transactions import in_transaction

from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.repositories.tenant_repository import TenantRepository
from application.repositories.user_role_repository import UserRoleRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.event_service import EventService
from application.utils.environment import get_platform_host
from application.utils.hostname import normalize_hostname
from models import MembershipSource, Role, RoleSource, Tenant, User

_SLUG_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$')
# Top-level paths the platform surface owns; a tenant slug can never be one
# (slugs live under /t/<slug>, but reserving these avoids confusing links).
_RESERVED_SLUGS = {'platform', 'api', 'static', 'login', 'logout', 'oauth', 't', 'sw', 'health'}

# Simple per-process caches; invalidated wholesale on any write. Safe under the
# single-worker deployment. Keyed by the resolution inputs the request path uses.
# A guild may back several tenants, so the guild cache holds the full list.
_cache_by_slug: dict[str, Optional[Tenant]] = {}
_cache_by_guild: dict[int, List[Tenant]] = {}
# Host mode puts get_by_domain on the hot path of every custom-domain request.
# Positives and negatives are capped *separately*: a flood of distinct random
# Host values (scanners) is all-negatives, and sharing one FIFO pool would let
# that flood evict the handful of real domains. Negatives also help only against
# repeated unknown hosts, so their pool stays small.
_cache_by_domain: dict[str, Tenant] = {}
_neg_cache_by_domain: dict[str, None] = {}

# Bound the resolution caches. get_by_slug caches negative lookups too, keyed on
# the attacker-controlled URL slug, so without a cap a flood of distinct
# /t/<slug> requests would grow the cache without limit (memory-exhaustion DoS).
# Dicts preserve insertion order, so evict the oldest key (FIFO) when full.
_CACHE_MAX = 2048
_CACHE_NEG_MAX = 512


def _cache_put(cache: dict, key, value, cap: int = _CACHE_MAX) -> None:
    if key not in cache and len(cache) >= cap:
        cache.pop(next(iter(cache)), None)
    cache[key] = value


def _clear_cache() -> None:
    _cache_by_slug.clear()
    _cache_by_guild.clear()
    _cache_by_domain.clear()
    _neg_cache_by_domain.clear()


def slugify(name: str) -> str:
    """Best-effort URL-safe slug from a display name (for suggestions)."""
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return slug[:63] or 'tenant'


class TenantService:
    """Tenant resolution, CRUD, membership, and super-admin grants."""

    # ---- resolution (cached, cross-tenant) --------------------------------

    @staticmethod
    async def get_by_id(tenant_id: int) -> Optional[Tenant]:
        return await TenantRepository.get_by_id(tenant_id)

    @staticmethod
    async def community_name(tenant_id: int) -> str:
        """The community's display name for a tenant id, or '' if unknown.

        Used to stamp a community footer on Discord notification embeds so a user
        active in several communities can tell whose DM this is."""
        tenant = await TenantRepository.get_by_id(tenant_id)
        return tenant.name if tenant else ''

    @staticmethod
    async def current_community_name() -> str:
        """The in-scope tenant's community name for embed footers, or '' if none.

        Non-raising and best-effort: it reads the ambient tenant via
        ``get_current_tenant_id`` (which returns None rather than raising) and
        swallows any lookup failure to '' — the footer is cosmetic and must never
        turn a best-effort notification DM into a hard failure."""
        from application.tenant_context import get_current_tenant_id
        tid = get_current_tenant_id()
        if not tid:
            return ''
        try:
            return await TenantService.community_name(tid)
        except Exception:
            return ''

    @staticmethod
    async def get_by_slug(slug: str) -> Optional[Tenant]:
        key = (slug or '').lower()
        if key in _cache_by_slug:
            return _cache_by_slug[key]
        tenant = await TenantRepository.get_by_slug(key)
        _cache_put(_cache_by_slug, key, tenant)
        return tenant

    @staticmethod
    async def get_by_domain(domain: str) -> Optional[Tenant]:
        # Normalize the same way domains are stored (see _validate_domain), so a
        # browser's Host matches the verbatim-stored value. A non-normalizable
        # input can never match a stored domain, so short-circuit to None.
        key = normalize_hostname(domain)
        if key is None:
            return None
        if key in _cache_by_domain:
            return _cache_by_domain[key]
        if key in _neg_cache_by_domain:
            return None
        tenant = await TenantRepository.get_by_domain(key)
        if tenant is not None:
            _cache_put(_cache_by_domain, key, tenant)
        else:
            _cache_put(_neg_cache_by_domain, key, None, cap=_CACHE_NEG_MAX)
        return tenant

    @staticmethod
    async def list_tenants_for_guild(guild_id: int) -> List[Tenant]:
        """Every tenant linked to a Discord guild (a guild may back several).

        The bot fans out over the result — role sync, etc. — so a shared server
        drives all of its communities, not just one.
        """
        if guild_id in _cache_by_guild:
            return _cache_by_guild[guild_id]
        tenants = await TenantRepository.list_by_guild_id(guild_id)
        _cache_put(_cache_by_guild, guild_id, tenants)
        return tenants

    @staticmethod
    async def list_tenants() -> List[Tenant]:
        return await TenantRepository.list_all()

    @staticmethod
    async def set_discord_guild_id(tenant: Tenant, guild_id: Optional[int]) -> Tenant:
        """Set or clear a tenant's linked Discord guild, dropping the resolution cache.

        Authorization is the caller's responsibility — unlike ``update_tenant``
        there is no super-admin gate, because the verified link flow
        (``DiscordLinkService``) proves the actor controls the guild.
        """
        await TenantRepository.update(tenant, discord_guild_id=guild_id)
        _clear_cache()
        return tenant

    @staticmethod
    async def _validate_slug(slug: str, exclude_id: Optional[int] = None) -> str:
        slug = (slug or '').strip().lower()
        if not _SLUG_RE.match(slug):
            raise ValueError(
                'Slug must be 1-64 chars, lowercase letters/digits/hyphens, '
                'not starting or ending with a hyphen.'
            )
        if slug in _RESERVED_SLUGS:
            raise ValueError(f"'{slug}' is a reserved slug.")
        if await TenantRepository.slug_exists(slug, exclude_id=exclude_id):
            raise ValueError(f"A tenant with slug '{slug}' already exists.")
        return slug

    @staticmethod
    async def _validate_domain(domain: Optional[str], exclude_id: Optional[int] = None) -> Optional[str]:
        """Normalize + validate a custom domain, or None when blank.

        Normalization is the shared contract with request-time resolution
        (:func:`~application.utils.hostname.normalize_hostname`): stored and
        matched forms must agree, or a domain silently never routes. Rejecting a
        malformed value or the platform host here surfaces the problem at
        ``/platform`` immediately rather than as a mysterious wrong-surface
        render later.
        """
        if not (domain or '').strip():
            return None
        normalized = normalize_hostname(domain)
        if normalized is None:
            raise ValueError(
                'Enter a valid custom domain like "example.com" — no scheme, path, or spaces.'
            )
        platform_host = normalize_hostname(get_platform_host()) or get_platform_host()
        if normalized == platform_host:
            raise ValueError(
                'That is the platform host. A custom domain must differ from the main site host.'
            )
        if await TenantRepository.domain_exists(normalized, exclude_id=exclude_id):
            raise ValueError(f"A tenant with domain '{normalized}' already exists.")
        return normalized

    @staticmethod
    async def create_tenant(
        actor: User,
        *,
        name: str,
        slug: str,
        domain: Optional[str] = None,
        discord_guild_id: Optional[int] = None,
        is_active: bool = True,
        config: Optional[dict] = None,
    ) -> Tenant:
        await AuthService.ensure_super_admin(actor)
        name = (name or '').strip()
        if not name:
            raise ValueError('Tenant name is required.')
        slug = await TenantService._validate_slug(slug)
        domain = await TenantService._validate_domain(domain)

        async with in_transaction():
            tenant = await TenantRepository.create(
                name=name, slug=slug, domain=domain,
                discord_guild_id=discord_guild_id, is_active=is_active,
                config=config or {},
            )
            event = await EventService.ensure_first_event(tenant)
        _clear_cache()
        await AuditService().write_log(
            actor, AuditActions.TENANT_CREATED,
            {'tenant_id': tenant.id, 'slug': slug, 'name': name, 'event_id': event.id},
        )
        return tenant

    @staticmethod
    async def update_tenant(actor: User, tenant: Tenant, **fields) -> Tenant:
        await AuthService.ensure_super_admin(actor)
        if 'slug' in fields:
            fields['slug'] = await TenantService._validate_slug(fields['slug'], exclude_id=tenant.id)
        if 'domain' in fields:
            fields['domain'] = await TenantService._validate_domain(fields['domain'], exclude_id=tenant.id)
        if 'name' in fields:
            fields['name'] = (fields['name'] or '').strip()
            if not fields['name']:
                raise ValueError('Tenant name is required.')

        await TenantRepository.update(tenant, **fields)
        _clear_cache()
        await AuditService().write_log(
            actor, AuditActions.TENANT_UPDATED,
            {'tenant_id': tenant.id, 'changed': sorted(fields.keys())},
        )
        return tenant

    @staticmethod
    async def is_member(user_id: int, tenant_id: int) -> bool:
        return await TenantMembershipRepository.is_member(user_id, tenant_id)

    @staticmethod
    async def grant_super_admin(actor: User, user: User) -> None:
        await AuthService.ensure_super_admin(actor)
        # SUPER_ADMIN carries tenant=NULL; UserRoleRepository.add encodes that.
        await UserRoleRepository.add(user, Role.SUPER_ADMIN, granted_by=actor)
        await AuditService().write_log(
            actor, AuditActions.SUPER_ADMIN_GRANTED, {'user_id': user.id},
        )

    @staticmethod
    async def revoke_super_admin(actor: User, user: User) -> None:
        await AuthService.ensure_super_admin(actor)
        await UserRoleRepository.remove(user, Role.SUPER_ADMIN)
        await AuditService().write_log(
            actor, AuditActions.SUPER_ADMIN_REVOKED, {'user_id': user.id},
        )

    @staticmethod
    async def list_staff(actor: User, tenant_id: int) -> List[User]:
        """Who holds STAFF in a tenant, read from the platform surface.

        Wraps ``tenant_scope`` because the role tables are scoped and
        ``/platform`` has no ambient tenant — the same reason
        ``bootstrap_staff`` does.
        """
        from application.tenant_context import tenant_scope

        await AuthService.ensure_super_admin(actor)
        with tenant_scope(tenant_id):
            return await UserRoleRepository.list_users_with_role(Role.STAFF)

    @staticmethod
    async def bootstrap_staff(actor: User, tenant_id: int, user: User) -> None:
        """Grant the first STAFF role in a tenant + membership, from /platform.

        Runs at platform level (no tenant context), so it stamps the tenant
        explicitly rather than through the ambient scope.
        """
        from application.tenant_context import tenant_scope

        await AuthService.ensure_super_admin(actor)
        await TenantMembershipRepository.add(user, tenant_id, MembershipSource.STAFF)
        await TenantMembershipRepository.unblock(user.id, tenant_id)
        with tenant_scope(tenant_id):
            await UserRoleRepository.add(user, Role.STAFF, granted_by=actor, source=RoleSource.MANUAL)
        await AuditService().write_log(
            actor, AuditActions.USER_ROLE_GRANTED,
            {'tenant_id': tenant_id, 'user_id': user.id, 'role': Role.STAFF.value, 'bootstrap': True},
        )
