"""The app's Admin → Notifications screen in one read.

Changes go through ``/notification-settings``; this is the grid they edit,
already grouped into cards with the Staff column folded.
"""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_staff
from api.schemas.admin_notifications import AdminNotificationsResponse, NotificationCardResponse
from application.services import NotificationSettingsService
from models import User

router = APIRouter(
    prefix='/admin/notifications', tags=['Notification settings'], route_class=ServiceErrorRoute,
)


@router.get(
    '',
    response_model=AdminNotificationsResponse,
    summary='Notification settings as the admin screen shows them',
    description=(
        'One card per subheading of the catalogue, each listing its messages with a cell per '
        'kind of recipient. The on-shift, admins and all-staff recipients fold into one '
        '`staff_alerts` cell carrying the Staff choice and the options it offers. Change a cell '
        'with `PUT /notification-settings/{notice}/{recipient}`, or the Staff cell with '
        '`PUT /notification-settings/{notice}/staff-alerts`.\n\n'
        '**Who can call this:** Staff.'
    ),
)
async def admin_notifications(actor: User = Depends(require_staff)):
    cards = await NotificationSettingsService.screen(actor)
    return AdminNotificationsResponse(
        cards=[NotificationCardResponse.from_card(c) for c in cards],
        changed=sum(1 for card in cards for m in card.messages for cell in m.cells if cell.changed),
    )
