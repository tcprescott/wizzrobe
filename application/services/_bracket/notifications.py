"""Bracket notifications: the matchup-ready DM, and series context for the rest.

One new player-facing message and one existing set made richer (D7). The rule is
narrow on purpose: **a DM is sent when it asks the recipient to act.** Scheduling
is the one thing in a bracket only the entrants can do — bracket-run tournaments
run with ``allow_player_match_requests`` off, so the Player tab's schedule dialog
is their sole route to a booking — and it is therefore the only thing that earns
its own DM. Advancing and being eliminated earn none; the bracket shows those.

That DM's button opens the dialog itself. It used to open the public bracket
page, whose Schedule button is staff-only: the message asked two players to book
a time and then sent them somewhere they could not.

That rule also removes a carve-out. A losers-bracket drop needs no special
handling: a winners-bracket loser simply gets the ready DM when their losers
matchup opens, by the same trigger as everyone else.

``BracketNotificationMixin`` is composed into :class:`BracketService` and reaches
``self.repository`` and sibling methods through it. Every send goes out on
``discord_queue``, which re-binds the enqueueing request's ``tenant_scope`` in its
worker, and every one is best-effort: a Discord problem must never abort the
advancement that triggered it.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from application.services import notification_links
from application.services._bracket._composed import ComposedBracketService
from application.services.bracket_engines.round_names import RoundNode, round_names
from application.services.discord import discord_queue
from application.services.notification_catalog import MATCHUP_READY, MATCHUP_REOPENED, PLAYERS
from application.utils.discord_embeds import matchup_ready_embed
from application.utils.discord_messages import DMLink, matchup_ready_dm
from models import (
    Bracket,
    BracketFormat,
    BracketMatch,
    BracketMatchGameState,
    BracketMatchState,
)

logger = logging.getLogger(__name__)


class BracketNotificationMixin(ComposedBracketService):
    # -- round naming ------------------------------------------------------
    async def round_name_for(self, bracket: Bracket, round_number: int) -> str:
        """The human name of one round of a stage — "Semifinals", "Losers Round 2".

        Naming is structural (which round is the winners final, how deep the
        losers bracket runs, which match is the grand final), so it needs the
        stage's graph; :meth:`BracketRepository.round_links` is the lean
        projection for it. One query.

        Only an elimination stage counts down to a final. Swiss and round robin
        stay on "Round N" — the public bracket already names them that way
        (``round_display_names``), and a group stage announcing "Quarterfinals"
        tells its entrants they are in a knockout they are not in.
        """
        rows = await self.repository.round_links(bracket.id)
        names = round_names(
            [
                RoundNode(
                    id=r['id'], round=r['round'],
                    winner_to_id=r['winner_to_id'], loser_to_id=r['loser_to_id'],
                )
                for r in rows
            ],
            double_elim=bracket.format == BracketFormat.DOUBLE_ELIM,
            # Defined on AdvancementMixin, reachable on the composed service.
            elimination=bracket.format in self._ELIM_FORMATS,  # type: ignore[attr-defined]
        )
        return names.get(round_number) or f'Round {round_number}'

    async def round_name_for_match(
        self, bracket: Bracket, bracket_match: BracketMatch,
    ) -> str:
        """The round name a *matchup* carries, group-qualified in a grouped stage.

        Every group of a round-robin stage runs the same round numbers at once,
        so "Round 2" alone does not tell an entrant which of four concurrent
        rounds is theirs. Mirrors the admin's ``match_label`` vocabulary.
        """
        name = await self.round_name_for(bracket, bracket_match.round)
        if bracket_match.group_number:
            return f'Group {bracket_match.group_number} · {name}'
        return name

    # -- series context for the ordinary match DMs -------------------------
    async def bracket_line_for_match(self, match_id: int) -> str:
        """"Semifinals · Game 2 of 3 · Series 1-0" for a match, or '' if not a game.

        Folded into every existing match DM (``scheduled``, ``rescheduled``,
        ``checked_in``, ``cancelled``, ``state_changed``) so a bracket game's
        notification stops being indistinguishable from a casual scheduled match.
        Returns '' — never raises — for the vast majority of matches, which no
        bracket scheduled, and for any lookup that fails: a missing context line
        must not cost a player their match DM.
        """
        line, _ = await self.match_dm_context(match_id)
        return line

    async def match_dm_context(self, match_id: int) -> Tuple[str, bool]:
        """``(bracket_line, frees_a_slot)`` for one match, in a single lookup.

        The cancellation DM needs both facts and needs them *before* the row is
        deleted, so they are resolved together rather than by two passes over the
        same game row. ``frees_a_slot`` predicts what
        :meth:`SchedulingMixin.release_game_if_linked` will do — the game exists
        and is still ``SCHEDULED`` — which is what lets the DM say "the matchup is
        open to reschedule" instead of "nothing further is needed from you".
        """
        try:
            game = await self.repository.get_game_for_match(match_id)
            if game is None:
                return '', False
            frees = game.state == BracketMatchGameState.SCHEDULED
            bracket_match = game.bracket_match
            bracket = await self._require_bracket(bracket_match.bracket_id)
            best_of = self.resolve_best_of(bracket, bracket_match)
            parts = [await self.round_name_for_match(bracket, bracket_match)]
            if best_of > 1:
                parts.append(f'Game {game.game_number} of {best_of}')
                games = await self.repository.list_games(bracket_match.id)
                e1, e2 = self._standing_from(games, bracket_match)
                if e1 or e2:
                    parts.append(f'Series {max(e1, e2)}-{min(e1, e2)}')
            return ' · '.join(parts), frees
        except Exception:
            logger.exception('bracket DM context lookup failed for match %s', match_id)
            return '', False

    async def matchup_summary(self, match_id: int) -> Optional[Dict[str, Any]]:
        """What a scheduled ``Match`` is *for*, for the admin match editor (U4).

        ``{bracket_id, bracket_name, round_name, game_number, best_of, standing,
        entrants: [{name, seed}], stakes}`` — or ``None`` when the match backs no
        bracket game. ``stakes`` is "Winner to Semifinals", resolved by following
        ``winner_to`` and naming that round; '' for a final, which has no onward
        pointer and where "this decides it" is already obvious from the name.

        The editor previously showed only the link picker, so staff editing a
        game had no way to see which game of what series they were changing.
        """
        game = await self.repository.get_game_for_match(match_id)
        if game is None:
            return None
        resolved = await self.repository.get_match_with_entrants(
            game.bracket_match_id
        )
        if resolved is None:
            return None
        bracket = resolved.bracket
        best_of = self.resolve_best_of(bracket, resolved)
        games = await self.repository.list_games(resolved.id)
        e1, e2 = self._standing_from(games, resolved)
        return {
            'bracket_id': bracket.id,
            'bracket_name': bracket.name,
            'round_name': await self.round_name_for_match(bracket, resolved),
            'game_number': game.game_number,
            'best_of': best_of,
            'standing': f'{max(e1, e2)}-{min(e1, e2)}' if (e1 or e2) else '',
            'entrants': [
                {
                    'name': entry.entrant.display_name if entry and entry.entrant else 'TBD',
                    'seed': entry.seed if entry else None,
                }
                for entry in (resolved.entry1, resolved.entry2)
            ],
            'stakes': await self._stakes(bracket, resolved),
        }

    async def _stakes(self, bracket: Bracket, bracket_match: BracketMatch) -> str:
        """"Winner to Semifinals", or '' when nothing follows this matchup."""
        if bracket_match.winner_to_id is None:
            return ''
        target = await self.repository.get_match(bracket_match.winner_to_id)
        if target is None:
            return ''
        return f'Winner to {await self.round_name_for(bracket, target.round)}'

    # -- the matchup-ready DM ----------------------------------------------
    async def notify_matchup_ready(
        self, bracket_match: BracketMatch, *, rebook: bool = False,
    ) -> None:
        """DM both entrants that they have a matchup to schedule.

        Fired at the moment a matchup becomes bookable: the ``PENDING → OPEN``
        transition in ``_settle_match``, the grand-final reset opening in
        ``_advance_after_result``, each round-1 / Swiss pairing at generation, and
        — with ``rebook`` — a game whose slot was released back (D3).

        Keyed on the *transition* rather than on the state, so a matchup whose two
        slots fill at different times is announced once. Silent when either slot
        is still an unlinked placeholder: there is nobody to tell, and the DM will
        fire when the entrant is linked and the matchup re-settles.
        """
        try:
            if bracket_match.state != BracketMatchState.OPEN:
                return
            resolved = await self.repository.get_match_with_entrants(bracket_match.id)
            if resolved is None or not self._both_entrants_linked(resolved):
                return

            bracket = resolved.bracket
            tournament = bracket.tournament
            round_name = await self.round_name_for_match(bracket, resolved)
            best_of = self.resolve_best_of(bracket, resolved)
            schedule_link = await self._schedule_link(resolved.id, rebook=rebook)

            for entry, opponent in (
                (resolved.entry1, resolved.entry2),
                (resolved.entry2, resolved.entry1),
            ):
                discord_queue.enqueue(self._send_matchup_ready(
                    user=entry.entrant.user,
                    opponent_name=opponent.entrant.display_name,
                    opponent_seed=opponent.seed,
                    tournament_name=tournament.name,
                    round_name=round_name,
                    best_of=best_of,
                    schedule_link=schedule_link,
                    rebook=rebook,
                ))
        except Exception:
            logger.exception(
                'matchup-ready notification failed for bracket match %s',
                getattr(bracket_match, 'id', None),
            )

    async def notify_matchup_reopened(
        self, bracket_match: BracketMatch, *, reason: str = '',
    ) -> None:
        """The released-game variant: the matchup is bookable again (U3a).

        A no-op once the series is decided or the matchup left ``OPEN`` — the
        clinch teardown releases nothing (its games are already ``CANCELLED``),
        but a staff override can settle a matchup whose game is then cancelled,
        and telling those two to rebook a series that is over would be wrong.
        """
        fresh = await self.repository.get_match(bracket_match.id)
        if fresh is None or fresh.state != BracketMatchState.OPEN:
            return
        await self.notify_matchup_ready(fresh, rebook=True)

    async def notify_open_matchups(self, bracket: Bracket) -> None:
        """Announce every already-bookable matchup of a freshly-started stage.

        Generation writes round 1 straight to ``OPEN`` rather than transitioning
        into it, so the per-transition hook never sees those — without this, the
        entrants who need the DM most (nobody has told them anything yet) would be
        the only ones who never got it. Disjoint from the transition hook by
        construction: ``_settle_match`` is not on the generation path.
        """
        for bracket_match in await self.repository.list_open_matches(bracket.id):
            await self.notify_matchup_ready(bracket_match)

    @staticmethod
    async def _schedule_link(
        bracket_match_id: int, *, rebook: bool = False,
    ) -> Optional[DMLink]:
        """The **Pick a time** button: this matchup's schedule dialog, already open.

        Previously this pointed at ``/brackets/<id>``, which was wrong in the way
        that matters most — the bracket page gates its Schedule button behind
        ``is_staff``, so the two people the DM was addressed to arrived at a
        dialog they could only close. The entrant's real route has always been
        the Player tab, and ``?schedule=<matchup id>`` opens the picker on
        arrival.

        Best-effort: a tenant that will not resolve costs the button, not the DM.
        """
        return await notification_links.player_schedule(
            bracket_match_id, label='Pick a new time' if rebook else 'Pick a time',
        )

    @staticmethod
    async def _send_matchup_ready(
        *,
        user: Any,
        opponent_name: str,
        opponent_seed: Optional[int],
        tournament_name: str,
        round_name: str,
        best_of: int,
        schedule_link: Optional[DMLink],
        rebook: bool,
    ) -> None:
        """One entrant's DM. ``send_dm`` honours the Discord on/off switch.

        Silent by default (see the catalogue): the matchup waits in the
        player's Pick-a-time list, so the DM is a heads-up rather than a summons.
        """
        from application.services.discord.discord_service import DiscordService
        from application.services.match.match_schedule_service import (
            _community_name,
            _dm_opt_ok,
        )

        if not _dm_opt_ok(user):
            return
        schedule_url = schedule_link.url if schedule_link else ''
        message = matchup_ready_dm(
            tournament_name, round_name, opponent_name,
            opponent_seed=opponent_seed, best_of=best_of,
            schedule_url=schedule_url, rebook=rebook,
        )
        embed = matchup_ready_embed(
            tournament=tournament_name,
            round_name=round_name,
            opponent=(
                f'{opponent_name} (#{opponent_seed})' if opponent_seed else opponent_name
            ),
            community_name=await _community_name(),
            best_of=best_of,
            schedule_url=schedule_url or None,
            rebook=rebook,
        )
        success, err = await DiscordService().send_dm(
            user.discord_id, message, embed=embed, link=schedule_link,
            notice=(MATCHUP_REOPENED if rebook else MATCHUP_READY).to(PLAYERS),
        )
        if not success:
            logger.warning(
                'matchup-ready DM failed for %s: %s', user.discord_id, err,
            )
