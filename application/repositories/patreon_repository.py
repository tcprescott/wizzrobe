"""
Patreon Repository - Data Access Layer

A community's connected campaign, its tier labels, linked members' standing in
it, and Patreon sign-ins in flight.
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tortoise.expressions import Subquery

from application.repositories._base import TenantScopedRepository
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    PatreonCampaign,
    PatreonMembership,
    PatreonOAuthPurpose,
    PatreonOAuthState,
    PatreonTierBadge,
    TenantMembership,
    User,
)

ACTIVE_PATRON = 'active_patron'


class PatreonRepository(TenantScopedRepository[PatreonCampaign]):
    """Everything Patreon stores, for the community in scope unless a method says otherwise."""

    model = PatreonCampaign

    @staticmethod
    async def get_campaign() -> Optional[PatreonCampaign]:
        return await scoped(PatreonCampaign.all()).first()

    @staticmethod
    async def get_campaign_by_webhook_token(token: str) -> Optional[PatreonCampaign]:
        """The campaign a Patreon webhook names, from any community — **unscoped** on purpose.

        A webhook carries no community: the opaque token in its URL picks the
        row, and the caller binds that row's tenant before reading anything else.
        """
        return await PatreonCampaign.get_or_none(webhook_token=token)

    @staticmethod
    async def connected_tenant_ids() -> List[int]:
        """Every community with a campaign connected — a **global** scan for the sync worker."""
        return [c.tenant_id for c in await PatreonCampaign.all().only('id', 'tenant_id')]

    @staticmethod
    async def campaign_sync_health() -> Tuple[int, int]:
        """``(connected campaigns, of which the last sync failed)`` everywhere — a **global** count for health."""
        total = await PatreonCampaign.all().count()
        return total, await PatreonCampaign.filter(last_sync_error__isnull=False).count()

    @staticmethod
    async def save_campaign(campaign: PatreonCampaign, *fields: str) -> None:
        await save_fields(campaign, *fields)

    @staticmethod
    async def delete_community_data() -> Tuple[int, int]:
        """Drop the campaign, its tier labels and every membership here. Returns ``(memberships, active)``."""
        memberships = scoped(PatreonMembership.all())
        active = await memberships.filter(patron_status=ACTIVE_PATRON).count()
        removed = await memberships.delete()
        await scoped(PatreonTierBadge.all()).delete()
        await scoped(PatreonCampaign.all()).delete()
        return removed, active

    # ── tiers ──────────────────────────────────────────────────────────

    @staticmethod
    async def list_tiers() -> List[PatreonTierBadge]:
        return await scoped(PatreonTierBadge.all()).order_by('sort', 'id')

    @staticmethod
    async def sync_tiers(tiers: Sequence[Tuple[str, str]]) -> None:
        """Match the stored tiers to Patreon's ``(id, title)`` list, keeping labels and order."""
        tenant_id = current_tenant_id()
        existing = {t.tier_id: t for t in await scoped(PatreonTierBadge.all())}
        wanted = {tier_id for tier_id, _ in tiers}
        gone = [t.id for tier_id, t in existing.items() if tier_id not in wanted]
        if gone:
            await scoped(PatreonTierBadge.filter(id__in=gone)).delete()
        next_sort = max((t.sort for t in existing.values()), default=-1) + 1
        for tier_id, title in tiers:
            row = existing.get(tier_id)
            if row is None:
                await PatreonTierBadge.create(tenant_id=tenant_id, tier_id=tier_id, tier_title=title[:255], sort=next_sort)
                next_sort += 1
            elif row.tier_title != title[:255]:
                row.tier_title = title[:255]
                await save_fields(row, 'tier_title')

    @staticmethod
    async def set_tier_labels(labels: Sequence[Tuple[str, Optional[str]]]) -> None:
        """Set each tier's label and its order to its place in ``labels``."""
        rows = {t.tier_id: t for t in await scoped(PatreonTierBadge.all())}
        for sort, (tier_id, label) in enumerate(labels):
            row = rows.get(tier_id)
            if row is None:
                continue
            row.badge_label = label
            row.sort = sort
            await save_fields(row, 'badge_label', 'sort')

    # ── memberships ────────────────────────────────────────────────────

    @staticmethod
    async def get_membership(user_id: int) -> Optional[PatreonMembership]:
        return await scoped(PatreonMembership.filter(user_id=user_id)).first()

    @staticmethod
    async def memberships_by_user() -> Dict[int, PatreonMembership]:
        return {m.user_id: m for m in await scoped(PatreonMembership.all())}

    @staticmethod
    async def upsert_membership(
        user_id: int, *, member_id: Optional[str], patron_status: Optional[str],
        tier_ids: List[str], synced_at: datetime,
    ) -> PatreonMembership:
        row = await scoped(PatreonMembership.filter(user_id=user_id)).first()
        if row is None:
            return await PatreonMembership.create(
                tenant_id=current_tenant_id(), user_id=user_id, patreon_member_id=member_id,
                patron_status=patron_status, tier_ids=tier_ids, synced_at=synced_at,
            )
        row.patreon_member_id = member_id
        row.patron_status = patron_status
        row.tier_ids = tier_ids
        row.synced_at = synced_at
        await save_fields(row, 'patreon_member_id', 'patron_status', 'tier_ids', 'synced_at')
        return row

    @staticmethod
    async def delete_membership(user_id: int) -> None:
        await scoped(PatreonMembership.filter(user_id=user_id)).delete()

    @staticmethod
    async def delete_memberships(user_ids: Iterable[int]) -> None:
        ids = list(user_ids)
        if ids:
            await scoped(PatreonMembership.filter(user_id__in=ids)).delete()

    @staticmethod
    async def active_memberships() -> List[Tuple[int, List[str]]]:
        """``(user_id, tier_ids)`` for every active patron here whose badge isn't hidden."""
        rows = await scoped(PatreonMembership.filter(
            patron_status=ACTIVE_PATRON, user__patreon_badge_hidden=False,
        )).values_list('user_id', 'tier_ids')
        return [(int(u), list(t or [])) for u, t in rows]

    @staticmethod
    async def membership_counts() -> Tuple[int, int]:
        """``(linked members with a row, of whom active patrons)`` here."""
        rows = scoped(PatreonMembership.all())
        return await rows.count(), await rows.filter(patron_status=ACTIVE_PATRON).count()

    @staticmethod
    async def memberships_everywhere(user_id: int) -> List[PatreonMembership]:
        """The person's rows in every community — **cross-tenant**: their own data, for unlink and Me."""
        return await PatreonMembership.filter(user_id=user_id).prefetch_related('tenant')

    @staticmethod
    async def delete_memberships_everywhere(user_id: int) -> None:
        """**Cross-tenant**: unlinking forgets the person's standing in every community."""
        await PatreonMembership.filter(user_id=user_id).delete()

    @staticmethod
    async def connected_tenant_ids_among(tenant_ids: Iterable[int]) -> List[int]:
        """Which of ``tenant_ids`` have a campaign connected — a **global** read for linking."""
        ids = list(tenant_ids)
        if not ids:
            return []
        return [c.tenant_id for c in await PatreonCampaign.filter(tenant_id__in=ids).only('id', 'tenant_id')]

    # ── people ─────────────────────────────────────────────────────────

    @staticmethod
    async def users_by_patreon_id(patreon_user_ids: Iterable[str]) -> Dict[str, int]:
        """Linked Wizzrobe accounts for these Patreon user ids (``User`` is global)."""
        ids = [p for p in set(patreon_user_ids) if p]
        if not ids:
            return {}
        rows = await User.filter(patreon_user_id__in=ids).values_list('patreon_user_id', 'id')
        return {str(p): int(u) for p, u in rows}

    @staticmethod
    async def linked_member_ids() -> Dict[str, int]:
        """Patreon id → user id for every linked member of the community in scope."""
        member_ids = Subquery(TenantMembership.filter(tenant_id=current_tenant_id()).values('user_id'))
        rows = await User.filter(patreon_user_id__isnull=False, id__in=member_ids).values_list('patreon_user_id', 'id')
        return {str(p): int(u) for p, u in rows}

    @staticmethod
    async def user_by_patreon_id(patreon_user_id: str) -> Optional[User]:
        return await User.get_or_none(patreon_user_id=patreon_user_id)

    @staticmethod
    async def set_user_link(user: User, patreon_user_id: Optional[str], name: Optional[str], at: Optional[datetime]) -> None:
        user.patreon_user_id = patreon_user_id  # type: ignore[assignment]
        user.patreon_username = name  # type: ignore[assignment]
        user.patreon_linked_at = at  # type: ignore[assignment]
        await save_fields(user, 'patreon_user_id', 'patreon_username', 'patreon_linked_at')

    @staticmethod
    async def set_badge_hidden(user: User, hidden: bool) -> None:
        user.patreon_badge_hidden = hidden
        await save_fields(user, 'patreon_badge_hidden')

    # ── sign-ins in flight ─────────────────────────────────────────────

    @staticmethod
    async def create_state(
        state_hash: str, purpose: PatreonOAuthPurpose, user_id: int, expires_at: datetime,
    ) -> PatreonOAuthState:
        return await PatreonOAuthState.create(
            tenant_id=current_tenant_id(), state_hash=state_hash, purpose=purpose, user_id=user_id,
            expires_at=expires_at,
        )

    @staticmethod
    async def pop_state(state_hash: str) -> Optional[PatreonOAuthState]:
        """Take a pending sign-in by its hash, once — **unscoped**: the callback has no community yet."""
        row = await PatreonOAuthState.get_or_none(state_hash=state_hash)
        if row is None:
            return None
        deleted = await PatreonOAuthState.filter(id=row.id).delete()
        return row if deleted else None

    @staticmethod
    async def prune_states(before: datetime) -> None:
        """Forget lapsed sign-ins in every community — a **global** sweep."""
        await PatreonOAuthState.filter(expires_at__lt=before).delete()
