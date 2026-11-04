"""Seed rules at roll time, and who may change them.

The rolls that matter: a tournament with no rules rolls what it always did,
hard mode and staff's override still come first, the first fitting rule
decides, its tweaks reach the settings sent upstream without touching the
preset, and the seed records why.
"""

import json
import random
from unittest.mock import AsyncMock

import pytest

from application.services import SeedRuleService
from application.services.round_schedule_service import RoundScheduleService
from application.services.seed_rules import MatchFacts
from models import AuditLog, GeneratedSeeds, Match, MatchPlayers, Preset, Tournament, User
from tests.factories import make_user, utc
from tests.services._match_schedule_setup import build_service, make_dm_user, make_staff


async def _staff():
    return await User.get_or_none(discord_id=9000) or await make_staff()


def _rolled(url='https://alttpr.com/h/x'):
    from application.services.seedgen_service import RolledSeed
    from application.utils.seed_provider import ProviderCall

    return ProviderCall(
        value=RolledSeed(url=url, settings=None),
        provider='alttpr', operation='generate_seed', attempts=1, latency_ms=5,
    )


async def _setup(*, rules=None, hard=False, game=None, best_of=None, title=None):
    standard = await Preset.create(name='Open', randomizer='alttpr', settings={'mode': 'open'})
    other = await Preset.create(name='Standard', randomizer='alttpr', settings={'mode': 'standard', 'item': {'pool': 'normal'}})
    hard_preset = await Preset.create(name='Hard', randomizer='alttpr', settings={'pool': 'hard'}) if hard else None
    tournament = await Tournament.create(
        name='T', seed_generator='alttpr', preset=standard,
    )
    if rules is not None:
        await SeedRuleService().save_rules(tournament.id, rules(standard, other), await _staff())
    first = None
    for n in range(1, game or 1):
        earlier = await Match.create(
            tournament=tournament, scheduled_at=utc(2025, 1, 15, 17 + n, 0),
            set_best_of=best_of, set_first=first,
        )
        first = first or earlier
    match = await Match.create(
        tournament=tournament, scheduled_at=utc(2025, 1, 15, 21, 30),
        set_best_of=best_of, set_first=first, title=title,
    )
    players = []
    for i in range(2):
        user = await make_dm_user(60 + i, name=f'sr{i}')
        await MatchPlayers.create(match=match, user=user)
        players.append(user)
    return tournament, match, players, standard, other, hard_preset


async def _roll(match):
    service = build_service()
    service.seedgen_service.generate_seed_call = AsyncMock(return_value=_rolled())
    ok, msg, _ = await service.generate_seed(match.id, await _staff())
    assert ok, msg
    preset = service.seedgen_service.generate_seed_call.await_args.args[1]
    return await GeneratedSeeds.all().order_by('-id').first(), preset


def _game2(standard, other):
    return [{'name': 'Game 2', 'when': {'game': [2]}, 'then': {'preset': other.id},
             'tweaks': {'item.swords': 'assured'}}]


class TestRolling:
    async def test_no_rules_rolls_the_tournament_preset(self, db, stub_discord_queue):
        _, match, _, standard, _, _ = await _setup()

        seed, preset = await _roll(match)

        assert seed.preset_id == standard.id
        assert preset.settings == {'mode': 'open'}
        assert seed.resolution == {'source': 'default', 'preset_id': standard.id}

    async def test_a_fitting_rule_decides_and_its_tweaks_are_sent(self, db, stub_discord_queue):
        _, match, _, _, other, _ = await _setup(rules=_game2, game=2, best_of=3)

        seed, preset = await _roll(match)

        assert seed.preset_id == other.id
        assert preset.settings == {'mode': 'standard', 'item': {'pool': 'normal', 'swords': 'assured'}}
        assert (await Preset.get(id=other.id)).settings == {'mode': 'standard', 'item': {'pool': 'normal'}}
        assert seed.resolution['source'] == 'rule'
        assert seed.resolution['rule_name'] == 'Game 2'
        assert seed.resolution['tweaks'] == {'item.swords': 'assured'}
        audit = await AuditLog.filter(action='match.seed_rolled').first()
        assert json.loads(audit.details)['seed_rule'] == 'Game 2'

    async def test_why_names_the_rule_for_staff(self, db, stub_discord_queue):
        _, match, _, _, _, _ = await _setup(rules=_game2, game=2, best_of=3)

        seed, _ = await _roll(match)

        assert await SeedRuleService().why(seed) == 'Rule 1: Game 2 → Standard + tweaks'

    async def test_a_rule_that_does_not_fit_leaves_the_default(self, db, stub_discord_queue):
        _, match, _, standard, _, _ = await _setup(rules=_game2, game=1, best_of=3)

        seed, _ = await _roll(match)

        assert seed.preset_id == standard.id

    async def test_staff_override_beats_the_rules(self, db, stub_discord_queue):
        _, match, _, standard, _, _ = await _setup(rules=_game2, hard=True, game=2, best_of=3)
        match.forced_preset = standard
        await match.save(update_fields=['forced_preset_id'])

        seed, _ = await _roll(match)

        assert seed.preset_id == standard.id
        assert seed.resolution['source'] == 'override'

    async def test_a_deleted_preset_falls_through(self, db, stub_discord_queue):
        _, match, _, standard, other, _ = await _setup(rules=_game2, game=2, best_of=3)
        await other.delete()

        seed, _ = await _roll(match)

        assert seed.preset_id == standard.id

    async def test_round_comes_from_the_bracket(self, db, stub_discord_queue, monkeypatch):
        def rules(standard, other):
            return [{'name': 'Final', 'when': {'round': ['gf']}, 'then': {'preset': other.id}}]

        _, match, _, _, other, _ = await _setup(rules=rules)
        monkeypatch.setattr(RoundScheduleService, 'round_key_for_match', AsyncMock(return_value='gf'))

        seed, _ = await _roll(match)

        assert seed.preset_id == other.id

    async def test_weighted_outcome_records_the_draw(self, db, stub_discord_queue):
        def rules(standard, other):
            return [{'name': 'Mix', 'then': {'weighted': [
                {'preset': standard.id, 'weight': 1}, {'preset': other.id, 'weight': 3}]}}]

        tournament, match, _, standard, other, _ = await _setup(rules=rules)
        await match.fetch_related('tournament', 'tournament__preset', 'players')

        resolution = await SeedRuleService().resolve(match, rng=random.Random(3))

        assert resolution.record['draw']['weights'] == {str(standard.id): 1, str(other.id): 3}
        assert resolution.record['draw']['picked'] == resolution.preset.id


