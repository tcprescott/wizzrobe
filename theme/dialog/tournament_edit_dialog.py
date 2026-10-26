from datetime import date, timedelta

from nicegui import app, ui

from application.services import (
    AuthService,
    ChallongeService,
    FeatureFlagService,
    PresetService,
    RaceRoomProfileService,
    RacetimeBotService,
    StartggService,
    TournamentService,
    get_user_from_session,
)
from application.services.tournament_service import DELETE_CONFIRMATION_PHRASE
from application.tenant_context import require_tenant_id
from models import FeatureFlag
from theme.busy import busy_click
from theme.dialog._helpers import (
    datetime_to_local_input,
    dialog_actions,
    dialog_header,
    local_input_to_datetime,
    mobile_sheet,
    native_date_input,
    native_datetime_input,
    native_time_input,
    submit_on_enter,
)
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.startgg_section import render_startgg_section
from theme.markdown_editor import MarkdownEditor
from theme.notify import notify_error


class TournamentDialog:
    def __init__(self, tournament=None, on_submit=None, on_delete=None):
        self.tournament = tournament
        self.on_submit = on_submit
        # Called with no arguments after a permanent delete, so the surface that
        # opened this dialog can drop the row it is still showing.
        self.on_delete = on_delete
        self.dialog = None
        self.tournament_service = TournamentService()
        self.challonge_service = ChallongeService()
        self.preset_service = PresetService()
        self.racetime_bot_service = RacetimeBotService()
        self.race_room_profile_service = RaceRoomProfileService()

    async def open(self):
        is_create = self.tournament is None
        title = 'Add Tournament' if is_create else 'Edit Tournament'
        actor = await get_user_from_session(app.storage.user)
        can_sync = await AuthService.can_manage_sync(actor)
        is_staff = await AuthService.is_staff(actor)
        can_delete = not is_create and is_staff
        # This dialog opens from the (ungated) Tournaments tab, so its Challonge
        # section has to check the flag itself — link_tournament refuses without it,
        # and refuses anyone but staff.
        challonge_live = (
            is_staff
            and self.challonge_service.is_configured()
            and await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE)
        )
        # Racetime room automation is only offered to sync managers of a community
        # that has the feature on, and only the bot categories this tenant is
        # authorized for are selectable. Same reason as Challonge above: this
        # dialog opens from the ungated Tournaments tab, so it checks the flag
        # itself — every racetime surface behind it already refuses without it.
        startgg_live = (
            StartggService.is_configured()
            and await FeatureFlagService().is_enabled(FeatureFlag.STARTGG)
        )
        racetime_live = can_sync and await FeatureFlagService().is_enabled(
            FeatureFlag.RACETIME_ROOMS
        )
        rt = {}
        if racetime_live:
            rt['bots'] = await self.racetime_bot_service.list_authorized_for_tenant(require_tenant_id())
            rt['profiles'] = await self.race_room_profile_service.list_selectable()

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(title, dialog)
            with ui.column().classes('gap-2'):
                ui.label('* required').classes('required-legend')
                name_input = ui.input(
                    'Tournament Name *',
                    value=self.tournament.name if self.tournament else '',
                ).props('required').classes('input-full-width')
                name_input.props(
                    'hint="Everything below has a sensible default — you can come back to it."'
                )
                short_name_input = ui.input(
                    'Short name',
                    value=(self.tournament.short_name or '') if self.tournament else '',
                ).props('maxlength=40 counter').classes('input-full-width')
                short_name_input.props(
                    'hint="Shown on the run sheets and timeline, where the full name won\'t fit. '
                    'Leave blank to use the full name."'
                )
                description_input = MarkdownEditor(
                    'Description',
                    value=self.tournament.description if self.tournament and self.tournament.description else '',
                    placeholder='What players should know: format, schedule, where to be.',
                )
                is_active_checkbox = ui.checkbox(
                    'Active',
                    value=self.tournament.is_active if self.tournament else True,
                )
                with ui.column().classes('gap-0'):
                    is_test_checkbox = ui.checkbox(
                        'Test tournament',
                        value=self.tournament.is_test if self.tournament else False,
                    )
                    ui.label(
                        "Left out of the event info tournament list. Use it for a "
                        "rehearsal, like a Challonge integration check."
                    ).classes('text-caption text-grey q-ml-xl')
                with ui.column().classes('gap-0'):
                    uses_room_checkbox = ui.checkbox(
                        'Plays in the tournament room',
                        value=self.tournament.uses_tournament_room if self.tournament else True,
                    )
                    ui.label(
                        "Untick for a tournament played elsewhere. Its matches that "
                        "aren't on a stage then don't count toward the room's capacity."
                    ).classes('text-caption text-grey q-ml-xl')

                # Everything below is optional and defaulted. Collapsed on create
                # so the cheapest correct action — type a name, save — stops
                # looking like the riskiest one; expanded on edit, because an
                # admin who came back to configure racetime should not have to
                # hunt for it.
                sections_open = not is_create

                with ui.expansion(
                    'Scheduling', icon='schedule', value=sections_open,
                ).classes('w-full').props('dense'):
                    with ui.row().classes('form-pair gap-2'):
                        average_match_duration_input = ui.number(
                            'Avg Match Duration (min)',
                            value=self.tournament.average_match_duration if self.tournament and self.tournament.average_match_duration else None,
                            min=0,
                        ).props('inputmode=numeric')
                        max_match_duration_input = ui.number(
                            'Max Match Duration (min)',
                            value=self.tournament.max_match_duration if self.tournament and self.tournament.max_match_duration else None,
                            min=0,
                        ).props('inputmode=numeric')
                    with ui.row().classes('form-pair gap-2'):
                        players_per_match_input = ui.number(
                            'Players per Match',
                            value=self.tournament.players_per_match if self.tournament else 2,
                            min=1, max=100,
                        ).props('inputmode=numeric')

                    # --- Player signups ---
                    ui.separator()
                    ui.label('Player signups').classes('text-bold')
                    ui.label(
                        'When players can sign themselves up on the Tournaments tab. '
                        'Leave both blank to keep signups open indefinitely. Staff can '
                        'always add or remove entrants, window or no window.'
                    ).classes('text-caption text-grey')
                    with ui.row().classes('form-pair gap-2'):
                        signups_open_input = native_datetime_input(
                            'Signups open',
                            datetime_to_local_input(
                                self.tournament.signups_open_at if self.tournament else None
                            ),
                            dense=True,
                        )
                        signups_close_input = native_datetime_input(
                            'Signups close',
                            datetime_to_local_input(
                                self.tournament.signups_close_at if self.tournament else None
                            ),
                            dense=True,
                        )

                    # --- Tournament Days (per-tournament override of the tenant setting) ---
                    ui.separator()
                    ui.label('Tournament Days').classes('text-bold')
                    ui.label(
                        'Override the community event window and per-day match hours for '
                        'this tournament. Leave blank to use the community default.'
                    ).classes('text-caption text-grey')

                    t = self.tournament
                    with ui.row().classes('form-pair gap-2'):
                        event_start_input = native_date_input(
                            'Event Start Date',
                            t.event_start_date.isoformat() if t and t.event_start_date else '',
                            clearable=True,
                        )
                        event_end_input = native_date_input(
                            'Event End Date',
                            t.event_end_date.isoformat() if t and t.event_end_date else '',
                            clearable=True,
                        )

                    # Stored blob seeds the grid; snapshotted on every refresh so
                    # typed-but-unsaved values survive a date-window change.
                    hours_state: dict = dict(t.tournament_hours) if t and t.tournament_hours else {}
                    hours_inputs: dict[str, dict] = {}

                    @ui.refreshable
                    def render_hours() -> None:
                        for d_iso, row in hours_inputs.items():
                            hours_state[d_iso] = {
                                'open': (row['open'].value or ''),
                                'close': (row['close'].value or ''),
                            }
                        hours_inputs.clear()
                        start_raw = (event_start_input.value or '').strip()
                        end_raw = (event_end_input.value or '').strip()
                        try:
                            start_d = date.fromisoformat(start_raw)
                            end_d = date.fromisoformat(end_raw)
                        except ValueError:
                            ui.label(
                                'Set start and end dates to configure per-day hours.'
                            ).classes('text-caption text-grey')
                            return
                        if end_d < start_d:
                            ui.label('End date is before start date.').classes('text-caption text-warning')
                            return
                        ui.label('Per-day match hours (blank = any time):').classes('text-caption q-mt-sm')
                        current = start_d
                        rendered = 0
                        while current <= end_d and rendered < 60:
                            window = hours_state.get(current.isoformat())
                            open_val = window.get('open', '') if isinstance(window, dict) else ''
                            close_val = window.get('close', '') if isinstance(window, dict) else ''
                            with ui.row().classes('form-pair items-center gap-3'):
                                ui.label(current.isoformat()).classes('w-28 text-mono')
                                open_i = native_time_input('Open', open_val)
                                close_i = native_time_input('Close', close_val)
                                hours_inputs[current.isoformat()] = {'open': open_i, 'close': close_i}
                            current += timedelta(days=1)
                            rendered += 1

                    render_hours()
                    event_start_input.on('update:model-value', lambda: render_hours.refresh())
                    event_end_input.on('update:model-value', lambda: render_hours.refresh())

                with ui.expansion(
                    'Entry & administration', icon='how_to_reg', value=sections_open,
                ).classes('w-full').props('dense'):
                    with ui.row().classes('gap-4'):
                        allow_requests_checkbox = ui.checkbox(
                            'Players may request matches',
                            value=(
                                self.tournament.allow_player_match_requests
                                if self.tournament else True
                            ),
                        )
                        allow_requests_checkbox.tooltip(
                            'Turned off automatically when a bracket is attached — a '
                            'bracket-run tournament schedules only its own matchups.'
                        )
                    rules_url_input = ui.input(
                        'Rules URL',
                        value=self.tournament.rules_url if self.tournament and self.tournament.rules_url else '',
                    ).classes('input-full-width')
                    tournament_format_input = ui.input(
                        'Tournament Format',
                        value=self.tournament.tournament_format if self.tournament and self.tournament.tournament_format else '',
                    ).classes('input-full-width')
                    randomizer_notes_input = MarkdownEditor(
                        'Randomizer Notes',
                        value=self.tournament.randomizer_notes if self.tournament and self.tournament.randomizer_notes else '',
                        placeholder='Settings, preset and build players race on. Shown on the Tournaments tab.',
                        compact=True,
                    )

                    ui.separator()
                    ui.label('Stream crew').classes('text-bold')
                    ui.label(
                        'How much approved crew a streamed match needs before the coverage '
                        'reports count it as covered. Set a role to 0 if this tournament '
                        'does not use it — otherwise every streamed match shows a gap, and '
                        'the schedule keeps offering a signup nobody will staff. A role set '
                        'to 0 stops accepting signups; anyone already signed up can still '
                        'withdraw.'
                    ).classes('text-caption text-grey')
                    with ui.row().classes('form-pair gap-2'):
                        required_commentators_input = ui.number(
                            'Commentators needed',
                            value=self.tournament.required_commentators if self.tournament else 1,
                            min=0, max=20,
                        ).props('inputmode=numeric')
                        required_trackers_input = ui.number(
                            'Trackers needed',
                            value=self.tournament.required_trackers if self.tournament else 1,
                            min=0, max=20,
                        ).props('inputmode=numeric')

                    # Sits with the stream crew rather than under Racetime, where
                    # the other "minutes before" number lives: that whole block is
                    # hidden from communities with no racetime bot, and a stage
                    # call has nothing to do with race rooms.
                    ui.separator()
                    ui.label('Stage reminder').classes('text-bold')
                    ui.label(
                        'Assigning a stage DMs the players and approved crew straight '
                        'away. This is how long before the match a second DM reminds '
                        'them which stage to walk to. Set it to 0 to send no reminder. '
                        'Nothing to do with Open Room under Racetime, which decides '
                        'when a race room is created.'
                    ).classes('text-caption text-grey')
                    with ui.row().classes('form-pair gap-2'):
                        stage_reminder_input = ui.number(
                            'Remind about the stage (min before)',
                            value=(
                                self.tournament.stage_reminder_minutes
                                if self.tournament else 30
                            ),
                            min=0, max=1440,
                        ).props('inputmode=numeric')

                with ui.expansion(
                    'Seeds & randomizer', icon='casino', value=sections_open,
                ).classes('w-full').props('dense'):
                    presets = await self.preset_service.list_selectable()
                    # None (0) clears the FK; ui.select cannot key an option on None,
                    # so a 0 sentinel stands in and maps back to None on submit.
                    preset_options = {0: '— None (no seeds) —'}
                    preset_options.update({p.id: f'{p.randomizer} / {p.name}' for p in presets})
                    default_preset = self.tournament.preset_id if self.tournament and self.tournament.preset_id else 0
                    preset_input = ui.select(
                        preset_options, label='Seed Preset', value=default_preset,
                    ).classes('input-full-width').props(
                        'hint="Picks the randomizer and the settings seeds roll with. Manage them under Admin → Seed Presets."'
                    )
                    if not presets:
                        ui.label(
                            'No presets yet — add one under Admin → Seed Presets, '
                            'or import a randomizer\'s published presets there.'
                        ).classes('text-caption text-grey')

                    # The opt-in harder preset. Same option list, same 0
                    # sentinel; the service refuses a pairing whose randomizers
                    # disagree, because the winning preset is what names the
                    # randomizer at roll time.
                    hard_preset_options = {0: '— None (no harder option) —'}
                    hard_preset_options.update(
                        {p.id: f'{p.randomizer} / {p.name}' for p in presets}
                    )
                    default_hard = (
                        self.tournament.hard_preset_id
                        if self.tournament and self.tournament.hard_preset_id else 0
                    )
                    hard_preset_input = ui.select(
                        hard_preset_options, label='Harder Preset (opt-in)',
                        value=default_hard,
                    ).classes('input-full-width').props(
                        'hint="Players can privately opt into this instead. A match only '
                        'rolls it when every player opted in, and nobody is told who did '
                        'unless all of them did."'
                    )
                    # A tournament configured before the preset became the only
                    # control still rolls on its stored generator. Say so, because
                    # saving without a preset is what turns that off.
                    legacy_generator = (
                        self.tournament.seed_generator
                        if self.tournament and self.tournament.seed_generator
                        and not self.tournament.preset_id
                        else None
                    )
                    if legacy_generator:
                        ui.label(
                            f'Currently rolling seeds with {legacy_generator} and no '
                            'preset. Pick one to replace it — saving with None turns '
                            'seed rolling off.'
                        ).classes('text-caption text-warning')
                    with ui.column().classes('gap-0'):
                        players_release_seed_checkbox = ui.checkbox(
                            'Players release their own seed',
                            value=self.tournament.players_release_seed if self.tournament else False,
                        )
                        ui.label(
                            "After check-in, each player taps Ready for seed. The seed "
                            "rolls and is sent when everyone is. Proctors and staff can "
                            "still roll it any time. Not used by racetime.gg tournaments."
                        ).classes('text-caption text-grey q-ml-xl')
                    triforce_access_message_input = MarkdownEditor(
                        'Triforce Text Access Message',
                        value=self.tournament.triforce_access_message if self.tournament and self.tournament.triforce_access_message else '',
                        placeholder="Shown to players who can't submit yet, e.g. how to get access.",
                        compact=True,
                    )

                with ui.expansion(
                    'Integrations', icon='hub', value=sections_open,
                ).classes('w-full').props('dense'):
                    bracket_url_input = ui.input(
                        'Bracket URL',
                        value=self.tournament.bracket_url if self.tournament and self.tournament.bracket_url else '',
                    ).classes('input-full-width')

                    # A tournament follows one bracket site, so a link to one hides the other.
                    if self.tournament and challonge_live and not self.tournament.startgg_event_slug:
                        ui.separator()
                        ui.label('Challonge').classes('text-bold')
                        challonge_status = ui.label().classes('text-caption text-muted')

                        def render_challonge_status() -> None:
                            if self.tournament.challonge_tournament_id:
                                challonge_status.set_text(
                                    f"Linked to Challonge tournament {self.tournament.challonge_tournament_id}"
                                )
                            else:
                                challonge_status.set_text('Not linked to a Challonge tournament.')

                        render_challonge_status()
                        challonge_input = ui.input(
                            'Challonge tournament ID or URL',
                            value=self.tournament.challonge_tournament_url or self.tournament.challonge_tournament_id or '',
                        ).classes('input-full-width')

                        async def link_and_sync() -> None:
                            value = (challonge_input.value or '').strip()
                            if not value:
                                with self.dialog:
                                    ui.notify('Enter a Challonge tournament ID or URL.', color='warning')
                                return
                            try:
                                actor = await get_user_from_session(app.storage.user)
                                await self.challonge_service.link_tournament(self.tournament.id, value, actor)
                                await self.tournament.refresh_from_db()
                                with self.dialog:
                                    ui.notify('Linked and synced with Challonge.', color='positive')
                                    render_challonge_status()
                            except (ValueError, PermissionError) as e:
                                with self.dialog:
                                    notify_error(e)

                        ui.button('Link & Sync', icon='sync', on_click=busy_click('Syncing…', link_and_sync)).props('flat color=primary')

                    if self.tournament and startgg_live and not self.tournament.challonge_tournament_id:
                        await render_startgg_section(self.tournament, dialog)

                    if racetime_live:
                        ui.separator()
                        ui.label('Racetime').classes('text-bold')
                        t = self.tournament
                        # 0 sentinel stands in for "no FK" — ui.select cannot key on None.
                        bot_options = {0: '— None —'}
                        bot_options.update({b.id: f'{b.category} ({b.name})' for b in rt['bots']})
                        default_bot = t.racetime_bot_id if t and t.racetime_bot_id else 0
                        rt['bot_input'] = ui.select(
                            bot_options, label='Racetime Bot', value=default_bot,
                        ).classes('input-full-width').props(
                            'hint="Only categories your community is authorized for appear here."'
                        )
                        profile_options = {0: '— None —'}
                        profile_options.update({p.id: p.name for p in rt['profiles']})
                        default_profile = t.race_room_profile_id if t and t.race_room_profile_id else 0
                        rt['profile_input'] = ui.select(
                            profile_options, label='Race Room Profile', value=default_profile,
                        ).classes('input-full-width')
                        rt['goal_input'] = ui.input(
                            'Default Goal',
                            value=(t.racetime_default_goal if t and t.racetime_default_goal else ''),
                        ).classes('input-full-width')
                        with ui.row().classes('form-pair gap-2'):
                            rt['open_before_input'] = ui.number(
                                'Open Room (min before)',
                                value=(t.room_open_minutes_before if t else 30),
                                min=0,
                            ).props('inputmode=numeric')
                        with ui.row().classes('gap-4'):
                            rt['auto_create_input'] = ui.checkbox(
                                'Auto-create rooms',
                                value=(t.racetime_auto_create_rooms if t else False),
                            )
                            rt['require_link_input'] = ui.checkbox(
                                'Require racetime link',
                                value=(t.require_racetime_link if t else False),
                            )

                if can_delete:
                    with ui.expansion(
                        'Danger zone', icon='warning', value=False,
                    ).classes('w-full').props('dense'):
                        ui.label(
                            'Deleting a tournament removes it and everything attached '
                            'to it — matches, entrants, brackets and triforce texts — '
                            'with no way to get it back. Deactivating it instead keeps '
                            'the history and takes it out of every picker.'
                        ).classes('text-caption text-grey')
                        delete_button = ui.button(
                            'Delete tournament', icon='delete_forever',
                            on_click=lambda: confirm_delete(),
                        ).props('flat color=negative')
                        if self.tournament.is_active:
                            delete_button.disable()
                            ui.label(
                                'Only an inactive tournament can be deleted. Uncheck '
                                'Active above, save, then reopen this dialog.'
                            ).classes('text-caption text-warning')

            async def confirm_delete() -> None:
                async def after_delete() -> None:
                    dialog.close()
                    if self.on_delete:
                        await self.on_delete()

                await confirm_tournament_delete(self.tournament, on_deleted=after_delete)

            async def submit():
                if not (name_input.value or '').strip():
                    with self.dialog:
                        ui.notify('Please fill required field(s): Tournament Name.', color='warning')
                    return
                # Per-tournament days override: blank dates inherit the tenant
                # setting; only fully-filled day rows become hour windows.
                event_start_value = (event_start_input.value or '').strip() or None
                event_end_value = (event_end_input.value or '').strip() or None
                hours_mapping: dict[date, tuple[str, str]] = {}
                for d_iso, row in hours_inputs.items():
                    open_str = (row['open'].value or '').strip()
                    close_str = (row['close'].value or '').strip()
                    if open_str and close_str:
                        hours_mapping[date.fromisoformat(d_iso)] = (open_str, close_str)
                days_kwargs = dict(
                    event_start_date=event_start_value,
                    event_end_date=event_end_value,
                    tournament_hours=hours_mapping or None,
                )

                try:
                    signup_kwargs = dict(
                        signups_open_at=local_input_to_datetime(signups_open_input.value),
                        signups_close_at=local_input_to_datetime(signups_close_input.value),
                    )
                except ValueError as e:
                    with self.dialog:
                        notify_error(e)
                    return

                rt_kwargs = {}
                if racetime_live:
                    rt_kwargs = dict(
                        racetime_bot_id=(rt['bot_input'].value or None),
                        race_room_profile_id=(rt['profile_input'].value or None),
                        racetime_auto_create_rooms=rt['auto_create_input'].value,
                        room_open_minutes_before=int(rt['open_before_input'].value or 0),
                        require_racetime_link=rt['require_link_input'].value,
                        racetime_default_goal=rt['goal_input'].value,
                    )
                try:
                    actor = await get_user_from_session(app.storage.user)
                    if self.tournament:
                        with self.dialog:
                            await self.tournament_service.update_tournament(
                                self.tournament,
                                name=name_input.value,
                                short_name=short_name_input.value,
                                description=description_input.value,
                                bracket_url=bracket_url_input.value,
                                rules_url=rules_url_input.value,
                                tournament_format=tournament_format_input.value,
                                triforce_access_message=triforce_access_message_input.value,
                                randomizer_notes=randomizer_notes_input.value,
                                average_match_duration=average_match_duration_input.value,
                                max_match_duration=max_match_duration_input.value,
                                is_active=is_active_checkbox.value,
                                is_test=is_test_checkbox.value,
                                uses_tournament_room=uses_room_checkbox.value,
                                players_per_match=players_per_match_input.value,
                                required_commentators=required_commentators_input.value,
                                required_trackers=required_trackers_input.value,
                                stage_reminder_minutes=int(stage_reminder_input.value or 0),
                                allow_player_match_requests=allow_requests_checkbox.value,
                                players_release_seed=players_release_seed_checkbox.value,
                                preset_id=(preset_input.value or None),
                                hard_preset_id=(hard_preset_input.value or None),
                                actor=actor,
                                **days_kwargs,
                                **signup_kwargs,
                                **rt_kwargs,
                            )
                            ui.notify('Tournament updated.', color='positive')
                            dialog.close()
                            if self.on_submit:
                                await self.on_submit(self.tournament)
                    else:
                        new_tournament = await self.tournament_service.create_tournament(
                            name=name_input.value,
                            short_name=short_name_input.value,
                            description=description_input.value,
                            bracket_url=bracket_url_input.value,
                            rules_url=rules_url_input.value,
                            tournament_format=tournament_format_input.value,
                            triforce_access_message=triforce_access_message_input.value,
                            randomizer_notes=randomizer_notes_input.value,
                            average_match_duration=average_match_duration_input.value,
                            max_match_duration=max_match_duration_input.value,
                            is_active=is_active_checkbox.value,
                            is_test=is_test_checkbox.value,
                            uses_tournament_room=uses_room_checkbox.value,
                            players_per_match=players_per_match_input.value,
                            required_commentators=required_commentators_input.value,
                            required_trackers=required_trackers_input.value,
                            stage_reminder_minutes=int(stage_reminder_input.value or 0),
                            allow_player_match_requests=allow_requests_checkbox.value,
                            players_release_seed=players_release_seed_checkbox.value,
                            preset_id=(preset_input.value or None),
                            hard_preset_id=(hard_preset_input.value or None),
                            actor=actor,
                            **days_kwargs,
                            **signup_kwargs,
                            **rt_kwargs,
                        )
                        with self.dialog:
                            ui.notify('Tournament created.', color='positive')
                            dialog.close()
                            if self.on_submit:
                                await self.on_submit(new_tournament)
                except (ValueError, PermissionError) as e:
                    with self.dialog:
                        notify_error(e)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                primary_btn = ui.button(
                    'Create' if is_create else 'Save', on_click=submit
                ).props('color=primary')
                primary_btn.bind_enabled_from(
                    name_input, 'value',
                    backward=lambda v: bool(v and v.strip()),
                )

            submit_on_enter(dialog, submit)
            dialog.open()


