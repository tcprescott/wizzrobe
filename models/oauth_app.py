"""Persistence for the third-party app authorization server (``/oauth``).

Separate from the MCP authorization server (:mod:`models.mcp`): an MCP grant is
platform-wide and dynamically registered, while an app here is registered by a
person, may hold a secret, and every grant it receives is bound to the one
community the approving person picked. The grant itself is an
:class:`~models.ApiToken` with ``origin='app'``.
"""

from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import OAuthClientType


class OAuthApp(Model):
    """A third-party app a Wizzrobe user registered.

    **Global** (no ``tenant``), like the :class:`~models.User` who owns it: an
    app is offered to people in any community that has the
    ``third_party_apps`` flag on, and each grant names its own community.

    ``name``, ``description`` and ``homepage_url`` are player-written and are
    plain text everywhere they are shown — never markdown, never HTML.
    """

    id = fields.IntField(pk=True)
    owner: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='oauth_apps', on_delete=fields.CASCADE,
    )
    owner_id: int
    name = fields.CharField(max_length=60)
    description = fields.CharField(max_length=300, default='')
    homepage_url = fields.CharField(max_length=512, null=True)
    client_type = fields.CharEnumField(OAuthClientType, max_length=16)
    client_id = fields.CharField(max_length=64, unique=True, index=True)
    # Confidential apps only. SHA-256 like every other credential here; the
    # plaintext is shown once. The previous hash stays valid for a grace period
    # after a rotation so a deployed app can pick up the new secret.
    client_secret_hash = fields.CharField(max_length=64, null=True)
    secret_prefix = fields.CharField(max_length=24, null=True)
    previous_secret_hash = fields.CharField(max_length=64, null=True)
    secret_rotated_at = fields.DatetimeField(null=True)
    # Exact-match allowlist, checked at authorize and again at the token endpoint.
    # mypy cannot infer a JSONField's type parameter, as with every sibling.
    redirect_uris = fields.JSONField(default=list)  # type: ignore[var-annotated]
    # ApiScope values the app may ask for. Asking is not getting: the person
    # approving decides what the grant carries.
    allowed_scopes = fields.JSONField(default=list)  # type: ignore[var-annotated]
    # Set by a platform admin (abuse); every grant stops working while set.
    disabled_at = fields.DatetimeField(null=True)
    disabled_reason = fields.CharField(max_length=300, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    codes = fields.ReverseRelation['OAuthAppCode']
    grants = fields.ReverseRelation['ApiToken']  # type: ignore[name-defined]

    class Meta:
        table = 'oauthapp'


class OAuthAppCode(Model):
    """A single-use authorization code awaiting exchange at ``/oauth/token``.

    Mirrors :class:`~models.McpAuthorizationCode`, plus the community and the
    scopes the person approved, which the issued grant inherits unchanged.
    Only the hash is stored. ``consumed_at`` makes it single-use; ``grant``
    records what it was exchanged for, so a replayed code can revoke that grant
    (RFC 6749 §4.1.2).
    """

    id = fields.IntField(pk=True)
    code_hash = fields.CharField(max_length=64, unique=True, index=True)
    app: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.OAuthApp', related_name='codes', on_delete=fields.CASCADE,
    )
    app_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='oauth_app_codes', on_delete=fields.CASCADE,
    )
    user_id: int
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='oauth_app_codes', on_delete=fields.CASCADE,
    )
    tenant_id: int
    redirect_uri = fields.CharField(max_length=2048)
    code_challenge = fields.CharField(max_length=128)
    scope = fields.CharField(max_length=255)
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    grant: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.ApiToken', related_name='oauth_app_codes', on_delete=fields.SET_NULL, null=True
    )
    grant_id: Optional[int]
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'oauthappcode'
