"""One person's notification choices in one community (Profile → Notifications).

A preset plus per-message overrides, keyed ``"<notice>:<recipient>"`` with an
``off``/``silent``/``ping`` value. The community's own setting
(:class:`NotificationSetting`) is a ceiling: a person can make a message quieter,
never louder. A person with no row gets everything the community sends.
"""

from tortoise import fields
from tortoise.models import Model

from .enums import NotificationPreset


class NotificationPreference(Model):
    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='notification_preferences', on_delete=fields.CASCADE)
    tenant_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='notification_preferences', on_delete=fields.CASCADE)
    user_id: int
    preset = fields.CharEnumField(NotificationPreset, max_length=16, default=NotificationPreset.EVERYTHING)
    # mypy cannot infer a JSONField's type parameter.
    overrides = fields.JSONField(default=dict)  # type: ignore[var-annotated]
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'notificationpreference'
        unique_together = (('tenant', 'user'),)

    def __str__(self) -> str:
        return f'{self.user_id}@{self.tenant_id}: {self.preset}'
