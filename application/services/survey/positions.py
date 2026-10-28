"""The event's volunteer positions, as the positions condition and the preview use them."""

from dataclasses import dataclass, field
from typing import Any, Dict, List

from application.repositories.survey_participation_repository import SurveyParticipationRepository
from application.services.survey.conditions import POSITIONS, choices


@dataclass
class ConditionChoice:
    """One condition a question or section can carry; ``options`` are what a positions condition picks from."""

    key: str
    label: str
    options: List[dict] = field(default_factory=list)


def _sorted(names: Dict[int, str]) -> List[dict]:
    return [{'id': i, 'name': n} for i, n in sorted(names.items(), key=lambda kv: kv[1].lower())]


async def event_positions(event_id: int) -> List[dict]:
    """Every volunteer position on the event's schedule, worked or not, by name."""
    return _sorted(await SurveyParticipationRepository.position_names(event_id))


async def named_in(event_id: int, entries: Any) -> Dict[int, str]:
    """The event's positions (id → name), read only when one of ``entries`` is a positions condition."""
    if not any((e or {}).get('key') == POSITIONS for e in entries or []):
        return {}
    return await SurveyParticipationRepository.position_names(event_id)


async def condition_choices() -> List[ConditionChoice]:
    """Every condition, with the current event's positions on the positions one."""
    options = _sorted(await SurveyParticipationRepository.position_names())
    return [ConditionChoice(key, label, options if key == POSITIONS else []) for key, label in choices().items()]
