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