class TestPreview:
    async def test_a_draft_names_the_rule_without_rolling(self, db):
        tournament, match, _, standard, other, _ = await _setup(game=2, best_of=3)
        staff = await _staff()

        out = await SeedRuleService().preview(
            tournament.id, staff, rules=_game2(standard, other), match_id=match.id,
        )

        assert (out['source'], out['rule_number']) == ('rule', 1)
        assert out['settings'] == {'mode': 'standard', 'item': {'pool': 'normal', 'swords': 'assured'}}
        assert not await GeneratedSeeds.exists()

    async def test_no_rule_is_the_tournament_preset(self, db):
        tournament, *_ = await _setup()

        out = await SeedRuleService().preview(tournament.id, await _staff(), facts=MatchFacts(game=1))

        assert out['source'] == 'default'
        assert out['outcomes'][0]['name'] == 'Open'

    async def test_editor_lists_only_this_randomizers_presets(self, db):
        tournament, *_ = await _setup()
        await Preset.create(name='Other game', randomizer='smz3', settings={})

        data = await SeedRuleService().editor(tournament.id, await _staff())

        assert {p['name'] for p in data['presets']} == {'Open', 'Standard'}
        assert data['default_preset']['name'] == 'Open'


class TestEditing:
    async def test_staff_save_and_read_back(self, db):
        tournament, *_ = await _setup()
        staff = await _staff()
        preset_id = tournament.preset_id

        saved = await SeedRuleService().save_rules(
            tournament.id, [{'name': 'All', 'then': {'preset': preset_id}}], staff,
        )

        assert await SeedRuleService().get_rules(tournament.id, staff) == saved
        assert await AuditLog.filter(action='tournament.seed_rules_updated').exists()

    async def test_a_player_may_not_edit(self, db):
        tournament, *_ = await _setup()
        player = await make_user(discord_id=777001, username='nosy')

        with pytest.raises(PermissionError):
            await SeedRuleService().save_rules(tournament.id, [], player)

    async def test_another_communitys_preset_is_refused(self, db):
        tournament, *_ = await _setup()

        with pytest.raises(ValueError, match="Rule 1: names a preset"):
            await SeedRuleService().save_rules(
                tournament.id, [{'then': {'preset': 999999}}], await _staff(),
            )

    async def test_a_preset_of_another_randomizer_is_refused(self, db):
        tournament, *_ = await _setup()
        smz3 = await Preset.create(name='Other game', randomizer='smz3', settings={})

        with pytest.raises(ValueError, match='smz3 preset'):
            await SeedRuleService().save_rules(tournament.id, [{'then': {'preset': smz3.id}}], await _staff())

    async def test_text_view_round_trips(self, db):
        tournament, _, _, standard, other, _ = await _setup(rules=_game2)
        staff = await _staff()
        service = SeedRuleService()

        text = await service.export_text(tournament.id, staff)
        parsed = await service.parse_text(tournament.id, text, staff)

        assert 'Standard' in text
        assert parsed[0]['then'] == {'preset': other.id}
        assert parsed[0]['tweaks'] == {'item.swords': 'assured'}
