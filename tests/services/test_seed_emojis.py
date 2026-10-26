"""Seed codes drawn with the bot's application emojis (unit, no Discord)."""

from types import SimpleNamespace

import pytest

from application.services.discord import seed_emojis
from application.utils.discord_embeds import match_embed


class _Emoji(SimpleNamespace):
    def __str__(self) -> str:
        return f'<:{self.name}:{self.id}>'


class _Bot:
    def __init__(self, names, *, ready=True, fails=False):
        self._emojis = [_Emoji(name=n, id=100 + i) for i, n in enumerate(names)]
        self._ready = ready
        self._fails = fails
        self.fetches = 0

    def is_ready(self):
        return self._ready

    async def fetch_application_emojis(self):
        self.fetches += 1
        if self._fails:
            raise RuntimeError('discord is down')
        return self._emojis


@pytest.fixture(autouse=True)
def _fresh_cache():
    seed_emojis.reset_cache()
    yield
    seed_emojis.reset_cache()


def test_emoji_names_are_discord_safe():
    assert seed_emojis.emoji_name('alttpr', 'Magic Powder') == 'alttpr_magic_powder'
    assert seed_emojis.emoji_name('dk64', 'DK Coin') == 'dk64_dk_coin'
    assert seed_emojis.emoji_name('ootr', 'SOLD OUT') == 'ootr_sold_out'
    for names in seed_emojis.expected_names().values():
        assert len(names) == len(set(names))
        assert all(2 <= len(n) <= 32 and n.replace('_', '').isalnum() for n in names)


def test_avianart_shares_the_alttpr_set():
    assert set(seed_emojis.expected_names()) == {'alttpr', 'dk64', 'ootr'}


async def test_row_uses_emojis_and_keeps_words_for_missing_ones():
    bot = _Bot(['alttpr_bow', 'ALTTPR_Hookshot'])
    row = await seed_emojis.code_row('alttpr', ['Bow', 'Hookshot', 'Lamp'], bot)
    assert row == '<:alttpr_bow:100> <:ALTTPR_Hookshot:101> Lamp'


async def test_avianart_rolls_use_alttpr_emojis():
    row = await seed_emojis.code_row('avianart', ['Bow'], _Bot(['alttpr_bow']))
    assert row == '<:alttpr_bow:100>'


@pytest.mark.parametrize('randomizer,code,bot', [
    ('alttpr', ['Bow'], _Bot([])),
    ('alttpr', ['Bow'], None),
    ('alttpr', ['Bow'], _Bot(['alttpr_bow'], ready=False)),
    ('alttpr', ['Bow'], _Bot(['alttpr_bow'], fails=True)),
    ('alttpr', None, _Bot(['alttpr_bow'])),
    ('smvaria', ['GEEMER'], _Bot(['smvaria_geemer'])),
])
async def test_no_row_falls_back_to_the_text_line(randomizer, code, bot):
    assert await seed_emojis.code_row(randomizer, code, bot) is None


async def test_list_is_cached_between_rolls():
    bot = _Bot(['alttpr_bow'])
    await seed_emojis.code_row('alttpr', ['Bow'], bot)
    await seed_emojis.code_row('alttpr', ['Bow'], bot)
    assert bot.fetches == 1


async def test_a_failed_refresh_keeps_the_last_good_list(monkeypatch):
    await seed_emojis.code_row('alttpr', ['Bow'], _Bot(['alttpr_bow']))
    monkeypatch.setattr(seed_emojis, 'REFRESH_SECONDS', 0)
    row = await seed_emojis.code_row('alttpr', ['Bow'], _Bot([], fails=True))
    assert row == '<:alttpr_bow:100>'


def test_code_field_sits_between_time_and_stage():
    from datetime import datetime, timezone
    embed = match_embed(
        title='Seed ready', color=0, tournament='Cup',
        when=datetime(2026, 10, 22, tzinfo=timezone.utc), stage_name='Stage 2',
        code='<:alttpr_bow:1>\n-# Bow',
    )
    assert [f.name for f in embed.fields] == ['Tournament', 'Time', 'Code', 'Stage']
