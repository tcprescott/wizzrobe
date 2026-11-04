"""A tournament's seed rules: which preset each match rolls.

The rules are tried in order at roll time and the first that fits decides;
when none fits the tournament's own preset rolls. Staff's per-match override
still comes first. Hard mode is one of the rules (it reads the match form).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.seed_rules import (
    SeedRule,
    SeedRulesEditorResponse,
    SeedRulesParsed,
    SeedRulesPreviewRequest,
    SeedRulesPreviewResponse,
    SeedRulesText,
    SeedRulesUpdate,
)
from application.services import SeedRuleService
from application.services.seed_rules import MatchFacts
from models import Tournament, User

router = APIRouter(
    prefix='/admin/tournaments',
    tags=['Admin: seed rules'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(tournament_id=Tournament))],
)

_WHO = "**Who can call this:** Staff, a preset manager, or an admin of this tournament."


def _plain(rules: Optional[List[SeedRule]]) -> Optional[list]:
    if rules is None:
        return None
    return [r.model_dump(mode='json', by_alias=True, exclude_none=True) for r in rules]


@router.get(
    '/{tournament_id}/seed-rules', response_model=SeedRulesEditorResponse, summary="A tournament's seed rules",
    description=(
        "The rules in order, the presets a rule may name (this tournament's randomizer only), the "
        "round keys its bracket offers and unseeded matches to try in the preview.\n\n" + _WHO
    ),
)
async def get_seed_rules(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await SeedRuleService().editor(tournament_id, actor)


@router.put(
    '/{tournament_id}/seed-rules', response_model=SeedRulesEditorResponse, summary='Replace the seed rules',
    description=(
        "Saves the whole list; an empty list goes back to rolling the tournament's preset. A bad rule "
        "fails with 400 naming it (`Rule 2: …`). Seeds already rolled keep what they rolled.\n\n" + _WHO
    ),
)
async def save_seed_rules(tournament_id: int, body: SeedRulesUpdate, actor: User = Depends(require_write_actor)):
    service = SeedRuleService()
    await service.save_rules(tournament_id, _plain(body.rules), actor)
    return await service.editor(tournament_id, actor)


@router.post(
    '/{tournament_id}/seed-rules/preview', response_model=SeedRulesPreviewResponse,
    summary='Try the seed rules on a match',
    description=(
        "Which rule a real or made-up match would fire and what it would roll, without rolling. Pass "
        "`rules` to try an unsaved draft. Staff's override isn't consulted.\n\n" + _WHO
    ),
)
async def preview_seed_rules(
    tournament_id: int, body: SeedRulesPreviewRequest, actor: User = Depends(require_api_actor),
):
    facts = MatchFacts(**body.facts.model_dump()) if body.facts is not None else None
    return await SeedRuleService().preview(
        tournament_id, actor, rules=_plain(body.rules), match_id=body.match_id, facts=facts,
    )


@router.get(
    '/{tournament_id}/seed-rules/text', response_model=SeedRulesText, summary='The seed rules as text',
    description="The saved rules as YAML with presets by name, to copy into another tournament.\n\n" + _WHO,
)
async def export_seed_rules(tournament_id: int, actor: User = Depends(require_api_actor)):
    return {'text': await SeedRuleService().export_text(tournament_id, actor)}


@router.post(
    '/{tournament_id}/seed-rules/text', response_model=SeedRulesParsed, summary='Read seed rules from text',
    description=(
        "Checks YAML rules and returns them for the editor, without saving; save with `PUT`. Presets "
        "are matched by name. Bad YAML fails with 400 naming the line.\n\n" + _WHO
    ),
)
async def import_seed_rules(tournament_id: int, body: SeedRulesText, actor: User = Depends(require_api_actor)):
    return {'rules': await SeedRuleService().parse_text(tournament_id, body.text, actor)}
