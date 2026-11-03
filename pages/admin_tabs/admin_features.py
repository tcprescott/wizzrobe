"""Admin → Settings → Features: per-tenant feature toggles (tenant STAFF tier).

The community-facing half of the two-tier feature-flag system. A super-admin
decides which features are *available* to this community (on ``/platform``);
STAFF here flip the available ones on or off for their community. A feature that
hasn't been made available shows locked with a hint. Effective state (what users
see) is available AND enabled. See docs/features/feature-flags.md.
"""

from nicegui import app, ui

from application.services import FeatureFlagService, get_user_from_session
from models import FeatureFlag
from theme.notify import notify_error
from theme.page_header import page_header


async def admin_features_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    service = FeatureFlagService()

    async def _toggle(flag_value: str, enabled: bool) -> None:
        try:
            await service.set_tenant_enabled(actor, FeatureFlag(flag_value), enabled)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Feature enabled' if enabled else 'Feature disabled', color='positive')

    try:
        rows = await service.list_for_tenant_admin(actor)
    except PermissionError as e:
        ui.notify(str(e), color='warning')
        return
    tier = await service.current_tenant_group_name()

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Features',
            'Turn community features on or off. Which features are available to '
            'your community is set by a platform administrator (your tier); you '
            'control whether each available one is on.',
        )
        if tier:
            ui.label(f'Your tier: {tier}').classes('text-caption text-primary')

        categories: list[str] = []
        by_category: dict[str, list[dict]] = {}
        for row in rows:
            if row['category'] not in by_category:
                by_category[row['category']] = []
                categories.append(row['category'])
            by_category[row['category']].append(row)

        for category in categories:
            ui.label(category).classes('text-subtitle1 text-bold q-mt-md')
            for row in by_category[category]:
                with ui.card().classes('w-full'):
                    with ui.row().classes('items-center justify-between w-full no-wrap'):
                        with ui.column().classes('gap-0'):
                            ui.label(row['label']).classes('text-subtitle2 text-bold')
                            ui.label(row['description']).classes('text-caption text-grey')
                        if row['available']:
                            switch = ui.switch(
                                value=row['enabled'],
                                on_change=lambda e, fv=row['flag']: _toggle(fv, e.value),
                            ).props('color=primary')
                            switch.props['aria-label'] = row['label']
                        else:
                            with ui.column().classes('items-end gap-0'):
                                ui.icon('lock').classes('text-grey')
                                ui.label('Not available').classes('text-caption text-grey')
