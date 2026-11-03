"""Service exceptions as GraphQL error codes.

A resolver lets the service's exception propagate, as a REST handler does, and
the error is reported at the field's path while the rest of the query answers.
Anything the services don't raise on purpose is masked, so an internal message
never reaches a client, and logged for Sentry.
"""

import logging
from typing import Any, Dict, List

from graphql import GraphQLError

from application.errors import NotFoundError

logger = logging.getLogger(__name__)

INTERNAL_MESSAGE = 'Something went wrong answering this field.'


def error_code(error: GraphQLError) -> str:
    original = error.original_error
    if original is None:
        return (error.extensions or {}).get('code', 'GRAPHQL_VALIDATION_FAILED')
    if isinstance(original, NotFoundError):
        return 'NOT_FOUND'
    if isinstance(original, PermissionError):
        return 'FORBIDDEN'
    if isinstance(original, ValueError):
        return 'BAD_USER_INPUT'
    return 'INTERNAL'


def format_errors(errors: List[GraphQLError]) -> List[Dict[str, Any]]:
    out = []
    for error in errors:
        code = error_code(error)
        formatted: Dict[str, Any] = dict(error.formatted)
        if code == 'INTERNAL':
            logger.error('GraphQL resolver failed at %s', error.path, exc_info=error.original_error)
            formatted['message'] = INTERNAL_MESSAGE
        formatted['extensions'] = {**(formatted.get('extensions') or {}), 'code': code}
        out.append(formatted)
    return out
