"""Schemas for the reporting endpoints.

The report services build their payloads for the chart that is about to draw
them. Two are too heavy to hand back whole and are reshaped in the router (the
same call the MCP tools make, for the same reason); the rest pass through, and
the models here describe them key for key.
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

_LOCAL_TIME = (
    "ISO 8601 with a UTC offset, expressed on the community's clock rather than in UTC."
)
_BUCKET = "Bucket size the series uses: `week` (buckets start on Monday) or `month` (start on the 1st)."
_BUCKET_STARTS = (
    "First day of each bucket (YYYY-MM-DD, on the community's calendar), oldest first. "
    "Every per-bucket list in this response lines up with this one."
)
_BUCKET_LABELS = "Display label for each bucket: the start date for weeks, `YYYY-MM` for months."


class CapacityPeak(BaseModel):
    at: datetime = Field(..., description=f"Start of the interval. {_LOCAL_TIME}")
    players: int = Field(..., description="Players expected to be playing at that moment.")


class CapacityForecastResponse(BaseModel):
    start: datetime = Field(..., description=f"Start of the window the forecast covers. {_LOCAL_TIME}")
    end: datetime = Field(..., description=f"End of the window the forecast covers. {_LOCAL_TIME}")
    interval_minutes: int = Field(
        ...,
        description="Sampling step: 15 minutes for a window up to a day, 30 up to three days, 60 beyond.",
    )
    max_capacity: Optional[int] = Field(
        None, description="The community's configured maximum number of concurrent players."
    )
    peak_players: int = Field(..., description="Highest concurrent-player count at any sampled moment.")
    peak_on_stream_players: int = Field(
        ..., description="Highest concurrent-player count counting only matches assigned to a stage."
    )
    over_capacity_intervals: int = Field(
        ..., description="How many sampled moments exceed `max_capacity`."
    )
    busiest: List[CapacityPeak] = Field(
        default_factory=list, description="The five busiest sampled moments, busiest first."
    )


class StageUtilizationRow(BaseModel):
    stage_id: Optional[int] = Field(None, description="ID of the stage.")
    stage_name: Optional[str] = Field(None, description="Name of the stage.")
    scheduled_hours: float = Field(
        ..., description="Hours of match time booked on the stage inside the window, to one decimal."
    )
    match_count: int = Field(..., description="Matches on the stage inside the window.")
    gap_hours: float = Field(
        ..., description="Idle hours between consecutive matches on the stage, to one decimal."
    )
    back_to_back_count: int = Field(
        ..., description="Times a match starts less than 15 minutes after the previous one ends."
    )


class StageUtilizationResponse(BaseModel):
    stages: List[StageUtilizationRow] = Field(
        default_factory=list, description="One row per active stage, sorted by name."
    )
    unplaced_candidate_count: int = Field(
        0, description="Stream-candidate matches in the window that have no stage yet."
    )


class ActiveMatchRow(BaseModel):
    match_id: int = Field(..., description="ID of the match.")
    tournament: Optional[str] = Field(None, description="Name of the match's tournament.")
    scheduled_at: Optional[datetime] = Field(None, description="Scheduled start time (UTC).")
    players: List[str] = Field(default_factory=list, description="Display names of the players.")
    stage: Optional[str] = Field(None, description="Name of the stage the match is on, if any.")


class MatchOperationsRow(BaseModel):
    match_id: int = Field(..., description="ID of the match.")
    tournament_id: Optional[int] = Field(None, description="ID of the match's tournament.")
    tournament_name: str = Field(..., description="Name of the tournament, or an empty string.")
    scheduled_at: Optional[datetime] = Field(None, description=f"Scheduled start time. {_LOCAL_TIME}")
    state: str = Field(
        ..., description="Lifecycle state: `Scheduled`, `Checked In`, `In Progress` or `Finished`."
    )
    stage: str = Field(..., description="Name of the stage, or an empty string when unassigned.")
    staged: bool = Field(False, description="Whether the match has a stage assigned.")
    start_delay_min: Optional[int] = Field(
        None, description="Minutes between the scheduled time and the actual start; negative when early."
    )
    seat_delay_min: Optional[int] = Field(
        None,
        description="Minutes between the scheduled time and check-in (seated); negative when early, "
        "null when never checked in.",
    )
    setup_min: Optional[int] = Field(
        None, description="Minutes from check-in to the actual start; null unless both happened."
    )
    duration_min: Optional[int] = Field(None, description="Minutes from start to finish.")
    confirmation_lag_min: Optional[int] = Field(
        None, description="Minutes from finish until the result was confirmed."
    )
    player_count: int = Field(..., description="Number of players in the match.")


class MatchOperationsAggregate(BaseModel):
    tournament_id: Optional[int] = Field(None, description="ID of the tournament.")
    tournament_name: str = Field(..., description="Name of the tournament, or an empty string.")
    matches_total: int = Field(..., description="Matches scheduled in the window.")
    matches_started: int = Field(..., description="Of those, how many have started.")
    matches_finished: int = Field(..., description="Of those, how many have a recorded start and finish.")
    avg_start_delay_min: Optional[float] = Field(
        None, description="Average start delay in minutes, to one decimal."
    )
    avg_duration_min: Optional[float] = Field(
        None, description="Average match length in minutes, to one decimal."
    )
    expected_avg_min: Optional[int] = Field(
        None, description="The tournament's configured average match length in minutes."
    )
    on_time_pct: Optional[float] = Field(
        None, description="Share of started matches that began within 5 minutes of schedule, as a percentage."
    )
    avg_seat_delay_min: Optional[float] = Field(
        None, description="Average check-in delay in minutes, to one decimal."
    )
    seated_on_time_pct: Optional[float] = Field(
        None,
        description="Share of checked-in matches that checked in within 5 minutes of schedule, "
        "as a percentage.",
    )
    avg_setup_min: Optional[float] = Field(
        None, description="Average minutes from check-in to start, to one decimal."
    )
    staged_avg_seat_delay_min: Optional[float] = Field(
        None, description="Average check-in delay in minutes, to one decimal. Matches on a stage only."
    )
    staged_seated_on_time_pct: Optional[float] = Field(
        None,
        description="Share of checked-in matches that checked in within 5 minutes of schedule, "
        "as a percentage. Matches on a stage only.",
    )
    staged_avg_setup_min: Optional[float] = Field(
        None, description="Average minutes from check-in to start, to one decimal. Matches on a stage only."
    )
    unstaged_avg_seat_delay_min: Optional[float] = Field(
        None, description="Average check-in delay in minutes, to one decimal. Matches with no stage only."
    )
    unstaged_seated_on_time_pct: Optional[float] = Field(
        None,
        description="Share of checked-in matches that checked in within 5 minutes of schedule, "
        "as a percentage. Matches with no stage only.",
    )
    unstaged_avg_setup_min: Optional[float] = Field(
        None, description="Average minutes from check-in to start, to one decimal. Matches with no stage only."
    )


class MatchOperationsResponse(BaseModel):
    rows: List[MatchOperationsRow] = Field(..., description="One row per match scheduled in the window.")
    aggregates: List[MatchOperationsAggregate] = Field(
        ..., description="One row per tournament. Averages and percentages leave out flagged matches."
    )
    flagged_match_ids: List[int] = Field(
        default_factory=list,
        description="Matches whose times can't be a real race (started or checked in more than 6 hours "
        "early, or check-in, start and finish all the same instant), left out of every average.",
    )
    duration_outlier_ids: List[int] = Field(
        default_factory=list,
        description="Matches whose length is left out of the average length only: zero minutes, or more than "
        "three times the tournament's configured match length (a Finish clicked hours late).",
    )


class CrewCoverageRow(BaseModel):
    match_id: int = Field(..., description="ID of the match.")
    tournament_name: str = Field(..., description="Name of the tournament, or an empty string.")
    scheduled_at: Optional[datetime] = Field(None, description=f"Scheduled start time. {_LOCAL_TIME}")
    stage: str = Field(..., description="Name of the stage, or an empty string when unassigned.")
    is_stream_candidate: bool = Field(..., description="Whether the match is flagged for stream.")
    commentators_approved: int = Field(..., description="Approved commentator signups.")
    commentators_total: int = Field(..., description="All commentator signups, approved or not.")
    trackers_approved: int = Field(..., description="Approved tracker signups.")
    trackers_total: int = Field(..., description="All tracker signups, approved or not.")
    commentators_required: int = Field(..., description="Commentators the tournament requires.")
    trackers_required: int = Field(..., description="Trackers the tournament requires.")
    broadcasters_approved: int = Field(0, description="Approved broadcaster signups.")
    broadcasters_total: int = Field(0, description="All broadcaster signups, approved or not.")
    broadcasters_required: int = Field(
        0, description="Broadcasters the tournament requires; 0 when its event doesn't have broadcasters."
    )
    coverage_gap: bool = Field(
        ..., description="True when a stream candidate lacks the approved crew its tournament requires."
    )


class CrewContributionRow(BaseModel):
    user_id: Optional[int] = Field(None, description="ID of the crew member.")
    name: str = Field(..., description="Display name of the crew member.")
    commentator_approved: int = Field(..., description="Approved commentator signups.")
    commentator_total: int = Field(..., description="All commentator signups.")
    tracker_approved: int = Field(..., description="Approved tracker signups.")
    tracker_total: int = Field(..., description="All tracker signups.")
    broadcaster_approved: int = Field(0, description="Approved broadcaster signups.")
    broadcaster_total: int = Field(0, description="All broadcaster signups.")
    hours_covered: float = Field(..., description="Match hours covered by approved signups, to one decimal.")
    hours_total: float = Field(
        ...,
        description="Match hours across all signups, to one decimal. Zero when `approved_only` is set.",
    )


class CrewedPerson(BaseModel):
    user_id: int = Field(..., description="ID of the crew member.")
    name: str = Field(..., description="Display name of the crew member.")
    commentary: int = Field(..., description="Matches they commentated.")
    tracker: int = Field(..., description="Matches they tracked.")
    broadcast: int = Field(0, description="Matches they broadcast.")
    total: int = Field(..., description="Commentary, tracker and broadcast together.")


class CrewedTournamentRow(BaseModel):
    tournament_id: int = Field(..., description="ID of the tournament.")
    tournament_name: str = Field(..., description="Name of the tournament.")
    commentary: int = Field(..., description="Commentary slots crewed across the tournament.")
    tracker: int = Field(..., description="Tracker slots crewed across the tournament.")
    broadcast: int = Field(0, description="Broadcast slots crewed across the tournament.")
    people: List[CrewedPerson] = Field(..., description="Who crewed it, most matches first.")


class CrewCoverageResponse(BaseModel):
    coverage_rows: List[CrewCoverageRow] = Field(..., description="One row per match in the window.")
    contribution_rows: List[CrewContributionRow] = Field(
        ..., description="One row per crew member, most covered hours first."
    )
    tournament_rows: List[CrewedTournamentRow] = Field(
        ...,
        description=(
            "Matches crewed per person per tournament, counting approved crew on finished matches "
            "that went to a stage. Tournaments nobody crewed are left out."
        ),
    )


class TournamentHealthRow(BaseModel):
    tournament_id: int = Field(..., description="ID of the tournament.")
    tournament_name: str = Field(..., description="Name of the tournament.")
    matches_total: int = Field(..., description="Matches scheduled in the window.")
    matches_past: int = Field(..., description="Of those, how many were scheduled before now.")
    matches_started: int = Field(..., description="Matches that have started.")
    matches_finished: int = Field(..., description="Matches that have finished.")
    completion_pct: Optional[float] = Field(
        None, description="Share of past matches that have finished, as a percentage."
    )
    on_time_pct: Optional[float] = Field(
        None, description="Share of started matches that began within 5 minutes of schedule, as a percentage."
    )
    coverage_pct: Optional[float] = Field(
        None, description="Share of stream candidates with the required approved crew, as a percentage."
    )
    avg_start_delay_min: Optional[float] = Field(
        None, description="Average start delay in minutes, to one decimal."
    )
    avg_duration_min: Optional[float] = Field(
        None, description="Average match length in minutes, to one decimal."
    )
    expected_avg_min: Optional[int] = Field(
        None, description="The tournament's configured average match length in minutes."
    )
    health_score: Optional[float] = Field(
        None,
        description=(
            "Weighted 0 to 100 score from completion, punctuality, crew coverage and duration "
            "adherence. Components with no data are left out; null when none have data."
        ),
    )


class CrewContributor(BaseModel):
    user_id: Optional[int] = Field(None, description="ID of the crew member.")
    name: str = Field(..., description="Display name of the crew member.")
    commentator_approved: int = Field(..., description="Approved commentator signups in the window.")
    tracker_approved: int = Field(..., description="Approved tracker signups in the window.")
    total_approved: int = Field(..., description="All approved signups in the window.")


class CrewParticipationTotals(BaseModel):
    commentator_signups: int = Field(..., description="Commentator signups across the window.")
    commentator_approved: int = Field(..., description="Approved commentator signups across the window.")
    tracker_signups: int = Field(..., description="Tracker signups across the window.")
    tracker_approved: int = Field(..., description="Approved tracker signups across the window.")
    unique_people: int = Field(..., description="Distinct people who signed up in the window.")


class CrewParticipationTrendsResponse(BaseModel):
    bucket: str = Field(..., description=_BUCKET)
    bucket_starts: List[date] = Field(..., description=_BUCKET_STARTS)
    bucket_labels: List[str] = Field(..., description=_BUCKET_LABELS)
    commentator_signups: List[int] = Field(..., description="Commentator signups per bucket.")
    commentator_approved: List[int] = Field(..., description="Approved commentator signups per bucket.")
    tracker_signups: List[int] = Field(..., description="Tracker signups per bucket.")
    tracker_approved: List[int] = Field(..., description="Approved tracker signups per bucket.")
    unique_people: List[int] = Field(..., description="Distinct people who signed up, per bucket.")
    top_contributors: List[CrewContributor] = Field(
        ..., description="Up to 15 people with the most approved signups, most first."
    )
    totals: CrewParticipationTotals = Field(..., description="Totals across the whole window.")


class VolunteerPositionHours(BaseModel):
    position_id: int = Field(..., description="ID of the volunteer position.")
    name: str = Field(..., description="Name of the position.")
    color: Optional[str] = Field(None, description="The position's display colour, if set.")
    hours: List[float] = Field(..., description="Scheduled volunteer hours per bucket, to one decimal.")


class TopVolunteer(BaseModel):
    user_id: Optional[int] = Field(None, description="ID of the volunteer.")
    name: str = Field(..., description="Display name of the volunteer.")
    scheduled_hours: float = Field(..., description="Hours of shifts assigned, to one decimal.")
    checked_in_hours: float = Field(..., description="Hours of shifts checked in to, to one decimal.")
    shifts: int = Field(..., description="Number of shifts assigned.")


class VolunteerHourTotals(BaseModel):
    scheduled_hours: float = Field(..., description="Assigned volunteer hours across the window.")
    checked_in_hours: float = Field(..., description="Checked-in volunteer hours across the window.")
    needed_hours: float = Field(..., description="Volunteer hours the shifts called for across the window.")
    volunteers: int = Field(..., description="Distinct volunteers assigned in the window.")


class VolunteerHourTrendsResponse(BaseModel):
    bucket: str = Field(..., description=_BUCKET)
    bucket_starts: List[date] = Field(..., description=_BUCKET_STARTS)
    bucket_labels: List[str] = Field(..., description=_BUCKET_LABELS)
    scheduled_hours: List[float] = Field(
        ..., description="Assigned volunteer hours (shift length times assignees) per bucket."
    )
    checked_in_hours: List[float] = Field(
        ..., description="Hours from assignments that were checked in, per bucket."
    )
    needed_hours: List[float] = Field(
        ..., description="Hours the shifts called for (shift length times slots needed) per bucket."
    )
    fill_rate: List[Optional[float]] = Field(
        ..., description="Scheduled hours as a percentage of needed hours per bucket; null when nothing was needed."
    )
    positions: List[VolunteerPositionHours] = Field(
        ..., description="Scheduled hours broken down by position, sorted by name."
    )
    top_volunteers: List[TopVolunteer] = Field(
        ..., description="Up to 15 volunteers with the most scheduled hours, most first."
    )
    totals: VolunteerHourTotals = Field(..., description="Totals across the whole window.")


class ActivityCategory(BaseModel):
    category: str = Field(
        ..., description="Action namespace: the part of the audit action before the dot, such as `match`."
    )
    counts: List[int] = Field(..., description="Audit entries in this namespace per bucket.")
    total: int = Field(..., description="Audit entries in this namespace across the window.")


class ActivityTrendsResponse(BaseModel):
    bucket: str = Field(..., description=_BUCKET)
    bucket_starts: List[date] = Field(..., description=_BUCKET_STARTS)
    bucket_labels: List[str] = Field(..., description=_BUCKET_LABELS)
    categories: List[ActivityCategory] = Field(
        ..., description="One entry per action namespace, busiest first."
    )
    total: int = Field(..., description="All audit entries across the window.")
