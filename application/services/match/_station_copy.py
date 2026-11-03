"""Naming the match sitting at an occupied station.

Both places a proctor meets a station conflict — the picker option ("Station 4 —
in use (…)") and the refusal ``assign_stations`` raises — used to quote a primary
key. That is the one thing a proctor standing in the venue cannot act on: their
next move is to go and find the people at that station, so the copy says who they
are. The rule is ``application.utils.match_labels``'; this module is the read
that feeds it.

Lives beside :mod:`match_service` rather than inside it because it is the same
kind of thing as ``_dm_context`` — the wording of one interaction, resolved from
the rows a repository can supply. Business-layer, so it may call a repository;
``match_labels`` itself stays pure formatting and cannot.
"""

from typing import Iterable, Mapping

from application.utils.match_labels import match_model_label

UNKNOWN_MATCH = 'another match'


async def name_matches(repository, match_ids: Iterable[int]) -> dict[int, str]:
    """``{match id: human label}`` in one query, whatever the id count."""
    matches = await repository.get_by_ids_with_players(set(match_ids))
    return {m.id: match_model_label(m, with_context=False) for m in matches}


async def label_occupied(repository, occupied: Mapping[str, int]) -> dict[str, str]:
    """Turn ``{station: match id}`` into ``{station: match name}``."""
    if not occupied:
        return {}
    labels = await name_matches(repository, occupied.values())
    return {station: labels.get(mid, UNKNOWN_MATCH) for station, mid in occupied.items()}


async def name_one(repository, match_id: int) -> str:
    """The label for a single match, for the conflict message."""
    return (await name_matches(repository, [match_id])).get(match_id, UNKNOWN_MATCH)
