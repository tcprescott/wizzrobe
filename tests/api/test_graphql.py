"""The GraphQL endpoint: REST's door, community walls, the limits, the contract."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from graphql import parse, validate

from api.graphql import schema
from api.graphql.limits import MAX_ROWS, RowEstimateRule
from application.event_context import event_scope
from application.services import OAuthAppService
from application.services.telemetry_service import TelemetryEventType
from application.tenant_context import tenant_scope
from models import (
    ApiToken,
    BracketState,
    Broadcaster,
    Commentator,
    Event,
    EventStatus,
    Match,
    MatchPlayers,
    OAuthClientType,
    Role,
    Stage,
    TelemetryEvent,
    Tenant,
    Tournament,
    User,
)
from tests.api_helpers import client_for, create_user_token
from tests.services._bracket_overview_setup import native_elim
from tests.test_query_budget import count_queries

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


# ---------------------------------------------------------------------------
# Tournaments, stages and matches
# ---------------------------------------------------------------------------

_ids = iter(range(10_000, 10**9))

T0 = datetime(2026, 10, 23, 14, tzinfo=timezone.utc)

RUN_SHEET = '''
query RunSheet($first: Int!, $after: String) {
  event {
    matches(first: $first, after: $after) {
      nodes {
        id scheduledAt
        tournament { name }
        stage { name }
        seed { url }
        players { user { username } station finishRank }
        crew { role user { username } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
'''


async def _schedule(n: int, *, tournament=None, stage=None):
    tournament = tournament or await Tournament.create(name='Cup', is_active=True)
    stage = stage or await Stage.create(name='Main')
    matches = []
    for i in range(n):
        m = await Match.create(tournament=tournament, stage=stage, scheduled_at=T0 + timedelta(minutes=30 * i))
        p = await User.create(discord_id=next(_ids), username=f'p{m.id}')
        await MatchPlayers.create(match=m, user=p)
        matches.append(m)
    return tournament, stage, matches


async def test_event_lists_tournaments_and_stages(app, db):
    _, raw = await create_user_token()
    await Tournament.create(name='Live Cup', is_active=True)
    await Tournament.create(name='Old Cup', is_active=False)
    await Stage.create(name='Main')
    async with client_for(app, raw) as client:
        data = (await _query(client, '{ event { tournaments { name } stages { name } } }')).json()['data']
    assert [t['name'] for t in data['event']['tournaments']] == ['Live Cup']
    assert [s['name'] for s in data['event']['stages']] == ['Main']


async def test_matches_page_forward_without_repeats(app, db):
    _, raw = await create_user_token()
    _, _, matches = await _schedule(5)
    seen, after = [], None
    async with client_for(app, raw) as client:
        for _ in range(3):
            page = (await _query(client, RUN_SHEET, {'first': 2, 'after': after})).json()['data']['event']['matches']
            seen += [n['id'] for n in page['nodes']]
            after = page['pageInfo']['endCursor']
            if not page['pageInfo']['hasNextPage']:
                break
    assert seen == [m.id for m in matches]


async def test_unscheduled_matches_are_left_out(app, db):
    _, raw = await create_user_token()
    tournament, _, matches = await _schedule(1)
    await Match.create(tournament=tournament)
    async with client_for(app, raw) as client:
        nodes = (await _query(client, RUN_SHEET, {'first': 10})).json()['data']['event']['matches']['nodes']
    assert [n['id'] for n in nodes] == [matches[0].id]


async def test_only_approved_crew_are_listed(app, db):
    _, raw = await create_user_token()
    _, _, (match,) = await _schedule(1)
    yes = await User.create(discord_id=501, username='on-mic')
    no = await User.create(discord_id=502, username='pending')
    await Commentator.create(match=match, user=yes, approved=True)
    await Commentator.create(match=match, user=no, approved=False)
    await Broadcaster.create(match=match, user=no, approved=False)
    async with client_for(app, raw) as client:
        node = (await _query(client, RUN_SHEET, {'first': 10})).json()['data']['event']['matches']['nodes'][0]
    assert node['crew'] == [{'role': 'COMMENTATOR', 'user': {'username': 'on-mic'}}]


async def test_a_page_costs_the_same_queries_for_3_or_12_matches(app, db):
    _, raw = await create_user_token()
    tournament, stage, _ = await _schedule(3)
    async with client_for(app, raw) as client:
        with count_queries() as small:
            await _query(client, RUN_SHEET, {'first': 50})
        await _schedule(9, tournament=tournament, stage=stage)
        with count_queries() as big:
            resp = await _query(client, RUN_SHEET, {'first': 50})
    assert len(resp.json()['data']['event']['matches']['nodes']) == 12
    assert small['n'] == big['n']


async def test_nested_tournament_and_stage_matches(app, db):
    _, raw = await create_user_token()
    tournament, stage, matches = await _schedule(2)
    other = await Tournament.create(name='Other', is_active=True)
    await Match.create(tournament=other, stage=stage, scheduled_at=T0)
    query = '{ tournament(id: %d) { matches { nodes { id } } } stage(id: %d) { matches(first: 10) { nodes { id } } } }'
    async with client_for(app, raw) as client:
        data = (await _query(client, query % (tournament.id, stage.id))).json()['data']
    assert [n['id'] for n in data['tournament']['matches']['nodes']] == [m.id for m in matches]
    assert len(data['stage']['matches']['nodes']) == 3


async def test_a_match_works_in_its_own_event(app, events):
    _, raw = await create_user_token()
    with event_scope(events['live'].id):
        _, _, (theirs,) = await _schedule(1)
    async with client_for(app, raw) as client:
        default = (await _query(client, RUN_SHEET, {'first': 10})).json()['data']['event']['matches']['nodes']
        direct = (await _query(client, '{ match(id: %d) { id tournament { name } } }' % theirs.id)).json()
        named = (await _query(client, '{ event(slug: "sgl27") { matches { nodes { id } } } }')).json()
    assert default == []
    assert direct['data']['match']['id'] == theirs.id
    assert [n['id'] for n in named['data']['event']['matches']['nodes']] == [theirs.id]


async def test_a_draft_events_match_is_hidden_from_players(app, events):
    _, raw = await create_user_token()
    with event_scope(events['draft'].id):
        _, _, (hidden,) = await _schedule(1)
    async with client_for(app, raw) as client:
        resp = (await _query(client, '{ match(id: %d) { id } }' % hidden.id)).json()
    assert resp['data'] == {'match': None}
    assert resp['errors'][0]['extensions']['code'] == 'NOT_FOUND'


async def test_another_communitys_rows_are_not_found(app, events):
    _, raw = await create_user_token(roles=[Role.STAFF])
    with tenant_scope(events['other'].id), event_scope(events['theirs'].id):
        t = await Tournament.create(name='Theirs', is_active=True, tenant_id=events['other'].id)
        m = await Match.create(tournament=t, scheduled_at=T0, tenant_id=events['other'].id)
    query = '{ match(id: %d) { id } tournament(id: %d) { id } }' % (m.id, t.id)
    async with client_for(app, raw) as client:
        resp = (await _query(client, query)).json()
    assert resp['data'] == {'match': None, 'tournament': None}
    assert {e['extensions']['code'] for e in resp['errors']} == {'NOT_FOUND'}


async def test_an_inactive_tournament_is_hidden_from_players(app, db):
    _, raw = await create_user_token()
    old = await Tournament.create(name='Old Cup', is_active=False)
    async with client_for(app, raw) as client:
        resp = (await _query(client, '{ tournament(id: %d) { name } }' % old.id)).json()
    assert resp['errors'][0]['extensions']['code'] == 'NOT_FOUND'


async def test_first_over_100_and_bad_cursors_are_user_errors(app, db):
    _, raw = await create_user_token()
    async with client_for(app, raw) as client:
        big = (await _query(client, RUN_SHEET, {'first': 101})).json()
        bad = (await _query(client, RUN_SHEET, {'first': 5, 'after': 'nope'})).json()
    assert big['errors'][0]['extensions']['code'] == 'BAD_USER_INPUT'
    assert bad['errors'][0]['extensions']['code'] == 'BAD_USER_INPUT'


async def test_a_deep_page_of_pages_is_refused_before_running(app, db):
    _, raw = await create_user_token()
    query = '{ event { tournaments { matches(first: 100) { nodes { id } } } } }'
    nested = '{ event { matches(first: 100) { nodes { tournament { matches(first: 100) { nodes { id } } } } } } }'
    async with client_for(app, raw) as client:
        fine = await _query(client, query)
        refused = await _query(client, nested)
    assert fine.status_code == 200
    assert refused.status_code == 400
    assert refused.json()['errors'][0]['extensions']['code'] == 'QUERY_TOO_COMPLEX'


# ---------------------------------------------------------------------------
# Brackets
# ---------------------------------------------------------------------------


BRACKET = '''
query Bracket($id: Int!, $state: String) {
  tournament(id: $id) {
    bracket(state: $state) {
      source filtered
      phases { pools { sets { id identifier state slots { name userId fromSetId } winnerTo { setId slot } } } }
      entrants { name userId }
    }
  }
}
'''


async def test_the_bracket_matches_the_rest_payload(app, db):
    tournament, _, (semi1, _, final), _ = await native_elim()
    _, raw = await create_user_token(username='plain')
    async with client_for(app, raw) as client:
        rest = (await client.get(f'/api/tournaments/{tournament.id}/bracket')).json()
        resp = (await _query(client, BRACKET, {'id': tournament.id})).json()
    assert resp.get('errors') is None, resp
    bracket = resp['data']['tournament']['bracket']
    assert bracket['source'] == rest['source'] == 'native'
    sets = {s['id']: s for s in bracket['phases'][0]['pools'][0]['sets']}
    assert sets[semi1.id]['winnerTo'] == {'setId': final.id, 'slot': 1}
    assert {e['userId'] for e in bracket['entrants']} == {None}


async def test_bracket_filters_and_a_bad_state(app, db):
    tournament, *_ = await native_elim()
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    async with client_for(app, raw) as client:
        opened = (await _query(client, BRACKET, {'id': tournament.id, 'state': 'open'})).json()
        bad = (await _query(client, BRACKET, {'id': tournament.id, 'state': 'done'})).json()
    bracket = opened['data']['tournament']['bracket']
    assert bracket['filtered'] is True
    assert [s['identifier'] for s in bracket['phases'][0]['pools'][0]['sets']] == ['1.2']
    assert bad['errors'][0]['extensions']['code'] == 'BAD_USER_INPUT'


async def test_an_unpublished_bracket_is_not_found_for_members(app, db):
    tournament, *_ = await native_elim(state=BracketState.DRAFT)
    _, raw = await create_user_token(username='plain')
    async with client_for(app, raw) as client:
        resp = (await _query(client, BRACKET, {'id': tournament.id})).json()
    assert resp['data']['tournament']['bracket'] is None
    assert resp['errors'][0]['extensions']['code'] == 'NOT_FOUND'
