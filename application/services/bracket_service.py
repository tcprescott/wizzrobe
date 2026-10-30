"""Bracket Service - Business Logic Layer (native brackets, docs/features/brackets.md).

Owns the bracket lifecycle: authoring a stage (create/update/delete while DRAFT),
managing the tournament-level roster (entrants) and per-stage participation
(entries), and the generate-then-persist ``start`` that turns a seeded field into
a persisted :class:`BracketMatch` graph via the pure structural engines. After
start, elimination advancement is plain pointer-following over the persisted
rows (B7); Swiss/round-robin re-pair per round.

``BracketService`` is a thin composer: it keeps ``__init__`` (wiring the
repository + audit service), the shared helpers, and the roster/enrollment CRUD,
while the lifecycle logic lives in per-concern mixins under
``application/services/_bracket/`` (generation, advancement, completion,
multistage, scheduling, series, notifications). The split is pure code motion — every method resolves
``self.repository`` / ``self.audit_service`` and sibling methods through this one
composed class, so callers still ``from application.services import
BracketService`` and use it unchanged.
"""

from typing import Any, Dict, List, Optional, Union

from application.errors import require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories import BracketRepository, TournamentRepository, UserRepository
from application.services._bracket.advancement import AdvancementMixin
from application.services._bracket.completion import CompletionMixin
from application.services._bracket.generation import GenerationMixin
from application.services._bracket.multistage import MultiStageMixin
from application.services._bracket.notifications import BracketNotificationMixin
from application.services._bracket.scheduling import SchedulingMixin
from application.services._bracket.series import SeriesMixin
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.bracket_config import validate_bracket_config
from application.services.tenant_membership_service import TenantMembershipService
from application.tenant_context import require_tenant_id
from models import (
    Bracket,
    BracketEntrant,
    BracketEntrantStatus,
    BracketEntry,
    BracketEntryStatus,
    BracketFormat,
    BracketMatch,
    BracketState,
    FeatureFlag,
    Tournament,
    User,
)

#: The stage states that are not public — see the viewer-aware reads below.
UNPUBLISHED_STATES = (BracketState.DRAFT, BracketState.CANCELLED)


