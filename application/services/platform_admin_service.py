"""Platform Admin Service — the reads ``/platform`` builds from several services.

The REST ``/platform`` routers and the MCP platform tools both need "a community
with its setup readiness and tier" and "an account with its communities", and
neither may reach a repository. This is where those composite reads live, so the
two surfaces return the same answer. Writes stay with the services that own
them (``TenantService``, ``FeatureFlagService``, ``PasswordAuthService`` …);
the helpers here only resolve the ids they take.

Every method is super-admin-only. ``TenantService.get_by_id`` and
``TenantSetupService.status_for`` are ungated by design (the web chrome calls
them), so the gate here is the one that stands between them and a token.
"""

from dataclasses import dataclass
from typing import List, Optional

from application.errors import require_found
from application.repositories.feature_flag_group_repository import FeatureFlagGroupRepository
from application.repositories.tenant_repository import TenantRepository
from application.repositories.user_repository import UserRepository
from application.services.auth_service import AuthService
from application.services.password_auth_service import PasswordAuthService
from application.services.tenant_service import TenantService
from application.services.tenant_setup_service import SetupStep, TenantSetupService
from models import Role, Tenant, User


@dataclass(frozen=True)
class CommunityOverview:
    """One community as the platform sees it."""

    tenant: Tenant
    feature_group: Optional[str]
    setup: List[SetupStep]

    @property
    def setup_ready(self) -> bool:
        return TenantSetupService.is_ready(self.setup)

    @property
    def setup_missing(self) -> List[str]:
        return [step.label for step in TenantSetupService.outstanding(self.setup)]


@dataclass(frozen=True)
class AccountOverview:
    """One account as the platform's Users tab shows it."""

    user: User
    communities: List[str]
    is_super_admin: bool


class PlatformAdminService:
    """Super-admin reads across every community and account."""

    async def list_communities(self, actor: Optional[User]) -> List[CommunityOverview]:
        """Every community, active or not, with its tier and setup readiness."""
        await AuthService.ensure_super_admin(actor)
        groups = {g.id: g.name for g in await FeatureFlagGroupRepository().list_all()}
        return [
            await self._overview(tenant, groups)
            for tenant in await TenantRepository.list_all()
        ]

    async def get_community(self, actor: Optional[User], tenant_id: int) -> CommunityOverview:
        await AuthService.ensure_super_admin(actor)
        tenant = await self.require_community(actor, tenant_id)
        groups = {g.id: g.name for g in await FeatureFlagGroupRepository().list_all()}
        return await self._overview(tenant, groups)

    async def require_community(self, actor: Optional[User], tenant_id: int) -> Tenant:
        """Load any community by id, inactive ones included, or raise ``NotFoundError``."""
        await AuthService.ensure_super_admin(actor)
        return require_found(await TenantRepository.get_by_id(tenant_id), 'Community')

    async def find_community(self, actor: Optional[User], slug: str) -> Tenant:
        """Load any community by slug, inactive ones included.

        Unlike the per-call ``tenant`` argument, which hides inactive
        communities, a super admin needs to reach a switched-off one to switch
        it back on.
        """
        await AuthService.ensure_super_admin(actor)
        key = (slug or '').strip().lower()
        return require_found(await TenantRepository.get_by_slug(key), f"Community '{key}'")

    @staticmethod
    async def _overview(tenant: Tenant, groups: dict) -> CommunityOverview:
        return CommunityOverview(
            tenant=tenant,
            feature_group=groups.get(tenant.feature_group_id),  # type: ignore[attr-defined]
            setup=await TenantSetupService.status_for(tenant.id),
        )

    async def grant_community_staff(
        self, actor: Optional[User], tenant_id: int, user_id: int,
    ) -> List[User]:
        """Make ``user_id`` STAFF (and a member) of a community; return its staff."""
        await AuthService.ensure_super_admin(actor)
        assert actor is not None
        tenant = await self.require_community(actor, tenant_id)
        user = await self.require_account(actor, user_id)
        await TenantService.bootstrap_staff(actor, tenant.id, user)
        return await TenantService.list_staff(actor, tenant.id)

    async def list_accounts(
        self,
        actor: Optional[User],
        *,
        search: Optional[str] = None,
        has_password: Optional[bool] = None,
        is_active: Optional[bool] = None,
    ) -> List[AccountOverview]:
        """Every real account, filtered the way the Users tab filters.

        ``search`` matches name, Discord handle, Discord id and sign-in name,
        case-insensitively — the same fields the tab's search box matches.
        """
        await AuthService.ensure_super_admin(actor)
        assert actor is not None
        needle = (search or '').strip().lower()
        out: List[AccountOverview] = []
        for user in await PasswordAuthService().list_accounts(actor):
            if has_password is not None and bool(user.login_name) != has_password:
                continue
            if is_active is not None and user.is_active != is_active:
                continue
            if needle and needle not in self._haystack(user):
                continue
            out.append(self._account(user))
        return out

    async def get_account(self, actor: Optional[User], user_id: int) -> AccountOverview:
        await AuthService.ensure_super_admin(actor)
        user = await self.require_account(actor, user_id)
        await user.fetch_related('tenant_memberships__tenant', 'roles')
        return self._account(user)

    async def require_account(self, actor: Optional[User], user_id: int) -> User:
        """Load any real account by id, or raise ``NotFoundError``."""
        await AuthService.ensure_super_admin(actor)
        user = await UserRepository.get_by_id(user_id)
        if user is None or user.is_placeholder or user.is_system:
            user = None
        return require_found(user, 'User')

    async def set_account_active(
        self, actor: Optional[User], user_id: int, active: bool,
    ) -> AccountOverview:
        """Deactivate or reactivate an account platform-wide."""
        await AuthService.ensure_super_admin(actor)
        assert actor is not None
        user = await self.require_account(actor, user_id)
        await PasswordAuthService().set_active(actor, user, active)
        return await self.get_account(actor, user_id)

    @staticmethod
    def _haystack(user: User) -> str:
        return ' '.join(filter(None, [
            user.preferred_name, user.username, str(user.discord_id or ''), user.login_name or '',
        ])).lower()

    @staticmethod
    def _account(user: User) -> AccountOverview:
        return AccountOverview(
            user=user,
            communities=sorted({m.tenant.name for m in user.tenant_memberships}),  # type: ignore[attr-defined]
            is_super_admin=any(
                r.role == Role.SUPER_ADMIN and r.tenant_id is None for r in user.roles  # type: ignore[attr-defined]
            ),
        )
