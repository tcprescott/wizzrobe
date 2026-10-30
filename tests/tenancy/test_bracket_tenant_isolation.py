"""Cross-tenant isolation (leak) tests for the native bracket models.

All four models — Bracket, BracketEntrant, BracketEntry, BracketMatch — carry a
``tenant`` FK, so a tenant-scoped read must return only that tenant's rows. B0
has no repository yet (that arrives in B6); these tests exercise the same
``scoped()`` helper the repositories will use, which is what enforces the
contract in production.
"""

import pytest

from application.repositories._tenant import scoped
from application.tenant_context import tenant_scope
from models import (
    Bracket,
    BracketEntrant,
    BracketEntry,
    BracketFormat,
    BracketMatch,
    BracketMatchGame,
    Tournament,
)


async def _tournament(name: str) -> Tournament:
    return await Tournament.create(name=name)


async def test_bracket_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await _tournament('TA')
        ba = await Bracket.create(tournament=ta, name='Main', format=BracketFormat.SINGLE_ELIM)
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.SINGLE_ELIM)

    with tenant_scope(a.id):
        assert [x.id for x in await scoped(Bracket.all())] == [ba.id]
        assert await scoped(Bracket.filter(id=bb.id)).first() is None
    with tenant_scope(b.id):
        assert [x.id for x in await scoped(Bracket.all())] == [bb.id]
        assert await scoped(Bracket.filter(id=ba.id)).first() is None


async def test_bracket_entrant_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await _tournament('TA')
        ea = await BracketEntrant.create(tournament=ta, display_name='Alice')
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        eb = await BracketEntrant.create(tournament=tb, display_name='Alice')

    with tenant_scope(a.id):
        assert [x.id for x in await scoped(BracketEntrant.all())] == [ea.id]
        assert await scoped(BracketEntrant.filter(id=eb.id)).first() is None
    with tenant_scope(b.id):
        assert [x.id for x in await scoped(BracketEntrant.all())] == [eb.id]
        assert await scoped(BracketEntrant.filter(id=ea.id)).first() is None


async def test_bracket_entry_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await _tournament('TA')
        ba = await Bracket.create(tournament=ta, name='Main', format=BracketFormat.SWISS)
        enta = await BracketEntrant.create(tournament=ta, display_name='Alice')
        rowa = await BracketEntry.create(bracket=ba, entrant=enta, seed=1)
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.SWISS)
        entb = await BracketEntrant.create(tournament=tb, display_name='Bob')
        rowb = await BracketEntry.create(bracket=bb, entrant=entb, seed=1)

    with tenant_scope(a.id):
        assert [x.id for x in await scoped(BracketEntry.all())] == [rowa.id]
        assert await scoped(BracketEntry.filter(id=rowb.id)).first() is None
    with tenant_scope(b.id):
        assert [x.id for x in await scoped(BracketEntry.all())] == [rowb.id]
        assert await scoped(BracketEntry.filter(id=rowa.id)).first() is None


async def test_bracket_match_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await _tournament('TA')
        ba = await Bracket.create(tournament=ta, name='Main', format=BracketFormat.DOUBLE_ELIM)
        ma = await BracketMatch.create(bracket=ba, round=1, position=1)
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.DOUBLE_ELIM)
        mb = await BracketMatch.create(bracket=bb, round=1, position=1)

    with tenant_scope(a.id):
        assert [x.id for x in await scoped(BracketMatch.all())] == [ma.id]
        assert await scoped(BracketMatch.filter(id=mb.id)).first() is None
    with tenant_scope(b.id):
        assert [x.id for x in await scoped(BracketMatch.all())] == [mb.id]
        assert await scoped(BracketMatch.filter(id=ma.id)).first() is None


async def test_bracket_match_game_reads_are_isolated(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await _tournament('TA')
        ba = await Bracket.create(tournament=ta, name='Main', format=BracketFormat.SINGLE_ELIM)
        ma = await BracketMatch.create(bracket=ba, round=1, position=1)
        ga = await BracketMatchGame.create(bracket_match=ma, game_number=1)
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.SINGLE_ELIM)
        mb = await BracketMatch.create(bracket=bb, round=1, position=1)
        gb = await BracketMatchGame.create(bracket_match=mb, game_number=1)

    with tenant_scope(a.id):
        assert [x.id for x in await scoped(BracketMatchGame.all())] == [ga.id]
        assert await scoped(BracketMatchGame.filter(id=gb.id)).first() is None
    with tenant_scope(b.id):
        assert [x.id for x in await scoped(BracketMatchGame.all())] == [gb.id]
        assert await scoped(BracketMatchGame.filter(id=ga.id)).first() is None