class BracketService(
    GenerationMixin,
    AdvancementMixin,
    CompletionMixin,
    MultiStageMixin,
    SchedulingMixin,
    SeriesMixin,
    BracketNotificationMixin,
):
    """Service for native-bracket lifecycle operations."""

    def __init__(self) -> None:
        self.repository = BracketRepository()
        self.audit_service = AuditService()

    @staticmethod
    def _coerce_format(fmt: Union[str, BracketFormat]) -> BracketFormat:
        if isinstance(fmt, BracketFormat):
            return fmt
        try:
            return BracketFormat(fmt)
        except ValueError as exc:
            raise ValueError(f"Invalid bracket format: {fmt!r}") from exc

    async def _require_tournament(self, tournament_id: int) -> Tournament:
        return require_found(
            await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id()),
            "Tournament",
        )

    async def _require_bracket(self, bracket_id: int) -> Bracket:
        return require_found(await self.repository.get_bracket(bracket_id), "Bracket")

    @staticmethod
    def _ensure_no_challonge_link(tournament: Tournament) -> None:
        """Reject a native bracket when the tournament is already on Challonge.

        A tournament uses a native bracket OR a Challonge link, never both — the
        symmetric guard lives in ``ChallongeService.link_tournament``.
        """
        if tournament.challonge_tournament_id:
            raise ValueError(
                "This tournament is already linked to a Challonge bracket; a "
                "tournament uses a native bracket or a Challonge link, never both."
            )
        if tournament.startgg_event_slug:
            raise ValueError(
                "This tournament follows a start.gg event; unlink it before building "
                "a bracket here."
            )

    @requires_feature(FeatureFlag.BRACKETS)
    async def create_bracket(
        self,
        actor: Optional[User],
        tournament_id: int,
        name: str,
        format: Union[str, BracketFormat],
        stage_order: int = 0,
        config: Optional[Dict[str, Any]] = None,
    ) -> Bracket:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        tournament = await self._require_tournament(tournament_id)
        self._ensure_no_challonge_link(tournament)

        if not name or not name.strip():
            raise ValueError("Bracket name is required")

        fmt = self._coerce_format(format)
        config = validate_bracket_config(config, fmt=fmt, stage_order=stage_order)

        if await self.repository.get_stage(tournament_id, stage_order) is not None:
            raise ValueError(f"A bracket stage already exists at stage_order {stage_order}")

        bracket = await self.repository.create(
            tournament_id=tournament_id,
            name=name.strip(),
            format=fmt,
            state=BracketState.DRAFT,
            stage_order=stage_order,
            config=config,
        )

        # A bracket-run tournament schedules only what the bracket produced, so
        # attaching the first stage closes the manual player-request path. Only on
        # the first stage: a later stage must not undo a staff re-open.
        if tournament.allow_player_match_requests and stage_order == 0:
            await TournamentRepository.update(
                tournament, allow_player_match_requests=False,
            )

        details = {
            'bracket_id': bracket.id,
            'tournament_id': tournament_id,
            'name': bracket.name,
            'format': fmt.value,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_CREATED, details, EventType.BRACKET_CREATED,
        )
        return bracket

    @requires_feature(FeatureFlag.BRACKETS)
    async def update_bracket(
        self,
        actor: Optional[User],
        bracket_id: int,
        name: Optional[str] = None,
        stage_order: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
        format: Optional[Union[str, BracketFormat]] = None,
    ) -> Bracket:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        if bracket.state != BracketState.DRAFT:
            raise ValueError("Only a DRAFT bracket can be edited")

        update_data: Dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValueError("Bracket name cannot be empty")
            update_data['name'] = name.strip()
        if format is not None:
            # Safe while DRAFT precisely because no match graph exists yet: the
            # format is only read by ``start_bracket`` when it picks an engine.
            update_data['format'] = self._coerce_format(format)
        if stage_order is not None and stage_order != bracket.stage_order:
            existing = await self.repository.get_stage(bracket.tournament_id, stage_order)
            if existing is not None and existing.id != bracket.id:
                raise ValueError(
                    f"A bracket stage already exists at stage_order {stage_order}"
                )
            update_data['stage_order'] = stage_order
        if config is not None:
            # Validated against the stage as it will be *after* this edit, so
            # changing the format and its format-specific keys in one call is
            # checked against the new format rather than the old one.
            update_data['config'] = validate_bracket_config(
                config,
                fmt=update_data.get('format', bracket.format),
                stage_order=update_data.get('stage_order', bracket.stage_order),
            )

        if update_data:
            bracket = await self.repository.update(bracket, **update_data)
            await self.audit_service.write_log(
                actor,
                AuditActions.BRACKET_UPDATED,
                {
                    'bracket_id': bracket.id,
                    'tournament_id': bracket.tournament_id,
                    'changed': sorted(update_data),
                },
            )
        return bracket

    @requires_feature(FeatureFlag.BRACKETS)
    async def set_round_metadata(
        self,
        actor: Optional[User],
        bracket_id: int,
        rounds: Optional[Dict[str, Any]],
    ) -> Bracket:
        """Set per-round metadata (best-of / scheduled window), any state.

        Round chrome never touches the persisted graph, so — unlike
        :meth:`update_bracket`, which is DRAFT-only — it can be edited after a
        stage has started. ``rounds`` is
        ``{round_str: {best_of, scheduled_at, scheduled_end}}``; it is merged over
        the existing config and revalidated by the config schema, so a bad
        key/value surfaces as a user-facing ``ValueError``. Editing the window
        retimes future suggestions only — matches already scheduled stay put.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        merged = dict(bracket.config or {})
        if rounds:
            merged['rounds'] = rounds
        else:
            merged.pop('rounds', None)
        # Shape-checked only: the cross-field checks belong to the authoring
        # path, where the operator can act on them. This one is the single edit
        # allowed after a stage starts, and it must not start refusing over a key
        # it is not touching and the caller can no longer reach.
        validated = validate_bracket_config(merged)
        bracket = await self.repository.update(bracket, config=validated)
        await self.audit_service.write_log(
            actor,
            AuditActions.BRACKET_UPDATED,
            {
                'bracket_id': bracket.id,
                'tournament_id': bracket.tournament_id,
                'changed': ['rounds'],
                'rounds': rounds or None,
            },
        )
        return bracket

    @requires_feature(FeatureFlag.BRACKETS)
    async def cancel_stage(self, actor: Optional[User], bracket_id: int) -> Bracket:
        """Abandon a stage: terminal, but no ``final_rank`` and no champion.

        The close-out for a stage that was started and then called off. Without
        it the only way to finish one is to invent a winner for every remaining
        match, which writes a false result into the public bracket and into every
        entrant's record. A CANCELLED stage keeps its played results as history,
        disappears from the public views, and cannot be advanced from — there is
        no ranking to draw a field out of. It can be deleted afterwards if the
        stage slot is wanted back.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        if bracket.state == BracketState.CANCELLED:
            raise ValueError("Bracket is already cancelled")
        if bracket.state == BracketState.COMPLETE:
            raise ValueError(
                "A completed stage cannot be cancelled — its results are final "
                "and a later stage may already have drawn from them"
            )
        bracket = await self.repository.update(bracket, state=BracketState.CANCELLED)
        details = {
            'bracket_id': bracket.id,
            'tournament_id': bracket.tournament_id,
            'name': bracket.name,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_CANCELLED, details, EventType.BRACKET_CANCELLED,
        )
        return bracket

    @requires_feature(FeatureFlag.BRACKETS)
    async def delete_bracket(self, actor: Optional[User], bracket_id: int) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        if bracket.state not in (BracketState.DRAFT, BracketState.CANCELLED):
            raise ValueError("Only a DRAFT or CANCELLED bracket can be deleted")
        details = {
            'bracket_id': bracket.id,
            'tournament_id': bracket.tournament_id,
            'name': bracket.name,
        }
        await self.repository.delete(bracket)
        await self.audit_service.write_log(
            actor, AuditActions.BRACKET_DELETED, details,
        )

    @requires_feature(FeatureFlag.BRACKETS)
    async def get_bracket(self, bracket_id: int) -> Optional[Bracket]:
        return await self.repository.get_bracket(bracket_id)

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_brackets(self, tournament_id: int) -> List[Bracket]:
        return await self.repository.list_for_tournament(tournament_id)

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_all_brackets(self) -> List[Bracket]:
        """Every stage in the tenant, tournament loaded, active tournaments first.

        The browse surface's one query: it groups the rows by tournament rather
        than asking for each tournament's stages in turn.
        """
        return await self.repository.list_all_with_tournament()

    # A DRAFT stage is unpublished: it exists so staff can author and seed a
    # field before announcing it. A CANCELLED one was published, then called
    # off, and leaving it listed invites people to keep reading a bracket nobody
    # will finish. Both are staff-only on every surface that shows stages to
    # anyone else — the public pages, their static twins, the join door, the
    # home browse tab, REST and MCP — so the rule lives here, once, rather than
    # as a filter each surface has to remember. The unfiltered reads above stay
    # for staff tooling (Admin → Brackets) and internal lifecycle code.

    @staticmethod
    def is_published(bracket: Bracket) -> bool:
        """Whether ``bracket`` is public: neither DRAFT nor CANCELLED."""
        return bracket.state not in UNPUBLISHED_STATES

    @staticmethod
    async def _sees_unpublished(viewer: Optional[User]) -> bool:
        return await AuthService.is_staff(viewer)

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_visible_brackets(
        self, viewer: Optional[User], tournament_id: int,
    ) -> List[Bracket]:
        """A tournament's stages as ``viewer`` may see them (``None`` = signed out)."""
        stages = await self.repository.list_for_tournament(tournament_id)
        if await self._sees_unpublished(viewer):
            return stages
        return [b for b in stages if self.is_published(b)]

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_all_visible_brackets(self, viewer: Optional[User]) -> List[Bracket]:
        """Every stage in the tenant ``viewer`` may see, tournament loaded."""
        stages = await self.repository.list_all_with_tournament()
        if await self._sees_unpublished(viewer):
            return stages
        return [b for b in stages if self.is_published(b)]

    @requires_feature(FeatureFlag.BRACKETS)
    async def get_visible_bracket(
        self, viewer: Optional[User], bracket_id: int,
    ) -> Optional[Bracket]:
        """One stage, or ``None`` when it is missing *or* not for this viewer.

        Same answer either way, so asking for an unpublished stage tells a
        non-staff reader nothing an unused id would not.
        """
        bracket = await self.repository.get_bracket(bracket_id)
        if bracket is None:
            return None
        if self.is_published(bracket) or await self._sees_unpublished(viewer):
            return bracket
        return None

    @requires_feature(FeatureFlag.BRACKETS)
    async def get_visible_tournament(
        self, viewer: Optional[User], tournament_id: int,
    ) -> Optional[Tournament]:
        """The tournament behind a bracket view, or ``None`` if it is not public.

        A tournament with no published stage has not been announced, and its
        name is not public either: walking ids must not list a community's
        unannounced events. Staff see every tournament.
        """
        tournament = await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id())
        if tournament is None:
            return None
        if await self._sees_unpublished(viewer):
            return tournament
        stages = await self.repository.list_for_tournament(tournament_id)
        return tournament if any(self.is_published(b) for b in stages) else None

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_matches(self, bracket_id: int) -> List[BracketMatch]:
        return await self.repository.list_matches(bracket_id)

    @requires_feature(FeatureFlag.BRACKETS)
    async def get_match_with_games(self, match_id: int) -> Optional[BracketMatch]:
        """A bracket match with its series games loaded (for render/serialize)."""
        return await self.repository.get_match_with_games(match_id)

    @requires_feature(FeatureFlag.BRACKETS)
    async def get_visible_match_with_games(
        self, viewer: Optional[User], match_id: int,
    ) -> Optional[BracketMatch]:
        """A matchup with its games, or ``None`` when it is missing or its
        stage is not for this viewer (same answer, as :meth:`get_visible_bracket`)."""
        match = await self.repository.get_match_with_games(match_id)
        if match is None or await self.get_visible_bracket(viewer, match.bracket_id) is None:  # type: ignore[attr-defined]
            return None
        return match

    @requires_feature(FeatureFlag.BRACKETS)
    async def preview_advance(
        self, actor: Optional[User], tournament_id: int, from_stage_order: int,
    ) -> List[BracketEntry]:
        """The dry run of :meth:`advance_stage`, for the people who may run it:
        the next stage is a draft, so its existence and rule are staff-only."""
        await AuthService.ensure(
            await AuthService.is_staff(actor), "Only Staff can manage brackets",
        )
        return await self.get_advancing_preview(tournament_id, from_stage_order)

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_entries(self, bracket_id: int) -> List[BracketEntry]:
        return await self.repository.list_entries(bracket_id)

    @requires_feature(FeatureFlag.BRACKETS)
    async def list_entrants(self, tournament_id: int) -> List[BracketEntrant]:
        return await self.repository.list_entrants(tournament_id)

    @requires_feature(FeatureFlag.BRACKETS)
    async def add_entrant(
        self,
        actor: Optional[User],
        tournament_id: int,
        display_name: str,
        user_id: Optional[int] = None,
    ) -> BracketEntrant:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        await self._require_tournament(tournament_id)
        if not display_name or not display_name.strip():
            raise ValueError("Entrant display name is required")
        if user_id is not None:
            # Resolve before the insert: an unknown id would otherwise surface as
            # a raw FK IntegrityError (a 500, and an id oracle) rather than the
            # 404 every other user-referencing service raises.
            require_found(await UserRepository.get_by_id(user_id), f"User {user_id}")
            await TenantMembershipService.require_community_users([user_id], actor)

        entrant = await self.repository.create_entrant(
            tournament_id=tournament_id,
            display_name=display_name.strip(),
            user_id=user_id,
            status=BracketEntrantStatus.ACTIVE,
        )
        details = {
            'entrant_id': entrant.id,
            'tournament_id': tournament_id,
            'display_name': entrant.display_name,
            'user_id': user_id,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_ENTRANT_ADDED, details,
            EventType.BRACKET_ENTRANT_ADDED,
        )
        return entrant

    @requires_feature(FeatureFlag.BRACKETS)
    async def set_entrant_user(
        self,
        actor: Optional[User],
        entrant_id: int,
        user_id: Optional[int],
    ) -> BracketEntrant:
        """Link a roster entrant to a user account (``None`` unlinks).

        The link is what makes a matchup schedulable, DM-able and joinable to a
        race room, and :meth:`add_entrant` is the only other place it can be set —
        so a placeholder seeded before signups closed would otherwise be stuck
        unlinked for the life of the tournament. Editable in any bracket state:
        it names who the entrant *is*, and identity does not become wrong the
        moment a stage starts.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        entrant = require_found(await self.repository.get_entrant(entrant_id), "Entrant")
        if user_id is not None:
            require_found(await UserRepository.get_by_id(user_id), f"User {user_id}")
            await TenantMembershipService.require_community_users([user_id], actor)
        entrant = await self.repository.update_entrant(entrant, user_id=user_id)
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.BRACKET_ENTRANT_UPDATED,
            {
                'entrant_id': entrant.id,
                'tournament_id': entrant.tournament_id,
                'changed': ['user_id'],
                'user_id': user_id,
            },
            EventType.BRACKET_ENTRANT_UPDATED,
        )
        return entrant

    @requires_feature(FeatureFlag.BRACKETS)
    async def import_entrants_from_roster(
        self, actor: Optional[User], tournament_id: int
    ) -> List[BracketEntrant]:
        """Create a linked entrant for every enrolled player not already rostered.

        The tournament's own signup list is the roster staff already collected,
        so importing it is both the fast path to a field and the only one that
        links every entrant by construction. Idempotent: a player who already has
        an entrant (matched on ``user_id``) is skipped, so it can be re-run after
        late signups.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        await self._require_tournament(tournament_id)

        existing = await self.repository.list_entrants(tournament_id)
        already_linked = {en.user_id for en in existing if en.user_id is not None}
        enrolled = await TournamentRepository.get_enrolled_players_by_tournament_id(
            tournament_id
        )

        created: List[BracketEntrant] = []
        for tp in enrolled:
            user = tp.user
            if user is None or user.id in already_linked:
                continue
            already_linked.add(user.id)
            entrant = await self.repository.create_entrant(
                tournament_id=tournament_id,
                display_name=user.preferred_name or user.username,
                user_id=user.id,
                status=BracketEntrantStatus.ACTIVE,
            )
            created.append(entrant)
            # The same per-entrant audit + event ``add_entrant`` writes: a
            # subscriber tracking the roster must not go blind on a bulk import.
            await self.audit_service.write_and_publish(
                actor,
                AuditActions.BRACKET_ENTRANT_ADDED,
                {
                    'entrant_id': entrant.id,
                    'tournament_id': tournament_id,
                    'display_name': entrant.display_name,
                    'user_id': user.id,
                    'source': 'roster_import',
                },
                EventType.BRACKET_ENTRANT_ADDED,
            )
        return created

    @requires_feature(FeatureFlag.BRACKETS)
    async def drop_entrant(self, actor: Optional[User], entrant_id: int) -> BracketEntrant:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        entrant = require_found(await self.repository.get_entrant(entrant_id), "Entrant")
        entrant = await self.repository.update_entrant(
            entrant, status=BracketEntrantStatus.DROPPED,
        )
        details = {'entrant_id': entrant.id, 'tournament_id': entrant.tournament_id}
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_ENTRANT_DROPPED, details,
            EventType.BRACKET_ENTRANT_DROPPED,
        )
        return entrant

    @requires_feature(FeatureFlag.BRACKETS)
    async def enroll(
        self,
        actor: Optional[User],
        bracket_id: int,
        entrant_id: int,
        seed: Optional[int] = None,
        group_number: Optional[int] = None,
    ) -> BracketEntry:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        if bracket.state != BracketState.DRAFT:
            raise ValueError("Can only enroll into a DRAFT bracket")

        entrant = require_found(await self.repository.get_entrant(entrant_id), "Entrant")
        if entrant.tournament_id != bracket.tournament_id:
            raise ValueError("Entrant belongs to a different tournament")

        if await self.repository.get_entry_for_entrant(bracket_id, entrant_id) is not None:
            raise ValueError("Entrant is already enrolled in this bracket")

        entry = await self.repository.create_entry(
            bracket_id=bracket_id,
            entrant_id=entrant_id,
            seed=seed,
            group_number=group_number,
            status=BracketEntryStatus.ACTIVE,
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.BRACKET_ENTRY_ADDED,
            {
                'entry_id': entry.id,
                'bracket_id': bracket_id,
                'tournament_id': bracket.tournament_id,
                'entrant_id': entrant_id,
                'seed': seed,
            },
        )
        return entry

    @requires_feature(FeatureFlag.BRACKETS)
    async def unenroll(self, actor: Optional[User], entry_id: int) -> None:
        """Remove an entry from a DRAFT stage outright.

        The inverse of :meth:`enroll`, and DRAFT-only for the same reason: no
        match graph references the entry yet, so it can leave without a trace.
        Once a stage has started, use :meth:`retire_entry` instead — the played
        matches must keep pointing at something.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        entry = require_found(await self.repository.get_entry(entry_id), "Entry")
        bracket = await self._require_bracket(entry.bracket_id)
        if bracket.state != BracketState.DRAFT:
            raise ValueError(
                "Only a DRAFT stage can be un-enrolled from — retire the entry "
                "instead once the stage has started"
            )
        details = {
            'entry_id': entry.id,
            'bracket_id': bracket.id,
            'tournament_id': bracket.tournament_id,
            'entrant_id': entry.entrant_id,
        }
        await self.repository.delete_entry(entry)
        await self.audit_service.write_log(
            actor, AuditActions.BRACKET_ENTRY_REMOVED, details,
        )

    @requires_feature(FeatureFlag.BRACKETS)
    async def retire_entry(self, actor: Optional[User], entry_id: int) -> BracketEntry:
        """Mark a stage entry ``DROPPED`` — the mid-stage withdrawal.

        Allowed in any state. Their played results stand and keep counting for
        everyone else's standings; what changes is that Swiss stops pairing them
        and stage advancement skips them. Distinct from
        :meth:`drop_entrant`, which retires the entrant from the *tournament
        roster* and deliberately does not cascade into any stage entry.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        entry = require_found(await self.repository.get_entry(entry_id), "Entry")
        if entry.status == BracketEntryStatus.DROPPED:
            raise ValueError("This entry is already retired")
        bracket = await self._require_bracket(entry.bracket_id)
        entry = await self.repository.update_entry(
            entry, status=BracketEntryStatus.DROPPED,
        )
        details = {
            'entry_id': entry.id,
            'bracket_id': bracket.id,
            'tournament_id': bracket.tournament_id,
            'entrant_id': entry.entrant_id,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_ENTRY_RETIRED, details,
            EventType.BRACKET_ENTRY_RETIRED,
        )
        return entry

    @requires_feature(FeatureFlag.BRACKETS)
    async def set_seeds(
        self,
        actor: Optional[User],
        bracket_id: int,
        seeds: Dict[int, Optional[int]],
    ) -> None:
        """Set per-entry seeds (``entry_id → seed``, ``None`` clears). DRAFT-only.

        Rejects a seed below 1 or one that collides with another entry's seed in
        the same bracket, so a duplicate can never collapse two entrants onto one
        engine slot (silently dropping a player) at ``start_bracket``.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        if bracket.state != BracketState.DRAFT:
            raise ValueError("Can only reseed a DRAFT bracket")

        entries = await self.repository.list_entries(bracket_id)
        entry_by_id = {e.id: e for e in entries}

        # Resolve the full per-entry seeding that would result from applying the
        # requested changes, then validate the whole set before writing anything.
        resulting: Dict[int, Optional[int]] = {e.id: e.seed for e in entries}
        for entry_id, seed in seeds.items():
            if entry_id not in entry_by_id:
                require_found(
                    await self.repository.get_entry(entry_id), "Entry"
                )
                raise ValueError("Entry belongs to a different bracket")
            if seed is not None and seed < 1:
                raise ValueError("A seed must be 1 or greater")
            resulting[entry_id] = seed

        seen: Dict[int, int] = {}
        for eid, seed in resulting.items():
            if seed is None:
                continue
            if seed in seen:
                raise ValueError(
                    f"Seed {seed} is assigned to more than one entry"
                )
            seen[seed] = eid

        await self.repository.set_entry_seeds(seeds)

        await self.audit_service.write_log(
            actor,
            AuditActions.BRACKET_UPDATED,
            {
                'bracket_id': bracket.id,
                'tournament_id': bracket.tournament_id,
                'changed': ['seeds'],
                'seeds': {str(k): v for k, v in seeds.items()},
            },
        )
