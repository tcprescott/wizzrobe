import discord
import pytest

from application.services.notification_catalog import CATALOG
from application.utils.discord_messages import DMLink
from application.utils.settings_footnote import SettingsFootnote


def _card() -> discord.Embed:
    card = discord.Embed(title='Seed ready', description='Your seed is ready')
    card.add_field(name='Tournament', value='Proctor Training', inline=False)
    card.add_field(name='Players', value='A vs B', inline=False)
    card.set_footer(text='SpeedGaming Live 2026')
    return card


@pytest.mark.parametrize('key', sorted(CATALOG))
def test_every_notice_adds_exactly_one_footnote_per_recipient(key):
    footnote = SettingsFootnote(CATALOG[key].title, DMLink('Notification settings', 'https://example.test/m'))
    shared = _card()

    sent = [footnote.with_embed(shared) for _ in range(3)]

    for card in sent:
        assert [f.value for f in card.fields].count(footnote.markdown()) == 1
        assert card.fields[-1].value == footnote.markdown()
        assert card.footer.text == 'SpeedGaming Live 2026'
    assert len(shared.fields) == 2
