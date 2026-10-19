"""The static bracket documents — page shell, and the two whole-page renderers.

Assembles :mod:`.markup` and :mod:`.data_tables` into complete HTML documents for
the cached public views registered in ``pages/static_brackets.py``. The shell is
deliberately tiny: the app stylesheets for the shared bracket look, a small
layout sheet, and no client framework at all — the Quasar/NiceGUI payload (and
the socket behind it) is exactly what this view exists to avoid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Mapping, Optional

from application.utils.timezone import format_local_display, to_local
from models import Bracket, BracketEntry, BracketFormat, BracketMatch, BracketState
from theme.assets import asset_url
from theme.favicon import icon_links_html

from ..labels import format_label, stage_label, state_label
from ..render import build_context
from .data_tables import _round_robin_html, _swiss_html
from .markup import _elimination_2d_html, _elimination_list_html, _esc, _tag


@dataclass
class StaticBracketView:
    """Everything the static bracket document renders, already loaded."""

    bracket: Bracket
    tournament_name: str
    entries: List[BracketEntry]
    matches: List[BracketMatch]
    entry_name: Dict[int, str]
    # entry_id -> Discord avatar URL (``theme.brackets.render.entry_avatars``).
    entry_avatar: Dict[int, str] = field(default_factory=dict)
    live_state: Dict[int, dict] = field(default_factory=dict)
    advancement: Optional[dict] = None
    generated_at: Optional[datetime] = None
    # Path prefix carrying any /t/<slug> root_path, so links work in path mode.
    root_path: str = ''
    primary_color: Optional[str] = None
    # The community's theme (TenantThemeService), for its logo icons.
    icons: Dict[str, str] = field(default_factory=dict)
    refresh_seconds: int = 60


@dataclass
class StaticIndexView:
    """The per-tournament stage index, static twin of ``/tournament/{id}/brackets``."""

    tournament_id: int
    tournament_name: str
    brackets: List[Bracket]
    generated_at: Optional[datetime] = None
    root_path: str = ''
    primary_color: Optional[str] = None
    icons: Dict[str, str] = field(default_factory=dict)
    refresh_seconds: int = 300


# The static views load the app stylesheets for the shared bracket look, plus
# this small shell — the interactive pages get their frame from Quasar, which is
# exactly the payload (and the socket behind it) this view exists to avoid.
_SHELL_CSS = """
*, *::before, *::after { box-sizing: border-box; }
/* Every text colour routes through the --bracket-* properties brackets.css
   already flips on .body--dark, so the shell follows the reader's theme
   without a second palette to keep in sync. */
