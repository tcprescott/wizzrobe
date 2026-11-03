"""
Patreon Campaign Service - Business Logic Layer

A community's Patreon campaign. A staff member signs in as the creator once;
Wizzrobe then keeps the sealed tokens fresh, registers a webhook on the
campaign, and keeps each linked member's standing current from that webhook
and a periodic full pass over the campaign's members.

Nothing here hands a token or the webhook secret back out, and nothing asks
Patreon for a pledge amount.

Gated by :attr:`~models.FeatureFlag.PATREON`.
"""

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Mapping, Optional, Sequence, Tuple

from application.errors import NotFoundError
from application.feature_flags import requires_feature
from application.repositories import PatreonRepository, TenantMembershipRepository
from application.services import _patreon_oauth as oauth
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.patron_service import PatronService
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.clients.patreon_client import (
    CREATOR_SCOPE,
    MemberStatus,
    PatreonAuthError,
    PatreonError,
    Tokens,
    parse_webhook,
    signature_ok,
)
from application.utils.environment import get_base_url
from application.utils.shared_lock import shared_lock
from models import FeatureFlag, PatreonCampaign, PatreonOAuthPurpose, User

logger = logging.getLogger(__name__)

SYNC_EVERY = timedelta(hours=6)
_REFRESH_BUFFER = timedelta(days=3)
PAGE_PAUSE_SECONDS = 0.5
MAX_LABEL = 40
_STAFF_ONLY = 'Only staff can manage the Patreon connection.'
_RECONNECT = 'The Patreon sign-in was revoked or has expired. Reconnect it on Settings → Integrations.'


class PatreonSignatureError(ValueError):
    """A webhook whose signature doesn't match: rejected, nothing changed."""


@dataclass(frozen=True)
class TierLabel:
    tier_id: str
    title: str
    badge_label: Optional[str]


@dataclass(frozen=True)
class CampaignOverview:
    configured: bool
    connected: bool
    campaign_name: Optional[str] = None
    connected_by: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None
    sync_pending: bool = False
    linked_members: int = 0
    patrons: int = 0
    tiers: Tuple[TierLabel, ...] = ()


def webhook_url(campaign: PatreonCampaign) -> str:
    return f'{get_base_url()}/api/patreon-webhooks/{campaign.webhook_token}'


def _expires_at(tokens: Tokens) -> Optional[datetime]:
    if not tokens.expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=tokens.expires_in)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clean_label(label: Optional[str]) -> Optional[str]:
    text = ' '.join((label or '').split())
    if not text:
        return None
    if len(text) > MAX_LABEL:
        raise ValueError(f'Keep a badge label to {MAX_LABEL} characters.')
    return text


