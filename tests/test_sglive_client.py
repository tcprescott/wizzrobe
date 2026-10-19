"""The sglive.speedgaming.org data-feed parser."""

from decimal import Decimal

import pytest

from application.utils.clients.sglive_client import SgliveAPIError, parse_games

# Trimmed from the live feed at /data/ (2026-10-19).
PAYLOAD = {'tournaments': [
    {'slug': 'alttpr', 'long_name': 'The Legend of Zelda: A Link to the Past Randomizer Tournament',
     'short_name': 'A Link to the Past Randomizer Tournament', 'donations_total': '$1,054.00',
     'rules_url': '', 'registration_url': '', 'bracket_url': '', 'schedule_url': '', 'admins': '',
     'donations': [{'name': 'clearmouse', 'total': '$404.00'}]},
    {'slug': 'dk64', 'long_name': '', 'short_name': 'Donkey Kong 64 Randomizer',
     'donations_total': '$0.00', 'donations': []},
    {'slug': 'mm64r', 'long_name': "The Legend of Zelda: Majora's Mask Randomizer",
     'short_name': "Majora's Mask Randomizer", 'donations_total': '$1,126.50', 'donations': []},
]}


def test_reads_every_game_with_slug_name_and_total():
    games = {g.slug: g for g in parse_games(PAYLOAD)}

    assert list(games) == ['alttpr', 'dk64', 'mm64r']
    assert games['alttpr'].total == Decimal('1054.00')
    assert games['alttpr'].name == 'A Link to the Past Randomizer Tournament'
    assert games['dk64'].total == Decimal('0.00')
    assert games['mm64r'].total == Decimal('1126.50')


@pytest.mark.parametrize('payload', [{}, {'tournaments': []}, [], {'tournaments': 'nope'}])
def test_a_payload_with_no_games_raises_instead_of_returning_nothing(payload):
    with pytest.raises(SgliveAPIError, match='no tournaments'):
        parse_games(payload)


def test_a_game_without_a_total_raises():
    with pytest.raises(SgliveAPIError, match='slug or total'):
        parse_games({'tournaments': [{'slug': 'wwr', 'short_name': 'Wind Waker'}]})


def test_a_mangled_amount_raises():
    payload = {'tournaments': [{'slug': 'wwr', 'short_name': 'WW', 'donations_total': 'TBD'}]}
    with pytest.raises(SgliveAPIError, match='dollar amount'):
        parse_games(payload)
