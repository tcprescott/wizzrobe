"""
Patreon Link Service - Business Logic Layer

A member linking their own Patreon account, and the callback both Patreon
sign-ins come back to.

A link is global (one Patreon account per Wizzrobe account) and evaluated per
community. The member's token is used once, to read who they are and their
memberships, and then dropped: afterwards their standing comes from each
community's campaign (webhook and sync). Unlinking forgets the Patreon id and
every badge, in every community.

Gated by :attr:`~models.FeatureFlag.PATREON`.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from application.feature_flags import requires_feature
from application.repositories import PatreonRepository, TenantMembershipRepository
from application.services import _patreon_oauth as oauth
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.patreon_campaign_service import PatreonCampaignService
from application.services.patron_service import PatronService, PatronStatus
from application.tenant_context import tenant_scope
from application.utils.clients.patreon_client import MEMBER_SCOPE, PatreonError
from models import FeatureFlag, PatreonOAuthPurpose, User

logger = logging.getLogger(__name__)



class PatreonLinkTaken(ValueError):
    """The Patreon account is already linked to someone else."""


@dataclass(frozen=True)
class CommunityBadge:
    community: str
    is_patron: bool
    badge_label: Optional[str]


@dataclass(frozen=True)
class MyPatreon:
    configured: bool
    linked: bool
    username: Optional[str]
    linked_at: Optional[datetime]
    badge_hidden: bool
    connected_here: bool
    here: PatronStatus
    communities: Tuple[CommunityBadge, ...] = ()


class PatreonLinkService:
    """Link, unlink and show the caller's own Patreon account."""

    def __init__(self) -> None:
        self.repository = PatreonRepository()
        self.audit_service = AuditService()
        self.patrons = PatronService()

    @requires_feature(FeatureFlag.PATREON)
    async def my_status(self, actor: User) -> MyPatreon:
        here = await self.patrons.get_patron_status(actor)
        communities: List[CommunityBadge] = []
        if actor.patreon_user_id:
            for row in await self.repository.memberships_everywhere(actor.id):
                status = await self.patrons.get_patron_status(actor, tenant_id=row.tenant_id)
                communities.append(CommunityBadge(row.tenant.name, status.is_patron, status.badge_label))
        return MyPatreon(
            configured=oauth.is_configured(), linked=bool(actor.patreon_user_id),
            username=actor.patreon_username, linked_at=actor.patreon_linked_at,
            badge_hidden=actor.patreon_badge_hidden,
            connected_here=await self.repository.get_campaign() is not None,
            here=here, communities=tuple(sorted(communities, key=lambda c: c.community.lower())),
        )

    @requires_feature(FeatureFlag.PATREON)
    async def start_link(self, actor: User) -> str:
        """Patreon's sign-in page for the caller, as a URL the app opens."""
        await AuthService.ensure(actor is not None and not actor.is_system, 'Sign in first.')
        return await oauth.start_sign_in(actor, PatreonOAuthPurpose.MEMBER, MEMBER_SCOPE)

    @requires_feature(FeatureFlag.PATREON)
    async def set_badge_hidden(self, actor: User, hidden: bool) -> MyPatreon:
        await self.repository.set_badge_hidden(actor, hidden)
        return await self.my_status(actor)

    @requires_feature(FeatureFlag.PATREON)
    async def unlink(self, actor: User) -> int:
        """Forget the caller's Patreon account and their badges everywhere. Returns badges removed."""
        if not actor.patreon_user_id:
            return 0
        removed = 0
        for row in await self.repository.memberships_everywhere(actor.id):
            if row.patron_status != 'active_patron':
                continue
            removed += 1
            with tenant_scope(row.tenant_id):
                await self.audit_service.write_log(actor, AuditActions.PATREON_BADGE_REVOKED, {
                    'user_id': actor.id, 'source': 'unlink', 'patron_status': None,
                })
        await self.repository.delete_memberships_everywhere(actor.id)
        await self.repository.set_user_link(actor, None, None, None)
        await self.audit_service.write_log(actor, AuditActions.PATREON_UNLINKED, {
            'user_id': actor.id, 'badges_removed': removed,
        })
        return removed

    # feature-gate: exempt — the OAuth callback checks the flag in the community before calling this.
    async def finish_link(self, actor: User, code: str) -> None:
        try:
            tokens = await oauth.oauth_client().exchange_code(code, oauth.redirect_uri())
            identity = await oauth.api_client(tokens.access_token).identity(with_memberships=True)
        except PatreonError as e:
            raise ValueError(f"Patreon didn't accept the sign-in: {e}") from e
        holder = await self.repository.user_by_patreon_id(identity.patreon_user_id)
        if holder is not None and holder.id != actor.id:
            raise PatreonLinkTaken('That Patreon account is already linked to another Wizzrobe account.')
        name = (identity.name or '').strip()[:255] or None
        await self.repository.set_user_link(actor, identity.patreon_user_id, name, datetime.now(timezone.utc))
        await self.audit_service.write_log(actor, AuditActions.PATREON_LINKED, {
            'user_id': actor.id, 'patreon_user_id': identity.patreon_user_id,
        })
        by_campaign = {m.campaign_id: m for m in identity.memberships if m.campaign_id}
        memberships = await TenantMembershipRepository.list_for_user(actor)
        for tenant_id in await self.repository.connected_tenant_ids_among(m.tenant_id for m in memberships):
            with tenant_scope(tenant_id):
                if not await FeatureFlagService().is_enabled(FeatureFlag.PATREON):
                    continue
                campaign = await self.repository.get_campaign()
                if campaign is None:
                    continue
                found = by_campaign.get(campaign.campaign_id)
                if found is not None:
                    await self.patrons.record(actor.id, found, actor, source='link')
                else:
                    campaign.sync_requested_at = datetime.now(timezone.utc)
                    await self.repository.save_campaign(campaign, 'sync_requested_at')


