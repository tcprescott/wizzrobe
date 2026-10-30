#!/usr/bin/env python3
"""Give everyone who participates in a community a ``TenantMembership``.

Run from the project root:
    poetry run python scripts/backfill_memberships.py [--dry-run]

Two populations need catching up, both created by the same gap: the
multitenancy migration wrote a membership per user *at that point*, and nothing
wrote one afterwards until the "a role in a tenant implies membership in it"
invariant landed.

- **Role holders** granted through the Users tab or the Discord role sync, both
  of which wrote a role and no membership.
- **Ordinary participants** — accounts created since that migration that never
  received a role: enrolled players, match players, crew signups, volunteers.
  These are the ones the membership gate would lock out.

The signals are defined in ``scripts/membership_coverage.py`` and shared with
``scripts/audit_membership_coverage.py``, so the report and the fix can never
disagree. The audit-log signal is deliberately **not** among them: appearing in
an audit row can mean somebody acted *on* you.

``SUPER_ADMIN`` is skipped: its ``UserRole`` carries ``tenant=NULL`` and it
belongs to no community.

Idempotent and safe to re-run; the membership gate's go/no-go re-runs it.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tortoise import Tortoise

from models import TenantMembership
from scripts.membership_coverage import coverage_gaps, grantable_gaps


async def backfill(dry_run: bool = False) -> int:
    gaps = await grantable_gaps()
    if not gaps:
        print('No gaps: every participant is already a member of the communities '
              'they take part in.')
        return 0

    by_signal = await coverage_gaps(include_report_only=False)
    print(f'{len(gaps)} (user, tenant) pair(s) with participation but no membership:')
    for label, pairs in by_signal.items():
        if pairs:
            print(f'  via {label}: {len(pairs)}')
    for user_id, tenant_id in gaps:
        print(f'  user={user_id} tenant={tenant_id}')

    if dry_run:
        print('--dry-run: nothing written.')
        return len(gaps)
    for user_id, tenant_id in gaps:
        await TenantMembership.get_or_create(user_id=user_id, tenant_id=tenant_id)
    print(f'Wrote {len(gaps)} membership row(s).')
    return len(gaps)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='report without writing')
    args = parser.parse_args()

    # Lazy, so importing this module stays env-free: load_dotenv() at import time leaks the dev .env.
    from dotenv import load_dotenv
    load_dotenv()

    from migrations.tortoise_config import TORTOISE_ORM

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        await backfill(dry_run=args.dry_run)
    finally:
        await Tortoise.close_connections()


if __name__ == '__main__':
    asyncio.run(main())
