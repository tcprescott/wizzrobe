"""The app's admin shell: which admin areas and settings panes the caller may open."""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_api_actor
from api.schemas.admin_nav import AdminAreaResponse, AdminNavResponse, SettingsPaneResponse, SetupStepResponse
from application.services import AdminNavService
from models import User

router = APIRouter(prefix='/admin', tags=['Admin shell'], route_class=ServiceErrorRoute)


@router.get(
    '/nav',
    response_model=AdminNavResponse,
    summary='Your admin areas',
    description=(
        "Every admin area you may open in this community, in menu order with its group, plus "
        "the Settings and Event settings panes and the setup checklist. The app draws its admin "
        "menu from this and hides what the API would refuse.\n\n"
        "**Who can call this:** Any member. Someone with no admin access gets empty lists."
    ),
)
async def admin_nav(actor: User = Depends(require_api_actor)) -> AdminNavResponse:
    nav = await AdminNavService().nav(actor)
    return AdminNavResponse(
        areas=[
            AdminAreaResponse(slug=a.slug, label=a.label, icon=a.icon, group=a.group, half=a.half, aliases=list(a.aliases))
            for a in nav.areas
        ],
        settings_panes=[SettingsPaneResponse.model_validate(p) for p in nav.settings_panes],
        event_settings_panes=[SettingsPaneResponse.model_validate(p) for p in nav.event_settings_panes],
        setup=[SetupStepResponse.model_validate(s) for s in nav.setup],
        setup_ready=nav.setup_ready,
    )
