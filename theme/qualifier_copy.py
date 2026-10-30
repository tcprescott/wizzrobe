"""Shared explanatory copy for the async-qualifier surfaces.

Both the player's own qualifier page and the admin leaderboard show `Score` and
`Estimate`, and neither defined either. The wording lives here because the two
surfaces must not explain the same column differently — a reviewer answering
"what does Estimate mean?" should be reading the same sentence the competitor is.

Presentation-only: strings, no logic.
"""

__all__ = [
    'BAND_EXPLAINER',
    'BAND_LABELS',
    'BOARD_EXPLAINER',
    'LIVE_RACE_STATUS_LABELS',
    'REVIEW_LABELS',
    'SCORE_EXPLAINER',
    'STATUS_LABELS',
]

SCORE_EXPLAINER = (
    "Scores are relative to each seed's par — the average of the fastest approved runs "
    "on it. Approving someone else's run on a seed you played can move your score. "
    "100 is par; the cap is 105."
)

BOARD_EXPLAINER = (
    "Score is your realised total — unrun slots count zero. Estimate projects your unrun "
    "slots at your own average, so a partial entrant isn't understated. Ranking is by Score."
)

# A band, not a number, while open: a number is exactly solvable for the seed's par.
BAND_LABELS = {
    'under': 'Under par',
    'near': 'Near par',
    'above': 'Above par',
}

BAND_EXPLAINER = (
    "Exact scores appear once this qualifier closes. Until then you see which side of "
    "each seed's par you landed on — a number would tell you the par itself, which is "
    "hidden while other people are still running."
)

STATUS_LABELS = {
    'pending': 'Waiting for the race',
    'in_progress': 'Running',
    'finished': 'Finished',
    'forfeit': 'Forfeited',
    'disqualified': 'Disqualified',
}

REVIEW_LABELS = {
    'pending': 'Awaiting review',
    'approved': 'Approved',
    'rejected': 'Rejected',
}

LIVE_RACE_STATUS_LABELS = {
    'scheduled': 'Scheduled',
    'pending': 'Room open',
    'in_progress': 'Racing',
    'finished': 'Finished',
    'cancelled': 'Cancelled',
}
