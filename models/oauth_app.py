"""Persistence for the third-party app authorization server (``/oauth``).

Separate from the MCP authorization server (:mod:`models.mcp`): an MCP grant is
platform-wide and dynamically registered, while an app here is registered by a
person, may hold a secret, and every grant it receives is bound to the one
community the approving person picked (except a first-party app's, which
names none). The grant itself is an
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
    client_secret_hash = fields.CharField(max_length=64, null=True)
    secret_prefix = fields.CharField(max_length=24, null=True)
    previous_secret_hash = fields.CharField(max_length=64, null=True)
    secret_rotated_at = fields.DatetimeField(null=True)
    redirect_uris = fields.JSONField(default=list)  # type: ignore[var-annotated]
    allowed_scopes = fields.JSONField(default=list)  # type: ignore[var-annotated]
    disabled_at = fields.DatetimeField(null=True)
    disabled_reason = fields.CharField(max_length=300, null=True)
    # Wizzrobe's own app (web/): no consent screen, and its grant names no
    # community, so each REST call picks one (X-Wizzrobe-Community). Set only
    # by scripts/register_first_party_app.py, never from a page.
    first_party = fields.BooleanField(default=False)
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
    # NULL for a first-party app's code: its grant isn't tied to a community.
    tenant: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='oauth_app_codes', on_delete=fields.CASCADE, null=True,
    )
    tenant_id: Optional[int]
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


class OAuthAppBlock(Model):
    """One community's refusal of one app.

    Staff block an app from Admin → Settings → Connected apps: its grants in
    this community are revoked, the community stops appearing on that app's
    consent screen, and new codes and refreshes for it are refused here. Other
    communities are untouched; a platform admin's
    :attr:`OAuthApp.disabled_at` is the everywhere switch.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='oauth_app_blocks', on_delete=fields.CASCADE,
    )
    tenant_id: int
    app: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.OAuthApp', related_name='blocks', on_delete=fields.CASCADE,
    )
    app_id: int
    blocked_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='oauth_app_blocks', on_delete=fields.SET_NULL, null=True,
    )
    blocked_by_id: Optional[int]
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'oauthappblock'
        unique_together = (('tenant', 'app'),)
