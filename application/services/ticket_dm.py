"""The DMs ticket sales sends: badges bought, a badge claimed, a comp refund.

Each one is best-effort and queued: a Discord outage never undoes a payment,
a claim or a refund. Each carries a link button to the person's badges.
"""

import logging

from application.services.notification_catalog import (
    COMP_REFUND_SENT,
    MEMBERS,
    TICKET_CLAIMED,
    TICKETS_BOUGHT,
    NoticeTarget,
)
from models import User

logger = logging.getLogger(__name__)

MY_BADGES_PATH = '/me/tickets'


def money(cents: int, currency: str) -> str:
    symbol = {'usd': '$', 'cad': 'CA$', 'aud': 'A$', 'eur': '€', 'gbp': '£'}.get(currency.lower())
    amount = f'{cents / 100:,.2f}'.removesuffix('.00')
    return f'{symbol}{amount}' if symbol else f'{amount} {currency.upper()}'


async def _send(user: User, title: str, body: str, notice: NoticeTarget) -> None:
    if not user.discord_id:
        return
    try:
        from application.services import notification_links
        from application.services.discord import DiscordService, discord_queue
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import COLOR_CONFIRMED, notification_embed

        community = await TenantService.current_community_name()
        embed = notification_embed(title=title, color=COLOR_CONFIRMED, community_name=community, description=body)
        link = await notification_links.link_for('Your badges', MY_BADGES_PATH)
        discord_queue.enqueue(DiscordService().send_dm(
            int(user.discord_id), body, embed=embed, link=link, notice=notice,
        ))
    except Exception:
        logger.exception('ticket DM to user %s failed', user.id)


async def tickets_bought(buyer: User, count: int, total: str) -> None:
    extra = count - 1
    body = f'Your badge is paid ({total}). Show its QR code at the check-in desk.'
    if extra:
        body += (f" You've also got {extra} extra badge{'s' if extra > 1 else ''} to hand out: "
                 'copy each claim link from your badges and send it to the person it\'s for.')
    await _send(buyer, '🎟️ Badges bought', body, TICKETS_BOUGHT.to(MEMBERS))


async def ticket_claimed(buyer: User, claimer_name: str, type_name: str) -> None:
    await _send(buyer, '🎟️ Badge claimed', f'{claimer_name} claimed the {type_name} you sent them.',
                TICKET_CLAIMED.to(MEMBERS))


async def comp_refund_sent(owner: User, type_name: str, amount: str, provider_label: str) -> None:
    body = (f'Your {type_name} is comped, so {amount} is on its way back to you through {provider_label}. '
            'Your badge still works as before.')
    await _send(owner, '🎟️ Comp refund sent', body, COMP_REFUND_SENT.to(MEMBERS))
