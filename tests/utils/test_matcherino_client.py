"""The Matcherino client's reading of the live venue wire shape, served through MockTransport."""

import base64
import json

import httpx
import pytest

from application.utils.clients import matcherino_client as mc
from application.utils.clients.matcherino_client import (
    MatcherinoAPIError,
    MatcherinoAuthError,
    MatcherinoClient,
    mock_purchase,
    mock_tier,
    parse_purchase,
)

LIVE_PURCHASE_KEYS = {
    'address1', 'address2', 'bountyAttachments', 'city', 'code', 'completed', 'country', 'email',
    'entries', 'firstName', 'id', 'lastName', 'meta', 'pass', 'passId', 'phone', 'purchasedAt',
    'refundedAt', 'revshareTotal', 'sourceId', 'sourceType', 'state', 'transactions', 'upsells',
    'user', 'userId', 'venue', 'venueId', 'zip',
}
LIVE_TIER_KEYS = {
    'amount', 'applicableTaxes', 'availableChildren', 'availableEnd', 'availableParents',
    'availableStart', 'createdAt', 'description', 'discountsAvailable', 'id', 'isDeleted', 'meta',
    'precedence', 'qtySold', 'role', 'thumbnailImg', 'title', 'validEnd', 'validStart', 'venueId',
}
REFRESH = 'refresh-value-for-tests'
ACCESS = 'access-value-for-tests'
TIER = mock_tier(1988, 'Base Tier Badge', 8000, qty_sold=1)


@pytest.fixture(autouse=True)
def fresh_token_cache():
    mc.reset_token_cache()
    yield
    mc.reset_token_cache()


