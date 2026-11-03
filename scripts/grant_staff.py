#!/usr/bin/env python3
"""Grant the STAFF role to a user **within a tenant**, by Discord ID.

Run from the project root:
    poetry run python scripts/grant_staff.py <discord_id> [tenant_slug]

``tenant_slug`` defaults to ``default`` (the tenant the migration backfilled).
Roles are per-tenant now, so a STAFF grant always names a tenant; this also
ensures the user is a member of that tenant. Idempotent — safe to re-run. The
user must already exist (they are created on first login).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from tortoise import Tortoise

from migrations.tortoise_config import TORTOISE_ORM
from models import Role, Tenant, TenantMembership, User, UserRole


async def grant_staff(discord_id: str, tenant_slug: str = "default") -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        tenant = await Tenant.get_or_none(slug=tenant_slug)
        if tenant is None:
            print(f"No tenant found with slug {tenant_slug!r}. "
                  "Create it first with scripts/seed_tenant.py.")
            sys.exit(1)

        user = await User.get_or_none(discord_id=discord_id)
        if user is None:
            print(f"No user found with discord_id {discord_id!r}. "
                  "They must log in at least once before being granted a role.")
            sys.exit(1)

        _, created = await UserRole.get_or_create(
            user=user, role=Role.STAFF, tenant=tenant, defaults={"granted_by": None},
        )
        await TenantMembership.get_or_create(user=user, tenant=tenant)
        label = user.display_name or user.username or discord_id
        if created:
            print(f"Granted STAFF to {label} ({discord_id}) in tenant '{tenant_slug}'.")
        else:
            print(f"{label} ({discord_id}) already has STAFF in tenant '{tenant_slug}'.")
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: poetry run python scripts/grant_staff.py <discord_id> [tenant_slug]")
        sys.exit(2)
    slug = sys.argv[2] if len(sys.argv) == 3 else "default"
    asyncio.run(grant_staff(sys.argv[1], slug))
