"""Who opened a survey, who started, who finished, and where the rest stopped.

Pure: given each recipient's progress columns (no names) and the running
per-part counts kept for people who already sent it, it adds them up. Counts
are of people, never of answers.
"""

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from application.services.survey import plan
from application.services.survey.results import MIN_GROUP
from models import SurveyPartTally, SurveySection


@dataclass
class PartFunnel:
    key: str
    title: str
    icon: str
    opened: int = 0
    answered: int = 0
    stopped: int = 0


@dataclass
class Dropoff:
    sent: int = 0
    opened: int = 0
    started: int = 0
    submitted: int = 0
    declined: int = 0
    parts: List[PartFunnel] = field(default_factory=list)
    median_seconds: Optional[int] = None
    estimate_seconds: Optional[int] = None


def _key(section_id: int, scope: str) -> str:
    return f'{section_id}|{scope}'


def build(sections: Sequence[SurveySection], rows: Sequence[Mapping[str, Any]],
          tallies: Sequence[SurveyPartTally]) -> Dropoff:
    order = {s.id: i for i, s in enumerate(sections)}
    by_id = {s.id: s for s in sections}
    out = Dropoff(sent=len(rows))
    funnels: Dict[str, PartFunnel] = {}
    sort_keys: Dict[str, Tuple[int, str]] = {}

    def funnel(section_id: int, scope: str, title: str, icon: str) -> PartFunnel:
        key = _key(section_id, scope)
        if key not in funnels:
            funnels[key] = PartFunnel(key, title, icon)
            sort_keys[key] = (order.get(section_id, len(order)), title)
        return funnels[key]

    estimates: List[int] = []
    durations: List[int] = []
    for row in rows:
        facts = row.get('facts') or {}
        parts = plan.parts_for(sections, facts)
        if parts:
            estimates.append(plan.seconds(parts))
        if row.get('opened_at'):
            out.opened += 1
        if row.get('submitted_at'):
            out.submitted += 1
            out.started += 1
            if row.get('finish_seconds') is not None:
                durations.append(int(row['finish_seconds']))
            continue
        if row.get('declined_at'):
            out.declined += 1
        progress = row.get('progress') or {}
        opened, done = set(progress.get('opened') or []), set(progress.get('done') or [])
        if done:
            out.started += 1
        for part in parts:
            if part.key not in opened | done:
                continue
            f = funnel(part.section_id, part.scope, _title(by_id.get(part.section_id), part.scope_label), part.icon)
            f.opened += 1
            f.answered += part.key in done
            f.stopped += part.key == row.get('last_part') and not row.get('declined_at')

    for tally in tallies:
        section = by_id.get(tally.section_id)
        if section is None:
            continue
        f = funnel(tally.section_id, tally.scope, _title(section, tally.scope_label), _icon(section))
        f.opened += tally.opened
        f.answered += tally.answered

    out.parts = sorted(funnels.values(), key=lambda f: sort_keys[f.key])
    if len(durations) >= MIN_GROUP:
        out.median_seconds = int(median(durations))
    if estimates:
        out.estimate_seconds = int(median(estimates))
    return out


def _title(section: Optional[SurveySection], scope_label: str) -> str:
    title = section.title if section else ''
    return f'{title}: {scope_label}' if scope_label else title


def _icon(section: SurveySection) -> str:
    return plan.icon_for(section)
