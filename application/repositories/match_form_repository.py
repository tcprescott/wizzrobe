"""Data access for tournament match forms: the questions and the answers.

The answer reads can name who answered what, which a hidden question must never
show another player. ``MatchFormService`` is the only caller and decides what
each viewer sees.
"""

from typing import Any, Dict, List, Optional

from application.event_writes import guarded_delete
from application.repositories._tenant import current_tenant_id, scoped
from models import BracketEntry, MatchFormAnswer, MatchFormQuestion, MatchPlayers


def _unwrap(stored: Any) -> Any:
    return stored.get('v') if isinstance(stored, dict) else stored


class MatchFormRepository:
    @staticmethod
    async def list_questions(tournament_id: int) -> List[MatchFormQuestion]:
        return await scoped(MatchFormQuestion.filter(tournament_id=tournament_id)).order_by('position', 'id')

    @staticmethod
    async def create_question(tournament_id: int, **fields: Any) -> MatchFormQuestion:
        return await MatchFormQuestion.create(tenant_id=current_tenant_id(), tournament_id=tournament_id, **fields)

    @staticmethod
    async def update_question(question: MatchFormQuestion, **fields: Any) -> None:
        await scoped(MatchFormQuestion.filter(id=question.id)).update(**fields)

    @staticmethod
    async def delete_questions(ids: List[int]) -> None:
        if ids:
            await guarded_delete(scoped(MatchFormQuestion.filter(id__in=ids)))

    @staticmethod
    async def answers_for_match(match_id: int) -> List[MatchFormAnswer]:
        """Each row's ``value`` is the answer itself (the stored wrapper taken off)."""
        rows = await scoped(MatchFormAnswer.filter(match_id=match_id))
        for row in rows:
            row.value = _unwrap(row.value)
        return rows

    @staticmethod
    async def set_answer(match_id: int, user_id: int, question_id: int, value: Any) -> None:
        # Wrapped: Tortoise's JSONField takes a bare str for already-encoded JSON.
        value = {'v': value}
        updated = await scoped(
            MatchFormAnswer.filter(match_id=match_id, user_id=user_id, question_id=question_id)
        ).update(value=value)
        if not updated:
            await MatchFormAnswer.create(
                tenant_id=current_tenant_id(), match_id=match_id, user_id=user_id,
                question_id=question_id, value=value,
            )

    @staticmethod
    async def clear_answer(match_id: int, user_id: int, question_id: int) -> None:
        await guarded_delete(scoped(
            MatchFormAnswer.filter(match_id=match_id, user_id=user_id, question_id=question_id)
        ))

    @staticmethod
    async def players(match_id: int) -> List[MatchPlayers]:
        return await scoped(MatchPlayers.filter(match_id=match_id)).prefetch_related('user')

    @staticmethod
    async def native_seeds(tournament_id: int, user_ids: List[int]) -> Dict[int, int]:
        """Each player's seed in the tournament's native bracket, where they have one."""
        if not user_ids:
            return {}
        rows = await scoped(BracketEntry.filter(
            bracket__tournament_id=tournament_id, entrant__user_id__in=user_ids, seed__isnull=False,
        )).values_list('entrant__user_id', 'seed')
        out: Dict[int, int] = {}
        for uid, seed in rows:
            if uid is not None and (uid not in out or seed < out[uid]):
                out[uid] = seed
        return out