async def test_linking_cannot_reach_across_tenants(two_tenants):
    """A matchup id from another tenant must not resolve, let alone link.

    ``link_match_to_bracket_match`` loads the matchup through the repository's
    scoped read, so the cross-tenant id 404s before any authorization or
    player-set check runs.
    """
    from application.errors import NotFoundError
    from application.services.bracket_service import BracketService
    from models import Match, MatchPlayers, Role, User, UserRole

    a, b = two_tenants
    service = BracketService()

    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.SINGLE_ELIM)
        foreign_match = await BracketMatch.create(bracket=bb, round=1, position=1)

    with tenant_scope(a.id):
        staff = await User.create(discord_id=8801, username='staff-a')
        await UserRole.create(user=staff, role=Role.STAFF)
        ta = await _tournament('TA')
        player = await User.create(discord_id=8802, username='p-a')
        local_match = await Match.create(tournament=ta)
        await MatchPlayers.create(match=local_match, user=player)

        with pytest.raises(NotFoundError):
            await service.link_match_to_bracket_match(
                staff, foreign_match.id, local_match.id,
            )

    with tenant_scope(b.id):
        assert not await scoped(
            BracketMatchGame.filter(bracket_match_id=foreign_match.id)
        ).exists()


async def test_unlink_cannot_reach_across_tenants(two_tenants):
    """The game lookup is scoped, so another tenant's link is invisible."""
    from application.services.bracket_service import BracketService
    from models import Match, Role, User, UserRole

    a, b = two_tenants
    service = BracketService()

    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.SINGLE_ELIM)
        mb = await BracketMatch.create(bracket=bb, round=1, position=1)
        foreign_scheduled = await Match.create(tournament=tb)
        game = await BracketMatchGame.create(
            bracket_match=mb, game_number=1, match=foreign_scheduled,
        )

    with tenant_scope(a.id):
        staff = await User.create(discord_id=8811, username='staff-a')
        await UserRole.create(user=staff, role=Role.STAFF)
        assert await service.unlink_match(staff, foreign_scheduled.id) is False

    with tenant_scope(b.id):
        assert await scoped(BracketMatchGame.filter(id=game.id)).exists()


async def test_browse_read_is_isolated(two_tenants):
    """``list_all_with_tournament`` spans tournaments, so it is the widest read.

    It backs an anonymous-readable page, which makes a leak here public.
    """
    from application.repositories.bracket_repository import BracketRepository

    a, b = two_tenants
    repo = BracketRepository()

    with tenant_scope(a.id):
        ta = await _tournament('TA')
        ba = await Bracket.create(tournament=ta, name='Main', format=BracketFormat.SWISS)
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(tournament=tb, name='Main', format=BracketFormat.SWISS)

    with tenant_scope(a.id):
        assert [x.id for x in await repo.list_all_with_tournament()] == [ba.id]
    with tenant_scope(b.id):
        assert [x.id for x in await repo.list_all_with_tournament()] == [bb.id]


async def test_batched_room_lookup_is_isolated(two_tenants):
    """The bracket view's racetime-room read (U2) is batched — and public.

    ``for_matches`` takes a list of match ids, so an unscoped version would
    happily return another community's room for an id it was handed. That URL is
    then painted on an anonymous-readable page as a "Watch" link.
    """
    from application.repositories.racetime_room_repository import RacetimeRoomRepository
    from models import Match, RaceRoomStatus, RacetimeBot, RacetimeRoom

    a, b = two_tenants
    repo = RacetimeRoomRepository()

    bot = await RacetimeBot.create(
        category='alttpr', client_id='c', client_secret='s', name='bot',
    )
    with tenant_scope(a.id):
        ta = await _tournament('TA')
        match_a = await Match.create(tournament=ta)
        room_a = await RacetimeRoom.create(
            bot=bot, slug='alttpr/room-a', category='alttpr',
            status=RaceRoomStatus.OPEN, match=match_a,
        )
    with tenant_scope(b.id):
        tb = await _tournament('TB')
        match_b = await Match.create(tournament=tb)
        await RacetimeRoom.create(
            bot=bot, slug='alttpr/room-b', category='alttpr',
            status=RaceRoomStatus.OPEN, match=match_b,
        )

    with tenant_scope(a.id):
        found = await repo.for_matches([match_a.id, match_b.id])
        assert list(found) == [match_a.id]
        assert found[match_a.id].id == room_a.id


async def test_release_does_not_reach_another_tenants_game(two_tenants):
    """The cancel/delete slot release (U3a) must not free a foreign slot.

    Same shape as the ``unlink_match`` leak test above: the release resolves the
    game from a ``Match`` id, so a scoping gap would let one community's
    cancellation delete another's booked game.
    """
    from application.services.bracket_service import BracketService
    from models import Match, Role, User, UserRole

    a, b = two_tenants
    service = BracketService()

    with tenant_scope(b.id):
        tb = await _tournament('TB')
        bb = await Bracket.create(
            tournament=tb, name='Main', format=BracketFormat.SINGLE_ELIM,
        )
        mb = await BracketMatch.create(bracket=bb, round=1, position=1)
        foreign_scheduled = await Match.create(tournament=tb)
        game = await BracketMatchGame.create(
            bracket_match=mb, game_number=1, match=foreign_scheduled,
        )

    with tenant_scope(a.id):
        staff = await User.create(discord_id=8812, username='staff-a2')
        await UserRole.create(user=staff, role=Role.STAFF)
        assert await service.release_game_if_linked(
            foreign_scheduled, staff, reason='cross-tenant',
        ) is False

    with tenant_scope(b.id):
        assert await scoped(BracketMatchGame.filter(id=game.id)).exists()
