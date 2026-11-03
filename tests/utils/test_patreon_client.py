"""The Patreon client against Patreon's wire shapes, served through MockTransport."""

import json

import httpx
import pytest

from application.utils.clients.patreon_client import (
    PatreonAuthError,
    PatreonClient,
    PatreonError,
    PatreonOAuthClient,
    build_authorize_url,
    parse_webhook,
)


def _factory(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _no_sleep(_seconds):
    return None


async def test_members_page_asks_for_status_only_and_follows_the_cursor():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers['Authorization'] == 'Bearer tok'
        return httpx.Response(200, json={
            'data': [{
                'type': 'member', 'id': 'm-1', 'attributes': {'patron_status': 'active_patron'},
                'relationships': {
                    'user': {'data': {'type': 'user', 'id': '42'}},
                    'currently_entitled_tiers': {'data': [{'type': 'tier', 'id': '7'}]},
                },
            }],
            'meta': {'pagination': {'cursors': {'next': 'abc'}, 'total': 2}},
        })

    client = PatreonClient('tok', client_factory=_factory(handler), sleep=_no_sleep)
    members, cursor = await client.members_page('99', 'start')
    assert cursor == 'abc'
    m = members[0]
    assert (m.patreon_user_id, m.campaign_id, m.patron_status, m.tier_ids, m.is_patron) == ('42', '99', 'active_patron', ('7',), True)
    params = dict(seen[0].url.params)
    assert params['fields[member]'] == 'patron_status'
    assert 'amount' not in str(seen[0].url)
    assert params['page[cursor]'] == 'start' and seen[0].url.path.endswith('/campaigns/99/members')


async def test_a_429_is_waited_out_then_retried():
    calls, waits = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={'Retry-After': '7'})
        return httpx.Response(200, json={'data': []})

    async def sleep(seconds):
        waits.append(seconds)

    client = PatreonClient('tok', client_factory=_factory(handler), sleep=sleep)
    assert await client.campaigns() == []
    assert waits == [7.0, 7.0]


async def test_endless_429_gives_up_and_refused_tokens_say_so():
    client = PatreonClient('tok', client_factory=_factory(lambda r: httpx.Response(429)), sleep=_no_sleep)
    with pytest.raises(PatreonError):
        await client.campaigns()
    client = PatreonClient('tok', client_factory=_factory(lambda r: httpx.Response(401)), sleep=_no_sleep)
    with pytest.raises(PatreonAuthError):
        await client.campaigns()


async def test_code_exchange_and_refresh_post_the_app_credentials():
    bodies = []

    def handler(request):
        bodies.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={'access_token': 'a', 'refresh_token': 'r', 'expires_in': 2678400})

    oauth = PatreonOAuthClient('cid', 'csecret', client_factory=_factory(handler), sleep=_no_sleep)
    tokens = await oauth.exchange_code('code-1', 'https://x/cb')
    assert (tokens.access_token, tokens.refresh_token, tokens.expires_in) == ('a', 'r', 2678400)
    await oauth.refresh('r')
    assert bodies[0]['grant_type'] == 'authorization_code' and bodies[0]['client_secret'] == 'csecret'
    assert bodies[1] == {'grant_type': 'refresh_token', 'refresh_token': 'r', 'client_id': 'cid', 'client_secret': 'csecret'}


async def test_a_spent_refresh_token_is_an_auth_error():
    oauth = PatreonOAuthClient('cid', 'cs', client_factory=_factory(lambda r: httpx.Response(400, json={'error': 'invalid_grant'})))
    with pytest.raises(PatreonAuthError):
        await oauth.refresh('spent')


async def test_create_webhook_returns_id_and_secret():
    def handler(request):
        body = json.loads(request.content)
        assert body['data']['attributes']['triggers'] == ['members:create', 'members:update', 'members:delete']
        assert body['data']['relationships']['campaign']['data']['id'] == '99'
        return httpx.Response(201, json={'data': {'type': 'webhook', 'id': 'w1', 'attributes': {'secret': 's3'}}})

    client = PatreonClient('tok', client_factory=_factory(handler), sleep=_no_sleep)
    assert await client.create_webhook('99', 'https://x/hook') == ('w1', 's3')


def test_authorize_url_and_webhook_parsing():
    url = build_authorize_url('cid', 'https://x/cb', 'identity identity.memberships', 'st')
    assert 'response_type=code' in url and 'state=st' in url and 'identity.memberships' in url
    with pytest.raises(PatreonError):
        parse_webhook(b'not json')
    with pytest.raises(PatreonError):
        parse_webhook(b'{"data": {"type": "user", "id": "1"}}')
