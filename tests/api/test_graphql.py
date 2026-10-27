"""The GraphQL endpoint: REST's door, community walls, the limits, the contract."""

from pathlib import Path

import pytest
from graphql import parse, validate

from api.graphql import schema
from api.graphql.limits import MAX_ROWS, RowEstimateRule
from application.services import OAuthAppService
from application.services.telemetry_service import TelemetryEventType
from application.tenant_context import tenant_scope
from models import ApiToken, Event, EventStatus, OAuthClientType, Role, TelemetryEvent, Tenant
from tests.api_helpers import client_for, create_user_token

SCHEMA_FILE = Path(__file__).resolve().parents[2] / 'docs' / 'reference' / 'schema.graphql'


async def _query(client, query: str, variables=None):
    return await client.post('/api/graphql', json={'query': query, 'variables': variables})


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


async def test_no_token_is_a_401(app, db):
    async with client_for(app) as client:
        resp = await _query(client, '{ viewer { isStaff } }')
    assert resp.status_code == 401


async def test_viewer_is_the_tokens_user(app, db):
    user, raw = await create_user_token(username='runner', roles=[Role.PROCTOR])
    async with client_for(app, raw) as client:
        resp = await _query(client, '{ viewer { user { id username discordId } roles isStaff scopes } }')
    assert resp.status_code == 200, resp.text
    viewer = resp.json()['data']['viewer']
    assert viewer['user'] == {'id': user.id, 'username': 'runner', 'discordId': str(user.discord_id)}
    assert viewer['roles'] == ['PROCTOR']
    assert viewer['isStaff'] is False
    assert viewer['scopes'] is None


async def test_a_read_only_token_can_query(app, db):
    _, raw = await create_user_token(read_only=True)
    async with client_for(app, raw) as client:
        resp = await _query(client, '{ viewer { isStaff } }')
    assert resp.status_code == 200, resp.text


async def _app_token(scope):
    user, raw = await create_user_token(username='app-user')
    app_row, _ = await OAuthAppService().create_app(
        user, name='Overlay', client_type=OAuthClientType.CONFIDENTIAL,
        redirect_uris=['https://app.example.com/callback'], allowed_scopes=['schedule:read', 'profile'],
    )
    await ApiToken.filter(user=user).update(origin='app', scope=scope, oauth_app_id=app_row.id)
    return raw


async def test_an_app_needs_schedule_read(app, db):
    refused = await _app_token('profile')
    async with client_for(app, refused) as client:
        resp = await _query(client, '{ viewer { isStaff } }')
    assert resp.status_code == 403
    assert 'schedule:read' in resp.json()['detail']


async def test_an_app_with_schedule_read_sees_its_scopes(app, db):
    raw = await _app_token('schedule:read')
    async with client_for(app, raw) as client:
        resp = await _query(client, '{ viewer { scopes } }')
    assert resp.status_code == 200, resp.text
    assert resp.json()['data']['viewer']['scopes'] == ['schedule:read']


async def test_mcp_tokens_are_refused(app, db):
    _, raw = await create_user_token()
    await ApiToken.all().update(origin='oauth', tenant_id=None)
    async with client_for(app, raw) as client:
        resp = await _query(client, '{ viewer { isStaff } }')
    assert resp.status_code == 401


@pytest.mark.parametrize('body', ['[{"query": "{ viewer { isStaff } }"}]', '"hi"', 'not json', '{}'])
async def test_batches_and_junk_are_refused(app, db, body):
    _, raw = await create_user_token()
    async with client_for(app, raw) as client:
        resp = await client.post('/api/graphql', content=body, headers={'content-type': 'application/json'})
    assert resp.status_code == 400
    assert resp.json()['errors'][0]['extensions']['code'] == 'BAD_REQUEST'


async def test_graphiql_is_a_static_page(app, db):
    async with client_for(app) as client:
        resp = await client.get('/api/graphql')
    assert resp.status_code == 200
    assert 'graphiql' in resp.text.lower()


# ---------------------------------------------------------------------------
# Events and the community wall
# ---------------------------------------------------------------------------


@pytest.fixture
async def events(db):
    live = await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE, is_default=False)
    draft = await Event.create(tenant_id=1, name='SGL 2028', slug='sgl28', status=EventStatus.DRAFT, is_default=False)
    other = await Tenant.create(slug='elsewhere', name='Elsewhere')
    theirs = await Event.create(tenant_id=other.id, name='Theirs', slug='theirs', status=EventStatus.LIVE)
    return {'live': live, 'draft': draft, 'other': other, 'theirs': theirs}


async def test_events_hide_drafts_from_players(app, events):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as client:
        listed = (await _query(client, '{ events { slug } }')).json()['data']['events']
        draft = (await _query(client, '{ event(slug: "sgl28") { slug } }')).json()
    assert {e['slug'] for e in listed} == {'default', 'sgl27'}
    assert draft['data'] == {'event': None}
    assert draft['errors'][0]['extensions']['code'] == 'NOT_FOUND'
    assert draft['errors'][0]['path'] == ['event']


