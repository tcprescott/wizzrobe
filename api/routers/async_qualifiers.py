"""Async-qualifier endpoints — the self-paced permalink-pool qualifier aggregate.

Mixed auth: admin reads/writes gate ``can_admin_qualifier`` in the service, player
run methods enforce ownership on the resolved actor, and the public shell / open
list are ungated by design (a valid token is still required). Every read uses the
``require_api_actor`` (A) dependency and every write ``require_write_actor`` (W).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api._helpers import load_user_or_404
from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.async_qualifiers import (
    AdminRequest,
    AsyncQualifierPermalinkResponse,
    AsyncQualifierPoolResponse,
    AsyncQualifierPublicResponse,
    AsyncQualifierResponse,
    AsyncQualifierReviewNoteResponse,
    AsyncQualifierRunPage,
    AsyncQualifierRunResponse,
    LeaderboardEntryResponse,
    MyQualifierRunResponse,
    PermalinkBulkRequest,
    PermalinkBulkResponse,
    PermalinkCreateRequest,
    PermalinkRollRequest,
    PermalinkUpdateRequest,
    PoolCreateRequest,
    PoolUpdateRequest,
    QualifierCreateRequest,
    QualifierUpdateRequest,
    ReattemptRequest,
    RejectedPermalinkLine,
    ReviewRequest,
    StartRunRequest,
    SubmitRunRequest,
)
from api.schemas.common import UserBase
from api.schemas.detail import DetailResponse
from application.errors import require_found
from application.services import AsyncQualifierService
from application.tenant_context import require_tenant_id
from models import AsyncQualifier, AsyncQualifierPermalink, AsyncQualifierPool, AsyncQualifierRun, User

router = APIRouter(prefix="/async-qualifiers", tags=["Async qualifiers"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(qualifier_id=AsyncQualifier, run_id=AsyncQualifierRun, pool_id=AsyncQualifierPool, permalink_id=AsyncQualifierPermalink))])

# One page of runs, and the most a caller may ask for at once. A 500-player
# qualifier holds a few thousand runs, so the unbounded read this replaces was a
# 1.66 MB body every time.
RUNS_PAGE_DEFAULT = 100
RUNS_PAGE_MAX = 500


async def _load_qualifier_or_404(qualifier_id: int) -> AsyncQualifier:
    return require_found(
        await AsyncQualifier.get_or_none(id=qualifier_id, tenant_id=require_tenant_id()),
        "Qualifier",
    )


# ============================================================ reads (A)

@router.get(
    "", response_model=List[AsyncQualifierResponse], summary="List qualifiers (admin)",
    description=(
        "The qualifiers you administer, newest first, including each one's internal "
        '`config`. Staff and Qualifier admins see every qualifier in the community; a '
        'qualifier admin sees only the ones that list them. Anyone who administers none '
        'is refused.\n\n**Who can call this:** Staff, Qualifier admin, or admins of at '
        'least one qualifier.'
    ),
)
async def list_qualifiers(actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().list_qualifiers(actor)


@router.get(
    "/open", response_model=List[AsyncQualifierPublicResponse], summary="List open (active) qualifiers",
    description=(
        'Every qualifier switched on (`is_active`), newest first, as the public shell: '
        'name, window and run rules, without the internal `config`. A qualifier appears '
        'here even before it opens or after it closes, as long as it is still switched '
        'on.\n\n**Who can call this:** Any member.'
    ),
)
async def list_open_qualifiers(actor: User = Depends(require_api_actor)):
    # Ungated player-facing list: the public shell only (no internal ``config``).
    return await AsyncQualifierService().list_open_qualifiers()


@router.get(
    "/{qualifier_id}", response_model=AsyncQualifierResponse, summary="Get a qualifier (admin)",
    description=(
        'One qualifier, including its internal `config` (par sample size, draw threshold, '
        'run time limit and DM templates).\n\n**Who can call this:** Staff, Qualifier '
        'admin, or admins of this qualifier.'
    ),
)
async def get_qualifier(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().get_qualifier(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/public",
    response_model=AsyncQualifierPublicResponse,
    summary="Get a qualifier's public shell",
    description=(
        "A qualifier's public shell: name, description, window and run rules, without the "
        'internal `config`. This is what the player pages read.\n\n**Who can call this:** '
        'Any member.'
    ),
)
async def get_qualifier_public(qualifier_id: int, actor: User = Depends(require_api_actor)):
    # Ungated player shell: the public response model omits the internal ``config``.
    return await AsyncQualifierService().get_qualifier_for_player(qualifier_id)


@router.get(
    "/{qualifier_id}/admins",
    response_model=List[UserBase],
    summary="List a qualifier's admins/reviewers",
    description=(
        'The people listed as admins of this qualifier. They can review its runs and '
        'manage its pools, on top of Staff and Qualifier admins.\n\n**Who can call '
        'this:** Staff, Qualifier admin, or admins of this qualifier.'
    ),
)
async def list_admins(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().list_admins(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/pools",
    response_model=List[AsyncQualifierPoolResponse],
    summary="List a qualifier's pools (admin)",
    description=(
        'Every pool in the qualifier, with the preset each one rolls seeds from.\n\n**Who '
        'can call this:** Staff, Qualifier admin, or admins of this qualifier.'
    ),
)
async def list_pools(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().list_pools(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/pools/available",
    response_model=List[AsyncQualifierPoolResponse],
    summary="Pools the caller may still draw from",
    description=(
        'The pools you can still draw a run from right now: you have runs left in the '
        "pool and it still holds a permalink you haven't played. Fails with 400 when the "
        "qualifier is switched off, hasn't opened yet or has closed.\n\n**Who can call "
        'this:** Any member.'
    ),
)
async def get_player_pools(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().get_player_pools(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/review-queue",
    response_model=List[AsyncQualifierRunResponse],
    summary="Runs pending review (admin)",
    description=(
        'Finished runs waiting on a verdict, not counting runs that were voided for a '
        "reattempt. Forfeits never appear here, because they're settled as approved with "
        'a score of 0; find those with the runs list.\n\n**Who can call this:** Staff, '
        'Qualifier admin, or admins of this qualifier.'
    ),
)
async def list_review_queue(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().list_review_queue(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/runs",
    response_model=AsyncQualifierRunPage,
    summary="Runs in the qualifier, newest first (admin)",
    description=(
        'One page of every run in the qualifier, newest first, with exact scores, plus '
        '`total`, the number of runs that exist. Use this to find a forfeit or a settled '
        'run to grant a reattempt on.\n\n**Who can call this:** Staff, Qualifier admin, '
        'or admins of this qualifier.'
    ),
)
async def list_runs(
    qualifier_id: int,
    limit: int = Query(RUNS_PAGE_DEFAULT, ge=1, le=RUNS_PAGE_MAX,
                       description="Maximum runs to return."),
    offset: int = Query(0, ge=0),
    actor: User = Depends(require_api_actor),
):
    """One page of runs, plus the qualifier's total.

    Paginated because it was not: a 500-player qualifier answered this with 3,129
    runs in a 1.66 MB body, every time, and a caller that wanted the ten most recent
    had no way to say so. ``total`` is how many exist, not how many came back.
    """
    service = AsyncQualifierService()
    return AsyncQualifierRunPage(
        total=await service.count_runs(actor, qualifier_id),
        limit=limit,
        offset=offset,
        items=[
            AsyncQualifierRunResponse.model_validate(run)
            for run in await service.list_runs(actor, qualifier_id, limit=limit, offset=offset)
        ],
    )


@router.get(
    "/{qualifier_id}/leaderboard",
    response_model=List[LeaderboardEntryResponse],
    summary="Qualifier leaderboard (hidden while open for non-admins)",
    description=(
        "The qualifier's standings. Each runner's best scores fill their "
        "`runs_per_pool` slots in every pool and `actual` is the sum, with empty slots "
        "counting 0; `estimate` fills every slot at their average so a runner partway "
        "through isn't understated. Equal totals share a rank. While the qualifier is "
        "open the board is hidden from runners, so a runner asking before it closes is "
        "refused.\n\n**Who can call this:** Any member once the qualifier closes or is "
        "switched off; while it's open, Staff, Qualifier admin, or admins of this "
        "qualifier."
    ),
)
async def get_leaderboard(qualifier_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().get_leaderboard(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/me/runs",
    response_model=List[MyQualifierRunResponse],
    summary="The caller's runs in a qualifier",
    description=(
        'Your own runs in this qualifier, newest first, with the seed you played, '
        'reviewer notes and, for a run still in progress, its deadline. While the '
        'qualifier is open `score` is null and `score_band` says where you stand against '
        'par instead, because an exact score would reveal the par.\n\n**Who can call '
        'this:** Any member.'
    ),
)
async def list_my_runs(qualifier_id: int, actor: User = Depends(require_api_actor)):
    """The caller's own runs, in the caller's own projection.

    Narrower than the reviewer's ``/{id}/runs`` on purpose: an exact score plus
    the caller's own elapsed time solves for the seed's par, which the
    active-window lockdown exists to hide, so ``score`` is null and ``score_band``
    stands in until the qualifier closes.
    """
    await _load_qualifier_or_404(qualifier_id)
    return await AsyncQualifierService().list_user_runs(actor, qualifier_id)


@router.get(
    "/{qualifier_id}/me/active-run",
    response_model=Optional[AsyncQualifierRunResponse],
    summary="The caller's active run (or null)",
    description=(
        "Your run that's in progress in this qualifier, or null if you have "
        'none.\n\n**Who can call this:** Any member.'
    ),
)
async def get_my_active_run(qualifier_id: int, actor: User = Depends(require_api_actor)):
    await _load_qualifier_or_404(qualifier_id)
    return await AsyncQualifierService().get_active_run(actor, qualifier_id)


@router.get(
    "/runs/{run_id}/notes",
    response_model=List[AsyncQualifierReviewNoteResponse],
    summary="Review notes on a run",
    description=(
        'The reviewer notes on a run, oldest first.\n\n**Who can call this:** Staff, '
        'Qualifier admin, or admins of this qualifier, or the runner whose run it is.'
    ),
)
async def get_run_notes(run_id: int, actor: User = Depends(require_api_actor)):
    return await AsyncQualifierService().get_run_notes(actor, run_id)


# ============================================================ writes (W)

@router.post(
    "",
    response_model=AsyncQualifierResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a qualifier",
    description=(
        'Create a qualifier in this community. It starts switched on, so a qualifier '
        'created inside its own window is open at once. Fails with 400 when the name is '
        'blank, `runs_per_pool` is below 1, `allowed_reattempts` is negative, the close '
        "time isn't after the open time, or `config` has an unknown or invalid "
        'key.\n\n**Who can call this:** Staff or Qualifier admin.'
    ),
)
async def create_qualifier(body: QualifierCreateRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().create_qualifier(actor, **body.model_dump())


@router.patch(
    "/{qualifier_id}", response_model=AsyncQualifierResponse, summary="Update a qualifier",
    description=(
        "Change a qualifier's details. Only the fields you send change; to remove the "
        'window, send `clear_window: true` with no times. Moving the window or switching '
        'the qualifier off takes effect immediately. Fails with 400 for the same reasons '
        'as creating one.\n\n**Who can call this:** Staff, Qualifier admin, or admins of '
        'this qualifier.'
    ),
)
async def update_qualifier(
    qualifier_id: int, body: QualifierUpdateRequest, actor: User = Depends(require_write_actor),
):
    return await AsyncQualifierService().update_qualifier(
        actor, qualifier_id, **body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{qualifier_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a qualifier",
    description=(
        'Delete a qualifier along with its pools, permalinks, live races, runs and review '
        "notes. This can't be undone.\n\n**Who can call this:** Staff, Qualifier admin, "
        'or admins of this qualifier.'
    ),
)
async def delete_qualifier(qualifier_id: int, actor: User = Depends(require_write_actor)):
    await AsyncQualifierService().delete_qualifier(actor, qualifier_id)


@router.post(
    "/{qualifier_id}/admins",
    response_model=DetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a user admin/reviewer on a qualifier",
    description=(
        'List a person as an admin of this qualifier, so they can review its runs and '
        'manage its pools and live races. Listing someone who is '
        'already an admin changes nothing.\n\n**Who can call this:** Staff, Qualifier '
        'admin, or admins of this qualifier.'
    ),
)
async def add_admin(qualifier_id: int, body: AdminRequest, actor: User = Depends(require_write_actor)):
    target = await load_user_or_404(body.user_id)
    await AsyncQualifierService().add_admin(actor, qualifier_id, target)
    return DetailResponse(detail="Qualifier admin added")


@router.delete(
    "/{qualifier_id}/admins/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a user's admin/reviewer on a qualifier",
    description=(
        "Take a person off this qualifier's admin list. Staff and Qualifier admins keep "
        'their access through their role.\n\n**Who can call this:** Staff, Qualifier '
        'admin, or admins of this qualifier.'
    ),
)
async def remove_admin(qualifier_id: int, user_id: int, actor: User = Depends(require_write_actor)):
    target = await load_user_or_404(user_id)
    await AsyncQualifierService().remove_admin(actor, qualifier_id, target)


# --- pools ---

@router.post(
    "/{qualifier_id}/pools",
    response_model=AsyncQualifierPoolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pool",
    description=(
        'Add a pool to the qualifier. Each runner gets `runs_per_pool` draws from every '
        'pool. Fails with 400 when the name is blank or another pool in the qualifier '
        'already has it (ignoring case).\n\n**Who can call this:** Staff, Qualifier '
        'admin, or admins of this qualifier.'
    ),
)
async def create_pool(qualifier_id: int, body: PoolCreateRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().create_pool(actor, qualifier_id, **body.model_dump())


@router.patch(
    "/pools/{pool_id}", response_model=AsyncQualifierPoolResponse, summary="Update a pool",
    description=(
        'Rename a pool or change the preset it rolls seeds from. Only the fields you send '
        'change; send `clear_preset: true` to detach the preset. Fails with 400 when the '
        'new name is blank.\n\n**Who can call this:** Staff, Qualifier admin, or admins '
        'of this qualifier.'
    ),
)
async def update_pool(pool_id: int, body: PoolUpdateRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().update_pool(actor, pool_id, **body.model_dump(exclude_unset=True))


@router.delete(
    "/pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a pool",
    description=(
        'Delete a pool along with its permalinks and live races. Runs already drawn from '
        "it are kept, but lose their link to the seed. This can't be undone.\n\n**Who can "
        'call this:** Staff, Qualifier admin, or admins of this qualifier.'
    ),
)
async def delete_pool(pool_id: int, actor: User = Depends(require_write_actor)):
    await AsyncQualifierService().delete_pool(actor, pool_id)


# --- permalinks ---

@router.post(
    "/pools/{pool_id}/permalinks",
    response_model=AsyncQualifierPermalinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a permalink to a pool",
    description=(
        'Add one seed permalink to a pool. Runners draw from these, and the permalink is '
        "revealed the moment a run starts. Fails with 400 when `url` isn't an http(s) URL "
        'with a host.\n\n**Who can call this:** Staff, Qualifier admin, or admins of this '
        'qualifier.'
    ),
)
async def add_permalink(pool_id: int, body: PermalinkCreateRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().add_permalink(actor, pool_id, **body.model_dump())


@router.post(
    "/pools/{pool_id}/permalinks/bulk",
    response_model=PermalinkBulkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add many permalinks to a pool",
    description=(
        "Add one permalink per line. Blank lines are ignored, and a line that isn't an "
        'http(s) URL is skipped rather than failing the whole request: it comes back in '
        '`rejected` with its line number and the reason.\n\n**Who can call this:** Staff, '
        'Qualifier admin, or admins of this qualifier.'
    ),
)
async def add_permalinks_bulk(
    pool_id: int, body: PermalinkBulkRequest, actor: User = Depends(require_write_actor),
):
    """Add one permalink per usable line, and report the lines that were not.

    A line that is not an http(s) URL is skipped rather than failing the whole
    paste, and comes back in ``rejected`` with its position and the reason — a
    count alone made a typo indistinguishable from a success.
    """
    result = await AsyncQualifierService().add_permalinks_bulk(actor, pool_id, urls=body.urls)
    return PermalinkBulkResponse(
        created=[AsyncQualifierPermalinkResponse.model_validate(p) for p in result.created],
        rejected=[
            RejectedPermalinkLine(line=number, value=value, reason=reason)
            for number, value, reason in result.rejected
        ],
    )


@router.post(
    "/pools/{pool_id}/permalinks/roll",
    response_model=List[AsyncQualifierPermalinkResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Roll fresh seeds into a pool from its preset",
    description=(
        "Roll `count` new seeds from the pool's preset and add each as a permalink. If "
        'any roll fails, none are kept. Fails with 400 when the pool has no preset, its '
        "randomizer only rolls in the background, the community hasn't set up that "
        "randomizer's credentials, or `count` is outside 1 to 25.\n\n**Who can call "
        'this:** Staff, Qualifier admin, or admins of this qualifier.'
    ),
)
async def roll_permalinks(
    pool_id: int, body: PermalinkRollRequest, actor: User = Depends(require_write_actor),
):
    return await AsyncQualifierService().roll_permalinks(actor, pool_id, count=body.count)


@router.patch(
    "/permalinks/{permalink_id}",
    response_model=AsyncQualifierPermalinkResponse,
    summary="Update a permalink",
    description=(
        "Change a permalink's URL, notes, or whether it's kept for live races. Only the "
        "fields you send change. Fails with 400 when the new `url` isn't an http(s) URL "
        'with a host.\n\n**Who can call this:** Staff, Qualifier admin, or admins of this '
        'qualifier.'
    ),
)
async def update_permalink(
    permalink_id: int, body: PermalinkUpdateRequest, actor: User = Depends(require_write_actor),
):
    return await AsyncQualifierService().update_permalink(
        actor, permalink_id, **body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/permalinks/{permalink_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a permalink",
    description=(
        'Delete a permalink from its pool. Runs already drawn on it are kept, but lose '
        "their link to the seed. This can't be undone.\n\n**Who can call this:** Staff, "
        'Qualifier admin, or admins of this qualifier.'
    ),
)
async def delete_permalink(permalink_id: int, actor: User = Depends(require_write_actor)):
    await AsyncQualifierService().delete_permalink(actor, permalink_id)


# --- player run lifecycle ---

@router.post(
    "/{qualifier_id}/runs",
    response_model=AsyncQualifierRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start (draw) a run in a pool",
    description=(
        'Draw a permalink from the pool and start your run: the seed is revealed and the '
        "clock starts now. If you don't submit or forfeit before the qualifier's run time "
        "limit, the run is forfeited for you. Fails with 400 when the qualifier isn't "
        'open, the pool belongs to another qualifier, you already have a run in progress, '
        "you've used all your runs in this pool, or there's no permalink left for you to "
        'draw.\n\n**Who can call this:** Any member.'
    ),
)
async def start_run(qualifier_id: int, body: StartRunRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().start_run(actor, qualifier_id, body.pool_id)


@router.post(
    "/runs/{run_id}/submit", response_model=AsyncQualifierRunResponse, summary="Submit a finished run",
    description=(
        'Submit your finish time for a run in progress. The run is marked finished and '
        "goes to the review queue. Fails with 400 when the run isn't in progress, the "
        "time isn't a positive number of seconds, it's over a week, it's longer than the "
        "run has actually been going, or the VoD link isn't a valid URL.\n\n**Who can "
        'call this:** Yourself only (the runner who started the run).'
    ),
)
async def submit_run(run_id: int, body: SubmitRunRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().submit_run(
        actor, run_id, elapsed_seconds=body.elapsed_seconds, runner_vod_url=body.runner_vod_url
    )


@router.post(
    "/runs/{run_id}/forfeit", response_model=AsyncQualifierRunResponse, summary="Forfeit a run",
    description=(
        'Give up a run in progress. A forfeit scores 0, is settled straight away, and '
        "can't be undone, though you can spend a reattempt to free its pool slot. Fails "
        "with 400 when the run isn't in progress.\n\n**Who can call this:** Yourself only "
        '(the runner who started the run).'
    ),
)
async def forfeit_run(run_id: int, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().forfeit_run(actor, run_id)


@router.post(
    "/runs/{run_id}/reattempt", response_model=AsyncQualifierRunResponse, summary="Reattempt a run",
    description=(
        'Void one of your finished or forfeited runs to free its pool slot for a new '
        "draw. This spends one of the qualifier's `allowed_reattempts`. Fails with 400 "
        "when the reason is blank, the run was already reattempted, it's still in "
        'progress, or you have no reattempts left.\n\n**Who can call this:** Yourself '
        'only (the runner whose run it is).'
    ),
)
async def reattempt_run(run_id: int, body: ReattemptRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().reattempt_run(actor, run_id, reason=body.reason)


@router.post(
    "/runs/{run_id}/grant-reattempt",
    response_model=AsyncQualifierRunResponse,
    summary="Grant a reattempt on a runner's behalf (admin, ignores their allowance)",
    description=(
        "Void a runner's finished or forfeited run on their behalf, freeing its pool slot "
        'without using their own reattempts. Works after the qualifier closes too. The '
        'runner gets a Discord DM with your reason. Fails with 400 when the reason is '
        "blank, the run was already reattempted, or it's still in progress.\n\n**Who can "
        'call this:** Staff, Qualifier admin, or admins of this qualifier.'
    ),
)
async def grant_reattempt(run_id: int, body: ReattemptRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().grant_reattempt(actor, run_id, reason=body.reason)


# --- review ---

@router.post(
    "/runs/{run_id}/claim", response_model=AsyncQualifierRunResponse, summary="Claim a run for review",
    description=(
        "Claim a run for review so other reviewers know you're on it. A claim lapses on "
        'its own after two hours. Claiming a run you already hold renews it. Fails with '
        '400 when another reviewer holds a live claim on the run.\n\n**Who can call '
        'this:** Staff, Qualifier admin, or admins of this qualifier.'
    ),
)
async def claim_run(run_id: int, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().claim_run(actor, run_id)


@router.post(
    "/runs/{run_id}/release", response_model=AsyncQualifierRunResponse, summary="Release a review claim",
    description=(
        "Release the review claim on a run. Any reviewer can release anyone's claim, so a "
        "run isn't stuck when its reviewer has gone.\n\n**Who can call this:** Staff, "
        'Qualifier admin, or admins of this qualifier.'
    ),
)
async def release_claim(run_id: int, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().release_claim(actor, run_id)


@router.post(
    "/runs/{run_id}/review", response_model=AsyncQualifierRunResponse, summary="Approve or reject a run",
    description=(
        "Approve or reject a finished run. Approving recalculates the permalink's par and "
        "rescores every approved run on it; rejecting clears the run's score. Your note "
        'is saved on the run and the runner gets a Discord DM with the verdict and the '
        "note. Fails with 400 when it's your own run, the run isn't finished, another "
        'reviewer holds the claim, the run already has a verdict, another reviewer '
        'settled it first, or you reject without a note.\n\n**Who can call this:** Staff, '
        'Qualifier admin, or admins of this qualifier.'
    ),
)
async def review_run(run_id: int, body: ReviewRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierService().review_run(
        actor, run_id, approved=body.approved, note=body.note
    )
