"""The in-app help section.

**Anonymous-readable.** Both routes are :func:`public_page`: help has to work for
someone who cannot get in, which is exactly when they most need it. The article
set is filtered by the community's live feature flags and by who is reading:
staff articles (``audience: staff`` or ``roles:``) are listed in their own "For staff" group
for someone who can open Admin, and are simply absent for everyone else.

Presentation only — it reads through :class:`HelpService` and renders parsed
blocks with :func:`theme.help.render_blocks`. Nothing here touches the ORM.
"""

from nicegui import app, ui

from application.help import HelpArticle
from application.services import (
    HelpService,
    TenantService,
    get_user_from_session,
)
from middleware.auth import public_page
from theme.a11y import make_activatable
from theme.base import BaseLayout
from theme.error_page import render_not_found
from theme.help.article import render_article
from theme.page_header import page_header
from theme.steady import steady_refreshable

__all__ = ['create']


def _article_card(article: HelpArticle) -> None:
    with make_activatable(ui.card().classes('wiz-help-card cursor-pointer')
                          .on('click', lambda slug=article.slug: ui.navigate.to(f'/help/{slug}')), role='link'):
        with ui.row().classes('items-center no-wrap gap-3'):
            ui.icon(article.icon).props('size=sm').classes('text-primary')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label(article.title).classes('text-weight-bold')
                if article.summary:
                    ui.label(article.summary).classes('text-caption text-grey-7')


def _whats_new_card() -> None:
    """The way into ``/changelog``, which has no menu item of its own."""
    with make_activatable(ui.card().classes('wiz-help-card cursor-pointer q-mt-md')
                          .on('click', lambda: ui.navigate.to('/changelog')), role='link'):
        with ui.row().classes('items-center no-wrap gap-3'):
            ui.icon('new_releases').props('size=sm').classes('text-primary')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label("What's new").classes('text-weight-bold')
                ui.label('The bigger changes to the app, a week at a time.') \
                    .classes('text-caption text-grey-7')


def _about_card() -> None:
    """The way into ``/about`` (privacy policy, third-party notices), which has no menu item."""
    with make_activatable(ui.card().classes('wiz-help-card cursor-pointer q-mt-sm')
                          .on('click', lambda: ui.navigate.to('/about')), role='link'):
        with ui.row().classes('items-center no-wrap gap-3'):
            ui.icon('info').props('size=sm').classes('text-primary')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label('About Wizzrobe').classes('text-weight-bold')
                ui.label('What the app is, its privacy policy and third-party notices.') \
                    .classes('text-caption text-grey-7')


async def _viewer():
    return await get_user_from_session(app.storage.user)


async def _render_chrome(user) -> None:
    await BaseLayout(user=user).render()


async def _title(*parts: str) -> str:
    """A tab title that says which community's help this is."""
    community = await TenantService.current_community_name() or 'Wizzrobe'
    return ' — '.join((*parts, community))


def create() -> None:
    @public_page('/help')
    async def help_index() -> None:
        ui.page_title(await _title('Help'))
        user = await _viewer()
        await _render_chrome(user)

        articles = await HelpService.list_articles(user)
        state = {'query': ''}

        with ui.column().classes('page-container-narrow w-full'):
            page_header('Help')
            ui.label(
                'How the app works for players, crew and volunteers. '
                'Search, or pick a topic.'
            ).classes('text-muted')

            _whats_new_card()
            _about_card()

            search = ui.input(placeholder='Search help') \
                .props('outlined dense clearable autofocus') \
                .classes('full-width q-mt-md')
            with search.add_slot('prepend'):
                ui.icon('search')

            @steady_refreshable
            async def results() -> None:
                matches = await HelpService.search(state['query'], user)
                if not matches:
                    ui.label(
                        f'Nothing matches "{state["query"]}". Try fewer words.'
                    ).classes('text-muted q-mt-md')
                    return
                general = [a for a in matches if not HelpService.for_staff(a)]
                staff = [a for a in matches if HelpService.for_staff(a)]
                for heading, group in ((None, general), ('For staff', staff)):
                    if not group:
                        continue
                    if heading:
                        ui.label(heading).classes('wiz-help-group-label')
                    with ui.element('div').classes('wiz-tile-grid'):
                        for article in group:
                            _article_card(article)

            async def on_search(event) -> None:
                # `clearable` sets the value to None, not ''.
                state['query'] = (event.value or '').strip()
                await results.refresh()

            search.on_value_change(on_search)

            with ui.column().classes('full-width gap-2 q-mt-md'):
                await results()

            if not articles:
                ui.label('No help articles are available.').classes('text-muted')

    @public_page('/help/{slug}')
    async def help_article(slug: str) -> None:
        user = await _viewer()
        article = await HelpService.get_article(slug, user)
        if article is None:
            await render_not_found(
                user=user, actions=[('All help', 'help_outline', '/help')],
            )
            return

        ui.page_title(await _title(article.title, 'Help'))
        await _render_chrome(user)
        render_article(
            article, await HelpService.list_articles(user), base='/help', back_label='All help',
            for_staff=HelpService.for_staff,
        )
