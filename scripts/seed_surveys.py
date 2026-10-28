"""Dev seed: Admin → Surveys and the survey page (application/services/survey/).

Three surveys per community: a draft on the builder, an open one with answers
(one named, one anonymous, one half-done draft), and last year's closed one.
Between them every question type, every audience and every status shows, plus
a question with two conditions and a section that waits on an answer.

Each community also gets an AI provider with a ``mock-`` key (canned answers,
no network) and one set of pending AI suggestions on the draft. The first
community seeded uses Claude, the rest the OpenAI-compatible adapter.
"""

from datetime import datetime, timedelta, timezone

from application.services.survey.suggestions import _clean_drafts
from application.utils import secret_box
from application.utils.mocks.mock_ai import mock_answer
from models import (
    AiProvider,
    AiProviderSetting,
    Survey,
    SurveyAnswer,
    SurveyAudience,
    SurveyPartTally,
    SurveyQuestion,
    SurveyQuestionKind,
    SurveyRecipient,
    SurveyResponse,
    SurveyResponseStatus,
    SurveySection,
    SurveyStatus,
    SurveySuggestionBatch,
    Tenant,
    Tournament,
    User,
    VolunteerPosition,
)

K = SurveyQuestionKind
A = SurveyAudience

_AGREE_STATEMENTS = ('The schedule ran on time', 'Staff answered my questions quickly',
                     'I knew where my station was')

_DESCRIPTIONS = {
    'Your tournaments': "About the tournament room and your matches. Skip anything you didn't use.",
}

# (section title, audience, [(prompt, kind, options, condition[, extras])])
_SECTIONS = [
    ('The event', A.EVERYONE, [
        ('Overall, how was the event?', K.RATING, None, None),
        ('How likely are you to recommend it to a friend?', K.NPS, None, None),
        ('Will you come back next year?', K.CHOICE_ONE, ['Yes', 'Maybe', 'No'], None),
        ('Would you play on a stream stage again?', K.YES_NO, None, 'on_stage_any'),
    ]),
    ('Your tournaments', A.EACH_TOURNAMENT, [
        ('How did you hear about the tournament?', K.CHOICE_ONE, ['Discord', 'Twitter / Bluesky', 'A friend'],
         None, {'other_option': True}),
        ('How was the tournament room?', K.RATING, None, 'tournament_room'),
        ('How was playing on stage?', K.RATING, None, 'on_stage'),
        ('Did your matches start on time?', K.CHOICE_ONE, ['Yes', 'Mostly', 'No'], None),
        ('How much do you agree?', K.AGREE_SCALE, list(_AGREE_STATEMENTS), None),
        ('Anything we should change?', K.COMMENT, None, None),
    ]),
    ('Volunteering', A.EACH_POSITION, [
        ('Did you have what you needed on shift?', K.YES_NO, None, None),
        ('What helped most?', K.CHOICE_MANY, ['Briefing', 'Radio', 'Snacks', 'Shift length'], None,
         {'other_option': True, 'none_option': 'None of these helped', 'max_picks': 2}),
    ]),
    ('Match crew', A.CREW, [
        ('How smooth was the crew signup?', K.RATING, None, None),
    ]),
    ('On commentary', A.COMMENTATORS, [
        ('Could you hear the game audio clearly?', K.YES_NO, None, None),
    ]),
    ('On tracking', A.TRACKERS, [
        ('Was the tracker layout readable?', K.RATING, None, None),
    ]),
    ('On broadcast', A.BROADCASTERS, [
        ('How was the stage handover between matches?', K.RATING, None, None),
    ]),
    ('Check-in', A.CHECKED_IN, [
        ('Was check-in quick?', K.YES_NO, None, None),
    ]),
    ('Anything else?', A.EVERYONE, [
        ('Anything else you want to tell us?', K.COMMENT, None, None, {'long_answer': True}),
    ]),
]


