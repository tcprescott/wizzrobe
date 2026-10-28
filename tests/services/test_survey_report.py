"""The results report's pure parts: folding small groups, headline scores, the group filter's choices."""

from types import SimpleNamespace

from application.services.survey import audiences
from application.services.survey.results import MIN_GROUP, OTHER, Breakdown, breakdown, fold_small, score_of
from models import SurveyQuestionKind

RATING = SimpleNamespace(kind=SurveyQuestionKind.RATING, options=None)
YES_NO = SimpleNamespace(kind=SurveyQuestionKind.YES_NO, options=None)


def test_small_groups_fold_into_other():
    groups, folded = fold_small(RATING, {'Big': [5] * 8, 'Bigger': [4] * 9, 'Tiny': [1, 2], 'Wee': [1, 1, 1]})
    assert set(groups) == {'Big', 'Bigger', OTHER}
    assert folded == ['Tiny', 'Wee']
    assert groups[OTHER].count == 5 and not groups[OTHER].hidden


def test_a_too_small_other_takes_the_smallest_shown_group_with_it():
    # Overall minus Big and Bigger would give Tiny's two answers away, so Big joins Other.
    groups, folded = fold_small(RATING, {'Big': [5] * 6, 'Bigger': [4] * 9, 'Tiny': [1, 2]})
    assert set(groups) == {'Bigger', OTHER}
    assert folded == ['Big', 'Tiny']
    assert groups[OTHER].count == 8


def test_every_group_small_is_one_hidden_other():
    groups, folded = fold_small(RATING, {'A': [1], 'B': [2, 3]})
    assert list(groups) == [OTHER] and groups[OTHER].hidden and groups[OTHER].count == 3
    assert folded == ['A', 'B']


def test_no_small_groups_no_other():
    groups, folded = fold_small(YES_NO, {'A': ['Yes'] * MIN_GROUP, 'B': ['No'] * MIN_GROUP})
    assert set(groups) == {'A', 'B'} and folded == []


def test_scores_go_up_when_things_went_well():
    rating = breakdown(RATING, [5, 4, 4, 2, 1])
    assert rating.top_share == 60 and score_of(SurveyQuestionKind.RATING, rating) == 60
    yes = breakdown(YES_NO, ['Yes', 'Yes', 'Yes', 'No'])
    assert score_of(SurveyQuestionKind.YES_NO, yes) == 75
    assert score_of(SurveyQuestionKind.RATING, Breakdown(count=2, hidden=True)) is None


def test_recommend_split():
    nps = breakdown(SimpleNamespace(kind=SurveyQuestionKind.NPS, options=None), [10, 9, 8, 3])
    assert nps.nps_split == (25, 25, 50) and nps.score == 25


def _row(submitted: bool, **facts):
    return {'facts': facts, 'submitted_at': object() if submitted else None}


def test_group_options_need_five_answers():
    rows = [_row(True, tournaments=[{'id': 1, 'name': 'ALTTPR'}]) for _ in range(5)]
    rows += [_row(False, tournaments=[{'id': 1, 'name': 'ALTTPR'}])]
    rows += [_row(True, tournaments=[{'id': 2, 'name': 'OoTR'}], crew=True) for _ in range(4)]
    options = audiences.group_options(rows)
    assert [(g.key, g.sent, g.responded) for g in options] == [('t:1', 6, 5)]
    rates = {a.label: (a.sent, a.responded) for a in audiences.audience_rates(rows)}
    assert rates == {'Players': (10, 9), 'Crew': (4, 4)}
