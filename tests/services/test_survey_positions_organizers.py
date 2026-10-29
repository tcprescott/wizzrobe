"""Survey parts for one volunteer position, and for tournament organizers."""

from datetime import timedelta

import pytest

from application.services import SurveyResponseService, SurveyService
from application.services.survey import audiences, conditions, plan
from models import (
    SurveyAudience,
    SurveyQuestionKind,
    SurveyRecipient,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.factories import make_member
from tests.services.test_survey_service import NOW


async def _runner(world):
    runner = await make_member(7, 'rita')
    position = await VolunteerPosition.create(name='Runner')
    shift = await VolunteerShift.create(position=position, starts_at=NOW, ends_at=NOW + timedelta(hours=2))
    await VolunteerAssignment.create(shift=shift, user=runner)
    return runner, position


async def _survey_with(world, title: str, audience=SurveyAudience.EVERYONE, section_conditions=None):
    service = SurveyService()
    survey = await service.create_survey(world['staff'], 'SGL26 survey', from_template=False)
    section = await service.add_section(world['staff'], survey.id, title, audience, conditions=section_conditions)
    await service.add_question(world['staff'], section.id, 'How was it?', SurveyQuestionKind.RATING)
    await service.send(world['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    return survey, section


async def _titles(user, survey_id: int) -> list:
    view = await SurveyResponseService().open_survey(user, survey_id)
    return [p.title for p in view.parts]


async def test_a_positions_part_shows_once_to_who_worked_it(event_world):
    w = event_world
    runner, _ = await _runner(w)
    survey, section = await _survey_with(
        w, 'Proctoring', section_conditions=[{'key': 'positions', 'position_ids': [w['desk'].id]}])
    assert section.conditions == [{'key': 'positions', 'position_ids': [w['desk'].id], 'names': ['Proctor']}]
    assert await _titles(w['vic'], survey.id) == ['Proctoring']
    assert await _titles(runner, survey.id) == []
    assert await _titles(w['alice'], survey.id) == []
    assert conditions.describe(section.conditions[0]) == 'Only if worked as Proctor'


async def test_picking_two_positions_shows_it_to_either(event_world):
    w = event_world
    runner, runner_position = await _runner(w)
    survey, section = await _survey_with(
        w, 'Desks', section_conditions=[{'key': 'positions', 'position_ids': [w['desk'].id, runner_position.id]}])
    assert await _titles(w['vic'], survey.id) == ['Desks']
    assert await _titles(runner, survey.id) == ['Desks']
    assert await _titles(w['bob'], survey.id) == []
    assert conditions.describe(section.conditions[0]) == 'Only if worked as Proctor or Runner'


async def test_volunteered_holds_for_anyone_with_a_shift(event_world):
    w = event_world
    runner, _ = await _runner(w)
    survey, _ = await _survey_with(w, 'Volunteering', section_conditions=[{'key': 'volunteered'}])
    assert await _titles(w['vic'], survey.id) == ['Volunteering']
    assert await _titles(runner, survey.id) == ['Volunteering']
    assert await _titles(w['alice'], survey.id) == []


async def test_positions_must_come_from_this_events_schedule(event_world):
    w = event_world
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Draft', from_template=False)
    with pytest.raises(ValueError, match='at least one position'):
        await service.add_section(w['staff'], survey.id, 'X', conditions=[{'key': 'positions', 'position_ids': []}])
    with pytest.raises(ValueError, match="volunteer schedule"):
        await service.add_section(w['staff'], survey.id, 'X', conditions=[{'key': 'positions', 'position_ids': [99999]}])
    with pytest.raises(ValueError, match="volunteer schedule"):
        await service.add_section(w['staff'], survey.id, 'X', conditions=[{'key': 'positions', 'position_ids': ['x']}])


async def test_names_are_refreshed_from_the_schedule_not_the_caller(event_world):
    w = event_world
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Draft', from_template=False)
    section = await service.add_section(w['staff'], survey.id, 'X', conditions=[
        {'key': 'positions', 'position_ids': [w['desk'].id], 'names': ['<b>Not this</b>']}])
    assert section.conditions[0]['names'] == ['Proctor']


async def test_a_tournament_admin_gets_the_survey_and_a_part_per_tournament_run(event_world):
    w = event_world
    tara = await make_member(8, 'tara')
    await w['alttpr'].admins.add(tara)
    await w['ootr'].admins.add(tara)
    survey, _ = await _survey_with(w, 'Running your tournament', SurveyAudience.EACH_TOURNAMENT_RUN)
    recipient = await SurveyRecipient.get(survey_id=survey.id, user_id=tara.id)
    assert [t['name'] for t in recipient.facts['organized']] == ['ALTTPR', 'OoTR']
    view = await SurveyResponseService().open_survey(tara, survey.id)
    assert [(p.title, p.scope) for p in view.parts] == [
        ('ALTTPR', f"o:{w['alttpr'].id}"), ('OoTR', f"o:{w['ootr'].id}"),
    ]
    assert await _titles(w['alice'], survey.id) == []


async def test_ran_a_tournament_splits_organizers_from_everyone_else(event_world):
    w = event_world
    await w['alttpr'].admins.add(w['bob'])
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'SGL26 survey', from_template=False)
    ran = await service.add_section(w['staff'], survey.id, 'Organizers', conditions=[{'key': 'ran_tournament'}])
    others = await service.add_section(w['staff'], survey.id, 'Everyone else',
                                       conditions=[{'key': 'not_ran_tournament'}])
    for section in (ran, others):
        await service.add_question(w['staff'], section.id, 'How was it?', SurveyQuestionKind.RATING)
    await service.send(w['staff'], survey.id, closes_at=NOW + timedelta(days=7))
    assert await _titles(w['bob'], survey.id) == ['Organizers']
    assert await _titles(w['alice'], survey.id) == ['Everyone else']


async def test_condition_choices_list_the_events_positions(event_world):
    w = event_world
    _, runner_position = await _runner(w)
    choices = {c.key: c for c in await SurveyService().condition_choices(w['staff'])}
    assert [o['name'] for o in choices['positions'].options] == ['Proctor', 'Runner']
    assert choices['volunteered'].options == []
    assert {'ran_tournament', 'not_ran_tournament'} <= set(choices)
    with pytest.raises(PermissionError):
        await SurveyService().condition_choices(w['bob'])


async def test_preview_offers_an_organizer_and_each_position(event_world):
    w = event_world
    await _runner(w)
    service = SurveyService()
    survey = await service.create_survey(w['staff'], 'Draft', from_template=False)
    section = await service.add_section(
        w['staff'], survey.id, 'Proctoring', conditions=[{'key': 'positions', 'position_ids': [w['desk'].id]}])
    await service.add_question(w['staff'], section.id, 'How was it?', SurveyQuestionKind.RATING)
    paths = {p.persona: p for p in await service.preview_paths(w['staff'], survey.id)}
    assert paths['organizer'].label == 'Tournament organizer'
    assert paths[f"position:{w['desk'].id}"].parts == 1
    runner = next(p for p in paths.values() if p.label == 'Runner')
    assert runner.parts == 0
    assert runner.hidden[0].reason == 'Only if worked as Proctor'
    _, parts = await service.preview(w['staff'], survey.id, f"position:{w['desk'].id}")
    assert [p.title for p in parts] == ['Proctoring']


def test_organizers_count_as_one_group():
    facts = {'organized': [{'id': 1, 'name': 'ALTTPR'}], 'tournaments': [], 'positions': []}
    assert {'key': 'organizers', 'label': 'Tournament organizers'} in audiences.groups_for(facts)
    rates = audiences.audience_rates([{'facts': facts, 'submitted_at': NOW}])
    assert [(r.label, r.responded) for r in rates] == [('Tournament organizers', 1)]


def test_organizer_preview_runs_the_events_tournaments():
    facts = plan.preview_facts('organizer', [{'id': 3, 'name': 'ALTTPR'}], [])
    assert facts['organized'] == [{'id': 3, 'name': 'ALTTPR'}]
    assert facts['conditions']['ran_tournament'] is True
