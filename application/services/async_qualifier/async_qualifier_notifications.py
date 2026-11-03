"""Async qualifier — the runner-facing Discord DMs.

Best-effort by construction: a DM must never block a review or a granted
reattempt, so every send is wrapped and swallowed here rather than at each call
site. The message text itself lives in
:mod:`application.utils.discord_messages`, per that module's rule that no service
inlines DM copy.

Split out of :class:`AsyncQualifierService` to keep that module inside the
file-length guideline; it is a service-layer peer, so it may reach the ORM and
the Discord service.
"""

import logging
from datetime import datetime
from typing import Iterable, Optional

from application.services import notification_links
from application.services.notification_catalog import (
    ADMINS,
    PLAYERS,
    REATTEMPT_GRANTED,
    REVIEW_QUEUE,
    RUN_APPROVED,
    RUN_EXPIRED,
    RUN_EXPIRING,
    RUN_REJECTED,
)
from application.utils.app_links import admin_qualifier_queue_url, qualifier_runs_url, qualifier_url
from application.utils.discord_embeds import time_field
from application.utils.discord_messages import DMLink
from application.utils.discord_messages_qualifier import (
    qualifier_reattempt_granted_dm,
    qualifier_review_queue_dm,
    qualifier_run_expired_dm,
    qualifier_run_expiring_dm,
    qualifier_run_reviewed_dm,
)
from application.utils.tenant_urls import event_path, tenant_url
from models import AsyncQualifier, AsyncQualifierRun, User

logger = logging.getLogger(__name__)


async def notify_run_reviewed(
    run: AsyncQualifierRun, approved: bool, reason: str = '', *, overridden: bool = False
) -> None:
    """Tell the runner the verdict, the reason behind it, and where to look."""
    try:
        await run.fetch_related('user', 'qualifier', 'tenant')
        discord_id = run.user.discord_id
        if not discord_id or run.user.is_placeholder:
            return
        from application.services.discord.discord_service import DiscordService
        await DiscordService().send_dm(
            int(discord_id),
            qualifier_run_reviewed_dm(
                run.qualifier.name, approved=approved, reason=reason,
                qualifier_url=_qualifier_url(run, runs=True), overridden=overridden,
            ),
            link=notification_links.player_qualifier_runs(run.tenant, run.qualifier_id),  # type: ignore[attr-defined]
            notice=(RUN_APPROVED if approved else RUN_REJECTED).to(PLAYERS),
        )
    except Exception:
        logger.warning("Failed to DM run-reviewed notification", exc_info=True)


async def notify_reattempt_granted(run: AsyncQualifierRun, reason: str) -> None:
    """Tell the runner a reviewer freed their pool slot — a change they did not make."""
    try:
        await run.fetch_related('user', 'qualifier', 'tenant', 'permalink__pool')
        discord_id = run.user.discord_id
        if not discord_id or run.user.is_placeholder:
            return
        pool = run.permalink.pool.name if run.permalink and run.permalink.pool else ''
        from application.services.discord.discord_service import DiscordService
        await DiscordService().send_dm(
            int(discord_id),
            qualifier_reattempt_granted_dm(
                run.qualifier.name, pool, reason=reason, qualifier_url=_qualifier_url(run),
            ),
            link=_qualifier_link(run, 'Start your next run'), notice=REATTEMPT_GRANTED.to(PLAYERS),
        )
    except Exception:
        logger.warning("Failed to DM reattempt-granted notification", exc_info=True)


async def notify_run_expiring(run: AsyncQualifierRun, deadline: datetime) -> None:
    """Warn the runner once, before the worker forfeits their open run."""
    try:
        await run.fetch_related('user', 'qualifier', 'tenant')
        discord_id = run.user.discord_id
        if not discord_id or run.user.is_placeholder:
            return
        from application.services.discord.discord_service import DiscordService
        await DiscordService().send_dm(
            int(discord_id),
            qualifier_run_expiring_dm(
                run.qualifier.name,
                deadline_display=time_field(deadline),
                qualifier_url=_qualifier_url(run),
            ),
            link=_qualifier_link(run, 'Submit or forfeit'), notice=RUN_EXPIRING.to(PLAYERS),
        )
    except Exception:
        logger.warning("Failed to DM run-expiring notification", exc_info=True)


