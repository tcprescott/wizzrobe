#!/usr/bin/env python3
"""Create a new Tenant and (optionally) bootstrap its first STAFF + a SUPER_ADMIN.

    poetry run python scripts/seed_tenant.py --name "Wizzrobe Live" --slug wizzrobe \
        [--domain wizzrobe.example.com] [--guild-id 123456789] [--operator-discord-id 456]

Creates the :class:`~models.Tenant` (idempotent on ``slug``). When
``--operator-discord-id`` is given and that user exists, grants them the global
``SUPER_ADMIN`` role, ``STAFF`` in the new tenant, and a membership — the way to
stand up a fresh community and its first admin. Requires the schema to exist
(run the migrations first).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from tortoise import Tortoise

from application.services.event_service import EventService
from migrations.tortoise_config import TORTOISE_ORM
from models import Role, Tenant, TenantMembership, User, UserRole


async def seed_tenant(name, slug, domain, guild_id, operator_discord_id) -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        tenant, created = await Tenant.get_or_create(
            slug=slug,
            defaults={'name': name, 'domain': domain, 'discord_guild_id': guild_id},
        )
        if created:
            print(f"Created tenant '{slug}' (id={tenant.id}).")
        else:
            print(f"Tenant '{slug}' already exists (id={tenant.id}).")
        event = await EventService.ensure_first_event(tenant)
        print(f"Default event: '{event.name}' (id={event.id}).")

        if operator_discord_id:
            user = await User.get_or_none(discord_id=operator_discord_id)
            if user is None:
                print(f"Operator discord_id {operator_discord_id!r} not found — they must log "
                      "in once before the role bootstrap. Tenant was still created.")
            else:
                await UserRole.get_or_create(
                    user=user, role=Role.SUPER_ADMIN, tenant=None, defaults={'granted_by': None},
                )
                await UserRole.get_or_create(
                    user=user, role=Role.STAFF, tenant=tenant, defaults={'granted_by': None},
                )
                await TenantMembership.get_or_create(user=user, tenant=tenant)
                label = user.display_name or user.username or operator_discord_id
                print(f"Bootstrapped {label}: SUPER_ADMIN + STAFF in '{slug}' + membership.")
    finally:
        await Tortoise.close_connections()


def _parse_args():
    p = argparse.ArgumentParser(description="Create a tenant and optionally bootstrap its first admin.")
    p.add_argument('--name', required=True, help="Display name.")
    p.add_argument('--slug', required=True, help="URL-safe slug (path routing key /t/<slug>).")
    p.add_argument('--domain', default=None, help="Optional custom domain (host mode).")
    p.add_argument('--guild-id', type=int, default=None, help="Discord guild id for bot routing.")
    p.add_argument('--operator-discord-id', default=None,
                   help="Discord id to grant SUPER_ADMIN + STAFF + membership.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(seed_tenant(
        args.name, args.slug, args.domain, args.guild_id, args.operator_discord_id,
    ))
