"""The Comments tab: every typed answer in one place to work through (Thomas picked B, 2026-10-28).

A question list on the left (a dropdown on a phone), then search, Unread /
Read and tag chips with counts, and the comments, newest first. Each one has
Mark read and its tags. Read marks and tags are shared by all staff and never
shown to respondents. Comment text is what people typed, drawn as plain text.
"""

from typing import Awaitable, Callable, Dict, List, Optional

from nicegui import ui

from application.services import CommentInbox, SurveyCommentService
from application.services.survey.comments import TAG_NAME_MAX, InboxComment
from models import User
from pages.admin_tabs.admin_surveys.dialogs import _footer
from theme.dialog._helpers import form_dialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error

SHOWN = 60
ALL = ''

Reload = Callable[[], Awaitable[CommentInbox]]


class CommentsTab:
    """One survey's inbox. Marks and tags change the local rows in place; adding or deleting a tag reloads."""

    def __init__(self, inbox: CommentInbox, user: User, load: Reload) -> None:
        self.inbox = inbox
        self.user = user
        self.load = load
        self.service = SurveyCommentService()
        self.state: Dict[str, object] = {'question': ALL, 'q': '', 'status': None, 'tag': None, 'shown': SHOWN}
        with ui.element('div').classes('wiz-inbox w-full'):
            self.nav = ui.refreshable(self._nav)
            self.nav()
            with ui.element('section').classes('wiz-inbox__main min-w-0'):
                self.picker = ui.refreshable(self._picker)
                self.picker()
                with ui.column().classes('wiz-inbox__tools w-full gap-2'):
                    # url-state: exempt — a search inside the inbox, not a view
                    search = ui.input(placeholder='Search comments').props('dense outlined clearable') \
                        .classes('w-full')
                    with search.add_slot('prepend'):
                        ui.icon('search').props('aria-hidden=true')
                    search._props['aria-label'] = 'Search comments'
                    search.on_value_change(lambda e: self._set(q=e.value or '', shown=SHOWN))
                    self.chips = ui.refreshable(self._chips)
                    self.chips()
                self.listing = ui.refreshable(self._listing)
                self.listing()

    # --- filtering ------------------------------------------------------

    def _in_question(self) -> List[InboxComment]:
        key = self.state['question']
        return [c for c in self.inbox.comments if not key or c.filter_key == key]

    def _found(self) -> List[InboxComment]:
        needle = str(self.state['q']).strip().lower()
        status, tag = self.state['status'], self.state['tag']
        return [c for c in self._in_question()
                if (not needle or needle in c.text.lower())
                and (status is None or c.read == (status == 'read'))
                and (tag is None or tag in c.tags)]

    def _set(self, **changes: object) -> None:
        self.state.update(changes)
        self.nav.refresh()
        self.picker.refresh()
        self.chips.refresh()
        self.listing.refresh()

    # --- question list --------------------------------------------------

    def _options(self) -> List[tuple[str, str, int]]:
        return [(ALL, 'All comments', len(self.inbox.comments))] + \
               [(q.key, q.prompt, q.count) for q in self.inbox.questions]

    def _nav(self) -> None:
        with ui.element('nav').classes('wiz-inbox__nav').props('aria-label="Comments by question"'):
            for key, label, count in self._options():
                current = self.state['question'] == key
                with ui.button(on_click=lambda _, k=key: self._set(question=k, shown=SHOWN), color=None) \
                        .props(f'flat no-caps align=left {"aria-current=true" if current else ""}') \
                        .classes('wiz-inbox__q w-full' + (' is-current' if current else '')):
                    ui.label(label).classes('wiz-inbox__q-label col')
                    ui.label(str(count)).classes('wiz-inbox__q-count')

    def _picker(self) -> None:
        options = {key: f'{label} · {count}' for key, label, count in self._options()}
        # url-state: exempt — the phone twin of the question list beside it
        picker = ui.select(options, value=self.state['question']).props('dense outlined options-dense') \
            .classes('wiz-inbox__picker w-full')
        picker._props['aria-label'] = 'Show comments for'
        with picker.add_slot('prepend'):
            ui.icon('filter_list').props('aria-hidden=true')
        picker.on_value_change(lambda e: self._set(question=e.value or ALL, shown=SHOWN))

    # --- chips ----------------------------------------------------------

    def _chip(self, label: str, picked: bool, on_click: Callable[[], None]) -> None:
        ui.button(label, on_click=on_click, color=None) \
            .props(f'dense no-caps rounded unelevated aria-pressed={"true" if picked else "false"}') \
            .classes('wiz-inbox__chip' + (' is-picked' if picked else ''))

    def _chips(self) -> None:
        here = self._in_question()
        unread = sum(1 for c in here if not c.read)
        with ui.row().classes('wiz-inbox__chips items-center gap-1 w-full'):
            for status, label, n in (('unread', 'Unread', unread), ('read', 'Read', len(here) - unread)):
                picked = self.state['status'] == status
                self._chip(f'{label} {n}', picked,
                           lambda s=status, p=picked: self._set(status=None if p else s, shown=SHOWN))
            for tag in self.inbox.tags:
                n = sum(1 for c in here if tag.id in c.tags)
                picked = self.state['tag'] == tag.id
                self._chip(f'{tag.name} {n}', picked,
                           lambda t=tag.id, p=picked: self._set(tag=None if p else t, shown=SHOWN))
            with more_menu('tags'):
                menu_action('new_label', 'New tag…', self._new_tag)
                if self.inbox.tags:
                    ui.separator()
                    menu_action('delete', 'Delete a tag…', self._delete_tag, negative=True)

    # --- the comments ---------------------------------------------------

    def _listing(self) -> None:
        found = self._found()
        if not found:
            ui.label('No comments match.' if self.inbox.comments else 'No comments yet.') \
                .classes('wiz-inbox__empty')
            return
        names = {t.id: t.name for t in self.inbox.tags}
        shown = int(self.state['shown'])  # type: ignore[call-overload]
        with ui.column().classes('wiz-inbox__list w-full gap-0'):
            for comment in found[:shown]:
                self._row(comment, names)
        if len(found) > shown:
            ui.button(f'Show {min(SHOWN, len(found) - shown)} more of {len(found) - shown}',
                      on_click=lambda: self._set(shown=shown + SHOWN)) \
                .props('flat no-caps color=primary').classes('wiz-inbox__more')

    def _row(self, comment: InboxComment, names: Dict[int, str]) -> None:
        with ui.element('article').classes('wiz-inbox__item w-full' + (' is-read' if comment.read else '')):
            with ui.row().classes('wiz-inbox__head no-wrap items-start w-full gap-2'):
                with ui.element('div').classes('col min-w-0 wiz-inbox__who'):
                    ui.label(comment.who).classes('wiz-inbox__name')
                    scope = comment.scope_label if comment.scope_label not in comment.who else ''
                    where = [scope, comment.prompt]
                    ui.label(' · '.join(w for w in where if w)).classes('wiz-inbox__where')
                if comment.read:
                    ui.button(icon='check_circle', on_click=lambda c=comment: self._mark(c, read=False)) \
                        .props('flat round dense color=positive').classes('wiz-inbox__read') \
                        .tooltip(f'Read{" by " + comment.read_by if comment.read_by else ""}. Mark unread')
                else:
                    ui.button('Mark read', on_click=lambda c=comment: self._mark(c, read=True), color=None) \
                        .props('outline no-caps dense').classes('wiz-inbox__mark')
            ui.label(comment.text).classes('wiz-inbox__text')
            with ui.row().classes('wiz-inbox__tags items-center gap-1'):
                for tag_id in comment.tags:
                    ui.label(names.get(tag_id, '')).classes('wiz-inbox__tag')
                with ui.button('Tag', icon='add', color=None).props('dense no-caps rounded flat').classes('wiz-inbox__add-tag') \
                        as button:
                    button._props['aria-label'] = f'Tags for the comment from {comment.who}'
                    with ui.menu():
                        for tag in self.inbox.tags:
                            on = tag.id in comment.tags
                            menu_action('check_box' if on else 'check_box_outline_blank', tag.name,
                                        lambda _, c=comment, t=tag.id: self._toggle(c, t))

    async def _mark(self, comment: InboxComment, *, read: bool) -> None:
        try:
            mark = await self.service.mark(self.user, self.inbox.survey.id, comment.answer_id, read=read)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        comment.read_at = mark.read_at
        comment.read_by = self.user.preferred_name if read else ''
        self.chips.refresh()
        self.listing.refresh()

    async def _toggle(self, comment: InboxComment, tag_id: int) -> None:
        tags = [t for t in comment.tags if t != tag_id] if tag_id in comment.tags else [*comment.tags, tag_id]
        try:
            mark = await self.service.mark(self.user, self.inbox.survey.id, comment.answer_id, tags=tags)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        comment.tags = list(mark.tags)
        for tag in self.inbox.tags:
            tag.count = sum(1 for c in self.inbox.comments if tag.id in c.tags)
        self.chips.refresh()
        self.listing.refresh()

    async def _reload(self) -> None:
        self.inbox = await self.load()
        self._set()

    def _new_tag(self) -> None:
        with form_dialog('New tag', card_classes='dialog-card dialog-card--sm') as dialog:
            name = ui.input('Tag name').props(f'outlined maxlength={TAG_NAME_MAX} autofocus').classes('w-full')
            ui.label('Only staff see tags.').classes('text-caption text-grey-7')

            async def save() -> None:
                try:
                    await self.service.add_tag(self.user, self.inbox.survey.id, name.value or '')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                dialog.close()
                await self._reload()

            _footer(dialog, 'Add tag', save)
        dialog.open()

    def _delete_tag(self) -> None:
        options = {t.id: f'{t.name} · {t.count}' for t in self.inbox.tags}
        with form_dialog('Delete a tag', card_classes='dialog-card dialog-card--sm') as dialog:
            pick = ui.select(options, label='Tag', value=next(iter(options), None)).props('outlined') \
                .classes('w-full')
            ui.label('Comments that carry it just lose the tag. The comments stay.') \
                .classes('text-caption text-grey-7')

            async def delete() -> None:
                if pick.value is None:
                    return
                try:
                    await self.service.delete_tag(self.user, self.inbox.survey.id, int(pick.value))
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                if self.state['tag'] == pick.value:
                    self.state['tag'] = None
                dialog.close()
                await self._reload()

            _footer(dialog, 'Delete tag', delete, negative=True)
        dialog.open()


def render_comments(inbox: CommentInbox, user: User, load: Reload) -> Optional[CommentsTab]:
    if not inbox.comments:
        ui.label('No comments yet. They show here as people send the survey.').classes('wiz-inbox__empty')
        return None
    return CommentsTab(inbox, user, load)
