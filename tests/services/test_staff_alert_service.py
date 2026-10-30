"""Who a match alert reaches: the admin on duty and the tournament's admins.

Player comments and reschedule requests used to ping every staff member. Now
they go to whoever is on an alert-receiving shift right now plus the
tournament's admins (minus those who opted out), falling back to all staff when
nobody is on duty.
"""

from datetime import timedelta

import pytest

import application.services.discord as discord_pkg
from application.services import MatchCommentService, StaffAlertService
from application.services.notification_catalog import ADMINS, ON_DUTY, STAFF
from application.tenant_context import tenant_scope
from models import (
    Match,
    MatchPlayers,
    Role,
    Tenant,
    Tournament,
    TournamentNotificationPreference,
    UserRole,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.factories import make_user, utc

NOW = utc(2026, 10, 22, 15, 0)


@pytest.fixture
def dms(monkeypatch):
    pending = []
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', pending.append)
    yield pending
    for coro in pending:
        coro.close()


async def _staff(discord_id: int, username: str):
    user = await make_user(discord_id=discord_id, username=username)
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=1)
    return user


async def _on_shift(user, *, staff_alerts=True, start=NOW - timedelta(hours=1),
                    end=NOW + timedelta(hours=1), published=True, name='Admin Desk'):
    position, _ = await VolunteerPosition.get_or_create(
        name=name, tenant_id=1, defaults={'staff_alerts': staff_alerts},
    )
    shift = await VolunteerShift.create(position=position, starts_at=start, ends_at=end)
    await VolunteerAssignment.create(shift=shift, user=user, auto_generated=not published)
    return shift


async def _match():
    t = await Tournament.create(name='ALTTPR')
    m = await Match.create(tournament=t, scheduled_at=NOW)
    p1 = await make_user(discord_id=8001, username='p1')
    p2 = await make_user(discord_id=8002, username='p2')
    await MatchPlayers.create(match=m, user=p1)
    await MatchPlayers.create(match=m, user=p2)
    return t, m, p1, p2


def _names(users):
    return sorted(u.username for u in users)


class TestRecipients:
    async def test_the_on_duty_admin_gets_it_not_all_staff(self, db):
        _, m, _, _ = await _match()
        desk = await _staff(9001, 'desk')
        await _staff(9002, 'other')
        await _on_shift(desk)

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['desk']

    async def test_nobody_on_duty_falls_back_to_all_staff(self, db):
        _, m, _, _ = await _match()
        await _staff(9001, 'a')
        await _staff(9002, 'b')

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['a', 'b']

    async def test_a_shift_that_ended_or_has_not_started_does_not_count(self, db):
        _, m, _, _ = await _match()
        early = await _staff(9001, 'early')
        late = await _staff(9002, 'late')
        await _on_shift(early, start=NOW - timedelta(hours=3), end=NOW)
        await _on_shift(late, start=NOW + timedelta(minutes=1), end=NOW + timedelta(hours=2))

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['early', 'late']

    async def test_only_positions_with_the_switch_count(self, db):
        _, m, _, _ = await _match()
        desk = await _staff(9001, 'desk')
        tech = await _staff(9002, 'tech')
        await _on_shift(desk)
        await _on_shift(tech, staff_alerts=False, name='Broadcast Tech')

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['desk']

    async def test_a_draft_assignment_does_not_count(self, db):
        _, m, _, _ = await _match()
        desk = await _staff(9001, 'desk')
        other = await _staff(9002, 'other')
        await _on_shift(desk, published=False)

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == _names([desk, other])

    async def test_tournament_admins_are_added_unless_opted_out(self, db):
        t, m, _, _ = await _match()
        desk = await _staff(9001, 'desk')
        await _on_shift(desk)
        ta_on = await make_user(discord_id=9101, username='ta_on')
        ta_off = await make_user(discord_id=9102, username='ta_off')
        await t.admins.add(ta_on, ta_off)
        await TournamentNotificationPreference.create(user=ta_off, tournament=t, staff_alerts=False)

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['desk', 'ta_on']

    async def test_a_tournament_admin_playing_in_the_match_is_skipped(self, db):
        t, m, p1, p2 = await _match()
        desk = await _staff(9001, 'desk')
        await _on_shift(desk)
        await t.admins.add(p2)

        got = await StaffAlertService.match_alert_recipients(m, exclude=[p1.id], at=NOW)

        assert _names(got) == ['desk']

    async def test_a_staff_member_playing_in_the_match_is_skipped_on_fallback(self, db):
        _, m, p1, p2 = await _match()
        await UserRole.create(user=p2, role=Role.STAFF, tenant_id=1)
        await _staff(9001, 'other')

        got = await StaffAlertService.match_alert_recipients(m, exclude=[p1.id], at=NOW)

        assert _names(got) == ['other']

    async def test_a_staff_admin_standing_in_for_an_empty_desk_is_tagged_staff(self, db):
        t, m, _, _ = await _match()
        boss = await _staff(9001, 'boss')
        await t.admins.add(boss)

        tagged = await StaffAlertService.tagged_match_alert_recipients(m, at=NOW)

        assert [(u.username, why) for u, why in tagged] == [('boss', STAFF)]

    async def test_a_staff_admin_stays_an_admin_while_someone_is_on_duty(self, db):
        t, m, _, _ = await _match()
        desk = await _staff(9001, 'desk')
        boss = await _staff(9002, 'boss')
        await t.admins.add(boss)
        await _on_shift(desk)

        tagged = dict((u.username, why) for u, why in await StaffAlertService.tagged_match_alert_recipients(m, at=NOW))

        assert tagged == {'desk': ON_DUTY, 'boss': ADMINS}

    async def test_an_on_duty_volunteer_who_cannot_act_is_skipped(self, db):
        _, m, _, _ = await _match()
        volunteer = await make_user(discord_id=9001, username='plain')
        await _staff(9002, 'boss')
        await _on_shift(volunteer)

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['boss']

    async def test_another_communitys_shift_never_counts(self, db):
        _, m, _, _ = await _match()
        await _staff(9001, 'home')
        other_tenant = await Tenant.create(slug='other', name='Other')
        outsider = await make_user(discord_id=9002, username='outsider')
        await UserRole.create(user=outsider, role=Role.STAFF, tenant_id=other_tenant.id)
        with tenant_scope(other_tenant.id):
            position = await VolunteerPosition.create(name='Admin Desk', tenant_id=other_tenant.id, staff_alerts=True)
            shift = await VolunteerShift.create(
                position=position, starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(hours=1),
                tenant_id=other_tenant.id,
            )
            await VolunteerAssignment.create(shift=shift, user=outsider, tenant_id=other_tenant.id)

        got = await StaffAlertService.match_alert_recipients(m, at=NOW)

        assert _names(got) == ['home']


