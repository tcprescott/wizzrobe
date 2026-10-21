"""Dev seed: the Sent messages log (see application/services/sent_message_service.py)."""

from datetime import datetime, timedelta, timezone

from models import SentMessage, SentMessageStatus, Tenant, User

# (recipient key, title, body, status, error, silent, push devices, link label, notice, audience)
_ROWS = (
    ('player_one', 'Your match is ready to book',
     'Your match is ready to book: Round 3 vs player_two. Pick a time before Thursday 6:00 PM.',
     SentMessageStatus.DELIVERED, None, True, 1, 'Pick a time', 'matchup_ready', 'players'),
    ('player_two', 'Stage call: Stage 2 in 10 minutes',
     'Stage call: your match is on Stage 2 in 10 minutes. Head there now.',
     SentMessageStatus.DELIVERED, None, False, None, 'View match', 'stage_call', 'players'),
    ('staff_user', 'New reschedule request',
     'player_two asked to move their Round 2 match to Friday 11:00 AM.',
     SentMessageStatus.BLOCKED, 'Cannot send DM to this user (DMs may be disabled)', False, 1,
     'Review request', 'reschedule_request', 'staff'),
    ('proctor_user', 'Match checked in',
     'Match checked in. Seats: player_one: A1 · player_two: A2',
     SentMessageStatus.DISCORD_OFF, None, False, None, 'View match', 'match_checked_in', 'crew'),
    ('sm_user', 'Your shift starts in 30 minutes',
     "Check-in desk, 1:00 PM to 3:00 PM. Tap I'm here when you arrive.",
     SentMessageStatus.FAILED, "Discord didn't answer in time. Please try again in a moment.",
     False, None, 'Open shift', 'shift_reminder', 'volunteers'),
    ('staff_user', 'Shift reminder',
     'Your Admin Desk shift starts at 2:00 PM. Tap I\'m here when you arrive.',
     SentMessageStatus.DELIVERED, None, False, 1, 'Your shifts', 'shift_reminder', 'volunteers'),
    ('player_one', 'Match scheduled',
     'Match scheduled: Round 2 vs player_two, Thursday 2:00 PM.',
     SentMessageStatus.PENDING, None, True, None, 'View match', 'match_scheduled', 'players'),
)


async def seed_sent_messages(tenant: Tenant, users: dict[str, User]) -> None:
    """A handful of logged DMs in every state, spread over the last hour."""
    now = datetime.now(timezone.utc)
    for n, (who, title, body, status, error, silent, devices, label, notice, audience) in enumerate(_ROWS):
        user = users[who]
        row, created = await SentMessage.get_or_create(
            tenant=tenant, recipient=user, title=title,
            defaults={
                'discord_id': str(user.discord_id or 0), 'body': body, 'status': status,
                'error': error, 'silent': silent, 'push_devices': devices,
                'link_label': label, 'link_url': f'http://localhost:8000/t/{tenant.slug}/home',
                'embed': {'title': title, 'description': body, 'color': 0x3BA55C},
                'notice_key': notice, 'audience': audience,
            },
        )
        if created:
            # auto_now_add stamps "now"; spread them out so the log reads like a feed.
            await SentMessage.filter(id=row.id).update(created_at=now - timedelta(minutes=7 * n))
