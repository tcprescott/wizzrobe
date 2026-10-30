import json
import logging
import re

from nicegui import app, context, ui

from application.table_preferences_context import table_prefs_scope
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.tenant_urls import login_path
from models import FeatureFlag, User
from theme.archived_banner import (
    READ_ONLY_CLASS,
    archived_view,
    banner_shown,
    bind_read_only,
    page_shows_event,
    read_only_scope,
    render_archived_banner,
    tab_shows_event,
    watch_event_status,
)
from theme.assets import asset_url
from theme.browser_monitoring import install_browser_monitoring
from theme.chrome import apply_brand_palette, cookie_dark_pref, dark_mode_button, install_page_scripts
from theme.connection import install_connection_watch
from theme.drawer_help import render_drawer_help, render_github_item
from theme.drawer_nav import render_drawer_nav
from theme.event_switcher import EventSwitcherState, load_event_switcher, render_event_switcher
from theme.links import set_link
from theme.nav_badges import bottom_nav_tab
from theme.notice import drain_notice
from theme.page_header import heading_fallback
from theme.url_state import PUSH_ATTR, History, url_state
from theme.usage_telemetry import install_usage_capture, record_tab_view
from theme.waiting import waiting_panel

logger = logging.getLogger(__name__)

TAB_HISTORY: History = 'push'

DRAWER_WIDTH_DESKTOP = 248
DRAWER_WIDTH_PHONE_MAX = 340
DRAWER_WIDTH_JS = (
    'Quasar.Screen.lt.md'
    f' ? Math.min(Math.round(Quasar.Screen.width * 0.85), {DRAWER_WIDTH_PHONE_MAX})'
    f' : {DRAWER_WIDTH_DESKTOP}'
)

HOME_SECTIONS: tuple[tuple[str, str], ...] = (
    ('Event', 'stadium'),
    ('My Schedule', 'event_available'),
    ('Tournaments', 'emoji_events'),
    ('Profile', 'account_circle'),
)


def current_local_url() -> str:
    """The tenant-local path and query of the page being built, or ``/``.

    ``TenantMiddleware`` has already moved ``/t/<slug>`` into ``root_path``, so
    the request's own path is the tenant-local one a sign-in button carries as
    ``?next=``.
    """
    try:
        url = context.client.request.url
    except Exception:
        return '/'
    return f'{url.path}?{url.query}' if url.query else url.path


def tab_slug(label: str) -> str:
    """Derive a URL path segment from a tab's display label.

    Lowercases and collapses each run of non-alphanumeric characters to a
    single hyphen: ``"Vol. Roster"`` → ``vol-roster``, ``"On Air"`` → ``on-air``.
    Auto-derived slugs are collision-free within each page's label set; a tab
    dict may override with an explicit ``'slug'`` key if ever needed.
    """
    return re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')


