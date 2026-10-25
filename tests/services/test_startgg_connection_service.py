"""The community start.gg sign-in: connect, refresh, fallback, and the per-tournament access check."""

from datetime import datetime, timedelta, timezone

import pytest

from application.repositories import StartggRepository
from application.services import startgg_connection_service as module
from application.services.startgg_connection_service import StartggConnectionService
from application.services.startgg_service import StartggService
from application.utils.clients.startgg_client import StartggAPIError, tournament_slug
from models import AuditLog, Tournament
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


class FakeOAuth:
    def __init__(self):
        self.refreshed: list = []

    async def exchange_code(self, code, redirect_uri, scope='user.identity'):
        return {'access_token': f'access-{code}', 'refresh_token': 'refresh-1', 'expires_in': 604800,
                'scope': scope}

    async def refresh(self, refresh_token, redirect_uri, scope):
        self.refreshed.append(refresh_token)
        return {'access_token': 'access-new', 'refresh_token': 'refresh-2', 'expires_in': 604800}

    async def get_me(self, access_token):
        return {'user_id': '555', 'username': 'synack', 'display_name': 'Synack'}


class FakeApi:
    def __init__(self, admin_slugs=None, admin_ids=None, fail_list=False, fail_ids=False):
        self.admin_slugs = admin_slugs or set()
        self.admin_ids = admin_ids or {}
        self.fail_list = fail_list
        self.fail_ids = fail_ids

    async def get_admin_tournament_slugs(self):
        if self.fail_list:
            raise StartggAPIError('nope')
        return '555', self.admin_slugs

    async def get_tournament_admin_ids(self, slug):
        if self.fail_ids:
            raise StartggAPIError('hidden')
        return self.admin_ids.get(slug, set())


@pytest.fixture
def oauth(monkeypatch):
    fake = FakeOAuth()
    monkeypatch.setattr(StartggConnectionService, '_oauth_client', staticmethod(lambda: fake))
    monkeypatch.delenv('STARTGG_API_TOKEN', raising=False)
    monkeypatch.delenv('MOCK_STARTGG', raising=False)
    return fake


def _api(monkeypatch, api):
    monkeypatch.setattr(module, 'StartggClient', lambda token: api)


async def _connect(actor):
    service = StartggConnectionService()
    return await service.save_connection(await service.exchange_connect_code('abc'), actor)


async def test_connect_stores_the_account_and_audits(db, oauth):
    actor = await make_user(1, 'staff')
    connection = await _connect(actor)
    assert connection.startgg_username == 'Synack'
    assert connection.startgg_user_id == '555'
    assert connection.scopes == 'user.identity tournament.reporter'
    assert await AuditLog.filter(action='startgg.connected').count() == 1
    status = await StartggConnectionService().get_status()
    assert status['connected'] and status['username'] == 'Synack' and not status['expired']


async def test_only_staff_connect(db, oauth, monkeypatch):
    from application.services.auth_service import AuthService

    async def no(*_a, **_k):
        return False

    async def ensure(ok, message):
        if not ok:
            raise PermissionError(message)

    monkeypatch.setattr(AuthService, 'is_staff', no)
    monkeypatch.setattr(AuthService, 'ensure', ensure)
    with pytest.raises(PermissionError, match='Only staff'):
        await _connect(await make_user(2, 'player'))
    assert await StartggRepository.get_connection() is None


async def test_token_is_refreshed_once_when_close_to_expiry(db, oauth):
    await _connect(await make_user(1, 'staff'))
    connection = await StartggRepository.get_connection()
    connection.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    await connection.save()
    service = StartggConnectionService()
    assert await service.api_token() == 'access-new'
    assert await service.api_token() == 'access-new'
    assert oauth.refreshed == ['refresh-1']
    assert (await StartggRepository.get_connection()).refresh_token == 'refresh-2'


