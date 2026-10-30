"""Persistence for the MCP server's OAuth 2.1 authorization server.

Both models are **global** — no ``tenant`` foreign key. An OAuth grant is
platform-wide: it authenticates a :class:`~models.User`, and the community a
given MCP tool call operates in is named by the call itself, not baked into the
credential. The access token the flow ultimately issues is an
:class:`~models.ApiToken` row with ``origin='oauth'`` and ``tenant=NULL``.
"""

from tortoise import fields
from tortoise.models import Model


class McpOAuthClient(Model):
    """An MCP client registered through RFC 7591 dynamic client registration.

    Clients are **public** — no secret is stored, because Claude Desktop and
    Claude Code are native applications that cannot keep one. PKCE (S256) is
    what actually binds an authorization code to the client that requested it,
    so a stolen code is useless without the verifier.

    Registration is open by design: that is what lets a user paste the server
    URL into a client and have it self-configure. Nothing is granted by
    registering — every code still requires an interactive, authenticated
    approval at the consent screen.
    """

    id = fields.IntField(pk=True)
    client_id = fields.CharField(max_length=64, unique=True, index=True)
    client_name = fields.CharField(max_length=255)
    redirect_uris = fields.JSONField(default=list)
    grant_types = fields.JSONField(default=list)
    response_types = fields.JSONField(default=list)
    token_endpoint_auth_method = fields.CharField(max_length=32, default='none')
    scope = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    tokens = fields.ReverseRelation['ApiToken']

    class Meta:
        table = 'mcpoauthclient'


class McpAuthorizationCode(Model):
    """A single-use authorization code awaiting exchange at the token endpoint.

    Only the hash is stored, exactly like an access token — the plaintext lives
    only in the redirect back to the client. ``consumed_at`` makes the exchange
    single-use: a replayed code is refused even inside its short expiry window.
    """

    id = fields.IntField(pk=True)
    code_hash = fields.CharField(max_length=64, unique=True, index=True)
    client = fields.ForeignKeyField(
        'models.McpOAuthClient', related_name='authorization_codes', on_delete=fields.CASCADE
    )
    user = fields.ForeignKeyField('models.User', related_name='mcp_authorization_codes')
    redirect_uri = fields.CharField(max_length=2048)
    redirect_uri_provided_explicitly = fields.BooleanField(default=True)
    code_challenge = fields.CharField(max_length=128)
    code_challenge_method = fields.CharField(max_length=8, default='S256')
    scope = fields.CharField(max_length=255, null=True)
    resource = fields.CharField(max_length=2048, null=True)
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'mcpauthorizationcode'
