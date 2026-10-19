"""Who hears about a match alert: the admin on duty and the tournament's admins.

A *match alert* is a staff-facing DM about one match that somebody has to pick
up: a player's comment, a reschedule request. It used to go to every STAFF
member at once, so during an event a dozen people were pinged for each one.

Now it goes to

* whoever holds a published shift, right now, on a volunteer position with
  ``staff_alerts`` on (sgl26's Admin Desk), and
* the tournament's admins, minus any who switched alerts off for it.

If nobody is on such a shift (a community that never set the switch, or a gap
in the roster) the alert falls back to every STAFF member, so it is never lost.
Community-wide requests (join, ADA, community events) are not match alerts and
still go to all staff. A volunteer running late or giving a shift back goes to
the volunteer coordinators plus the same on-duty set, with the same fallback.
"""

from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

from application.errors import require_found
from application.repositories import (
    TournamentNotificationRepository,
    TournamentRepository,
    UserRoleRepository,
    VolunteerAssignmentRepository,
)
from application.services.auth_service import AuthService
from models import Match, NotificationRecipient, Role, Tournament, User

#: May this person act on the alert? A DM that links somewhere the reader
#: cannot act is worse than none, so recipients are filtered by it.
CanAct = Callable[[User, Match], Awaitable[bool]]
#: A recipient and the notification setting that governs their copy.
Tagged = Tuple[User, NotificationRecipient]


class DutySummary(NamedTuple):
    """A person's alert-receiving shifts, for their profile."""

    position_name: str
    on_now: bool
    next_start: Optional[datetime]


class StaffAlertService:
    """Resolve match-alert recipients and a tournament admin's opt-out."""

    @staticmethod
    async def on_duty_now(at: Optional[datetime] = None) -> List[User]:
        """Who is on an alert-receiving shift at ``at`` (default: now)."""
        return await VolunteerAssignmentRepository.on_duty_users(at or datetime.now(timezone.utc))

    @staticmethod
    async def match_alert_recipients(
        match: Match,
        *,
        can_act: CanAct = AuthService.can_run_match,
        exclude: Iterable[int] = (),
        at: Optional[datetime] = None,
    ) -> List[User]:
        """The on-duty admins plus the tournament's opted-in admins.

        ``exclude`` drops user ids (the comment's author, the requester).
        Falls back to every STAFF member when nobody who can act is on duty.
        """
        tagged = await StaffAlertService.tagged_match_alert_recipients(
            match, can_act=can_act, exclude=exclude, at=at,
        )
        return [user for user, _ in tagged]

    @staticmethod
    async def tagged_match_alert_recipients(
        match: Match,
        *,
        can_act: CanAct = AuthService.can_run_match,
        exclude: Iterable[int] = (),
        at: Optional[datetime] = None,
    ) -> List[Tagged]:
        """:meth:`match_alert_recipients`, each tagged with why they hear it.

        The tag picks which notification setting applies: someone on duty is
        ``ON_DUTY`` even when they also admin the tournament.
        """
        skip = set(exclude)
        recipients: Dict[int, Tagged] = {}

        async def add(user: User, why: NotificationRecipient) -> None:
            if user.id in skip or user.id in recipients or not user.is_active:
                return
            if await can_act(user, match):
                recipients[user.id] = (user, why)

        for user in await StaffAlertService.on_duty_now(at):
            await add(user, NotificationRecipient.ON_DUTY)
        nobody_on_duty = not recipients

        # Admins before the fallback, so a staff member who also admins the
        # tournament reads as an admin and follows that column's setting.
        await match.fetch_related('tournament__admins')
        tournament = match.tournament
        if tournament is not None:
            opted_out = await TournamentNotificationRepository().staff_alert_opt_out_user_ids(tournament.id)
            for admin in tournament.admins:
                if admin.id not in opted_out:
                    await add(admin, NotificationRecipient.ADMINS)
        if nobody_on_duty:
            for user in await UserRoleRepository.list_users_with_role(Role.STAFF):
                await add(user, NotificationRecipient.STAFF)
        return list(recipients.values())

    @staticmethod
    async def volunteer_alert_recipients(at: Optional[datetime] = None) -> List[User]:
        """Who hears that a volunteer is running late or gave a shift back.

        The volunteer coordinators, plus whoever is on duty and can manage the
        roster; every STAFF member stands in for the on-duty set when that is
        empty.
        """
        return [user for user, _ in await StaffAlertService.tagged_volunteer_alert_recipients(at)]

    @staticmethod
    async def tagged_volunteer_alert_recipients(at: Optional[datetime] = None) -> List[Tagged]:
        """:meth:`volunteer_alert_recipients`, each tagged with why they hear it."""
        recipients: Dict[int, Tagged] = {}
        for user in await StaffAlertService.on_duty_now(at):
            if user.is_active and await AuthService.can_manage_volunteers(user):
                recipients.setdefault(user.id, (user, NotificationRecipient.ON_DUTY))
        nobody_on_duty = not recipients
        for user in await UserRoleRepository.list_users_with_role(Role.VOLUNTEER_COORDINATOR, active_only=True):
            recipients.setdefault(user.id, (user, NotificationRecipient.ADMINS))
        if nobody_on_duty:
            for user in await UserRoleRepository.list_users_with_role(Role.STAFF, active_only=True):
                recipients.setdefault(user.id, (user, NotificationRecipient.STAFF))
        return list(recipients.values())

    @staticmethod
    async def duty_summary(user: User, at: Optional[datetime] = None) -> Optional[DutySummary]:
        """Whether ``user`` is on duty now and when their next duty shift starts.

        ``None`` when they hold no current or upcoming alert-receiving shift.
        """
        now = at or datetime.now(timezone.utc)
        assignments = await VolunteerAssignmentRepository.alert_shifts_for_user(user.id, now)
        if not assignments:
            return None
        shifts = [a.shift for a in assignments]
        on_now = any(s.starts_at <= now for s in shifts)
        upcoming = [s.starts_at for s in shifts if s.starts_at > now]
        return DutySummary(
            position_name=shifts[0].position.name,
            on_now=on_now,
            next_start=upcoming[0] if upcoming else None,
        )

    @staticmethod
    async def admin_tournaments(user: User) -> List[tuple[Tournament, bool]]:
        """The active tournaments ``user`` admins, each with whether alerts are on."""
        tournaments = await TournamentRepository.active_admin_tournaments(user)
        if not tournaments:
            return []
        prefs = {
            p.tournament_id: p.staff_alerts  # type: ignore[attr-defined]
            for p in await TournamentNotificationRepository().get_all_for_user(user)
        }
        return [(t, prefs.get(t.id, True)) for t in tournaments]

    @staticmethod
    async def set_tournament_alerts(user: User, tournament_id: int, on: bool) -> None:
        """Switch one tournament's match alerts on or off for one of its admins."""
        tournament = require_found(
            await TournamentRepository().get_by_id(tournament_id), f'Tournament {tournament_id}',
        )
        if not await AuthService.is_tournament_admin(user, tournament.id):
            raise ValueError("You only get alerts for tournaments you run.")
        await TournamentNotificationRepository().set_staff_alerts(user, tournament, on)