async def test_staff_see_drafts(app, events):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, raw) as client:
        listed = (await _query(client, '{ events { slug status } }')).json()['data']['events']
        draft = (await _query(client, '{ event(slug: "sgl28") { name status } }')).json()['data']
    assert {'slug': 'sgl28', 'status': 'DRAFT'} in listed
    assert draft == {'event': {'name': 'SGL 2028', 'status': 'DRAFT'}}


async def test_no_slug_is_the_default_event(app, events):
    _, raw = await create_user_token()
    async with client_for(app, raw) as client:
        resp = (await _query(client, '{ event { slug isDefault } }')).json()
    assert resp['data'] == {'event': {'slug': 'default', 'isDefault': True}}


async def test_another_communitys_event_is_not_found(app, events):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, raw) as client:
        named = (await _query(client, '{ event(slug: "theirs") { id } }')).json()
        listed = (await _query(client, '{ events { id } }')).json()['data']['events']
    assert named['errors'][0]['extensions']['code'] == 'NOT_FOUND'
    assert events['theirs'].id not in {e['id'] for e in listed}


async def test_a_token_sees_only_its_own_community(app, events):
    with tenant_scope(events['other'].id):
        _, raw = await create_user_token(username='outsider', roles=[Role.STAFF])
    async with client_for(app, raw) as client:
        listed = (await _query(client, '{ events { slug } }')).json()['data']['events']
        ours = (await _query(client, '{ event(slug: "sgl27") { id } }')).json()
    assert {e['slug'] for e in listed} == {'elsewhere', 'theirs'}
    assert ours['errors'][0]['extensions']['code'] == 'NOT_FOUND'


# ---------------------------------------------------------------------------
# Limits and errors
# ---------------------------------------------------------------------------


async def test_unknown_fields_fail_validation_with_a_400(app, db):
    _, raw = await create_user_token()
    async with client_for(app, raw) as client:
        resp = await _query(client, '{ viewer { password } }')
    assert resp.status_code == 400
    assert resp.json()['data'] is None
    assert resp.json()['errors'][0]['extensions']['code'] == 'GRAPHQL_VALIDATION_FAILED'


async def test_too_many_aliases_are_refused(app, db):
    _, raw = await create_user_token()
    query = '{ ' + ' '.join(f'a{i}: viewer {{ isStaff }}' for i in range(40)) + ' }'
    async with client_for(app, raw) as client:
        resp = await _query(client, query)
    assert resp.status_code == 400


async def test_a_huge_body_is_refused_before_parsing(app, db):
    _, raw = await create_user_token()
    async with client_for(app, raw) as client:
        resp = await _query(client, '{ viewer { isStaff } }' + ' ' * 20_000)
    assert resp.status_code == 413


def test_the_row_estimate_multiplies_page_sizes_down_a_path():
    from graphql import build_schema

    toy = build_schema('''
        type Query { matches(first: Int): [Match!]! }
        type Match { id: Int! players(first: Int): [Player!]! }
        type Player { id: Int! }
    ''')
    small = parse('{ matches(first: 10) { id players(first: 5) { id } } }')
    big = parse('{ matches(first: 100) { players(first: 100) { id } } }')
    spread = parse('{ matches(first: 100) { ...P } } fragment P on Match { players(first: 100) { id } }')
    assert validate(toy, small, [RowEstimateRule]) == []
    for document in (big, spread):
        errors = validate(toy, document, [RowEstimateRule])
        assert errors and errors[0].extensions['code'] == 'QUERY_TOO_COMPLEX'
        assert str(MAX_ROWS) in errors[0].message


# ---------------------------------------------------------------------------
# Telemetry and the contract
# ---------------------------------------------------------------------------


async def test_each_operation_is_one_telemetry_row(app, db, monkeypatch):
    monkeypatch.setattr('application.services.telemetry_service.telemetry_enabled', lambda: True)
    user, raw = await create_user_token(username='counted')
    async with client_for(app, raw) as client:
        await client.post('/api/graphql', json={
            'query': 'query Overlay { viewer { isStaff } events { slug } }', 'operationName': 'Overlay',
        })
    rows = await TelemetryEvent.filter(event_type=TelemetryEventType.GRAPHQL_QUERY)
    assert [(r.target, r.user_id) for r in rows] == [('graphql events viewer', user.id)]


def test_the_committed_schema_matches_the_code():
    assert SCHEMA_FILE.read_text().strip() == schema.as_str().strip(), (
        'The GraphQL schema changed. Regenerate the committed copy with\n'
        '  poetry run python -m api.graphql.export > docs/reference/schema.graphql\n'
        'and check the diff: removing or renaming a field breaks clients, so deprecate instead.'
    )
