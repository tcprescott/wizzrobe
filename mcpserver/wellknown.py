"""RFC 9728 protected-resource metadata and the Bearer challenge.

Registered on the *outer* FastAPI app rather than through the SDK's
``create_protected_resource_routes``: that helper mounts the route inside the
MCPServer sub-app, which would serve the document at ``/mcp/.well-known/...``.
RFC 9728 §3.1 requires it at the origin root, and a client that cannot find it
there has no way to discover the authorization server.

Both the suffixed (``/.well-known/oauth-protected-resource/mcp``) and bare forms
are served. The suffixed form is what the spec derives from a resource URL with
a path; the bare one is what several clients request first.
"""

from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from application.services.mcp_auth_service import READ_SCOPE, WRITE_SCOPE
from application.utils.environment import get_base_url

SCOPES_SUPPORTED = [READ_SCOPE, WRITE_SCOPE]

_METADATA_PATHS = (
    '/.well-known/oauth-protected-resource/mcp',
    '/.well-known/oauth-protected-resource',
)


def resource_url() -> str:
    """The canonical resource identifier clients bind their tokens to."""
    return f'{get_base_url()}/mcp'


def metadata_url() -> str:
    return f'{get_base_url()}{_METADATA_PATHS[0]}'


def protected_resource_metadata() -> Dict[str, Any]:
    """The RFC 9728 document.

    ``authorization_servers`` points back at this same origin — the MCP server
    is its own authorization server (see ``mcpserver/oauth/``), which is the
    simple same-origin case: clients discover the AS metadata at
    ``/.well-known/oauth-authorization-server`` on this host.
    """
    base = get_base_url()
    return {
        'resource': resource_url(),
        'authorization_servers': [base],
        'bearer_methods_supported': ['header'],
        'scopes_supported': SCOPES_SUPPORTED,
        'resource_name': 'Wizzrobe',
        'resource_documentation': f'{base}/api/docs',
    }


def www_authenticate() -> str:
    """The ``WWW-Authenticate`` value for every 401 the MCP endpoint returns.

    Carrying ``resource_metadata`` is what turns a rejection into a recovery: a
    compliant client reads it, discovers the authorization server, and starts
    the OAuth flow instead of simply failing. This is also why a personal access
    token presented here is answered with 401 rather than 403 — 403 would be
    accurate but would leave the client with nowhere to go.
    """
    return f'Bearer resource_metadata="{metadata_url()}"'


def register_wellknown(app: FastAPI) -> None:
    """Register the protected-resource metadata routes at the origin root."""

    async def _metadata() -> JSONResponse:
        return JSONResponse(protected_resource_metadata())

    for path in _METADATA_PATHS:
        app.add_api_route(
            path, _metadata, methods=['GET'], include_in_schema=False,
        )