class BaseLayout:
    """Base layout component providing header, drawer, footer, and tabbed content structure."""

    def __init__(
        self,
        copyright_text: str | None = None,
        section: str | None = None,
        base_path: str | None = None,
        tabs: list | None = None,
        user: User | None = None,
        show_admin: bool | None = None,
        show_volunteer: bool | None = None,
        wordmark: str | None = None,
        chromeless: bool = False,
        **_kwargs
    ):
        self._copyright = copyright_text if copyright_text is not None else "© 2026 Thomas Prescott"
        self._chromeless = chromeless
        self.tabs = tabs
        self.user = user
        self.dark_mode = None
        self._wordmark = wordmark
        self._theme_colors: dict[str, str] | None = None

        self._drawer = None
        self._tab_panels = None
        self._bottom_tabs = None
        self._bottom_tab_labels: list = []
        self._syncing_nav = False
        self._tab_item_refs: dict = {}
        self._section_buttons: dict = {}
        self._section_lists: dict = {}
        self._section_host: ui.element | None = None
        self._shown_tab: str | None = None
        self._section = section
        self._tab_containers: dict = {}
        self._pending_tabs: dict = {}
        # Rebound around deferred tab builds, which run with no tenant contextvar.
        self._tenant_id = get_current_tenant_id()
        self._table_prefs: dict[str, dict] = {}
        self._base_path = base_path

        # None resolves from the user's access in render(); an explicit bool skips the DB for the sync render_chrome() path.
        # show_admin: only an explicit True is trusted, a False is still resolved.
        self._show_admin = show_admin
        self._show_volunteer = show_volunteer
        self._show_feedback = False
        self._show_event_info = False
        self._show_qualifiers = False
        self._show_check_in = False
        self._show_proctor = False
        self._is_member = False
        self.events = EventSwitcherState()
        self._prepared = False
        self.top_menu: list[dict] = []

        if tabs:
            self._slug_by_label = {t['label']: t.get('slug') or tab_slug(t['label']) for t in tabs}
            self._label_by_slug = {slug: label for label, slug in self._slug_by_label.items()}
            # A canonical slug always wins, so an alias can never shadow a live tab.
            self._label_by_alias = {
                alias: t['label']
                for t in tabs
                for alias in t.get('aliases', ())
                if alias not in self._label_by_slug
            }
            self._default_tab = (
                self._label_by_slug.get(section)
                or self._label_by_alias.get(section)
                or tabs[0]['label']
            )
        else:
            self._slug_by_label = {}
            self._label_by_slug = {}
            self._label_by_alias = {}
            self._default_tab = None

    @property
    def wordmark(self) -> str:
        """The header's brand text: the community's name once prepared, else Wizzrobe."""
        return self._wordmark or 'Wizzrobe'

    @property
    def is_prepared(self) -> bool:
        """Whether :meth:`prepare` has resolved the viewer-dependent chrome."""
        return self._prepared

    @property
    def is_member(self) -> bool:
        """Whether the viewer belongs to this community (or is a super-admin).

        ``False`` until :meth:`prepare` has run.
        """
        return self._is_member

    @property
    def offers_feedback(self) -> bool:
        """Whether this viewer may send this community feedback here.

        A member where ``FEEDBACK`` is live — the same test the
        drawer's item uses, so every Feedback affordance agrees with it.
        """
        return bool(self.user) and self._is_member and self._show_feedback

    async def prepare(self) -> 'BaseLayout':
        """Resolve everything the chrome needs that takes a query. Idempotent.

        Split from :meth:`render` for the surfaces that draw the frame
        synchronously — the join door and the error pages — so they can still
        carry the community's name, palette and drawer: resolve here in the
        async caller, then hand the layout to the synchronous renderer.
        """
        if self._prepared:
            return self
        self._prepared = True
        from application.services import AuthService, FeatureFlagService, TenantService
        if self._wordmark is None:
            self._wordmark = (await TenantService.current_community_name()) or 'Wizzrobe'
        if self._show_volunteer is None:
            self._show_volunteer = await AuthService.can_view_volunteer(self.user)
        if self._show_admin is not True and self.user is not None and self._tenant_id is not None:
            self._show_admin = await AuthService.can_open_admin(self.user)
        if self.user is not None and self._tenant_id is not None:
            joined = await TenantService.is_member(self.user.id, self._tenant_id)
            self._is_member = joined or await AuthService.is_super_admin(self.user)
            if joined:
                self._show_feedback = await FeatureFlagService().is_enabled(FeatureFlag.FEEDBACK)
        if self._tenant_id is not None:
            self._show_event_info = await FeatureFlagService().is_enabled(FeatureFlag.EVENT_INFO)
        if self._is_member:
            self._show_qualifiers = await FeatureFlagService().is_enabled(
                FeatureFlag.ASYNC_QUALIFIERS,
            )
        if self._is_member:
            self._show_check_in = await AuthService.can_view_check_in_desk(self.user)
            self._show_proctor = await AuthService.can_open_proctor_station(self.user)
        if self._tenant_id is not None and not self._chromeless:
            self.events = await load_event_switcher(self.user, self._tenant_id)
        await self._load_theme_colors()
        return self

    async def render(self) -> None:
        """Render the complete layout with header, drawer, footer, and optional tabbed content."""
        drain_notice()
        await self.prepare()
        # Loaded once here: customize_table reads it back synchronously and can't await.
        from application.services import TablePreferenceService
        self._table_prefs = await TablePreferenceService().prime(self.user)
        with table_prefs_scope(self._table_prefs):
            self.render_chrome()
            if self._is_member and not self._chromeless:
                from theme.profile_nudge_banner import render_profile_nudge_banner
                await render_profile_nudge_banner(self.user)
                if self._show_volunteer:
                    from theme.shift_arrival_banner import render_shift_arrival_banner
                    await render_shift_arrival_banner(self.user)
            watch_event_status(self.events.current)
            if self.tabs:
                await self._render_tab_panels()
            elif page_shows_event():
                view = archived_view(self.events, self.user)
                bind_read_only(view)
                if view is not None:
                    ui.query('body').classes(add=READ_ONLY_CLASS)

    async def _load_theme_colors(self) -> None:
        """Resolve the in-scope tenant's brand palette before drawing chrome.

        Best-effort and non-raising (the platform surface has no tenant); the
        service returns the shipped defaults when there is no tenant or no
        override. Imported lazily to keep this presentation module import-light.
        """
        from application.services import TenantThemeService
        self._theme_colors = await TenantThemeService.get_current_theme()

    def _build_top_menu(self) -> None:
        """Assemble the header/drawer nav. Deferred out of ``__init__`` so
        ``render`` can resolve Volunteer visibility (an async access check) first."""
        self.top_menu = [{'label': 'Home', 'icon': 'home', 'url': '/'}]
        if self._tenant_id is not None:
            self.top_menu.append({'label': 'Watch', 'icon': 'live_tv', 'url': '/live'})
        if self._show_volunteer or self._show_proctor or self._show_check_in:
            self.top_menu.append({'label': 'Volunteer', 'icon': 'volunteer_activism', 'url': '/volunteer'})
        if self._show_admin:
            self.top_menu.append({'label': 'Admin', 'icon': 'admin_panel_settings', 'url': '/admin'})

    def render_chrome(self) -> None:
        """Render the synchronous page frame (palette, header, drawer, footer).

        Split out from :meth:`render` so callers that cannot await — notably the
        synchronous ``on_page_exception`` error path — can still get the full
        themed chrome. Tab panels (the only async part) are rendered by
        :meth:`render`.
        """
        self._build_top_menu()
        from application.services.tenant_theme_service import DEFAULT_THEME
        colors = self._theme_colors or DEFAULT_THEME

        dark_pref = app.storage.user.get('dark_mode')
        if dark_pref is None:
            dark_pref = cookie_dark_pref()
        self.dark_mode = ui.dark_mode(dark_pref)
        for font_file in (
            'atkinson-hyperlegible-latin-400-normal',
            'atkinson-hyperlegible-latin-700-normal',
            'ibm-plex-mono-latin-400-normal',
        ):
            ui.add_head_html(
                f'<link rel="preload" href="/static/fonts/{font_file}.woff2" '
                'as="font" type="font/woff2" crossorigin>'
            )
        ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">')
        ui.add_head_html('<meta name="robots" content="noindex, nofollow">')
        # Must be served from the site root so the worker's scope covers start_url '/'.
        ui.add_head_html('<link rel="manifest" href="/static/manifest.webmanifest">')
        ui.add_head_html(
            f'<meta name="theme-color" content="{colors["header"]}" media="(prefers-color-scheme: light)">'
        )
        ui.add_head_html(
            '<meta name="theme-color" content="#17120D" media="(prefers-color-scheme: dark)">'
        )
        ui.add_head_html(
            '<script>'
            "if('serviceWorker' in navigator){"
            # updateViaCache:'none': the default lets the HTTP cache pin an old web-push-common.js.
            "navigator.serviceWorker.register('/sw.js',{updateViaCache:'none'})"
            '.catch(()=>{});'
            '}'
            '</script>'
        )
        ui.add_head_html(
            '<script>'
            'console.log('
            '"%c\U0001f3a2 Wizzrobe%c — did you know? '
            'Cats have been known to ride roller coasters purely for the airtime.",'
            '"color:#E0A82E;font-weight:bold;font-size:14px",'
            '"color:#9C6B12"'
            ');'
            '</script>'
        )
        main = context.client.content.parent_slot.parent
        main.props('tabindex=-1')
        ui.add_body_html(
            f'<a class="wiz-skip-link" href="#{main.html_id}" onclick="'
            f"var m=document.getElementById('{main.html_id}');"
            "if(m){event.preventDefault();m.focus();}"
            '">Skip to content</a>'
        )
        # The guard stops the listener being installed twice across renders.
        ui.add_body_html(
            '<script>'
            'if(!window.__wizzrobe_konami_installed){'
            'window.__wizzrobe_konami_installed=true;'
            "const seq=['ArrowUp','ArrowUp','ArrowDown','ArrowDown',"
            "'ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];"
            'let pos=0;'
            "window.addEventListener('keydown',function(e){"
            # Autofill and some iOS keyboards fire keydown with no key.
            "if(typeof e.key!=='string'||e.isComposing)return;"
            'const key=e.key.length===1?e.key.toLowerCase():e.key;'
            'if(key===seq[pos]){pos++;'
            "if(pos===seq.length){pos=0;emitEvent('wiz_konami',{});}}"
            'else{pos=(key===seq[0])?1:0;}'
            '});'
            '}'
            '</script>'
        )
        ui.on('wiz_konami', lambda _: self._open_fact())
        install_page_scripts()
        install_connection_watch()
        # In the head: a lazy tab build's <script> never runs.
        ui.add_head_html(f'<script src="{asset_url("js/table-columns.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/a11y.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/busy.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/dialog-keyboard.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/timeline.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/survey-drag.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/anchors.js")}"></script>')
        install_usage_capture(self._tenant_id)
        install_browser_monitoring()
        apply_brand_palette(colors)
        if self._chromeless:
            with ui.page_sticky(position='top-right', x_offset=12, y_offset=12):
                dark_mode_button(self.dark_mode).props('flat color=primary')
            return
        self._render_header()
        self._render_drawer()
        self._render_footer()

    def _open_fact(self) -> None:
        from theme.dialog import FactDialog
        FactDialog().open()

    def _render_header(self) -> None:
        """Render the header with burger menu button and user controls."""
        with ui.header().classes(replace='row items-center no-wrap wiz-header'):
            ui.button(
                icon='menu',
                on_click=lambda: self._drawer.toggle(),
                color=None,
            ).props('flat aria-label="Menu"').classes('wiz-burger')

            wordmark_taps = {'n': 0}

            def on_wordmark_tap():
                from theme.dialog import FactDialog
                wordmark_taps['n'] += 1
                if wordmark_taps['n'] >= 5:
                    wordmark_taps['n'] = 0
                    FactDialog().open()

            ui.label(self._wordmark or 'Wizzrobe').classes('wiz-wordmark').on('click', on_wordmark_tap)
            render_event_switcher(self)

            # Must be a real element, not margin-left:auto on the name: the name is display:none on phones.
            ui.space()

            if self.user:
                ui.label(self.user.preferred_name).classes('text-lg user-name')
                avatar_url = app.storage.user.get('avatar')
                if avatar_url:
                    ui.image(avatar_url).props(
                        'width=32 height=32 fit=cover round'
                    ).classes('user-avatar')
                else:
                    # ui.image(None) renders a broken image and a console error.
                    ui.icon('account_circle').classes('user-avatar user-avatar-placeholder')
                # A real link: an iPhone Home Screen web app ignored the tap when it went through the server.
                logout_btn = ui.button(icon='logout', color=None).props('flat').tooltip('Log out')
                set_link(logout_btn, '/logout', new_tab=False)
            else:
                # Label is a child element, not the text prop, so .login-button-text can hide it without fighting Quasar's .block.
                target = login_path(current_local_url()) if self._tenant_id is not None else '/login'
                login_btn = ui.button(icon='login', color=None).props('flat').classes('login-button')
                set_link(login_btn, target, new_tab=False)
                with login_btn:
                    ui.label('Login with Discord').classes('login-button-text')
                login_btn.tooltip('Login with Discord')

            dark_mode_button(self.dark_mode)

    def _render_drawer(self) -> None:
        """Render the left drawer with navigation links and optional tab navigation."""
        # Width is computed client-side, not overridden in CSS: the prop also sets the closed offset, so CSS leaves the drawer peeking out.
        with ui.left_drawer(value=False).props(
            'breakpoint=1023 show-if-above bordered '
            f':width="{DRAWER_WIDTH_JS}"'
        ) as self._drawer:
            current_path = current_local_url().split('?', 1)[0]
            render_drawer_nav(self, current_path)

            ui.space()
            if self._tenant_id is not None:
                render_drawer_help(self)
            else:
                with ui.list().props('padding'):
                    render_github_item()

            ui.label(self._copyright).classes('text-caption q-px-md q-pb-md wiz-drawer-copyright')

    def _switch_tab(self, label: str) -> None:
        # Only move the panel: _handle_tab_change does highlight and history so every entry point behaves the same.
        self._tab_panels.set_value(label)

    async def _handle_tab_change(self) -> None:
        """Single sink for panel-value changes: drawer item or bottom tab.
        Back/Forward go through :meth:`_show_slug`, which shows the tab first."""
        label = self._tab_panels.value
        if label == self._shown_tab:
            return
        await self._show_tab(label, history=TAB_HISTORY)

    async def _show_slug(self, slug: str) -> None:
        """Back or Forward landed on the tab ``slug`` names (none: the first)."""
        label = self._label_by_slug.get(slug) or self._label_by_alias.get(slug) or self.tabs[0]['label']
        if label == self._shown_tab:
            return
        self._shown_tab = label
        self._tab_panels.set_value(label)  # type: ignore[attr-defined]
        await self._show_tab(label, history=None)

    async def _show_tab(self, label: str, *, history: 'History | None') -> None:
        """Highlights, address and title, then build or refresh the tab.
        ``history=None`` when Back/Forward did it and the address has moved."""
        self._shown_tab = label
        for lbl, item in self._tab_item_refs.items():
            if lbl == label:
                item.props(add='active')
            else:
                item.props(remove='active')
        self._sync_bottom_nav(label)
        if self._base_path is not None:
            url_state().show_tab(label, f'{self._base_path}/{self._slug_by_label[label]}', history=history)
            record_tab_view(self._base_path, self._slug_by_label[label])
        # Not on first build: the panel loads its own data, so broadcasting too would fetch twice.
        if await self._build_tab(label):
            if history is not None:
                url_state().sync()
            return
        # Emitted client-side via emitEvent so e.args is the plain label string.
        ui.run_javascript(f"emitEvent('selected_tab', {json.dumps(label)})")

    def mark_tab_push(self, element: ui.element, label: str) -> None:
        """Let a click on ``element`` push ``label``'s address itself (``PUSH_ATTR``)."""
        slug = self._slug_by_label.get(label)
        if self._base_path is not None and slug:
            element.props[PUSH_ATTR] = f'{self._base_path}/{slug}'

    def _sync_bottom_nav(self, label: str) -> None:
        """Highlight the active tab in the bottom nav.

        The bar only exists where every tab is in it (see ``_render_footer``), so
        the label always has a matching child and there is no not-in-the-bar case
        to fall back from."""
        if self._bottom_tabs is None:
            return
        # Guard: setting the bottom tab's value re-fires its own change handler.
        self._syncing_nav = True
        self._bottom_tabs.set_value(label)
        self._syncing_nav = False

    def _on_bottom_tab(self) -> None:
        """A bottom-nav tab was tapped: drive the panel (which then routes through
        _handle_tab_change for the highlight/URL sync). Ignored while we are
        programmatically syncing the highlight to avoid a feedback loop."""
        if self._syncing_nav:
            return
        value = self._bottom_tabs.value
        if value and value != self._tab_panels.value:
            self._tab_panels.set_value(value)

    BOTTOM_NAV_CAPACITY = 4

    def _render_footer(self) -> None:
        """Render the app-shell bottom navigation for phones/tablets (<1024px).

        **Only when every tab fits.** The bar used to show `tabs[:4]` and hide the
        rest behind a **More** button, which meant a hub with more tabs than the
        bar holds — Admin has twenty-seven — spent 72px of phone screen offering
        four of them chosen by list order, plus a button that is the drawer with
        an extra tap. Where the nav does not fit, the drawer is the honest answer
        and the header's burger opens it.

        Where it does fit, the bar is worth its space: Home's four are the tabs a
        player switches between constantly, and a thumb reaches the bottom of a
        phone far more easily than the top-left corner.

        The footer is *only* the bottom nav — copyright and Feedback live in the
        drawer — so a page that does not qualify renders no footer at all rather
        than an empty strip.
        """
        if not self.tabs or len(self.tabs) < 2 or len(self.tabs) > self.BOTTOM_NAV_CAPACITY:
            return

        # Only on pages that render the bar, or styles.css reserves bottom-nav space for nothing.
        ui.query('body').classes(add='wiz-has-bottom-nav')

        with ui.footer().classes('q-py-xs q-px-md footer-dark-override'):
            self._bottom_tab_labels = [tab['label'] for tab in self.tabs]
            # url-state: exempt — the hub tabs, which BaseLayout keeps in the address itself
            with ui.tabs(value=self._default_tab).props('dense no-caps').classes('wiz-bottom-nav') as self._bottom_tabs:
                for tab in self.tabs:
                    self.mark_tab_push(bottom_nav_tab(tab), tab['label'])
            self._bottom_tabs.on_value_change(self._on_bottom_tab)

    @staticmethod
    async def _render_tab_content(tab: dict) -> None:
        """Invoke one tab's content callable, sync or async, with its optional
        ``(func, args, kwargs)`` tuple form."""
        import inspect

        content = tab['content']
        if isinstance(content, tuple):
            content_func = content[0]
            args = content[1] if len(content) > 1 and content[1] is not None else ()
            kwargs = content[2] if len(content) > 2 and content[2] is not None else {}
        else:
            content_func = content
            args = ()
            kwargs = {}
        if inspect.iscoroutinefunction(content_func):
            await content_func(*args, **kwargs)
        else:
            content_func(*args, **kwargs)

    async def _build_tab(self, label: str, *, deferred: bool = True) -> bool:
        """Build a tab's content the first time it is shown. Returns whether it
        built now (``False`` means it was already built, or the label is unknown).

        The tenant captured at page-build time is rebound around the build: a
        deferred build runs inside a websocket event handler, where the tenant
        contextvar is unset and only the client stash would answer — the same
        hazard, and the same fix, as ``admin_crud.wire_tab_refresh``.

        ``deferred`` distinguishes a post-load build (gets a waiting panel,
        because the user is sitting in front of it) from the initial in-page
        build of the visible tab.
        """
        tab = self._pending_tabs.pop(label, None)
        if tab is None:
            return False
        container = self._tab_containers[label]
        panel = None
        if deferred:
            with container:
                panel = waiting_panel('Loading…')
        view = archived_view(self.events, self.user) if tab_shows_event(tab) else None
        if view is not None:
            container.classes(add=READ_ONLY_CLASS)
        try:
            with container:
                with tenant_scope(self._tenant_id), table_prefs_scope(self._table_prefs), \
                        read_only_scope(view), heading_fallback(label):
                    await self._render_tab_content(tab)
                    if view is not None and not banner_shown():
                        shown = render_archived_banner()
                        if shown is not None:
                            shown.move(target_index=0)
        except Exception:
            logger.exception('Failed to build tab %r', label)
            with container:
                ui.label('This section failed to load. Try switching tabs again.') \
                    .classes('text-negative q-ma-md')
        finally:
            if panel is not None and not panel.is_deleted:
                container.remove(panel)
        return True

    async def _render_tab_panels(self) -> None:
        """Build the panel shells, then fill in only the visible one.

        Every ``ui.tab_panel`` container is created up front so panel switching,
        and deep-linked sections keep working, but a tab's *content* is
        built the first time that tab is shown. The home page carries up to nine
        tabs and a viewer sees one, so eager rendering spent ~47% of the page's
        render CPU on panels nobody looked at
        (docs/scaling-roadmap.md).
        """
        with ui.tab_panels(value=self._default_tab).props('animated').classes(
            'w-full'
        ) as self._tab_panels:
            for tab in self.tabs:
                with ui.tab_panel(tab['label']):
                    self._tab_containers[tab['label']] = ui.row().classes('full-width')
                self._pending_tabs[tab['label']] = tab

        state = url_state()
        state.bind_hub(self._base_path, self._section, self._default_tab,
                       self._slug_by_label[self._default_tab], self._tab_containers, self._show_slug)
        self._shown_tab = self._default_tab
        await self._build_tab(self._default_tab, deferred=False)
        record_tab_view(self._base_path, self._slug_by_label.get(self._default_tab))
        state.sync(keep_foreign=True)

        # Registered after construction so the deep-linked initial value doesn't fire it.
        # Bottom nav synced by hand: bind_value would force a value with no child when the tab is behind More.
        self._tab_panels.on_value_change(self._handle_tab_change)
