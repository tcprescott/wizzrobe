"""A community's override of how one notification reaches one kind of recipient.

The catalogue of notifications and their defaults lives in code
(``application/services/notification_catalog.py``); a row exists only where staff
chose something other than the default, so resetting deletes it.
"""

from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import NotificationMode, NotificationRecipient


class NotificationSetting(Model):
    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='notification_settings', on_delete=fields.CASCADE)
    tenant_id: int
    notice = fields.CharField(max_length=64)
    recipient = fields.CharEnumField(NotificationRecipient, max_length=16)
    mode = fields.CharEnumField(NotificationMode, max_length=16)
    updated_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='+', null=True, on_delete=fields.SET_NULL)
    updated_by_id: Optional[int]
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'notificationsetting'
        unique_together = (('tenant', 'notice', 'recipient'),)

    def __str__(self) -> str:
        return f'{self.notice}:{self.recipient} = {self.mode}'
