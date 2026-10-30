"""Shared 'Connected accounts' profile section.

Challonge, Twitch, and racetime each expose the same link/unlink affordance;
they differ only in the service class, the copy, the ``<provider>_user_id`` /
``<provider>_username`` attribute names, and the OAuth-initiation route. Rather
than one full card per provider (three near-identical cards to scroll past on a
phone), they render together as compact rows inside a single card. Each provider
module supplies a :class:`LinkSectionConfig`.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from nicegui import ui

from application.services.profile_nudge_service import ProfileSection
from application.tenant_context import is_host_mode
from application.utils.environment import host_oauth_handoff_enabled
from application.utils.timezone import format_local_date
from models import User
from pages._oauth_link import platform_link_redirect
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.profile_nudge_banner import section_anchor

__all__ = ['LinkSectionConfig', 'render_connected_accounts_section']


@dataclass(frozen=True)
class LinkSectionConfig:
    """Everything the shared row renderer needs for one provider.

    **Every field here must be rendered or deleted.** ``description`` and
    ``link_button_label`` were declared, populated three times and read by
    nothing for the whole life of this section, which is how an audit came to
    praise copy that had never been on a screen.
    """

    title: str
    icon: str
    description: str
    link_route: str
    link_button_label: str
    unlink_button_label: str
    unlinked_message: str
    user_id_attr: str
    username_attr: str
    linked_at_attr: str
    service_factory: Callable[[], object]
    unlink_confirmation: Optional[str] = None
    sign_in_first_url: Optional[str] = None


def _render_provider_row(
    user: User, config: LinkSectionConfig, main_site_url: Optional[str] = None,
) -> None:
    """One provider row: icon, name, link status, and a link/unlink button."""
    service = config.service_factory()

    @ui.refreshable
    def row() -> None:
        user_id = getattr(user, config.user_id_attr)
        username: Optional[str] = getattr(user, config.username_attr)
        linked_at = getattr(user, config.linked_at_attr, None)
        with ui.row().classes('items-center w-full no-wrap gap-3'):
            ui.icon(config.icon, size='sm').classes('icon-primary')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label(config.title).classes('text-weight-medium')
                if user_id:
                    with ui.row().classes('items-start gap-1 no-wrap'):
                        ui.icon('check_circle', size='xs').classes('text-positive q-mt-xs')
                        since = f' on {format_local_date(linked_at)}' if linked_at else ''
                        ui.label(f'Linked as {username or user_id}{since}') \
                            .classes('text-caption text-grey-7 col min-w-0') \
                            .style('overflow-wrap: anywhere')
                else:
                    ui.label('Not linked').classes('text-caption text-grey-7')
                    ui.label(config.description).classes('text-caption text-muted')
            if user_id:
                ui.button('Unlink', icon='link_off', on_click=confirm_unlink) \
                    .classes('col-auto') \
                    .props(f'flat dense color=negative aria-label="{config.unlink_button_label}"') \
                    .tooltip(config.unlink_button_label)
            elif is_host_mode() and not host_oauth_handoff_enabled():
                ui.link('Main site only', main_site_url or '/') \
                    .classes('text-caption') \
                    .tooltip(f'Open your profile on the main site to link {config.title}.')
            else:
                ui.button('Link', icon='link', on_click=start_link) \
                    .classes('col-auto') \
                    .props(f'flat dense color=primary aria-label="{config.link_button_label}"') \
                    .tooltip(config.link_button_label)

    def start_link() -> None:
        if config.sign_in_first_url:
            open_sign_in_first_dialog(config, config.sign_in_first_url)
        else:
            ui.navigate.to(config.link_route)

    async def unlink() -> None:
        # In place: the row re-renders under the same document, so ui.notify is right and stash_notice wrong.
        try:
            await service.unlink_player(user, actor=user)
            ui.notify(config.unlinked_message, color='positive')
        except ValueError as e:
            ui.notify(str(e), color='warning')
        row.refresh()

    async def confirm_unlink() -> None:
        if not config.unlink_confirmation:
            await unlink()
            return

        async def go() -> None:
            await unlink()

        dialog = ConfirmationDialog(
            message=config.unlink_confirmation,
            on_confirm=go,
            confirm_text='Unlink',
            title=f'Unlink {config.title}?',
        )
        dialog.open()

    row()


def open_sign_in_first_dialog(config: LinkSectionConfig, sign_in_url: str) -> None:
    """Ask the reader to sign in to the provider before starting its OAuth."""
    def go() -> None:
        dialog.close()
        ui.navigate.to(config.link_route)

    with form_dialog(f'Sign in to {config.title} first', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-2'):
            ui.label(
                f"If you aren't signed in to {config.title} already, it shows an "
                f'"authentication required" error instead of a sign-in page.'
            )
            ui.label(
                f'Open {config.title}, sign in, then come back here and continue.'
            )
            ui.link(f'Open {config.title} sign-in', sign_in_url, new_tab=True)
        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button("I'm signed in, continue", on_click=go).props('color=primary')
    dialog.open()


async def render_connected_accounts_section(
    user: User, configs: Sequence[LinkSectionConfig]
) -> None:
    """Render the linkable providers together in one 'Connected accounts' card.

    Providers whose integration isn't configured are skipped; if none are
    configured the whole card is omitted.
    """
    active = [c for c in configs if c.service_factory().is_configured()]
    if not active:
        return

    main_site_url = await platform_link_redirect('/home/profile')

    with ui.card().classes('card-full-width').props(f'id={section_anchor(ProfileSection.CONNECTED_ACCOUNTS)}'):
        ui.label('Connected accounts').classes('section-title')
        ui.label(
            'Link external accounts to verify your identity and let us find your '
            'bracket and race matches for scheduling.'
        ).classes('text-muted text-caption')
        for i, config in enumerate(active):
            if i:
                ui.separator().classes('q-my-sm')
            _render_provider_row(user, config, main_site_url)
