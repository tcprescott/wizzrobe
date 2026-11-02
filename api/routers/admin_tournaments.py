"""The app's Admin → Tournaments screen: the board, players, dropping a player,
bracket entrants, sync/link/stage steps and the round schedule.

Creating, editing and deleting a tournament stay on ``/tournaments``; these are
the reads and actions the board needs on top.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.admin_tournaments import (
    BracketSyncResultResponse,
    ConfirmedResponse,
    DeletionPreviewResponse,
    DropPlanResponse,
    DropRequest,
    DropResultResponse,
    EntrantAssignRequest,
    EntrantListResponse,
    LinkRequest,
    PersonResponse,
    PlayerResponse,
    RoundScheduleUpdate,
    SiteLabelResponse,
    StageActionRequest,
    StartggSettingsResponse,
    StartggSettingsUpdate,
    TournamentBoardResponse,
    TournamentBoardRowResponse,
)
from application.services import AdminTournamentsService
from models import Tournament, User

router = APIRouter(
    prefix='/admin/tournaments',
    tags=['Admin: tournaments'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(tournament_id=Tournament))],
)

_WHO = "**Who can call this:** Staff, or an admin of this tournament."
_STAFF = "**Who can call this:** Staff."


@router.get(
    '/board', response_model=TournamentBoardResponse, summary='The Tournaments board',
    description=(
        "Every tournament in this event the caller runs, by name, with signups, bracket site, seed, "
        "match length and player count, plus which actions each row offers. Staff see all of them; "
        "a tournament admin sees their own.\n\n" + _WHO.replace('of this tournament', 'of any tournament here')
    ),
)
async def board(actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().board(actor)


@router.post(
    '/sync', response_model=List[BracketSyncResultResponse], summary='Sync every linked bracket',
    description=(
        "Pulls every linked tournament on the board from its bracket site. One that fails is "
        "reported in its own `error` and doesn't stop the rest.\n\n"
        + _WHO.replace('of this tournament', 'of any tournament here')
    ),
)
async def sync_all(actor: User = Depends(require_write_actor)):
    return await AdminTournamentsService().sync_all(actor)


@router.get('/{tournament_id}', response_model=TournamentBoardRowResponse, summary="One tournament's board row",
            description="The same row the board shows, after a change.\n\n" + _WHO)
async def row(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().row(tournament_id, actor)


@router.get(
    '/{tournament_id}/deletion-preview', response_model=DeletionPreviewResponse,
    summary='What deleting a tournament takes with it',
    description="How many matches, players, brackets and triforce texts a permanent delete removes.\n\n" + _STAFF,
)
async def deletion_preview(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().deletion_preview(tournament_id, actor)


@router.get('/{tournament_id}/players', response_model=List[PlayerResponse], summary="A tournament's players",
            description="Everyone enrolled, by name, with when they joined.\n\n" + _WHO)
async def players(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().players(tournament_id, actor)


@router.get(
    '/{tournament_id}/people', response_model=List[PersonResponse], summary='Members to pick from',
    description="The community's active members, for the enrol and match-entrant pickers.\n\n" + _WHO,
)
async def people(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().people(tournament_id, actor)


@router.get(
    '/{tournament_id}/players/{user_id}/drop-plan', response_model=DropPlanResponse,
    summary='What dropping a player would do',
    description="The unplayed matches a drop settles, and whether they come off the Challonge bracket too.\n\n" + _WHO,
)
async def drop_plan(tournament_id: int, user_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().drop_plan(tournament_id, user_id, actor)


@router.post(
    '/{tournament_id}/players/{user_id}/drop', response_model=DropResultResponse, summary='Drop a player',
    description=(
        "Takes the player off the roster and the Challonge bracket, forfeits or calls off their "
        "head-to-head matches, takes them off larger matches, and DMs them once.\n\n" + _WHO
    ),
)
async def drop(tournament_id: int, user_id: int, body: DropRequest, actor: User = Depends(require_write_actor)):
    return await AdminTournamentsService().drop(
        tournament_id, user_id, actor, reason=body.reason, forfeit_matches=body.forfeit_matches,
    )


@router.post('/{tournament_id}/bracket/sync', response_model=BracketSyncResultResponse, summary='Sync the bracket now',
             description="Pulls the latest entrants and matches from the linked site.\n\n" + _WHO)
async def sync(tournament_id: int, actor: User = Depends(require_write_actor)):
    return await AdminTournamentsService().sync(tournament_id, actor)


@router.put(
    '/{tournament_id}/bracket', response_model=SiteLabelResponse, summary='Link a bracket',
    description=(
        "Links the tournament to a Challonge or start.gg bracket by its link or id and mirrors it. "
        "Fails with 400 when the link is for the other site or the site isn't on here.\n\n" + _STAFF
    ),
)
async def link(tournament_id: int, body: LinkRequest, actor: User = Depends(require_write_actor)):
    await AdminTournamentsService().link(tournament_id, body.site, body.bracket, actor)
    return {'site': body.site}


@router.delete(
    '/{tournament_id}/bracket', status_code=status.HTTP_204_NO_CONTENT, summary='Unlink the bracket',
    description=(
        "Detaches the tournament from its bracket site and drops the mirror here. Nothing on the "
        "site changes and booked matches stay.\n\n" + _STAFF
    ),
)
async def unlink(tournament_id: int, actor: User = Depends(require_write_actor)):
    await AdminTournamentsService().unlink(tournament_id, actor)


@router.post(
    '/{tournament_id}/bracket/stage', response_model=TournamentBoardRowResponse, summary='Run the next Challonge stage step',
    description=(
        "Starts the group stage, the final stage, or finalizes, on Challonge, then re-syncs. Needs the "
        "Challonge connection's stage scope. Fails with 400 when that isn't the next step any more.\n\n" + _WHO
    ),
)
async def stage(tournament_id: int, body: StageActionRequest, actor: User = Depends(require_write_actor)):
    return await AdminTournamentsService().advance_stage(tournament_id, body.action, actor)


@router.get('/{tournament_id}/bracket/entrants', response_model=EntrantListResponse,
            summary="The bracket's entrants and who they are",
            description="Each entrant on the linked site and the member it maps to.\n\n" + _WHO)
async def entrants(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().entrants(tournament_id, actor)


@router.put(
    '/{tournament_id}/bracket/entrants/{entrant_id}', status_code=status.HTTP_204_NO_CONTENT,
    summary='Match an entrant to a member',
    description="Sync keeps a hand match. Also confirms a start.gg suggestion.\n\n" + _WHO,
)
async def assign_entrant(
    tournament_id: int, entrant_id: int, body: EntrantAssignRequest, actor: User = Depends(require_write_actor),
):
    await AdminTournamentsService().assign_entrant(tournament_id, entrant_id, body.user_id, actor)


@router.delete(
    '/{tournament_id}/bracket/entrants/{entrant_id}', status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear an entrant's hand match",
    description="The entrant falls back to their own linked account, if any.\n\n" + _WHO,
)
async def clear_entrant(tournament_id: int, entrant_id: int, actor: User = Depends(require_write_actor)):
    await AdminTournamentsService().clear_entrant(tournament_id, entrant_id, actor)


@router.post(
    '/{tournament_id}/bracket/entrants/{entrant_id}/dismiss', status_code=status.HTTP_204_NO_CONTENT,
    summary='Dismiss a start.gg suggestion', description="404 unless the bracket is on start.gg.\n\n" + _WHO,
)
async def dismiss_suggestion(tournament_id: int, entrant_id: int, actor: User = Depends(require_write_actor)):
    await AdminTournamentsService().dismiss_suggestion(tournament_id, entrant_id, actor)


@router.post(
    '/{tournament_id}/bracket/entrants/confirm-suggestions', response_model=ConfirmedResponse,
    summary='Confirm every start.gg suggestion', description="404 unless the bracket is on start.gg.\n\n" + _WHO,
)
async def confirm_suggestions(tournament_id: int, actor: User = Depends(require_write_actor)):
    return {'confirmed': await AdminTournamentsService().confirm_all_suggestions(tournament_id, actor)}


@router.get(
    '/{tournament_id}/bracket/startgg', response_model=StartggSettingsResponse,
    summary="A start.gg bracket's settings", description="404 unless the bracket is on start.gg.\n\n" + _WHO,
)
async def startgg_settings(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminTournamentsService().startgg_settings(tournament_id, actor)


@router.patch(
    '/{tournament_id}/bracket/startgg', response_model=StartggSettingsResponse,
    summary="Change a start.gg bracket's settings",
    description="Which phase players book from, and whether results go back to start.gg.\n\n" + _WHO,
)
async def update_startgg_settings(
    tournament_id: int, body: StartggSettingsUpdate, actor: User = Depends(require_write_actor),
):
    sent = body.model_fields_set
    return await AdminTournamentsService().update_startgg_settings(
        tournament_id, actor,
        bookable_phase_id=body.bookable_phase_id, set_phase='bookable_phase_id' in sent,
        report_results=body.report_results if 'report_results' in sent else None,
    )


@router.put(
    '/{tournament_id}/round-schedule', status_code=status.HTTP_204_NO_CONTENT,
    summary='Replace the round schedule',
    description=(
        "Sets every stage's booking window and games at once; read it back from "
        "`GET /tournaments/{tournament_id}/round-schedule`. Daily hours need both ends.\n\n" + _WHO
    ),
)
async def save_round_schedule(
    tournament_id: int, body: RoundScheduleUpdate, actor: User = Depends(require_write_actor),
):
    entries = {
        key: {'opens': e.opens_at, 'closes': e.closes_at, 'daily_from': e.daily_from,
              'daily_until': e.daily_until, 'best_of': e.best_of}
        for key, e in body.stages.items()
    }
    await AdminTournamentsService().save_round_schedule(tournament_id, entries, actor)
