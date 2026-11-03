"""start.gg provider config for the profile 'Connected accounts' section.

Shown only where the start.gg feature is live. Linking is what maps a player to
their start.gg entries without staff matching names by hand.
"""

from application.services import StartggLinkService
from pages.home_tabs._link_section import LinkSectionConfig

CONFIG = LinkSectionConfig(
    title='start.gg',
    icon='emoji_events',
    description=(
        'Link your start.gg account so your start.gg brackets show up on My Schedule '
        'and you can book your matches.'
    ),
    link_route='/startgg/link',
    link_button_label='Link start.gg account',
    unlink_button_label='Unlink start.gg account',
    unlinked_message='start.gg account unlinked.',
    user_id_attr='startgg_user_id',
    username_attr='startgg_username',
    linked_at_attr='startgg_linked_at',
    service_factory=StartggLinkService,
    unlink_confirmation=(
        'Your start.gg matches will stop showing on My Schedule unless staff have '
        'matched you by hand, or your start.gg account has Discord connected.'
    ),
)