async def confirm_tournament_delete(tournament, on_deleted=None) -> None:
    """Ask before permanently deleting ``tournament``, listing what goes with it.

    Shared by the edit dialog's Danger zone and the Tournaments board's row menu,
    so both say the same thing and demand the same typed phrase. ``on_deleted``
    runs (with no arguments) once the delete has gone through.
    """
    service = TournamentService()
    preview = await service.deletion_preview(tournament)
    lines = '\n'.join(
        f'• {count} {one if count == 1 else many}'
        for one, many, count in (
            ('match', 'matches', preview['matches']),
            ('enrolled player', 'enrolled players', preview['players']),
            ('bracket stage', 'bracket stages', preview['brackets']),
            ('triforce text', 'triforce texts', preview['triforce_texts']),
        )
        if count
    ) or '• nothing else — this tournament has no matches or entrants'

    async def do_delete() -> None:
        try:
            actor = await get_user_from_session(app.storage.user)
            await service.delete_tournament(
                tournament, actor=actor, confirmation=confirm.typed_phrase,
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(f'Deleted “{tournament.name}”.', color='positive')
        if on_deleted:
            await on_deleted()

    confirm = ConfirmationDialog(
        f'Permanently delete “{tournament.name}”?\n\n'
        f'This also deletes:\n{lines}\n\n'
        'There is no undo.',
        on_confirm=do_delete,
        confirm_text='Delete forever',
        title='Delete tournament',
        require_phrase=DELETE_CONFIRMATION_PHRASE,
    )
    confirm.open()
