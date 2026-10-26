"""Station Assignment Dialog - Assign stations to match players"""

from typing import Callable, Optional

from nicegui import app, ui

from application.services import MatchService, StationService, get_user_from_session
from application.services.system_config_service import SystemConfigService
from application.utils.match_labels import match_model_label
from models import STATION_REGEXES, Match, StationFormat
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.help import resolve_help
from theme.notify import notify_error

# Copy for the dialog's jobs. 'checkin' is the proctor's combined
# step 1 + 2 (seat the players, then check the match in); 'start' asks again
# for stations skipped at check-in, then starts; 'stations' is correcting the
# seating of a match that is already checked in.
_PURPOSE_COPY = {
    'checkin': {
        'title': 'Check in — {match}',
        'lead': 'Seat each player at a station, then check the match in.',
        'submit': 'Check in & seat',
    },
    'start': {
        'title': 'Start — {match}',
        'lead': 'Not everyone has a station yet. Seat them now, or leave a blank and start anyway.',
        'submit': 'Start match',
    },
    'stations': {
        'title': 'Assign stations — {match}',
        'lead': 'Update the station each player is sitting at.',
        'submit': 'Save stations',
    },
}


class StationAssignmentDialog:
    """Dialog for assigning stations to players in a match."""

    def __init__(
        self,
        match: Match,
        on_submit: Optional[Callable] = None,
        *,
        purpose: str = 'stations',
    ):
        """
        Initialize the station assignment dialog.

        Args:
            match: The match with players to assign stations to
            on_submit: Optional callback when stations are assigned
            purpose: Which job the dialog is doing — ``'checkin'`` or
                ``'start'`` when the caller seats or starts the match
                afterwards (and therefore owns the success toast), ``'stations'`` when this dialog is the whole
                job.
        """
        self.match = match
        self.on_submit = on_submit
        self.purpose = purpose if purpose in _PURPOSE_COPY else 'stations'
        self.match_service = MatchService()
        self.dialog = None
        self.station_inputs = {}

    async def open(self):
        """Open the dialog and load match data."""
        await self.match.fetch_related('tournament', 'players', 'players__user')
        fmt = await SystemConfigService.get_station_format()
        copy = _PURPOSE_COPY[self.purpose]

        # A community that has defined no stations keeps the historical
        # free-text field; once it has a pool, the proctor picks from it.
        stations = await StationService().list_stations(active_only=True)
        occupied = await self.match_service.occupied_stations_for_dialog(self.match.id)

        title = copy['title'].format(match=match_model_label(self.match, with_context=False))
        help = await resolve_help(['proctor-stations'])
        with form_dialog(title, card_classes='dialog-card dialog-card--sm', help=help) as self.dialog:
            with ui.column().classes('full-width gap-3'):
                context = []
                if self.match.tournament:
                    context.append(self.match.tournament.name)
                if self.match.scheduled_at:
                    from application.utils.timezone import format_local_datetime
                    context.append(format_local_datetime(self.match.scheduled_at))
                if context:
                    ui.label(' · '.join(context)).classes('text-body2 text-grey-7')

                if not self.match.players:
                    ui.label('No players assigned to this match').classes('text-grey-7')
                else:
                    with ui.row().classes('items-center no-wrap full-width gap-1'):
                        ui.label(copy['lead']).classes('text-body2 col')
                        # Only offered for a head-to-head: the draw's whole job
                        # is putting two players on opposite sides, and it
                        # refuses any other player count.
                        if len(self.match.players) == 2 and stations:
                            ui.button(
                                'Randomize',
                                icon='casino',
                                on_click=self._handle_randomize,
                            ).props('flat dense color=primary').tooltip(
                                'Draw a free station for each player, on opposite '
                                'sides of the room'
                            )

                    for player in self.match.players:
                        # The player's name is the field's label: a name line
                        # over a "Station" field doubled each row's height.
                        name = player.user.preferred_name or player.user.username

                        # Station input (leave blank to clear assignment).
                        # ``occupied`` excludes this match, so a player's own
                        # current station never shows as taken.
                        if stations:
                            # An occupied station names the match sitting at
                            # it, not its id: the proctor's next move is to
                            # go and find those players, and "match #23"
                            # tells them nothing about who to look for.
                            options = {
                                s.name: (
                                    f'{s.name} — in use ({occupied[s.name]})'
                                    if s.name in occupied
                                    else (f'{s.name} · {s.section}' if s.section else s.name)
                                )
                                for s in stations
                            }
                            # An assignment made before the pool existed (or
                            # to a since-removed station) would otherwise
                            # render as an empty select, hiding what the
                            # schedule row still shows.
                            if player.assigned_station and player.assigned_station not in options:
                                options[player.assigned_station] = (
                                    f'{player.assigned_station} — not in the station pool'
                                )
                            station_input = ui.select(
                                options=options,
                                label=name,
                                with_input=True,
                                clearable=True,
                            ).props('outlined dense hide-bottom-space').classes('full-width')
                        else:
                            station_input = ui.input(
                                label=name,
                                placeholder=('Station number, e.g. 12' if fmt == StationFormat.NUMERIC
                                             else 'Station, e.g. A1, B2, Station 3'),
                                validation={'Invalid station format': lambda v: not v or STATION_REGEXES[fmt].fullmatch(v) is not None},
                            ).props('outlined dense hide-bottom-space maxlength=50').classes('full-width')
                            if fmt == StationFormat.NUMERIC:
                                # Phones open the number pad, not the full keyboard.
                                station_input.props('inputmode=numeric')

                        if player.assigned_station:
                            station_input.value = player.assigned_station

                        self.station_inputs[player.id] = station_input

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=self.dialog.close).props('flat')
                ui.button(copy['submit'], on_click=self._handle_submit).props('color=primary')
        
        self.dialog.open()
    
    async def _handle_randomize(self):
        """Fill the pickers with a drawn seating, without committing it.

        The proctor still submits, so a draw they dislike costs them a second
        click rather than a correction.
        """
        try:
            actor = await get_user_from_session(app.storage.user)
            suggestion = await self.match_service.suggest_stations(
                self.match.id, actor=actor,
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return

        for player_id, station in suggestion.assignments.items():
            station_input = self.station_inputs.get(player_id)
            if station_input is None:
                continue
            # A drawn station is guaranteed to be in the active pool, but the
            # select only offers the options built when the dialog opened.
            if hasattr(station_input, 'options') and station not in station_input.options:
                station_input.options[station] = station
                station_input.update()
            station_input.value = station

        for note in suggestion.relaxations:
            ui.notify(note, color='warning')
        if not suggestion.relaxations:
            ui.notify('Seats drawn — opposite sides, nobody crowded.', color='positive')

    async def _handle_submit(self):
        """Handle station assignment submission."""
        try:
            assignments = {}
            for player_id, station_input in self.station_inputs.items():
                station = station_input.value.strip() if station_input.value else None
                assignments[player_id] = station

            actor = await get_user_from_session(app.storage.user)
            await self.match_service.assign_stations(self.match.id, assignments, actor=actor)

            # In 'checkin' mode the caller still has to seat the match, so it
            # owns the success toast — saying "done" here would be premature.
            if self.purpose == 'stations':
                ui.notify(
                    f'Stations updated for '
                    f'{match_model_label(self.match, with_context=False)}.',
                    color='positive',
                )

            if self.on_submit:
                await self.on_submit(self.match)

            self.dialog.close()

        except (ValueError, PermissionError) as e:
            notify_error(e)
