"""Admin → Surveys: build a post-event survey, send it, read the results.

``page`` owns the list and the switch into one survey; ``builder`` draws a
survey's sections and questions with the time box beside them; ``dialogs``
holds the add/edit forms; ``results`` the numbers and comments.
"""

from .page import admin_surveys_page

__all__ = ['admin_surveys_page']