class TestOptOut:
    async def test_a_tournament_admin_can_switch_alerts_off_and_on(self, db):
        t, _, _, _ = await _match()
        ta = await make_user(discord_id=9101, username='ta')
        await t.admins.add(ta)

        assert await StaffAlertService.admin_tournaments(ta) == [(t, True)]
        await StaffAlertService.set_tournament_alerts(ta, t.id, False)
        [(got, on)] = await StaffAlertService.admin_tournaments(ta)
        assert (got.id, on) == (t.id, False)
        await StaffAlertService.set_tournament_alerts(ta, t.id, True)
        [(_, on)] = await StaffAlertService.admin_tournaments(ta)
        assert on is True

    async def test_someone_who_does_not_run_it_is_refused(self, db):
        t, _, _, _ = await _match()
        stranger = await make_user(discord_id=9103, username='stranger')

        with pytest.raises(ValueError, match='tournaments you run'):
            await StaffAlertService.set_tournament_alerts(stranger, t.id, False)

    async def test_opting_out_keeps_the_follow_level(self, db):
        t, _, _, _ = await _match()
        ta = await make_user(discord_id=9101, username='ta')
        await t.admins.add(ta)
        await TournamentNotificationPreference.create(user=ta, tournament=t, match_notifications='all')

        await StaffAlertService.set_tournament_alerts(ta, t.id, False)

        pref = await TournamentNotificationPreference.get(user=ta, tournament=t)
        assert (pref.match_notifications.value, pref.staff_alerts) == ('all', False)


class TestPlayerCommentDM:
    async def test_a_player_comment_dms_the_on_duty_admin_and_tournament_admin(self, db, dms, monkeypatch):
        t, m, p1, _ = await _match()
        desk = await _staff(9001, 'desk')
        await _staff(9002, 'resting')
        ta = await make_user(discord_id=9101, username='ta')
        await t.admins.add(ta)
        await _on_shift(desk, start=utc(2020, 1, 1), end=utc(2100, 1, 1))

        sent = []

        async def fake_notify(**kwargs):
            sent.append(kwargs)

        monkeypatch.setattr(
            'application.services.match._comment_notifications.notify_staff_of_player_comment', fake_notify,
        )
        await MatchCommentService().add_player_comment(m.id, p1, "Running late, train's delayed")

        assert len(dms) == 1
        await dms.pop()
        [call] = sent
        tagged = sorted((u.username, r) for u, r in call['recipients'])
        assert tagged == [('desk', ON_DUTY), ('ta', ADMINS)]
        assert call['body'] == "Running late, train's delayed"
        assert call['match_id'] == m.id

    async def test_the_staff_copy_escapes_player_markdown_and_mentions(self):
        from application.utils.discord_messages import player_comment_staff_dm

        text = player_comment_staff_dm('**Eve**', 'ALTTPR', '[click](https://evil.example) @everyone')

        assert '\\[click]' in text
        assert '@everyone' not in text
        assert '\\*\\*Eve\\*\\*' in text


class TestDutySummary:
    async def test_on_duty_now_with_a_later_shift_next(self, db):
        desk = await _staff(9001, 'desk')
        await _on_shift(desk)
        later = await _on_shift(desk, start=NOW + timedelta(hours=5), end=NOW + timedelta(hours=7))

        summary = await StaffAlertService.duty_summary(desk, at=NOW)

        assert summary.position_name == 'Admin Desk'
        assert summary.on_now is True
        assert summary.next_start == later.starts_at

    async def test_nothing_for_someone_without_duty_shifts(self, db):
        tech = await _staff(9002, 'tech')
        await _on_shift(tech, staff_alerts=False, name='Broadcast Tech')

        assert await StaffAlertService.duty_summary(tech, at=NOW) is None
