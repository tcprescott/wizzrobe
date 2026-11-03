"""
Patron Service - Business Logic Layer

The one place anything outside the Patreon integration asks about patrons.
:meth:`PatronService.get_patron_status` answers "is this person a patron in
this community?"; every later perk (patron Triforce texts, say) calls it and
never reads Patreon tables or Patreon itself.

Also owns the one write every path shares: recording what Patreon says about
a linked member here, and auditing the badge turning on or off.

Gated by :attr:`~models.FeatureFlag.PATREON`, as a soft integration point:
with the flag off these answer "not a patron" and "no badges" rather than
raising, so a caller in an unrelated flow never breaks.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from application.repositories import PatreonRepository
from application.repositories.patreon_repository import ACTIVE_PATRON
from application.services.audit_service import AuditActions, AuditService
from application.services.feature_flag_service import FeatureFlagService
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.clients.patreon_client import MemberStatus
from models import FeatureFlag, PatreonTierBadge, User

DEFAULT_BADGE_LABEL = 'Patron'


@dataclass(frozen=True)
class PatronStatus:
    """A person's standing in one community's campaign.

    ``badge_label`` is set whenever ``is_patron`` is, even if they chose to hide
    the badge: ``badge_hidden`` says whether to show it to other people.
    """

    is_patron: bool
    tier_ids: Tuple[str, ...] = field(default_factory=tuple)
    badge_label: Optional[str] = None
    last_synced: Optional[datetime] = None
    badge_hidden: bool = False


NOT_A_PATRON = PatronStatus(is_patron=False)


def badge_label_for(tier_ids: List[str], tiers: List[PatreonTierBadge]) -> str:
    """The label of the first tier (in staff's order) they hold that has one, else the default."""
    held = set(tier_ids)
    for tier in tiers:
        if tier.tier_id in held and tier.badge_label:
            return tier.badge_label
    return DEFAULT_BADGE_LABEL


class PatronService:
    """Patron status per (person, community), and the badge transitions behind it."""

    def __init__(self) -> None:
        self.repository = PatreonRepository()
        self.audit_service = AuditService()

    # feature-gate: exempt — soft integration point: off reads as "not a patron" for every caller.
    async def get_patron_status(self, user: Optional[User], *, tenant_id: Optional[int] = None) -> PatronStatus:
        """Whether ``user`` is an active patron in ``tenant_id`` (default: the community in scope)."""
        if user is None or not user.patreon_user_id:
            return NOT_A_PATRON
        target = tenant_id if tenant_id is not None else get_current_tenant_id()
        if target is None:
            return NOT_A_PATRON
        with tenant_scope(target):
            if not await FeatureFlagService().is_enabled(FeatureFlag.PATREON):
                return NOT_A_PATRON
            row = await self.repository.get_membership(user.id)
            if row is None:
                return NOT_A_PATRON
            is_patron = row.patron_status == ACTIVE_PATRON
            tiers = list(row.tier_ids or [])
            label = badge_label_for(tiers, await self.repository.list_tiers()) if is_patron else None
            return PatronStatus(
                is_patron=is_patron, tier_ids=tuple(tiers), badge_label=label,
                last_synced=row.synced_at, badge_hidden=user.patreon_badge_hidden,
            )

    # feature-gate: exempt — soft integration point: the member list shows no badges with the flag off.
    async def visible_badges(self) -> Dict[int, str]:
        """User id → badge label for every patron here who hasn't hidden theirs (two queries)."""
        if not await FeatureFlagService().is_enabled(FeatureFlag.PATREON):
            return {}
        rows = await self.repository.active_memberships()
        if not rows:
            return {}
        tiers = await self.repository.list_tiers()
        return {user_id: badge_label_for(tier_ids, tiers) for user_id, tier_ids in rows}

    # feature-gate: exempt — called by the gated Patreon services and the sync worker, which check the flag first.
    async def record(self, user_id: int, status: Optional[MemberStatus], actor: User, *, source: str) -> bool:
        """Store what Patreon says about linked member ``user_id`` here; ``None`` means no membership.

        Audits the badge turning on or off. Returns True when it changed.
        """
        before = await self.repository.get_membership(user_id)
        was = before is not None and before.patron_status == ACTIVE_PATRON
        if status is None:
            if before is not None:
                await self.repository.delete_membership(user_id)
        else:
            await self.repository.upsert_membership(
                user_id, member_id=status.member_id, patron_status=status.patron_status,
                tier_ids=list(status.tier_ids), synced_at=datetime.now(timezone.utc),
            )
        now = status is not None and status.is_patron
        if was == now:
            return False
        action = AuditActions.PATREON_BADGE_GRANTED if now else AuditActions.PATREON_BADGE_REVOKED
        await self.audit_service.write_log(actor, action, {
            'user_id': user_id, 'source': source,
            'patron_status': status.patron_status if status is not None else None,
        })
        return True
