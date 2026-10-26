"""
Tournament Service - Business Logic Layer

Handles tournament-related operations including creation, updates, validation,
and admin/crew-coordinator membership.
"""

from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from application.repositories import (
    DiscordTournamentGrantRepository,
    PresetRepository,
    RaceRoomProfileRepository,
    RacetimeBotRepository,
    TournamentRepository,
)
from application.services._tournament_drop import TournamentDropMixin
from application.services._tournament_signup import (
    COLOR_SIGNUP,
    TournamentSignupCard,
    TournamentSignupMixin,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.system_config_service import SystemConfigService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tournament_config import validate_tournament_config
from application.tenant_context import require_tenant_id
from application.utils.safe_url import normalize_http_url
from models import Tournament, TournamentGrant, User

__all__ = [
    'COLOR_SIGNUP',
    'DELETE_CONFIRMATION_PHRASE',
    'TournamentService',
    'TournamentSignupCard',
]

# Sentinel distinguishing "caller did not supply preset_id" (leave as-is) from an
# explicit None (detach the preset). Update-only; create defaults to no preset.
_UNSET = object()

# The phrase a caller must repeat to permanently delete a tournament. Compared
# case-insensitively after stripping, but never inferred from anything else on
# screen — a deletion has to be typed out.
DELETE_CONFIRMATION_PHRASE = 'permanently delete'


class TournamentService(TournamentDropMixin, TournamentSignupMixin):
    """Service for tournament-related business operations.

    Player self-service signup lives in
    :class:`~application.services._tournament_signup.TournamentSignupMixin`, and
    staff dropping a player in :class:`~application.services._tournament_drop.TournamentDropMixin`.
    """

    def __init__(self) -> None:
        self.repository = TournamentRepository()
        self.preset_repository = PresetRepository()
        self.racetime_bot_repository = RacetimeBotRepository()
        self.race_room_profile_repository = RaceRoomProfileRepository()
        self.discord_grant_repository = DiscordTournamentGrantRepository()
        self.audit_service = AuditService()

    async def _resolve_preset(self, preset_id: Optional[int]):
        """Validate an incoming preset_id is a real preset in this tenant.

        ``None`` clears the FK. A non-null id must resolve through the
        tenant-scoped repository, so a preset from another tenant is rejected
        rather than silently linked. Returns the preset itself because the
        randomizer it names is what ``seed_generator`` is set from.
        """
        if preset_id is None:
            return None
        preset = await self.preset_repository.get_by_id(preset_id)
        if preset is None:
            raise ValueError("Preset not found")
        return preset

    async def _resolve_hard_preset(self, hard_preset_id: Optional[int], preset):
        """Validate the opt-in "harder" preset against the standard one.

        Two rules, both because seed generation resolves the randomizer from
        whichever preset ends up winning: a hard preset with no standard preset
        to fall back to would make the fallback path randomizer-less, and a hard
        preset on a different randomizer would silently change *which game* a
        match rolls the moment its players agreed.
        """
        if hard_preset_id is None:
            return None
        hard = await self.preset_repository.get_by_id(hard_preset_id)
        if hard is None:
            raise ValueError("Preset not found")
        if preset is None:
            raise ValueError(
                "Set a seed preset before adding a harder one — the harder preset is "
                "what players opt into instead of it."
            )
        if hard.id == preset.id:
            raise ValueError("The harder preset must be a different preset from the standard one.")
        if hard.randomizer != preset.randomizer:
            raise ValueError(
                f"The harder preset rolls {hard.randomizer} but this tournament rolls "
                f"{preset.randomizer}. Both presets must use the same randomizer."
            )
        return hard

    async def _resolve_racetime_bot_id(self, racetime_bot_id: Optional[int]) -> Optional[int]:
        """Validate the bot is one this tenant is *authorized* to use.

        ``None`` clears the FK. A non-null id must appear in the tenant's active
        authorization grants, so a category the tenant was never granted — or
        another tenant's bot — is rejected rather than silently linked.
        """
        if racetime_bot_id is None:
            return None
        authorized = await self.racetime_bot_repository.list_active_for_tenant(require_tenant_id())
        if not any(bot.id == racetime_bot_id for bot in authorized):
            raise ValueError("Racetime bot not available to this tenant")
        return racetime_bot_id

    async def _resolve_race_room_profile_id(self, profile_id: Optional[int]) -> Optional[int]:
        """Validate the room profile belongs to this tenant. ``None`` clears it."""
        if profile_id is None:
            return None
        profile = await self.race_room_profile_repository.get_by_id(profile_id)
        if profile is None:
            raise ValueError("Race room profile not found")
        return profile.id

    @staticmethod
    def _coerce_day(value: Any, label: str) -> Optional[date]:
        """Coerce a date/ISO-string/blank into a ``date`` (or ``None`` to inherit)."""
        if value is None or value == '':
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            raise ValueError(f"{label} must be in YYYY-MM-DD format.") from None

    def _normalize_event_dates(
        self, event_start_date: Any, event_end_date: Any,
    ) -> Tuple[Optional[date], Optional[date]]:
        """Validate the per-tournament event-window override (each bound optional)."""
        start = self._coerce_day(event_start_date, "Event start date")
        end = self._coerce_day(event_end_date, "Event end date")
        if start is not None and end is not None and end < start:
            raise ValueError("Event end date cannot be before the event start date.")
        return start, end

    @staticmethod
    def _normalize_tournament_hours(
        tournament_hours: Optional[Dict[date, Tuple[str, str]]],
    ) -> Optional[Dict[str, Dict[str, str]]]:
        """Validate a {date: (open, close)} override into a storable blob.

        Returns ``None`` when the mapping is empty/absent so an unset override
        inherits the tenant hours; otherwise the validated
        ``{date_iso: {'open', 'close'}}`` blob. Reuses the tenant validator so
        both surfaces enforce identical HH:MM / close>open rules.
        """
        if not tournament_hours:
            return None
        return SystemConfigService.validate_hours_mapping(tournament_hours) or None

    #: Matches ``Tournament.short_name``'s column width.
    SHORT_NAME_MAX = 40

    @classmethod
    def _short_name(cls, value: Optional[str]) -> Optional[str]:
        """Blank clears it (the full name shows instead)."""
        cleaned = (value or '').strip()
        if len(cleaned) > cls.SHORT_NAME_MAX:
            raise ValueError(f'Keep the short name to {cls.SHORT_NAME_MAX} characters or fewer.')
        return cleaned or None

    @staticmethod
    def _non_negative(value: Any, what: str) -> int:
        number = int(value or 0)
        if number < 0:
            raise ValueError(f"{what} can't be negative")
        return number

    @staticmethod
    def _crew_requirement(value: Any, role: str) -> int:
        """Coerce a crew-requirement count, rejecting negatives.

        The UI hands these over as floats from ``ui.number``, and a negative
        would make a stream candidate permanently covered — the mirror image of
        the false gap this setting exists to fix.
        """
        count = int(value or 0)
        if count < 0:
            raise ValueError(f"Required {role} cannot be negative")
        return count

    @staticmethod
    def _check_automation_prerequisites(
        *,
        racetime_auto_create_rooms: bool,
        racetime_bot_id: Optional[int],
    ) -> None:
        """Refuse automation that has been switched on without what it needs.

        A tournament could previously be saved with ``racetime_auto_create_rooms``
        on and no bot to host the rooms with. Nothing rejected it — the worker
        simply returned ``None`` on every tick, and the setting read as enabled on
        the admin page right up until the moment a race needed a room that was
        never going to be created. That is the class of failure SahasrahBot's
        reliability audit asks to move to save time: an unsatisfiable
        configuration should fail while someone is looking at it, not during a
        live race-setup window.

        Scoped to what is genuinely unsatisfiable. A tournament with no preset and
        no ``seed_generator`` is **not** one of those: ``_attach_seed`` skips
        quietly and the room opens without a seed, which is a real way to run a
        race (the seed comes from somewhere else, or the goal is the settings).
        """
        if racetime_auto_create_rooms and racetime_bot_id is None:
            raise ValueError(
                'Automatic race rooms need a racetime bot — pick one, or turn '
                'automatic rooms off.'
            )

    async def create_tournament(
        self,
        name: str,
        description: Optional[str] = None,
        short_name: Optional[str] = None,
        seed_generator: Optional[str] = None,
        bracket_url: Optional[str] = None,
        rules_url: Optional[str] = None,
        tournament_format: Optional[str] = None,
        triforce_access_message: Optional[str] = None,
        randomizer_notes: Optional[str] = None,
        average_match_duration: Optional[int] = None,
        max_match_duration: Optional[int] = None,
        is_active: bool = True,
        is_test: bool = False,
        players_release_seed: bool = False,
        uses_tournament_room: bool = True,
        players_per_match: int = 2,
        required_commentators: int = 1,
        required_trackers: int = 1,
        allow_player_match_requests: bool = True,
        config: Optional[Dict[str, Any]] = None,
        preset_id: Optional[int] = None,
        hard_preset_id: Optional[int] = None,
        racetime_bot_id: Optional[int] = None,
        race_room_profile_id: Optional[int] = None,
        racetime_auto_create_rooms: bool = False,
        room_open_minutes_before: int = 30,
        stage_reminder_minutes: int = 30,
        require_racetime_link: bool = False,
        racetime_bot_runs_room: bool = False,
        racetime_seed_post_minutes: int = 10,
        racetime_close_finish_ms: int = 1000,
        racetime_default_goal: Optional[str] = None,
        event_start_date: Any = None,
        event_end_date: Any = None,
        tournament_hours: Optional[Dict[date, Tuple[str, str]]] = None,
        signups_open_at: Optional[datetime] = None,
        signups_close_at: Optional[datetime] = None,
        actor: Optional[User] = None,
    ) -> Tournament:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can create tournaments",
        )

        if not name or not name.strip():
            raise ValueError("Tournament name is required")

        if seed_generator == "None":
            seed_generator = None

        config = validate_tournament_config(config)
        event_start_date, event_end_date = self._normalize_event_dates(
            event_start_date, event_end_date,
        )
        if signups_open_at and signups_close_at and signups_close_at <= signups_open_at:
            raise ValueError('Signups must close after they open.')
        tournament_hours = self._normalize_tournament_hours(tournament_hours)
        preset = await self._resolve_preset(preset_id)
        if preset is not None:
            # The preset owns the randomizer: everything that still reads
            # ``seed_generator`` (triforce texts, the schedule's roll button)
            # would otherwise treat a preset-only tournament as having none.
            seed_generator = preset.randomizer
        preset_id = preset.id if preset is not None else None
        hard_preset = await self._resolve_hard_preset(hard_preset_id, preset)
        hard_preset_id = hard_preset.id if hard_preset is not None else None
        racetime_bot_id = await self._resolve_racetime_bot_id(racetime_bot_id)
        race_room_profile_id = await self._resolve_race_room_profile_id(race_room_profile_id)
        self._check_automation_prerequisites(
            racetime_auto_create_rooms=racetime_auto_create_rooms,
            racetime_bot_id=racetime_bot_id,
        )

        tournament = await self.repository.create(
            name=name.strip(),
            short_name=self._short_name(short_name),
            description=description.strip() if description else None,
            seed_generator=seed_generator,
            bracket_url=normalize_http_url(bracket_url, 'The bracket link'),
            rules_url=normalize_http_url(rules_url, 'The rules link'),
            tournament_format=tournament_format.strip() if tournament_format else None,
            triforce_access_message=triforce_access_message.strip() if triforce_access_message else None,
            randomizer_notes=randomizer_notes.strip() if randomizer_notes else None,
            average_match_duration=average_match_duration,
            max_match_duration=max_match_duration,
            is_active=is_active,
            is_test=is_test,
            players_release_seed=players_release_seed,
            uses_tournament_room=uses_tournament_room,
            players_per_match=players_per_match,
            required_commentators=self._crew_requirement(required_commentators, 'commentators'),
            required_trackers=self._crew_requirement(required_trackers, 'trackers'),
            allow_player_match_requests=allow_player_match_requests,
            config=config,
            preset_id=preset_id,
            hard_preset_id=hard_preset_id,
            racetime_bot_id=racetime_bot_id,
            race_room_profile_id=race_room_profile_id,
            racetime_auto_create_rooms=racetime_auto_create_rooms,
            room_open_minutes_before=room_open_minutes_before,
            stage_reminder_minutes=stage_reminder_minutes,
            require_racetime_link=require_racetime_link,
            racetime_bot_runs_room=racetime_bot_runs_room,
            racetime_seed_post_minutes=self._non_negative(racetime_seed_post_minutes, 'Seed post minutes'),
            racetime_close_finish_ms=self._non_negative(racetime_close_finish_ms, 'The close-finish gap'),
            racetime_default_goal=(racetime_default_goal.strip() if racetime_default_goal else None),
            event_start_date=event_start_date,
            event_end_date=event_end_date,
            tournament_hours=tournament_hours,
            signups_open_at=signups_open_at,
            signups_close_at=signups_close_at,
        )

        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_CREATED,
            {'tournament_id': tournament.id, 'name': tournament.name},
        )

        return tournament

    async def update_tournament(
        self,
        tournament: Tournament,
        name: Optional[str] = None,
        description: Optional[str] = None,
        short_name: Optional[str] = None,
        seed_generator: Optional[str] = None,
        bracket_url: Optional[str] = None,
        rules_url: Optional[str] = None,
        tournament_format: Optional[str] = None,
        triforce_access_message: Optional[str] = None,
        randomizer_notes: Optional[str] = None,
        average_match_duration: Optional[int] = None,
        max_match_duration: Optional[int] = None,
        is_active: Optional[bool] = None,
        is_test: Optional[bool] = None,
        players_release_seed: Optional[bool] = None,
        uses_tournament_room: Optional[bool] = None,
        players_per_match: Optional[int] = None,
        required_commentators: Optional[int] = None,
        required_trackers: Optional[int] = None,
        allow_player_match_requests: Optional[bool] = None,
        config: Optional[Dict[str, Any]] = None,
        preset_id: Any = _UNSET,
        hard_preset_id: Any = _UNSET,
        racetime_bot_id: Any = _UNSET,
        race_room_profile_id: Any = _UNSET,
        racetime_auto_create_rooms: Optional[bool] = None,
        room_open_minutes_before: Optional[int] = None,
        stage_reminder_minutes: Optional[int] = None,
        require_racetime_link: Optional[bool] = None,
        racetime_bot_runs_room: Optional[bool] = None,
        racetime_seed_post_minutes: Optional[int] = None,
        racetime_close_finish_ms: Optional[int] = None,
        racetime_default_goal: Any = _UNSET,
        event_start_date: Any = _UNSET,
        event_end_date: Any = _UNSET,
        tournament_hours: Any = _UNSET,
        signups_open_at: Any = _UNSET,
        signups_close_at: Any = _UNSET,
        actor: Optional[User] = None,
    ) -> Tournament:
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            f"User cannot edit tournament {tournament.id}",
        )

        if name is not None and (not name or not name.strip()):
            raise ValueError("Tournament name cannot be empty")

        if seed_generator == "None":
            seed_generator = None

        update_data: Dict[str, Any] = {}
        if name is not None:
            update_data['name'] = name.strip()
        if short_name is not None:
            update_data['short_name'] = self._short_name(short_name)
        if description is not None:
            update_data['description'] = description.strip() if description else None
        if seed_generator is not None:
            update_data['seed_generator'] = seed_generator
        if bracket_url is not None:
            update_data['bracket_url'] = normalize_http_url(bracket_url, 'The bracket link')
        if rules_url is not None:
            update_data['rules_url'] = normalize_http_url(rules_url, 'The rules link')
        if tournament_format is not None:
            update_data['tournament_format'] = tournament_format.strip() if tournament_format else None
        if triforce_access_message is not None:
            update_data['triforce_access_message'] = (
                triforce_access_message.strip() if triforce_access_message else None
            )
        if randomizer_notes is not None:
            update_data['randomizer_notes'] = randomizer_notes.strip() if randomizer_notes else None
        if average_match_duration is not None:
            update_data['average_match_duration'] = average_match_duration
        if max_match_duration is not None:
            update_data['max_match_duration'] = max_match_duration
        if is_active is not None:
            update_data['is_active'] = is_active
        if is_test is not None:
            update_data['is_test'] = is_test
        if players_release_seed is not None:
            update_data['players_release_seed'] = players_release_seed
        if uses_tournament_room is not None:
            update_data['uses_tournament_room'] = uses_tournament_room
        if players_per_match is not None:
            update_data['players_per_match'] = players_per_match
        if required_commentators is not None:
            update_data['required_commentators'] = self._crew_requirement(
                required_commentators, 'commentators'
            )
        if required_trackers is not None:
            update_data['required_trackers'] = self._crew_requirement(
                required_trackers, 'trackers'
            )
        if allow_player_match_requests is not None:
            update_data['allow_player_match_requests'] = allow_player_match_requests
        if config is not None:
            update_data['config'] = validate_tournament_config(config)
        if preset_id is not _UNSET:
            preset = await self._resolve_preset(preset_id)
            update_data['preset_id'] = preset.id if preset is not None else None
            # The preset decides which randomizer rolls the seed, so attaching one
            # rewrites ``seed_generator`` and detaching one clears it — unless this
            # same call named a generator explicitly (the legacy REST shape).
            if preset is not None:
                update_data['seed_generator'] = preset.randomizer
            elif seed_generator is None:
                update_data['seed_generator'] = None
        if hard_preset_id is not _UNSET:
            # Validated against whichever standard preset this call leaves in
            # place, not the one that happened to be stored: attaching both in a
            # single edit must still be checked against each other.
            standard = (
                preset if preset_id is not _UNSET
                else await self.preset_repository.get_by_id(tournament.preset_id)  # type: ignore[attr-defined]
                if tournament.preset_id else None  # type: ignore[attr-defined]
            )
            hard = await self._resolve_hard_preset(hard_preset_id, standard)
            update_data['hard_preset_id'] = hard.id if hard is not None else None
        elif preset_id is not _UNSET and tournament.hard_preset_id is not None:  # type: ignore[attr-defined]
            # The standard preset moved out from under an existing hard preset.
            # Re-validate the pair rather than leaving a mismatched randomizer.
            existing_hard = await self.preset_repository.get_by_id(tournament.hard_preset_id)  # type: ignore[attr-defined]
            if existing_hard is not None and (
                preset is None or existing_hard.randomizer != preset.randomizer
            ):
                raise ValueError(
                    "This tournament has a harder preset that no longer matches. "
                    "Change or clear the harder preset in the same edit."
                )
        if racetime_bot_id is not _UNSET:
            update_data['racetime_bot_id'] = await self._resolve_racetime_bot_id(racetime_bot_id)
        if race_room_profile_id is not _UNSET:
            update_data['race_room_profile_id'] = await self._resolve_race_room_profile_id(race_room_profile_id)
        if racetime_auto_create_rooms is not None:
            update_data['racetime_auto_create_rooms'] = racetime_auto_create_rooms
        if room_open_minutes_before is not None:
            update_data['room_open_minutes_before'] = room_open_minutes_before
        if stage_reminder_minutes is not None:
            update_data['stage_reminder_minutes'] = stage_reminder_minutes
        if require_racetime_link is not None:
            update_data['require_racetime_link'] = require_racetime_link
        if racetime_bot_runs_room is not None:
            update_data['racetime_bot_runs_room'] = racetime_bot_runs_room
        if racetime_seed_post_minutes is not None:
            update_data['racetime_seed_post_minutes'] = self._non_negative(
                racetime_seed_post_minutes, 'Seed post minutes',
            )
        if racetime_close_finish_ms is not None:
            update_data['racetime_close_finish_ms'] = self._non_negative(
                racetime_close_finish_ms, 'The close-finish gap',
            )
        if racetime_default_goal is not _UNSET:
            update_data['racetime_default_goal'] = (
                racetime_default_goal.strip() if racetime_default_goal else None
            )
        if event_start_date is not _UNSET or event_end_date is not _UNSET:
            # Validate the window as a pair, using the current stored value for
            # whichever bound the caller left untouched.
            raw_start = event_start_date if event_start_date is not _UNSET else tournament.event_start_date
            raw_end = event_end_date if event_end_date is not _UNSET else tournament.event_end_date
            norm_start, norm_end = self._normalize_event_dates(raw_start, raw_end)
            if event_start_date is not _UNSET:
                update_data['event_start_date'] = norm_start
            if event_end_date is not _UNSET:
                update_data['event_end_date'] = norm_end
        if tournament_hours is not _UNSET:
            update_data['tournament_hours'] = self._normalize_tournament_hours(tournament_hours)
        if signups_open_at is not _UNSET or signups_close_at is not _UNSET:
            # Validated as a pair against the stored value for whichever bound
            # this call left alone, so an edit that only moves the close date
            # cannot land it before an open date already saved.
            raw_open = signups_open_at if signups_open_at is not _UNSET else tournament.signups_open_at
            raw_close = signups_close_at if signups_close_at is not _UNSET else tournament.signups_close_at
            if raw_open and raw_close and raw_close <= raw_open:
                raise ValueError('Signups must close after they open.')
            if signups_open_at is not _UNSET:
                update_data['signups_open_at'] = signups_open_at
            if signups_close_at is not _UNSET:
                update_data['signups_close_at'] = signups_close_at

        # Checked against the *post-update* shape, falling back to what is stored
        # for every field this call left alone: clearing the bot on a tournament
        # that already has automatic rooms on is the same broken state as
        # switching rooms on without one, and must be refused the same way.
        def _after(key: str, default=None):
            if key in update_data:
                return update_data[key]
            return getattr(tournament, key, default)

        self._check_automation_prerequisites(
            racetime_auto_create_rooms=_after('racetime_auto_create_rooms', False),
            racetime_bot_id=_after('racetime_bot_id'),
        )

        result = await self.repository.update(tournament, **update_data)

        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_UPDATED,
            {'tournament_id': tournament.id, 'changed_fields': list(update_data.keys())},
        )

        return result

    async def deletion_preview(self, tournament: Tournament) -> Dict[str, int]:
        """Counts of the rows a permanent delete would cascade through.

        The confirmation dialog states them, so nobody types the phrase without
        seeing the matches and results that go with the tournament.
        """
        return await self.repository.dependent_counts(tournament.id)

    async def delete_tournament(
        self,
        tournament: Tournament,
        actor: Optional[User] = None,
        confirmation: Optional[str] = None,
    ) -> None:
        """Permanently delete a tournament and everything that cascades from it.

        Two gates, both enforced here rather than in the dialog that draws them:
        the tournament must already be inactive (so deletion is never the first
        step, and an active event cannot be lost to a stray click), and the
        caller must repeat ``permanently delete`` verbatim. Deactivating is
        reversible; this is not.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can delete tournaments",
        )
        if tournament.is_active:
            raise ValueError(
                'Deactivate this tournament before deleting it — uncheck Active, '
                'save, then delete.'
            )
        if (confirmation or '').strip().lower() != DELETE_CONFIRMATION_PHRASE:
            raise ValueError(
                f'Type “{DELETE_CONFIRMATION_PHRASE}” to confirm this deletion.'
            )
        details = {
            'tournament_id': tournament.id,
            'name': tournament.name,
            'deleted': await self.repository.dependent_counts(tournament.id),
        }
        await tournament.delete()
        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_DELETED,
            details,
        )

    async def _clear_discord_provenance(
        self, tournament: Tournament, target: User, grant: TournamentGrant
    ) -> None:
        """Drop any record that the guild-role sync made this grant.

        A staff member touching the grant by hand makes it theirs, so the sync
        must stop treating it as its own to revoke — the same pinning
        ``UserRoleRepository.add`` does by promoting a row to ``RoleSource.MANUAL``.
        """
        await self.discord_grant_repository.remove(target, tournament.id, grant)

    async def add_admin(self, tournament: Tournament, target: User, actor: Optional[User] = None) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can grant Tournament Admin",
        )
        await TenantMembershipService.require_community_users([target.id], actor)
        await self.repository.set_tournament_grant(
            tournament, target, TournamentGrant.TOURNAMENT_ADMIN, granted=True
        )
        await self._clear_discord_provenance(
            tournament, target, TournamentGrant.TOURNAMENT_ADMIN
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_ADMIN_GRANTED,
            {'tournament_id': tournament.id, 'target_user_id': target.id},
        )

    async def remove_admin(self, tournament: Tournament, target: User, actor: Optional[User] = None) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can revoke Tournament Admin",
        )
        await self.repository.set_tournament_grant(
            tournament, target, TournamentGrant.TOURNAMENT_ADMIN, granted=False
        )
        await self._clear_discord_provenance(
            tournament, target, TournamentGrant.TOURNAMENT_ADMIN
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_ADMIN_REVOKED,
            {'tournament_id': tournament.id, 'target_user_id': target.id},
        )

    async def add_crew_coordinator(self, tournament: Tournament, target: User, actor: Optional[User] = None) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can grant Crew Coordinator",
        )
        await TenantMembershipService.require_community_users([target.id], actor)
        await self.repository.set_tournament_grant(
            tournament, target, TournamentGrant.CREW_COORDINATOR, granted=True
        )
        await self._clear_discord_provenance(
            tournament, target, TournamentGrant.CREW_COORDINATOR
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_CREW_COORDINATOR_GRANTED,
            {'tournament_id': tournament.id, 'target_user_id': target.id},
        )

    async def remove_crew_coordinator(self, tournament: Tournament, target: User, actor: Optional[User] = None) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can revoke Crew Coordinator",
        )
        await self.repository.set_tournament_grant(
            tournament, target, TournamentGrant.CREW_COORDINATOR, granted=False
        )
        await self._clear_discord_provenance(
            tournament, target, TournamentGrant.CREW_COORDINATOR
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.TOURNAMENT_CREW_COORDINATOR_REVOKED,
            {'tournament_id': tournament.id, 'target_user_id': target.id},
        )

    async def get_all_tournaments(self, active_only: bool = False) -> list[Tournament]:
        return await self.repository.get_all(active_only=active_only)

    async def get_tournament_by_id(self, tournament_id: int) -> Optional[Tournament]:
        return await self.repository.get_by_id(tournament_id)

    async def list_for_viewer(
        self, viewer: Optional[User], active_only: bool = False,
    ) -> list[Tournament]:
        """Inactive tournaments are hidden from players; admins see them all."""
        if not active_only and not await AuthService.can_view_admin(viewer):
            active_only = True
        return await self.repository.get_all(active_only=active_only)

    async def get_for_viewer(
        self, viewer: Optional[User], tournament_id: int,
    ) -> Optional[Tournament]:
        """One tournament, or ``None`` when missing or inactive and ``viewer`` isn't an admin."""
        tournament = await self.repository.get_by_id(tournament_id)
        if tournament is None or tournament.is_active or await AuthService.can_view_admin(viewer):
            return tournament
        return None

    async def list_schedulable(
        self, actor: Optional[User], keep_id: Optional[int] = None,
    ) -> tuple[list[Tournament], dict[int, int]]:
        """The admin match dialog's Tournament options, and their entrant counts.

        Two thirds of the unfiltered list could not be scheduled into, and the
        dialog said so only after Create was pressed: an *inactive* tournament
        was offered on equal footing with a live one, and six of ten options
        opened an empty player menu with no message. Inactive ones are dropped
        (``keep_id`` spares the one an existing match already points at, so
        editing it does not blank its own chip); the counts let the dialog
        label the rest.

        A tournament admin is offered only the tournaments they run, the same
        ones ``create_match`` will accept from them.
        """
        tournaments = await self.repository.get_all(active_only=True)
        if not await AuthService.is_staff(actor):
            admin_ids, _ = await AuthService.tournament_scope(actor)
            tournaments = [t for t in tournaments if t.id in admin_ids]
        if keep_id is not None and not any(t.id == keep_id for t in tournaments):
            current = await self.repository.get_by_id(keep_id)
            if current is not None:
                tournaments = sorted([*tournaments, current], key=lambda t: t.name)
        return tournaments, await self.repository.enrolment_counts()

    async def list_player_requestable(
        self, user: Optional[User] = None,
    ) -> list[Tournament]:
        """Tournaments the request-a-match dialog may offer.

        Bracket-run tournaments are excluded — their matches come from the
        bracket. Pass ``user`` for the enrolled-only list, omit it for the
        dialog's "show all tournaments" mode.
        """
        return await self.repository.get_player_requestable(
            user_id=user.id if user is not None else None,
        )

    async def get_tournaments_by_ids(self, tournament_ids: list[int]) -> list[Tournament]:
        return await self.repository.get_by_ids(tournament_ids)

    async def get_enrolled_players(self, tournament: Tournament) -> list:
        return await self.repository.get_enrolled_players(tournament)

    async def list_players(self, tournament: Tournament, actor: User) -> list[User]:
        """The tournament's roster, for whoever may edit the tournament."""
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            f"User cannot view tournament {tournament.id}'s roster",
        )
        rows = await self.repository.get_enrolled_players(tournament)
        return [row.user for row in rows]

    async def enroll_player(self, tournament: Tournament, target: User, actor: User) -> None:
        """Enrol one player in one tournament, from the tournament's own screen.

        Refuses a non-member: the picker is scoped to the community, but UI-only
        gating is not gating — a Discord handler or a future page reaching this
        method must hit the same wall.
        """
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            'Only staff or this tournament\'s admins can manage its entrants',
        )
        if not await TenantMembershipService().is_member(target):
            raise ValueError(
                f'{target.display_name or target.username} is not a member of this '
                'community — add them on the Members tab first.'
            )
        if await self.repository.is_player_enrolled(tournament, target):
            raise ValueError(
                f'{target.display_name or target.username} is already enrolled.'
            )
        await self.repository.enroll_player(tournament, target)
        await self._record_enrolment(actor, target, tournament, added=True)

    async def unenroll_player(self, tournament: Tournament, target: User, actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            'Only staff or this tournament\'s admins can manage its entrants',
        )
        await self.repository.unenroll_player(tournament, target)
        await self._record_enrolment(actor, target, tournament, added=False)

    async def get_enrolled_players_by_user(self, user: User) -> list:
        return await self.repository.get_enrolled_players_by_user(user)

    async def get_enrolled_players_by_tournament_id(self, tournament_id: int) -> list:
        return await self.repository.get_enrolled_players_by_tournament_id(tournament_id)
