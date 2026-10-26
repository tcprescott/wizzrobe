"""Which finished races the room bot records itself, and which wait for staff."""

from application.services.race_room_holds import RacePlayer, gap_label, hold_for, hold_label
from models import RaceRoomHoldReason
from racetimebot.transport import EntrantStatus, RaceEntrant

ALICE = RacePlayer('Alice', 'u1')
BOB = RacePlayer('Bob', 'u2')


def _done(uid, ms, place=None):
    return RaceEntrant(user_id=uid, display_name=uid, status=EntrantStatus.DONE,
                       finish_time=ms // 1000, place=place, finish_ms=ms)


def test_clean_result_records():
    assert hold_for([ALICE, BOB], [_done('u1', 3_600_000, 1), _done('u2', 3_660_000, 2)]) is None


def test_close_finish_holds_at_the_threshold():
    entrants = [_done('u1', 3_600_000, 1), _done('u2', 3_600_400, 2)]
    hold = hold_for([ALICE, BOB], entrants, close_finish_ms=1000)
    assert hold.reason == RaceRoomHoldReason.CLOSE_FINISH
    assert hold.detail == 'Alice and Bob finished 0.4s apart.'
    assert hold_for([ALICE, BOB], entrants, close_finish_ms=300) is None
    assert hold_for([ALICE, BOB], entrants, close_finish_ms=0) is None


def test_forfeit_holds():
    forfeit = RaceEntrant(user_id='u2', display_name='u2', status=EntrantStatus.DID_NOT_FINISH)
    hold = hold_for([ALICE, BOB], [_done('u1', 3_600_000, 1), forfeit])
    assert hold.reason == RaceRoomHoldReason.FORFEIT
    assert hold.detail == 'Bob forfeited.'


def test_unmatched_runner_unlinked_player_and_no_show_hold():
    stranger = _done('u9', 3_700_000, 2)
    hold = hold_for([ALICE, BOB], [_done('u1', 3_600_000, 1), stranger, _done('u2', 3_800_000, 3)])
    assert hold.reason == RaceRoomHoldReason.UNMATCHED and 'u9' in hold.detail

    unlinked = hold_for([ALICE, RacePlayer('Cleo', None)], [_done('u1', 3_600_000, 1)])
    assert unlinked.reason == RaceRoomHoldReason.UNMATCHED
    assert unlinked.detail == 'Cleo has no racetime account linked.'

    absent = hold_for([ALICE, BOB], [_done('u1', 3_600_000, 1)])
    assert absent.detail == "Bob didn't join the race."


def test_fpa_comes_first():
    calls = [{'name': 'Bob', 'race_seconds': 1500}, {'name': 'Alice', 'race_seconds': 1600}]
    forfeit = RaceEntrant(user_id='u2', display_name='u2', status=EntrantStatus.DID_NOT_FINISH)
    hold = hold_for([ALICE, BOB], [_done('u1', 3_600_000, 1), forfeit], fpa_calls=calls)
    assert hold.reason == RaceRoomHoldReason.FPA
    assert hold.detail == 'Bob called a fair play pause at 0:25:00 (and 1 more).'


def test_labels():
    assert hold_label('close_finish') == 'Close finish'
    assert hold_label(None) == ''
    assert gap_label(400) == '0.4s'
    assert gap_label(1000) == '1s'
    assert gap_label(42_000) == '42s'
