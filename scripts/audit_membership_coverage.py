#!/usr/bin/env python3
"""Report users who participate in a community but are not members of it.

Read-only. Safe to run against production.

    poetry run python scripts/audit_membership_coverage.py

This is the go/no-go for closing the membership gate. Gating a community on
``TenantMembership`` locks out anyone the backfill missed, and the exposure is
specific: the multitenancy migration wrote a membership per user *at that
point*, and until the "a role implies membership" invariant landed, nothing
wrote one afterwards except the first-admin grant. So the people at risk are
accounts created since that migration who never received a role — ordinary
players and crew.

Run this, run ``scripts/backfill_memberships.py``, run this again. The second
run must report zero grantable gaps.

The audit-log signal is reported but never backfilled on: appearing in an audit
row can mean somebody acted *on* you, not that you belong. Read what it
surfaces that the others do not, by hand.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tortoise import Tortoise

from models import Tenant, TenantMembership, User
from scripts.membership_coverage import (
    GRANTING_SIGNALS,
    REPORT_ONLY_SIGNALS,
    coverage_gaps,
)

SAMPLE = 5


async def report() -> int:
    """Print the coverage report; return the number of grantable gaps."""
    gaps = await coverage_gaps(include_report_only=True)
    tenants = {t.id: t for t in await Tenant.all()}
    usernames = {u.id: (u.username or f'#{u.id}') for u in await User.all()}

    grantable = set().union(*(gaps[label] for label in GRANTING_SIGNALS)) if gaps else set()
    report_only = set().union(*(gaps[label] for label in REPORT_ONLY_SIGNALS)) - grantable

    print('=== Participation without membership, by signal ===')
    for label in list(GRANTING_SIGNALS) + list(REPORT_ONLY_SIGNALS):
        pairs = gaps.get(label, set())
        marker = '' if label in GRANTING_SIGNALS else '   (report only — never backfilled)'
        print(f'  {label}: {len(pairs)}{marker}')

    print()
    print('=== Grantable gaps, by community ===')
    if not grantable:
        print('  none — every participant is already a member.')
    else:
        by_tenant: dict[int, list[int]] = {}
        for user_id, tenant_id in sorted(grantable):
            by_tenant.setdefault(tenant_id, []).append(user_id)
        for tenant_id, user_ids in sorted(by_tenant.items(), key=lambda kv: -len(kv[1])):
            tenant = tenants.get(tenant_id)
            name = f"'{tenant.slug}'" if tenant else f'#{tenant_id} (deleted?)'
            sample = ', '.join(usernames.get(u, f'#{u}') for u in user_ids[:SAMPLE])
            more = f' … +{len(user_ids) - SAMPLE} more' if len(user_ids) > SAMPLE else ''
            print(f'  {name}: {len(user_ids)} — {sample}{more}')

    print()
    print('=== Audit-log-only participants (hand-review) ===')
    if not report_only:
        print('  none.')
    else:
        for user_id, tenant_id in sorted(report_only)[:20]:
            tenant = tenants.get(tenant_id)
            name = tenant.slug if tenant else f'#{tenant_id}'
            print(f"  {usernames.get(user_id, f'#{user_id}')} in '{name}'")
        if len(report_only) > 20:
            print(f'  … +{len(report_only) - 20} more')

    member_user_ids = {m.user_id for m in await TenantMembership.all()}
    homeless = [u for u in await User.all() if u.id not in member_user_ids]
    print()
    print(f'=== Accounts belonging to no community at all: {len(homeless)} ===')
    print('  These are correct: after the gate they meet the join page.')
    for u in homeless[:SAMPLE]:
        print(f'  {u.username}')
    if len(homeless) > SAMPLE:
        print(f'  … +{len(homeless) - SAMPLE} more')

    print()
    print(f'GRANTABLE GAPS: {len(grantable)}')
    return len(grantable)


async def main() -> None:
    # Lazy, so importing this module stays env-free.
    from dotenv import load_dotenv
    load_dotenv()

    from migrations.tortoise_config import TORTOISE_ORM

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        gaps = await report()
    finally:
        await Tortoise.close_connections()
    sys.exit(1 if gaps else 0)


if __name__ == '__main__':
    asyncio.run(main())
