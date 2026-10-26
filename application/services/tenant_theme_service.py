"""Tenant Theme Service — per-tenant brand colours over the shipped palette.

The app ships a fixed "Phoenix" palette (gold/ember) defined in ``theme/base.py``
and ``static/css/styles.css``. A community's STAFF may override a small set of
**brand** colours — primary, secondary, accent, and the header bar — which the
layout applies on top of the shipped defaults (Quasar colours + a handful of
``--wiz-*`` CSS variables). Semantic status colours (positive/negative/warning/
info) stay fixed so notifications and error states read consistently everywhere.

Storage is ``Tenant.config['theme']`` — no dedicated columns (see
``models/tenant.py``; the ``config`` JSONField is the documented home for
per-tenant knobs). Reads merge any stored overrides onto the defaults so a caller
always gets a full palette; writes are STAFF-gated, validated to 6-digit hex, and
audited. ``Tenant`` is the tenancy discriminator (never tenant-scoped), so this
service reads/writes through the cross-tenant ``TenantRepository``.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

from application.errors import require_found
from application.repositories.tenant_repository import TenantRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_logo_service import (
    TAB_ICON_SIDE,
    TOUCH_ICON_SIDE,
    TenantLogoService,
    icon_url,
    logo_url,
)
from application.tenant_context import get_current_tenant_id, require_tenant_id
from application.utils.color_contrast import contrast_ratio, mix
from models import User

# The overridable brand colours and their shipped Phoenix defaults. Keep these in
# sync with the ``:root`` seed values in static/css/styles.css and the fallback
# used by theme/base.py — ``primary`` mirrors --wiz-gold-deep, ``accent``
# --wiz-gold, ``secondary`` --wiz-ember-deep, ``header`` --wiz-header-bg
# (the brand mark beside the wordmark).
DEFAULT_THEME: dict[str, str] = {
    'primary': '#9C6B12',
    'secondary': '#C24E12',
    'accent': '#E0A82E',
    'header': '#9C6B12',
}
THEME_KEYS: tuple[str, ...] = tuple(DEFAULT_THEME.keys())

# Config sub-key under Tenant.config where the override dict lives.
CONFIG_KEY = 'theme'

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

# --- Contrast (WCAG AA) ----------------------------------------------------
# Each brand colour is checked where static/css/styles.css actually paints it,
# in each mode, so a warning names a real use ("button text in both modes")
# rather than an abstract score. The surfaces are the --surface / --page-bg /
# --surface-alt tokens; tests/theme/test_style_guide.py asserts they still match
# styles.css. Text and button labels are held to AA normal text (4.5:1); the
# brand mark is a graphic, so it gets the 3:1 non-text line.
_WHITE = '#ffffff'
_LIGHT_SURFACES = ('#ffffff', '#f5f5f4', '#fafaf9')
_DARK_SURFACES = ('#1f1d1a', '#161513', '#262420')
_AA_TEXT = 4.5
_AA_GRAPHIC = 3.0


@dataclass(frozen=True)
class _ContrastCheck:
    use: str
    mode: str                         # 'light', 'dark' or 'both'
    against: tuple[str, ...]          # the worst of these is reported
    threshold: float = _AA_TEXT
    paint: Callable[[str], str] = lambda colour: colour  # what CSS derives from it


_CONTRAST_CHECKS: dict[str, tuple[_ContrastCheck, ...]] = {
    # Filled buttons keep the exact primary in both modes, under a white label.
    # Light-mode links and primary text are --wiz-link: 15% toward black.
    'primary': (
        _ContrastCheck('button text', 'both', (_WHITE,)),
        _ContrastCheck('links and highlighted text', 'light', _LIGHT_SURFACES,
                       paint=lambda c: mix(c, '#000000', 0.85)),
    ),
    # Secondary text buttons sit on cards and dialogs; dark mode lifts the text
    # 30% toward white (the .text-secondary rule in @layer theme).
    'secondary': (
        _ContrastCheck('button text', 'both', (_WHITE,)),
        _ContrastCheck('text buttons', 'light', (_LIGHT_SURFACES[0],)),
        _ContrastCheck('text buttons', 'dark', (_DARK_SURFACES[0],),
                       paint=lambda c: mix(c, '#ffffff', 0.7)),
    ),
    # Dark mode's --accent: links, highlighted text, the active nav item.
    'accent': (
        _ContrastCheck('links and highlighted text', 'dark', _DARK_SURFACES),
    ),
    # The square beside the wordmark on the white header bar. Light mode only:
    # the mark is decorative, and a dark-mode check would flag every preset's
    # deep header colour.
    'header': (
        _ContrastCheck('the brand mark in the header', 'light', (_LIGHT_SURFACES[0],),
                       threshold=_AA_GRAPHIC),
    ),
}

_MODE_PHRASE = {'light': 'in light mode', 'dark': 'in dark mode', 'both': 'in light and dark mode'}

# Curated palettes, each **verified** to meet the contrast thresholds above —
# tests/test_color_contrast.py asserts every field of every preset passes, so a
# future edit that breaks a preset fails CI. 'Phoenix (default)' is the shipped
# palette; it is listed first and equals DEFAULT_THEME.
THEME_PRESETS: dict[str, dict[str, str]] = {
    'Phoenix (default)': dict(DEFAULT_THEME),
    'Ocean': {'primary': '#0e7470', 'secondary': '#1d4ed8', 'accent': '#38bdf8', 'header': '#0e7470'},
    'Forest': {'primary': '#15803d', 'secondary': '#b45309', 'accent': '#4ade80', 'header': '#166534'},
    'Grape': {'primary': '#6d28d9', 'secondary': '#be185d', 'accent': '#c084fc', 'header': '#5b21b6'},
    'Rose': {'primary': '#be123c', 'secondary': '#c81e4a', 'accent': '#fb7185', 'header': '#9f1239'},
    'Slate': {'primary': '#334155', 'secondary': '#0f766e', 'accent': '#94a3b8', 'header': '#1e293b'},
}


class TenantThemeService:
    """Read/write a tenant's brand colour overrides."""

    @staticmethod
    def _merge(overrides: Optional[dict]) -> dict[str, str]:
        """Return a full palette: valid overrides layered onto the defaults."""
        colors = dict(DEFAULT_THEME)
        if overrides:
            for key in THEME_KEYS:
                value = overrides.get(key)
                if isinstance(value, str) and _HEX_RE.match(value):
                    colors[key] = value.lower()
        return colors

    @staticmethod
    async def get_theme(tenant_id: int) -> dict[str, str]:
        """The full brand palette for a tenant (overrides merged onto defaults).

        Carries ``logo`` (the image URL), ``logo_icon`` and ``logo_touch`` (its
        tab and home-screen PNGs) too when the community has uploaded one; they
        ride here because this is the tenant read every page
        build already makes.
        """
        tenant = await TenantRepository.get_by_id(tenant_id)
        config = (tenant.config or {}) if tenant else {}
        colors = TenantThemeService._merge(config.get(CONFIG_KEY))
        sha256 = TenantLogoService.logo_sha(config)
        if sha256:
            colors['logo'] = logo_url(tenant_id, sha256, TenantLogoService.logo_format(config))
            colors['logo_icon'] = icon_url(tenant_id, sha256, TAB_ICON_SIDE)
            colors['logo_touch'] = icon_url(tenant_id, sha256, TOUCH_ICON_SIDE)
        return colors

    @staticmethod
    async def get_current_theme() -> dict[str, str]:
        """Best-effort palette for the in-scope tenant; defaults when none.

        Non-raising: the platform surface (no tenant) and the synchronous error
        page both fall back to the shipped Phoenix palette rather than turning a
        missing tenant into a chrome-render failure. Reads are always fresh
        (``get_by_id`` is uncached), so a saved change shows on the next load.
        """
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return dict(DEFAULT_THEME)
        try:
            return await TenantThemeService.get_theme(tenant_id)
        except Exception:
            return dict(DEFAULT_THEME)

    @staticmethod
    def chart_series_colour(colour: str) -> str:
        """``colour`` moved just far enough to read as a chart line in both modes.

        Report charts are canvas, painted once, so one value has to clear the
        3:1 graphic line on the light and the dark card surfaces alike. A
        community's own colour usually does; when it doesn't (a deep purple
        vanishes on the dark card), mix it toward white or black by the smallest
        step that gets there, so the line stays recognisably that colour.
        """
        surfaces = _LIGHT_SURFACES + _DARK_SURFACES

        def worst(c: str) -> float:
            return min(contrast_ratio(c, s) for s in surfaces)

        if worst(colour) >= _AA_GRAPHIC:
            return colour
        best = colour
        for step in range(1, 51):
            amount = step / 50
            for target in ('#ffffff', '#000000'):
                candidate = mix(target, colour, amount)
                if worst(candidate) >= _AA_GRAPHIC:
                    return candidate
                if worst(candidate) > worst(best):
                    best = candidate
        return best

    @staticmethod
    def is_customized(colors: dict[str, str]) -> bool:
        """Whether a resolved palette differs from the shipped defaults."""
        return any(colors.get(key) != value for key, value in DEFAULT_THEME.items())

    @staticmethod
    def list_presets() -> dict[str, dict[str, str]]:
        """Named, contrast-verified palettes offered by the theme editor."""
        return {name: dict(colors) for name, colors in THEME_PRESETS.items()}

    @staticmethod
    def contrast_report(colors: dict) -> dict[str, list[dict]]:
        """Every WCAG check for each colour, where the app actually uses it.

        Returns ``{key: [{'use', 'mode', 'ratio', 'threshold', 'ok'}, ...]}``
        for every valid hex value supplied; ``ratio`` is the worst across the
        surfaces that use sits on. Blank/invalid values are skipped so a
        half-typed field never errors. Pure: the editor uses it for live
        warnings and the preset test for verification.
        """
        report: dict[str, list[dict]] = {}
        for key, checks in _CONTRAST_CHECKS.items():
            value = colors.get(key)
            if not (isinstance(value, str) and _HEX_RE.match(value)):
                continue
            report[key] = []
            for check in checks:
                painted = check.paint(value)
                ratio = min(contrast_ratio(painted, surface) for surface in check.against)
                report[key].append({
                    'use': check.use,
                    'mode': check.mode,
                    'ratio': round(ratio, 2),
                    'threshold': check.threshold,
                    'ok': ratio >= check.threshold,
                })
        return report

    @staticmethod
    def contrast_issues(colors: dict) -> dict[str, list[str]]:
        """Per colour, one sentence for each use that falls below AA."""
        issues: dict[str, list[str]] = {}
        for key, results in TenantThemeService.contrast_report(colors).items():
            failing = [
                f"{r['use'].capitalize()} {_MODE_PHRASE[r['mode']]} would be "
                f"{r['ratio']:.1f}:1, under the {r['threshold']:g}:1 AA minimum."
                for r in results if not r['ok']
            ]
            if failing:
                issues[key] = failing
        return issues

    @staticmethod
    def contrast_warnings(colors: dict) -> list[str]:
        """Every failing use across the palette, each prefixed with its colour."""
        return [
            f'{key.title()}: {issue}'
            for key, issues in TenantThemeService.contrast_issues(colors).items()
            for issue in issues
        ]

    @staticmethod
    async def set_theme(actor: User, colors: dict) -> dict[str, str]:
        """Validate and persist the current tenant's brand colours (STAFF only).

        A blank value for a key clears that override; clearing every key resets
        the tenant to the shipped palette. Returns the resulting full palette.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only Staff can change theme colours.',
        )
        tenant_id = require_tenant_id()

        cleaned: dict[str, str] = {}
        for key in THEME_KEYS:
            raw = (colors.get(key) or '').strip()
            if not raw:
                continue
            if not _HEX_RE.match(raw):
                raise ValueError(
                    f'{key.title()} colour must be a 6-digit hex value like #9C6B12.'
                )
            cleaned[key] = raw.lower()

        tenant = require_found(await TenantRepository.get_by_id(tenant_id), 'Tenant')

        config = dict(tenant.config or {})
        if cleaned:
            config[CONFIG_KEY] = cleaned
        else:
            config.pop(CONFIG_KEY, None)
        await TenantRepository.update(tenant, config=config)

        await AuditService().write_log(
            actor,
            AuditActions.THEME_UPDATED,
            {'tenant_id': tenant_id, 'colors': cleaned},
        )
        return TenantThemeService._merge(cleaned)
