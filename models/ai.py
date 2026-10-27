from enum import Enum
from typing import Optional

from tortoise import fields
from tortoise.models import Model


class AiProvider(str, Enum):
    """Which API a community's AI key talks to.

    ``OPENAI_COMPATIBLE`` covers any service that speaks the OpenAI chat
    completions shape at a base URL the community gives (OpenAI itself, a
    gateway, a self-hosted model).
    """

    ANTHROPIC = 'anthropic'
    OPENAI_COMPATIBLE = 'openai_compatible'


class AiProviderSetting(Model):
    """A community's own AI provider and key, used to draft survey questions.

    There is no platform key: a community that wants AI help brings its own
    account and pays for it. ``key_ciphertext`` is the API key sealed by
    :mod:`application.utils.secret_box` (AES-GCM under a key derived from
    ``STORAGE_SECRET``, bound to the tenant id), so a database dump alone
    doesn't hand out working keys. :class:`~application.services.AiProviderService`
    is the only reader and never returns the key to a caller.

    ``daily_limit`` caps how many suggestion requests the community makes in
    any 24 hours, so a stuck button can't run up its bill.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='ai_provider_setting', on_delete=fields.CASCADE, unique=True,
    )
    tenant_id: int
    provider = fields.CharEnumField(AiProvider, max_length=32)
    model = fields.CharField(max_length=120)
    base_url = fields.CharField(max_length=255, null=True)
    key_ciphertext = fields.TextField()
    daily_limit = fields.IntField(default=20)
    updated_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='ai_provider_settings_updated', null=True, on_delete=fields.SET_NULL,
    )
    updated_by_id: Optional[int]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'ai_provider_setting'
