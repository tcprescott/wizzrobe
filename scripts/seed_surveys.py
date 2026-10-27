"""Dev seed: Admin → Surveys and the survey page (application/services/survey/).

Three surveys per community: a draft on the builder, an open one with answers
(one named, one anonymous, one half-done draft), and last year's closed one.
Between them every question type, every audience and every status shows, plus
a question with two conditions and a section that waits on an answer.
"""

from datetime import datetime, timedelta, timezone

from models import (
    Survey,
    SurveyAnswer,
    SurveyAudience,
    SurveyQuestion,
    SurveyQuestionKind,
    SurveyRecipient,
    SurveyResponse,
    SurveyResponseStatus,
    SurveySection,
    SurveyStatus,
    Tenant,
    Tournament,
    User,
    VolunteerPosition,
)

K = SurveyQuestionKind
A = SurveyAudience

# (section title, audience, [(prompt, kind, options, condition)])
_SECTIONS = [
    ('The event', A.EVERYONE, [
        ('Overall, how was the event?', K.RATING, None, None),
        ('How likely are you to recommend it to a friend?', K.NPS, None, None),
        ('Will you come back next year?', K.CHOICE_ONE, ['Yes', 'Maybe', 'No'], None),
    ]),
    ('Your tournaments', A.EACH_TOURNAMENT, [
        ('How was the tournament room?', K.RATING, None, 'tournament_room'),
        ('How was playing on stage?', K.RATING, None, 'on_stage'),
        ('Did your matches start on time?', K.CHOICE_ONE, ['Yes', 'Mostly', 'No'], None),
        ('Anything we should change?', K.COMMENT, None, None),
    ]),
    ('Volunteering', A.EACH_POSITION, [
        ('Did you have what you needed on shift?', K.YES_NO, None, None),
        ('What helped most?', K.CHOICE_MANY, ['Briefing', 'Radio', 'Snacks', 'Shift length'], None),
    ]),
    ('Match crew', A.CREW, [
        ('How smooth was the crew signup?', K.RATING, None, None),
    ]),
    ('Check-in', A.CHECKED_IN, [
        ('Was check-in quick?', K.YES_NO, None, None),
    ]),
    ('Anything else?', A.EVERYONE, [
        ('Anything else you want to tell us?', K.COMMENT, None, None),
    ]),
]


async def _build(tenant: Tenant, survey: Survey) -> dict[str, SurveyQuestion]:
    questions: dict[str, SurveyQuestion] = {}
    for s_pos, (title, audience, rows) in enumerate(_SECTIONS):
        section = await SurveySection.create(tenant=tenant, survey=survey, title=title, audience=audience,
                                             position=s_pos)
        for q_pos, (prompt, kind, options, condition) in enumerate(rows):
            questions[prompt] = await SurveyQuestion.create(
                tenant=tenant, section=section, prompt=prompt, kind=kind, options=options,
                conditions=[{'key': condition}] if condition else None, position=q_pos,
            )
    come_back = questions['Will you come back next year?']
    section = await SurveySection.get(id=come_back.section_id)
    questions['What would bring you back?'] = await SurveyQuestion.create(
        tenant=tenant, section=section, prompt='What would bring you back?', kind=K.COMMENT, position=3,
        conditions=[{'key': 'answer', 'question_id': come_back.id, 'values': ['Maybe', 'No']}],
    )
    stage = questions['How was playing on stage?']
    questions['What went wrong on stage?'] = await SurveyQuestion.create(
        tenant=tenant, section_id=stage.section_id, prompt='What went wrong on stage?', kind=K.COMMENT, position=4,
        conditions=[{'key': 'on_stage'}, {'key': 'answer', 'question_id': stage.id, 'values': [1, 2]}],
    )
    next_year = await SurveySection.create(
        tenant=tenant, survey=survey, title='Next year', audience=A.EVERYONE, position=len(_SECTIONS) - 1,
        conditions=[{'key': 'answer', 'question_id': come_back.id, 'values': ['Yes', 'Maybe']}],
    )
    await SurveySection.filter(survey=survey, title='Anything else?').update(position=len(_SECTIONS))
    questions['What should we add next year?'] = await SurveyQuestion.create(
        tenant=tenant, section=next_year, prompt='What should we add next year?', kind=K.COMMENT, position=0,
    )
    return questions


