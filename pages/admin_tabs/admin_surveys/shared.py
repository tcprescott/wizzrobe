"""Labels and icons the survey builder, dialogs and results share."""

from typing import Dict

from models import SurveyAudience, SurveyQuestionKind, SurveyStatus

KIND_ICONS: Dict[SurveyQuestionKind, str] = {
    SurveyQuestionKind.RATING: 'star_outline',
    SurveyQuestionKind.NPS: 'speed',
    SurveyQuestionKind.YES_NO: 'thumbs_up_down',
    SurveyQuestionKind.CHOICE_ONE: 'radio_button_checked',
    SurveyQuestionKind.CHOICE_MANY: 'checklist',
    SurveyQuestionKind.COMMENT: 'notes',
    SurveyQuestionKind.AGREE_SCALE: 'table_rows',
}

KIND_CHIPS: Dict[SurveyQuestionKind, str] = {
    SurveyQuestionKind.RATING: 'Rating',
    SurveyQuestionKind.NPS: '0–10',
    SurveyQuestionKind.YES_NO: 'Yes/No',
    SurveyQuestionKind.CHOICE_ONE: 'Pick one',
    SurveyQuestionKind.CHOICE_MANY: 'Pick any',
    SurveyQuestionKind.COMMENT: 'Comment',
    SurveyQuestionKind.AGREE_SCALE: 'Agree scale',
}

AUDIENCE_CHIPS: Dict[SurveyAudience, str] = {
    SurveyAudience.EVERYONE: 'Everyone',
    SurveyAudience.EACH_TOURNAMENT: 'Repeats per tournament played',
    SurveyAudience.EACH_POSITION: 'Repeats per position worked',
    SurveyAudience.CREW: 'Crew, any role',
    SurveyAudience.COMMENTATORS: 'Commentators',
    SurveyAudience.TRACKERS: 'Trackers',
    SurveyAudience.BROADCASTERS: 'Broadcast crew',
    SurveyAudience.CHECKED_IN: 'Checked in at the desk',
}

STATUS_BADGES: Dict[SurveyStatus, tuple[str, str]] = {
    SurveyStatus.DRAFT: ('Draft', 'grey'),
    SurveyStatus.OPEN: ('Open', 'positive'),
    SurveyStatus.CLOSED: ('Closed', 'grey'),
}
