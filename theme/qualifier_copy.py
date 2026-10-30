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

# The two anchors a competitor reasons with (100 = par, 105 = the cap), not the
# formula. Par moves because it is the mean of the fastest approved runs on a
# seed, so a stranger's approval legitimately moves your score — which nothing on
# the page said before.
SCORE_EXPLAINER = (
    "Scores are relative to each seed's par — the average of the fastest approved runs "
    "on it. Approving someone else's run on a seed you played can move your score. "
    "100 is par; the cap is 105."
)

BOARD_EXPLAINER = (
    "Score is your realised total — unrun slots count zero. Estimate projects your unrun "
    "slots at your own average, so a partial entrant isn't understated. Ranking is by Score."
)

# While the qualifier is open a runner sees a band rather than a number, because a
# number is exactly solvable for the seed's par. The labels say which side of par
# they landed on and nothing more precise.
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

# ``pending`` only exists on a live race: the run is created when the racer is
# entered, before the race starts.
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

# A live race's lifecycle, for the staff cards. ``pending`` means a racetime room
# exists and the race has not started, which is what the reader needs to know.
LIVE_RACE_STATUS_LABELS = {
    'scheduled': 'Scheduled',
    'pending': 'Room open',
    'in_progress': 'Racing',
    'finished': 'Finished',
    'cancelled': 'Cancelled',
}
