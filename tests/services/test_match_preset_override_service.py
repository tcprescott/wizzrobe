"""Staff forcing one match's preset, and handing it back.

Players choosing hard mode is a match form question plus a seed rule
(``test_match_form_service``); what's pinned here is the override: it beats the
players' answers and every rule, only staff may set it, it must fit the
tournament's randomizer, it shuts when the seed rolls, and its DM links
somewhere the reader can act.
"""

import itertools

import pytest

from application.services import MatchFormService, MatchPresetOverrideService, SeedRuleService
from models import GeneratedSeeds, Match, MatchPlayers, Preset, Role, Tournament, UserRole
from tests.factories import make_user, utc

_preset_seq = itertools.count()
_discord_ids = itertools.count(910000)


async def _staff():
    user = await make_user(discord_id=next(_discord_ids), username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _match(*, seeded=False):
    n = next(_preset_seq)
    standard = await Preset.create(name=f'Standard {n}', randomizer='alttpr', settings={'mode': 'open'})
    hard = await Preset.create(name=f'Hard Mode {n}', randomizer='alttpr', settings={'pool': 'hard'})
    tournament = await Tournament.create(name='T', seed_generator='alttpr', preset=standard)
    seed = None
    if seeded:
        seed = await GeneratedSeeds.create(seed_url='https://example.test/seed', randomizer='alttpr', preset=standard)
    match = await Match.create(tournament=tournament, scheduled_at=utc(2025, 1, 15, 19, 30), generated_seed=seed)
    users = []
    for i in range(2):
        user = await make_user(discord_id=next(_discord_ids), username=f'p{i}')
        await MatchPlayers.create(match=match, user=user)
        users.append(user)
    return match, users, standard, hard


async def _hard_mode_yes(match, users, hard):
    staff = await _staff()
    form = await MatchFormService().save_form(match.tournament_id, [
        {'kind': 'yes_no', 'prompt': 'Play Hard mode?', 'visibility': 'never'},
    ], staff)
    key = form[0]['key']
    await SeedRuleService().save_rules(match.tournament_id, [
        {'name': 'Hard mode', 'when': {'answer': [{'q': key, 'who': 'every', 'is': ['yes']}]},
         'then': {'preset': hard.id}},
    ], staff)
    for user in users:
        await MatchFormService().answer(match.id, user, key, 'yes')


async def _resolve(match_id):
    match = await Match.get(id=match_id).prefetch_related('tournament', 'tournament__preset')
    return await SeedRuleService().resolve(match)


class TestForcing:
    async def test_forcing_beats_the_tournament_preset(self, db, stub_discord_queue):
        match, _, _, hard = await _match()
        await MatchPresetOverrideService().set_override(match.id, hard.id, await _staff())
        resolution = await _resolve(match.id)
        assert resolution.preset.id == hard.id
        assert resolution.record == {'source': 'override', 'preset_id': hard.id}

    async def test_forcing_beats_everyone_saying_yes(self, db, stub_discord_queue):
        match, users, standard, hard = await _match()
        await _hard_mode_yes(match, users, hard)
        await MatchPresetOverrideService().set_override(match.id, standard.id, await _staff())
        assert (await _resolve(match.id)).preset.id == standard.id

    async def test_clearing_it_hands_back_to_the_rules(self, db, stub_discord_queue):
        """The players' answers stay put under an override, so clearing it restores them."""
        match, users, standard, hard = await _match()
        await _hard_mode_yes(match, users, hard)
        staff = await _staff()
        await MatchPresetOverrideService().set_override(match.id, standard.id, staff)
        await MatchPresetOverrideService().set_override(match.id, None, staff)
        resolution = await _resolve(match.id)
        assert resolution.record['source'] == 'rule'
        assert resolution.preset.id == hard.id

    async def test_any_preset_of_the_randomizer_is_offered(self, db, stub_discord_queue):
        match, _, standard, hard = await _match()
        await Preset.create(name='Combo', randomizer='smz3', settings={})
        await match.fetch_related('tournament')
        state = await MatchPresetOverrideService().state(match)
        assert {pid for pid, _ in state.choices} >= {standard.id, hard.id}
        assert all(name != 'Combo' for _, name in state.choices)

    async def test_another_randomizers_preset_is_refused(self, db, stub_discord_queue):
        match, *_ = await _match()
        combo = await Preset.create(name='Combo', randomizer='smz3', settings={})
        with pytest.raises(ValueError, match='smz3'):
            await MatchPresetOverrideService().set_override(match.id, combo.id, await _staff())

    async def test_a_player_cannot_overrule_their_own_match(self, db, stub_discord_queue):
        match, (p1, _p2), _, hard = await _match()
        with pytest.raises(PermissionError):
            await MatchPresetOverrideService().set_override(match.id, hard.id, p1)

    async def test_refused_once_the_seed_is_rolled(self, db, stub_discord_queue):
        match, _, _, hard = await _match(seeded=True)
        with pytest.raises(ValueError, match='already been rolled'):
            await MatchPresetOverrideService().set_override(match.id, hard.id, await _staff())


class TestOverrideNotification:
    """A call to action must land on a control the reader can actually use."""

    @staticmethod
    def _spy(monkeypatch):
        from application.services import notification_links
        from application.services.discord import discord_service as ds
        from application.utils.discord_messages import DMLink

        sent = []

        async def fake_send_dm(self, discord_id, message, **kwargs):
            sent.append(kwargs.get('link'))
            return True, None

        async def fake_form(match_id, **kwargs):
            return DMLink('Answer the questions', 'https://x.test/form')

        async def fake_match(match_id, **kwargs):
            return DMLink('View your match', 'https://x.test/match')

        monkeypatch.setattr(ds.DiscordService, 'send_dm', fake_send_dm)
        monkeypatch.setattr(notification_links, 'player_match_form', fake_form)
        monkeypatch.setattr(notification_links, 'player_match', fake_match)
        return sent

    async def _notify(self, monkeypatch, forced):
        from application.services.match._preset_override_notifications import notify_preset_override

        sent = self._spy(monkeypatch)
        player = await make_user(discord_id=next(_discord_ids), username='p')
        await notify_preset_override(
            match_id=1, tournament_name='T', preset_name='Hard Mode',
            when=None, recipients=[player], forced=forced,
        )
        return [link.label for link in sent]

    async def test_forcing_links_the_match(self, db, stub_discord_queue, monkeypatch):
        """Staff just decided the settings, so there's nothing left to answer about them."""
        assert await self._notify(monkeypatch, True) == ['View your match']

    async def test_handing_it_back_links_the_form(self, db, stub_discord_queue, monkeypatch):
        assert await self._notify(monkeypatch, False) == ['Answer the questions']
