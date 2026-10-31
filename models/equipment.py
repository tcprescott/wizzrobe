from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import EquipmentStatus


class Equipment(Model):
    """A physical asset available for lending at live events.

    Each asset gets an auto-assigned, unique ``asset_number`` (a scannable QR
    code on its page encodes the asset's URL). ``owner_user`` records who owns
    the asset; a ``null`` owner means it belongs to the community that owns it
    (its ``tenant``). ``status`` is kept in sync with open loans by the service
    layer (the single writer).
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='equipment', on_delete=fields.CASCADE)
    # Unique per tenant, not globally — each tenant runs its own asset numbering.
    asset_number = fields.IntField()
    name = fields.CharField(max_length=255)
    description = fields.TextField(null=True)
    private_notes = fields.TextField(null=True)
    owner_user = fields.ForeignKeyField(
        'models.User', related_name='owned_equipment', null=True, on_delete=fields.SET_NULL
    )
    status = fields.CharEnumField(EquipmentStatus, default=EquipmentStatus.AVAILABLE, max_length=20)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    loans = fields.ReverseRelation["EquipmentLoan"]

    def owner_label(self, community_name: str) -> str:
        """Display owner: the owner's ``preferred_name``, or the owning community.

        An asset with no ``owner_user`` belongs to the community that owns it
        (its ``tenant``); callers pass that community's display name, resolved
        from the request scope via ``TenantService.current_community_name()``.
        """
        return self.owner_user.preferred_name if self.owner_user else community_name

    class Meta:
        table = 'equipment'
        unique_together = (('tenant', 'asset_number'),)


class EquipmentLoan(Model):
    """A single checkout of an :class:`Equipment` asset.

    The open loan (``checked_in_at`` is null) identifies the current holder;
    closed loans form the asset's full lending history.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='equipment_loans', on_delete=fields.CASCADE)
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='equipment_loans', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    equipment = fields.ForeignKeyField('models.Equipment', related_name='loans', on_delete=fields.CASCADE)
    # RESTRICT on purpose: retire a user via is_active so the ownership trail survives.
    borrower = fields.ForeignKeyField(
        'models.User', related_name='equipment_loans', on_delete=fields.RESTRICT
    )
    checked_out_by = fields.ForeignKeyField(
        'models.User', related_name='equipment_checkouts_performed', on_delete=fields.RESTRICT
    )
    checked_out_at = fields.DatetimeField(auto_now_add=True)
    checked_in_at = fields.DatetimeField(null=True)
    checked_in_by = fields.ForeignKeyField(
        'models.User', related_name='equipment_checkins_performed', null=True, on_delete=fields.SET_NULL
    )

    class Meta:
        table = 'equipmentloan'
        indexes = (('equipment',), ('borrower',))
