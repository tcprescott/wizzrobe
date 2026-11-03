"""Dev-seed fixtures for one tenant's equipment-lending surface.

Split out of ``seed_dev.py`` when that module crossed the 800-line budget,
following the ``seed_volunteers_for_tenant`` / ``seed_brackets_for_tenant``
convention already used there: one module per domain, called from inside the
caller's ``tenant_scope``. Idempotent (``get_or_create`` / existence-guarded)
and tenant-stamped like every other seeded row.

The asset set is chosen so each state the lending UI can be in is reachable in
the dev loop, because a state the seed never creates is a state nobody can
re-check:

* an **available** asset and one **checked out to a player** (the original pair —
  other audits and screenshots depend on them);
* one **held by a plain VOLUNTEER**, which is how the borrower's own dead end
  (a checked-out asset with no controls) gets reproduced;
* one with a **long history**, so the bounded loan list and its "5 of N" line
  have a real N;
* an ``EQUIPMENT_MANAGER``-only grant, so the manager surfaces can be driven by
  someone who is not also STAFF;
* a **self-checked-out loaner** (``checked_out_by`` is the borrower, who holds no
  role at all) and a spare sitting **available** — the QR-code path, where a
  player scans the code on the device in their hand and checks it out to
  themselves. Both are needed: the closed one shows the resulting loan, the open
  one is what you scan to walk the flow.
"""

from datetime import datetime, timedelta

from tortoise.functions import Max

from models import Equipment, EquipmentLoan, EquipmentStatus, Role, Tenant, User, UserRole

_LONG_HISTORY = 15

_ASSET_SPECS = (
    ('Capture Card A', 'Elgato HD60 X', None),
    ('Capture Card B', 'Elgato HD60 X', None),
    ('Console 1', 'Super Nintendo (SNES)', 'staff'),
    ('HDMI Splitter', '4-way powered splitter', None),
    ('Mic Stand', 'Boom arm, held by a volunteer', None),
    ('Long HDMI Run', '50ft HDMI — lent constantly', None),
    ('Console 2', 'Super Nintendo (SNES) — dead PPU, kept for parts', 'staff'),
    ('RetroTINK 2X', 'Upscaler loaner — scan the QR code on the unit', None),
    ('RetroTINK 2X (spare)', 'Upscaler loaner — the one still on the shelf', None),
)


async def seed_equipment_for_tenant(
    tenant: Tenant,
    users: dict[str, User],
    staff: User,
    now_utc: datetime,
) -> dict[str, Equipment]:
    """Seed the role grants, the assets, and one loan in each meaningful state.

    Returns the assets by name; ``seed_dev.py``'s audit-log fixtures reference
    one of them.
    """
    # Don't grant staff_user EQUIPMENT_MANAGER: it must stay the plain-STAFF fixture (one holder per role).
    equip_manager = users['equip_manager']
    await UserRole.get_or_create(
        user=equip_manager, role=Role.EQUIPMENT_MANAGER, tenant=tenant,
        defaults={'granted_by': None},
    )

    owners = {'staff': staff}
    equipment: dict[str, Equipment] = {}
    for name, description, owner_key in _ASSET_SPECS:
        asset = await Equipment.get_or_none(name=name, tenant=tenant)
        if asset is None:
            row = await Equipment.filter(tenant=tenant).annotate(m=Max('asset_number')).values('m')
            next_number = (row[0]['m'] or 0) + 1
            asset = await Equipment.create(
                asset_number=next_number, name=name, description=description,
                owner_user=owners.get(owner_key), tenant=tenant,
            )
        equipment[name] = asset

    await _open_loan(
        equipment['Capture Card A'], users['player_one'], staff, tenant,
    )
    console = equipment['Console 1']
    if not await EquipmentLoan.filter(equipment=console, tenant=tenant).exists():
        await EquipmentLoan.create(
            equipment=console, borrower=users['player_two'], checked_out_by=staff,
            checked_in_at=now_utc, checked_in_by=staff, tenant=tenant,
        )

    await _open_loan(
        equipment['Mic Stand'], users['player_three'], equip_manager, tenant,
    )

    walk_in = users['outsider']
    await _open_loan(equipment['RetroTINK 2X'], walk_in, walk_in, tenant)

    retired = equipment['Console 2']
    if retired.status != EquipmentStatus.RETIRED:
        retired.status = EquipmentStatus.RETIRED
        await retired.save()

    long_run = equipment['Long HDMI Run']
    existing = await EquipmentLoan.filter(equipment=long_run, tenant=tenant).count()
    for i in range(existing, _LONG_HISTORY):
        out_at = now_utc - timedelta(days=_LONG_HISTORY - i, hours=3)
        await EquipmentLoan.create(
            equipment=long_run, borrower=users['player_four'], checked_out_by=staff,
            checked_out_at=out_at, checked_in_at=out_at + timedelta(hours=2),
            checked_in_by=staff, tenant=tenant,
        )

    print(
        f'    [{tenant.slug}] equipment ok — equip_manager holds EQUIPMENT_MANAGER only; '
        f"'Mic Stand' is out to player_three; 'RetroTINK 2X' is self-checked-out by outsider; "
        f"'Long HDMI Run' has {_LONG_HISTORY} closed loans"
    )
    return equipment


async def _open_loan(
    asset: Equipment, borrower: User, checked_out_by: User, tenant: Tenant,
) -> None:
    """Put ``asset`` out to ``borrower``, unless it already has any loan row."""
    if await EquipmentLoan.filter(equipment=asset, tenant=tenant).exists():
        return
    await EquipmentLoan.create(
        equipment=asset, borrower=borrower, checked_out_by=checked_out_by, tenant=tenant,
    )
    if asset.status != EquipmentStatus.CHECKED_OUT:
        asset.status = EquipmentStatus.CHECKED_OUT
        await asset.save()
