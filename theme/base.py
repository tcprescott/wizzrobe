import json
import logging
import re

from nicegui import app, context, ui

from application.table_preferences_context import table_prefs_scope
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.tenant_urls import login_path
from models import FeatureFlag, User
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

#: How a hub tab switch enters the browser's history: ``'push'`` makes Back undo it.
TAB_HISTORY: History = 'push'

DRAWER_WIDTH_DESKTOP = 248
DRAWER_WIDTH_PHONE_MAX = 340
DRAWER_WIDTH_JS = (
    'Quasar.Screen.lt.md'
    f' ? Math.min(Math.round(Quasar.Screen.width * 0.85), {DRAWER_WIDTH_PHONE_MAX})'
    f' : {DRAWER_WIDTH_DESKTOP}'
)

#: Home's four sections as ``(label, icon)``. Home builds its tabs from these,
#: and a tab-less page (a bracket, an article) offers them in the drawer to a
#: member, so the way back to their own matches is never more than one tap.
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
        # Optional in fact as well as in the default: every public page
        # (the bracket views, /help) renders chrome for an anonymous visitor,
        # and the error page does too.
        user: User | None = None,
        show_admin: bool | None = None,
        show_volunteer: bool | None = None,
        wordmark: str | None = None,
        chromeless: bool = False,
        **_kwargs
    ):
        self._copyright = copyright_text if copyright_text is not None else "© 2026 Thomas Prescott"
        # Kiosk mode: palette, fonts and page-level scripts, no header/drawer/footer.
        # A shared room PC has nowhere to navigate to and nobody to sign in, so the
        # chrome is screen space spent on controls that do nothing there.
        self._chromeless = chromeless
        self.tabs = tabs
        self.user = user
        self.dark_mode = None
        # Header wordmark: the in-scope community's own name on a tenant surface,
        # 'Wizzrobe' on the platform. Resolved from the ambient tenant in render()
        # when a caller does not pass one explicitly.
        self._wordmark = wordmark
        # Resolved per-tenant brand palette, loaded in render() before chrome is
        # drawn. None on the synchronous error path (render_chrome without
        # render), where render_chrome falls back to the shipped defaults.
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
        # Lazy tab rendering: every panel gets a container up front, but its
        # content is built on first show. `_pending_tabs` holds the not-yet-built
        # tabs; a label leaves it exactly once, when its content is built.
        self._tab_containers: dict = {}
        self._pending_tabs: dict = {}
        # Captured at page-build time (the request context) and rebound around a
        # deferred build, which runs in an event handler with no tenant contextvar.
        self._tenant_id = get_current_tenant_id()
        # This viewer's saved table layouts, loaded once in render() and rebound
        # the same way. Empty until then, which means "the shipped columns".
        self._table_prefs: dict[str, dict] = {}
        # Base URL (including any /t/<slug> root_path) that section slugs hang
        # off of, e.g. '/t/foo/admin'. None for tab-less pages, which never
        # rewrite the URL.
        self._base_path = base_path

        # Nav visibility. `show_volunteer=None` (the default) means "resolve it
        # from the user's access" in render(), which is what every tenant page
        # wants — the link must not be offered where the tenant has the feature
        # off or the user holds none of the volunteer roles, or it dead-ends on a
        # 404/403. An explicit bool is honoured as-is, which keeps the synchronous
        # render_chrome() path (the error page) working without a DB round-trip.
        # Admin follows the same rule from AuthService.can_open_admin, which
        # mirrors the admin page's tab gates. Only an explicit True is taken as
        # given (the admin page itself): a False is resolved anyway, because
        # callers used to pass can_view_admin, which leaves out the online-play
        # roles that do have an Admin tab.
        self._show_admin = show_admin
        self._show_volunteer = show_volunteer
        # Same shape as _show_volunteer: resolved in render() because the drawer
        # must not offer a Feedback item whose dialog the service would refuse.
        # False on the synchronous render_chrome() path (the error page), which
        # has no DB round-trip to spend and no need for the link.
        self._show_feedback = False
        # Same shape again, but resolved for signed-out visitors too: /event-info
        # is a public page, and someone who has not signed in is squarely who it
        # is written for.
        self._show_event_info = False
        # Members only, for the same reason: /qualifiers runs the membership gate.
        self._show_qualifiers = False
        # The desk, whenever check-in is on, and Proctor Station, which a proctor
        # keeps with VOLUNTEERS off. False on the error path, like Feedback.
        self._show_check_in = False
        self._show_proctor = False
        # Whether the viewer belongs to this community (or is a super-admin).
        # Gates what only a member can use: Feedback, whose submit the service
        # refuses to anyone else, and the home sections on a tab-less page.
        self._is_member = False
        # The event the page shows, and the events the header's switcher lists
        # (resolved in prepare()). An empty list draws no switcher.
        self.events = EventSwitcherState()
        self._prepared = False
        self.top_menu: list[dict] = []

        if tabs:
            # Sections are addressed in the URL by slug; internally everything
            # keys off the display label, so keep a slug↔label map for read-in
            # (path → default tab) and write-out (tab change → path).
            self._slug_by_label = {t['label']: t.get('slug') or tab_slug(t['label']) for t in tabs}
            self._label_by_slug = {slug: label for label, slug in self._slug_by_label.items()}
            # Retired slugs that must still resolve. A tab dict lists them under
            # 'aliases'; they answer on read-in only, so the URL normalizes to the
            # canonical slug on the first tab switch and nothing new is minted
            # against a name we no longer publish. A canonical slug always wins,
            # so an alias can never shadow a live tab.
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
            # Feedback is for people who joined; a super-admin joins first, as
            # for every other sign-up (the service refuses them otherwise).
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
        # A message stashed before a redirect (theme/notice.py) is shown on the
        # first framed page the browser lands on, whichever that turns out to be.
        drain_notice()
        await self.prepare()
        # One query for every table on the page. Bound for the rest of the build
        # so each customize_table call reads it back synchronously — a table
        # build cannot await, and a page can host a dozen of them.
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
            if self.tabs:
                await self._render_tab_panels()

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
        # The desk is a Volunteer tab, so desk workers get Volunteer too.
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
        # Per-tenant brand palette (loaded in render(); shipped defaults on the
        # synchronous error path or the tenant-less platform surface).
        from application.services.tenant_theme_service import DEFAULT_THEME
        colors = self._theme_colors or DEFAULT_THEME

        dark_pref = app.storage.user.get('dark_mode')  # None ⇒ auto: match the client's system theme
        if dark_pref is None:
            dark_pref = cookie_dark_pref()
        self.dark_mode = ui.dark_mode(dark_pref)
        # Preload only the above-the-fold fonts; the remaining weights load on demand.
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
        # Paired with the site-wide robots.txt (frontend.py): the signed-out
        # surfaces are shareable by link, not published to search engines, and
        # robots.txt alone does not deindex a URL a crawler already knows.
        ui.add_head_html('<meta name="robots" content="noindex, nofollow">')
        # PWA wiring: the manifest makes the app installable, the two media-scoped
        # theme-color metas tint the mobile browser/status-bar chrome to the gold
        # header (light) / charcoal (dark) palette; the tab and iOS home-screen
        # icons come with the palette (apply_brand_palette). The service worker
        # is registered from the site root (/sw.js, served in frontend.py) so its
        # scope covers start_url '/'.
        ui.add_head_html('<link rel="manifest" href="/static/manifest.webmanifest">')
        # Light-mode browser/status-bar tint follows the tenant's header colour;
        # dark mode keeps the charcoal bar (the header stays charcoal in dark).
        ui.add_head_html(
            f'<meta name="theme-color" content="{colors["header"]}" media="(prefers-color-scheme: light)">'
        )
        ui.add_head_html(
            '<meta name="theme-color" content="#17120D" media="(prefers-color-scheme: dark)">'
        )
        ui.add_head_html(
            '<script>'
            "if('serviceWorker' in navigator){"
            # updateViaCache:'none' — the default ('imports') lets the HTTP cache
            # answer for the worker's importScripts, which would pin an old
            # web-push-common.js against a current worker.
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
        # First tab stop on every page: without it a keyboard user tabs through
        # the header and the whole drawer before reaching the content (WCAG 2.4.1).
        main = context.client.content.parent_slot.parent  # Quasar's <main class="q-page">
        main.props('tabindex=-1')
        ui.add_body_html(
            f'<a class="wiz-skip-link" href="#{main.html_id}" onclick="'
            f"var m=document.getElementById('{main.html_id}');"
            "if(m){event.preventDefault();m.focus();}"
            '">Skip to content</a>'
        )
        # Konami code (↑↑↓↓←→←→BA) opens a hidden fact dialog. The guard keeps
        # the window listener from being installed more than once across renders.
        ui.add_body_html(
            '<script>'
            'if(!window.__wizzrobe_konami_installed){'
            'window.__wizzrobe_konami_installed=true;'
            "const seq=['ArrowUp','ArrowUp','ArrowDown','ArrowDown',"
            "'ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];"
            'let pos=0;'
            "window.addEventListener('keydown',function(e){"
            # Autofill and some iOS keyboards fire keydown with no key (SGLMAN-2C).
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
        # A dropped socket eats a click silently; this makes the page say so.
        install_connection_watch()
        # Drag-to-resize table headers; in the head because a lazy tab build's <script> never runs.
        ui.add_head_html(f'<script src="{asset_url("js/table-columns.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/a11y.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/busy.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/dialog-keyboard.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/timeline.js")}"></script>')
        ui.add_head_html(f'<script defer src="{asset_url("js/anchors.js")}"></script>')
        install_usage_capture(self._tenant_id)
        install_browser_monitoring()
        # Phoenix palette (gold primary, ember secondary), overridable per tenant via TenantThemeService.
        apply_brand_palette(colors)
        if self._chromeless:
            # The one control a kiosk still needs: a room's lighting is not the
            # viewer's system theme, and there is no header left to hang the
            # toggle on. Floated in the corner.
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

            # Flexible spacer that pushes the user/login controls to the right
            # edge. It must be a real element rather than margin-left:auto on the
            # name/login button, because the name is display:none on phones — the
            # spacer keeps the alignment identical to desktop in every state.
            ui.space()

            if self.user:
                ui.label(self.user.preferred_name).classes('text-lg user-name')
                avatar_url = app.storage.user.get('avatar')
                if avatar_url:
                    ui.image(avatar_url).props(
                        'width=32 height=32 fit=cover round'
                    ).classes('user-avatar')
                else:
                    # No avatar URL (mock-login sessions, or Discord users on the
                    # default avatar): show a placeholder glyph instead of an empty
                    # ui.image(None), which also renders a broken image + console error.
                    ui.icon('account_circle').classes('user-avatar user-avatar-placeholder')
                # A real link: a Home Screen web app on an iPhone ignored the tap
                # when it went through the server (see theme.links).
                logout_btn = ui.button(icon='logout', color=None).props('flat').tooltip('Log out')
                set_link(logout_btn, '/logout', new_tab=False)
            else:
                # The full-text "Login with Discord" button also lives in the page
                # body; here the label is a child element (not the q-btn text prop)
                # so it can be hidden on phones via .login-button-text without
                # fighting Quasar's `.block` utility. The tooltip keeps the
                # icon-only mobile button labelled for accessibility.
                # Carries the page it was pressed on, so signing in from a
                # bracket or an article comes back to it rather than to home.
                # Off any community there is nowhere local to come back to.
                target = login_path(current_local_url()) if self._tenant_id is not None else '/login'
                login_btn = ui.button(icon='login', color=None).props('flat').classes('login-button')
                set_link(login_btn, target, new_tab=False)
                with login_btn:
                    ui.label('Login with Discord').classes('login-button-text')
                login_btn.tooltip('Login with Discord')

            dark_mode_button(self.dark_mode)

    def _render_drawer(self) -> None:
        """Render the left drawer with navigation links and optional tab navigation."""
        # Unify the drawer's auto-show boundary with the app-shell <1024px break:
        # below it the bottom nav + burger carry navigation, at/above it the drawer
        # pins open (show-if-above). This matches the grid-card table breakpoint.
        # NiceGUI renders the drawer body (.q-drawer__content) as a flex column,
        # so a trailing ui.space() pushes the copyright to the foot of the drawer
        # when the nav fits, and it falls to the scroll-end when the nav is long.
        # Pinned on desktop it is 248px; as the phone overlay it takes 85% of the
        # screen (up to 340px) so rows are thumb-sized and a sliver of the page
        # still shows behind it. Quasar's width prop is a pixel number and also
        # sets the closed drawer's offset, so it's computed client-side rather
        # than overridden in CSS, which would leave the closed drawer peeking out.
        with ui.left_drawer(value=False).props(
            'breakpoint=1023 show-if-above bordered '
            f':width="{DRAWER_WIDTH_JS}"'
        ) as self._drawer:
            current_path = current_local_url().split('?', 1)[0]
            render_drawer_nav(self, current_path)

            # The info card and the copyright sit at the foot of the drawer
            # when the nav fits, and fall to its scroll-end when it is long.
            ui.space()
            if self._tenant_id is not None:
                render_drawer_help(self)
            else:
                with ui.list().props('padding'):
                    render_github_item()

            ui.label(self._copyright).classes('text-caption q-px-md q-pb-md wiz-drawer-copyright')

    def _switch_tab(self, label: str) -> None:
        # Only move the panel; the drawer highlight and the /base/<slug> history
        # replace are handled centrally by _handle_tab_change so every entry point
        # (drawer, bottom nav) produces identical side effects and no loops.
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
        # Done before the (possibly awaited) first build so the address lands
        # promptly. A switch loads no page, hence the explicit tab-view telemetry.
        if self._base_path is not None:
            url_state().show_tab(label, f'{self._base_path}/{self._slug_by_label[label]}', history=history)
            record_tab_view(self._base_path, self._slug_by_label[label])
        # First visit builds the panel, which loads its own data — broadcasting
        # ``selected_tab`` as well would make it fetch twice. Afterwards the panel
        # already exists, so the broadcast is what refreshes it.
        if await self._build_tab(label):
            if history is not None:
                url_state().sync()
            return
        # Notify the newly-shown tab so it can refresh its data. Tabs listen via
        # ``ui.on('selected_tab', ...)`` (see theme/tables/admin_crud.wire_tab_refresh
        # and the per-tab wiring in pages/admin_tabs/*); every listener filters on its
        # own label, so a single broadcast reaches exactly the active tab. Emitted
        # client-side via the global emitEvent so e.args is the plain label string.
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

    # A bottom bar can hold about this many tabs before the labels stop being
    # readable at 390px. It is also, not coincidentally, the number Home was cut
    # down to.
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
        # One tab (a desk-only Volunteer hub) has nowhere to switch to.
        if not self.tabs or len(self.tabs) < 2 or len(self.tabs) > self.BOTTOM_NAV_CAPACITY:
            return

        # Tells styles.css this page actually reserves bottom-nav clearance —
        # see the --wiz-bottom-nav-h comment there. Without it, a page with no bar
        # (a tab-less one like /help, or a hub whose nav does not fit) still
        # reserves 72px of dead space under the reconnect banner/popup and the
        # refresh FAB, for a nav that never renders.
        ui.query('body').classes(add='wiz-has-bottom-nav')

        with ui.footer().classes('q-py-xs q-px-md footer-dark-override'):
            self._bottom_tab_labels = [tab['label'] for tab in self.tabs]
            # Every tab is in the bar, so the deep-linked default always has a
            # matching child — no empty value to bind and no Quasar warning.
            # url-state: exempt — the hub tabs, which BaseLayout keeps in the address itself
            with ui.tabs(value=self._default_tab).props('dense no-caps').classes('wiz-bottom-nav') as self._bottom_tabs:
                for tab in self.tabs:
                    self.mark_tab_push(bottom_nav_tab(tab), tab['label'])
            # Tapping a bottom tab drives the panel; the highlight/URL sync then
            # flows back through _handle_tab_change.
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
        try:
            with container:
                with tenant_scope(self._tenant_id), table_prefs_scope(self._table_prefs), \
                        heading_fallback(label):
                    await self._render_tab_content(tab)
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
        # The address and title name the tab and any view it opened on.
        state.sync(keep_foreign=True)

        # Panel changes (drawer click or bottom-tab tap) all route through
        # _handle_tab_change, which syncs both highlights and the URL. Registered
        # after construction so the initial deep-linked value does not fire it (no
        # spurious history entry). The bottom nav is kept in sync manually rather
        # than via bind_value, because a two-way bind would force the tab model to a
        # value with no matching child whenever the active tab lives behind More.
        self._tab_panels.on_value_change(self._handle_tab_change)
