"""The per-community event information section.

**Anonymous-readable.** Both routes are :func:`public_page`: most of what this
section answers — when doors open, what to bring, where to go — is wanted
*before* someone has signed in, and a spectator may never sign in at all.

Unlike ``/help``, the article set here *does* vary by reader: an article may name
roles, and the floor procedure the room staff work from is written for them. The
page stays public because that filter subtracts — a signed-out reader gets the
public articles, never an empty page or a redirect to ``/login``.

Where ``/help`` explains the app, this explains one community's event, so the
whole section is behind ``EVENT_INFO`` and 404s where that is not granted.

Presentation only — it reads through :class:`EventInfoService` and renders parsed
blocks with :func:`theme.help.render_blocks`, the same closed-vocabulary renderer
the help section uses. Nothing here touches the ORM.
"""

from fastapi.responses import FileResponse, PlainTextResponse, Response
from nicegui import app, ui

from application.content import ContentArticle
from application.errors import FeatureDisabledError
from application.services import (
    EventGuide,
    EventInfoService,
    TenantService,
    TimezoneService,
    get_user_from_session,
)
from application.tenant_context import require_tenant_id
from application.utils.tenant_urls import login_path
from middleware.auth import public_page
from models import FeatureFlag
from theme.a11y import make_activatable
from theme.banner import banner
from theme.base import BaseLayout, current_local_url
from theme.error_page import render_not_found
from theme.event_info_tournaments import render_tournament_list
from theme.help.article import render_article
from theme.links import link_button
from theme.page_header import page_header

__all__ = ['create']


def _article_card(article: ContentArticle) -> None:
    with make_activatable(ui.card().classes('wiz-help-card cursor-pointer')
                          .on('click', lambda slug=article.slug: ui.navigate.to(f'/event-info/{slug}')), role='link'):
        with ui.row().classes('items-center no-wrap gap-3'):
            ui.icon(article.icon).props('size=sm').classes('text-primary')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label(article.title).classes('text-weight-bold')
                if article.summary:
                    ui.label(article.summary).classes('text-caption text-grey-7')


async def _viewer():
    """The signed-in reader, or None — the articles are role-filtered."""
    return await get_user_from_session(app.storage.user)


async def _render_chrome(user) -> None:
    await BaseLayout(user=user).render()


async def _title(*parts: str) -> str:
    """A tab title that says which community's handbook this is."""
    community = await TenantService.current_community_name() or 'Wizzrobe'
    return ' — '.join((*parts, community))


async def _embeds(article: ContentArticle) -> dict:
    """Builders for the live-data blocks ``article`` uses, loaded up front."""
    if not any(block.kind == 'embed' and block.name == 'tournaments' for block in article.blocks):
        return {}
    rows = await EventInfoService.list_tournaments()
    # The prose around the list states times on the venue's clock, so the
    # close times use it too rather than each reader's own zone.
    tz = await TimezoneService.tenant_timezone_name(require_tenant_id())
    return {'tournaments': lambda: render_tournament_list(rows, tz=tz)}


#: The organisers' own guide, served beside the articles. A fixed path rather
#: than the file's name so a printed link survives a new revision of the PDF.
GUIDE_PATH = '/event-info/guide.pdf'


def _guide_banner(guide: EventGuide) -> None:
    with banner(f'Directions, the hotel map and every detail. PDF, {guide.size_label}.',
                title='The full event guide', icon='picture_as_pdf'):
        link_button('Open guide', GUIDE_PATH).props('unelevated no-caps color=primary')


def _guide_link() -> None:
    with ui.row().classes('items-center gap-1 no-wrap q-mt-xs'):
        ui.icon('picture_as_pdf').props('size=xs').classes('text-primary')
        link = ui.link('Full details in the event guide (PDF)', GUIDE_PATH, new_tab=True)
        link.props['aria-label'] = 'Full details in the event guide (PDF, opens in a new tab)'


def _nothing_published() -> None:
    """The state a community sees between being granted the feature and writing.

    Reachable and deliberately plain: the flag being on says the community has
    the section, not that anyone has filled it in yet.
    """
    ui.label('Nothing has been published here yet.').classes('text-muted')
    ui.link('How the app works →', '/help').classes('q-mt-sm')


def create() -> None:
    # Registered before the article route so ``guide.pdf`` is never read as an
    # article slug.
    @app.get(GUIDE_PATH, include_in_schema=False)
    async def event_guide_pdf() -> Response:
        try:
            guide = await EventInfoService.get_guide()
        except (FeatureDisabledError, LookupError, RuntimeError):
            guide = None
        if guide is None:
            return PlainTextResponse('Not found', status_code=404)
        return FileResponse(
            guide.path, media_type='application/pdf', filename=guide.filename,
            content_disposition_type='inline',
            headers={'Cache-Control': 'public, max-age=300', 'X-Content-Type-Options': 'nosniff'},
        )

    @public_page('/event-info', feature=FeatureFlag.EVENT_INFO)
    async def event_info_index() -> None:
        ui.page_title(await _title('Event Information'))
        user = await _viewer()
        await _render_chrome(user)

        articles = await EventInfoService.list_articles(user)
        guide = await EventInfoService.get_guide()

        with ui.column().classes('page-container-narrow w-full'):
            page_header('Event Information')
            ui.label(
                'The short version: hours, tournaments, getting set up and who to ask.'
                if guide else
                'Everything about this event: what is on, attending on the day, '
                'and who to ask.'
            ).classes('text-muted')

            if guide:
                _guide_banner(guide)

            if not articles and not guide:
                _nothing_published()
                return

            with ui.element('div').classes('wiz-tile-grid q-mt-md'):
                for article in articles:
                    _article_card(article)

            # The boundary between the two sections, stated where a reader who
            # came looking for the wrong one will see it.
            ui.separator().classes('separator-spacing')
            with ui.row().classes('items-baseline gap-1 no-wrap'):
                ui.label('Looking for how the app works?').classes('text-muted')
                ui.link('Help', '/help')

    @public_page('/event-info/{slug}', feature=FeatureFlag.EVENT_INFO)
    async def event_info_article(slug: str) -> None:
        user = await _viewer()
        article = await EventInfoService.get_article(slug, user)
        if article is None:
            back = ('All event information', 'event_note', '/event-info')
            if user is None:
                # Not revealing which role-gated pages exist is deliberate, but
                # a signed-out reader holds no role by definition, so saying
                # some pages need one leaks nothing — and it is the proctor on
                # a borrowed laptop's only way forward.
                await render_not_found(
                    user=None,
                    message=(
                        "We couldn't find that page. Some pages here are only "
                        'for crew and staff, so if you have a role in this '
                        'community, sign in and try again.'
                    ),
                    actions=[('Sign in', 'login', login_path(current_local_url())), back],
                )
            else:
                await render_not_found(user=user, actions=[back])
            return

        ui.page_title(await _title(article.title, 'Event Information'))
        embeds = await _embeds(article)
        await _render_chrome(user)
        guide = await EventInfoService.get_guide()
        render_article(
            article, await EventInfoService.list_articles(user),
            base='/event-info', back_label='All event information', embeds=embeds,
            intro_extra=_guide_link if guide else None,
        )
