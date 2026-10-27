"""``POST /api/graphql``, and GraphiQL at ``GET /api/graphql``.

A plain FastAPI endpoint rather than Strawberry's ``GraphQLRouter``, so the
door is REST's door: ``resolve_token`` authenticates, binds the community and
checks an app grant's scope, the API router's rate limiter counts the request,
and nothing here opens a WebSocket, takes a query over GET or accepts a batch.
"""

import asyncio
import json
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from graphql import FieldNode, OperationDefinitionNode, parse
from strawberry.http.ides import get_graphql_ide_html

from api.dependencies import resolve_token
from api.graphql.context import GraphContext
from api.graphql.errors import format_errors
from api.graphql.limits import MAX_BODY_BYTES, MAX_TOKENS, TIMEOUT_SECONDS
from api.graphql.schema import schema
from api.schemas.graphql import GraphQLRequest, GraphQLResponse
from application.services.telemetry_service import TelemetryEventType, TelemetryService
from application.tenant_context import tenant_scope
from models import ApiToken, User

router = APIRouter(tags=['GraphQL'])

PATH = '/graphql'
_ROOT = schema._schema.query_type
assert _ROOT is not None
ROOT_FIELDS = frozenset(_ROOT.fields)


def _refuse(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse({'errors': [{'message': message, 'extensions': {'code': code}}]}, status_code=status)


def _top_level_fields(query: str, operation_name: Optional[str]) -> str:
    """The operation's root fields, for telemetry. Names come from the schema,
    never the client, so one client can't fan a report out into junk rows."""
    try:
        document = parse(query, max_tokens=MAX_TOKENS)
    except Exception:
        return '?'
    names: set[str] = set()
    for definition in document.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue
        if operation_name is not None and (definition.name is None or definition.name.value != operation_name):
            continue
        names.update(s.name.value for s in definition.selection_set.selections if isinstance(s, FieldNode))
    return ' '.join(sorted(names & ROOT_FIELDS)) or '?'


async def _record(user: User, token: ApiToken, target: str, outcome: str) -> None:
    with tenant_scope(token.tenant_id):
        await TelemetryService().track_api_call(
            event_type=TelemetryEventType.GRAPHQL_QUERY,
            target=target,
            path=f'/api{PATH}',
            user_id=user.id,
            username=user.username,
            outcome=outcome,
        )


@router.post(
    PATH,
    response_model=GraphQLResponse,
    openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {
        'schema': GraphQLRequest.model_json_schema(),
    }}}},
    summary='Run a GraphQL query',
    description=(
        'Read the community\'s events, schedule and brackets as one graph. Send '
        '`{"query": ..., "variables": ..., "operationName": ...}`; the schema is at '
        '`docs/reference/schema.graphql` and GraphiQL is at `GET /api/graphql`. Queries only: '
        'writes stay on the REST endpoints.\n\n**Who can call this:** Any member. Fields that show '
        'more than a member sees check your role, and an app\'s grant, field by field.'
    ),
)
async def run_query(request: Request, resolved: Tuple[User, ApiToken] = Depends(resolve_token)):
    user, token = resolved
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _refuse(413, f'A query can be at most {MAX_BODY_BYTES // 1024} KB.', 'QUERY_TOO_LARGE')
    try:
        payload: Any = json.loads(body or b'null')
    except ValueError:
        return _refuse(400, 'Send the query as a JSON object.', 'BAD_REQUEST')
    if not isinstance(payload, dict):
        return _refuse(400, 'Send one operation as a JSON object; batches are not accepted.', 'BAD_REQUEST')
    query = payload.get('query')
    variables = payload.get('variables')
    operation_name = payload.get('operationName')
    if not isinstance(query, str) or not query.strip():
        return _refuse(400, 'The body needs a "query" string.', 'BAD_REQUEST')
    if variables is not None and not isinstance(variables, dict):
        return _refuse(400, '"variables" must be an object.', 'BAD_REQUEST')
    if operation_name is not None and not isinstance(operation_name, str):
        return _refuse(400, '"operationName" must be a string.', 'BAD_REQUEST')

    target = f'graphql {_top_level_fields(query, operation_name)}'
    try:
        result = await asyncio.wait_for(
            schema.execute(
                query,
                variable_values=variables,
                context_value=GraphContext(actor=user, token=token),
                operation_name=operation_name,
            ),
            timeout=TIMEOUT_SECONDS,
        )
    except TimeoutError:
        await _record(user, token, target, 'TIMEOUT')
        return _refuse(200, f'This query took longer than {int(TIMEOUT_SECONDS)} seconds and was stopped.', 'TIMEOUT')

    out: Dict[str, Any] = {'data': result.data}
    if result.errors:
        out['errors'] = format_errors(result.errors)
    outcome = out['errors'][0]['extensions']['code'] if result.errors else 'ok'
    await _record(user, token, target, outcome)
    return JSONResponse(out, status_code=200 if result.data is not None else 400)


@router.get(PATH, include_in_schema=False)
async def graphiql() -> HTMLResponse:
    # The page is static and carries no data: its queries go back to
    # POST /api/graphql with whatever token the viewer pastes into Headers.
    return HTMLResponse(get_graphql_ide_html('graphiql'))
