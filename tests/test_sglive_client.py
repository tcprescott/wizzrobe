"""The sglive.speedgaming.org leaderboard parser."""

from decimal import Decimal

import pytest

from application.utils.clients.sglive_client import SgliveAPIError, parse_leaderboard

# Trimmed from the live page (2026-10-19): one online and one on-site block.
PAGE = '''<!DOCTYPE html><html><body><main><div class="leaderboard-container">
<div class="leaderboard"><img src="/static/prizepool/images/online-header.png">
<table class="sub-leaderboard tournament-top3"><tr><th colspan=3><img src="/x.png"></th></tr>
<tr><td>#1</td><td><a href="/z2r/" class="leaderboard-link">Zelda II Randomizer</a></td><td>$1,100.00</td></tr>
</table><table class="sub-leaderboard tournament-sub20"><tr><th colspan=3></th></tr>
<tr><td></td><td><a href="/mm7/" class="leaderboard-link">Mega Man 7 any%</a></td><td>$0.00</td></tr></table></div>
<div class="leaderboard"><img src="/static/prizepool/images/onsite-header.png">
<table class="sub-leaderboard tournament-top3">
<tr><td>#1</td><td><a href="/wwr/" class="leaderboard-link">Wind Waker Randomizer</a></td><td>$1,701.00</td></tr>
<tr><td>#3</td><td><a href="/mm64r/" class="leaderboard-link">Majora&#x27;s Mask Randomizer</a></td><td>$1,126.50</td></tr>
</table></div></div>
<div class="news-posts"><ul><li><a href="/news/18/">SGL Leadership Meeting</a></li></ul></div>
</main></body></html>'''


def test_reads_every_row_with_slug_name_total_and_side():
    games = {g.slug: g for g in parse_leaderboard(PAGE)}

    assert list(games) == ['z2r', 'mm7', 'wwr', 'mm64r']
    assert games['wwr'].total == Decimal('1701.00')
    assert games['wwr'].onsite is True
    assert games['z2r'].onsite is False
    assert games['mm7'].total == Decimal('0.00')
    assert games['mm64r'].name == "Majora's Mask Randomizer"
    assert games['mm64r'].total == Decimal('1126.50')


def test_a_page_with_no_rows_raises_instead_of_returning_nothing():
    with pytest.raises(SgliveAPIError, match='layout may have changed'):
        parse_leaderboard('<html><body><h1>Maintenance</h1></body></html>')


def test_a_mangled_amount_raises():
    page = PAGE.replace('$1,701.00', 'TBD')
    with pytest.raises(SgliveAPIError, match='dollar amount'):
        parse_leaderboard(page)
