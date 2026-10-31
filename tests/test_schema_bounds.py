"""Bounds on request schemas backing authenticated write endpoints.

A single authenticated request must not be able to submit an unbounded window
list or unbounded free-text note (request-body / storage exhaustion).
"""

import pytest
from pydantic import ValidationError

from api.schemas.player_availability import (
    PlayerAvailabilityWindowInput,
    SetPlayerAvailabilityRequest,
)
from api.schemas.volunteers import AvailabilityWindowInput, SetAvailabilityRequest

_START = '2025-01-01T00:00:00Z'
_END = '2025-01-01T01:00:00Z'


@pytest.mark.parametrize('req_cls, win_cls', [
    (SetPlayerAvailabilityRequest, PlayerAvailabilityWindowInput),
    (SetAvailabilityRequest, AvailabilityWindowInput),
])
def test_windows_list_is_bounded(req_cls, win_cls):
    one = win_cls(starts_at=_START, ends_at=_END)
    req_cls(windows=[one] * 500)
    with pytest.raises(ValidationError):
        req_cls(windows=[one] * 501)


@pytest.mark.parametrize('win_cls', [PlayerAvailabilityWindowInput, AvailabilityWindowInput])
def test_window_note_is_bounded(win_cls):
    win_cls(starts_at=_START, ends_at=_END, note='x' * 1000)
    with pytest.raises(ValidationError):
        win_cls(starts_at=_START, ends_at=_END, note='x' * 1001)
