"""NotificationChannelService: confirming an email, connecting Telegram, the DM copy and tests."""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import FeatureDisabledError
from application.services import notification_channel_service as ncs
from application.services import telegram_bot_worker
from application.services.audit_service import AuditActions
from application.services.notification_channel_service import Channel, NotificationChannelService
from application.tenant_context import tenant_scope
from models import AuditLog, ContactVerification, FeatureFlag, TenantFeatureFlag, User
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def transports(monkeypatch):
    """Capture every email and Telegram send instead of making one."""
    sent = {'email': [], 'telegram': []}

    async def fake_email(self, to, subject, text, html_body, headers=None):
        sent['email'].append({'to': to, 'subject': subject, 'text': text, 'html': html_body, 'headers': headers})
        return True

    async def fake_send(chat_id, text, button_url=None, button_label=None, silent=False):
        sent['telegram'].append({'chat_id': chat_id, 'text': text, 'url': button_url})
        if silent:
            sent['telegram'][-1]['silent'] = True
        return True

    async def fake_bot_username():
        return 'WizzrobeBot'

    monkeypatch.setenv('STORAGE_SECRET', 'test-secret')
    monkeypatch.setattr(NotificationChannelService, '_deliver_email', fake_email)
    monkeypatch.setattr(ncs, 'is_mock_discord', lambda: False)
    monkeypatch.setattr(ncs.sendgrid_client, 'is_configured', lambda: True)
    monkeypatch.setattr(ncs.telegram_client, 'is_configured', lambda: True)
    monkeypatch.setattr(ncs.telegram_client, 'send_message', fake_send)
    monkeypatch.setattr(ncs.telegram_client, 'bot_username', fake_bot_username)
    ncs.reset_rate_limits()
    yield sent
    ncs.reset_rate_limits()


@pytest.fixture
async def player():
    return await make_user(discord_id=700, username='player')


def _code_from(sent) -> str:
    return sent['email'][-1]['subject'].rsplit(' ', 1)[-1]


async def _actions() -> list[str]:
    return [a.action for a in await AuditLog.all().order_by('id')]


# ------------------------------------------------------------------ email


async def test_a_mailed_code_confirms_the_address_and_turns_email_on(player, transports):
    service = NotificationChannelService()
    stored = await service.start_email_verification(player, '  Sam.K@Example.COM ')

    assert stored == 'Sam.K@example.com'
    assert transports['email'][0]['to'] == 'Sam.K@example.com'
    await service.confirm_email(player, _code_from(transports))

    user = await User.get(id=player.id)
    assert (user.email, user.email_notifications) == ('Sam.K@example.com', True)
    assert user.email_verified_at is not None
    assert await ContactVerification.all().count() == 0
    assert await _actions() == [AuditActions.NOTIFICATION_EMAIL_CONFIRMED]


@pytest.mark.parametrize('bad', [
    'not-an-email', 'a@b', 'sam@example.com\nBcc: x@evil.test', 'sam@example.com, x@evil.test',
    '"sam"@example.com', 'x' * 65 + '@example.com', '',
])
async def test_junk_and_header_tricks_are_refused(player, transports, bad):
    with pytest.raises(ValueError):
        await NotificationChannelService().start_email_verification(player, bad)
    assert transports['email'] == []


async def test_a_wrong_code_counts_and_five_lock_the_code_out(player, transports):
    service = NotificationChannelService()
    await service.start_email_verification(player, 'sam@example.com')
    real = _code_from(transports)
    wrong = '000000' if real != '000000' else '111111'

    for _ in range(ncs.MAX_CODE_ATTEMPTS):
        with pytest.raises(ValueError, match="doesn't match"):
            await service.confirm_email(player, wrong)
    with pytest.raises(ValueError, match='Too many'):
        await service.confirm_email(player, real)
    assert (await User.get(id=player.id)).email is None


async def test_an_expired_code_is_refused(player, transports):
    service = NotificationChannelService()
    await service.start_email_verification(player, 'sam@example.com')
    await ContactVerification.all().update(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))

    with pytest.raises(ValueError, match='expired'):
        await service.confirm_email(player, _code_from(transports))


