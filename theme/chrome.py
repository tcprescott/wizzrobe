"""Brand chrome for the **tenant-less** surfaces (``/platform``, the MCP consent
screen).

These pages run on the bare platform host with no tenant in scope, so they
cannot use :class:`~theme.base.BaseLayout`: its drawer links into tenant routes
(``/admin``, ``/volunteer``) and its palette is resolved from the in-scope
tenant. What they still need is everything that makes a page read as Wizzrobe —
the stylesheet and its fonts, the phoenix palette, dark mode, and the header
bar with the brand mark. :func:`render_platform_chrome` is that subset,
shared so the tenant-less surfaces cannot drift apart from each other.

:func:`dark_mode_button` is shared with ``BaseLayout``'s header so the toggle
behaves identically everywhere (auto on first visit, then a sticky per-user
preference).
"""

from typing import Callable, Optional

from nicegui import app, context, ui

from theme.assets import asset_url
from theme.browser_monitoring import install_browser_monitoring
from theme.favicon import icon_links_html

_PRELOAD_FONTS = (
    'atkinson-hyperlegible-latin-400-normal',
    'atkinson-hyperlegible-latin-700-normal',
)


def install_framework_load_guard() -> None:
    """Reload once when the page's framework scripts never arrived.

    NiceGUI loads Vue as a module and Quasar as a deferred script. When one
    fetch drops (a flaky link, a phone waking up, a restart) the page stays
    blank with no error but Quasar's own ``window.Vue`` crash (SGLMAN-1E,
    SGLMAN-1R) or the language pack's ``window.Quasar is undefined``
    (SGLMAN-1J). Deferred and module scripts have all run by
    ``DOMContentLoaded``, so a missing ``Quasar`` there means the page is dead.

    A plain reload is not enough: NiceGUI marks every ``/_nicegui/`` answer
    immutable for a year, so a browser that kept a bad one reuses it on the
    reload and stays blank (SGLMAN-1S). Before the one reload per tab, every
    framework URL is fetched again with ``cache: 'reload'``, which replaces
    whatever the browser kept. If the page is still dead after that, say so on
    the page and report it.

    A browser without import maps (Safari before 16.4, SGLMAN-21/23) can never
    load NiceGUI's Vue module, and its Tailwind build fails to parse there too.
    No reload helps, so it gets told to update instead, with nothing reported.
    """
    ui.add_body_html(
        '<script>'
        "document.addEventListener('DOMContentLoaded',function(){"
        "var k='wizFrameworkReload';"
        'if(window.Quasar){try{sessionStorage.removeItem(k);}catch(e){}return;}'
        "function say(t){var p=document.createElement('p');"
        "p.setAttribute('role','alert');"
        "p.style.cssText='margin:2rem 1rem;font:16px/1.5 system-ui,sans-serif;text-align:center';"
        'p.textContent=t;document.body.prepend(p);}'
        "if(!(window.HTMLScriptElement&&HTMLScriptElement.supports&&HTMLScriptElement.supports('importmap'))){"
        "var f=document.getElementById('esm-fallback');if(f)f.remove();"
        'say("This browser is too old for this site. Update to Safari 16.4 or newer, or open this page in Firefox or Chrome.");'
        'return;}'
        'var first=false;'
        'try{if(!sessionStorage.getItem(k)){sessionStorage.setItem(k,String(Date.now()));first=true;}}catch(e){}'
        'if(first){'
        'var urls=[];'
        'function add(u){if(u&&u.indexOf(location.origin+"/")===0&&u.indexOf("/_nicegui/")>0&&urls.indexOf(u)<0)urls.push(u);}'
        "document.querySelectorAll('script[src],link[rel=modulepreload]').forEach(function(el){add(el.src||el.href);});"
        "try{var im=JSON.parse(document.querySelector('script[type=importmap]').textContent).imports||{};"
        'for(var n in im)add(new URL(im[n],location.href).href);}catch(e){}'
        'var gone=false;'
        'function go(){if(!gone){gone=true;location.reload();}}'
        'if(!window.fetch||!window.Promise){go();return;}'
        'setTimeout(go,5000);'
        "Promise.all(urls.map(function(u){return fetch(u,{cache:'reload'}).catch(function(){});})).then(go,go);"
        'return;}'
        "say(\"This page didn't finish loading. Check your connection, then reload.\");"
        "if(window.Sentry&&Sentry.captureMessage)Sentry.captureMessage('Framework scripts failed to load after a reload','warning');"
        '});'
        '</script>'
    )


