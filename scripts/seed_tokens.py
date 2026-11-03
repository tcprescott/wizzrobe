"""Dev-seed fixtures for one tenant's bearer tokens and room screens.

Split out of ``seed_dev.py`` when that module crossed the 800-line budget again,
following the ``seed_equipment_for_tenant`` / ``seed_volunteers_for_tenant``
convention already used there: one module per domain, called from inside the
caller's ``tenant_scope``. Idempotent and tenant-stamped like every other seeded
row.

Both halves are **deterministic** per tenant, which is the whole point: the
``/api-validation`` skill and any manual curl run need a bearer they can predict,
and ``/ui-validation`` needs a room URL that opens the seeds board without a
login. They are non-secret dev fixtures — only the hash is stored, exactly as in
production — and each surface gets a live row plus its retired/read-only twin so
both states render.
"""

import hashlib
from datetime import datetime

from application.services.room_token_service import hash_token
from models import ApiToken, RoomToken, Tenant, User


async def seed_tokens_for_tenant(tenant: Tenant, staff: User, now: datetime) -> None:
    """Seed one tenant's API tokens and tournament-room tokens."""
    dev_bearer = f"wizzrobe_pat_devseed_{tenant.slug}_local_only_do_not_use"
    if not await ApiToken.filter(user=staff, name="Dev Seed Token", tenant=tenant).exists():
        await ApiToken.create(
            user=staff, name="Dev Seed Token", tenant=tenant,
            token_hash=hashlib.sha256(dev_bearer.encode()).hexdigest(),
            token_prefix=dev_bearer[:17], read_only=False,
        )
    ro_bearer = f"wizzrobe_pat_devseedro_{tenant.slug}_local_only_do_not"
    if not await ApiToken.filter(user=staff, name="Dev Read-Only Token", tenant=tenant).exists():
        await ApiToken.create(
            user=staff, name="Dev Read-Only Token", tenant=tenant,
            token_hash=hashlib.sha256(ro_bearer.encode()).hexdigest(),
            token_prefix=ro_bearer[:17], read_only=True,
        )
    print(f"    [{tenant.slug}] api tokens ok (dev bearer: {dev_bearer})")

    room_token = f"wizzrobe_room_devseed_{tenant.slug}_local_only_do_not_use"
    await RoomToken.get_or_create(
        token_hash=hash_token(room_token),
        defaults={"label": "Room A desk PC", "tenant": tenant, "created_by": staff},
    )
    await RoomToken.get_or_create(
        token_hash=hash_token(f"{room_token}_retired"),
        defaults={
            "label": "Old laptop (retired)", "tenant": tenant,
            "created_by": staff, "revoked_at": now,
        },
    )
    print(f"    [{tenant.slug}] room tokens ok (/room/{room_token}/seeds)")