class PatreonCampaignService:
    """Connect, label, sync and disconnect the community's Patreon campaign."""

    def __init__(self) -> None:
        self.repository = PatreonRepository()
        self.audit_service = AuditService()
        self.patrons = PatronService()

    @staticmethod
    async def _ensure_staff(actor: Optional[User]) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), _STAFF_ONLY)

    @requires_feature(FeatureFlag.PATREON)
    async def overview(self, actor: User) -> CampaignOverview:
        await self._ensure_staff(actor)
        campaign = await self.repository.get_campaign()
        if campaign is None:
            return CampaignOverview(configured=oauth.is_configured(), connected=False)
        await campaign.fetch_related('connected_by')
        linked, patrons = await self.repository.membership_counts()
        return CampaignOverview(
            configured=oauth.is_configured(), connected=True, campaign_name=campaign.campaign_name,
            connected_by=campaign.connected_by.preferred_name if campaign.connected_by else None,
            connected_at=campaign.connected_at, last_synced_at=campaign.last_synced_at,
            last_sync_error=campaign.last_sync_error, sync_pending=campaign.sync_requested_at is not None,
            linked_members=linked, patrons=patrons,
            tiers=tuple(TierLabel(t.tier_id, t.tier_title, t.badge_label) for t in await self.repository.list_tiers()),
        )

    @requires_feature(FeatureFlag.PATREON)
    async def start_connect(self, actor: User) -> str:
        """Patreon's sign-in page for the creator, as a URL the app opens."""
        await self._ensure_staff(actor)
        return await oauth.start_sign_in(actor, PatreonOAuthPurpose.CREATOR, CREATOR_SCOPE)

    # feature-gate: exempt — the OAuth callback checks the flag in the community before calling this.
    async def finish_connect(self, actor: User, code: str) -> None:
        """Swap the creator's code for tokens, register the webhook, and ask for a first sync."""
        await self._ensure_staff(actor)
        try:
            tokens = await oauth.oauth_client().exchange_code(code, oauth.redirect_uri())
            client = oauth.api_client(tokens.access_token)
            campaigns = await client.campaigns()
            if not campaigns:
                raise ValueError("That Patreon account doesn't have a campaign to connect.")
            chosen = campaigns[0]
            tiers = await client.tiers(chosen.id)
            existing = await self.repository.get_campaign()
            token = existing.webhook_token if existing else secrets.token_urlsafe(24)
            probe = PatreonCampaign(tenant_id=require_tenant_id(), webhook_token=token)
            webhook_id, webhook_secret = await client.create_webhook(chosen.id, webhook_url(probe))
        except PatreonError as e:
            raise ValueError(f"Patreon didn't accept the sign-in: {e}") from e
        tenant_id = require_tenant_id()
        if existing is not None:
            await self._drop_webhook(existing)
        fields = dict(
            campaign_id=chosen.id, campaign_name=(chosen.name or '')[:255] or None,
            access_token=oauth.seal_for(tenant_id, tokens.access_token),
            refresh_token=oauth.seal_for(tenant_id, tokens.refresh_token) if tokens.refresh_token else None,
            token_expires_at=_expires_at(tokens), webhook_id=webhook_id,
            webhook_secret=oauth.seal_for(tenant_id, webhook_secret, oauth.WEBHOOK_PURPOSE),
            connected_by=actor, last_sync_error=None, sync_requested_at=datetime.now(timezone.utc),
        )
        if existing is None:
            await self.repository.create(webhook_token=token, **fields)
        else:
            if existing.campaign_id != chosen.id:
                await self.repository.delete_memberships((await self.repository.memberships_by_user()).keys())
            await self.repository.update(existing, **fields)
        await self.repository.sync_tiers([(t.id, t.title) for t in tiers])
        await self.audit_service.write_log(actor, AuditActions.PATREON_CONNECTED, {
            'campaign_id': chosen.id, 'campaign_name': fields['campaign_name'], 'replaced': existing is not None,
        })

    @requires_feature(FeatureFlag.PATREON)
    async def disconnect(self, actor: User) -> int:
        """Remove our webhook, the tokens, the tier labels and every badge here. Returns badges removed."""
        await self._ensure_staff(actor)
        campaign = await self.repository.get_campaign()
        if campaign is None:
            raise ValueError("Patreon isn't connected.")
        await self._drop_webhook(campaign)
        removed, active = await self.repository.delete_community_data()
        await self.audit_service.write_log(actor, AuditActions.PATREON_DISCONNECTED, {
            'campaign_id': campaign.campaign_id, 'memberships_removed': removed, 'badges_removed': active,
        })
        return active

    @requires_feature(FeatureFlag.PATREON)
    async def set_tier_labels(self, actor: User, labels: Sequence[Tuple[str, Optional[str]]]) -> CampaignOverview:
        """Set each tier's badge label (empty = the default) and their order, first wins."""
        await self._ensure_staff(actor)
        if await self.repository.get_campaign() is None:
            raise ValueError("Patreon isn't connected.")
        known = {t.tier_id for t in await self.repository.list_tiers()}
        cleaned = [(tier_id, _clean_label(label)) for tier_id, label in labels if tier_id in known]
        await self.repository.set_tier_labels(cleaned)
        await self.audit_service.write_log(actor, AuditActions.PATREON_TIERS_UPDATED, {
            'labels': {tier_id: label for tier_id, label in cleaned},
        })
        return await self.overview(actor)

    @requires_feature(FeatureFlag.PATREON)
    async def request_sync(self, actor: User) -> None:
        """Ask the sync worker for a full pass on its next tick."""
        await self._ensure_staff(actor)
        campaign = await self.repository.get_campaign()
        if campaign is None:
            raise ValueError("Patreon isn't connected.")
        campaign.sync_requested_at = datetime.now(timezone.utc)
        await self.repository.save_campaign(campaign, 'sync_requested_at')

    async def _drop_webhook(self, campaign: PatreonCampaign) -> None:
        """Best effort: a revoked sign-in can't remove its webhook, and that mustn't block staff."""
        if not campaign.webhook_id:
            return
        try:
            client = oauth.api_client(await self._access_token(campaign))
            await client.delete_webhook(campaign.webhook_id)
        except (PatreonError, ValueError):
            logger.info('could not remove the Patreon webhook for tenant %s', campaign.tenant_id, exc_info=True)

    async def _access_token(self, campaign: PatreonCampaign) -> str:
        """The creator's access token, refreshed first when it's close to lapsing."""
        if self._expiring(campaign) and campaign.refresh_token:
            async with shared_lock('patreon.token_refresh', campaign.tenant_id):
                await campaign.refresh_from_db()
                if self._expiring(campaign) and campaign.refresh_token:
                    refresh = oauth.open_for(campaign, campaign.refresh_token)
                    if refresh is None:
                        raise PatreonAuthError('The stored Patreon sign-in no longer opens.')
                    tokens = await oauth.oauth_client().refresh(refresh)
                    campaign.access_token = oauth.seal_for(campaign.tenant_id, tokens.access_token)
                    if tokens.refresh_token:
                        campaign.refresh_token = oauth.seal_for(campaign.tenant_id, tokens.refresh_token)
                    campaign.token_expires_at = _expires_at(tokens)  # type: ignore[assignment]
                    await self.repository.save_campaign(campaign, 'access_token', 'refresh_token', 'token_expires_at')
        access = oauth.open_for(campaign, campaign.access_token)
        if access is None:
            raise PatreonAuthError('The stored Patreon sign-in no longer opens.')
        return access

    @staticmethod
    def _expiring(campaign: PatreonCampaign) -> bool:
        if campaign.token_expires_at is None:
            return False
        return datetime.now(timezone.utc) >= _aware(campaign.token_expires_at) - _REFRESH_BUFFER

    # ── webhook ────────────────────────────────────────────────────────

    # feature-gate: exempt — Patreon posts it; with the flag off it is acknowledged and ignored.
    async def handle_webhook(self, token: str, body: bytes, headers: Mapping[str, str]) -> str:
        """Verify and apply one Patreon webhook. Returns what it did, for the log.

        Unknown token → :class:`NotFoundError`; bad or missing signature →
        :class:`PatreonSignatureError`, before anything is read from the body.
        Repeats are harmless: the same status recorded twice changes nothing.
        """
        from application.services.user_service import UserService

        campaign = await self.repository.get_campaign_by_webhook_token(token)
        if campaign is None:
            raise NotFoundError('Unknown webhook.')
        lowered = {k.lower(): v for k, v in headers.items()}
        secret = oauth.open_for(campaign, campaign.webhook_secret, oauth.WEBHOOK_PURPOSE)
        if not signature_ok(secret, body, lowered.get('x-patreon-signature')):
            raise PatreonSignatureError('Bad signature.')
        with tenant_scope(campaign.tenant_id):
            if not await FeatureFlagService().is_enabled(FeatureFlag.PATREON):
                return 'disabled'
            try:
                member = parse_webhook(body)
            except PatreonError:
                return 'ignored'
            if member.campaign_id and member.campaign_id != campaign.campaign_id:
                return 'other campaign'
            if not member.patreon_user_id:
                return 'ignored'
            user = await self.repository.user_by_patreon_id(member.patreon_user_id)
            if user is None or not await TenantMembershipRepository.is_member(user.id, campaign.tenant_id):
                return 'not linked'
            status: Optional[MemberStatus] = None if lowered.get('x-patreon-event') == 'members:delete' else member
            system = await UserService().get_system_user()
            changed = await self.patrons.record(user.id, status, system, source='webhook')
            return 'badge changed' if changed else 'recorded'

    # ── full pass (the sync worker) ────────────────────────────────────

    # feature-gate: exempt — the sync worker checks the flag for each community before calling this.
    async def sync_if_due(self, actor: User, *, force: bool = False) -> Optional[int]:
        """Refresh the token and, when due, correct every linked member here. Returns badges changed, or None if skipped."""
        campaign = await self.repository.get_campaign()
        if campaign is None:
            return None
        now = datetime.now(timezone.utc)
        due = force or campaign.sync_requested_at is not None or campaign.last_synced_at is None or (
            _aware(campaign.last_synced_at) <= now - SYNC_EVERY
        )
        try:
            access = await self._access_token(campaign)
            if not due:
                return None
            changed = await self._reconcile(campaign, access, actor)
        except PatreonAuthError:
            logger.warning('Patreon sign-in refused for tenant %s', campaign.tenant_id, exc_info=True)
            await self._sync_failed(campaign, _RECONNECT)
            return None
        except PatreonError as e:
            logger.warning('Patreon sync failed for tenant %s', campaign.tenant_id, exc_info=True)
            await self._sync_failed(campaign, f'Last sync failed: {e}'[:255])
            return None
        campaign.last_synced_at = datetime.now(timezone.utc)
        campaign.last_sync_error = None  # type: ignore[assignment]
        campaign.sync_requested_at = None  # type: ignore[assignment]
        await self.repository.save_campaign(campaign, 'last_synced_at', 'last_sync_error', 'sync_requested_at')
        return changed

    async def _sync_failed(self, campaign: PatreonCampaign, message: str) -> None:
        campaign.last_sync_error = message
        campaign.sync_requested_at = None  # type: ignore[assignment]
        await self.repository.save_campaign(campaign, 'last_sync_error', 'sync_requested_at')

    async def _reconcile(self, campaign: PatreonCampaign, access: str, actor: User) -> int:
        client = oauth.api_client(access)
        await self.repository.sync_tiers([(t.id, t.title) for t in await client.tiers(campaign.campaign_id)])
        linked = await self.repository.linked_member_ids()
        seen: Dict[str, MemberStatus] = {}
        cursor: Optional[str] = None
        while True:
            members, cursor = await client.members_page(campaign.campaign_id, cursor)
            for m in members:
                if m.patreon_user_id in linked:
                    seen[m.patreon_user_id] = m
            if cursor is None:
                break
            await asyncio.sleep(PAGE_PAUSE_SECONDS)
        changed = 0
        for patreon_id, user_id in linked.items():
            if await self.patrons.record(user_id, seen.get(patreon_id), actor, source='sync'):
                changed += 1
        stale = set((await self.repository.memberships_by_user()).keys()) - set(linked.values())
        for user_id in stale:
            if await self.patrons.record(user_id, None, actor, source='sync'):
                changed += 1
        return changed

