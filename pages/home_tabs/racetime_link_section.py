"""racetime.gg provider config for the profile 'Connected accounts' section.

Lets a user verify-link their racetime.gg identity via a one-time OAuth login.
Rendered (alongside the other providers) by
:func:`render_connected_accounts_section`, which hides any provider whose
integration isn't configured.
"""

from application.services import RacetimeService
from pages.home_tabs._link_section import LinkSectionConfig

CONFIG = LinkSectionConfig(
    title='racetime.gg',
    icon='timer',
    description=(
        'Link your racetime.gg account so we can attribute your race results and '
        'check auto-open eligibility.'
    ),
    link_route='/racetime/link',
    link_button_label='Link racetime.gg account',
    unlink_button_label='Unlink racetime.gg account',
    unlinked_message='racetime.gg account unlinked.',
    user_id_attr='racetime_user_id',
    username_attr='racetime_username',
    linked_at_attr='racetime_linked_at',
    service_factory=RacetimeService,
)
