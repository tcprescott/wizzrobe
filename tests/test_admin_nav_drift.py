"""The app's admin menu (``AdminNavService``) must match the website's admin drawer.

Both lists gate the same areas and panes on the same roles and flags. Until the
website is retired they live side by side, so this fails the moment one changes
without the other.
"""

import itertools
import random
from dataclasses import fields

import pytest

from application.services.admin_nav_service import (
    APP_ONLY_AREAS,
    AdminAccess,
    admin_areas,
    event_settings_panes,
    settings_panes,
)
from application.services.tenant_setup_service import SetupStep
from models import FeatureFlag
from pages.admin import build_admin_tabs
from pages.admin_tabs.settings import build_event_settings_panes, build_settings_panes
from theme.base import tab_slug

GRANTS = [f.name for f in fields(AdminAccess) if f.name.startswith('is_')]
ALL = set(FeatureFlag)
_rng = random.Random(7)
FLAG_SETS = [set(), ALL] + [{f for f in FeatureFlag if _rng.random() < 0.5} for _ in range(12)]
ACCESSES = [AdminAccess(**{g: True}) for g in GRANTS] + [
    AdminAccess(**{a: True, b: True}) for a, b in itertools.combinations(GRANTS, 2)
]
NOT_READY = [SetupStep('staff', 'Give the community a staff member', False, '', 'Members', True)]


@pytest.mark.parametrize('ready', [True, False])
def test_areas_match_the_admin_drawer(ready):
    for access, live in itertools.product(ACCESSES, FLAG_SETS):
        website = build_admin_tabs(access, live, {}, None if ready else NOT_READY)
        app = [a for a in admin_areas(access, live, setup_ready=ready) if a.slug not in APP_ONLY_AREAS]
        assert [t['label'] for t in website] == [a.label for a in app], (access, sorted(live))
        assert [tab_slug(t['label']) for t in website] == [a.slug for a in app]
        assert [tuple(t.get('aliases', ())) for t in website if t['label'] != 'Settings'] == [
            a.aliases for a in app if a.label != 'Settings'
        ]


def test_settings_panes_match():
    for access, live in itertools.product(ACCESSES, FLAG_SETS):
        assert [(p.slug, p.label, p.icon) for p in build_settings_panes(access, live)] == [
            (p.slug, p.label, p.icon) for p in settings_panes(access, live)
        ]
        assert [(p.slug, p.label, p.icon) for p in build_event_settings_panes(access, live)] == [
            (p.slug, p.label, p.icon) for p in event_settings_panes(access, live)
        ]
