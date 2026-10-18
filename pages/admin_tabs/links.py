"""URLs into the admin tabs.

Thin re-export of :mod:`application.utils.app_links`, which owns the builders so
the notification layer can address the same routes (a service may not import a
presentation module). Import from here inside ``pages/``; import from
``app_links`` inside ``application/``.
"""

from application.utils.app_links import (
    INTEGRATIONS,
    REPORTS,
    SCHEDULE,
    USERS,
    VOL_SCHEDULE,
    admin_url,
)

__all__ = ['INTEGRATIONS', 'REPORTS', 'SCHEDULE', 'USERS', 'VOL_SCHEDULE', 'admin_url']
