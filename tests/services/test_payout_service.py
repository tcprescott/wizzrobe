"""PayoutService — the arithmetic, the gates, and the block that gets pasted."""

from decimal import Decimal

import pytest

from application.errors import FeatureDisabledError
from application.services.payout_service import PayoutService
from models import FeatureFlag, Role, TenantFeatureFlag, Tournament, TournamentPayout, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import join_community, make_user


@pytest.fixture
async def staff(db):
    user = await make_user(discord_id=900, username='staff')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def tournament(db):
    return await Tournament.create(
        name='ALTTPR Main', prize_pool=Decimal('1000.00'), prize_bonus=Decimal('100.00'),
    )


async def _split(tournament, rows):
    for place, percentage, entrant in rows:
        await TournamentPayout.create(
            tournament=tournament, place=place,
            percentage=Decimal(percentage), entrant=entrant,
        )


async def test_half_of_a_pool_plus_bonus(staff, tournament):
    """The sum this feature was specified from: 50% of $1000 + $100 is $550."""
    winner = await make_user(discord_id=1, username='jem')
    await _split(tournament, [(1, '50.00', winner)])

    split = await PayoutService().get_split(tournament.id, staff)

    assert split.total == Decimal('1100.00')
    assert split.lines[0].amount == Decimal('550.00')


async def test_a_tie_pays_both_rows_in_full(staff, tournament):
    third_a = await make_user(discord_id=2, username='specks')
    third_b = await make_user(discord_id=3, username='andy')
    await _split(tournament, [(3, '10.00', third_a), (3, '10.00', third_b)])

    split = await PayoutService().get_split(tournament.id, staff)

    assert [line.amount for line in split.lines] == [Decimal('110.00'), Decimal('110.00')]


async def test_a_drafted_row_still_computes_its_amount(staff, tournament):
    await _split(tournament, [(1, '50.00', None)])

    split = await PayoutService().get_split(tournament.id, staff)

    assert split.lines[0].entrant is None
    assert split.lines[0].amount == Decimal('550.00')
    assert split.is_complete is False


async def test_shares_may_not_exceed_the_pool(staff, tournament):
    with pytest.raises(ValueError, match='more than the pool'):
        await PayoutService().set_split(
            tournament.id,
            [{'place': 1, 'percentage': Decimal('60')},
             {'place': 2, 'percentage': Decimal('50')}],
            staff,
        )


async def test_an_unallocated_remainder_is_allowed(staff, tournament):
    split = await PayoutService().set_split(
        tournament.id,
        [{'place': 1, 'percentage': Decimal('50')},
         {'place': 2, 'percentage': Decimal('30')}],
        staff,
    )

    assert split.allocated_percentage == Decimal('80.00')


async def test_a_place_below_one_is_refused(staff, tournament):
    with pytest.raises(ValueError, match='Place must be'):
        await PayoutService().set_split(
            tournament.id, [{'place': 0, 'percentage': Decimal('10')}], staff,
        )


async def test_a_share_of_zero_is_refused(staff, tournament):
    with pytest.raises(ValueError, match='above 0'):
        await PayoutService().set_split(
            tournament.id, [{'place': 1, 'percentage': Decimal('0')}], staff,
        )


async def test_a_negative_pool_is_refused(staff, tournament):
    with pytest.raises(ValueError, match='cannot be negative'):
        await PayoutService().set_pool(
            tournament.id, Decimal('-5'), None, staff,
        )


async def test_setting_the_pool_moves_every_share(staff, tournament):
    winner = await make_user(discord_id=4, username='jem')
    await _split(tournament, [(1, '50.00', winner)])

    await PayoutService().set_pool(tournament.id, Decimal('2000'), Decimal('0'), staff)
    split = await PayoutService().get_split(tournament.id, staff)

    assert split.lines[0].amount == Decimal('1000.00')


async def test_replacing_a_split_leaves_no_old_rows(staff, tournament):
    await _split(tournament, [(1, '50.00', None), (2, '30.00', None)])

    await PayoutService().set_split(
        tournament.id, [{'place': 1, 'percentage': Decimal('100')}], staff,
    )

    assert await TournamentPayout.filter(tournament=tournament).count() == 1


async def test_naming_a_winner_on_a_drafted_row(staff, tournament):
    await _split(tournament, [(1, '50.00', None)])
    row = await TournamentPayout.get(tournament=tournament)
    winner = await make_user(discord_id=5, username='jem')
    await join_community(winner)

    updated = await PayoutService().set_entrant(row.id, winner.id, staff)

    assert updated.entrant_id == winner.id


async def test_naming_the_same_person_twice_at_one_place_is_refused(staff, tournament):
    winner = await make_user(discord_id=8, username='jem')
    await join_community(winner)
    await _split(tournament, [(3, '10.00', winner), (3, '10.00', None)])
    drafted = await TournamentPayout.get(tournament=tournament, entrant=None)

    with pytest.raises(ValueError, match='already paid at place 3'):
        await PayoutService().set_entrant(drafted.id, winner.id, staff)


async def test_a_tournament_admin_may_edit_their_own_and_not_another(db):
    admin = await make_user(discord_id=6, username='ta')
    mine = await Tournament.create(name='Mine', prize_pool=Decimal('100'))
    theirs = await Tournament.create(name='Theirs', prize_pool=Decimal('100'))
    await mine.admins.add(admin)

    service = PayoutService()
    await service.set_split(mine.id, [{'place': 1, 'percentage': Decimal('100')}], admin)

    with pytest.raises(PermissionError):
        await service.set_split(
            theirs.id, [{'place': 1, 'percentage': Decimal('100')}], admin,
        )