async def test_codes_are_rate_limited_per_account(player, transports):
    service = NotificationChannelService()
    for _ in range(ncs.CODES_PER_HOUR):
        await service.start_email_verification(player, 'sam@example.com')
    with pytest.raises(ValueError, match='lot of codes'):
        await service.start_email_verification(player, 'sam@example.com')
    assert len(transports['email']) == ncs.CODES_PER_HOUR


async def test_one_players_code_does_not_confirm_another(player, transports):
    other = await make_user(discord_id=701, username='other')
    service = NotificationChannelService()
    await service.start_email_verification(player, 'sam@example.com')
    await service.start_email_verification(other, 'other@example.com')

    with pytest.raises(ValueError):
        await service.confirm_email(other, transports['email'][0]['subject'].rsplit(' ', 1)[-1])


async def test_discord_email_needs_the_same_account_and_a_verified_address(player):
    service = NotificationChannelService()
    with pytest.raises(ValueError, match="isn't the one"):
        await service.use_discord_email(player, '999', 'sam@example.com', True)
    with pytest.raises(ValueError, match="hasn't confirmed"):
        await service.use_discord_email(player, '700', 'sam@example.com', False)

    await service.use_discord_email(player, '700', 'sam@example.com', True)
    user = await User.get(id=player.id)
    assert (user.email, user.email_notifications) == ('sam@example.com', True)


async def test_the_unsubscribe_link_turns_email_off_only_for_its_address(player):
    service = NotificationChannelService()
    await service.use_discord_email(player, '700', 'sam@example.com', True)
    player = await User.get(id=player.id)
    token = ncs.unsubscribe_url(player).split('t=', 1)[1]

    assert await service.unsubscribe_email('garbage') is False
    assert await service.unsubscribe_email(token) is True
    assert (await User.get(id=player.id)).email_notifications is False

    await service.use_discord_email(player, '700', 'new@example.com', True)
    assert await service.unsubscribe_email(token) is False
    assert (await User.get(id=player.id)).email_notifications is True


# --------------------------------------------------------------- telegram


async def test_the_start_link_binds_the_chat_once(player):
    service = NotificationChannelService()
    link = await service.create_telegram_link(player)
    assert link.startswith('https://t.me/WizzrobeBot?start=')
    token = link.split('start=', 1)[1]

    reply = await telegram_bot_worker.handle_text(5555, 'samk', f'/start {token}')
    assert "You're connected" in reply
    user = await User.get(id=player.id)
    assert (user.telegram_chat_id, user.telegram_username, user.telegram_notifications) == (5555, 'samk', True)

    again = await telegram_bot_worker.handle_text(5555, 'samk', f'/start {token}')
    assert 'expired' in again
    assert AuditActions.NOTIFICATION_TELEGRAM_LINKED in await _actions()


async def test_a_chat_bound_to_someone_else_is_not_stolen(player):
    other = await make_user(discord_id=701, username='other')
    service = NotificationChannelService()
    first = (await service.create_telegram_link(other)).split('start=', 1)[1]
    await telegram_bot_worker.handle_text(5555, 'samk', f'/start {first}')

    token = (await service.create_telegram_link(player)).split('start=', 1)[1]
    reply = await telegram_bot_worker.handle_text(5555, 'samk', f'/start {token}')

    assert 'another Wizzrobe account' in reply
    assert (await User.get(id=player.id)).telegram_chat_id is None


async def test_stop_turns_telegram_off(player):
    token = (await NotificationChannelService().create_telegram_link(player)).split('start=', 1)[1]
    await telegram_bot_worker.handle_text(5555, None, f'/start {token}')

    await telegram_bot_worker.handle_text(5555, None, '/stop')
    assert (await User.get(id=player.id)).telegram_notifications is False
    assert await telegram_bot_worker.handle_text(5555, None, 'hello') is None


# ------------------------------------------------------- the DM copy


async def _with_both(player) -> User:
    service = NotificationChannelService()
    await service.use_discord_email(player, '700', 'sam@example.com', True)
    token = (await service.create_telegram_link(player)).split('start=', 1)[1]
    await telegram_bot_worker.handle_text(5555, None, f'/start {token}')
    return await User.get(id=player.id)


