"""Rendering parsed help blocks with native NiceGUI elements.

There is deliberately no ``ui.markdown`` here. Every block and every span is
drawn by a branch below, so an article can only ever produce elements this module
creates — the article text reaches the page as element *content*, never as
markup. That is what makes shipping editable prose through the UI safe, and it is
the same reason ``check_markdown_xss`` exists.

The vocabulary is closed: seven block kinds, seven span kinds, plus ``embed``,
whose content the page hands in as a builder (an embed with no builder renders
nothing). Anything the parser
could not classify arrived here as plain text and renders as plain text.
"""

from typing import Callable, Mapping, Optional

from nicegui import ui

from application.help import STATE_STYLES, Block, Span

__all__ = ['render_blocks', 'render_spans', 'render_user_blocks']


def _render_user_link(span: Span) -> None:
    # Someone else wrote this link: new tab, no opener, no referrer.
    link = ui.link('', span.href, new_tab=True).classes('wiz-help-link wiz-user-link')
    link.props['rel'] = 'noopener noreferrer nofollow'
    link.props['aria-label'] = f'{span.text} (opens in a new tab)'
    with link:
        ui.label(span.text).classes('wiz-help-inline')
        ui.icon('open_in_new').props('size=0.9em aria-hidden=true').classes('wiz-user-link__icon')


def _render_span(span: Span, *, untrusted: bool = False) -> None:
    if span.kind == 'link' and untrusted:
        _render_user_link(span)
    elif span.kind == 'link':
        ui.link(span.text, span.href, new_tab=not span.href.startswith(('/', '#'))) \
            .classes('wiz-help-link')
    elif span.kind == 'strong':
        ui.label(span.text).classes('wiz-help-inline text-weight-bold')
    elif span.kind == 'em':
        ui.label(span.text).classes('wiz-help-inline wiz-help-em')
    elif span.kind == 'code':
        ui.label(span.text).classes('wiz-help-inline wiz-help-code')
    elif span.kind == 'chip':
        with ui.element('span').classes(f'wiz-chip wiz-chip--{span.tone}'):
            ui.label(span.text).classes('wiz-help-inline')
    elif span.kind == 'state':
        icon, tone = STATE_STYLES[span.text]
        with ui.element('span').classes('wiz-help-state'):
            ui.icon(icon).classes(tone).props('size=xs')
            ui.label(span.text).classes('wiz-help-inline')
    else:
        ui.label(span.text).classes('wiz-help-inline')


def render_spans(spans: tuple[Span, ...], *, untrusted: bool = False) -> None:
    """Render one run of inline content into the current slot."""
    for span in spans:
        _render_span(span, untrusted=untrusted)


def _render_table(block: Block) -> None:
    # (mobile-grid: exempt — static prose, scrolls in its own container.)
    with ui.element('div').classes('wiz-help-table-wrap'):
        with ui.element('table').classes('wiz-help-table'):
            if block.headers:
                with ui.element('thead'), ui.element('tr'):
                    for cell in block.headers:
                        with ui.element('th'):
                            render_spans(cell)
            with ui.element('tbody'):
                for row in block.rows:
                    with ui.element('tr'):
                        for cell in row:
                            with ui.element('td'):
                                render_spans(cell)


def _render_list(block: Block, *, untrusted: bool = False) -> None:
    tag = 'ul' if block.kind == 'bullets' else 'ol'
    with ui.element(tag).classes('wiz-help-list'):
        for item in block.items:
            with ui.element('li'):
                render_spans(item, untrusted=untrusted)


def _img(block: Block, *, lazy: bool):
    image = ui.element('img').classes('wiz-help-img')
    # Set as values, never via the props string: alt text may hold quotes or spaces the parser splits on.
    image.props['src'] = block.src
    image.props['alt'] = block.alt
    if lazy:
        image.props['loading'] = 'lazy'
    image.props['decoding'] = 'async'
    return image


def _render_image(block: Block) -> None:
    zoom = ui.dialog().props('transition-show=fade transition-hide=fade')
    with zoom, ui.element('div').classes('wiz-help-zoom').on('click', zoom.close):
        _img(block, lazy=False)
    with ui.element('figure').classes('wiz-help-figure'):
        opener = ui.element('button').classes('wiz-help-figure-open').on('click', zoom.open)
        opener.props['type'] = 'button'
        opener.props['aria-label'] = f'Enlarge: {block.alt}'
        with opener:
            _img(block, lazy=True)
        if block.spans:
            with ui.element('figcaption').classes('wiz-help-caption'):
                render_spans(block.spans)


def render_blocks(
    blocks,
    *,
    heading_offset: int = 0,
    embeds: Optional[Mapping[str, Callable[[], None]]] = None,
) -> None:
    """Render a block list into the current slot.

    ``heading_offset`` demotes every heading by that many levels — a popup
    quoting a snippet renders its headings smaller than the article does, so a
    two-line popup does not open with a page title. ``embeds`` maps an embed's
    name to the builder that draws it in place.
    """
    for block in blocks:
        if block.kind == 'heading':
            level = min(block.level + heading_offset, 6)
            heading = ui.element(f'h{level}').classes(f'wiz-help-h{level}')
            if block.anchor and not heading_offset:
                heading.props(f'id={block.anchor}')
            with heading:
                render_spans(block.spans)
        elif block.kind == 'para':
            with ui.element('p').classes('wiz-help-p'):
                render_spans(block.spans)
        elif block.kind in ('bullets', 'numbers'):
            _render_list(block)
        elif block.kind == 'table':
            _render_table(block)
        elif block.kind == 'image':
            _render_image(block)
        elif block.kind == 'embed' and embeds and block.name in embeds:
            embeds[block.name]()
        elif block.kind == 'note':
            with ui.element('aside').classes('wiz-help-note'):
                ui.icon('info').props('size=xs').classes('wiz-help-note-icon')
                with ui.element('span'):
                    render_spans(block.spans)


def render_user_blocks(blocks) -> None:
    """Render text a player or volunteer wrote (from ``parse_user_blocks``).

    Only paragraphs and lists are drawn; any other block kind is skipped rather
    than trusted, and every link is marked as leaving the page.
    """
    for block in blocks:
        if block.kind == 'para':
            with ui.element('p').classes('wiz-help-p'):
                render_spans(block.spans, untrusted=True)
        elif block.kind in ('bullets', 'numbers'):
            _render_list(block, untrusted=True)