@dataclass(frozen=True)
class SignInResult:
    """How a Patreon sign-in ended: ``connected``, ``linked``, ``taken``, ``cancelled``, ``expired`` or ``failed``."""

    outcome: str
    creator: bool = False


class PatreonOAuthService:
    """Where both Patreon sign-ins land: ``GET /api/patreon/callback``."""

    def __init__(self) -> None:
        self.repository = PatreonRepository()

    # feature-gate: exempt — binds the sign-in's community and checks the flag there itself.
    async def finish(self, state: Optional[str], code: Optional[str], error: Optional[str]) -> SignInResult:
        """Complete the sign-in ``state`` names and say how it went."""
        pending = await self.repository.pop_state(oauth.hash_state(state)) if state else None
        if pending is None:
            return SignInResult('expired')
        creator = pending.purpose is PatreonOAuthPurpose.CREATOR
        with tenant_scope(pending.tenant_id):
            outcome = await self._complete(pending.user_id, creator, pending.expires_at, code, error)
        return SignInResult(outcome, creator)

    async def _complete(
        self, user_id: int, creator: bool, expires_at: datetime, code: Optional[str], error: Optional[str],
    ) -> str:
        from application.services.user_service import UserService

        if error or not code:
            return 'cancelled'
        if expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
            return 'expired'
        if not await FeatureFlagService().is_enabled(FeatureFlag.PATREON):
            return 'failed'
        user = await UserService().get_user_by_id(user_id)
        if user is None or not user.is_active:
            return 'failed'
        try:
            if creator:
                await PatreonCampaignService().finish_connect(user, code)
                return 'connected'
            await PatreonLinkService().finish_link(user, code)
            return 'linked'
        except PatreonLinkTaken:
            return 'taken'
        except ValueError as e:
            logger.info('Patreon sign-in did not finish: %s', e)
            return 'failed'