async def _build(tenant: Tenant, survey: Survey) -> dict[str, SurveyQuestion]:
    questions: dict[str, SurveyQuestion] = {}
    for s_pos, (title, audience, rows) in enumerate(_SECTIONS):
        section = await SurveySection.create(tenant=tenant, survey=survey, title=title, audience=audience,
                                             position=s_pos, description=_DESCRIPTIONS.get(title))
        for q_pos, (prompt, kind, options, condition, *extras) in enumerate(rows):
            questions[prompt] = await SurveyQuestion.create(
                tenant=tenant, section=section, prompt=prompt, kind=kind, options=options,
                conditions=[{'key': condition}] if condition else None, position=q_pos,
                **(extras[0] if extras else {}),
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
        conditions.update(tournament_room=[tournament.id], on_stage=[tournament.id],
                          tournament_room_any=True, on_stage_any=True)
    if crew:
        conditions['crewed'] = True
    return {
        'tournaments': tournaments,
        'positions': [{'id': position.id, 'name': position.name, 'shifts': 3}] if position else [],
        'crew': crew, 'crew_roles': ['commentary'] if crew else [], 'checked_in': True, 'conditions': conditions,
    }


async def _answer(tenant: Tenant, response: SurveyResponse, question: SurveyQuestion, value, *,
                  scope: str = '', label: str = '', other: str | None = None) -> None:
    comment = question.kind is K.COMMENT
    await SurveyAnswer.create(tenant=tenant, response=response, question=question, scope=scope,
                              scope_label=label, data=None if comment else {'v': value},
                              text=value if comment else other)


async def _seed_ai(tenant: Tenant, staff: User, draft: Survey) -> None:
    first = not await AiProviderSetting.exists()
    provider = AiProvider.ANTHROPIC if first else AiProvider.OPENAI_COMPATIBLE
    await AiProviderSetting.get_or_create(tenant=tenant, defaults={
        'provider': provider,
        'model': 'claude-opus-5-5' if first else 'gpt-local',
        'base_url': None if first else 'http://localhost:11434/v1',
        'key_ciphertext': secret_box.seal('mock-seed-key', purpose='ai-provider-key', context=f'tenant:{tenant.id}'),
        'updated_by': staff,
    })
    section = await SurveySection.filter(survey=draft).order_by('position').first()
    if section is None or await SurveySuggestionBatch.exists(survey=draft):
        return
    context = 'Seeded suggestions: no request was sent for these.'
    answer = mock_answer(context)
    await SurveySuggestionBatch.create(
        tenant=tenant, survey=draft, section=section, requested_by=staff,
        provider=provider.value, model='claude-opus-5-5' if first else 'gpt-local', context=context,
        drafts=_clean_drafts(answer.data)[:3], input_tokens=answer.input_tokens, output_tokens=answer.output_tokens,
    )


async def _seed_dropoff(tenant: Tenant, survey: Survey, users: dict[str, User]) -> None:
    """The drop-off card's rows: staff stopped partway, the volunteer said Not interested,
    and the two who sent it are folded into the per-part tallies with a time to finish."""
    sections = await SurveySection.filter(survey=survey).order_by('position').prefetch_related('questions')
    if not sections:
        return
    first, second = sections[0], sections[min(1, len(sections) - 1)]
    staff = await SurveyRecipient.get_or_none(survey=survey, user=users['staff_user'])
    if staff is not None and not staff.progress:
        staff.progress = {'opened': [f'{first.id}|', f'{second.id}|'], 'done': [f'{first.id}|']}
        staff.last_part = f'{second.id}|'
        staff.started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        await staff.save(update_fields=['progress', 'last_part', 'started_at'])
    await SurveyRecipient.filter(survey=survey, user=users['volunteer_only'], declined_at__isnull=True) \
        .update(declined_at=datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    await SurveyRecipient.filter(survey=survey, submitted_at__isnull=False, finish_seconds__isnull=True) \
        .update(finish_seconds=260)
    for section in sections[:2]:
        await SurveyPartTally.get_or_create(
            tenant=tenant, survey=survey, section=section, scope='', defaults={'opened': 2, 'answered': 2},
        )


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
    await _seed_ai(tenant, staff, draft)

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
                await _answer(tenant, response, q['How did you hear about the tournament?'],
                              'Other' if anonymous else 'Discord', scope=scope, label=label,
                              other='A friend at the venue' if anonymous else None)
                await _answer(tenant, response, q['How much do you agree?'],
                              dict(zip(_AGREE_STATEMENTS, (4, 5, 2) if not anonymous else (2, 4, 3), strict=True)),
                              scope=scope, label=label)
                await _answer(tenant, response, q['Anything we should change?'],
                              'The room got loud during the finals.', scope=scope, label=label)
            if facts['positions']:
                await _answer(tenant, response, q['Did you have what you needed on shift?'], 'Yes',
                              scope=p_scope, label=p_label)
                await _answer(tenant, response, q['What helped most?'], ['Briefing', 'Radio'],
                              scope=p_scope, label=p_label)

    await _seed_dropoff(tenant, survey, users)

    closed, created = await Survey.get_or_create(
        tenant=tenant, title='SG Live 2025 survey',
        defaults={'status': SurveyStatus.CLOSED, 'created_by': staff, 'sent_by': staff,
                  'sent_at': now - timedelta(days=365), 'closed_at': now - timedelta(days=350),
                  'allow_anonymous': False},
    )
    if created:
        await _build(tenant, closed)
    await _seed_lineage(tenant, survey, closed, users)
    print('  surveys ok (draft with AI suggestions, open with answers, closed, last year linked)')


async def _seed_lineage(tenant: Tenant, survey: Survey, last_year: Survey, users: dict[str, User]) -> None:
    """This year's questions as if duplicated from last year's, and a few answers there, for "Last time"."""
    earlier = {q.prompt: q for q in await SurveyQuestion.filter(section__survey=last_year)}
    for question in await SurveyQuestion.filter(section__survey=survey, copied_from_id=None):
        if question.prompt in earlier:
            question.copied_from_id = earlier[question.prompt].id
            await question.save(update_fields=['copied_from_id'])
    if await SurveyResponse.filter(survey=last_year).exists():
        return
    year_ago = (datetime.now(timezone.utc) - timedelta(days=360)).date()
    for user, rating, recommend in ((users['player_one'], 3, 7), (users['volunteer_only'], 4, 8)):
        response = await SurveyResponse.create(tenant=tenant, survey=last_year, user=user,
                                               status=SurveyResponseStatus.SUBMITTED, groups=[],
                                               submitted_on=year_ago)
        await _answer(tenant, response, earlier['Overall, how was the event?'], rating)
        await _answer(tenant, response, earlier['How likely are you to recommend it to a friend?'], recommend)
