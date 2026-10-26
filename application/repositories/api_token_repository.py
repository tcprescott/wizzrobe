"""
API Token Repository - Data Access Layer

Handles database operations for bearer credentials: tenant-bound personal access
tokens (REST), platform-wide OAuth tokens (MCP), and tenant-bound third-party
app grants (REST).
"""

from datetime import datetime
from typing import List, Optional

from tortoise.expressions import Q

from application.repositories._tenant import current_tenant_id, scoped
from models import ApiToken, ApiTokenOrigin, McpOAuthClient, OAuthApp, User


class ApiTokenRepository:
    """Repository for API token data access.

    Two kinds of row live here (see :class:`~models.ApiToken`): PATs carry a
    ``tenant`` and serve the REST API; OAuth tokens carry ``tenant=NULL`` and
    serve the MCP server platform-wide. Reads are therefore scoped to
    "the current tenant OR no tenant at all" rather than to the current tenant
    alone — a user's own OAuth tokens must be listable and revocable from any
    community's profile page, since they belong to no single one.

    ``get_by_hash`` is global for a different reason: the token is resolved
    *before* any tenant context exists, on every surface.
    """

    @staticmethod
    async def create(
        user: User,
        name: str,
        token_hash: str,
        token_prefix: str,
        read_only: bool = False,
        expires_at: Optional[datetime] = None,
    ) -> ApiToken:
        return await ApiToken.create(
            tenant_id=current_tenant_id(),
            user=user,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            read_only=read_only,
            expires_at=expires_at,
            origin=ApiTokenOrigin.PAT.value,
        )

    @staticmethod
    async def create_oauth_token(
        user: User,
        client: McpOAuthClient,
        name: str,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
        refresh_token_hash: str,
        refresh_expires_at: datetime,
        scope: Optional[str] = None,
        read_only: bool = True,
    ) -> ApiToken:
        """Create a platform-wide OAuth access token.

        Deliberately global — ``tenant`` stays NULL. An OAuth grant authenticates
        a user across the platform; the community each MCP tool call operates in
        is named by that call, so stamping a tenant here would be wrong, not
        merely redundant.

        ``read_only`` defaults to True, matching the consent screen's own
        default: a connection changes nothing unless the person approving it
        asked for that.
        """
        return await ApiToken.create(
            tenant_id=None,
            user=user,
            oauth_client=client,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            read_only=read_only,
            origin=ApiTokenOrigin.OAUTH.value,
            expires_at=expires_at,
            refresh_token_hash=refresh_token_hash,
            refresh_expires_at=refresh_expires_at,
            scope=scope,
        )

    @staticmethod
    async def get_by_refresh_hash(refresh_hash: str) -> Optional[ApiToken]:
        """Return the OAuth token holding this refresh-token hash.

        GLOBAL by design, exactly like ``get_by_hash``: a refresh exchange
        arrives with no tenant context and OAuth tokens belong to no tenant.

        tenant-scope: exempt — OAuth tokens carry tenant=NULL and the exchange
        has no tenant in scope to filter on.
        """
        return await ApiToken.get_or_none(
            refresh_token_hash=refresh_hash, origin=ApiTokenOrigin.OAUTH.value
        ).prefetch_related('user', 'oauth_client')

    @staticmethod
    async def create_app_grant(
        user: User,
        app: OAuthApp,
        tenant_id: int,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
        refresh_token_hash: str,
        refresh_expires_at: datetime,
        scope: str,
        read_only: bool,
    ) -> ApiToken:
        """Create a third-party app grant bound to the community the person picked.

        The community is an argument, not the ambient tenant: the token endpoint
        runs with no tenant in scope and the authorization code names it.
        """
        return await ApiToken.create(
            tenant_id=tenant_id,
            user=user,
            oauth_app=app,
            name=app.name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            read_only=read_only,
            origin=ApiTokenOrigin.APP.value,
            expires_at=expires_at,
            refresh_token_hash=refresh_token_hash,
            refresh_expires_at=refresh_expires_at,
            scope=scope,
        )

    @staticmethod
    async def get_app_grant_by_hash(token_hash: str) -> Optional[ApiToken]:
        """An app grant by its access-token hash.

        cross-tenant: like ``get_by_hash``, this runs before any tenant exists.
        """
        return await ApiToken.get_or_none(
            token_hash=token_hash, origin=ApiTokenOrigin.APP.value
        ).prefetch_related('user', 'oauth_app')

    @staticmethod
    async def get_app_grant_by_id(token_id: int) -> Optional[ApiToken]:
        """An app grant by primary key, for revoking what a replayed code produced.

        cross-tenant: reached from a code fetched by hash at the token endpoint.
        """
        return await ApiToken.get_or_none(
            id=token_id, origin=ApiTokenOrigin.APP.value
        ).prefetch_related('user', 'oauth_app')

    @staticmethod
    async def get_app_grant_by_refresh_hash(refresh_hash: str) -> Optional[ApiToken]:
        """An app grant by its current refresh-token hash.

        cross-tenant: the refresh exchange has no tenant in scope; the grant
        names its own.
        """
        return await ApiToken.get_or_none(
            refresh_token_hash=refresh_hash, origin=ApiTokenOrigin.APP.value
        ).prefetch_related('user', 'oauth_app__owner')

    @staticmethod
    async def get_app_grant_by_previous_refresh_hash(refresh_hash: str) -> Optional[ApiToken]:
        """The app grant that rotated away from this refresh hash, if any.

        cross-tenant: reuse detection runs at the token endpoint, before any
        tenant is in scope.
        """
        return await ApiToken.filter(
            previous_refresh_hash=refresh_hash, origin=ApiTokenOrigin.APP.value
        ).prefetch_related('user', 'oauth_app').first()

    @staticmethod
    async def live_app_grants(user_id: int, app_id: int, tenant_id: int) -> List[ApiToken]:
        """Live grants one person holds for one app in one community."""
        return await ApiToken.filter(
            user_id=user_id, oauth_app_id=app_id, tenant_id=tenant_id,
            origin=ApiTokenOrigin.APP.value, revoked_at=None,
        )

    @staticmethod
    async def revoke_app_grants_for_app(app_id: int, when: datetime) -> int:
        """Revoke every live grant of one app, in every community.

        cross-tenant: an app is global, and deleting or disabling it ends all
        of its grants at once.
        """
        return await ApiToken.filter(
            oauth_app_id=app_id, origin=ApiTokenOrigin.APP.value, revoked_at=None,
        ).update(revoked_at=when)

    @staticmethod
    async def app_grant_users_here() -> dict[int, int]:
        """People holding a live grant in this community, per app id."""
        rows = await scoped(ApiToken.filter(
            origin=ApiTokenOrigin.APP.value, revoked_at=None, oauth_app_id__isnull=False,
        )).values_list('oauth_app_id', 'user_id')
        users: dict[int, set] = {}
        for app_id, user_id in rows:
            users.setdefault(app_id, set()).add(user_id)
        return {app_id: len(ids) for app_id, ids in users.items()}

    @staticmethod
    async def revoke_app_grants_here(app_id: int, when: datetime) -> int:
        """Revoke every live grant of one app in this community."""
        return await scoped(ApiToken.filter(
            oauth_app_id=app_id, origin=ApiTokenOrigin.APP.value, revoked_at=None,
        )).update(revoked_at=when)

    @staticmethod
    async def app_grant_reach() -> dict[int, tuple[int, int]]:
        """Per app id: (people holding a live grant, communities they're in).

        cross-tenant: the /platform view of every app, for a super admin.
        """
        rows = await ApiToken.filter(
            origin=ApiTokenOrigin.APP.value, revoked_at=None, oauth_app_id__isnull=False,
        ).values_list('oauth_app_id', 'user_id', 'tenant_id')
        users: dict[int, set] = {}
        tenants: dict[int, set] = {}
        for app_id, user_id, tenant_id in rows:
            users.setdefault(app_id, set()).add(user_id)
            tenants.setdefault(app_id, set()).add(tenant_id)
        return {app_id: (len(users[app_id]), len(tenants[app_id])) for app_id in users}

    @staticmethod
    async def count_app_grant_users(app_id: int) -> int:
        """How many people hold a live grant for this app, across communities.

        cross-tenant: an app's owner sees one platform-wide count, never who.
        """
        user_ids = await ApiToken.filter(
            oauth_app_id=app_id, origin=ApiTokenOrigin.APP.value, revoked_at=None,
        ).distinct().values_list('user_id', flat=True)
        return len(set(user_ids))

    @staticmethod
    async def rotate_refresh(
        token: ApiToken,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
        refresh_token_hash: str,
        refresh_expires_at: datetime,
        remember_previous: bool = False,
        scope: Optional[str] = None,
        read_only: Optional[bool] = None,
    ) -> bool:
        """Re-key an OAuth token in place on a refresh exchange; False when the
        refresh token was already spent.

        Rotating both halves means a leaked refresh token is single-use: the old
        hashes stop matching the moment the exchange succeeds. Conditional on
        the old refresh hash so two concurrent refreshes can't both succeed.
        Cross-tenant: OAuth rows carry no tenant, and an app grant is
        addressed by the primary key of a row already fetched by hash.

        ``remember_previous`` keeps the spent hash on the row (app grants), so a
        later presentation of it is recognised as reuse rather than as a bad
        token. ``scope``/``read_only`` narrow the grant in the same write.
        """
        changes: dict = {
            'token_hash': token_hash,
            'token_prefix': token_prefix,
            'expires_at': expires_at,
            'refresh_token_hash': refresh_token_hash,
            'refresh_expires_at': refresh_expires_at,
        }
        if remember_previous:
            changes['previous_refresh_hash'] = token.refresh_token_hash
        if scope is not None:
            changes['scope'] = scope
        if read_only is not None:
            changes['read_only'] = read_only
        rotated = await ApiToken.filter(
            id=token.id, refresh_token_hash=token.refresh_token_hash,
            revoked_at__isnull=True,
        ).update(**changes)
        return rotated == 1

    @staticmethod
    async def get_by_id(token_id: int) -> Optional[ApiToken]:
        """Fetch a manageable token by id.

        Scoped to this tenant's PATs plus the global (``tenant=NULL``) OAuth
        tokens, so a user can revoke their MCP connections from any community
        they belong to. Ownership is still enforced by the service — this only
        bounds *which rows are addressable*, never *whose*.

        tenant-scope: exempt — the scoping is the ``Q`` OR above, which the
        check reads as an unscoped filter.
        """
        return await ApiToken.filter(
            Q(tenant_id=current_tenant_id()) | Q(tenant_id=None), id=token_id
        ).first()

    @staticmethod
    async def get_by_hash(token_hash: str) -> Optional[ApiToken]:
        """Return the token matching this hash, with its relations prefetched.

        GLOBAL by design (token_hash is globally unique) — the caller sets tenant
        context from the resolved token afterwards.

        tenant-scope: exempt — this read is what *establishes* the tenant, so
        there is none to filter on yet.

        ``oauth_client`` and ``oauth_app`` are prefetched too: an unfetched FK is
        a lazy QuerySet, not None, so any caller reading
        ``token.oauth_client.client_id`` on an OAuth token (or the app's state on
        an app grant) would get an AttributeError rather than a value.
        """
        return await ApiToken.get_or_none(
            token_hash=token_hash
        ).prefetch_related('user', 'oauth_client', 'oauth_app__owner')

    @staticmethod
    async def list_for_user(user: User) -> List[ApiToken]:
        """Active (non-revoked) tokens for a user, newest first.

        This tenant's PATs plus the user's global OAuth tokens, so the profile
        page shows every credential they can revoke from wherever they are.

        tenant-scope: exempt — the scoping is the ``Q`` OR above, which the
        check reads as an unscoped filter.
        """
        return await ApiToken.filter(
            Q(tenant_id=current_tenant_id()) | Q(tenant_id=None),
            user=user,
            revoked_at=None,
        ).order_by('-created_at').prefetch_related('oauth_client', 'oauth_app')

    @staticmethod
    async def touch_last_used(token: ApiToken, when: datetime) -> None:
        token.last_used_at = when
        await token.save(update_fields=['last_used_at'])

    @staticmethod
    async def revoke(token: ApiToken, when: datetime) -> None:
        token.revoked_at = when
        await token.save(update_fields=['revoked_at'])

    @staticmethod
    async def revoke_for_user_in_tenant(user_id: int, tenant_id: int, when: datetime) -> int:
        """Revoke every live token ``user_id`` holds for ``tenant_id``; returns the count."""
        return await ApiToken.filter(
            user_id=user_id, tenant_id=tenant_id, revoked_at=None,
        ).update(revoked_at=when)
