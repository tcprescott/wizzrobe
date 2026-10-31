"""Scheduling a match into a tournament enrols the players — say so.

Measured: ticking "Choose any players" turned the Players select from 0 options
into every account on the platform, and creating the match silently enrolled
whoever was chosen. The notification said "Match created successfully" and
nothing else.

Wave 2 fixed *who* is offered. This is *what happens next*: a caption before the
save and a report after it. No confirmation dialog — enrolment is reversible
from the tournament's own entrants dialog, and a modal would tax the common path
to guard a cheap mistake.
"""

from theme.dialog.match_dialog_base import enrolment_preview, enrolment_report


class TestPreview:
    def test_names_only_the_not_yet_enrolled(self):
        assert enrolment_preview(['Alice'], 'Spring Open') == (
            'Alice will be enrolled in Spring Open.'
        )

    def test_is_empty_when_everyone_is_enrolled(self):
        assert enrolment_preview([], 'Spring Open') == ''

    def test_two_names_read_as_a_sentence(self):
        assert enrolment_preview(['Alice', 'Bob'], 'Spring Open') == (
            'Alice and Bob will be enrolled in Spring Open.'
        )

    def test_three_names_are_comma_separated(self):
        assert enrolment_preview(['Alice', 'Bob', 'Cara'], 'Cup') == (
            'Alice, Bob and Cara will be enrolled in Cup.'
        )

    def test_the_tournament_is_named(self):
        assert 'Spring Open' in enrolment_preview(['Alice'], 'Spring Open')


class TestReport:
    def test_past_tense_for_the_notification(self):
        assert enrolment_report(['Alice'], 'Cup') == 'Alice was enrolled in Cup.'
        assert enrolment_report(['Alice', 'Bob'], 'Cup') == (
            'Alice and Bob were enrolled in Cup.'
        )

    def test_is_empty_when_nothing_was_enrolled(self):
        assert enrolment_report([], 'Cup') == ''
