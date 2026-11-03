"""A small native single-elim bracket, shared by the full-bracket read tests."""

from models import (
    Bracket,
    BracketEntrant,
    BracketEntry,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    BracketState,
    Tournament,
)


async def native_elim(state=BracketState.ACTIVE):
    """Four entrants, single elim: two semis feeding a final; Alice won semi 1."""
    tournament = await Tournament.create(name='Native Cup', is_active=True)
    bracket = await Bracket.create(
        tournament=tournament, name='Main', format=BracketFormat.SINGLE_ELIM, state=state,
    )
    entries = []
    for seed, name in enumerate(('Alice', 'Bob', 'Cara', 'Dan'), start=1):
        entrant = await BracketEntrant.create(tournament=tournament, display_name=name)
        entries.append(await BracketEntry.create(bracket=bracket, entrant=entrant, seed=seed))
    final = await BracketMatch.create(bracket=bracket, round=2, position=1, entry1=entries[0])
    semi1 = await BracketMatch.create(
        bracket=bracket, round=1, position=1, entry1=entries[0], entry2=entries[3],
        winner=entries[0], entry1_score=1, entry2_score=0, state=BracketMatchState.COMPLETE,
        winner_to=final, winner_to_slot=1,
    )
    semi2 = await BracketMatch.create(
        bracket=bracket, round=1, position=2, entry1=entries[1], entry2=entries[2],
        state=BracketMatchState.OPEN, winner_to=final, winner_to_slot=2,
    )
    return tournament, bracket, (semi1, semi2, final), entries