def _facts(tournament: Tournament | None, position: VolunteerPosition | None, *, crew: bool = False) -> dict:
    tournaments = [{'id': tournament.id, 'name': tournament.short_name or tournament.name, 'matches': 2}] \
        if tournament else []
    conditions: dict = {'checked_in': True}
    if tournament:
        conditions.update(tournament_room=[tournament.id], on_stage=[tournament.id])
    if crew:
        conditions['crewed'] = True
    return {
        'tournaments': tournaments,
        'positions': [{'id': position.id, 'name': position.name, 'shifts': 3}] if position else [],
        'crew': crew, 'checked_in': True, 'conditions': conditions,
    }


async def _answer(tenant: Tenant, response: SurveyResponse, question: SurveyQuestion, value, *,
                  scope: str = '', label: str = '') -> None:
    text = value if question.kind is K.COMMENT else None
    await SurveyAnswer.create(tenant=tenant, response=response, question=question, scope=scope,
                              scope_label=label, data=None if text else {'v': value}, text=text)


async def seed_surveys_for_tenant(tenant: Tenant, users: dict[str, User]) -> None:
    staff = users['staff_user']
    now = datetime.now(timezone.utc)

    draft, created = await Survey.get_or_create(
        tenant=tenant, title='How was the weekend?',
        defaults={'status': SurveyStatus.DRAFT, 'created_by': staff,
                  'intro': 'Five minutes, tops. Questions are picked from what you did.'},
    )
    if created:
        await _build(tenant, draft)

    tournament = await Tournament.filter(tenant=tenant, is_test=False).order_by('id').first()
    position = await VolunteerPosition.filter(tenant=tenant).order_by('id').first()
    survey, created = await Survey.get_or_create(
        tenant=tenant, title='SG Live 2026 survey',
        defaults={'status': SurveyStatus.OPEN, 'created_by': staff, 'sent_by': staff, 'sent_at': now,
                  'closes_at': now + timedelta(days=7)},
    )
    if created:
        q = await _build(tenant, survey)
        scope, label = (f't:{tournament.id}', tournament.short_name or tournament.name) if tournament else ('', '')
        p_scope, p_label = (f'p:{position.id}', position.name) if position else ('', '')
        people = [
            (users['player_one'], _facts(tournament, None), True, False),
            (users['player_two'], _facts(tournament, None, crew=True), True, True),
            (users['volunteer_only'], _facts(None, position), False, False),
            (staff, _facts(tournament, position), False, False),
        ]
        for user, facts, submitted, anonymous in people:
            await SurveyRecipient.create(
                tenant=tenant, survey=survey, user=user, facts=facts, dm_sent_at=now, opened_at=now,
                submitted_at=now if submitted else None,
            )
            if user is staff:
                continue
            response = await SurveyResponse.create(
                tenant=tenant, survey=survey, user=None if anonymous else user, anonymous=anonymous,
                status=SurveyResponseStatus.SUBMITTED if submitted else SurveyResponseStatus.DRAFT,
                groups=[{'key': scope, 'label': f'{label} player'}] if facts['tournaments'] else
                [{'key': p_scope, 'label': p_label}],
                submitted_on=now.date() if submitted else None,
            )
            await _answer(tenant, response, q['Overall, how was the event?'], 4 if not anonymous else 3)
            await _answer(tenant, response, q['How likely are you to recommend it to a friend?'], 9 if not anonymous else 6)
            await _answer(tenant, response, q['Will you come back next year?'], 'Yes' if not anonymous else 'Maybe')
            if anonymous:
                await _answer(tenant, response, q['What would bring you back?'], 'Shorter waits between rounds.')
            if facts['tournaments']:
                await _answer(tenant, response, q['How was the tournament room?'], 5, scope=scope, label=label)
                await _answer(tenant, response, q['Did your matches start on time?'], 'Mostly', scope=scope, label=label)
                await _answer(tenant, response, q['Anything we should change?'],
                              'The room got loud during the finals.', scope=scope, label=label)
            if facts['positions']:
                await _answer(tenant, response, q['Did you have what you needed on shift?'], 'Yes',
                              scope=p_scope, label=p_label)
                await _answer(tenant, response, q['What helped most?'], ['Briefing', 'Radio'],
                              scope=p_scope, label=p_label)

    closed, created = await Survey.get_or_create(
        tenant=tenant, title='SG Live 2025 survey',
        defaults={'status': SurveyStatus.CLOSED, 'created_by': staff, 'sent_by': staff,
                  'sent_at': now - timedelta(days=365), 'closed_at': now - timedelta(days=350),
                  'allow_anonymous': False},
    )
    if created:
        await _build(tenant, closed)
    print('  surveys ok (draft, open with answers, closed)')
