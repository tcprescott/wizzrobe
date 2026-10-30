"""The one help button a heading or dialog title carries.

Every surface gets at most one, always the same round "?", always at the end of
its heading row (in a dialog, beside the close button). It used to be up to six
icons and labelled chips per section, in three different looks, scattered
beside individual controls; on a phone the chips wrapped to two rows above the
content they explained.

A hover tooltip is unreachable on a touch screen, and most of the people this
help is for are reading it on a phone in a loud room. So this is a real button
with a ``q-menu``: it opens on tap and on click alike, stays open until
dismissed, and its content is selectable. With one topic the menu shows that
snippet; with several it lists them by name and each opens in place, with Back
to the list. On a phone the menu becomes a bottom sheet.

The snippet it shows is a region of an article, not a second copy of the words —
the popup and the article cannot drift apart, and "Read more" deep-links to the
heading the region sits under.

**It resolves across both article sections.** Help owns the app mechanics; the
per-community event handbook owns the floor procedure that used to sit in the
player and proctor articles. An icon names a snippet, not a section, so moving a
region from one to the other keeps every icon working — the popup simply starts
linking to ``/event-info/…`` instead of ``/help/…``.
"""

from dataclasses import dataclass
from typing import Optional, Sequence, Union

from nicegui import app, ui

from application.content import article_sections
from application.help import HelpSnippet, all_articles, get_article, get_snippet
from application.services import (
    EventInfoService,
    FeatureFlagService,
    HelpService,
    get_user_from_session,
)
from models import FeatureFlag, User
from theme.help.render import render_blocks

__all__ = ['HelpTopic', 'ResolvedHelp', 'help_button', 'help_icon', 'resolve_help']

HelpTopic = Union[str, tuple[str, str]]


async def _event_info_snippet(snippet_name: str, user: Optional[User], user_given: bool):
    """Look ``snippet_name`` up in this community's handbook, or ``None``.

    Resolves the flag here rather than letting the service raise: this helper is
    called from surfaces that are *not* themselves gated on ``EVENT_INFO``, and a
    community without the feature must get a missing snippet, not an exception
    that takes the page down.
    """
    if FeatureFlag.EVENT_INFO not in await FeatureFlagService().enabled_flags():
        return None
    if not user_given:
        user = await get_user_from_session(app.storage.user)
    return await EventInfoService.get_snippet(snippet_name, user)


def _gated(article) -> bool:
    return article is not None and HelpService.for_staff(article)


def _depends_on_the_reader(snippet_name: str) -> bool:
    """Whether a help snippet reads differently for different viewers.

    True when its own article is staff- or role-gated, when it links into
    ``/event-info`` (handbook pages can be role-gated), or when it links into
    ``/help`` while some help article is gated. Content only, no query.
    """
    raw = get_snippet(snippet_name)
    if raw is None:
        return False
    if _gated(get_article(raw.article_slug)):
        return True
    sections = article_sections(raw.blocks)
    if '/event-info' in sections:
        return True
    return '/help' in sections and any(_gated(article) for article in all_articles())


@dataclass(frozen=True)
class ResolvedHelp:
    """A topic this viewer can read, with the name the menu shows for it."""

    label: str
    snippet: HelpSnippet


async def _resolve_one(snippet_name: str, user: Optional[User]) -> Optional[HelpSnippet]:
    viewer = user
    if viewer is None and _depends_on_the_reader(snippet_name):
        # Judged as signed out, staff snippets would vanish for staff.
        viewer = await get_user_from_session(app.storage.user)
    snippet = await HelpService.get_snippet(snippet_name, viewer)
    if snippet is None:
        snippet = await _event_info_snippet(snippet_name, viewer, viewer is not None)
    return snippet