def install_page_scripts() -> None:
    """The per-page browser scripts every Wizzrobe page shell installs."""
    install_timezone_detection()
    install_framework_load_guard()


def install_timezone_detection() -> None:
    """Report the device's IANA timezone to the server on a cookie.

    The server renders times before any websocket exists, so the zone has to
    arrive with the HTTP request itself — a cookie, not a ``run_javascript``
    round-trip. This snippet writes it on every load (cheap and idempotent), and
    ``TenantMiddleware`` reads it back into the request's timezone context.

    On a browser's **first** visit there is no cookie yet, so that one page
    renders on the community's default clock and then reloads once to pick up
    the real zone. The reload is guarded twice over: ``sessionStorage`` caps it
    at one per tab even when cookies are blocked outright (otherwise the missing
    cookie would look like a first visit forever), and the window flag stops a
    double-fire within a single load. A zone that merely *changed* — someone
    travelled — updates the cookie silently and takes effect on the next
    navigation, because a surprise reload mid-session is worse than a stale
    clock for a few seconds.

    Ignored entirely when the community pins a timezone: the cookie is still
    written, but resolution never reads it.

    The browser's preferred clock rides the same snippet on ``wiz_hc`` (``12`` or
    ``24``). It never reloads on its own: on a first visit the zone's reload
    carries it, and a browser that already had a zone cookie picks it up on its
    next navigation. A saved Profile choice outranks it.
    """
    ui.add_body_html(
        '<script>'
        'if(!window.__wizTzInstalled){'
        'window.__wizTzInstalled=true;'
        'try{'
        "var o=new Intl.DateTimeFormat([],{hour:'numeric'}).resolvedOptions();"
        "var h12=o.hour12!==undefined?o.hour12:/h1[12]/.test(o.hourCycle||'');"
        "document.cookie='wiz_hc='+(h12?'12':'24')+';path=/;max-age=31536000;samesite=Lax';"
        'var tz=Intl.DateTimeFormat().resolvedOptions().timeZone;'
        'if(tz){'
        "var m=document.cookie.match(/(?:^|;\\s*)wiz_tz=([^;]*)/);"
        'var cur=m?decodeURIComponent(m[1]):null;'
        'if(cur!==tz){'
        "document.cookie='wiz_tz='+encodeURIComponent(tz)+"
        "';path=/;max-age=31536000;samesite=Lax';"
        "if(!m&&!sessionStorage.getItem('wizTzReload')){"
        "sessionStorage.setItem('wizTzReload','1');location.reload();}"
        '}}}catch(e){}'
        '}'
        '</script>'
    )


def apply_brand_palette(colors: dict[str, str]) -> None:
    """Point Quasar's palette and the ``--wiz-*`` brand vars at ``colors``.

    Shared by every surface that paints itself Wizzrobe — the tenant layout, the
    platform chrome, and the auth wait screens — so a community's colours reach
    all of them and none of them drift. Semantic status colours stay fixed,
    warm-tuned to the ``--status-*`` tokens in styles.css.
    """
    ui.colors(
        primary=colors['primary'],
        secondary=colors['secondary'],
        accent=colors['accent'],
        positive='#557A1F',
        negative='#B3362B',
        warning='#B45309',
        info='#0E7470',
    )
    # Loaded after the stylesheet so it wins. The dark text-tint rules mirror the !important defaults in styles.css.
    ui.add_head_html(
        '<style>'
        ':root{'
        f'--wiz-gold-deep:{colors["primary"]};'
        f'--wiz-gold:{colors["accent"]};'
        f'--wiz-ember-deep:{colors["secondary"]};'
        f'--wiz-ember:{colors["secondary"]};'
        f'--wiz-header-bg:{colors["header"]};'
        '}'
        '.body--dark .text-primary,.q-dark .text-primary{color:var(--wiz-gold)!important;}'
        '.body--dark .text-secondary,.q-dark .text-secondary{color:var(--wiz-ember)!important;}'
        '</style>'
    )
    ui.add_head_html(icon_links_html(colors))
    logo = colors.get('logo')
    if logo:
        # Safe inside url(): the URL is service-built from an int id and a hex hash.
        ui.add_head_html(f'<style>:root{{--wiz-brand-logo:url("{logo}");}}</style>')
        ui.query('body').classes('wiz-has-logo')


