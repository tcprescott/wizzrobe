"""Pre-match stage reminder loop.

A runner told hours ago that they're on Kraid has played two matches since. This
worker sends one more DM shortly before the match, naming the stage and the
time, to the players and approved crew of every match that has one.

Modelled on ``application/services/volunteer/volunteer_reminder.py``, which
solves the same problem for a shift: a cross-tenant scan over a generous fixed
window, then a per-row re-check against that row's own configured lead. The lead
here is per *tournament* (``Tournament.stage_reminder_minutes``) rather than per
tenant, because a Best-of-NES stage call and an OOTR stage call are not the same
problem.

Publishes no event. The reminder observes a match nobody changed, so there is no
fact to record beyond the stamp itself.
"""

import logging
from datetime import datetime, timedelta, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

# How often the loop wakes up to look for due reminders.
TICK_SECONDS = 60

# Bound on the cross-tenant scan. The loop cannot know each tournament's lead
# before it has the rows, so it scans wide and re-checks per match. A tournament
# configured beyond this is warned about rather than silently dropped, and its
# matches are still reminded once they enter the window. A day is generous: a
# stage call further out than that is a schedule, not a reminder.
MAX_LEAD_MINUTES = 24 * 60


async def _tick() -> None:
    from application.repositories import MatchRepository
    from application.services.discord import discord_queue
    from application.services.discord.discord_service import discord_bot_down
    from application.services.match.match_schedule_service import MatchScheduleService

    if discord_bot_down():
        # Reminders are stamped before their DM is queued; wait for the bot so
        # they go out after a reconnect rather than being stamped and lost.
        return

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(minutes=MAX_LEAD_MINUTES)
    due = await MatchRepository.due_for_stage_reminder(now, window_end)
    if not due:
        return

    schedule_service = MatchScheduleService()

    async def _remind(match) -> None:
        lead = match.tournament.stage_reminder_minutes or 0
        if lead <= 0:
            return  # this tournament sends no stage reminder
        if lead > MAX_LEAD_MINUTES:
            logger.warning(
                'Tournament %s stage_reminder_minutes=%s exceeds the %s-minute scan '
                'window; its matches are reminded only once they enter the window.',
                match.tournament_id, lead, MAX_LEAD_MINUTES,
            )
        # Not yet within this tournament's lead — leave un-stamped so a later
        # tick reminds it once it enters the window.
        if match.scheduled_at > now + timedelta(minutes=lead):
            return

        # Stamp first so a delivery failure (or a restart) can't re-fire it.
        match.stage_reminder_sent_at = now
        await match.save(update_fields=['stage_reminder_sent_at'])

        # ``for_each_tenant_scoped`` has this match's tenant bound and
        # ``discord_queue.enqueue`` captures it for the queue worker, so the
        # DM's link resolves to the right community when it is finally sent.
        discord_queue.enqueue(schedule_service.notify_stage_reminder(
            match, stage_name=match.stage.name if match.stage else '',
        ))

    await for_each_tenant_scoped(
        due,
        _remind,
        tenant_id_of=lambda match: match.tenant_id,  # type: ignore[attr-defined]
        event_id_of=lambda match: match.event_id,
        logger=logger,
        describe=lambda match: f'match {getattr(match, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'stage reminder', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