async def resolve_help(
    topics: Sequence[HelpTopic], *, user: Optional[User] = None,
) -> list[ResolvedHelp]:
    """The topics in ``topics`` this viewer can read, in order.

    For a sync header (``dialog_header``, ``form_dialog``) that takes the
    result as ``help=``. Drops a topic whose snippet does not exist, whose
    article is gated off for this community, or whose roles the reader lacks:
    a menu entry that opens nothing is worse than no entry, and a missing
    snippet is an authoring slip that should not break a page.

    Pass ``user`` from any surface that already has one. It only matters for a
    role-gated snippet, and without it each one looks the viewer up itself.
    """
    resolved: list[ResolvedHelp] = []
    for topic in topics:
        name, label = topic if isinstance(topic, tuple) else (topic, '')
        snippet = await _resolve_one(name, user)
        if snippet is not None:
            resolved.append(ResolvedHelp(label or snippet.heading or snippet.article_title, snippet))
    return resolved


def _snippet_body(snippet: HelpSnippet) -> None:
    # Block flow, not flex: a height-capped flex parent shrinks its children instead of letting the popup scroll.
    with ui.element('div').classes('wiz-help-prose'):
        render_blocks(snippet.blocks, heading_offset=2)
    ui.link('Read more', snippet.href).classes('wiz-help-more')


def _menu_header(menu, title: str, back=None):
    """Title row of the help popover: an optional Back, the title, and Close."""
    with ui.row().classes('wiz-help-menu-head no-wrap items-center'):
        if back is not None:
            ui.button('Help', icon='chevron_left', on_click=back, color=None) \
                .props('flat dense no-caps aria-label="Back to help topics"').classes('wiz-help-back')
        ui.label(title).classes('wiz-help-menu-title')
        ui.button('Close', icon='close', on_click=menu.close, color=None) \
            .props('flat dense no-caps').classes('dialog-close')


def _topic_menu(menu, topics: Sequence[ResolvedHelp]) -> None:
    """Several topics: a list of names, each opening its snippet in place.

    One view at a time rather than accordions: an expanded section inside a
    capped popup was what left the text a word or two wide beside a scrollbar.
    """
    views: dict = {}

    def show(target) -> None:
        for view in views.values():
            view.set_visibility(view is target)

    with ui.column().classes('wiz-help-view') as index:
        views[None] = index
        _menu_header(menu, 'Help')
        with ui.column().classes('wiz-help-menu-body wiz-help-topic-list'):
            for i, topic in enumerate(topics):
                ui.button(topic.label, color=None, on_click=lambda i=i: show(views[i])) \
                    .props('flat no-caps align=between icon-right=chevron_right') \
                    .classes('wiz-help-topic')
    for i, topic in enumerate(topics):
        with ui.column().classes('wiz-help-view') as page:
            views[i] = page
            _menu_header(menu, topic.label, back=lambda: show(index))
            with ui.column().classes('wiz-help-menu-body'):
                _snippet_body(topic.snippet)
        page.set_visibility(False)

    menu.on('hide', lambda: show(index))


def help_button(topics: Sequence[ResolvedHelp], *, pill: bool = False) -> None:
    """Render the "?" for already-resolved ``topics``, or nothing if there are none.

    Rendered inside a heading row it moves itself to the row's far end (CSS
    ``order``), so a caller can add actions after it without reordering.

    ``pill`` is the dialog-header form: a labelled "Help" pill on desktop and a
    disc on a phone, the same shape as the Close button beside it.

    It opens a popover with a fixed reading width on desktop; below 600px the
    same menu is restyled as a bottom sheet, like the app's phone dialogs.
    """
    if not topics:
        return
    button = (
        ui.button('Help', icon='help_outline', color=None)
        .props('flat dense no-caps').classes('wiz-help-icon dialog-help')
        if pill else
        ui.button(icon='help_outline').props('flat round dense aria-label=Help')
        .classes('wiz-help-icon')
    )
    with button:
        with ui.menu().props('anchor="bottom end" self="top end" :offset="[0, 6]"') \
                .classes('wiz-help-menu') as menu:
            if len(topics) == 1:
                _menu_header(menu, topics[0].label)
                with ui.column().classes('wiz-help-menu-body'):
                    _snippet_body(topics[0].snippet)
            else:
                _topic_menu(menu, topics)


async def help_icon(*topics: HelpTopic, user: Optional[User] = None) -> None:
    """Resolve ``topics`` and render one "?" for them: the async shorthand.

    One call per heading. Several topics go in the same call and become one menu;
    two buttons on one heading is the clutter this replaced.
    """
    help_button(await resolve_help(topics, user=user))
