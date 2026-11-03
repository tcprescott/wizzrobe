"""How seeds get into a pool — the audit's F12.

Rolling was already right. What was not: every entry path took whatever was pasted,
so `not-a-url-at-all`, `ftp://weird/x` and `javascript:alert(1)` all became
permalinks and all rendered as clickable links. The everyday cost is the reason
this matters more than the exotic one — **reveal is start**, so a runner drawn a
typo'd permalink has already spent their slot on a seed that will not open.

Also here: the refusal the Roll button now reads before it is offered, which used
to arrive only after a click, in a dialog that could not change the preset.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_pools import roll_refusal
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import AsyncQualifierPermalink, Preset, Role, User, UserRole

pytestmark = pytest.mark.anyio


async def _staff(discord_id: int = 900601, name: str = 'poolstaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _pool(service, staff, *, preset_id=None):
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Entry Q', opens_at=now - timedelta(days=1),
        closes_at=now + timedelta(days=1), runs_per_pool=1,
    )
    return await service.create_pool(staff, q.id, name='Pool A', preset_id=preset_id)


@pytest.mark.parametrize('url', [
    'https://alttpr.com/en/h/AbCdEf1234',
    'http://localhost:8000/seed/1',
    'https://example.test/x?with=query#frag',
])
def test_a_real_link_is_accepted(url):
    assert rules.permalink_url_error(url) is None
    assert rules.validate_permalink_url(f'  {url}  ') == url


@pytest.mark.parametrize('url, expected', [
    ('', 'required'),
    ('   ', 'required'),
    ('not-a-url-at-all', 'http://'),
    ('alttpr.com/en/h/AbCdEf', 'http://'),
    ('ftp://weird/x', 'http://'),
    ('javascript:alert(1)', 'http://'),
    ('data:text/html,<b>x', 'http://'),
    ('https://', 'no host'),
])
def test_what_cannot_be_a_permalink(url, expected):
    error = rules.permalink_url_error(url)
    assert error is not None
    assert expected in error


def test_the_refusal_quotes_the_line_but_not_all_of_it():
    error = rules.permalink_url_error('x' * 200)
    assert error is not None
    assert len(error) < 120
    assert '…' in error


async def test_add_permalink_refuses_a_non_url(db):
    service = AsyncQualifierService()
    staff = await _staff()
    pool = await _pool(service, staff)

    with pytest.raises(ValueError, match='http://'):
        await service.add_permalink(staff, pool.id, url='alttpr.com/en/h/no-scheme')
    assert await AsyncQualifierPermalink.filter(pool_id=pool.id).count() == 0


async def test_update_permalink_refuses_a_non_url_and_keeps_the_old_one(db):
    service = AsyncQualifierService()
    staff = await _staff(900602, 'poolstaff2')
    pool = await _pool(service, staff)
    pl = await service.add_permalink(staff, pool.id, url='https://example.test/good')

    with pytest.raises(ValueError, match='http://'):
        await service.update_permalink(staff, pl.id, url='javascript:alert(1)')
    assert (await AsyncQualifierPermalink.get(id=pl.id)).url == 'https://example.test/good'


async def test_bulk_takes_the_good_lines_and_numbers_the_rest(db):
    """A mangled line must not cost the paste, and must not go unmentioned."""
    service = AsyncQualifierService()
    staff = await _staff(900603, 'poolstaff3')
    pool = await _pool(service, staff)

    result = await service.add_permalinks_bulk(staff, pool.id, urls=[
        'https://example.test/a',
        '',
        'ftp://weird/x',
        '  https://example.test/b  ',
        'javascript:alert(1)',
    ])
    assert [p.url for p in result.created] == [
        'https://example.test/a', 'https://example.test/b',
    ]
    assert [n for n, _, _ in result.rejected] == [3, 5]
    assert 'line 3' in result.summary and 'line 5' in result.summary
    assert 'Added 2' in result.summary


async def test_bulk_that_takes_everything_says_only_that(db):
    service = AsyncQualifierService()
    staff = await _staff(900604, 'poolstaff4')
    pool = await _pool(service, staff)

    result = await service.add_permalinks_bulk(
        staff, pool.id, urls=['https://example.test/a', 'https://example.test/b'])
    assert result.rejected == []
    assert result.summary == 'Added 2 permalink(s)'


async def test_bulk_where_every_line_is_bad_creates_nothing(db):
    service = AsyncQualifierService()
    staff = await _staff(900605, 'poolstaff5')
    pool = await _pool(service, staff)

    result = await service.add_permalinks_bulk(staff, pool.id, urls=['a', 'b'])
    assert result.created == []
    assert len(result.rejected) == 2
    assert await AsyncQualifierPermalink.filter(pool_id=pool.id).count() == 0


async def test_roll_refusal_reads_the_same_answer_the_service_enforces(db):
    """The page asks this before offering the button; the service asks it again."""
    service = AsyncQualifierService()
    staff = await _staff(900606, 'poolstaff6')
    async_preset = await Preset.create(
        name='DK', randomizer='dk64r', settings={'settings_string': 'x'})
    ok_preset = await Preset.create(
        name='ALTTPR', randomizer='alttpr', settings={'preset': 'open'})

    assert roll_refusal(None) == 'Pool has no preset to roll from'
    assert roll_refusal(ok_preset) is None
    refusal = roll_refusal(async_preset)
    assert refusal is not None and 'dk64r' in refusal

    pool = await _pool(service, staff, preset_id=async_preset.id)
    with pytest.raises(ValueError, match='dk64r'):
        await service.roll_permalinks(staff, pool.id, count=1)


async def test_rolling_more_than_the_batch_cap_is_refused(db):
    service = AsyncQualifierService()
    staff = await _staff(900607, 'poolstaff7')
    preset = await Preset.create(
        name='ALTTPR2', randomizer='alttpr', settings={'preset': 'open'})
    pool = await _pool(service, staff, preset_id=preset.id)

    with pytest.raises(ValueError, match='between 1 and 25'):
        await service.roll_permalinks(staff, pool.id, count=40)
    assert await AsyncQualifierPermalink.filter(pool_id=pool.id).count() == 0
