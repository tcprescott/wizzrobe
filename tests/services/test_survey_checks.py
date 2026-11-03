"""The builder's Checks: each rule finds its problem and stays quiet otherwise."""

from types import SimpleNamespace

from application.services.survey import checks, plan
from models import SurveyQuestionKind


def _q(qid: int, prompt: str, kind: SurveyQuestionKind = SurveyQuestionKind.RATING, **extra):
    return SimpleNamespace(id=qid, prompt=prompt, kind=kind, required=extra.get('required', False),
                           options=extra.get('options'), conditions=[])


def _s(sid: int, title: str, *questions):
    return SimpleNamespace(id=sid, title=title, questions=list(questions))


def _path(who: str, *sections):
    return checks.Path(who, [plan.Part(s.id, s.title, '', '', '', 'event', list(s.questions)) for s in sections])


def test_a_clean_survey_passes_every_check():
    event = _s(1, 'The event', _q(1, 'Overall, how was it?'), _q(2, 'Anything else?', SurveyQuestionKind.COMMENT))
    found, passed = checks.run_checks([event], [_path('Someone', event)])
    assert found == [] and len(passed) == len(checks.CHECKS)


def test_each_rule_names_what_it_found():
    comments = [_q(10 + i, f'Comment {i}', SurveyQuestionKind.COMMENT) for i in range(4)]
    busy = _s(1, 'Busy', _q(1, 'How was the stream and the stage?'),
              _q(2, 'Why?', SurveyQuestionKind.COMMENT, required=True), *comments)
    agree = _s(2, 'Agree', _q(3, 'Agree?', SurveyQuestionKind.AGREE_SCALE, options=list('abcdef')))
    empty = _s(3, 'Empty')
    nobody = _s(4, 'Nobody', _q(4, 'Only the stage crew'))
    found, _ = checks.run_checks([busy, agree, empty, nobody], [_path('A player', busy, agree)])
    by_check = {f.check: f for f in found}
    assert set(by_check) == {'empty_part', 'unreachable', 'two_in_one', 'comment_boxes', 'long_part',
                             'long_agree', 'required_comment'}
    assert by_check['two_in_one'].question_id == 1
    assert by_check['unreachable'].section_id == 4
    assert by_check['comment_boxes'].text.startswith('A player sees 5 comment boxes')
