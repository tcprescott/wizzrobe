"""Twitch provider config for the profile 'Connected accounts' section.

Lets a user verify-link their Twitch identity via a one-time OAuth login.
Rendered (alongside the other providers) by
:func:`render_connected_accounts_section`, which hides any provider whose
integration isn't configured.
"""

from application.services import TwitchService
from pages.home_tabs._link_section import LinkSectionConfig

CONFIG = LinkSectionConfig(
    title='Twitch',
    icon='live_tv',
    description=(
        'Link your Twitch account so we can associate your verified Twitch identity '
        'with your profile.'
    ),
    link_route='/twitch/link',
    link_button_label='Link Twitch account',
    unlink_button_label='Unlink Twitch account',
    unlinked_message='Twitch account unlinked.',
    user_id_attr='twitch_user_id',
    username_attr='twitch_username',
    linked_at_attr='twitch_linked_at',
    service_factory=TwitchService,
)
