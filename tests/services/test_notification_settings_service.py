"""Admin → Notifications: the catalogue, a community's overrides, and send_dm honouring them."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.audit_service import AuditActions
from application.services.notification_catalog import (
    CATALOG,
    GROUPS,
    JOIN_REQUEST,
    MATCH_CONFIRMED,
    MATCH_SCHEDULED,
    PLAYER_COMMENT,
    PLAYERS,
    SECTIONS,
    WATCHERS,
    StaffChoice,
    notices,
    staff_choice_for,
)
from application.services.notification_settings_service import NotificationSettingsService
from models import (
    AuditLog,
    NotificationMode,
    NotificationRecipient,
    NotificationSetting,
    Role,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user


async def _staff(discord_id: int = 7001):
    user = await make_user(discord_id=discord_id, username=f'staff{discord_id}')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


class TestCatalog:
    def test_every_notice_sits_in_a_known_group(self):
        assert {n.group for n in notices()} <= set(GROUPS)

    def test_keys_are_unique_and_match_the_catalog(self):
        keys = [n.key for n in notices()]
        assert len(keys) == len(set(keys))
        assert set(keys) == set(CATALOG)
        assert all(CATALOG[k].key == k for k in CATALOG)

    def test_keys_fit_the_column(self):
        max_length = NotificationSetting._meta.fields_map['notice'].max_length
        assert all(len(k) <= max_length for k in CATALOG)

    def test_every_default_is_a_mode_for_a_recipient(self):
        for notice in notices():
            assert notice.defaults, notice.key
            for recipient, mode in notice.defaults.items():
                assert isinstance(recipient, NotificationRecipient)
                assert isinstance(mode, NotificationMode)
            assert set(notice.who) <= set(notice.defaults), notice.key

    def test_a_target_reads_its_default_and_refuses_a_missing_recipient(self):
        assert MATCH_CONFIRMED.to(WATCHERS).default is NotificationMode.SILENT
        with pytest.raises(KeyError):
            MATCH_CONFIRMED.to(NotificationRecipient.VOLUNTEERS)


class TestSections:
    def test_a_group_with_sections_files_every_message_under_one(self):
        for notice in notices():
            if notice.group in SECTIONS:
                assert notice.section, notice.key
            else:
                assert notice.section == '', notice.key

    def test_sections_hold_only_their_own_group(self):
        for group, sections in SECTIONS.items():
            for _, members in sections:
                assert all(n.group == group for n in members)

    def test_display_order_follows_the_sections(self):
        matches = [n for n in notices() if n.group == 'Matches']
        expected = [n for _, members in SECTIONS['Matches'] for n in members]
        assert matches == expected


class TestStaffChoice:
    def test_every_default_adds_up_to_a_choice(self):
        for notice in notices():
            if notice.staff_recipients:
                assert staff_choice_for(notice, notice.defaults) is not None, notice.key

    def test_ping_on_duty_drops_the_admins_and_keeps_the_fallback(self):
        modes = {
            NotificationRecipient.ON_DUTY: NotificationMode.PING,
            NotificationRecipient.ADMINS: NotificationMode.OFF,
            NotificationRecipient.STAFF: NotificationMode.PING,
        }
        assert staff_choice_for(PLAYER_COMMENT, modes) is StaffChoice.PING_ON_DUTY

    def test_a_mix_no_choice_matches_is_custom(self):
        modes = {
            NotificationRecipient.ON_DUTY: NotificationMode.SILENT,
            NotificationRecipient.ADMINS: NotificationMode.PING,
            NotificationRecipient.STAFF: NotificationMode.PING,
        }
        assert staff_choice_for(PLAYER_COMMENT, modes) is None

    async def test_setting_a_choice_writes_every_staff_recipient(self, db):
        staff = await _staff()

        rows = await NotificationSettingsService.set_staff_choice(staff, 'player_comment', 'ping_on_duty')

        assert {r.recipient: r.mode for r in rows} == {
            NotificationRecipient.ON_DUTY: NotificationMode.PING,
            NotificationRecipient.ADMINS: NotificationMode.OFF,
            NotificationRecipient.STAFF: NotificationMode.PING,
        }
        assert {r.staff_choice for r in rows} == {StaffChoice.PING_ON_DUTY}
        # Only the admins row differs from the default, so only it is stored.
        [setting] = await NotificationSetting.all()
        assert setting.recipient is NotificationRecipient.ADMINS
        assert await NotificationSettingsService.mode_for(
            PLAYER_COMMENT.to(NotificationRecipient.ADMINS),
        ) is NotificationMode.OFF
        [log] = await AuditLog.filter(action=AuditActions.NOTIFICATION_SETTING_CHANGED)
        details = json.loads(log.details)
        assert (details['recipient'], details['old'], details['new']) == (
            'staff_alerts', 'ping_all', 'ping_on_duty',
        )

    async def test_back_to_the_default_choice_clears_the_overrides(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_staff_choice(staff, 'player_comment', 'off')
        assert await NotificationSetting.all().count() == 3

        await NotificationSettingsService.set_staff_choice(staff, 'player_comment', 'ping_all')

        assert await NotificationSetting.all().count() == 0

    async def test_list_reports_the_choice_on_staff_rows_only(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_staff_choice(staff, 'player_comment', 'silent')

        rows = await NotificationSettingsService.list_settings(staff)

        comment = {r.recipient: r.staff_choice for r in rows if r.notice.key == 'player_comment'}
        assert set(comment.values()) == {StaffChoice.SILENT}
        assert all(r.staff_choice is None for r in rows if r.notice.key == 'match_scheduled')

    async def test_a_single_cell_change_can_leave_a_custom_mix(self, db):
        staff = await _staff()
        row = await NotificationSettingsService.set_mode(staff, 'player_comment', 'on_duty', 'silent')
        assert row.staff_choice is None

    @pytest.mark.parametrize('notice, choice', [
        ('match_scheduled', 'ping_all'),       # no staff recipients
        (JOIN_REQUEST.key, 'ping_on_duty'),    # all staff only, no on-shift desk
        ('player_comment', 'loud'),
        ('no_such_notice', 'off'),
    ])
    async def test_a_choice_that_does_not_apply_is_refused(self, db, notice, choice):
        staff = await _staff()
        with pytest.raises(ValueError):
            await NotificationSettingsService.set_staff_choice(staff, notice, choice)
        assert await NotificationSetting.all().count() == 0

    async def test_a_member_who_is_not_staff_is_refused(self, db):
        member = await make_user(discord_id=7102, username='member2')
        with pytest.raises(PermissionError):
            await NotificationSettingsService.set_staff_choice(member, 'player_comment', 'off')


class TestModeFor:
    async def test_no_override_means_the_default(self, db):
        assert await NotificationSettingsService.mode_for(MATCH_SCHEDULED.to(PLAYERS)) is NotificationMode.PING
        assert await NotificationSettingsService.mode_for(MATCH_SCHEDULED.to(WATCHERS)) is NotificationMode.SILENT

    async def test_an_override_wins_once_set(self, db):
        staff = await _staff()
        target = MATCH_CONFIRMED.to(WATCHERS)
        await NotificationSettingsService.mode_for(target)  # warm the cache first

        row = await NotificationSettingsService.set_mode(staff, target.key, 'watchers', 'off')

        assert row.changed and row.mode is NotificationMode.OFF
        assert await NotificationSettingsService.mode_for(target) is NotificationMode.OFF
        assert await NotificationSettingsService.mode_for(MATCH_CONFIRMED.to(PLAYERS)) is NotificationMode.SILENT

    async def test_setting_the_default_again_deletes_the_row(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, NotificationMode.PING)
        assert await NotificationSetting.all().count() == 1

        row = await NotificationSettingsService.set_mode(
            staff, 'match_confirmed', WATCHERS, NotificationMode.SILENT,
        )

        assert not row.changed
        assert await NotificationSetting.all().count() == 0
        assert await NotificationSettingsService.mode_for(MATCH_CONFIRMED.to(WATCHERS)) is NotificationMode.SILENT

    async def test_changing_an_override_updates_its_row(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'off')
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'ping')

        [setting] = await NotificationSetting.all()
        assert setting.mode is NotificationMode.PING
        assert setting.updated_by_id == staff.id
        assert setting.tenant_id == DEFAULT_TEST_TENANT_ID


class TestListAndReset:
    async def test_list_has_every_cell_and_marks_the_changed_one(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_scheduled', 'followers', 'off')

        rows = await NotificationSettingsService.list_settings(staff)

        assert len(rows) == sum(len(n.defaults) for n in notices())
        assert [(r.notice.key, r.recipient) for r in rows if r.changed] == [
            ('match_scheduled', NotificationRecipient.FOLLOWERS),
        ]

    async def test_reset_all_puts_everything_back(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'off')
        await NotificationSettingsService.set_mode(staff, 'match_scheduled', PLAYERS, 'silent')

        assert await NotificationSettingsService.reset_all(staff) == 2

        assert await NotificationSetting.all().count() == 0
        assert await NotificationSettingsService.mode_for(MATCH_CONFIRMED.to(WATCHERS)) is NotificationMode.SILENT
        log = await AuditLog.filter(action=AuditActions.NOTIFICATION_SETTINGS_RESET).get()
        assert json.loads(log.details)['count'] == 2

    async def test_resetting_nothing_writes_no_audit(self, db):
        staff = await _staff()
        assert await NotificationSettingsService.reset_all(staff) == 0
        assert not await AuditLog.filter(action=AuditActions.NOTIFICATION_SETTINGS_RESET).exists()


class TestRefusals:
    async def test_a_member_who_is_not_staff_is_refused(self, db):
        member = await make_user(discord_id=7101, username='member')
        await UserRole.create(user=member, role=Role.PROCTOR, tenant_id=DEFAULT_TEST_TENANT_ID)

        with pytest.raises(PermissionError):
            await NotificationSettingsService.set_mode(member, 'match_confirmed', WATCHERS, 'off')
        with pytest.raises(PermissionError):
            await NotificationSettingsService.list_settings(member)
        with pytest.raises(PermissionError):
            await NotificationSettingsService.reset_all(member)
        with pytest.raises(PermissionError):
            await NotificationSettingsService.list_settings(None)
        assert await NotificationSetting.all().count() == 0

    @pytest.mark.parametrize('notice, recipient, mode', [
        ('no_such_notice', 'players', 'off'),
        ('match_confirmed', 'volunteers', 'off'),
        ('match_confirmed', 'nobody', 'off'),
        ('match_confirmed', 'players', 'loud'),
    ])
    async def test_an_unknown_cell_is_refused(self, db, notice, recipient, mode):
        staff = await _staff()
        with pytest.raises(ValueError):
            await NotificationSettingsService.set_mode(staff, notice, recipient, mode)
        assert await NotificationSetting.all().count() == 0


class TestAudit:
    async def test_a_change_is_audited_with_before_and_after(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'off')

        log = await AuditLog.filter(action=AuditActions.NOTIFICATION_SETTING_CHANGED).get()
        assert log.user_id == staff.id
        details = json.loads(log.details)
        assert {k: details[k] for k in ('notice', 'title', 'recipient', 'old', 'new')} == {
            'notice': 'match_confirmed', 'title': MATCH_CONFIRMED.title,
            'recipient': 'watchers', 'old': 'silent', 'new': 'off',
        }

    async def test_choosing_the_current_mode_writes_nothing(self, db):
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'silent')
        assert not await AuditLog.filter(action=AuditActions.NOTIFICATION_SETTING_CHANGED).exists()


class TestSendDmHonoursTheSetting:
    """``send_dm(notice=...)`` is where the setting takes effect, real and mock alike."""

    @pytest.fixture
    def mirrors(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            'application.services.discord.discord_service._mirror_dm_to_web_push',
            lambda *a, **kw: calls.append(('push', kw.get('silent'))),
        )
        monkeypatch.setattr(
            'application.services.discord.discord_service._mirror_dm_to_channels',
            lambda *a, **kw: calls.append(('channels', kw.get('silent'))),
        )
        return calls

    @pytest.fixture
    def real_svc(self):
        from application.services.discord.discord_service import _RealDiscordService

        recipient = SimpleNamespace(send=AsyncMock(
            return_value=SimpleNamespace(id=2, channel=SimpleNamespace(id=1)),
        ))
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_user.return_value = recipient
        svc = object.__new__(_RealDiscordService)
        svc._bot = bot
        return svc, recipient

    async def test_off_sends_nothing_anywhere(self, db, mirrors, real_svc):
        from application.services.discord.discord_service import DM_NOTICE_OFF

        svc, recipient = real_svc
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'off')

        result = await svc.send_dm(555, 'hi', notice=MATCH_CONFIRMED.to(WATCHERS))

        assert result == (True, DM_NOTICE_OFF)
        recipient.send.assert_not_awaited()
        assert mirrors == []

    async def test_silent_default_goes_out_silent(self, db, mirrors, real_svc):
        svc, recipient = real_svc

        ok, _ = await svc.send_dm(555, 'hi', notice=MATCH_CONFIRMED.to(WATCHERS))

        assert ok
        assert recipient.send.await_args.kwargs['silent'] is True
        assert mirrors == [('push', True), ('channels', True)]

    async def test_the_setting_overrides_the_silent_argument(self, db, mirrors, real_svc):
        svc, recipient = real_svc
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_confirmed', WATCHERS, 'ping')

        await svc.send_dm(555, 'hi', silent=True, notice=MATCH_CONFIRMED.to(WATCHERS))

        assert recipient.send.await_args.kwargs['silent'] is False

    async def test_mock_off_skips_and_silent_is_marked(self, db, mirrors, capsys):
        from application.services.discord.discord_service import DM_NOTICE_OFF, MockDiscordService

        svc = MockDiscordService()
        staff = await _staff()
        await NotificationSettingsService.set_mode(staff, 'match_scheduled', PLAYERS, 'off')

        assert await svc.send_dm(555, 'booked', notice=MATCH_SCHEDULED.to(PLAYERS)) == (True, DM_NOTICE_OFF)
        assert mirrors == []
        assert '[MOCK Discord DM] -> 555: booked' not in capsys.readouterr().out

        ok, _ = await svc.send_dm(555, 'confirmed', notice=MATCH_CONFIRMED.to(WATCHERS))
        assert ok
        assert mirrors == [('channels', True)]
        assert '[MOCK Discord DM] -> 555: confirmed [silent]' in capsys.readouterr().out