async def test_failed_refresh_falls_back_to_the_platform_token(db, oauth, monkeypatch):
    await _connect(await make_user(1, 'staff'))
    connection = await StartggRepository.get_connection()
    connection.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await connection.save()

    async def boom(*_a, **_k):
        raise StartggAPIError('refresh refused')

    monkeypatch.setattr(oauth, 'refresh', boom)
    monkeypatch.setenv('STARTGG_API_TOKEN', 'platform')
    assert await StartggConnectionService().api_token() == 'platform'
    assert (await StartggConnectionService().get_status())['expired']


async def test_status_renews_a_lapsed_token_instead_of_calling_it_lapsed(db, oauth):
    """A sign-in nobody used all week lapses without a renewal being tried;
    the status renews it rather than telling staff it couldn't be renewed."""
    await _connect(await make_user(1, 'staff'))
    connection = await StartggRepository.get_connection()
    connection.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await connection.save()
    assert not (await StartggConnectionService().get_status())['expired']
    assert oauth.refreshed == ['refresh-1']


async def test_no_sign_in_uses_the_platform_token(db, oauth, monkeypatch):
    service = StartggConnectionService()
    assert await service.api_token() is None
    assert not await service.has_access()
    monkeypatch.setenv('STARTGG_API_TOKEN', 'platform')
    assert await service.api_token() == 'platform'
    assert await service.has_access()


async def test_startgg_service_reads_with_the_sign_in(db, oauth, monkeypatch):
    await _connect(await make_user(1, 'staff'))
    seen = {}
    monkeypatch.setattr(
        'application.services.startgg_service.StartggClient', lambda token: seen.setdefault('token', token),
    )
    await StartggService()._client()
    assert seen['token'] == 'access-abc'


async def test_disconnect(db, oauth):
    actor = await make_user(1, 'staff')
    await _connect(actor)
    await StartggConnectionService().disconnect(actor)
    assert await StartggRepository.get_connection() is None
    assert await AuditLog.filter(action='startgg.disconnected').count() == 1


async def _tournaments():
    ok = await Tournament.create(name='OoTR', startgg_event_slug='tournament/ootr/event/singles',
                                 startgg_report_results=True)
    nope = await Tournament.create(name='Test', startgg_event_slug='tournament/test/event/singles')
    return ok, nope


async def test_access_check_per_linked_tournament(db, oauth, monkeypatch):
    actor = await make_user(1, 'staff')
    await _connect(actor)
    await _tournaments()
    _api(monkeypatch, FakeApi(admin_slugs={'tournament/ootr'}))
    rows = {r.name: r for r in await StartggConnectionService().access_for_linked(actor)}
    assert rows['OoTR'].can_report is True and rows['OoTR'].reports_results
    assert rows['Test'].can_report is False


async def test_access_falls_back_to_the_tournament_admin_list(db, oauth, monkeypatch):
    actor = await make_user(1, 'staff')
    await _connect(actor)
    await _tournaments()
    _api(monkeypatch, FakeApi(fail_list=True, admin_ids={'tournament/ootr': {'555'}}))
    rows = {r.name: r for r in await StartggConnectionService().access_for_linked(actor)}
    assert rows['OoTR'].can_report is True
    assert rows['Test'].can_report is False


async def test_access_unknown_when_startgg_answers_neither(db, oauth, monkeypatch):
    actor = await make_user(1, 'staff')
    await _connect(actor)
    await _tournaments()
    _api(monkeypatch, FakeApi(fail_list=True, fail_ids=True))
    rows = await StartggConnectionService().access_for_linked(actor)
    assert {r.can_report for r in rows} == {None}


async def test_no_access_rows_without_a_sign_in(db, oauth):
    await _tournaments()
    assert await StartggConnectionService().access_for_linked(await make_user(1, 'staff')) == []


def test_tournament_slug():
    assert tournament_slug('tournament/ootr-sgl-2026/event/sgl2026') == 'tournament/ootr-sgl-2026'
