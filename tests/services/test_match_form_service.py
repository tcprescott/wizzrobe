"""The match form: building it, who answers, what other players see, and the roll reading it.

The promises that matter: a hidden (``never``) question never shows another
player's answer, answers lock once the seed rolls, a question a rule reads
can't be removed, and a rule on "every player answered Yes" decides the roll.
"""

import pytest

from application.services import MatchFormService, SeedRuleService, seed_rules
from models import (
    AuditLog,
    Bracket,
    BracketEntrant,
    BracketEntry,
    BracketFormat,
    GeneratedSeeds,
    MatchFormAnswer,
    Preset,
)
from tests.services.test_seed_rule_service import _roll, _setup, _staff


def _hard_question(**over):
    q = {'kind': 'yes_no', 'prompt': 'Play on hard?', 'visibility': 'never'}
    q.update(over)
    return q


async def _form(tournament, questions):
    return await MatchFormService().save_form(tournament.id, questions, await _staff())


class TestBuildingTheForm:
    async def test_saves_and_keeps_keys(self, db, stub_discord_queue):
        tournament, *_ = await _setup()
        saved = await _form(tournament, [_hard_question()])
        assert [c['value'] for c in saved[0]['choices']] == ['yes', 'no']
        again = await _form(tournament, [dict(saved[0], prompt='Hard mode?')])
        assert again[0]['key'] == saved[0]['key'] and again[0]['prompt'] == 'Hard mode?'
        assert await AuditLog.filter(action='tournament.match_form_updated').count() == 2

    async def test_rejects_bad_questions(self, db, stub_discord_queue):
        tournament, *_ = await _setup()
        with pytest.raises(ValueError, match='Question 1'):
            await _form(tournament, [{'kind': 'choice_one', 'prompt': 'Pick', 'choices': [{'label': 'Only'}]}])
        with pytest.raises(ValueError, match=r'bounds|below'):
            await _form(tournament, [{'kind': 'number', 'prompt': 'How many', 'config': {'min': 5, 'max': 2}}])

    async def test_linked_preset_must_match_the_randomizer(self, db, stub_discord_queue):
        tournament, *_ = await _setup()
        smz3 = await Preset.create(name='Combo', randomizer='smz3', settings={})
        with pytest.raises(ValueError, match='another randomizer'):
            await _form(tournament, [{'kind': 'choice_one', 'prompt': 'Mode', 'choices': [
                {'label': 'A', 'preset': smz3.id}, {'label': 'B'},
            ]}])

    async def test_a_question_a_rule_reads_cannot_be_removed(self, db, stub_discord_queue):
        tournament, _m, _p, _s, other, _h = await _setup()
        saved = await _form(tournament, [_hard_question()])
        key = saved[0]['key']
        await SeedRuleService().save_rules(tournament.id, [
            {'name': 'Hard', 'when': {'answer': [{'q': key, 'who': 'every', 'is': ['yes']}]},
             'then': {'preset': other.id}},
        ], await _staff())
        assert (await MatchFormService().get_form(tournament.id, await _staff()))[0]['used_by'] == [1]
        with pytest.raises(ValueError, match='seed rule 1'):
            await _form(tournament, [])

    async def test_a_rule_cannot_name_a_missing_question(self, db, stub_discord_queue):
        tournament, _m, _p, _s, other, _h = await _setup()
        with pytest.raises(ValueError, match="doesn't have"):
            await SeedRuleService().save_rules(tournament.id, [
                {'name': 'X', 'when': {'answer': [{'q': 'nope', 'who': 'any'}]}, 'then': {'preset': other.id}},
            ], await _staff())

    async def test_players_cannot_edit_the_form(self, db, stub_discord_queue):
        tournament, _m, players, *_ = await _setup()
        with pytest.raises(PermissionError):
            await MatchFormService().save_form(tournament.id, [_hard_question()], players[0])


