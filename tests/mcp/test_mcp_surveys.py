"""Survey tools: staff build a draft and read results; nothing here sends one."""

from models import Role, Survey, SurveyStatus, VolunteerPosition
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'


async def test_staff_build_a_draft(db):
    _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
    async with mcp_session() as client:
        is_error, survey = await call_tool(client, raw, 'create_survey', tenant=TENANT, title='How was it?',
                                           from_template=False)
        assert not is_error, survey
        is_error, section = await call_tool(client, raw, 'add_survey_section', tenant=TENANT,
                                            survey_id=survey['id'], title='Each tournament',
                                            audience='each_tournament')
        assert not is_error, section
        is_error, first = await call_tool(client, raw, 'add_survey_question', tenant=TENANT,
                                          section_id=section['id'], prompt='Rate the room', kind='rating')
        assert not is_error, first
        is_error, follow = await call_tool(
            client, raw, 'add_survey_question', tenant=TENANT, section_id=section['id'], prompt='What went wrong?',
            kind='comment', conditions=[{'key': 'on_stage'},
                                        {'key': 'answer', 'question_id': first['id'], 'values': ['1', 2]}],
        )
        assert not is_error, follow
        assert [c['key'] for c in follow['conditions']] == ['on_stage', 'answer']
        assert follow['conditions'][1] == {'key': 'answer', 'question_id': first['id'], 'values': [1, 2]}
        is_error, full = await call_tool(client, raw, 'get_survey', tenant=TENANT, survey_id=survey['id'])
        assert [q['prompt'] for q in full['sections'][0]['questions']] == ['Rate the room', 'What went wrong?']
        is_error, results = await call_tool(client, raw, 'get_survey_results', tenant=TENANT,
                                            survey_id=survey['id'])
        assert not is_error and results['responses'] == 0
        is_error, payload = await call_tool(client, raw, 'delete_survey', tenant=TENANT, survey_id=survey['id'])
        assert not is_error, payload


async def test_a_sent_survey_cant_be_deleted_over_mcp(db):
    _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
    sent = await Survey.create(title='Sent', status=SurveyStatus.OPEN)
    async with mcp_session() as client:
        is_error, payload = await call_tool(client, raw, 'delete_survey', tenant=TENANT, survey_id=sent.id)
    assert is_error and 'draft' in payload, payload
    assert await Survey.filter(id=sent.id).exists()


async def test_players_are_refused(db):
    _, raw = await create_oauth_token(roles=[], write=True)
    async with mcp_session() as client:
        is_error, payload = await call_tool(client, raw, 'list_surveys', tenant=TENANT)
    assert is_error, payload


async def test_conditions_list_the_events_positions_and_take_them(db):
    _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
    proctor = await VolunteerPosition.create(name='Proctor')
    async with mcp_session() as client:
        is_error, choices = await call_tool(client, raw, 'list_survey_conditions', tenant=TENANT)
        assert not is_error, choices
        positions = next(c for c in choices['result'] if c['key'] == 'positions')
        assert positions['options'] == [{'id': proctor.id, 'name': 'Proctor'}]
        is_error, survey = await call_tool(client, raw, 'create_survey', tenant=TENANT, title='S',
                                           from_template=False)
        is_error, section = await call_tool(
            client, raw, 'add_survey_section', tenant=TENANT, survey_id=survey['id'], title='Running it',
            audience='each_tournament_run', conditions=[{'key': 'positions', 'position_ids': [proctor.id]}],
        )
        assert not is_error, section
        assert section['audience'] == 'each_tournament_run'
        assert section['conditions'][0]['names'] == ['Proctor']
