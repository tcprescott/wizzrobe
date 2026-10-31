"""The 'Connected accounts' rows render everything their config declares.

``LinkSectionConfig`` carried ``description`` and ``link_button_label`` for the
whole life of this section without either being read — the row title came from
``title``, the button said the literal ``'Link'``, and an audit quoted the
racetime description approvingly from the *source*. The first test below is the
mechanical guard against a fourth field going the same way.

The row renderer needs a NiceGUI slot context, so these drive it inside
``ui.card()`` on a headless client and read the built element tree, rather than
asserting on strings in the module. The "user" is an attribute holder, not a
``User`` row — the renderer only ever does ``getattr``, so no database is needed.
"""

import dataclasses
from types import SimpleNamespace

import pytest
from nicegui import context, ui

from pages.home_tabs._link_section import (
    LinkSectionConfig,
    _render_provider_row,
    open_sign_in_first_dialog,
)
from tests.factories import utc


class _Service:
    def is_configured(self):
        return True

    async def unlink_player(self, user, actor):
        return None


def make_config(**overrides) -> LinkSectionConfig:
    base = dict(
        title='Provider X',
        icon='star',
        description='Link Provider X so we can find your matches.',
        link_route='/x/link',
        link_button_label='Link Provider X account',
        unlink_button_label='Unlink Provider X account',
        unlinked_message='Provider X account unlinked.',
        user_id_attr='x_user_id',
        username_attr='x_username',
        linked_at_attr='x_linked_at',
        service_factory=_Service,
    )
    base.update(overrides)
    return LinkSectionConfig(**base)


def link_holder(user_id=None, username=None, linked_at=None):
    """A stand-in for the ``User`` columns the row renderer reads."""
    return SimpleNamespace(x_user_id=user_id, x_username=username, x_linked_at=linked_at)


def render(user, config, main_site_url=None) -> str:
    """Render one row and return its visible text plus its controls' labels."""
    with ui.card() as card:
        _render_provider_row(user, config, main_site_url)
    return collect_text(card)


def collect_text(card) -> str:
    parts = []

    def walk(element):
        text = getattr(element, 'text', None)
        if text:
            parts.append(str(text))
        props = getattr(element, '_props', {}) or {}
        for key in ('aria-label', 'href', 'label'):
            if key in props:
                parts.append(str(props[key]))
        for child in element.default_slot.children:
            walk(child)

    walk(card)
    return '\n'.join(parts)


@pytest.fixture(autouse=True)
def _slot_context():
    from nicegui.client import Client
    client = Client(lambda: None, request=None)
    with client:
        yield


def test_every_link_section_config_field_is_rendered():
    """No field may be declared, populated, and never shown.

    Introspects the dataclass and asserts every *copy* field reaches the rendered
    row in at least one of the linked / unlinked states — as text or as an
    accessible label. This is the guard that made the wave exist; a new field
    must be listed as behavioural with a reason, not quietly skipped.
    """
    behavioural = {
        'icon', 'link_route', 'user_id_attr', 'username_attr', 'linked_at_attr',
        'service_factory', 'unlinked_message', 'unlink_confirmation',
        'sign_in_first_url',
    }
    config = make_config()
    unlinked = render(link_holder(), config)
    linked = render(link_holder('x1', 'XName', utc(2024, 3, 4)), config)
    both = unlinked + '\n' + linked

    missing = [
        f.name for f in dataclasses.fields(LinkSectionConfig)
        if f.name not in behavioural
        and isinstance(getattr(config, f.name), str)
        and getattr(config, f.name) not in both
    ]
    assert not missing, (
        f'LinkSectionConfig fields declared but never rendered: {missing}. '
        'Render them or delete them — dead config that reads like shipped '
        'behaviour is the bug this test exists to prevent.'
    )


def test_an_unlinked_row_shows_the_providers_description():
    text = render(link_holder(), make_config())
    assert 'Link Provider X so we can find your matches.' in text
    assert 'Not linked' in text


def test_a_linked_row_does_not_repeat_the_description():
    text = render(link_holder('x1', 'XName'), make_config())
    assert 'Link Provider X so we can find your matches.' not in text
    assert 'Linked as XName' in text


def test_the_link_button_carries_the_configured_accessible_label():
    assert 'Link Provider X account' in render(link_holder(), make_config())


def test_the_unlink_button_carries_the_configured_accessible_label():
    assert 'Unlink Provider X account' in render(link_holder('x1', 'XName'), make_config())


def test_a_linked_row_shows_the_link_date():
    text = render(link_holder('x1', 'XName', utc(2024, 3, 4, 12)), make_config())
    assert 'Linked as XName on 2024-03-04' in text


def test_the_link_date_is_rendered_in_eastern_not_utc():
    # 01:00 UTC on the 4th is still the 3rd in US/Eastern.
    text = render(link_holder('x1', 'XName', utc(2024, 3, 4, 1)), make_config())
    assert 'on 2024-03-03' in text


def test_a_linked_row_with_no_date_omits_it():
    text = render(link_holder('x1', 'XName', None), make_config())
    assert 'Linked as XName' in text
    assert ' on ' not in text


def test_a_linked_row_falls_back_to_the_id_when_there_is_no_username():
    assert 'Linked as x1' in render(link_holder('x1', None), make_config())


@pytest.fixture
def custom_domain(monkeypatch):
    """A tenant's own domain, with the cross-host link handoff turned off."""
    monkeypatch.setattr('pages.home_tabs._link_section.is_host_mode', lambda: True)
    monkeypatch.setattr(
        'pages.home_tabs._link_section.host_oauth_handoff_enabled', lambda: False,
    )


def test_custom_domain_without_handoff_links_to_the_platform_host(custom_domain):
    text = render(link_holder(), make_config(), 'https://main.example/t/acme/home/profile')
    assert 'https://main.example/t/acme/home/profile' in text
    assert 'Main site only' in text
    assert 'Link Provider X account' not in text


def test_custom_domain_with_handoff_renders_the_link_button(monkeypatch):
    monkeypatch.setattr('pages.home_tabs._link_section.is_host_mode', lambda: True)
    monkeypatch.setattr(
        'pages.home_tabs._link_section.host_oauth_handoff_enabled', lambda: True,
    )
    text = render(link_holder(), make_config(), 'https://main.example/t/acme/home/profile')
    assert 'Link Provider X account' in text
    assert 'Main site only' not in text


def test_a_linked_row_on_a_custom_domain_can_still_unlink(custom_domain):
    assert 'Unlink Provider X account' in render(link_holder('x1', 'XName'), make_config())


def test_the_sign_in_first_dialog_links_to_the_providers_sign_in_page():
    open_sign_in_first_dialog(make_config(), 'https://x.example/login')
    text = collect_text(context.client.layout)
    assert 'Sign in to Provider X first' in text
    assert 'https://x.example/login' in text
    assert "I'm signed in, continue" in text


def test_challonge_asks_players_to_sign_in_to_challonge_first():
    from pages.home_tabs.challonge_link_section import CONFIG
    assert CONFIG.sign_in_first_url == 'https://challonge.com/user_session/new'
