"""The About section: what Wizzrobe is, its privacy policy, and the third-party
software it ships.

A peer of ``application/changelog``: shipped text with no database, no tenant and
no rules. The privacy policy is ``privacy.md`` beside this module, parsed into
the same safe block model as help.
"""

from application.about.notices import GROUPS, Notice, NoticeGroup, all_notices, find_notice
from application.about.privacy import PRIVACY_UPDATED, privacy_blocks

__all__ = ['GROUPS', 'PRIVACY_UPDATED', 'Notice', 'NoticeGroup', 'all_notices', 'find_notice', 'privacy_blocks']
