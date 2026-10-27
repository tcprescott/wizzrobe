"""The starter survey a new one opens with.

Staff write the survey; this only saves them typing the questions nearly every
event asks. Everything here is editable or deletable once it's created.
"""

from typing import List, Optional, Tuple

from models import SurveyAudience, SurveyQuestionKind

RATING = SurveyQuestionKind.RATING
NPS = SurveyQuestionKind.NPS
YES_NO = SurveyQuestionKind.YES_NO
CHOICE_ONE = SurveyQuestionKind.CHOICE_ONE
COMMENT = SurveyQuestionKind.COMMENT

# (prompt, kind, options, condition)
Question = Tuple[str, SurveyQuestionKind, Optional[List[str]], Optional[str]]

STARTER: List[Tuple[str, SurveyAudience, List[Question]]] = [
    ('The event', SurveyAudience.EVERYONE, [
        ('Overall, how was the event?', RATING, None, None),
        ('How likely are you to recommend it to a friend?', NPS, None, None),
        ('Will you come back next year?', CHOICE_ONE, ['Yes', 'Maybe', 'No'], None),
    ]),
    ('Your tournaments', SurveyAudience.EACH_TOURNAMENT, [
        ('How was the tournament room?', RATING, None, 'tournament_room'),
        ('How was playing on stage?', RATING, None, 'on_stage'),
        ('Did your matches start on time?', CHOICE_ONE, ['Yes', 'Mostly', 'No'], None),
        ('Anything we should change?', COMMENT, None, None),
    ]),
    ('Volunteering', SurveyAudience.EACH_POSITION, [
        ('Did you have what you needed on shift?', YES_NO, None, None),
        ('Shift length felt…', CHOICE_ONE, ['Too short', 'About right', 'Too long'], None),
    ]),
    ('Anything else?', SurveyAudience.EVERYONE, [
        ('Anything else you want to tell us?', COMMENT, None, None),
    ]),
]
