"""Admin → Settings → Integrations, AI provider: the community's own AI account for survey suggestions.

There is no platform key: a community that wants "Suggest questions" in the
survey builder brings its own provider and key, and pays for its own use.
The key is write-only, like the randomizer keys beside it: never rendered
back, a blank Save keeps the stored one, and clearing is its own action.
"""

from typing import Any, Dict

from nicegui import app, ui

from application.services import AiProviderService, get_user_from_session
from application.utils.clients.ai_client import DEFAULT_MODELS, PROVIDER_LABELS
from models import AiProvider
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable


async def admin_ai_provider_page() -> None:
    service = AiProviderService()

    async def _current():
        return await get_user_from_session(app.storage.user)

    @steady_refreshable
    async def card() -> None:
        try:
            status: Dict[str, Any] = await service.status(await _current())
        except PermissionError as e:
            notify_error(e)
            return
        with ui.card().classes('w-full'):
            with ui.row().classes('items-center justify-between w-full no-wrap'):
                ui.label('Provider').classes('text-subtitle2 text-bold')
                if status['configured'] and status['key_readable']:
                    ui.label('Configured').classes('text-caption text-positive')
                elif status['configured']:
                    ui.label('Key needs re-entering').classes('text-caption text-negative')
                else:
                    ui.label('Not set').classes('text-caption text-grey')
            # url-state: exempt — a form field, not a view
            provider = ui.toggle({p.value: PROVIDER_LABELS[p] for p in AiProvider}, value=status['provider']) \
                .props('no-caps unelevated').classes('wiz-segmented')
            provider.props('aria-label="Provider"')
            model = ui.input('Model', value=status['model']).props('outlined dense maxlength=120').classes('w-full')
            base_url = ui.input('Provider address', value=status['base_url'] or '',
                                placeholder='https://api.openai.com/v1') \
                .props('outlined dense maxlength=255').classes('w-full')
            key = ui.input('API key').props('type=password autocomplete=off outlined dense').classes('w-full')
            if status['configured'] and status['key_readable']:
                key.props('hint="Leave blank to keep the stored key"')
            limit = ui.number('Requests a day (all staff together)', value=status['daily_limit'],
                              min=1, max=200, step=1, precision=0).props('outlined dense').classes('w-full')
            ui.label('Each press of "Suggest questions" is one request, usually a few cents. '
                     'Only the event’s structure and totals are sent: no names, handles or anything people wrote.') \
                .classes('text-caption text-grey-7')

            def provider_changed(value: str) -> None:
                chosen = AiProvider(value)
                base_url.set_visibility(chosen is AiProvider.OPENAI_COMPATIBLE)
                if not model.value or model.value in DEFAULT_MODELS.values():
                    model.value = DEFAULT_MODELS[chosen]

            provider.on_value_change(lambda e: provider_changed(e.value))
            base_url.set_visibility(status['provider'] == AiProvider.OPENAI_COMPATIBLE.value)

            async def save() -> None:
                try:
                    await service.save(await _current(), provider=provider.value, model=model.value or '',
                                       base_url=base_url.value, api_key=key.value,
                                       daily_limit=int(limit.value or 0))
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                key.value = ''
                ui.notify('AI provider saved', color='positive')
                card.refresh()

            async def clear() -> None:
                try:
                    await service.clear(await _current())
                except PermissionError as e:
                    notify_error(e)
                    return
                ui.notify('AI provider removed', color='positive')
                card.refresh()

            with ui.element('div').classes('wiz-pair-actions'):
                if status['configured']:
                    ui.button('Clear', icon='delete', on_click=lambda: ConfirmationDialog(
                        'Remove the AI provider and its key? "Suggest questions" disappears from the survey '
                        'builder until someone sets one up again.',
                        on_confirm=clear, confirm_text='Clear', title='Clear AI provider?',
                    ).open()).props('flat color=negative')
                ui.button('Save', icon='save', on_click=save).props('color=primary')

    with ui.column().classes('page-container-narrow'):
        page_header(
            'AI provider',
            'Your community’s own AI account, used by "Suggest questions" in the survey builder. '
            'Bring a Claude (Anthropic) key, or any OpenAI-compatible service. Stored encrypted and never shown again.',
        )
        await card()
