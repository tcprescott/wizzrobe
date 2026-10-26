"""Seed-generation endpoints.

Exposes the list of supported randomizers and a single roll-a-seed action over
the existing :class:`SeedGenerationService`. A free roll spends the community's
randomizer keys, so it is held to the people the web app offers Seed Presets'
Test roll to (``AuthService.ensure_can_roll_free_seed``).
"""

from typing import List

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.seeds import (
    RandomizerResponse,
    SeedGenerateRequest,
    SeedResponse,
    SeedTaskResponse,
)
from application.errors import require_found
from application.services import (
    AuthService,
    ProviderTaskService,
    RandomizerCredentialService,
    SeedGenerationService,
    SeedRollService,
)
from application.tenant_context import require_tenant_id
from models import Preset, ProviderTask, User

router = APIRouter(prefix="/seeds", tags=["Seeds"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding())])


@router.get(
    "/randomizers",
    response_model=List[RandomizerResponse],
    summary="List supported randomizers",
    description=(
        "Lists the randomizers this community can roll seeds for. Randomizers that need an "
        "API key the community hasn't added are left out.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_randomizers(actor: User = Depends(require_api_actor)):
    # Drop randomizers whose upstream needs a key this community has not supplied,
    # mirroring the web selector surfaces (a stored preset stays valid, but the
    # catalogue advertises only what this community may actually roll).
    configured = await RandomizerCredentialService().configured_randomizers()
    return [
        RandomizerResponse(
            randomizer=r,
            supports_triforce_texts=SeedGenerationService.supports_triforce_texts(r),
        )
        for r in SeedGenerationService.available_randomizers(configured)
    ]


@router.post(
    "",
    response_model=SeedResponse,
    responses={
        status.HTTP_202_ACCEPTED: {
            "model": SeedTaskResponse,
            "description": "The roll was queued. Poll `GET /seeds/tasks/{task_id}` for the seed.",
        },
    },
    summary="Generate a seed",
    description=(
        "Rolls a seed, optionally from one of this community's presets. Most randomizers "
        "answer 200 with the seed. Donkey Kong 64 rolls take minutes, so they answer 202 with "
        "a task to poll at `GET /seeds/tasks/{task_id}`. Fails with 400 when the randomizer "
        "is unknown, needs a preset or API key that's missing, or the randomizer site rejects "
        "the roll. Fails with 403 for anyone who isn't Staff or a Preset manager.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def generate_seed(
    body: SeedGenerateRequest,
    actor: User = Depends(require_write_actor),
):
    """Roll a seed.

    **200** with the permalink for a synchronous randomizer. **202** with a task
    id for one whose upstream is a queue (`dk64r`), because those take minutes
    and there is no url to hand back yet — poll `GET /seeds/tasks/{id}`.
    """
    await AuthService.ensure_can_roll_free_seed(actor)
    # A keyed randomizer this community has not configured raises inside the
    # generator; ServiceErrorRoute turns that ValueError into a 400 naming the
    # missing credential.
    preset = None
    if body.preset_id is not None:
        preset = require_found(
            await Preset.get_or_none(id=body.preset_id, tenant_id=require_tenant_id()), "Preset"
        )

    if body.randomizer in SeedGenerationService.ASYNC_RANDOMIZERS:
        task = await ProviderTaskService().queue(
            provider=body.randomizer, operation='generate_seed', actor=actor,
            preset_id=preset.id if preset is not None else None,
        )
        task = await SeedRollService().submit(task, preset)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_task_response(task).model_dump(),
        )

    url = await SeedGenerationService().generate_seed(body.randomizer, preset)
    return SeedResponse(url=url)


@router.get(
    "/tasks/{task_id}",
    response_model=SeedTaskResponse,
    summary="Check an asynchronous seed roll",
    description=(
        "Reports how a queued seed roll is going. Poll until `status` is `succeeded`, "
        "`failed` or `abandoned`.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_seed_task(task_id: int, actor: User = Depends(require_api_actor)):
    """Where a queued roll has got to. 404 for a task another community owns."""
    task = require_found(await ProviderTaskService().get_by_id(task_id), "Task")
    return _task_response(task)


def _task_response(task: ProviderTask) -> SeedTaskResponse:
    # The permalink lives on the GeneratedSeeds row the completion wrote, not on
    # the task, so a finished task is resolved through its match. A task rolled
    # without a match (a bare API roll) has nowhere to put one, and reports
    # succeeded with no url.
    seed = getattr(getattr(task, 'match', None), 'generated_seed', None)
    return SeedTaskResponse(
        task_id=task.id,
        randomizer=task.provider,
        status=task.status.value if hasattr(task.status, 'value') else str(task.status),
        url=seed.seed_url if seed is not None else None,
        error=task.error,
    )
