"""The OpenAPI document behind ``/api/docs``.

Holds the intro and getting-started recipes, the section (tag) order, and the
parts of each endpoint's documentation that follow mechanically from its
dependencies: whether it needs a token, whether a read-only token is refused,
which feature flag gates it, and which error responses it can return. Writing
those by hand on 268 routes is how they drifted, so they are derived here and
each route's own ``description=`` carries only what it does and who may call it.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from pydantic import BaseModel, Field

from api.dependencies import (
    require_staff_write,
    require_super_admin_write,
    require_write_actor,
    resolve_token,
)
from api.rate_limit import limit_per_minute
from api.scopes import PAT_ONLY, scope_for
from application.api_scopes import CONSENT_WORDING, ApiScope
from application.events import EventType
from application.feature_flags import spec_for
from application.services.webhook_service import WebhookService
from models import FeatureFlag

logger = logging.getLogger(__name__)

API_PREFIX = '/api'

WHO_CAN_CALL = '**Who can call this:**'

_API_DESCRIPTION = """
Scripts, bots and spreadsheets can do through this API what people do in the
Wizzrobe web app: read the schedule, sign up for crew, run matches, record
results, and manage a community's setup.

## Getting a token

Open your home page, go to the **Profile** tab and expand **API tokens & AI
clients**, then choose **Generate token**. The token is shown once, so copy it
straight away. Send it on every request:

    Authorization: Bearer wizzrobe_pat_xxxxxxxx...

Click **Authorize** above and paste it to try endpoints from this page.

- **A token belongs to one community.** You pick the community when you make
  it, and every call works inside that community only. To work with two
  communities, make two tokens.
- **A token acts as you.** It can do exactly what you can do in the web app,
  no more. Each endpoint says who can call it.
- **Read-only tokens** can call `GET` endpoints only. Anything that changes
  data answers `403`.
- Tokens can expire, and you can revoke one at any time from the same card.

A personal token is for your own scripts. An app someone else wrote signs you
in with OAuth instead, and gets only the **scopes** you approve. Each endpoint
lists the scope an app needs to call it; a few say apps can't call them at all.

## Conventions

- **Times are UTC**, in ISO 8601 (`2026-10-16T18:00:00Z`). Convert to local
  time before showing them to people. Reports are the exception: their times
  are on the community's own clock, with the UTC offset included.
- **Errors** come back as JSON with a `detail` message:
  `{"detail": "Staff access required"}`. `400` means a rule refused the
  request (the message says which), `401` a missing or expired token, `403` no
  permission, a read-only token or a scope the app wasn't given, `404` nothing with that id in your
  community or a feature your community hasn't turned on, `422` a malformed
  request.
- **Rate limit:** {rate_limit} requests a minute per token, and the same
  across everything coming from one network address. Over that you get `429`
  with a `Retry-After` header giving the seconds to wait.
- Some sections only work when the community has turned the feature on
  (Admin → Features). Those endpoints say so.

## Recipes

Set two shell variables first. `WIZZROBE` is the address you open Wizzrobe at.

    export WIZZROBE=https://onsite.speedgaming.org
    export TOKEN=wizzrobe_pat_xxxxxxxx...

**Check your token works**, and see who it acts as:

    curl -H "Authorization: Bearer $TOKEN" "$WIZZROBE/api/users/me"

**Today's matches on one stage.** Find the stage id with `GET /api/stages`,
then filter by it and a time window (UTC):

    curl -H "Authorization: Bearer $TOKEN" \\
      "$WIZZROBE/api/matches?stage_id=3&start_date=2026-10-16T00:00:00Z&end_date=2026-10-17T00:00:00Z"

**Sign up to commentate a match.** A crew coordinator still has to approve you:

    curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
      -d '{"role": "commentator"}' "$WIZZROBE/api/matches/812/crew"

**Record the winner of a match.** `winner_id` is the `id` of the winning entry
in the match's `players` list, not the person's user id:

    curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
      -d '{"winner_id": 1534}' "$WIZZROBE/api/matches/812/result"

