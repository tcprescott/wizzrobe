"""Every actionless view of an asset page says something.

The finding: a volunteer scanning the label of the asset they are holding got no
controls and no explanation. The rule this locks is stronger than that one case —
if a viewer has no button, they get a sentence.
"""

import pytest

from theme.equipment_copy import (
    CHECKIN_GUIDANCE,
    HOLDER_PREFIX,
    NOT_BORROWABLE_GUIDANCE,
    RETIRED_GUIDANCE,
    equipment_guidance,
)

VOLUNTEER = {'can_checkout': True, 'can_checkin': False, 'can_manage': False}
MANAGER = {'can_checkout': True, 'can_checkin': True, 'can_manage': True}
PLAIN_USER = {'can_checkout': False, 'can_checkin': False, 'can_manage': False}


class TestTheHoldersDeadEnd:
    def test_the_holder_is_told_they_hold_it_and_what_to_do(self):
        line = equipment_guidance(
            status='checked_out', is_checked_out=True, viewer_is_holder=True, **VOLUNTEER,
        )
        assert line == f'{HOLDER_PREFIX} {CHECKIN_GUIDANCE}'

    def test_someone_else_gets_the_reason_without_the_claim(self):
        line = equipment_guidance(
            status='checked_out', is_checked_out=True, viewer_is_holder=False, **VOLUNTEER,
        )
        assert line == CHECKIN_GUIDANCE
        assert HOLDER_PREFIX not in line

    def test_the_copy_names_who_can_do_it(self):
        assert 'staff' in CHECKIN_GUIDANCE
        assert 'equipment manager' in CHECKIN_GUIDANCE


class TestTheOtherActionlessStates:
    def test_a_retired_asset_says_so(self):
        line = equipment_guidance(
            status='retired', is_checked_out=False, viewer_is_holder=False, **VOLUNTEER,
        )
        assert line == RETIRED_GUIDANCE

    def test_someone_who_cannot_borrow_is_told_who_can(self):
        line = equipment_guidance(
            status='available', is_checked_out=False, viewer_is_holder=False, **PLAIN_USER,
        )
        assert line == NOT_BORROWABLE_GUIDANCE

    @pytest.mark.parametrize('status', ['available', 'checked_out', 'retired'])
    def test_a_viewer_with_no_control_always_gets_a_line(self, status):
        line = equipment_guidance(
            status=status, is_checked_out=(status == 'checked_out'),
            viewer_is_holder=False, **PLAIN_USER,
        )
        assert line, f'{status} left a signed-in viewer with nothing'


class TestSilenceWhereThereIsAButton:
    @pytest.mark.parametrize('status,out', [
        ('available', False), ('checked_out', True), ('retired', False),
    ])
    def test_a_manager_is_never_lectured(self, status, out):
        assert equipment_guidance(
            status=status, is_checked_out=out, viewer_is_holder=False, **MANAGER,
        ) is None

    def test_a_volunteer_who_can_check_out_gets_no_line(self):
        assert equipment_guidance(
            status='available', is_checked_out=False, viewer_is_holder=False, **VOLUNTEER,
        ) is None

    def test_a_check_in_capable_non_manager_gets_no_line(self):
        # Defensive: the gate is manager-only today, but the helper must not
        # explain away a button the viewer actually has.
        assert equipment_guidance(
            status='checked_out', is_checked_out=True, viewer_is_holder=True,
            can_checkout=True, can_checkin=True, can_manage=False,
        ) is None
