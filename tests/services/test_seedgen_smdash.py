"""Tests for the Super Metroid: DASH backend of SeedGenerationService (unit — no network)."""

import pytest

from application.services.seedgen_service import SeedGenerationService
from application.utils.seed_provider import (
    SeedProviderBadResponse,
    SeedProviderInvalidRequest,
)
from models import Preset


@pytest.fixture
def service():
    return SeedGenerationService()


class _FakeResp:
    def __init__(self, status, headers=None, body=''):
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._body


@pytest.fixture
def dash(monkeypatch):
    """Patch ``aiohttp.request``; set ``.response`` and read ``.calls``."""
    import aiohttp

    class _Stub:
        def __init__(self):
            self.response = _FakeResp(307, {'Location': 'https://www.dashrando.net/seed/abc123'})
            self.calls = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return self.response

    stub = _Stub()
    monkeypatch.setattr(aiohttp, 'request', stub)
    monkeypatch.delenv('MOCK_SEEDGEN', raising=False)
    return stub


class TestGenerateSmdash:
    def test_rolls_once_because_upstream_saves_every_seed(self):
        assert SeedGenerationService.PROVIDER_ATTEMPTS['smdash'] == 1

    async def test_rolls_the_bundled_sgl26_preset_as_a_race_seed(self, service, dash):
        call = await service.generate_seed_call('smdash')
        assert call.value.url == 'https://www.dashrando.net/seed/abc123'
        assert call.value.settings == {'preset': 'sgl26', 'race': True}
        sent, code_fetch = dash.calls
        assert sent['url'] == 'https://www.dashrando.net/generate/sgl26'
        assert sent['params'] == {'race': '1'}
        assert sent['allow_redirects'] is False
        assert code_fetch['url'] == 'https://www.dashrando.net/seed/abc123'
        assert call.value.code is None

    async def test_uses_the_preset_tag(self, service, dash):
        preset = Preset(name='classic', randomizer='smdash', settings={'preset': 'classic_mm'})
        await service.generate_seed('smdash', preset)
        assert dash.calls[0]['url'] == 'https://www.dashrando.net/generate/classic_mm'

    async def test_resolves_a_relative_location(self, service, dash):
        dash.response = _FakeResp(307, {'Location': '/seed/rel456'})
        assert await service.generate_seed('smdash') == 'https://www.dashrando.net/seed/rel456'

    @pytest.mark.parametrize('settings', [{}, {'preset': ''}, {'preset': '../admin'}, {'preset': 7}])
    async def test_rejects_a_preset_without_a_usable_tag(self, service, dash, settings):
        preset = Preset(name='bad', randomizer='smdash', settings=settings)
        with pytest.raises(ValueError, match='"preset" key'):
            await service.generate_seed('smdash', preset)
        assert dash.calls == []

    async def test_unknown_upstream_preset_is_an_invalid_request(self, service, dash):
        dash.response = _FakeResp(422, body='Invalid preset. Valid presets are: sgl26')
        with pytest.raises(SeedProviderInvalidRequest):
            await service.generate_seed('smdash')

    @pytest.mark.parametrize('response', [
        _FakeResp(200, body='<html>'),
        _FakeResp(307, {'Location': 'https://www.dashrando.net/generate'}),
        _FakeResp(307, {'Location': '/error?from=/seed/x'}),
        _FakeResp(307, {'Location': 'https://evil.example/seed/abc'}),
        _FakeResp(307, {'Location': 'http://www.dashrando.net/seed/abc'}),
        _FakeResp(307, {}),
    ])
    async def test_a_response_that_is_not_a_seed_redirect_is_bad(self, service, dash, response):
        dash.response = response
        with pytest.raises(SeedProviderBadResponse):
            await service.generate_seed('smdash')