**Receive events instead of polling.** Staff can point a webhook at your
server (Admin → Settings → Integrations → Webhooks). What it sends, and how to
check the signature, is under **Webhooks** at the bottom of this page.
"""



def api_description() -> str:
    return _API_DESCRIPTION.replace('{rate_limit}', str(limit_per_minute()))


# Swagger lists sections in this order. ReDoc also reads ``x-tagGroups``.
TAG_GROUPS: List[tuple[str, List[tuple[str, str]]]] = [
    ('Event day', [
        ('Matches', 'The schedule, and running a match from seating to a confirmed result.'),
        ('Crew', 'Commentator and tracker signups, approval and acknowledgement.'),
        ('Reschedule requests', 'Players asking to move or call off a match, and staff answering.'),
        ('Volunteers', 'Volunteer positions, shifts, assignments, availability and hours.'),
        ('Check-in', 'The event check-in desk: roster, check-in, walk-ups and account matching.'),
        ('Equipment', 'Lending equipment out and getting it back.'),
        ('Stages', 'Stages (streams and setups) that matches are assigned to.'),
        ('Triforce texts', 'Player-submitted triforce texts and their moderation.'),
        ('Feedback', 'Feedback people send from inside the app.'),
    ]),
    ('People', [
        ('Users', 'People in this community, their profiles and roles.'),
        ('Membership', 'Requests to join the community, and answering them.'),
        ('Player availability', 'When players say they can and can\'t play.'),
        ('Notifications', 'Which messages reach you and whether they ping, and the tournaments you follow.'),
        ('API tokens', 'Your personal API tokens.'),
    ]),
    ('Setup', [
        ('Tournaments', 'Tournaments, their admins and crew coordinators, and signing up.'),
        ('Payouts', 'Prize pools and payouts per tournament.'),
        ('Webhooks', 'Outbound webhooks that POST to your server when events happen.'),
        ('Discord role mappings', 'Discord roles granted to people with a given Wizzrobe role.'),
        ('System config', 'Community settings stored as key/value pairs.'),
        ('Notification settings', 'Which DMs the community sends, to whom, and whether they ping.'),
    ]),
    ('Reports', [
        ('Reports', 'Operational reports: stage use, crew coverage, tournament health and trends.'),
        ('Telemetry', 'Which parts of the app people use.'),
        ('Audit', 'The log of who changed what.'),
        ('Sent messages', 'Every DM sent in the community, and whether it got there.'),
        ('Exports', 'Tournament and volunteer data exports, built in the background.'),
    ]),
    ('Online tournaments', [
        ('Brackets', 'Brackets run inside Wizzrobe: entrants, matches, results and standings.'),
        ('Async qualifiers', 'Asynchronous qualifiers: pools, seeds, runs, review and leaderboards.'),
        ('Async qualifier live races', 'Live races that count toward an async qualifier.'),
        ('Presets', 'Randomizer seed presets.'),
        ('Seeds', 'Rolling randomizer seeds.'),
        ('Race rooms', 'racetime.gg race rooms opened for matches.'),
        ('Race room profiles', 'Saved racetime.gg room settings.'),
        ('SpeedGaming', 'Importing a SpeedGaming schedule.'),
        ('Discord events', 'Discord scheduled events mirrored from matches.'),
    ]),
    ('Platform', [
        ('Communities', 'Every community on the platform, its first Staff and which features it may use.'),
        ('Platform users', 'Every account on the platform, and switching one off.'),
        ('Feature groups', 'Tiers that decide which features a community may use.'),
        ('Log levels', 'How much each part of the server writes to its log.'),
        ('Racetime bots', 'racetime.gg bot accounts shared across communities.'),
        ('Service health', 'Whether the outside services Wizzrobe depends on are up.'),
        ('Health', 'Liveness check for the server itself.'),
        ('Web push', 'Browser push subscription upkeep, called by the app\'s service worker.'),
    ]),
]

# Router tags written before there was a naming rule; renamed in the document
# rather than in the routers so the five per-area documentation PRs don't all
# touch the same ``APIRouter(...)`` lines.
TAG_RENAMES: Dict[str, str] = {
    'health': 'Health',
    'Triforce Texts': 'Triforce texts',
    'Player Availability': 'Player availability',
    'System Config': 'System config',
    'API Tokens': 'API tokens',
    'Discord Role Mappings': 'Discord role mappings',
    'Web Push': 'Web push',
}


class ErrorResponse(BaseModel):
    detail: str = Field(..., description='What went wrong, in words you can show a person.')


class WebhookDelivery(BaseModel):
    """The JSON body Wizzrobe POSTs to your webhook URL. Keys arrive sorted."""

    event_type: str = Field(..., description='Which event happened, such as `match.finished`.')
    occurred_at: str = Field(..., description='When it happened: ISO 8601, UTC.')
    tenant_id: Optional[int] = Field(
        None, description='The community it happened in. A webhook only ever receives its own community\'s events.'
    )
    actor_id: Optional[int] = Field(
        None, description='User id of the person whose action caused it; null for automatic events.'
    )
    actor_username: Optional[str] = Field(None, description='That person\'s username; null when actor_id is.')
    data: Dict[str, Any] = Field(
        ..., description='Ids of what changed (match, tournament, user and so on), varying by event. '
                         'Fetch the full records from the API with them.'
    )


_WRITE_GATES: Set[Callable] = {require_write_actor, require_staff_write, require_super_admin_write}

_ERRORS = {
    '400': 'A rule refused the request. `detail` says which.',
    '401': 'No token, or the token is unknown, expired or revoked.',
    '403': 'You don\'t have permission, the token is read-only, an app\'s token lacks the scope, '
           'or your account or community is inactive.',
    '404': 'Nothing with that id in your community, or the feature is turned off for your community.',
    '429': 'Over the rate limit. Wait the number of seconds in the `Retry-After` header.',
}


def _calls(dependant: Dependant) -> Iterable[Callable]:
    for dep in dependant.dependencies:
        if dep.call is not None:
            yield dep.call
        yield from _calls(dep)


def _feature_flag(calls: Iterable[Callable]) -> Optional[FeatureFlag]:
    for call in calls:
        flag = getattr(call, 'feature_flag', None)
        if flag is not None:
            return flag
    return None


OAUTH_SCHEME = 'WizzrobeApps'


def _scope(route: RouteContext, method: str):
    return scope_for(method.upper(), (route.path_format or '').removeprefix(API_PREFIX))


def _access_notes(route: RouteContext, method: str) -> tuple[List[str], bool]:
    """Notes derived from the route's dependencies, and whether it authenticates."""
    calls = list(_calls(route.dependant))
    authenticated = resolve_token in calls
    notes: List[str] = []
    if not authenticated:
        notes.append('**Token:** not needed.')
    elif any(call in _WRITE_GATES for call in calls):
        notes.append('**Token:** needs a token that isn\'t read-only.')
    scope = _scope(route, method)
    if scope is PAT_ONLY:
        notes.append('**Apps:** can\'t call this; it needs a personal API token.')
    elif isinstance(scope, ApiScope):
        notes.append(f'**App scope:** `{scope.value}`')
    flag = _feature_flag(calls)
    if flag is not None:
        notes.append(
            f'**Feature:** only works when the community has **{spec_for(flag).label}** turned on; '
            'otherwise every call answers `404`.'
        )
    return notes, authenticated


