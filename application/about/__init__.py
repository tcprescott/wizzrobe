"""The About section: what Wizzrobe is, its privacy policy, and the third-party
software it ships.

A peer of ``application/changelog``: shipped text with no database, no tenant and
no rules. The privacy policy is ``privacy.md`` beside this module, parsed into
the same safe block model as help.
"""

from application.about.notices import GROUPS, Notice, NoticeGroup, all_notices, find_notice
from application.about.privacy import PRIVACY_UPDATED, privacy_blocks

ABOUT_INTRO = (
    'Wizzrobe runs the schedule for speedrun and randomizer events: matches, brackets, crew, '
    'volunteers, equipment and check-in, with Discord sign-in and notifications. Each community '
    'gets its own space, run by its own staff.\n\n'
    "It's free software under the [GNU General Public License v3](https://www.gnu.org/licenses/gpl-3.0.html), "
    'and the source is on [GitHub](https://github.com/tcprescott/wizzrobe).'
)

BUILT_WITH_CLAUDE = (
    "Much of Wizzrobe's code was written with Claude, an AI model made by "
    'Anthropic. A person reviews every change before it ships, but if something '
    "looks wrong, tell us through Send Feedback or your community's staff."
)

__all__ = ['ABOUT_INTRO', 'BUILT_WITH_CLAUDE', 'GROUPS', 'PRIVACY_UPDATED', 'Notice', 'NoticeGroup', 'all_notices', 'find_notice', 'privacy_blocks']
