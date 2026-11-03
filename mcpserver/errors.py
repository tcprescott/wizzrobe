"""Service-layer exception → MCP tool error translation.

The MCP analogue of ``api/dependencies.py::ServiceErrorRoute``. It has to live
inside the per-tool wrapper rather than at a single central seam, because the
SDK's ``Tool.run`` catches *every* exception and re-raises it as a bare
``ToolError`` — the type is gone by the time anything above the tool sees it.
So the mapping runs before the exception escapes the tool.

Errors reach the model as ``isError: true`` tool results (not JSON-RPC protocol
errors), which is what MCP intends for domain failures: the model reads them and
can recover. Only transport and authentication failures are HTTP-level.
"""

import logging
from typing import Optional

from mcp.server.mcpserver.exceptions import ToolError

from application.errors import EventArchivedError, NotFoundError

logger = logging.getLogger(__name__)

FORBIDDEN = 'forbidden'
NOT_FOUND = 'not_found'
INVALID_REQUEST = 'invalid_request'
READ_ONLY = 'read_only'
INTERNAL_ERROR = 'internal_error'


def map_service_error(exc: Exception, tool: Optional[str] = None) -> ToolError:
    """Translate a service-layer exception into a ``ToolError``.

    Ordering mirrors ``ServiceErrorRoute`` exactly, including the subtlety that
    ``NotFoundError`` must be tested before ``ValueError`` — it subclasses it.
    ``FeatureDisabledError`` is a ``NotFoundError``, so a feature the community
    has not enabled reports as ``not_found``, matching the 404 the REST
    ``require_feature`` gate returns.
    """
    if isinstance(exc, PermissionError):
        return ToolError(f'{FORBIDDEN}: {exc or "Permission denied"}')
    if isinstance(exc, NotFoundError):
        return ToolError(f'{NOT_FOUND}: {exc or "Not found"}')
    if isinstance(exc, EventArchivedError):
        return ToolError(f'{READ_ONLY}: {exc}')
    if isinstance(exc, ValueError):
        return ToolError(f'{INVALID_REQUEST}: {exc}')
    # Log the traceback but hand the model an opaque message, like the 500 page.
    logger.exception('Unhandled exception in MCP tool %s', tool or '(unknown)', exc_info=exc)
    return ToolError(f'{INTERNAL_ERROR}: The server hit an unexpected error.')