class TestAnswering:
    async def test_hidden_answers_stay_private(self, db, stub_discord_queue):
        tournament, match, players, *_ = await _setup()
        key = (await _form(tournament, [_hard_question()]))[0]['key']
        await MatchFormService().answer(match.id, players[0], key, 'yes')
        theirs = await MatchFormService().player_form(match.id, players[1])
        assert theirs['questions'][0]['answer'] is None
        assert theirs['questions'][0]['others'] == []
        mine = await MatchFormService().player_form(match.id, players[0])
        assert mine['questions'][0]['answer'] == 'yes'

    async def test_right_away_answers_show_to_the_other_player(self, db, stub_discord_queue):
        tournament, match, players, *_ = await _setup()
        key = (await _form(tournament, [_hard_question(visibility='right_away')]))[0]['key']
        await MatchFormService().answer(match.id, players[0], key, 'no')
        theirs = await MatchFormService().player_form(match.id, players[1])
        assert theirs['questions'][0]['others'] == [{'name': players[0].preferred_name, 'value': 'no'}]

    async def test_outsiders_and_bad_values_are_refused(self, db, stub_discord_queue):
        from tests.factories import make_user

        tournament, match, players, *_ = await _setup()
        key = (await _form(tournament, [_hard_question()]))[0]['key']
        with pytest.raises(PermissionError):
            await MatchFormService().answer(match.id, await make_user(777), key, 'yes')
        with pytest.raises(ValueError, match='Pick one'):
            await MatchFormService().answer(match.id, players[0], key, 'maybe')

    async def test_clearing_an_answer(self, db, stub_discord_queue):
        tournament, match, players, *_ = await _setup()
        key = (await _form(tournament, [_hard_question()]))[0]['key']
        await MatchFormService().answer(match.id, players[0], key, 'yes')
        await MatchFormService().answer(match.id, players[0], key, None)
        assert await MatchFormAnswer.filter(match_id=match.id).count() == 0

    async def test_higher_seed_needs_native_seeds(self, db, stub_discord_queue):
        tournament, match, players, *_ = await _setup()
        await _form(tournament, [_hard_question(who='higher_seed', visibility='right_away')])
        assert (await MatchFormService().player_form(match.id, players[0]))['questions'] == []
        bracket = await Bracket.create(tournament=tournament, name='Main', format=BracketFormat.SINGLE_ELIM)
        for seed, user in zip((4, 1), players, strict=True):
            entrant = await BracketEntrant.create(tournament=tournament, display_name=user.username, user=user)
            await BracketEntry.create(bracket=bracket, entrant=entrant, seed=seed)
        assert (await MatchFormService().player_form(match.id, players[0]))['questions'] == []
        assert len((await MatchFormService().player_form(match.id, players[1]))['questions']) == 1