def _dark_cookie_js(dark: bool) -> str:
    """Mirror the choice into a cookie the cached static pages read in the browser.

    Those pages (``/live`` in static mode, the static brackets) are shared by
    every reader and never see the session, so this is how they follow the
    toggle.
    """
    return f"document.cookie='wiz_dark={1 if dark else 0}; path=/; max-age=31536000; samesite=lax';"


def cookie_dark_pref() -> Optional[bool]:
    """The choice a reader made on a static page, which has no session to keep it in."""
    try:
        value = context.client.request.cookies.get('wiz_dark')
    except Exception:
        return None
    return {'1': True, '0': False}.get(value or '')


def dark_mode_button(dark: ui.dark_mode) -> ui.button:
    """The header's dark-mode toggle, bound to ``dark`` and the user's session.

    The initial glyph reflects the stored preference — ``brightness_auto`` when
    there is none, because the client is following its own system theme and the
    button has not yet chosen for it.
    """
    dark_pref = app.storage.user.get('dark_mode')
    if dark_pref is not None:
        ui.add_head_html(f'<script>{_dark_cookie_js(dark_pref)}</script>')
    ref: dict = {'btn': None}

    def toggle() -> None:
        dark.value = not dark.value
        app.storage.user['dark_mode'] = dark.value
        ui.run_javascript(_dark_cookie_js(dark.value))
        if ref['btn'] is not None:
            ref['btn'].props(f"icon={'light_mode' if dark.value else 'dark_mode'}")
            ref['btn'].update()

    icon = (
        'brightness_auto' if dark_pref is None
        else 'light_mode' if dark_pref
        else 'dark_mode'
    )
    ref['btn'] = ui.button(icon=icon, on_click=toggle, color=None).props('flat') \
        .tooltip('Toggle dark mode')
    return ref['btn']


def render_platform_chrome(
    subtitle: Optional[str] = None,
    *,
    right: Optional[Callable[[], None]] = None,
) -> None:
    """Apply the Wizzrobe stylesheet, palette and header bar to a tenant-less page.

    Args:
        subtitle: Rendered after the wordmark as "Wizzrobe · <subtitle>" to name
            the surface (e.g. ``'Platform'``).
        right: Optional callable rendering extra header content before the
            dark-mode toggle, in the right-hand slot.
    """
    dark = ui.dark_mode(app.storage.user.get('dark_mode'))
    for font_file in _PRELOAD_FONTS:
        ui.add_head_html(
            f'<link rel="preload" href="/static/fonts/{font_file}.woff2" '
            'as="font" type="font/woff2" crossorigin>'
        )
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">')
    ui.add_head_html('<meta name="robots" content="noindex, nofollow">')
    # The same scripts BaseLayout loads; keep the two in step.
    ui.add_head_html(f'<script src="{asset_url("js/table-columns.js")}"></script>')
    ui.add_head_html(f'<script defer src="{asset_url("js/a11y.js")}"></script>')
    ui.add_head_html(f'<script defer src="{asset_url("js/busy.js")}"></script>')
    ui.add_head_html(f'<script defer src="{asset_url("js/dialog-keyboard.js")}"></script>')
    ui.add_head_html(f'<script defer src="{asset_url("js/anchors.js")}"></script>')
    install_page_scripts()
    install_browser_monitoring()
    # Shipped defaults, never a tenant override: the consent screen grants a credential not scoped to one community.
    from application.services.tenant_theme_service import DEFAULT_THEME
    apply_brand_palette(DEFAULT_THEME)
    with ui.header().classes(replace='row items-center no-wrap wiz-header'):
        ui.label('Wizzrobe').classes('wiz-wordmark')
        if subtitle:
            ui.label(f'· {subtitle}').classes('wiz-wordmark wiz-wordmark-sub')
        ui.space()
        if right is not None:
            right()
        dark_mode_button(dark)