def _error_ref() -> Dict[str, Any]:
    return {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}


def _document_operation(operation: Dict[str, Any], route: RouteContext, method: str) -> None:
    notes, authenticated = _access_notes(route, method)
    if notes:
        description = (operation.get('description') or '').rstrip()
        operation['description'] = '\n\n'.join(filter(None, [description, '  \n'.join(notes)]))
    operation['tags'] = [TAG_RENAMES.get(tag, tag) for tag in operation.get('tags', [])]
    responses = operation.setdefault('responses', {})
    codes = ['429']
    if authenticated:
        codes += ['401', '403', '404']
    elif route.path_format != f'{API_PREFIX}/health':
        codes.append('404')
    if method != 'get':
        codes.append('400')
    for code in sorted(codes):
        responses.setdefault(code, {'description': _ERRORS[code], 'content': _error_ref()})
    scope = _scope(route, method)
    if operation.get('security') and isinstance(scope, ApiScope):
        operation['security'] = [*operation['security'], {OAUTH_SCHEME: [scope.value]}]


def _oauth_scheme() -> Dict[str, Any]:
    """Apps sign in at the ``/oauth`` issuer and are held to these scopes."""
    return {
        'type': 'oauth2',
        'description': (
            'For apps acting on someone\'s behalf. Authorization code flow with PKCE (S256); '
            'the person picks one community and approves the scopes.'
        ),
        'flows': {
            'authorizationCode': {
                'authorizationUrl': '/oauth/authorize',
                'tokenUrl': '/oauth/token',
                'refreshUrl': '/oauth/token',
                'scopes': {scope.value: wording for scope, wording in CONSENT_WORDING.items()},
            },
        },
    }


