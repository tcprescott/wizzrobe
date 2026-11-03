"""The pieces more than one ``/platform`` section needs.

``pages/platform.py`` is the page; each section it stacks lives in its own
module (``platform_bots``, ``platform_feature_groups``, ``platform_tenant_admins``)
because the page kept running at the file-length guideline. Anything two of them
share lands here rather than being imported from the page — that direction would
be a cycle.
"""

from nicegui import app, ui

from application.services import get_user_from_session

# Rows must carry an `active_bool` boolean.
ACTIVE_ICON_SLOT = '''
    <q-td :props="props">
        <q-icon :name="props.row.active_bool ? 'check_circle' : 'cancel'"
                :color="props.row.active_bool ? 'positive' : 'negative'" size="sm" />
    </q-td>
'''


async def current_actor():
    """The signed-in super-admin, re-read for a refresh outside a click handler."""
    return await get_user_from_session(app.storage.user)


def section_intro(caption: str) -> ui.row:
    """One tab's explanation, with a row for its actions beside it on desktop.

    Below ``md`` the caption takes the full width and the actions drop under it:
    side by side on a phone, a primary button squeezed the caption into a
    two-word column.
    """
    with ui.row().classes('w-full items-start justify-between gap-3'):
        ui.label(caption).classes('text-caption col-12 col-md')
        actions = ui.row().classes('items-center gap-2')
    return actions
