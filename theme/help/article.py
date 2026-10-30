"""The article page shared by ``/help/{slug}`` and ``/event-info/{slug}``.

Three columns on a wide screen, centred so a 1920px monitor isn't two thirds
empty: a sticky article list (back link, every article, staff ones under their
own label), the body, and a sticky "On this page" rail. The rail needs room the
body can't spare until 1280px, so below that the headings fold into an "On this
page" expander above the body instead.

Below 1024px the columns stack with the body first, because the list is ~550px
of links and in source order it pushed the title below the fold. The way back
moves above the body with the expander, and the stacked list becomes a "more"
footer. ``static/css/styles.css`` owns which copy shows where.
"""

from typing import Callable, Mapping, Optional

from nicegui import ui

from application.content import ContentArticle
from theme.help.render import render_blocks
from theme.page_header import page_title

__all__ = ['render_article']


def _contents(article: ContentArticle, base: str) -> None:
    for anchor, text in article.headings:
        ui.link(text, f'{base}/{article.slug}#{anchor}').classes('wiz-help-nav-sub')


def render_article(
    article: ContentArticle,
    articles: list[ContentArticle],
    *,
    base: str,
    back_label: str,
    for_staff: Optional[Callable[[ContentArticle], bool]] = None,
    embeds: Optional[Mapping[str, Callable[[], None]]] = None,
    intro_extra: Optional[Callable[[], None]] = None,
) -> None:
    """Render ``article`` with its sidebar; ``articles`` is the reader's list.

    ``for_staff`` marks the articles that go under a "For staff" label. Help
    passes it, and ``HelpService.list_articles`` orders that group last; the
    event handbook passes nothing, because its role-gated pages are not staff's.
    """
    staff = {a.slug for a in articles if for_staff is not None and for_staff(a)}
    labelled = bool(staff)
    with ui.row().classes('wiz-help-layout no-wrap w-full items-start'):
        with ui.column().classes('wiz-help-nav gap-0'):
            ui.link(f'← {back_label}', base).classes('wiz-help-back-link wiz-help-wide-only')
            if labelled:
                ui.label('Everyone').classes('wiz-help-nav-group')
            staff_label_shown = False
            for other in articles:
                if other.slug in staff and not staff_label_shown:
                    staff_label_shown = True
                    ui.label('For staff').classes('wiz-help-nav-group')
                link = ui.link(other.title, f'{base}/{other.slug}') \
                    .classes('wiz-help-nav-item')
                if other.slug == article.slug:
                    link.classes(add='wiz-help-nav-item--active').props('aria-current=page')

        with ui.column().classes('wiz-help-body col min-w-0'):
            with ui.column().classes('gap-1 wiz-help-toc-inline w-full'):
                ui.link(f'← {back_label}', base).classes('wiz-help-back-link wiz-help-narrow-only')
                if article.headings:
                    with ui.expansion('On this page').props('dense') \
                            .classes('wiz-help-toc w-full'):
                        with ui.column().classes('gap-1'):
                            _contents(article, base)
            with ui.row().classes('items-center gap-2 no-wrap'):
                ui.icon(article.icon).props('size=sm').classes('text-primary')
                page_title(article.title)
            if article.summary:
                ui.label(article.summary).classes('text-muted')
            if intro_extra is not None:
                intro_extra()
            ui.separator().classes('separator-spacing')
            with ui.element('div').classes('wiz-help-prose'):
                render_blocks(article.blocks, embeds=embeds)

        if article.headings:
            with ui.element('nav').classes('wiz-help-rail') \
                    .props('aria-label="On this page"'):
                ui.label('On this page').classes('wiz-help-nav-group')
                _contents(article, base)
