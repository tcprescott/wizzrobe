"""Who a survey went to, by group: the filter's choices and the response rate per audience.

Pure: works from each recipient's frozen ``facts`` and whether they sent it.
A group is offered as a filter only once :data:`MIN_GROUP` of its people have
answered, so a filtered report never speaks for fewer than that.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence

from application.services.survey.results import MIN_GROUP, ORGANIZERS


@dataclass
class GroupOption:
    key: str
    label: str
    sent: int
    responded: int


@dataclass
class AudienceRate:
    label: str
    sent: int
    responded: int

    @property
    def rate(self) -> int:
        return round(self.responded * 100 / self.sent) if self.sent else 0


def groups_for(facts: Mapping[str, Any]) -> List[dict]:
    """The groups a respondent counts in: each tournament, organizers, each position, crew, checked in."""
    groups = [{'key': f"t:{t['id']}", 'label': f"{t['name']} player"} for t in facts.get('tournaments') or []]
    if facts.get('organized'):
        groups.append({'key': 'organizers', 'label': ORGANIZERS})
    groups += [{'key': f"p:{p['id']}", 'label': p['name']} for p in facts.get('positions') or []]
    if facts.get('crew'):
        groups.append({'key': 'crew', 'label': 'Crew'})
    if facts.get('checked_in'):
        groups.append({'key': 'checked_in', 'label': 'Checked in'})
    return groups


_AUDIENCES = (('Players', 'tournaments'), (ORGANIZERS, 'organized'), ('Volunteers', 'positions'), ('Crew', 'crew'))


def audience_rates(rows: Sequence[Mapping[str, Any]]) -> List[AudienceRate]:
    """Response rate for players, organizers, volunteers and crew, leaving out an audience nobody was in."""
    out = []
    for label, fact in _AUDIENCES:
        mine = [r for r in rows if (r.get('facts') or {}).get(fact)]
        if mine:
            out.append(AudienceRate(label, len(mine), sum(1 for r in mine if r.get('submitted_at'))))
    return out


def _order(key: str, label: str) -> tuple[int, str]:
    kind = {'t': 0, 'p': 1}.get(key.split(':', 1)[0], 2)
    return kind, label.lower()


def group_options(rows: Sequence[Mapping[str, Any]]) -> List[GroupOption]:
    """Groups with at least :data:`MIN_GROUP` answers, tournaments then positions then the rest."""
    sent: Counter = Counter()
    responded: Counter = Counter()
    labels: Dict[str, str] = {}
    for row in rows:
        for group in groups_for(row.get('facts') or {}):
            labels[group['key']] = group['label']
            sent[group['key']] += 1
            responded[group['key']] += bool(row.get('submitted_at'))
    return sorted(
        (GroupOption(key, labels[key], sent[key], responded[key]) for key in labels if responded[key] >= MIN_GROUP),
        key=lambda g: _order(g.key, g.label),
    )