def _webhook_paths() -> Dict[str, Any]:
    """One entry per event family under the document's ``webhooks`` section."""
    reference = WebhookService.format_reference()
    headers = [
        {
            'name': header['name'],
            'in': 'header',
            'required': True,
            'description': header['description'],
            'schema': {'type': 'string'},
        }
        for header in reference['headers']
    ]
    constants = reference['constants']
    delivery_note = (
        f"Answer with any `2xx` within {constants['timeout_seconds']:.0f} seconds. Anything else is retried, "
        f"up to {constants['max_attempts']} attempts in all, waiting longer each time. Redirects are not followed."
    )
    verify = (
        'Check the signature against the raw body bytes before trusting a delivery:\n\n'
        '    import hmac, hashlib\n'
        '    signed = f"{headers[\'X-Wizzrobe-Timestamp\']}.{raw_body}".encode()\n'
        '    expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()\n'
        '    assert hmac.compare_digest(expected, headers["X-Wizzrobe-Signature"])\n\n'
        'Reject deliveries whose timestamp is more than a few minutes old.'
    )
    families: Dict[str, List[str]] = {}
    for name in sorted(EventType.ALL):
        family = name.split('.', 1)[0]
        if family == 'service_health':
            continue  # platform-level, no community, so never delivered to a webhook
        families.setdefault(family, []).append(name)
    paths: Dict[str, Any] = {}
    for family, names in families.items():
        listing = '\n'.join(f'- `{name}`' for name in names)
        schema = {
            'allOf': [{'$ref': '#/components/schemas/WebhookDelivery'}],
            'properties': {'event_type': {'type': 'string', 'enum': names}},
        }
        content: Dict[str, Any] = {'schema': schema}
        if reference['example_payload']['event_type'] in names:
            content['example'] = reference['example_payload']
        paths[f'{family}.*'] = {
            'post': {
                'tags': ['Webhooks'],
                'summary': f'{family.replace("_", " ").capitalize()} events',
                'description': (
                    f'Sent to a webhook subscribed to any of these events, or to `{reference["wildcard"]}`:'
                    f'\n\n{listing}\n\n{delivery_note}\n\n{verify}'
                ),
                'parameters': headers,
                'requestBody': {'required': True, 'content': {'application/json': content}},
                'responses': {'200': {'description': 'Any 2xx tells Wizzrobe the delivery arrived.'}},
            }
        }
    return paths


def build_openapi(app: FastAPI) -> Dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    operations = schema.get('paths', {})
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        if not (route.path_format or '').startswith(f'{API_PREFIX}/'):
            continue
        for method in route.methods or ():
            operation = operations.get(route.path_format, {}).get(method.lower())
            if operation is not None:
                _document_operation(operation, route, method.lower())
    schema.setdefault('components', {}).setdefault('securitySchemes', {})[OAUTH_SCHEME] = _oauth_scheme()
    components = schema.setdefault('components', {}).setdefault('schemas', {})
    components['ErrorResponse'] = ErrorResponse.model_json_schema()
    components['WebhookDelivery'] = WebhookDelivery.model_json_schema()
    schema['tags'] = [
        {'name': name, 'description': description}
        for _, tags in TAG_GROUPS for name, description in tags
    ]
    schema['x-tagGroups'] = [
        {'name': group, 'tags': [name for name, _ in tags]} for group, tags in TAG_GROUPS
    ]
    schema['webhooks'] = _webhook_paths()
    app.openapi_schema = schema
    return schema


def install(app: FastAPI) -> None:
    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]


def warm_openapi(app: FastAPI) -> None:
    """Build the schema once at startup, off the event loop.

    Sentry SGLMAN-17: the first ``/api/openapi.json`` after a deploy built it on
    the loop and froze every page for over two seconds.
    """
    from nicegui import background_tasks

    async def _warm() -> None:
        try:
            await asyncio.to_thread(build_openapi, app)
        except Exception:
            logger.warning('Building the OpenAPI schema at startup failed', exc_info=True)

    background_tasks.create(_warm(), name='warm openapi schema')


__all__ = ['TAG_GROUPS', 'WHO_CAN_CALL', 'api_description', 'build_openapi', 'install', 'warm_openapi']