async def test_a_dm_is_copied_to_email_and_telegram(player, transports):
    await _with_both(player)
    await NotificationChannelService().mirror_dm(
        700, '**Your match** is up\nStation 4', navigate='https://x.test/m/1', label='Open the match',
    )

    email = transports['email'][-1]
    assert email['subject'] == 'Your match is up'
    assert 'Open the match: https://x.test/m/1' in email['text']
    assert email['headers']['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
    assert transports['telegram'][-1] == {'chat_id': 5555, 'text': 'Your match is up\nStation 4', 'url': 'https://x.test/m/1'}


async def test_the_copy_keeps_underscores_in_names(player, transports):
    await _with_both(player)
    await NotificationChannelService().mirror_dm(700, '**Match** vs dark__link')

    assert transports['email'][-1]['subject'] == 'Match vs dark__link'
    assert transports['telegram'][-1]['text'] == 'Match vs dark__link'


async def test_a_silent_dm_reaches_telegram_quietly(player, transports):
    await _with_both(player)
    await NotificationChannelService().mirror_dm(700, 'Match started', silent=True)

    assert transports['telegram'][-1]['silent'] is True


async def test_the_copy_escapes_html(player, transports):
    await _with_both(player)
    await NotificationChannelService().mirror_dm(700, '<script>alert(1)</script>')
    assert '<script>' not in transports['email'][-1]['html']


async def test_no_copy_when_the_community_has_the_flag_off(player, transports):
    await _with_both(player)
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.NOTIFICATION_CHANNELS.value,
    ).update(enabled=False)
    before = (len(transports['email']), len(transports['telegram']))

    with tenant_scope(DEFAULT_TEST_TENANT_ID):
        await NotificationChannelService().mirror_dm(700, 'Your match is up')
    assert (len(transports['email']), len(transports['telegram'])) == before

    with pytest.raises(FeatureDisabledError):
        await NotificationChannelService().start_email_verification(player, 'sam@example.com')


async def test_no_copy_to_a_channel_turned_off(player, transports):
    user = await _with_both(player)
    service = NotificationChannelService()
    await service.set_email_enabled(user, False)
    before = len(transports['email'])

    await service.mirror_dm(700, 'Your match is up')
    assert len(transports['email']) == before
    assert len(transports['telegram']) == 1


# ------------------------------------------------------------------ tests


async def test_tests_go_where_asked_and_are_rate_limited(player, transports):
    user = await _with_both(player)
    service = NotificationChannelService()

    results = await service.send_test(user, [Channel.EMAIL, Channel.TELEGRAM])
    assert results == {Channel.EMAIL: True, Channel.TELEGRAM: True}

    for _ in range(ncs.TESTS_PER_WINDOW - 1):
        await service.send_test(user, [Channel.EMAIL])
    with pytest.raises(ValueError, match='3 tests'):
        await service.send_test(user, [Channel.EMAIL])


async def test_a_test_to_an_unset_channel_reports_failure(player):
    results = await NotificationChannelService().send_test(player, ['email'])
    assert results == {Channel.EMAIL: False}


async def test_discord_tests_work_with_the_flag_off_but_email_does_not(player, monkeypatch):
    from application.services.discord.discord_service import DiscordService

    async def fake_dm(self, discord_id, message, **kwargs):
        return True, None

    monkeypatch.setattr(DiscordService, 'send_dm', fake_dm)
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.NOTIFICATION_CHANNELS.value,
    ).update(enabled=False)

    with tenant_scope(DEFAULT_TEST_TENANT_ID):
        results = await NotificationChannelService().send_test(player, [Channel.DISCORD])
        assert results == {Channel.DISCORD: True}
        with pytest.raises(ValueError, match="aren't turned on"):
            await NotificationChannelService().send_test(player, [Channel.DISCORD, Channel.EMAIL])


async def test_the_copy_carries_the_settings_footnote(player, transports):
    from application.utils.discord_messages import DMLink
    from application.utils.settings_footnote import SettingsFootnote

    await _with_both(player)
    footnote = SettingsFootnote('Seed ready', DMLink('Notification settings', 'https://x.test/home/profile'))
    await NotificationChannelService().mirror_dm(700, 'Your seed is ready', footnote=footnote)

    email = transports['email'][-1]
    assert 'Change how you get “Seed ready” messages: https://x.test/home/profile' in email['text']
    assert 'href="https://x.test/home/profile"' in email['html']
    assert transports['telegram'][-1]['text'].endswith(
        '\n\nChange how you get “Seed ready” messages: https://x.test/home/profile'
    )
