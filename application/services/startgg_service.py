"""
start.gg Service - Business Logic Layer

A read-only mirror of a start.gg event, so a tournament whose bracket lives on
start.gg can show its pools and bracket in Wizzrobe and let players book their
matches. Organisers keep recording results on start.gg; the sync (on demand and
from :mod:`startgg_sync_worker`) brings them in.

Entrants map to Wizzrobe users from, in order: a staff assignment, the entrant's
start.gg account linked on a Wizzrobe profile, then the Discord account they
connected on start.gg. A same-name community member is only a *suggestion* until
staff confirm it, since a name is not proof of identity.

Writeback is opt-in per tournament (``startgg_report_results``): a confirmed
Wizzrobe result is reported to start.gg only when start.gg has no result for the
set yet, re-read right before writing.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import (
    BracketRepository,
    StartggRepository,
    TenantMembershipRepository,
    TournamentRepository,
    UserRepository,
)
from application.services import notification_links
from application.services._bracket.notifications import BracketNotificationMixin
from application.services._tournament_signup import record_enrolment_change
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.feature_flag_service import FeatureFlagService
from application.services.match.match_service import MatchService
from application.services.round_schedule_service import RoundScheduleService
from application.tenant_context import require_tenant_id
from application.utils.clients.startgg_client import (
    StartggAPIError,
    StartggClient,
    event_url,
    parse_event_slug,
)
from application.utils.mocks.mock_startgg import MockStartggClient, is_mock_startgg
from application.utils.timezone import to_utc_aware
from models import (
    FeatureFlag,
    Match,
    StartggEntrant,
    StartggPhase,
    StartggPool,
    StartggSet,
    StartggSetState,
    Tournament,
    User,
)

logger = logging.getLogger(__name__)

# A manual Sync within this window of the last one is a no-op, so a double-tap
# doesn't spend start.gg's per-minute budget twice.
_SYNC_THROTTLE = timedelta(seconds=20)


def _display(user: Optional[User]) -> str:
    if user is None:
        return ''
    return user.display_name or user.username


def _is_real_set(set_id: str) -> bool:
    """start.gg names sets of an unstarted phase ``preview_…`` and replaces them on start."""
    return not set_id.startswith('preview')


class StartggService:
    """Business logic for the read-only start.gg integration."""

    def __init__(self) -> None:
        self.repository = StartggRepository()
        self.tournament_repository = TournamentRepository()
        self.audit_service = AuditService()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @staticmethod
    def is_configured() -> bool:
        """True when the platform's start.gg API token is set (or mock is on)."""
        return is_mock_startgg() or bool(os.getenv('STARTGG_API_TOKEN'))

    @staticmethod
    def _client() -> Any:
        return MockStartggClient() if is_mock_startgg() else StartggClient()

    @staticmethod
    def event_url(tournament: Tournament) -> Optional[str]:
        return event_url(tournament.startgg_event_slug) if tournament.startgg_event_slug else None

    async def _editable_tournament(self, tournament_id: int, actor: User) -> Tournament:
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), f"Tournament {tournament_id}"
        )
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            "You do not have permission to manage this tournament's start.gg link",
        )
        return tournament

    async def _fetch(self, slug: str) -> Dict[str, Any]:
        try:
            return await self._client().get_event_full(slug)
        except StartggAPIError as e:
            if e.status == 404:
                raise ValueError(f"start.gg has no event at {event_url(slug)}.") from e
            raise ValueError(f"Couldn't read the event from start.gg: {e}") from e

    # ------------------------------------------------------------------
    # Linking an event
    # ------------------------------------------------------------------
    @requires_feature(FeatureFlag.STARTGG)
    async def link_event(self, tournament_id: int, id_or_url: str, actor: User) -> Dict[str, int]:
        tournament = await self._editable_tournament(tournament_id, actor)
        if not self.is_configured():
            raise ValueError('start.gg is not set up on this server yet (no API token).')
        if tournament.challonge_tournament_id:
            raise ValueError(
                'This tournament is linked to Challonge. Unlink it there first; a tournament '
                'follows one bracket site.'
            )
        if await BracketRepository().list_for_tournament(tournament.id):
            raise ValueError(
                'This tournament already has a bracket built in Wizzrobe; a tournament '
                'follows one bracket.'
            )
        slug = parse_event_slug(id_or_url)
        full = await self._fetch(slug)
        event = full['event']
        if tournament.startgg_event_id and tournament.startgg_event_id != event['event_id']:
            await self.repository.delete_mirror(tournament)
        await self.repository.set_event_link(tournament, event['event_id'], event['slug'], event['name'])
        await self.audit_service.write_log(actor, AuditActions.STARTGG_EVENT_LINKED, {
            'tournament_id': tournament.id, 'event_id': event['event_id'], 'slug': event['slug'],
        })
        return await self._apply(tournament, full, actor, audit=False)

    @requires_feature(FeatureFlag.STARTGG)
    async def unlink_event(self, tournament_id: int, actor: User) -> None:
        tournament = await self._editable_tournament(tournament_id, actor)
        if not tournament.startgg_event_slug:
            raise ValueError("This tournament isn't linked to start.gg.")
        old = {'event_id': tournament.startgg_event_id, 'slug': tournament.startgg_event_slug}
        await self.repository.delete_mirror(tournament)
        await self.repository.set_event_link(tournament, None, None, None)
        await self.audit_service.write_log(
            actor, AuditActions.STARTGG_EVENT_UNLINKED, {'tournament_id': tournament.id, **old},
        )

    @requires_feature(FeatureFlag.STARTGG)
    async def set_bookable_phase(self, tournament_id: int, phase_id: Optional[str], actor: User) -> None:
        tournament = await self._editable_tournament(tournament_id, actor)
        if phase_id is not None and phase_id not in {p.phase_id for p in await self.repository.list_phases(tournament)}:
            raise ValueError("That phase isn't part of the linked event.")
        before = await self._bookable_set_ids(tournament)
        await self.repository.set_bookable_phase(tournament, phase_id)
        await self.audit_service.write_log(actor, AuditActions.STARTGG_BOOKABLE_PHASE_SET, {
            'tournament_id': tournament.id, 'phase_id': phase_id,
        })
        await self._announce_newly_bookable(tournament, before)

    @requires_feature(FeatureFlag.STARTGG)
    async def set_report_results(self, tournament_id: int, enabled: bool, actor: User) -> None:
        tournament = await self._editable_tournament(tournament_id, actor)
        await self.repository.set_report_results(tournament, bool(enabled))
        await self.audit_service.write_log(actor, AuditActions.STARTGG_REPORTING_SET, {
            'tournament_id': tournament.id, 'enabled': bool(enabled),
        })

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    @requires_feature(FeatureFlag.STARTGG)
    async def sync_event(self, tournament_id: int, actor: User, force: bool = False) -> Dict[str, int]:
        tournament = await self._editable_tournament(tournament_id, actor)
        if not tournament.startgg_event_slug:
            raise ValueError("This tournament isn't linked to a start.gg event yet.")
        last = tournament.startgg_last_synced_at
        if not force and last and datetime.now(timezone.utc) - to_utc_aware(last) < _SYNC_THROTTLE:
            return {'entrants': await self.repository.count_entrants(tournament), 'skipped': 1}
        full = await self._fetch(tournament.startgg_event_slug)
        return await self._apply(tournament, full, actor, audit=True)

    @requires_feature(FeatureFlag.STARTGG)
    async def sync_linked_events(self, actor: User) -> int:
        """Sync every linked event in the current community. For the worker; no audit row.

        One event failing (a deleted event, an expired token) doesn't stop the rest.
        """
        synced = 0
        for tournament in await self.repository.linked_tournaments():
            try:
                full = await self._client().get_event_full(tournament.startgg_event_slug)
                await self._apply(tournament, full, actor, audit=False)
                synced += 1
            except StartggAPIError as e:
                logger.warning('start.gg sync failed for tournament %s: %s', tournament.id, e)
        return synced

    async def _apply(
        self, tournament: Tournament, full: Dict[str, Any], actor: User, *, audit: bool,
    ) -> Dict[str, int]:
        before = await self._bookable_set_ids(tournament)
        entrants = await self._mirror_entrants(tournament, full['entrants'])
        phase_ids, pool_ids, set_ids = await self._mirror_structure(tournament, full, entrants)
        removed = await self.repository.prune(
            tournament, entrant_ids=set(entrants), phase_ids=phase_ids, pool_ids=pool_ids, set_ids=set_ids,
        )
        await self.repository.record_sync(tournament, datetime.now(timezone.utc), full['event'].get('name'))
        await self._mirror_players(tournament, actor)
        result = {'entrants': len(entrants), 'sets': len(set_ids), 'pools': len(pool_ids)}
        if audit:
            await self.audit_service.write_log(actor, AuditActions.STARTGG_EVENT_SYNCED, {
                'tournament_id': tournament.id, **result,
                'entrants_removed': removed['entrants'], 'sets_removed': removed['sets'],
            })
        await self._announce_newly_bookable(tournament, before)
        return result

    async def _mirror_entrants(
        self, tournament: Tournament, remote: List[Dict[str, Any]],
    ) -> Dict[str, StartggEntrant]:
        existing = {e.entrant_id: e for e in await self.repository.list_entrants(tournament)}
        by_startgg = await self.repository.users_by_startgg_ids(r['startgg_user_id'] for r in remote)
        by_discord = await self.repository.users_by_discord_ids(r['discord_id'] for r in remote)
        by_name = await self.repository.members_by_lower_name(
            [n for r in remote for n in (r['gamer_tag'], r['name']) if n], require_tenant_id(),
        )
        mirrored: Dict[str, StartggEntrant] = {}
        for r in remote:
            prior = existing.get(r['entrant_id'])
            manual = prior is not None and prior.user_assigned_manually
            if manual:
                user = prior.user  # type: ignore[union-attr]
            else:
                user = by_startgg.get(r['startgg_user_id'] or '') or by_discord.get(r['discord_id'] or '')
            dismissed = prior is not None and prior.suggestion_dismissed
            suggestion = None
            if user is None and not dismissed:
                candidates = {
                    u.id: u for n in (r['gamer_tag'], r['name']) if n for u in by_name.get(n.lower(), [])
                }
                if len(candidates) == 1:
                    suggestion = next(iter(candidates.values()))
            mirrored[r['entrant_id']] = await self.repository.upsert_entrant(
                tournament, r['entrant_id'],
                name=r['name'][:255], gamer_tag=r['gamer_tag'],
                startgg_user_id=r['startgg_user_id'], discord_id=r['discord_id'],
                user_id=user.id if user else None, user_assigned_manually=manual,
                suggested_user_id=suggestion.id if suggestion else None,
            )
        return mirrored

    async def _mirror_structure(
        self, tournament: Tournament, full: Dict[str, Any], entrants: Dict[str, StartggEntrant],
    ) -> Tuple[Set[str], Set[str], Set[str]]:
        identifiers = {
            s['set_id']: s.get('identifier') for detail in full['pools'].values() for s in detail['sets']
        }
        phase_ids: Set[str] = set()
        pool_ids: Set[str] = set()
        set_ids: Set[str] = set()
        for ph in full['event']['phases']:
            phase = await self.repository.upsert_phase(
                tournament, ph['phase_id'], name=ph['name'][:255], phase_order=ph['phase_order'],
                bracket_type=ph['bracket_type'], state=ph['state'],
            )
            phase_ids.add(ph['phase_id'])
            for pl in ph['pools']:
                detail = full['pools'].get(pl['phase_group_id']) or {'standings': [], 'sets': []}
                pool = await self.repository.upsert_pool(
                    tournament, phase, pl['phase_group_id'], identifier=pl['identifier'] or '1',
                    bracket_type=pl['bracket_type'], complete=pl['complete'],
                    standings=[s for s in detail['standings'] if s['entrant_id'] in entrants],
                )
                pool_ids.add(pl['phase_group_id'])
                for s in detail['sets']:
                    await self.repository.upsert_set(
                        tournament, pool, s['set_id'], **self._set_values(s, entrants, identifiers),
                    )
                    set_ids.add(s['set_id'])
        return phase_ids, pool_ids, set_ids

    @staticmethod
    def _set_values(
        s: Dict[str, Any], entrants: Dict[str, StartggEntrant], identifiers: Dict[str, Optional[str]],
    ) -> Dict[str, Any]:
        slots = (s['slots'] + [{}, {}])[:2]
        ents = [entrants.get(slot.get('entrant_id') or '') for slot in slots]
        labels: List[Optional[str]] = []
        for slot in slots:
            ident = identifiers.get(slot.get('prereq_id') or '') if slot.get('prereq_type') == 'set' else None
            if ident:
                labels.append(f"{'Loser' if slot.get('prereq_placement') == 2 else 'Winner'} of {ident}")
            else:
                labels.append(None)
        if s['complete']:
            state = StartggSetState.COMPLETE
        elif all(ents) and _is_real_set(s['set_id']):
            state = StartggSetState.OPEN
        else:
            state = StartggSetState.PENDING
        winner = entrants.get(s.get('winner_id') or '')
        return {
            'round': s['round'], 'round_text': (s.get('round_text') or '')[:64] or None,
            'identifier': s.get('identifier'), 'state': state,
            'entrant1_id': ents[0].id if ents[0] else None, 'entrant2_id': ents[1].id if ents[1] else None,
            'slot1_label': labels[0], 'slot2_label': labels[1],
            'winner_id': winner.id if winner else None,
            'score1': slots[0].get('score'), 'score2': slots[1].get('score'),
            'completed_at': s.get('completed_at'),
        }

    async def _mirror_players(self, tournament: Tournament, actor: User) -> None:
        """Make the tournament's Players list the event's mapped entrants, as Challonge does."""
        wanted = {e.user.id: e.user for e in await self.repository.list_entrants(tournament) if e.user is not None}
        enrolled = {tp.user.id: tp.user for tp in await self.tournament_repository.get_enrolled_players(tournament)}
        for user_id in wanted.keys() - enrolled.keys():
            await self.tournament_repository.enroll_player(tournament, wanted[user_id])
            await record_enrolment_change(self.audit_service, actor, wanted[user_id], tournament.id, added=True)
        for user_id in enrolled.keys() - wanted.keys():
            await self.tournament_repository.unenroll_player(tournament, enrolled[user_id])
            await record_enrolment_change(self.audit_service, actor, enrolled[user_id], tournament.id, added=False)

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------
    @staticmethod
    def bookable_phase_id(tournament: Tournament, phases: List[StartggPhase]) -> Optional[str]:
        """The phase players book from: the chosen one, else the event's last phase."""
        if tournament.startgg_bookable_phase_id:
            return tournament.startgg_bookable_phase_id
        ordered = sorted(phases, key=lambda p: (p.phase_order, p.id))
        return ordered[-1].phase_id if ordered else None

    @staticmethod
    def _is_bookable(row: StartggSet, phase_id: Optional[str]) -> bool:
        return (
            row.state == StartggSetState.OPEN and row.match_id is None  # type: ignore[attr-defined]
            and _is_real_set(row.set_id) and row.pool.phase.phase_id == phase_id
            and row.entrant1 is not None and row.entrant2 is not None
            and row.entrant1.user_id is not None and row.entrant2.user_id is not None
        )

    async def _bookable_set_ids(self, tournament: Tournament) -> Set[int]:
        phase_id = self.bookable_phase_id(tournament, await self.repository.list_phases(tournament))
        return {r.id for r in await self.repository.list_sets(tournament) if self._is_bookable(r, phase_id)}

    async def _announce_newly_bookable(self, tournament: Tournament, before: Set[int]) -> None:
        """DM both players of each set that just became bookable. Never fails the sync."""
        try:
            phase_id = self.bookable_phase_id(tournament, await self.repository.list_phases(tournament))
            for row in await self.repository.list_sets(tournament):
                if row.id not in before and self._is_bookable(row, phase_id):
                    await self._notify_matchup_ready(tournament, row)
        except Exception:
            logger.exception('start.gg matchup-ready notifications failed for tournament %s', tournament.id)

    @staticmethod
    async def _notify_matchup_ready(tournament: Tournament, row: StartggSet) -> None:
        e1, e2 = row.entrant1, row.entrant2
        if e1 is None or e2 is None:
            return
        link = await notification_links.player_startgg_schedule(row.id)
        for me, opponent in ((e1, e2), (e2, e1)):
            discord_queue.enqueue(BracketNotificationMixin._send_matchup_ready(
                user=me.user,
                opponent_name=opponent.name,
                opponent_seed=None,
                tournament_name=tournament.name,
                round_name=row.round_text or 'Bracket match',
                best_of=1,
                schedule_link=link,
                rebook=False,
            ))

    @requires_feature(FeatureFlag.STARTGG)
    async def list_unbooked_sets_for_user(self, user: User) -> List[StartggSet]:
        """The player's sets that are ready to book, in each tournament's bookable phase."""
        rows = await self.repository.open_unbooked_sets_for_user(user)
        phases: Dict[int, Optional[str]] = {}
        for row in rows:
            if row.tournament_id not in phases:  # type: ignore[attr-defined]
                phases[row.tournament_id] = self.bookable_phase_id(  # type: ignore[attr-defined]
                    row.tournament, await self.repository.list_phases(row.tournament),
                )
        return [
            r for r in rows
            if _is_real_set(r.set_id) and r.pool.phase.phase_id == phases[r.tournament_id]  # type: ignore[attr-defined]
        ]

    @requires_feature(FeatureFlag.STARTGG)
    async def schedule_set(
        self, set_pk: int, scheduled_date: str, scheduled_time: str, actor: User,
        comment: Optional[str] = None,
    ) -> Match:
        row = require_found(await self.repository.get_set(set_pk), 'start.gg match')
        if row.match_id is not None:  # type: ignore[attr-defined]
            raise ValueError('This match has already been scheduled.')
        phase_id = self.bookable_phase_id(row.tournament, await self.repository.list_phases(row.tournament))
        if row.state != StartggSetState.OPEN or not _is_real_set(row.set_id) or row.pool.phase.phase_id != phase_id:
            raise ValueError("This match isn't ready to schedule yet.")
        e1, e2 = row.entrant1, row.entrant2
        if e1 is None or e2 is None or e1.user is None or e2.user is None:
            raise ValueError(
                'Both players need to be matched to a Wizzrobe account first. Linking start.gg '
                'on your profile does it, or ask staff.'
            )
        rounds = RoundScheduleService()
        await rounds.check_booking(
            await rounds.window_for_startgg_set(row), row.tournament,
            scheduled_date, scheduled_time, actor,
        )
        match = await MatchService().submit_match_request(
            tournament_id=row.tournament_id,  # type: ignore[attr-defined]
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            player_ids=[e1.user.id, e2.user.id],
            actor=actor,
            comment=comment,
            # The bracket is the sanctioned path here, and the service authorizes
            # the actor as one of the set's players, so the tournament's
            # player-request toggle doesn't apply.
            from_bracket=True,
        )
        await self.repository.link_match(row, match)
        return match

    # ------------------------------------------------------------------
    # Entrant mapping (staff)
    # ------------------------------------------------------------------
    @requires_feature(FeatureFlag.STARTGG)
    async def list_entrants(self, tournament_id: int, actor: User) -> List[StartggEntrant]:
        tournament = await self._editable_tournament(tournament_id, actor)
        return await self.repository.list_entrants(tournament)

    @requires_feature(FeatureFlag.STARTGG)
    async def list_phases(self, tournament_id: int, actor: User) -> List[StartggPhase]:
        tournament = await self._editable_tournament(tournament_id, actor)
        return await self.repository.list_phases(tournament)

    async def _editable_entrant(self, entrant_pk: int, actor: User) -> StartggEntrant:
        entrant = require_found(await self.repository.get_entrant(entrant_pk), 'start.gg entrant')
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, entrant.tournament),
            "You do not have permission to manage this tournament's start.gg link",
        )
        return entrant

    @requires_feature(FeatureFlag.STARTGG)
    async def assign_entrant(self, entrant_pk: int, user_id: int, actor: User) -> StartggEntrant:
        """Map an entrant to a member by hand (or confirm a suggestion); sync keeps it."""
        entrant = await self._editable_entrant(entrant_pk, actor)
        user = require_found(await UserRepository.get_by_id(user_id), 'User')
        if not await TenantMembershipRepository.is_member(user.id, require_tenant_id()):
            raise ValueError(f"{_display(user)} isn't a member of this community.")
        taken = next(
            (e for e in await self.repository.list_entrants(entrant.tournament)
             if e.user is not None and e.user.id == user.id and e.id != entrant.id),
            None,
        )
        if taken is not None:
            raise ValueError(f'{_display(user)} is already matched to {taken.name}.')
        before = await self._bookable_set_ids(entrant.tournament)
        await self.repository.set_entrant_user(entrant, user, manual=True)
        await self.audit_service.write_log(actor, AuditActions.STARTGG_ENTRANT_ASSIGNED, {
            'tournament_id': entrant.tournament.id, 'entrant_id': entrant.entrant_id, 'user_id': user.id,
        })
        await self._mirror_players(entrant.tournament, actor)
        await self._announce_newly_bookable(entrant.tournament, before)
        return entrant

    @requires_feature(FeatureFlag.STARTGG)
    async def confirm_all_suggestions(self, tournament_id: int, actor: User) -> int:
        tournament = await self._editable_tournament(tournament_id, actor)
        confirmed = 0
        for entrant in await self.repository.list_entrants(tournament):
            if entrant.user is None and entrant.suggested_user is not None:
                try:
                    await self.assign_entrant(entrant.id, entrant.suggested_user.id, actor)
                    confirmed += 1
                except ValueError:
                    continue
        return confirmed

    @requires_feature(FeatureFlag.STARTGG)
    async def dismiss_suggestion(self, entrant_pk: int, actor: User) -> None:
        entrant = await self._editable_entrant(entrant_pk, actor)
        await self.repository.dismiss_suggestion(entrant)
        await self.audit_service.write_log(actor, AuditActions.STARTGG_SUGGESTION_DISMISSED, {
            'tournament_id': entrant.tournament.id, 'entrant_id': entrant.entrant_id,
        })

    @requires_feature(FeatureFlag.STARTGG)
    async def clear_entrant(self, entrant_pk: int, actor: User) -> None:
        """Drop a hand match; the entrant falls back to their linked accounts."""
        entrant = await self._editable_entrant(entrant_pk, actor)
        fallback = (
            (await self.repository.users_by_startgg_ids([entrant.startgg_user_id])).get(entrant.startgg_user_id or '')
            or (await self.repository.users_by_discord_ids([entrant.discord_id])).get(entrant.discord_id or '')
        )
        await self.repository.set_entrant_user(entrant, fallback, manual=False)
        await self.audit_service.write_log(actor, AuditActions.STARTGG_ENTRANT_UNASSIGNED, {
            'tournament_id': entrant.tournament.id, 'entrant_id': entrant.entrant_id,
        })
        await self._mirror_players(entrant.tournament, actor)

    # ------------------------------------------------------------------
    # The public bracket view
    # ------------------------------------------------------------------
    @requires_feature(FeatureFlag.STARTGG)
    async def get_event_view(self, tournament_id: int) -> Dict[str, Any]:
        """Everything the bracket page shows, publicly readable (names are public)."""
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), f'Tournament {tournament_id}'
        )
        if not tournament.startgg_event_slug:
            raise ValueError("This tournament isn't linked to start.gg.")
        phases = await self.repository.list_phases(tournament)
        pools = await self.repository.list_pools(tournament)
        sets = await self.repository.list_sets(tournament)
        entrants = {e.id: e for e in await self.repository.list_entrants(tournament)}
        by_entrant_id = {e.entrant_id: e for e in entrants.values()}
        sets_by_pool: Dict[int, List[StartggSet]] = {}
        for row in sets:
            sets_by_pool.setdefault(row.pool_id, []).append(row)  # type: ignore[attr-defined]
        bookable = self.bookable_phase_id(tournament, phases)
        view_phases = []
        for phase in phases:
            phase_pools = sorted(
                (p for p in pools if p.phase_id == phase.id),  # type: ignore[attr-defined]
                key=lambda p: (len(p.identifier), p.identifier),
            )
            view_phases.append({
                'phase': phase,
                'bookable': phase.phase_id == bookable,
                'pools': [self._pool_view(p, sets_by_pool.get(p.id, []), by_entrant_id) for p in phase_pools],
            })
        return {
            'tournament': tournament,
            'event_url': self.event_url(tournament),
            'last_synced_at': tournament.startgg_last_synced_at,
            'phases': view_phases,
        }

    @staticmethod
    def _pool_view(
        pool: StartggPool, sets: List[StartggSet], by_entrant_id: Dict[str, StartggEntrant],
    ) -> Dict[str, Any]:
        record: Dict[int, List[int]] = {}
        for row in sets:
            if row.state == StartggSetState.COMPLETE and row.winner_id is not None:  # type: ignore[attr-defined]
                for ent in (row.entrant1, row.entrant2):
                    if ent is not None:
                        wl = record.setdefault(ent.id, [0, 0])
                        wl[0 if ent.id == row.winner_id else 1] += 1  # type: ignore[attr-defined]
        rows = []
        seen: Set[int] = set()
        for standing in sorted(pool.standings or [], key=lambda s: (s.get('placement') or 999)):
            entrant = by_entrant_id.get(str(standing.get('entrant_id')))
            if entrant is None or entrant.id in seen:
                continue
            seen.add(entrant.id)
            wins, losses = record.get(entrant.id, [0, 0])
            rows.append({'entrant': entrant, 'placement': standing.get('placement'), 'wins': wins, 'losses': losses})
        played = sum(1 for r in sets if r.state == StartggSetState.COMPLETE)
        return {'pool': pool, 'standings': rows, 'sets': sets, 'played': played, 'total': len(sets)}

    @staticmethod
    def entrant_label(row: StartggSet, slot: int) -> str:
        entrant = row.entrant1 if slot == 1 else row.entrant2
        if entrant is not None:
            return entrant.name
        return (row.slot1_label if slot == 1 else row.slot2_label) or 'TBD'

    # ------------------------------------------------------------------
    # Writeback (opt-in per tournament)
    # ------------------------------------------------------------------
    # feature-gate: exempt — the soft integration point, like ChallongeService.push_result_if_linked.
    async def report_result_if_linked(self, match: Match, actor: Optional[User]) -> bool:
        """Report a confirmed result to start.gg when the match came from a start.gg set.

        Called from match confirmation for every match, so it answers False
        (instead of raising) when the feature is off, the tournament hasn't
        opted in, or the match didn't come from start.gg. Returns True only
        when a result was sent.
        """
        if actor is None:
            return False
        if not await FeatureFlagService().is_enabled(FeatureFlag.STARTGG):
            return False
        row = next(iter(await self.repository.sets_for_match(match)), None)
        if row is None or not row.tournament.startgg_report_results:
            return False
        try:
            return await self._report(row, match, actor)
        except (ValueError, StartggAPIError):
            logger.error('start.gg result report failed for match %s', match.id, exc_info=True)
            return False

    async def _report(self, row: StartggSet, match: Match, actor: User) -> bool:
        await match.fetch_related('players')
        winner = next((p for p in match.players if p.finish_rank == 1), None)  # type: ignore[attr-defined]
        if winner is None:
            return False
        entrant = next(
            (e for e in (row.entrant1, row.entrant2) if e is not None and e.user_id == winner.user_id),
            None,
        )
        if entrant is None:
            raise ValueError("The match winner isn't one of the start.gg set's players.")
        client = self._client()
        current = await client.get_set_state(row.set_id)
        if current['complete']:
            # start.gg already has a result (an admin reported it): theirs stands.
            return False
        await client.report_winner(row.set_id, entrant.entrant_id)
        await self.audit_service.write_log(actor, AuditActions.STARTGG_RESULT_REPORTED, {
            'tournament_id': row.tournament.id, 'match_id': match.id, 'set_id': row.set_id,
            'winner_entrant_id': entrant.entrant_id,
        })
        try:
            full = await client.get_event_full(row.tournament.startgg_event_slug)
            await self._apply(row.tournament, full, actor, audit=False)
        except StartggAPIError:
            logger.error('start.gg re-sync after report failed for tournament %s', row.tournament.id, exc_info=True)
        return True
