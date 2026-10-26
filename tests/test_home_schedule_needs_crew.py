"""The home board's Needs crew switch: which rows a volunteer still has a use for."""

from pages.home_tabs.schedule import needs_crew


def _row(**overrides):
    row = {
        'crew_signup_open': True,
        'crew_wanted': {'commentators': True, 'trackers': True},
        'crew_required': {'commentators': 2, 'trackers': 1},
        'commentators': [],
        'trackers': [],
    }
    row.update(overrides)
    return row


def test_a_match_with_no_crew_needs_crew():
    assert needs_crew(_row())


def test_a_fully_approved_crew_is_covered():
    assert not needs_crew(_row(
        commentators=[{'approved': True}, {'approved': True}],
        trackers=[{'approved': True}],
    ))


def test_pending_signups_do_not_count_as_cover():
    # Someone waiting on approval has not filled the slot; another volunteer
    # may still be the one staff pick.
    assert needs_crew(_row(
        commentators=[{'approved': True}, {'approved': False}],
        trackers=[{'approved': True}],
    ))


def test_a_role_the_tournament_does_not_use_is_ignored():
    assert not needs_crew(_row(
        crew_wanted={'commentators': True, 'trackers': False},
        crew_required={'commentators': 1, 'trackers': 0},
        commentators=[{'approved': True}],
    ))


def test_a_match_closed_to_signups_never_needs_crew():
    assert not needs_crew(_row(crew_signup_open=False))


def test_a_row_without_requirements_falls_back_to_one_of_each():
    row = _row(commentators=[{'approved': True}], trackers=[])
    del row['crew_required']
    assert needs_crew(row)


def test_a_broadcaster_short_needs_crew_where_the_event_has_them():
    assert needs_crew(_row(
        crew_wanted={'commentators': True, 'trackers': True, 'broadcasters': True},
        crew_required={'commentators': 2, 'trackers': 1, 'broadcasters': 1},
        commentators=[{'approved': True}, {'approved': True}],
        trackers=[{'approved': True}],
        broadcasters=[],
    ))


def test_broadcasters_are_ignored_where_the_row_does_not_ask_for_them():
    assert not needs_crew(_row(
        commentators=[{'approved': True}, {'approved': True}],
        trackers=[{'approved': True}],
    ))