class TestRolling:
    async def test_every_player_yes_rolls_the_rule(self, db, stub_discord_queue):
        tournament, match, players, standard, other, _h = await _setup()
        key = (await _form(tournament, [_hard_question()]))[0]['key']
        await SeedRuleService().save_rules(tournament.id, [
            {'name': 'Hard', 'when': {'answer': [{'q': key, 'who': 'every', 'is': ['yes']}]},
             'then': {'preset': other.id}},
        ], await _staff())
        await MatchFormService().answer(match.id, players[0], key, 'yes')
        facts = await SeedRuleService().facts_for(match)
        assert not seed_rules.matches({'answer': [{'q': key, 'who': 'every', 'is': ['yes']}]}, facts)
        await MatchFormService().answer(match.id, players[1], key, 'yes')
        seed, preset = await _roll(match)
        assert preset.id == other.id
        assert seed.resolution['source'] == 'rule'
        with pytest.raises(ValueError, match='locked'):
            await MatchFormService().answer(match.id, players[0], key, 'no')

    async def test_from_answer_rolls_the_linked_preset(self, db, stub_discord_queue):
        tournament, match, players, standard, other, _h = await _setup()
        key = (await _form(tournament, [{'kind': 'choice_one', 'prompt': 'Mode', 'choices': [
            {'label': 'Open', 'preset': standard.id}, {'label': 'Standard', 'preset': other.id},
        ]}]))[0]['key']
        form = await MatchFormService().get_form(tournament.id, await _staff())
        std_value = form[0]['choices'][1]['value']
        await SeedRuleService().save_rules(tournament.id, [
            {'name': 'Players pick', 'when': {}, 'then': {'from_answer': key}},
        ], await _staff())
        for user in players:
            await MatchFormService().answer(match.id, user, key, std_value)
        _seed, preset = await _roll(match)
        assert preset.id == other.id

    async def test_split_answers_fall_through(self, db, stub_discord_queue):
        tournament, match, players, standard, other, _h = await _setup()
        form = await _form(tournament, [{'kind': 'choice_one', 'prompt': 'Mode', 'choices': [
            {'label': 'Open', 'preset': standard.id}, {'label': 'Standard', 'preset': other.id},
        ]}])
        key = form[0]['key']
        await SeedRuleService().save_rules(tournament.id, [
            {'name': 'Players pick', 'when': {}, 'then': {'from_answer': key}},
        ], await _staff())
        await MatchFormService().answer(match.id, players[0], key, form[0]['choices'][0]['value'])
        await MatchFormService().answer(match.id, players[1], key, form[0]['choices'][1]['value'])
        seed, preset = await _roll(match)
        assert preset.id == standard.id
        assert seed.resolution['source'] == 'default'
        assert await GeneratedSeeds.all().count() == 1


class TestBeforeYourMatchDm:
    async def _sent(self, monkeypatch, match):
        from application.services.match import _match_form_notifications as notif

        sent = []

        async def fake_send(self, user_id, message, **kwargs):
            sent.append((user_id, message, kwargs))
            return True, ''

        monkeypatch.setattr(notif.DiscordService, 'send_dm', fake_send)
        await notif.notify_match_form(match)
        return sent

    async def test_each_asked_player_gets_one_with_the_link(self, db, stub_discord_queue, monkeypatch):
        tournament, match, players, *_ = await _setup()
        await _form(tournament, [_hard_question()])
        sent = await self._sent(monkeypatch, match)
        assert sorted(s[0] for s in sent) == sorted(p.discord_id for p in players)
        assert 'a question for you' in sent[0][1]
        assert f'form={match.id}' in sent[0][2]['link'].url
        assert sent[0][2]['notice'].key == 'match_form_asked'

    async def test_no_form_no_dm(self, db, stub_discord_queue, monkeypatch):
        _t, match, *_ = await _setup()
        assert await self._sent(monkeypatch, match) == []


class TestRosterChanges:
    """Answers belong to the match as it stood: a dropped player's go, and a move clears them all."""

    async def test_a_dropped_player_loses_their_answers(self, db, stub_discord_queue):
        from application.services import MatchService

        tournament, match, players, *_ = await _setup()
        key = (await _form(tournament, [_hard_question()]))[0]['key']
        for user in players:
            await MatchFormService().answer(match.id, user, key, 'yes')
        await MatchService().update_match(match.id, player_ids=[players[0].id], actor=await _staff())
        left = await MatchFormAnswer.filter(match_id=match.id).values_list('user_id', flat=True)
        assert list(left) == [players[0].id]

    async def test_moving_the_match_clears_every_answer(self, db, stub_discord_queue):
        from application.services import MatchService
        from models import Tournament

        tournament, match, players, *_ = await _setup()
        key = (await _form(tournament, [_hard_question()]))[0]['key']
        await MatchFormService().answer(match.id, players[0], key, 'yes')
        elsewhere = await Tournament.create(name='Elsewhere')
        await MatchService().update_match(match.id, tournament_id=elsewhere.id, actor=await _staff())
        assert await MatchFormAnswer.filter(match_id=match.id).count() == 0
