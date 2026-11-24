"""Challonge's monthly request allowance: one refusal pauses every Challonge call (SGLMAN-30)."""

import pytest

from application.utils.clients import challonge_client as cc

_LIMIT_BODY = (
    '{"errors":{"detail":"API request limit exceeded. Your plan allows 500 requests '
    'per 30 days. Reduce usage or upgrade your plan.","status":429}}'
)


class _Resp:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body
        self.headers = {}

    async def text(self) -> str:
        return self._body


@pytest.fixture(autouse=True)
def _no_pause():
    cc.reset_quota_pause()
    yield
    cc.reset_quota_pause()


async def test_monthly_limit_refusal_pauses_calls():
    with pytest.raises(cc.ChallongeQuotaError, match='monthly API limit'):
        await cc.ChallongeClient._parse(_Resp(429, _LIMIT_BODY))
    assert cc.quota_paused()


async def test_plain_rate_limit_does_not_pause():
    with pytest.raises(cc.ChallongeAPIError) as raised:
        await cc.ChallongeClient._parse(_Resp(429, '{"errors":"slow down"}'))
    assert not isinstance(raised.value, cc.ChallongeQuotaError)
    assert not cc.quota_paused()


async def test_paused_client_sends_nothing():
    calls = []

    async def token_provider(force_refresh: bool = False) -> str:
        calls.append('token')
        return 't'

    async def count() -> None:
        calls.append('request')

    cc._pause_for_quota()
    client = cc.ChallongeClient('id', 'secret', token_provider=token_provider, on_request=count)
    with pytest.raises(cc.ChallongeQuotaError):
        await client.get_tournament('abc')
    with pytest.raises(cc.ChallongeQuotaError):
        await client.get_me('t')
    assert calls == []


async def test_auto_sync_skips_while_paused(monkeypatch):
    from application.repositories import ChallongeRepository
    from application.services import challonge_sync_worker
    from application.services.challonge_service import ChallongeService

    async def boom():
        raise AssertionError('the paused worker read the linked communities')

    monkeypatch.setattr(ChallongeService, 'is_configured', staticmethod(lambda: True))
    monkeypatch.setattr(ChallongeRepository, 'linked_tenant_ids', staticmethod(boom))
    cc._pause_for_quota()
    await challonge_sync_worker._tick()


def test_unavailable_handback_says_come_back_later():
    from pages import _oauth_link as link

    link.register_link_handoff_provider(link.LinkHandoffProvider(
        key='quota-demo', label='Demo', profile_return='/home/profile',
        authorize_url=lambda s: '', exchange=None, record=None,
        is_mock=lambda: False, callback_route='/quota-demo/oauth/callback',
    ))
    assert link._handback_message('unavailable', 'quota-demo') == (
        "Demo isn't taking requests from Wizzrobe right now. Try again in a few hours."
    )