async def notify_run_expired(run: AsyncQualifierRun, limit_hours: int) -> None:
    """Tell the runner their open run was forfeited, and that it is appealable."""
    try:
        await run.fetch_related('user', 'qualifier', 'tenant')
        discord_id = run.user.discord_id
        if not discord_id or run.user.is_placeholder:
            return
        from application.services.discord.discord_service import DiscordService
        await DiscordService().send_dm(
            int(discord_id),
            qualifier_run_expired_dm(
                run.qualifier.name, hours=limit_hours,
                qualifier_url=_qualifier_url(run, runs=True),
            ),
            link=notification_links.player_qualifier_runs(run.tenant, run.qualifier_id),  # type: ignore[attr-defined]
            notice=RUN_EXPIRED.to(PLAYERS),
        )
    except Exception:
        logger.warning("Failed to DM run-expired notification", exc_info=True)


def _qualifier_url(run: AsyncQualifierRun, *, runs: bool = False) -> str:
    if not run.tenant:
        return ''
    qualifier_id = run.qualifier_id  # type: ignore[attr-defined]
    path = qualifier_runs_url(qualifier_id) if runs else qualifier_url(qualifier_id)
    return tenant_url(run.tenant, path)


async def notify_review_queue_waiting(
    reviewers: Iterable[User],
    qualifier: AsyncQualifier,
    *,
    waiting: int,
    oldest_hours: int,
) -> int:
    """Tell each reviewer the queue has a backlog. Returns how many DMs went out.

    The one notification on this surface aimed at staff rather than a runner, and
    the reason the queue stopped being pull-only. A reviewer with no Discord id is
    skipped silently — the Reviewers tab badges them so somebody can notice.
    """
    # Don't drop this: a qualifier loaded without tenant prefetched leaves a QuerySet and the link breaks.
    await qualifier.fetch_related('tenant')
    # Pass the event: a bare link opens the default event's queue, which may not hold this qualifier.
    slug = await notification_links.event_slug(qualifier.tenant_id, qualifier.event_id)  # type: ignore[attr-defined]
    path = event_path(slug, admin_qualifier_queue_url(qualifier.id))
    link = notification_links.link_for_tenant(qualifier.tenant, 'Open the review queue', path)
    url = tenant_url(qualifier.tenant, path) if qualifier.tenant else ''
    message = qualifier_review_queue_dm(
        qualifier.name, waiting=waiting, oldest_hours=oldest_hours, queue_url=url,
    )
    sent = 0
    from application.services.discord.discord_service import DiscordService
    service = DiscordService()
    for reviewer in reviewers:
        if not reviewer.discord_id or reviewer.is_placeholder:
            continue
        try:
            ok, _err = await service.send_dm(int(reviewer.discord_id), message, link=link, notice=REVIEW_QUEUE.to(ADMINS))
        except Exception:
            logger.warning("Failed to DM review-queue notification", exc_info=True)
            continue
        if ok:
            sent += 1
        else:
            logger.warning("Review-queue DM to %s failed: %s", reviewer.discord_id, _err)
    return sent


def _qualifier_link(run: AsyncQualifierRun, label: str) -> Optional[DMLink]:
    """The button on a qualifier DM: the qualifier's own page.

    Built from the loaded ``run.tenant`` rather than the ambient scope — these
    fire from the expiry worker as well as from a review, and the worker holds
    the row already.
    """
    return notification_links.player_qualifier(
        run.tenant, run.qualifier_id, label=label,  # type: ignore[attr-defined]
    )
