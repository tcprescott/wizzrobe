"""Bracket types, built from the REST bracket schemas.

``BracketOverviewService`` already flattens native, start.gg and Challonge
brackets into one shape, and ``api/schemas/bracket_overview.py`` documents it
field by field; these types are that schema, so GraphQL and REST can't drift.
"""

from strawberry.experimental import pydantic as strawberry_pydantic

from api.schemas.bracket_overview import (
    BracketBookingResponse,
    BracketEntrantSummary,
    BracketFeedResponse,
    BracketPhaseResponse,
    BracketPoolResponse,
    BracketSetResponse,
    BracketSlotResponse,
    BracketStandingResponse,
    TournamentBracketResponse,
)


@strawberry_pydantic.type(model=BracketSlotResponse, all_fields=True, name='BracketSlot')
class BracketSlot:
    pass


@strawberry_pydantic.type(model=BracketFeedResponse, all_fields=True, name='BracketFeed')
class BracketFeed:
    pass


@strawberry_pydantic.type(model=BracketBookingResponse, all_fields=True, name='BracketBooking')
class BracketBooking:
    pass


@strawberry_pydantic.type(model=BracketSetResponse, all_fields=True, name='BracketSet')
class BracketSet:
    pass


@strawberry_pydantic.type(model=BracketStandingResponse, all_fields=True, name='BracketStanding')
class BracketStanding:
    pass


@strawberry_pydantic.type(model=BracketPoolResponse, all_fields=True, name='BracketPool')
class BracketPool:
    pass


@strawberry_pydantic.type(model=BracketPhaseResponse, all_fields=True, name='BracketPhase')
class BracketPhase:
    pass


@strawberry_pydantic.type(model=BracketEntrantSummary, all_fields=True, name='BracketEntrant')
class BracketEntrant:
    pass


@strawberry_pydantic.type(model=TournamentBracketResponse, all_fields=True, name='TournamentBracket')
class TournamentBracket:
    pass
