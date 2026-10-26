"""
OAuth App Repository - Data Access Layer

Third-party apps and their authorization codes, for the ``/oauth``
authorization server.

Apps are global (owned by a user, offered in every community with the flag
on), so this binds the generic base only for its ``update``/``delete`` and
overrides the lookups unscoped, the way ``RacetimeBot`` does. Codes carry the
community the person picked, but are looked up by hash at the token endpoint
before any tenant context exists, so those reads are cross-tenant by design;
the hash is the capability.
"""

from datetime import datetime
from typing import List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import ApiToken, OAuthApp, OAuthAppBlock, OAuthAppCode, OAuthClientType, User


class OAuthAppRepository(TenantScopedRepository[OAuthApp]):
    """Repository for third-party app and authorization-code data access."""

    model = OAuthApp

    # --- Apps --------------------------------------------------------------

    @staticmethod
    async def create_app(
        owner: User,
        name: str,
        description: str,
        homepage_url: Optional[str],
        client_type: OAuthClientType,
        client_id: str,
        redirect_uris: List[str],
        allowed_scopes: List[str],
        client_secret_hash: Optional[str] = None,
        secret_prefix: Optional[str] = None,
    ) -> OAuthApp:
        """Register an app. Global: an app belongs to its owner, not a community."""
        return await OAuthApp.create(
            owner=owner,
            name=name,
            description=description,
            homepage_url=homepage_url,
            client_type=client_type,
            client_id=client_id,
            redirect_uris=redirect_uris,
            allowed_scopes=allowed_scopes,
            client_secret_hash=client_secret_hash,
            secret_prefix=secret_prefix,
        )

    @classmethod
    async def get_by_id(cls, obj_id: int) -> Optional[OAuthApp]:
        """Global lookup (the table has no tenant); ownership is the service's check."""
        return await OAuthApp.get_or_none(id=obj_id).prefetch_related('owner')

    @staticmethod
    async def get_by_client_id(client_id: str) -> Optional[OAuthApp]:
        """Global lookup; the token endpoint has no community in scope."""
        return await OAuthApp.get_or_none(client_id=client_id).prefetch_related('owner')

    @staticmethod
    async def list_for_owner(owner: User) -> List[OAuthApp]:
        return await OAuthApp.filter(owner=owner).order_by('name', 'id')

    @staticmethod
    async def count_for_owner(owner: User) -> int:
        return await OAuthApp.filter(owner=owner).count()

    @staticmethod
    async def list_all() -> List[OAuthApp]:
        """Every app on the platform, for the super-admin view. Global."""
        return await OAuthApp.all().order_by('name', 'id').prefetch_related('owner')

    # --- Codes -------------------------------------------------------------

    @staticmethod
    async def create_code(
        code_hash: str,
        app: OAuthApp,
        user: User,
        tenant_id: int,
        redirect_uri: str,
        code_challenge: str,
        scope: str,
        expires_at: datetime,
    ) -> OAuthAppCode:
        """Persist a code, stamped with the community the person picked.

        The community is an argument rather than the ambient tenant: the
        consent screen is tenant-less and the person chooses it there.
        """
        return await OAuthAppCode.create(
            code_hash=code_hash,
            app=app,
            user=user,
            tenant_id=tenant_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope,
            expires_at=expires_at,
        )

    @staticmethod
    async def get_code(code_hash: str) -> Optional[OAuthAppCode]:
        """Fetch a code by hash.

        cross-tenant: the token endpoint runs before any tenant is in scope;
        the code itself names its community.
        """
        return await OAuthAppCode.get_or_none(code_hash=code_hash).prefetch_related('app', 'user')

    @staticmethod
    async def consume_code(code: OAuthAppCode, when: datetime) -> bool:
        """Mark a code used; False when it already was.

        One conditional UPDATE, so of two concurrent exchanges exactly one wins.
        cross-tenant: addressed by primary key from a code fetched by hash.
        """
        claimed = await OAuthAppCode.filter(id=code.id, consumed_at__isnull=True).update(consumed_at=when)
        if claimed:
            code.consumed_at = when
        return claimed == 1

    @staticmethod
    async def link_code_to_grant(code: OAuthAppCode, grant: ApiToken) -> None:
        """cross-tenant: addressed by primary key from a code fetched by hash."""
        await OAuthAppCode.filter(id=code.id).update(grant_id=grant.id)

    @staticmethod
    async def purge_expired_codes(now: datetime) -> int:
        """Delete codes past their expiry. cross-tenant housekeeping."""
        return await OAuthAppCode.filter(expires_at__lt=now).delete()

    # --- Community blocks --------------------------------------------------

    @staticmethod
    async def is_blocked(app_id: int, tenant_id: int) -> bool:
        """Whether ``tenant_id`` blocks this app.

        The community is an argument: the consent screen and the token endpoint
        ask about a community they name, not the ambient one.
        """
        return await scoped(OAuthAppBlock.filter(app_id=app_id), tenant_id).exists()

    @staticmethod
    async def blocked_app_ids() -> List[int]:
        return await scoped(OAuthAppBlock.all()).values_list('app_id', flat=True)  # type: ignore[return-value]

    @staticmethod
    async def block(app: OAuthApp, actor: User) -> bool:
        """Block ``app`` in this community; False when it already was."""
        _, created = await OAuthAppBlock.get_or_create(
            tenant_id=current_tenant_id(), app=app, defaults={'blocked_by': actor},
        )
        return created

    @staticmethod
    async def unblock(app_id: int) -> bool:
        return await scoped(OAuthAppBlock.filter(app_id=app_id)).delete() > 0

    @staticmethod
    async def list_by_ids(app_ids: List[int]) -> List[OAuthApp]:
        """Apps by id, for a community's connected-apps list. Apps are global."""
        if not app_ids:
            return []
        return await OAuthApp.filter(id__in=app_ids).order_by('name', 'id').prefetch_related('owner')
