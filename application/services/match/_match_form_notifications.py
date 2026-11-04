"""The "Before your match" DM: a match's form has questions for this player.

Sent with the scheduling fan-out, once per player who has something to answer.
The text names how many questions and never what anyone answered, so a hidden
question stays hidden.
"""

import logging

from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import MATCH_FORM_ASKED, PLAYERS
from application.utils.discord_embeds import COLOR_SEED, match_embed
from models import Match

logger = logging.getLogger(__name__)


def match_form_dm(tournament_name: str, count: int) -> str:
    asks = 'a question' if count == 1 else f'{count} questions'
    return (
        f"**{tournament_name}** has {asks} for you before this match. "
        "Your answers decide its settings, and you can change them until the seed rolls."
    )


async def notify_match_form(match: Match) -> None:
    """DM each player the form asks something of. Never raises."""
    from application.services import notification_links
    from application.services.match_form_service import MatchFormService

    try:
        await match.fetch_related('tournament', 'players', 'players__user')
        if match.generated_seed_id is not None:  # type: ignore[attr-defined]
            return
        service = MatchFormService()
        users = [p.user for p in match.players]  # type: ignore[attr-defined]
        link = None
        discord_service = DiscordService()
        for user in users:
            count = await service.question_count_for(match, user.id)
            if not count or not user.discord_id:
                continue
            link = link or await notification_links.player_match_form(match.id)
            message = match_form_dm(match.tournament.name, count)
            embed = match_embed(
                title='📝 Before your match', color=COLOR_SEED, description=message,
                tournament=match.tournament.name, player_names=[u.preferred_name for u in users],
                when=match.scheduled_at,
            )
            ok, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=MATCH_FORM_ASKED.to(PLAYERS),
            )
            if not ok:
                logger.warning("notify_match_form DM failed for %s: %s", user.discord_id, err)
    except Exception:
        logger.exception("notify_match_form unexpected error for match %s", match.id)
