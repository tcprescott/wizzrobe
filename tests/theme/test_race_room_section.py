"""The match dialog's Racetime Room section, once a room exists.

It used to be a dead end: ``f'{room.slug} — {room.status.value}'`` and a
``return``. The slug was not a link, the status was the raw enum the bot writes,
and ``RaceRoomService.cancel_room`` — reachable over REST and covered by tests —
had no web surface at all, so calling off a room opened by mistake meant the API
or racetime.gg itself.

Rendering needs a live NiceGUI client, so these lock the parts that decide what
the section says and offers.
"""

import pytest

from models import RaceRoomStatus, RacetimeRoom
from theme.dialog.match_dialog import (
    _CANCELLABLE_ROOM_STATUSES,
    _ROOM_STATUS_CHIP,
    _ROOM_STATUS_LABEL,
)


class TestStatusCopy:
    def test_every_status_has_a_human_label_and_a_chip(self):
        for status in RaceRoomStatus:
            assert status in _ROOM_STATUS_LABEL
            assert status in _ROOM_STATUS_CHIP

    def test_no_label_is_the_raw_enum_value(self):
        """"in_progress" is what the bot stores, not what an operator reads."""
        for status, label in _ROOM_STATUS_LABEL.items():
            assert label != status.value
            assert '_' not in label

    def test_every_chip_is_a_known_modifier(self):
        known = {
            'wiz-chip--ok', 'wiz-chip--live', 'wiz-chip--pending',
            'wiz-chip--neutral', 'wiz-chip--cancelled', 'wiz-chip--candidate',
        }
        assert set(_ROOM_STATUS_CHIP.values()) <= known


class TestCancellableStatuses:
    def test_only_a_live_room_can_be_called_off(self):
        assert set(_CANCELLABLE_ROOM_STATUSES) == {
            RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS,
        }

    @pytest.mark.parametrize('status', [RaceRoomStatus.FINISHED, RaceRoomStatus.CANCELLED])
    def test_a_terminal_room_offers_nothing(self, status):
        assert status not in _CANCELLABLE_ROOM_STATUSES


class TestRoomUrl:
    def test_the_slug_becomes_a_racetime_address(self):
        room = RacetimeRoom(slug='alttpr/dev-room', category='alttpr')
        assert room.url == 'https://racetime.gg/alttpr/dev-room'

    def test_a_room_with_no_slug_has_no_url(self):
        assert RacetimeRoom(slug='', category='alttpr').url == ''

    def test_the_bracket_watch_link_uses_the_same_derivation(self):
        """One definition, so the two surfaces cannot drift apart."""
        from application.services._bracket.scheduling import SchedulingMixin

        room = RacetimeRoom(slug='alttpr/dev-room', category='alttpr')
        assert SchedulingMixin._watch_url(None, room) == room.url
