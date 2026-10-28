"""The comments behind one question, in a side sheet (a bottom sheet on a phone).

Opened from a report line's "45 comments ›" or a comparison table's count, so
the report keeps its place underneath. Comments are what respondents typed,
drawn as plain text, newest first, with a search and one chip per group.
"""

from collections import Counter
from typing import Dict, List, Optional

from nicegui import ui

from application.services.survey.results import CommentRow
from theme.dialog._helpers import dialog_header

SHOWN = 100


class CommentSheet:
    """One sheet per results view; :meth:`open` fills it with a question's comments."""

    def __init__(self) -> None:
        self.comments: List[CommentRow] = []
        self.state: Dict[str, Optional[str]] = {'q': '', 'group': None}
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet wiz-survey-sheet wiz-comment-sheet'):
            self.title = dialog_header('Comments', self.dialog)
            # url-state: exempt — a search inside a sheet, not a view
            self.search = ui.input(placeholder='Search comments').props('dense outlined clearable') \
                .classes('w-full wiz-comment-sheet__search')
            self.search.on_value_change(lambda e: self._set(q=e.value or ''))
            self.chips = ui.refreshable(self._chips)
            self.chips()
            self.listing = ui.refreshable(self._listing)
            self.listing()

    def open(self, prompt: str, comments: List[CommentRow], *, group: Optional[str] = None) -> None:
        self.comments = comments
        self.state.update(q='', group=group)
        self.title.set_text(prompt)
        self.search.set_value('')
        self.search._props['aria-label'] = f'Search answers to {prompt}'
        self.search.set_visibility(len(comments) > 3)
        self.chips.refresh()
        self.listing.refresh()
        self.dialog.open()

    def _set(self, **changes: Optional[str]) -> None:
        self.state.update(changes)
        self.chips.refresh()
        self.listing.refresh()

    def _chips(self) -> None:
        counts = Counter(c.group for c in self.comments if c.group)
        if len(counts) < 2:
            return
        with ui.row().classes('wiz-comment-sheet__chips gap-1'):
            for label, n in [(None, len(self.comments)), *sorted(counts.items(), key=lambda kv: kv[0].lower())]:
                picked = self.state['group'] == label
                ui.button(f'{label or "All"} · {n}', on_click=lambda _, g=label: self._set(group=g)) \
                    .props(f'dense no-caps rounded {"unelevated color=primary" if picked else "outline"} '
                           f'aria-pressed={"true" if picked else "false"}')

    def _listing(self) -> None:
        needle = (self.state['q'] or '').strip().lower()
        found = [c for c in self.comments
                 if (not self.state['group'] or c.group == self.state['group'])
                 and (not needle or needle in c.text.lower())]
        if not found:
            ui.label('Nothing matches.' if self.comments else 'No comments yet.') \
                .classes('wiz-survey-result__meta q-pa-md')
            return
        with ui.column().classes('wiz-survey-comments wiz-comment-sheet__list w-full gap-0'):
            for comment in found[:SHOWN]:
                with ui.column().classes('wiz-survey-comment gap-0 w-full'):
                    who = comment.who + (f' · {comment.group}' if comment.group and not
                                         comment.who.startswith('Anonymous ·') else '')
                    ui.label(who).classes('wiz-survey-comment__who')
                    ui.label(comment.text).classes('wiz-survey-comment__text')
            if len(found) > SHOWN:
                ui.label(f'and {len(found) - SHOWN} more. Search to narrow them, '
                         'or read them all over REST or MCP.').classes('wiz-survey-result__meta')