def _client(monkeypatch, handler, refresh_token=REFRESH) -> MatcherinoClient:
    client = MatcherinoClient(refresh_token)
    monkeypatch.setattr(
        client, '_session',
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client


def _ok(body):
    return httpx.Response(200, json={'status': 200, 'body': body})


def _venue_api(tiers, purchases, *, seen=None, reject_first=0):
    """A fake Matcherino: sign-in, badge types and purchases."""
    state = {'rejections': reject_first}

    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith('/auth/token'):
            return _ok({'accessToken': ACCESS, 'refreshToken': REFRESH, 'expiresIn': 86400})
        if request.headers.get('x-mno-auth') != f'Bearer {ACCESS}' or state['rejections']:
            state['rejections'] = max(state['rejections'] - 1, 0)
            return httpx.Response(401, json={'status': 401, 'error': {'message': 'requires authentication'},
                                             'body': None})
        if request.url.path.endswith('/venues/pass/listPrivate'):
            return _ok(tiers)
        if request.url.path.endswith('/venues/admin/purchaseData'):
            return _ok(purchases)
        return httpx.Response(404, json={'status': 404, 'error': {'message': 'no route'}})
    return handler


def test_the_mock_fixtures_match_the_live_key_sets():
    assert set(mock_purchase(1, 'x')) == LIVE_PURCHASE_KEYS
    assert set(mock_tier(1, 'x', 1)) == LIVE_TIER_KEYS
    for raw in mc.MOCK_PURCHASES:
        assert set(raw) == LIVE_PURCHASE_KEYS
    for raw in mc.MOCK_TIERS:
        assert set(raw) == LIVE_TIER_KEYS


def test_the_mock_tier_counts_match_the_mock_sales():
    for tier in mc.MOCK_TIERS:
        assert tier['qtySold'] == sum(1 for p in mc.MOCK_PURCHASES if p['passId'] == tier['id'])


def test_a_purchase_parses_identity_tier_and_code_and_drops_contact_details():
    raw = mock_purchase(7, 'Buyer', tier=TIER, purchase_id=31, code=12345678, auth_provider='Discord',
                        auth_id='300000000000000020', purchased_at='2026-09-28T13:15:02.450024Z')
    raw.update(email='buyer@example.com', phone='555-0100', firstName='Pat')
    raw['transactions'] = [{'amount': 8000}]
    tiers = {1988: mc.parse_tier(TIER)}

    parsed = parse_purchase(raw, tiers)

    assert (parsed.id, parsed.code, parsed.tier.title) == (31, '12345678', 'Base Tier Badge')
    assert (parsed.buyer.user_id, parsed.buyer.display_name, parsed.buyer.auth_provider) == (
        '7', 'Buyer', 'discord')
    assert parsed.buyer.handle == 'Buyer#7'
    assert parsed.purchased_at.tzinfo is not None and parsed.active
    stored = json.dumps([parsed.raw, parsed.buyer.raw])
    assert 'example.com' not in stored and '555-0100' not in stored and 'Pat' not in stored
    assert 'transactions' not in parsed.raw


def test_a_badge_type_missing_from_the_venue_list_comes_from_the_purchase():
    parsed = parse_purchase(mock_purchase(7, 'x', tier=mock_tier(9, 'Early Bird', 6000)), {})
    assert (parsed.tier.id, parsed.tier.title, parsed.tier.amount_cents) == (9, 'Early Bird', 6000)


def test_a_refunded_purchase_is_not_active():
    parsed = parse_purchase(mock_purchase(7, 'x', refunded_at='2026-09-20T10:00:00Z'), {})
    assert not parsed.active


@pytest.mark.parametrize('breakage', ['no_user_id', 'no_code', 'other_buyer', 'no_name'])
def test_a_changed_purchase_shape_raises(breakage):
    raw = mock_purchase(7, 'x')
    if breakage == 'no_user_id':
        raw['userId'] = None
    elif breakage == 'no_code':
        raw['code'] = None
    elif breakage == 'other_buyer':
        raw['user']['id'] = 8
    else:
        raw['user']['displayName'] = ''
    with pytest.raises(MatcherinoAPIError):
        parse_purchase(raw, {})


async def test_sales_are_read_with_a_minted_token(monkeypatch):
    seen = []
    client = _client(monkeypatch, _venue_api([TIER], [mock_purchase(7, 'x', tier=TIER)], seen=seen))

    sales = await client.fetch_sales(182105)

    assert [p.buyer.user_id for p in sales.purchases] == ['7']
    assert [t.title for t in sales.tiers] == ['Base Tier Badge']
    sign_in, tiers_call, purchases_call = seen
    assert json.loads(sign_in.content) == {'appName': 'WEB', 'refreshToken': REFRESH}
    assert json.loads(tiers_call.content) == {'venueId': 182105}
    assert tiers_call.headers['content-type'].startswith('text/plain')
    assert purchases_call.url.params['venueId'] == '182105'


async def test_the_token_is_reused_until_it_expires(monkeypatch):
    seen = []
    client = _client(monkeypatch, _venue_api([TIER], [mock_purchase(7, 'x', tier=TIER)], seen=seen))

    await client.fetch_sales(1)
    await client.fetch_sales(1)

    assert sum(1 for r in seen if r.url.path.endswith('/auth/token')) == 1


async def test_a_rejected_token_is_minted_again_once(monkeypatch):
    seen = []
    client = _client(monkeypatch, _venue_api([TIER], [mock_purchase(7, 'x', tier=TIER)], seen=seen,
                                             reject_first=1))

    await client.fetch_sales(1)

    assert sum(1 for r in seen if r.url.path.endswith('/auth/token')) == 2


async def test_a_login_that_is_not_a_venue_admin_says_so(monkeypatch):
    client = _client(monkeypatch, _venue_api([TIER], [], reject_first=99))
    with pytest.raises(MatcherinoAuthError, match='must be an admin of this venue') as caught:
        await client.fetch_tiers(1)
    assert ACCESS not in str(caught.value) and REFRESH not in str(caught.value)


async def test_a_refused_login_names_the_setting_but_not_the_secret(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={'status': 401, 'error': {'message': 'bad refresh token'}})

    with pytest.raises(MatcherinoAuthError, match='Admin → Check-in') as caught:
        await _client(monkeypatch, handler).fetch_tiers(1)
    assert REFRESH not in str(caught.value)


async def test_no_stored_login_fails_before_calling_matcherino(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok({})

    with pytest.raises(MatcherinoAuthError, match='no Matcherino login is saved'):
        await _client(monkeypatch, handler, refresh_token=None).fetch_sales(1)
    assert calls == []


async def test_each_login_mints_and_caches_its_own_token(monkeypatch):
    minted = []

    def handler(request):
        if request.url.path.endswith('/auth/token'):
            refresh = json.loads(request.content)['refreshToken']
            minted.append(refresh)
            return _ok({'accessToken': f'access-for-{refresh}', 'expiresIn': 86400})
        return _ok([])

    for refresh in ('login-a', 'login-b', 'login-a'):
        await _client(monkeypatch, handler, refresh_token=refresh).fetch_tiers(1)

    assert minted == ['login-a', 'login-b']


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'eyJhbGciOiJIUzI1NiJ9.{body}.sig'


async def test_verifying_a_login_always_signs_in_and_reports_the_account(monkeypatch):
    minted = []

    def handler(request):
        minted.append(request)
        return _ok({'accessToken': _jwt({'sub': 4321, 'exp': 1}), 'expiresIn': 86400})

    client = _client(monkeypatch, handler)
    assert await client.verify_login() == '4321'
    assert await client.verify_login() == '4321'
    assert len(minted) == 2


@pytest.mark.parametrize('pasted, expected', [
    ('  3f1c-uuid  ', '3f1c-uuid'),
    ('"3f1c-uuid"', '3f1c-uuid'),
    ('{"appName":"WEB","refreshToken":"3f1c-uuid"}', '3f1c-uuid'),
    ('%7B%22refreshToken%22%3A%223f1c-uuid%22%2C%22appName%22%3A%22WEB%22%7D', '3f1c-uuid'),
    ('{"appName":"WEB"}', ''),
    ('', ''),
    (None, ''),
])
def test_a_pasted_token_or_request_payload_is_normalized(pasted, expected):
    assert mc.normalize_refresh_token(pasted) == expected


@pytest.mark.parametrize('token', ['not-a-jwt', 'a.!!!.c', _jwt({'exp': 1})])
def test_an_access_token_without_a_readable_sub_has_no_account(token):
    assert mc.account_id_from_access_token(token) is None


async def test_a_short_read_is_an_error_not_fewer_buyers(monkeypatch):
    tier = mock_tier(1988, 'Base', 8000, qty_sold=3)
    purchases = [mock_purchase(1, 'a', tier=tier), mock_purchase(2, 'b', tier=tier)]
    client = _client(monkeypatch, _venue_api([tier], purchases))
    with pytest.raises(MatcherinoAPIError, match='counts 3 badges sold but listed 2'):
        await client.fetch_sales(1)


async def test_a_purchase_listed_twice_is_one_badge(monkeypatch):
    tier = mock_tier(1988, 'Base', 8000, qty_sold=2)
    purchases = [
        mock_purchase(1, 'a', tier=tier, purchase_id=10),
        mock_purchase(1, 'a', tier=tier, purchase_id=10, refunded_at='2026-10-21T00:00:00Z'),
        mock_purchase(2, 'b', tier=tier, purchase_id=20),
    ]
    sales = await _client(monkeypatch, _venue_api([tier], purchases)).fetch_sales(1)
    assert [(p.id, p.active) for p in sales.purchases] == [(10, False), (20, True)]


async def test_an_error_envelope_raises_with_matcherinos_message(monkeypatch):
    body = {'status': 500, 'error': {'message': 'Failed to parse int string'}, 'body': None}
    client = _client(monkeypatch, lambda r: httpx.Response(500, json=body))
    with pytest.raises(MatcherinoAPIError, match='Failed to parse int string'):
        await client.fetch_venue(1)


async def test_non_json_raises(monkeypatch):
    client = _client(monkeypatch, lambda r: httpx.Response(502, text='<html>bad gateway</html>'))
    with pytest.raises(MatcherinoAPIError, match='non-JSON'):
        await client.fetch_venue(1)


async def test_a_network_failure_raises(monkeypatch):
    def handler(request):
        raise httpx.ConnectError('down', request=request)

    with pytest.raises(MatcherinoAPIError, match="Couldn't reach Matcherino"):
        await _client(monkeypatch, handler).fetch_venue(1)


async def test_a_venue_title_is_read(monkeypatch):
    body = {'id': 182105, 'kind': 'venue', 'title': 'SpeedGaming Live 2026'}
    venue = await _client(monkeypatch, lambda r: _ok(body)).fetch_venue(182105)
    assert (venue.id, venue.title) == (182105, 'SpeedGaming Live 2026')


async def test_a_plain_bounty_is_not_a_venue(monkeypatch):
    body = {'id': 182107, 'kind': 'bounty', 'title': 'SpeedGaming Live 2026 Tournaments'}
    with pytest.raises(MatcherinoAPIError, match='not a ticketed venue'):
        await _client(monkeypatch, lambda r: _ok(body)).fetch_venue(182107)


def test_the_mock_refuses_production(monkeypatch):
    monkeypatch.setenv('MOCK_MATCHERINO', 'true')
    monkeypatch.setattr(mc, 'is_production', lambda: True)
    with pytest.raises(RuntimeError):
        mc.is_mock_matcherino()


def test_the_mock_discord_buyer_is_racer_six():
    from scripts.seed_support import fixture_discord_id

    assert mc.MOCK_DISCORD_RACER_ID == fixture_discord_id('racer_06')