body { margin: 0; color: var(--bracket-text, #1f2328); }
a { color: var(--wiz-link, #9C6B12); }
.wizs-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline;
  justify-content: space-between; padding: 12px 16px;
  background: var(--surface, #fff); color: var(--fg, #1c1b19);
  border-bottom: 1px solid var(--border-subtle, #e2e1de); }
.wizs-bar a { color: var(--wiz-link, #9C6B12); }
.wizs-bar .wizs-sub { color: var(--fg-muted, #6f6d67); }
.wizs-brand { display: inline-flex; align-items: center; gap: 8px; font-weight: 700; }
.wizs-brand::before { content: ''; width: 16px; height: 16px; border-radius: 5px;
  background: var(--wiz-header-bg, #9C6B12); }
.wizs-wrap { max-width: 1400px; margin: 0 auto; padding: 16px; }
.wizs-card { background: var(--bracket-card-bg, #fff);
  border: 1px solid var(--bracket-card-border, #e2e5ea); border-radius: 8px;
  padding: 16px; margin-bottom: 16px; }
.wizs-title { font-size: 1.4rem; font-weight: 700; margin: 0 0 4px;
  color: var(--bracket-text, #1f2328); }
.wizs-sub { font-size: .8rem; color: var(--bracket-muted, #6b7280); }
.wizs-muted { font-size: .8rem; color: var(--bracket-muted, #6b7280); }
.wizs-line { font-size: .8rem; padding: 1px 0; }
.wizs-badge { display: inline-block; border-radius: 10px; padding: 1px 9px;
  font-size: .72rem; font-weight: 600; background: var(--bracket-seed-bg, #eef0f3);
  color: var(--bracket-text, #1f2328); }
.wizs-details { margin: 6px 0; }
/* The interactive view lays these out with a ui.row; the static twin has no
   Quasar flex helpers, so the meta strips get their own rule. */
.bracket-header-meta, .bracket-round-meta { display: flex; gap: 6px;
  align-items: center; flex-wrap: wrap; }
.wizs-summary { cursor: pointer; font-size: .82rem; font-weight: 600;
  padding: 6px 4px; color: var(--bracket-text, #1f2328); }
.wizs-stage { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  justify-content: space-between; border: 1px solid var(--bracket-card-border, #e2e5ea);
  border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; }
.wizs-foot { padding: 8px 16px 28px; font-size: .75rem;
  color: var(--bracket-muted, #6b7280); text-align: center; }
"""

# Hover-run highlight, the one behaviour worth 300 bytes of vanilla JS: it needs
# no socket, no framework and no state, and it is what makes a large bracket
# readable. Everything else in the static view is served pre-rendered. The theme
# follows the ``wiz_dark`` cookie the app's dark-mode toggle writes
# (``theme.chrome.dark_mode_button``), else the reader's system theme.
_HOVER_JS = (
    "<script>(function(){var t=function(on){return function(e){"
    "var el=e.target.closest?e.target.closest('[data-entrant-id]'):null;if(!el)return;"
    "var id=el.getAttribute('data-entrant-id');if(!id)return;"
    "var ns=document.querySelectorAll('.bracket-slot[data-entrant-id=\"'+id+'\"]');"
    "for(var i=0;i<ns.length;i++){ns[i].classList.toggle('run-highlight',on);}};};"
    "document.addEventListener('mouseover',t(true),true);"
    "document.addEventListener('mouseout',t(false),true);"
    "var c=document.cookie.match(/(?:^|; )wiz_dark=([01])/);"
    "if(c?c[1]==='1':(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){"
    "document.body.classList.add('body--dark');}})();</script>"
)


_HEX_COLOR = re.compile(r'#[0-9a-fA-F]{6}')


def _document(
    *,
    title: str,
    body: str,
    refresh_seconds: int,
    primary_color: Optional[str],
    generated_at: Optional[datetime],
    footer_links: str = '',
    description: str = '',
    icons: Optional[Mapping[str, str]] = None,
) -> str:
    # HTML escaping does nothing inside <style>, so the colour is re-checked
    # here rather than trusting every caller to have validated it.
    if primary_color and not _HEX_COLOR.fullmatch(primary_color):
        primary_color = None
    palette = (
        f'<style>body{{--q-primary:{_esc(primary_color)};'
        f'--wiz-header-bg:{_esc(primary_color)};}}</style>'
        if primary_color else ''
    )
    stamp = (
        f'Updated {_esc(format_local_display(to_local(generated_at)))}'
        if generated_at else ''
    )
    refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if refresh_seconds else ''
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'{refresh}'
        f'<title>{_esc(title)}</title>'
        # What a link unfurl (Discord, chat apps) shows: these pages are the
        # link staff paste into a channel, and a bare <title> is all it had.
        f'<meta property="og:title" content="{_esc(title)}">'
        + (f'<meta property="og:description" content="{_esc(description)}">' if description else '')
        + icon_links_html(icons)
        + f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">'
        f'<link rel="stylesheet" href="{asset_url("css/brackets.css")}">'
        f'<style>{_SHELL_CSS}</style>{palette}'
        '</head><body>'
        f'{body}'
        f'<div class="wizs-foot">{stamp}'
        + (f' · this page reloads itself every {int(refresh_seconds)}s' if refresh_seconds else '')
        + (f' · {footer_links}' if footer_links else '')
        + '</div>'
        f'{_HOVER_JS}'
        '</body></html>'
    )


def _bar(tournament_name: str, right: str) -> str:
    return _tag(
        'div', 'wizs-bar',
        _tag('div', 'wizs-brand', _esc(tournament_name)) + _tag('div', 'wizs-sub', right),
    )


def render_bracket_document(view: StaticBracketView) -> str:
    """The whole static bracket page for one stage."""
    bracket = view.bracket
    entries, matches = view.entries, view.matches
    entry_name = view.entry_name
    root = view.root_path

    # No config_summary here: the stage's rules as configured are a check for
    # staff, and this page is for spectators (the interactive page shows them
    # to staff only, for the same reason).
    header = _tag(
        'div', 'wizs-card',
        _tag('div', 'wizs-title', _esc(bracket.name))
        + _tag('div', 'wizs-sub', _esc(
            f'{view.tournament_name} · {stage_label(bracket.stage_order)} · '
            f'{format_label(bracket.format)}'))
        + _tag('div', 'wizs-sub', _tag('span', 'wizs-badge', _esc(state_label(bracket.state)))),
    )

    if bracket.state == BracketState.DRAFT or not matches:
        seeded = ''
        if entries:
            rows = ''.join(
                _tag('div', 'wizs-line', _esc(
                    (f'#{e.seed} · ' if e.seed is not None else '')
                    + entry_name.get(e.id, 'Unknown')))
                for e in entries
            )
            seeded = _tag('div', 'bracket-section-title', 'Seeded entrants') + rows
        body = header + _tag(
            'div', 'wizs-card',
            _tag('div', 'wizs-muted', 'This stage has not started yet.') + seeded,
        )
    elif bracket.format in (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM):
        double = bracket.format == BracketFormat.DOUBLE_ELIM
        ctx = build_context(
            bracket.config, entries, matches, entry_name,
            entry_avatar=view.entry_avatar, live_state=view.live_state,
        )
        body = header + _tag(
            'div', 'wizs-card',
            _tag('div', 'bracket-2d', _elimination_2d_html(matches, ctx, double=double))
            + _tag(
                'div', 'bracket-mobile-list',
                _elimination_list_html(matches, ctx, double=double),
            ),
        )
    else:
        complete = bracket.state == BracketState.COMPLETE
        if bracket.format == BracketFormat.ROUND_ROBIN:
            inner = _round_robin_html(
                entries, matches, entry_name, bracket.config,
                advancement=view.advancement, complete=complete,
                entry_avatar=view.entry_avatar,
            )
        else:
            inner = _swiss_html(
                entries, matches, entry_name, bracket.config,
                advancement=view.advancement, complete=complete,
                entry_avatar=view.entry_avatar,
            )
        body = header + _tag('div', 'wizs-card', inner)

    index_url = f'{root}/live/tournament/{bracket.tournament_id}/brackets'
    bar = _bar(
        view.tournament_name,
        f'<a href="{_esc(index_url)}">All stages</a>',
    )
    return _document(
        title=f'{bracket.name} — {view.tournament_name}',
        description=(
            f'{stage_label(bracket.stage_order)} · {format_label(bracket.format)} · '
            f'{state_label(bracket.state)}'
        ),
        body=bar + _tag('div', 'wizs-wrap', body),
        refresh_seconds=view.refresh_seconds,
        primary_color=view.primary_color,
        icons=view.icons,
        generated_at=view.generated_at,
        footer_links=(
            f'<a href="{_esc(root)}/brackets/{_esc(bracket.id)}">interactive view</a>'
        ),
    )


def render_index_document(view: StaticIndexView) -> str:
    """The static stage index for one tournament."""
    root = view.root_path
    if not view.brackets:
        inner = _tag(
            'div', 'wizs-muted',
            'No brackets have been published for this tournament.',
        )
    else:
        rows = []
        for b in view.brackets:
            rows.append(_tag(
                'div', 'wizs-stage',
                _tag(
                    'div', 'wizs-stage-main',
                    _tag('div', '', f'<strong>{_esc(b.name)}</strong>')
                    + _tag('div', 'wizs-sub', _esc(
                        f'{stage_label(b.stage_order)} · {format_label(b.format)}')),
                )
                + _tag(
                    'div', 'wizs-stage-side',
                    _tag('span', 'wizs-badge', _esc(state_label(b.state)))
                    + f' <a href="{_esc(root)}/live/brackets/{_esc(b.id)}">View</a>',
                ),
            ))
        inner = ''.join(rows)

    body = _tag(
        'div', 'wizs-wrap',
        _tag(
            'div', 'wizs-card',
            _tag('div', 'wizs-title', _esc(f'{view.tournament_name} — Brackets')) + inner,
        ),
    )
    return _document(
        title=f'{view.tournament_name} — Brackets',
        description=f'{len(view.brackets)} stage(s) · live bracket',
        body=_bar(view.tournament_name, 'Bracket stages') + body,
        refresh_seconds=view.refresh_seconds,
        primary_color=view.primary_color,
        icons=view.icons,
        generated_at=view.generated_at,
        footer_links=(
            f'<a href="{_esc(root)}/tournament/{_esc(view.tournament_id)}/brackets">'
            'interactive view</a>'
        ),
    )


def render_not_found_document(
    *, root_path: str = '', community: str = '', primary_color: Optional[str] = None,
    icons: Optional[Mapping[str, str]] = None,
) -> str:
    """The static twin of the app's 404: same words, a way back, no framework.

    These routes answer without NiceGUI, so they cannot render the themed error
    page — but a spectator who followed a stale link should still get a page
    that says what happened and where to go, not a line of plain text.
    """
    from theme.error_page import NOT_FOUND_HEADLINE, NOT_FOUND_MESSAGE

    body = _tag(
        'div', 'wizs-wrap',
        _tag(
            'div', 'wizs-card',
            _tag('div', 'wizs-title', _esc(NOT_FOUND_HEADLINE))
            + _tag('p', '', _esc(NOT_FOUND_MESSAGE))
            + f'<a href="{_esc(root_path)}/">Back to home</a>',
        ),
    )
    return _document(
        title=f'{NOT_FOUND_HEADLINE} — {community or "Wizzrobe"}',
        body=_bar(community or 'Wizzrobe', '') + body,
        refresh_seconds=0,
        primary_color=primary_color,
        icons=icons,
        generated_at=None,
    )
