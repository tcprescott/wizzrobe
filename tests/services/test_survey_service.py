"""Post-event surveys: building, sending to the right people, answering, results."""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import NotFoundError
from application.services import SurveyResponseService, SurveyService
from application.services.survey import plan
from models import (
    Commentator,
    Match,
    MatchPlayers,
    Role,
    Stage,
    Survey,
    SurveyAudience,
    SurveyQuestion,
    SurveyQuestionKind,
    SurveyRecipient,
    SurveyResponse,
    SurveyStatus,
    Tournament,
    UserRole,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.factories import make_member

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def event_world(db):
    """Two tournaments (one on a stage), a volunteer shift, a crew member, and staff."""
    staff = await make_member(100, 'staff')
    await UserRole.create(user=staff, role=Role.STAFF)
    alice = await make_member(1, 'alice')     # plays both, ALTTPR on stage
    bob = await make_member(2, 'bob')         # plays OoTR only
    vic = await make_member(3, 'vic')         # volunteers
    cam = await make_member(4, 'cam')         # commentates
    await make_member(5, 'idle')              # member, did nothing
    alttpr = await Tournament.create(name='ALTTPR')
    ootr = await Tournament.create(name='OoTR')
    test_cup = await Tournament.create(name='Test Cup', is_test=True)
    stage = await Stage.create(name='Main')
    m1 = await Match.create(tournament=alttpr, stage=stage)
    m2 = await Match.create(tournament=ootr)
    m3 = await Match.create(tournament=test_cup)
    await MatchPlayers.create(match=m1, user=alice)
    await MatchPlayers.create(match=m2, user=alice)
    await MatchPlayers.create(match=m2, user=bob)
    await MatchPlayers.create(match=m3, user=vic)
    await Commentator.create(match=m1, user=cam, approved=True)
    desk = await VolunteerPosition.create(name='Proctor')
    shift = await VolunteerShift.create(position=desk, starts_at=NOW, ends_at=NOW + timedelta(hours=2))
    await VolunteerAssignment.create(shift=shift, user=vic)
    return {'staff': staff, 'alice': alice, 'bob': bob, 'vic': vic, 'cam': cam,
            'alttpr': alttpr, 'ootr': ootr, 'desk': desk}


async def _sent(world) -> Survey:
    service = SurveyService()
    survey = await service.create_survey(world['staff'], 'SGL26 survey')
    await service.send(world['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    return survey


async def test_template_survey_starts_as_a_draft(event_world):
    survey = await SurveyService().create_survey(event_world['staff'], 'SGL26 survey')
    assert survey.status is SurveyStatus.DRAFT
    assert [s.audience for s in survey.sections][:2] == [SurveyAudience.EVERYONE, SurveyAudience.EACH_TOURNAMENT]
    assert all(s.questions for s in survey.sections)


async def test_only_staff_build(event_world):
    with pytest.raises(PermissionError):
        await SurveyService().create_survey(event_world['bob'], 'Mine')


async def test_send_reaches_everyone_who_took_part_and_nobody_else(event_world, stub_discord_queue):
    survey = await _sent(event_world)
    recipients = {r.user_id: r for r in await SurveyRecipient.filter(survey_id=survey.id)}
    w = event_world
    assert set(recipients) == {w['alice'].id, w['bob'].id, w['vic'].id, w['cam'].id}
    assert [t['name'] for t in recipients[w['alice'].id].facts['tournaments']] == ['ALTTPR', 'OoTR']
    # A test tournament doesn't make a player.
    assert recipients[w['vic'].id].facts['tournaments'] == []
    assert recipients[w['vic'].id].facts['positions'][0]['name'] == 'Proctor'
    assert recipients[w['cam'].id].facts['crew'] is True
    assert len(stub_discord_queue) == 4
    assert all(r.dm_sent_at for r in recipients.values())


async def test_sent_survey_is_frozen(event_world):
    survey = await _sent(event_world)
    service = SurveyService()
    with pytest.raises(ValueError, match='been sent'):
        await service.add_section(event_world['staff'], survey.id, 'Late')


async def test_tournament_section_repeats_per_tournament_and_stage_question_only_on_stage(event_world):
    survey = await _sent(event_world)
    w = event_world
    view = await SurveyResponseService().open_survey(w['alice'], survey.id)
    tournament_parts = [p for p in view.parts if p.tournament_id]
    assert [p.scope_label for p in tournament_parts] == ['ALTTPR', 'OoTR']
    prompts = {p.scope_label: [q.prompt for q in p.questions] for p in tournament_parts}
    stage_q = next(q for s in survey.sections for q in s.questions if q.conditions == [{'key': 'on_stage'}])
    assert stage_q.prompt in prompts['ALTTPR']
    assert stage_q.prompt not in prompts['OoTR']
    bob_view = await SurveyResponseService().open_survey(w['bob'], survey.id)
    assert [p.scope_label for p in bob_view.parts if p.tournament_id] == ['OoTR']
    assert plan.seconds(view.parts) <= plan.BUDGET_SECONDS


async def test_people_it_wasnt_sent_to_cant_open_it(event_world):
    survey = await _sent(event_world)
    idle = await make_member(6, 'late')
    with pytest.raises(NotFoundError):
        await SurveyResponseService().open_survey(idle, survey.id)


async def _answer_everything(user, survey_id: int, rating: int) -> None:
    service = SurveyResponseService()
    view = await service.open_survey(user, survey_id)
    for part in view.parts:
        answers = {}
        for q in part.questions:
            if q.kind in (SurveyQuestionKind.RATING,):
                answers[q.id] = rating
            elif q.kind is SurveyQuestionKind.NPS:
                answers[q.id] = 9
            elif q.kind is SurveyQuestionKind.YES_NO:
                answers[q.id] = 'Yes'
            elif q.kind is SurveyQuestionKind.CHOICE_ONE:
                answers[q.id] = q.options[0]
            elif q.kind is SurveyQuestionKind.CHOICE_MANY:
                answers[q.id] = q.options[:2]
            else:
                answers[q.id] = 'Great <b>event</b>'
        await service.save_part(user, survey_id, part.key, answers)


async def test_answers_save_per_part_and_submit(event_world):
    survey = await _sent(event_world)
    alice = event_world['alice']
    await _answer_everything(alice, survey.id, 4)
    service = SurveyResponseService()
    view = await service.open_survey(alice, survey.id)
    assert all(view.done(p) for p in view.parts)
    await service.submit(alice, survey.id)
    response = await SurveyResponse.get(survey_id=survey.id)
    assert response.user_id == alice.id and not response.anonymous
    with pytest.raises(ValueError, match='already sent'):
        await service.submit(alice, survey.id)


async def test_anonymous_submit_cuts_the_link(event_world):
    survey = await _sent(event_world)
    bob = event_world['bob']
    await _answer_everything(bob, survey.id, 2)
    await SurveyResponseService().submit(bob, survey.id, anonymous=True)
    response = await SurveyResponse.get(survey_id=survey.id)
    assert response.user_id is None and response.anonymous
    assert response.submitted_on is not None
    assert {g['label'] for g in response.groups} == {'OoTR player'}
    recipient = await SurveyRecipient.get(survey_id=survey.id, user_id=bob.id)
    assert recipient.submitted_at is not None


async def test_crafted_answers_are_refused(event_world):
    survey = await _sent(event_world)
    w = event_world
    service = SurveyResponseService()
    view = await service.open_survey(w['bob'], survey.id)
    everyone = view.parts[0]
    rating = next(q for q in everyone.questions if q.kind is SurveyQuestionKind.RATING)
    with pytest.raises(ValueError):
        await service.save_part(w['bob'], survey.id, everyone.key, {rating.id: 9})
    # Bob never played ALTTPR, so that part isn't his to answer.
    with pytest.raises(ValueError, match="isn't in your survey"):
        await service.save_part(w['bob'], survey.id, f'{view.parts[1].section_id}:t:{w["alttpr"].id}', {})


async def test_results_hide_small_groups(event_world):
    survey = await _sent(event_world)
    w = event_world
    for user, rating in ((w['alice'], 5), (w['bob'], 3)):
        await _answer_everything(user, survey.id, rating)
        await SurveyResponseService().submit(user, survey.id, anonymous=user is w['bob'])
    results = await SurveyService().results(w['staff'], survey.id)
    assert results.responses == 2 and results.anonymous == 1
    assert results.recipients['sent'] == 4
    overall = results.headline(SurveyQuestionKind.RATING)
    assert overall is not None and overall.overall.count == 2 and overall.overall.average == 4
    comments = [c for r in results.questions for c in r.comments]
    assert any(c.who == 'Anonymous' and c.group == '' for c in comments)
    assert all('<b>' in c.text for c in comments)  # stored as plain text, rendered as text
    room = next(r for r in results.questions if r.groups)
    assert all(g.hidden for g in room.groups.values())


async def test_answer_condition_follows_an_earlier_question(event_world):
    service = SurveyService()
    staff = event_world['staff']
    survey = await service.create_survey(staff, 'Follow-ups', from_template=False)
    section = await service.add_section(staff, survey.id, 'The event')
    first = await service.add_question(staff, section.id, 'Come back?', SurveyQuestionKind.YES_NO)
    follow = await service.add_question(
        staff, section.id, 'Why not?', SurveyQuestionKind.COMMENT,
        conditions=[{'key': 'answer', 'question_id': first.id, 'values': ['No']}],
    )
    with pytest.raises(ValueError, match='follows this question'):
        await service.delete_question(staff, first.id)
    with pytest.raises(ValueError, match='earlier question'):
        await service.update_question(staff, first.id,
                                      conditions=[{'key': 'answer', 'question_id': follow.id, 'values': ['x']}])
    await service.send(staff, survey.id)
    bob = event_world['bob']
    responder = SurveyResponseService()
    view = await responder.open_survey(bob, survey.id)
    part = view.parts[0]
    assert [q.prompt for q in view.showing(part)] == ['Come back?']
    view = await responder.save_part(bob, survey.id, part.key, {first.id: 'No'})
    assert [q.prompt for q in view.showing(view.parts[0])] == ['Come back?', 'Why not?']


async def test_preview_shows_every_part_without_recording(event_world):
    survey = await SurveyService().create_survey(event_world['staff'], 'Draft')
    _, parts = await SurveyService().preview(event_world['staff'], survey.id)
    # Every question once, a repeating section shown for one tournament.
    assert [p.scope_label for p in parts if p.tournament_id] == ['ALTTPR']
    shown = {q.id for p in parts for q in p.questions}
    assert shown == {q.id for s in survey.sections for q in s.questions}
    _, parts = await SurveyService().preview(event_world['staff'], survey.id, 'player')
    assert [p.scope_label for p in parts if p.tournament_id] == ['ALTTPR', 'OoTR']
    assert await SurveyRecipient.filter(survey_id=survey.id).count() == 0
    with pytest.raises(PermissionError):
        await SurveyService().preview(event_world['bob'], survey.id)


async def test_builder_summary_finds_the_longest_path(event_world):
    survey = await SurveyService().create_survey(event_world['staff'], 'Draft')
    summary = await SurveyService().builder_summary(event_world['staff'], survey.id)
    assert 'played 2 tournaments' in summary.longest_who
    assert summary.longest_seconds > 0 and not summary.over_budget


async def test_remind_once_a_day_and_close(event_world, stub_discord_queue):
    survey = await _sent(event_world)
    service = SurveyService()
    staff = event_world['staff']
    assert await service.remind(staff, survey.id) == 4
    with pytest.raises(ValueError, match='last day'):
        await service.remind(staff, survey.id)
    await service.close(staff, survey.id)
    with pytest.raises(ValueError, match='closed'):
        await _answer_everything(event_world['alice'], survey.id, 5)


async def test_duplicate_keeps_follow_ups_pointing_at_the_copy(event_world):
    service = SurveyService()
    staff = event_world['staff']
    survey = await service.create_survey(staff, 'One', from_template=False)
    section = await service.add_section(staff, survey.id, 'S')
    first = await service.add_question(staff, section.id, 'Q1', SurveyQuestionKind.YES_NO)
    await service.add_question(staff, section.id, 'Q2', SurveyQuestionKind.COMMENT,
                               conditions=[{'key': 'answer', 'question_id': first.id, 'values': ['Yes']}])
    await service.add_section(staff, survey.id, 'T',
                              conditions=[{'key': 'answer', 'question_id': first.id, 'values': ['No']}])
    copy = await service.duplicate(staff, survey.id)
    q1, q2 = copy.sections[0].questions
    assert q2.conditions[0]['question_id'] == q1.id != first.id
    assert copy.sections[1].conditions[0]['question_id'] == q1.id


async def test_question_conditions_stack_and_all_must_hold(event_world):
    service = SurveyService()
    staff = event_world['staff']
    survey = await service.create_survey(staff, 'Stacked', from_template=False)
    section = await service.add_section(staff, survey.id, 'Yours', SurveyAudience.EACH_TOURNAMENT)
    rate = await service.add_question(staff, section.id, 'Stage?', SurveyQuestionKind.RATING)
    entries = [{'key': 'on_stage'}, {'key': 'answer', 'question_id': rate.id, 'values': ['1', 2]}]
    why = await service.add_question(staff, section.id, 'What went wrong?', SurveyQuestionKind.COMMENT,
                                     conditions=entries)
    assert why.conditions == [{'key': 'on_stage'}, {'key': 'answer', 'question_id': rate.id, 'values': [1, 2]}]
    with pytest.raises(ValueError, match='at most 3'):
        await service.update_question(staff, why.id, conditions=[
            {'key': 'on_stage'}, {'key': 'crewed'}, {'key': 'checked_in'}, {'key': 'rescheduled'}])
    with pytest.raises(ValueError, match='contradicts'):
        await service.update_question(staff, why.id, conditions=[{'key': 'on_stage'}, {'key': 'not_on_stage'}])
    await service.send(staff, survey.id)
    alice = event_world['alice']
    responder = SurveyResponseService()
    view = await responder.open_survey(alice, survey.id)
    on_stage, off_stage = (next(p for p in view.parts if p.scope_label == name) for name in ('ALTTPR', 'OoTR'))
    assert [q.prompt for q in on_stage.questions] == ['Stage?', 'What went wrong?']
    assert [q.prompt for q in off_stage.questions] == ['Stage?']
    view = await responder.save_part(alice, survey.id, on_stage.key, {rate.id: 5})
    assert [q.prompt for q in view.showing(view.part(on_stage.key))] == ['Stage?']
    view = await responder.save_part(alice, survey.id, on_stage.key, {rate.id: 1})
    assert [q.prompt for q in view.showing(view.part(on_stage.key))] == ['Stage?', 'What went wrong?']


async def test_a_section_can_wait_on_an_answer_and_hidden_answers_are_dropped(event_world):
    service = SurveyService()
    staff = event_world['staff']
    survey = await service.create_survey(staff, 'Sections', from_template=False)
    first = await service.add_section(staff, survey.id, 'The event')
    back = await service.add_question(staff, first.id, 'Come back?', SurveyQuestionKind.YES_NO)
    with pytest.raises(ValueError, match='at most 1'):
        await service.add_section(staff, survey.id, 'Two', conditions=[{'key': 'crewed'}, {'key': 'checked_in'}])
    later = await service.add_section(staff, survey.id, 'Next year', conditions=[
        {'key': 'answer', 'question_id': back.id, 'values': ['Yes']}])
    add = await service.add_question(staff, later.id, 'What should we add?', SurveyQuestionKind.COMMENT)
    with pytest.raises(ValueError, match='follows this question'):
        await service.delete_question(staff, back.id)
    with pytest.raises(ValueError, match='would come after it'):
        await service.move_section(staff, later.id, -1)
    await service.send(staff, survey.id)
    bob = event_world['bob']
    responder = SurveyResponseService()
    view = await responder.open_survey(bob, survey.id)
    assert [p.title for p in view.shown_parts] == ['The event']
    view = await responder.save_part(bob, survey.id, view.parts[0].key, {back.id: 'Yes'})
    assert [p.title for p in view.shown_parts] == ['The event', 'Next year']
    view = await responder.save_part(bob, survey.id, view.parts[1].key, {add.id: 'More snacks'})
    view = await responder.save_part(bob, survey.id, view.parts[0].key, {back.id: 'No'})
    assert [p.title for p in view.shown_parts] == ['The event']
    await responder.submit(bob, survey.id)
    results = await service.results(staff, survey.id)
    comments = next(r for r in results.questions if r.question_id == add.id)
    assert comments.comments == [] and comments.overall.count == 0


def test_agree_scale_statements_and_answers():
    from application.services.survey._validation import clean_options, coerce_answer
    from application.services.survey.results import breakdown

    kind = SurveyQuestionKind.AGREE_SCALE
    assert clean_options(kind, ['Rooms ran on time', ' ', 'Rooms ran on time', 'Signs were clear']) == \
        ['Rooms ran on time', 'Signs were clear']
    with pytest.raises(ValueError, match='statements'):
        clean_options(kind, ['Only one'])
    q = SurveyQuestion(prompt='Agree?', kind=kind, options=['A', 'B'])
    assert coerce_answer(q, {'B': '2', 'A': 5}) == ({'A': 5, 'B': 2}, None)
    assert coerce_answer(q, {'A': None}) == (None, None)
    for crafted in ({'C': 3}, {'A': 6}, {'A': True}, {'A': '12'}, 'A', [1]):
        with pytest.raises(ValueError):
            coerce_answer(q, crafted)
    result = breakdown(q, [{'A': 5, 'B': 1}, {'A': 4}, {'A': 1, 'Z': 3}])
    assert result.statements == {'A': [1, 0, 0, 1, 1], 'B': [1, 0, 0, 0, 0]}
    assert result.agree_share('A') == 67 and result.agree_share('B') == 0
    assert breakdown(q, [{'A': 5}], protect=True).hidden


async def test_required_agree_scale_needs_every_statement(event_world):
    w = event_world
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Agree survey', from_template=False)
    section = await service.add_section(w['staff'], survey.id, 'Overall', SurveyAudience.EVERYONE)
    question = await service.add_question(w['staff'], section.id, 'How much do you agree?',
                                          SurveyQuestionKind.AGREE_SCALE, options=['A', 'B'], required=True)
    await service.send(w['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    responses = SurveyResponseService()
    view = await responses.open_survey(w['alice'], survey.id)
    await responses.save_part(w['alice'], survey.id, view.parts[0].key, {question.id: {'A': 4}})
    with pytest.raises(ValueError, match='every statement'):
        await responses.submit(w['alice'], survey.id)
    await responses.save_part(w['alice'], survey.id, view.parts[0].key, {question.id: {'A': 4, 'B': 2}})
    await responses.submit(w['alice'], survey.id)


async def test_crew_counts_only_matches_on_a_stage(event_world, stub_discord_queue):
    w = event_world
    off_stage = await Match.create(tournament=w['ootr'])
    dan = await make_member(7, 'dan')
    await Commentator.create(match=off_stage, user=dan, approved=True)
    survey = await _sent(w)
    recipients = {r.user_id for r in await SurveyRecipient.filter(survey_id=survey.id)}
    assert w['cam'].id in recipients
    assert dan.id not in recipients


async def test_choice_extras_are_checked_when_built(event_world):
    w = event_world
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Extras', from_template=False)
    section = await service.add_section(w['staff'], survey.id, 'Overall', SurveyAudience.EVERYONE,
                                        description='About the weekend.')
    assert section.description == 'About the weekend.'
    many = SurveyQuestionKind.CHOICE_MANY
    with pytest.raises(ValueError, match='only 3 choices'):
        await service.add_question(w['staff'], section.id, 'Q', many, options=['A', 'B', 'C'], max_picks=4)
    with pytest.raises(ValueError, match='fewest'):
        await service.add_question(w['staff'], section.id, 'Q', many, options=['A', 'B', 'C'], min_picks=3,
                                   max_picks=2)
    with pytest.raises(ValueError, match='write-in switch'):
        await service.add_question(w['staff'], section.id, 'Q', many, options=['A', 'Other'], other_option=True)
    with pytest.raises(ValueError, match='already a choice'):
        await service.add_question(w['staff'], section.id, 'Q', many, options=['A', 'B'], none_option='a')
    # A kind that doesn't take an extra drops it.
    rating = await service.add_question(w['staff'], section.id, 'Rate', SurveyQuestionKind.RATING,
                                        other_option=True, long_answer=True, max_picks=2)
    assert not rating.other_option and not rating.long_answer and rating.max_picks is None
    q = await service.add_question(w['staff'], section.id, 'Top 2?', many, options=['A', 'B', 'C'],
                                   other_option=True, none_option='None of these', max_picks=2)
    assert (q.other_option, q.none_option, q.max_picks) == (True, 'None of these', 2)
    updated = await service.update_question(w['staff'], q.id, none_option='', max_picks=0)
    assert updated.none_option is None and updated.max_picks is None and updated.other_option


def test_choice_answers_hold_to_their_extras():
    from application.services.survey._validation import coerce_answer

    q = SurveyQuestion(prompt='Top 2?', kind=SurveyQuestionKind.CHOICE_MANY, options=['A', 'B', 'C'],
                       other_option=True, none_option='None of these', min_picks=None, max_picks=2)
    assert coerce_answer(q, ['B', 'A']) == (['A', 'B'], None)
    assert coerce_answer(q, {'value': ['Other'], 'other': 'Radio'}) == (['Other'], 'Radio')
    assert coerce_answer(q, {'value': ['A'], 'other': 'ignored'}) == (['A'], None)
    assert coerce_answer(q, ['None of these']) == (['None of these'], None)
    with pytest.raises(ValueError, match='Pick up to 2'):
        coerce_answer(q, ['A', 'B', 'C'])
    with pytest.raises(ValueError, match='can’t go with'):
        coerce_answer(q, ['A', 'None of these'])
    with pytest.raises(ValueError, match='what “Other” was'):
        coerce_answer(q, ['Other'])
    one = SurveyQuestion(prompt='Where?', kind=SurveyQuestionKind.CHOICE_ONE, options=['X', 'Y'],
                         other_option=True, none_option=None, min_picks=None, max_picks=None)
    assert coerce_answer(one, {'value': 'Other', 'other': 'A friend'}) == ('Other', 'A friend')
    with pytest.raises(ValueError):
        coerce_answer(one, 'Z')
    long = SurveyQuestion(prompt='More?', kind=SurveyQuestionKind.COMMENT, long_answer=True)
    assert coerce_answer(long, 'x' * 2500) == (None, 'x' * 2500)
    with pytest.raises(ValueError, match='3000'):
        coerce_answer(long, 'x' * 3001)
    short = SurveyQuestion(prompt='More?', kind=SurveyQuestionKind.COMMENT, long_answer=False)
    with pytest.raises(ValueError, match='1000'):
        coerce_answer(short, 'x' * 1001)


async def test_other_write_ins_reach_results_and_round_trip(event_world):
    w = event_world
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Write-ins', from_template=False)
    section = await service.add_section(w['staff'], survey.id, 'Overall', SurveyAudience.EVERYONE)
    q = await service.add_question(w['staff'], section.id, 'Where did you hear?', SurveyQuestionKind.CHOICE_ONE,
                                   options=['Discord', 'Twitter'], other_option=True)
    await service.send(w['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    responses = SurveyResponseService()
    view = await responses.open_survey(w['alice'], survey.id)
    key = view.parts[0].key
    view = await responses.save_part(w['alice'], survey.id, key, {q.id: {'value': 'Other', 'other': 'A friend'}})
    assert view.answers_in(view.parts[0]) == {q.id: {'value': 'Other', 'other': 'A friend'}}
    await responses.submit(w['alice'], survey.id)
    result = next(r for r in (await service.results(w['staff'], survey.id)).questions if r.question_id == q.id)
    assert list(result.overall.counts) == ['Discord', 'Twitter', 'Other']
    assert [c.text for c in result.comments] == ['A friend']


async def test_drag_places_a_question_in_another_section_but_not_above_what_it_follows(event_world):
    w = event_world
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Order', from_template=False)
    first = await service.add_section(w['staff'], survey.id, 'First', SurveyAudience.EVERYONE)
    second = await service.add_section(w['staff'], survey.id, 'Second', SurveyAudience.EVERYONE)
    a = await service.add_question(w['staff'], first.id, 'A?', SurveyQuestionKind.YES_NO)
    b = await service.add_question(w['staff'], first.id, 'B?', SurveyQuestionKind.RATING)
    c = await service.add_question(w['staff'], second.id, 'C?', SurveyQuestionKind.RATING, conditions=[
        {'key': 'answer', 'question_id': a.id, 'values': ['Yes']}])
    await service.place_question(w['staff'], b.id, second.id, 0)
    loaded = await service.get_survey(w['staff'], survey.id)
    assert [[q.prompt for q in s.questions] for s in loaded.sections] == [['A?'], ['B?', 'C?']]
    with pytest.raises(ValueError, match='would come after'):
        await service.place_question(w['staff'], a.id, second.id, 2)
    await service.place_question(w['staff'], c.id, first.id, 1)
    loaded = await service.get_survey(w['staff'], survey.id)
    assert [[q.prompt for q in s.questions] for s in loaded.sections] == [['A?', 'C?'], ['B?']]
    with pytest.raises(ValueError, match='would come after'):
        await service.place_question(w['staff'], c.id, first.id, 0)


async def test_crew_role_sections_reach_only_that_role(event_world):
    from models import Tracker

    w = event_world
    tracker = await make_member(8, 'tia')
    await Tracker.create(match=await Match.get(stage_id__isnull=False), user=tracker, approved=True)
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Crew', from_template=False)
    for title, audience in (('Comms', SurveyAudience.COMMENTATORS), ('Tracking', SurveyAudience.TRACKERS),
                            ('Broadcast', SurveyAudience.BROADCASTERS)):
        section = await service.add_section(w['staff'], survey.id, title, audience)
        await service.add_question(w['staff'], section.id, f'{title}?', SurveyQuestionKind.RATING)
    await service.send(w['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    cam = await SurveyResponseService().open_survey(w['cam'], survey.id)
    tia = await SurveyResponseService().open_survey(tracker, survey.id)
    assert [p.title for p in cam.parts] == ['Comms']
    assert [p.title for p in tia.parts] == ['Tracking']


async def test_any_tournament_conditions_ignore_which_tournament_a_part_is_about(event_world):
    w = event_world
    await Tournament.filter(id=w['ootr'].id).update(uses_tournament_room=True)
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Any', from_template=False)
    section = await service.add_section(w['staff'], survey.id, 'Per tournament', SurveyAudience.EACH_TOURNAMENT)
    for prompt, key in (('Stage any?', 'on_stage_any'), ('No stage any?', 'not_on_stage_any'),
                        ('Room any?', 'tournament_room_any'), ('No room any?', 'not_tournament_room_any')):
        await service.add_question(w['staff'], section.id, prompt, SurveyQuestionKind.YES_NO,
                                   conditions=[{'key': key}])
    await service.send(w['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    alice = await SurveyResponseService().open_survey(w['alice'], survey.id)
    bob = await SurveyResponseService().open_survey(w['bob'], survey.id)
    # Alice was on stage only in ALTTPR, but "any tournament" asks in her OoTR part too.
    assert {p.scope_label: [q.prompt for q in p.questions] for p in alice.parts} == {
        'ALTTPR': ['Stage any?', 'Room any?'], 'OoTR': ['Stage any?', 'Room any?'],
    }
    assert {p.scope_label: [q.prompt for q in p.questions] for p in bob.parts} == {
        'OoTR': ['No stage any?', 'Room any?'],
    }


async def test_progress_counts_parts_and_folds_into_tallies_on_submit(event_world):
    survey = await _sent(event_world)
    w = event_world
    responder = SurveyResponseService()
    view = await responder.open_survey(w['bob'], survey.id)
    first, second = view.parts[0], view.parts[1]
    await responder.open_part(w['bob'], survey.id, first.key)
    await responder.open_part(w['bob'], survey.id, second.key)
    rating = next(q for q in first.questions if q.kind is SurveyQuestionKind.RATING)
    await responder.save_part(w['bob'], survey.id, first.key, {rating.id: 4})
    bob = await SurveyRecipient.get(survey_id=survey.id, user_id=w['bob'].id)
    assert bob.progress == {'opened': [first.key, second.key], 'done': [first.key]}
    assert bob.last_part == first.key and bob.started_at is not None
    with pytest.raises(ValueError, match="isn't in your survey"):
        await responder.open_part(w['bob'], survey.id, '999|')

    alice = w['alice']
    await responder.open_survey(alice, survey.id)
    alice_parts = (await responder.open_survey(alice, survey.id)).parts
    await responder.open_part(alice, survey.id, alice_parts[0].key)

    results = await SurveyService().results(w['staff'], survey.id)
    d = results.dropoff
    assert (d.sent, d.opened, d.started, d.submitted) == (4, 2, 1, 0)
    by_key = {p.key: p for p in d.parts}
    assert by_key[first.key].opened == 2 and by_key[first.key].answered == 1
    assert by_key[first.key].stopped == 2  # bob saved it last, alice only opened it
    assert by_key[second.key].opened == 1 and by_key[second.key].stopped == 0

    await responder.submit(w['bob'], survey.id, anonymous=True)
    bob = await SurveyRecipient.get(survey_id=survey.id, user_id=w['bob'].id)
    # Nothing on the named row says which parts the anonymous answers came from.
    assert bob.progress == {} and bob.last_part is None and bob.started_at is None
    assert bob.finish_seconds is not None and bob.finish_seconds >= 0
    d = (await SurveyService().results(w['staff'], survey.id)).dropoff
    assert (d.started, d.submitted) == (1, 1)
    by_key = {p.key: p for p in d.parts}
    assert (by_key[first.key].opened, by_key[first.key].answered, by_key[first.key].stopped) == (2, 1, 1)
    assert (by_key[second.key].opened, by_key[second.key].answered) == (1, 0)
    assert d.median_seconds is None  # under five finishers


async def test_not_interested_stops_reminders(event_world, stub_discord_queue):
    survey = await _sent(event_world)
    w = event_world
    responder = SurveyResponseService()
    assert await responder.decline(w['bob'], survey.id) is True
    assert await responder.decline(w['bob'], survey.id) is False
    assert await SurveyService().remind(w['staff'], survey.id) == 3
    d = (await SurveyService().results(w['staff'], survey.id)).dropoff
    assert d.declined == 1
    # The link still works for someone who changes their mind.
    await _answer_everything(w['bob'], survey.id, 3)
    await responder.submit(w['bob'], survey.id)


async def test_reminder_to_someone_partway_through_says_so(event_world):
    from application.services.survey._notifications import invite_text

    survey = await SurveyService().get_survey(event_world['staff'], (await _sent(event_world)).id)
    facts = {'tournaments': [{'id': 1, 'name': 'ALTTPR'}]}
    invite = invite_text(survey, facts, name='Sam')
    assert invite.startswith('Hi Sam, ') and 'ALTTPR' in invite and 'about ' in invite
    assert "We'll share what we change" in invite
    assert invite_text(survey, facts).startswith('**')
    partway = invite_text(survey, facts, name='Sam', reminder=True, partway=True)
    assert "you're partway through" in partway and 'ALTTPR' not in partway
    assert 'last call' in invite_text(survey, facts, reminder=True, last_call=True).lower()


async def test_two_reminders_then_no_more(event_world, stub_discord_queue):
    survey = await _sent(event_world)
    service = SurveyService()
    staff = event_world['staff']
    await service.remind(staff, survey.id)
    await Survey.filter(id=survey.id).update(reminded_at=NOW - timedelta(days=2))
    await service.remind(staff, survey.id)
    assert (await Survey.get(id=survey.id)).reminders_sent == 2
    await Survey.filter(id=survey.id).update(reminded_at=NOW - timedelta(days=2))
    with pytest.raises(ValueError, match='last call'):
        await service.remind(staff, survey.id)


async def test_close_date_moves_while_open(event_world):
    survey = await _sent(event_world)
    service = SurveyService()
    later = NOW + timedelta(days=10)
    moved = await service.update_survey(event_world['staff'], survey.id, closes_at=later)
    assert moved.closes_at == later
    with pytest.raises(ValueError, match='future'):
        await service.update_survey(event_world['staff'], survey.id, closes_at=NOW - timedelta(hours=1))
    cleared = await service.update_survey(event_world['staff'], survey.id, closes_at=None)
    assert cleared.closes_at is None


def test_not_interested_button_round_trips_its_survey():
    from discordbot._ack_common import DMInteractionError
    from discordbot.survey_buttons import _parse, make_survey_decline_view

    button = make_survey_decline_view(7).children[0]
    assert button.label == 'Not interested' and not button.disabled
    assert _parse(button.custom_id) == 7
    done = make_survey_decline_view(7, declined=True).children[0]
    assert done.disabled
    with pytest.raises(DMInteractionError):
        _parse('survey:decline:x')
