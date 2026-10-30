"""Challonge provider config for the profile 'Connected accounts' section.

Lets a player verify-link their Challonge identity (one-time OAuth, scope
``me``) so they can be matched to bracket participants for scheduling. Rendered
(alongside the other providers) by :func:`render_connected_accounts_section`,
which hides any provider whose integration isn't configured.
"""

from application.services import ChallongeService
from pages.home_tabs._link_section import LinkSectionConfig

CONFIG = LinkSectionConfig(
    title='Challonge',
    icon='emoji_events',
    description=(
        'Link your Challonge account so we can find your bracket matches and let you '
        'schedule them here.'
    ),
    link_route='/challonge/link',
    link_button_label='Link Challonge account',
    unlink_button_label='Unlink Challonge account',
    unlinked_message='Challonge account unlinked.',
    user_id_attr='challonge_user_id',
    username_attr='challonge_username',
    linked_at_attr='challonge_linked_at',
    service_factory=ChallongeService,
    sign_in_first_url='https://challonge.com/user_session/new',
    unlink_confirmation=(
        'Your bracket participation is matched to you by your Challonge account.\n\n'
        'Tournaments you were enrolled in automatically from a bracket will stop '
        'listing you the next time that bracket is synced. Matches already '
        'scheduled are not cancelled.\n\n'
        'You can link Challonge again at any time.'
    ),
)
