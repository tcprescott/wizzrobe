"""Match domain services — the scheduling and lifecycle core.

``MatchScheduleService`` owns the state machine (request → seat → start →
finish) via its ``_transition`` template; ``MatchService`` owns CRUD and crew
signup; the rest are the pieces they compose — participant resolution, row
formatting for the tables, the derived status vocabulary, the SpeedGaming field
guard, the settled-bracket-result guard, the bracket-run scheduling guard,
player-initiated requests, cancellation, the dispute flag, station assignment
and the random seating draw, watchers, and time suggestion.

``stage_reminder`` is the background loop that DMs a stage match's players and
approved crew shortly before it starts; ``main.py`` starts and stops it.
"""

from application.services.match import stage_reminder
from application.services.match.bracket_result_guard import (
    assert_bracket_result_editable,
)
from application.services.match.match_acknowledgment import MatchAcknowledgmentMixin
from application.services.match.match_cancellation import CancellationMixin
from application.services.match.match_comment_service import MatchCommentService
from application.services.match.match_display_service import MatchDisplayService
from application.services.match.match_forfeit import ForfeitMixin
from application.services.match.match_hard_preset_service import (
    HardPresetState,
    MatchHardPresetService,
)
from application.services.match.match_participants import MatchParticipants
from application.services.match.match_placeholders import PlaceholderBookingMixin
from application.services.match.match_reads import MatchReadsMixin
from application.services.match.match_request import MatchRequestMixin
from application.services.match.match_request_guard import assert_player_requests_allowed
from application.services.match.match_review import MatchReviewMixin
from application.services.match.match_rewind import (
    MATCH_STEP_LABELS,
    reached_step,
    rewind_targets,
)
from application.services.match.match_schedule_service import MatchScheduleService
from application.services.match.match_service import MatchService
from application.services.match.match_source_guard import assert_sg_fields_unchanged
from application.services.match.match_station_draw import (
    StationDrawMixin,
    StationSuggestion,
)
from application.services.match.match_stations import StationAssignmentMixin
from application.services.match.match_status import MatchStatus, resolve_matchup
from application.services.match.match_stream_volunteer_service import (
    MatchStreamVolunteerService,
)
from application.services.match.match_suggestion_service import MatchSuggestionService
from application.services.match.match_text import (
    clean_description,
    clean_title,
    new_match_text,
    text_updates,
)
from application.services.match.match_watcher_service import MatchWatcherService
from application.services.match.player_late_service import PlayerLateService

__all__ = [
    'MATCH_STEP_LABELS',
    'CancellationMixin',
    'ForfeitMixin',
    'HardPresetState',
    'MatchAcknowledgmentMixin',
    'MatchCommentService',
    'MatchDisplayService',
    'MatchHardPresetService',
    'MatchParticipants',
    'MatchReadsMixin',
    'MatchRequestMixin',
    'MatchReviewMixin',
    'MatchScheduleService',
    'MatchService',
    'MatchStatus',
    'MatchStreamVolunteerService',
    'MatchSuggestionService',
    'MatchWatcherService',
    'PlaceholderBookingMixin',
    'PlayerLateService',
    'StationAssignmentMixin',
    'StationDrawMixin',
    'StationSuggestion',
    'assert_bracket_result_editable',
    'assert_player_requests_allowed',
    'assert_sg_fields_unchanged',
    'clean_description',
    'clean_title',
    'new_match_text',
    'reached_step',
    'resolve_matchup',
    'rewind_targets',
    'stage_reminder',
    'text_updates',
]