async def test_the_overview_lists_only_what_the_actor_runs(db):
    admin = await make_user(discord_id=7, username='ta')
    mine = await Tournament.create(name='Mine')
    await Tournament.create(name='Theirs')
    await mine.admins.add(admin)

    overviews = await PayoutService().list_overview(admin)

    assert [o.name for o in overviews] == ['Mine']


async def test_the_flag_being_off_hides_the_whole_surface(staff, tournament):
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.PAYOUTS.value,
    ).update(enabled=False)

    with pytest.raises(FeatureDisabledError):
        await PayoutService().get_split(tournament.id, staff)


async def test_the_export_block_reads_as_it_is_pasted(staff, tournament):
    jem = await make_user(discord_id=10, username='jemgold', display_name='Jemgold',
                          matcherino_username='jemgold#100234')
    blueshell = await make_user(discord_id=11, username='blueshell', display_name='blueshell',
                           matcherino_username='blueshell#204871')
    # No handle on purpose: a payout run stalls on exactly this, so the block
    # has to say so rather than leave a blank.
    greenpotion = await make_user(discord_id=12, username='greenpotion', display_name='greenpotion')
    await _split(tournament, [
        (1, '50.00', jem), (2, '30.00', blueshell),
        (3, '10.00', greenpotion), (3, '10.00', None),
    ])

    block = await PayoutService().export_block(tournament.id, staff)

    assert block == (
        'Total prize pool: $1000.00\n'
        'Bonus: $100.00\n'
        '\n'
        '1st place - Jemgold (jemgold#100234): 50% / $550.00\n'
        '2nd place - blueshell (blueshell#204871): 30% / $330.00\n'
        '3rd place - greenpotion (no Matcherino handle): 10% / $110.00\n'
        '3rd place - (unassigned): 10% / $110.00'
    )


async def test_a_split_cannot_name_someone_outside_the_community(staff, tournament):
    """``User`` is global: an unchecked id named anyone on the platform and
    returned their Matcherino handle in the split."""
    stranger = await make_user(discord_id=11, username='elsewhere')

    with pytest.raises(ValueError, match='not found'):
        await PayoutService().set_split(
            tournament.id,
            [{'place': 1, 'percentage': Decimal('50'), 'entrant_id': stranger.id}],
            staff,
        )
    await _split(tournament, [(2, '10.00', None)])
    drafted = await TournamentPayout.get(tournament=tournament, place=2)
    with pytest.raises(ValueError, match='not found'):
        await PayoutService().set_entrant(drafted.id, stranger.id, staff)


async def test_a_fixed_amount_pays_exactly_and_ignores_the_pool(staff, tournament):
    """SGL26 ALTTPR: $650 of $1,490 is 43.6241…%, which two decimals can't hold."""
    await PayoutService().set_pool(tournament.id, Decimal('1490'), None, staff)
    await PayoutService().set_split(
        tournament.id,
        [{'place': 1, 'amount': Decimal('650')},
         {'place': 2, 'amount': Decimal('350')},
         {'place': 3, 'amount': Decimal('145')},
         {'place': 3, 'amount': Decimal('145')},
         {'place': 5, 'percentage': Decimal('10')}],
        staff,
    )
    split = await PayoutService().get_split(tournament.id, staff)

    assert [line.amount for line in split.lines] == [
        Decimal('650.00'), Decimal('350.00'), Decimal('145.00'), Decimal('145.00'), Decimal('149.00'),
    ]
    assert split.lines[0].is_fixed and split.lines[0].percentage is None
    assert split.allocated_amount == Decimal('1439.00')
    assert split.allocated_percentage == Decimal('10.00')

    await PayoutService().set_pool(tournament.id, Decimal('2000'), None, staff)
    split = await PayoutService().get_split(tournament.id, staff)
    assert split.lines[0].amount == Decimal('650.00')
    assert split.lines[4].amount == Decimal('200.00')


async def test_fixed_amounts_may_not_exceed_the_total(staff, tournament):
    with pytest.raises(ValueError, match=r'\$1,150.00, which is more than the \$1,100.00'):
        await PayoutService().set_split(
            tournament.id,
            [{'place': 1, 'amount': Decimal('600')},
             {'place': 2, 'percentage': Decimal('50')}],
            staff,
        )


async def test_fixed_amounts_may_be_drafted_before_a_pool(staff, db):
    unpriced = await Tournament.create(name='No pool yet')

    split = await PayoutService().set_split(
        unpriced.id, [{'place': 1, 'amount': Decimal('650')}], staff,
    )

    assert split.lines[0].amount == Decimal('650.00')


@pytest.mark.parametrize('row', [
    {'place': 1},
    {'place': 1, 'percentage': Decimal('10'), 'amount': Decimal('10')},
])
async def test_a_place_takes_exactly_one_of_percentage_or_amount(staff, tournament, row):
    with pytest.raises(ValueError, match='either a percentage or a dollar amount'):
        await PayoutService().set_split(tournament.id, [row], staff)


async def test_an_amount_of_zero_is_refused(staff, tournament):
    with pytest.raises(ValueError, match=r'above \$0'):
        await PayoutService().set_split(
            tournament.id, [{'place': 1, 'amount': Decimal('0')}], staff,
        )


async def test_the_export_block_shows_a_fixed_line_in_dollars(staff, tournament):
    await PayoutService().set_split(
        tournament.id,
        [{'place': 1, 'amount': Decimal('650')}, {'place': 2, 'percentage': Decimal('10')}],
        staff,
    )

    block = await PayoutService().export_block(tournament.id, staff)

    assert block.splitlines()[3:] == [
        '1st place - (unassigned): $650.00',
        '2nd place - (unassigned): 10% / $110.00',
    ]
