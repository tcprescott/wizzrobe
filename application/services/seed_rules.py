"""Seed rules: which preset a match rolls, decided by an ordered rule list.

Pure: no database, no tenant. :class:`SeedRuleService` loads a tournament's
rules and a match's facts, and this module checks the rules on write
(:func:`clean_rules`), picks the outcome at roll time (:func:`resolve`) and
converts to and from the YAML text view (:func:`to_yaml`, :func:`from_yaml`).

Stored shape, one dict per rule, presets by id::

    {"id": "k3x9", "name": "Grand final mystery",
     "when": {"round": ["gf"], "game": [3, "last"], "title": ["final"],
              "on_stage": true, "from": "2026-11-20T00:00:00+00:00", "to": "..."},
     "then": {"preset": 12} | {"weighted": [{"preset": 12, "weight": 50}, ...]}
             | {"from_answer": "q1"},
     "tweaks": {"item.pool": "hard"}}

``when.answer`` reads the tournament's match form: ``[{"q": "hard", "who":
"every", "is": ["yes"]}]`` holds when every player asked answered Yes; ``who:
"any"`` when one did; leaving out ``is`` asks only that they answered.
``from_answer`` rolls the preset linked to the choice the answer picked.

Every condition in ``when`` must hold; a list inside one condition means
"any of". An empty ``when`` matches every match.
"""

from __future__ import annotations

import copy
import random
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

import yaml

MAX_RULES = 50
MAX_NAME = 80
MAX_LIST = 32
MAX_TEXT = 80
MAX_WEIGHTED = 16
MAX_WEIGHT = 10_000
MAX_TWEAKS = 24
MAX_TWEAK_KEY = 64
MAX_TWEAK_DEPTH = 4
LAST_GAME = 'last'
CONDITIONS = ('round', 'game', 'title', 'on_stage', 'from', 'to', 'answer')
MAX_ANSWER_CONDITIONS = 8
_ID_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789'


@dataclass(frozen=True)
class FormQuestion:
    """What the rule checks need to know about one match-form question."""
    key: str
    kind: str
    choices: tuple = ()
    choice_presets: Mapping[Any, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Answers:
    """One question's answers on one match.

    ``expected`` is how many people the question asks there; ``values`` holds
    one entry per person who answered; ``presets`` the presets their picked
    choices link to.
    """
    expected: int = 0
    values: tuple = ()
    presets: tuple = ()


@dataclass(frozen=True)
class MatchFacts:
    """What a rule may ask about a match. ``round`` is a Round schedule stage key."""
    round: Optional[str] = None
    game: Optional[int] = None
    best_of: Optional[int] = None
    title: Optional[str] = None
    on_stage: bool = False
    scheduled_at: Optional[datetime] = None
    answers: Mapping[str, Answers] = field(default_factory=dict)


@dataclass
class RuleHit:
    """The rule that decided, and what it decided."""
    index: int
    rule_id: str
    name: str
    preset_id: int
    tweaks: dict = field(default_factory=dict)
    draw: Optional[dict] = None

    def record(self) -> dict:
        out: dict[str, Any] = {
            'source': 'rule', 'rule_id': self.rule_id, 'rule_number': self.index + 1,
            'rule_name': self.name, 'preset_id': self.preset_id,
        }
        if self.tweaks:
            out['tweaks'] = dict(self.tweaks)
        if self.draw is not None:
            out['draw'] = self.draw
        return out


def new_rule_id(taken: set[str]) -> str:
    while True:
        rid = ''.join(secrets.choice(_ID_CHARS) for _ in range(6))
        if rid not in taken:
            return rid


def _err(n: int, msg: str) -> ValueError:
    return ValueError(f'Rule {n}: {msg}')


def _str_list(n: int, label: str, raw: Any, *, limit: int = MAX_TEXT) -> list[str]:
    items = raw if isinstance(raw, list) else [raw]
    if not items or len(items) > MAX_LIST:
        raise _err(n, f'{label} needs 1 to {MAX_LIST} values.')
    out: list[str] = []
    for item in items:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            raise _err(n, f'{label} values must be text.')
        text = str(item).strip()
        if not text or len(text) > limit:
            raise _err(n, f'{label} values must be 1 to {limit} characters.')
        if text not in out:
            out.append(text)
    return out


def _games(n: int, raw: Any) -> list:
    items = raw if isinstance(raw, list) else [raw]
    if not items or len(items) > 9:
        raise _err(n, 'Game in set needs 1 to 9 values.')
    out: list = []
    for item in items:
        if item == LAST_GAME:
            value: Any = LAST_GAME
        elif isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 9:
            value = item
        else:
            raise _err(n, 'Game in set takes game numbers 1 to 9, or "last".')
        if value not in out:
            out.append(value)
    return out


def _instant(n: int, label: str, raw: Any) -> str:
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError:
            raise _err(n, f'{label} must be a date and time.') from None
    else:
        raise _err(n, f'{label} must be a date and time.')
    if dt.tzinfo is None:
        raise _err(n, f'{label} needs a timezone.')
    return dt.astimezone(timezone.utc).isoformat()


def _answer_value(n: int, q: FormQuestion, value: Any) -> Any:
    if q.kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _err(n, f'answers to {q.key!r} are numbers.')
        return value
    if value not in q.choices:
        raise _err(n, f'{value!r} is not one of the choices for {q.key!r}.')
    return value


def _answers(n: int, raw: Any, questions: Mapping[str, FormQuestion]) -> list:
    items = raw if isinstance(raw, list) else [raw]
    if not items or len(items) > MAX_ANSWER_CONDITIONS:
        raise _err(n, f'Form answer needs 1 to {MAX_ANSWER_CONDITIONS} questions.')
    out = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) - {'q', 'who', 'is'}:
            raise _err(n, 'a form answer condition names a question, who, and optionally what they answered.')
        key = item.get('q')
        q = questions.get(key) if isinstance(key, str) else None
        if q is None:
            raise _err(n, f"asks about a question the match form doesn't have ({item.get('q')!r}).")
        who = item.get('who', 'every')
        if who not in ('every', 'any'):
            raise _err(n, 'who must be "every" or "any".')
        cond: dict[str, Any] = {'q': q.key, 'who': who}
        if item.get('is') is not None:
            values = item['is'] if isinstance(item['is'], list) else [item['is']]
            if not values or len(values) > MAX_LIST:
                raise _err(n, f'{q.key!r} needs 1 to {MAX_LIST} answers to look for.')
            cond['is'] = list(dict.fromkeys(_answer_value(n, q, v) for v in values))
        out.append(cond)
    return out


def _when(n: int, raw: Any, questions: Mapping[str, FormQuestion]) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _err(n, '"when" must be a list of conditions.')
    unknown = set(raw) - set(CONDITIONS)
    if unknown:
        raise _err(n, f'unknown condition {sorted(unknown)[0]!r}.')
    out: dict[str, Any] = {}
    if 'round' in raw:
        out['round'] = _str_list(n, 'Round', raw['round'], limit=64)
    if 'game' in raw:
        out['game'] = _games(n, raw['game'])
    if 'title' in raw:
        out['title'] = _str_list(n, 'Title', raw['title'])
    if 'on_stage' in raw:
        if not isinstance(raw['on_stage'], bool):
            raise _err(n, 'On stage must be true or false.')
        out['on_stage'] = raw['on_stage']
    for key, label in (('from', 'From'), ('to', 'To')):
        if key in raw:
            out[key] = _instant(n, label, raw[key])
    if 'from' in out and 'to' in out and out['from'] >= out['to']:
        raise _err(n, '"from" must be before "to".')
    if 'answer' in raw:
        out['answer'] = _answers(n, raw['answer'], questions)
    return out


def _preset_ref(n: int, raw: Any, presets: Mapping[int, Any]) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw not in presets:
        raise _err(n, "names a preset this community doesn't have.")
    return raw


def _tweak_value(n: int, key: str, value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 200:
            raise _err(n, f'tweak {key!r} is too long.')
        return value
    if isinstance(value, list) and depth < MAX_TWEAK_DEPTH and len(value) <= MAX_LIST:
        return [_tweak_value(n, key, v, depth + 1) for v in value]
    raise _err(n, f'tweak {key!r} must be text, a number, true/false or a short list.')


def _tweaks(n: int, raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or len(raw) > MAX_TWEAKS:
        raise _err(n, f'tweaks must be up to {MAX_TWEAKS} "setting: value" pairs.')
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_TWEAK_KEY:
            raise _err(n, 'a tweak needs a setting name.')
        parts = key.strip().split('.')
        if len(parts) > MAX_TWEAK_DEPTH or any(not p for p in parts):
            raise _err(n, f'tweak {key!r} is not a setting path.')
        out['.'.join(parts)] = _tweak_value(n, key, value)
    return out


def _then(n: int, raw: Any, presets: Mapping[int, Any], questions: Mapping[str, FormQuestion]) -> dict:
    if not isinstance(raw, Mapping):
        raise _err(n, 'needs an outcome.')
    if len({'preset', 'weighted', 'from_answer'} & set(raw)) > 1:
        raise _err(n, 'pick one preset, a weighted list or a form answer, not more than one.')
    if 'from_answer' in raw:
        q = questions.get(raw['from_answer']) if isinstance(raw['from_answer'], str) else None
        if q is None or not q.choice_presets:
            raise _err(n, "rolls a form answer, but that question has no choices linked to presets.")
        for pid in q.choice_presets.values():
            if pid not in presets:
                raise _err(n, f"question {q.key!r} links a preset this community doesn't have.")
        return {'from_answer': q.key}
    if 'preset' in raw:
        return {'preset': _preset_ref(n, raw['preset'], presets)}
    if 'weighted' in raw:
        items = raw['weighted']
        if not isinstance(items, list) or not 2 <= len(items) <= MAX_WEIGHTED:
            raise _err(n, f'a weighted roll needs 2 to {MAX_WEIGHTED} presets.')
        out, seen = [], set()
        for item in items:
            if not isinstance(item, Mapping):
                raise _err(n, 'each weighted entry needs a preset and a weight.')
            pid = _preset_ref(n, item.get('preset'), presets)
            weight = item.get('weight')
            if not isinstance(weight, int) or isinstance(weight, bool) or not 0 <= weight <= MAX_WEIGHT:
                raise _err(n, f'weights are whole numbers from 0 to {MAX_WEIGHT}.')
            if pid in seen:
                raise _err(n, 'a preset appears twice in the weighted roll.')
            seen.add(pid)
            out.append({'preset': pid, 'weight': weight})
        if not any(e['weight'] for e in out):
            raise _err(n, 'at least one weight must be above 0.')
        return {'weighted': out}
    raise _err(n, 'needs an outcome: a preset or a weighted roll.')


def clean_rules(
    raw: Any, presets: Mapping[int, Any], questions: Optional[Mapping[str, FormQuestion]] = None,
) -> list[dict]:
    """Validate and normalise a whole rule list, or raise ``ValueError``.

    ``presets`` maps every preset id the tournament may roll to anything; only
    membership is checked here. ``questions`` is the tournament's match form,
    by key, for rules that read answers. Rule ids are kept when valid and unique, and
    minted otherwise, so reordering never changes a rule's identity.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('Seed rules must be a list.')
    if len(raw) > MAX_RULES:
        raise ValueError(f'A tournament can have up to {MAX_RULES} seed rules.')
    form = questions or {}
    taken: set[str] = set()
    out: list[dict] = []
    for i, item in enumerate(raw):
        n = i + 1
        if not isinstance(item, Mapping):
            raise _err(n, 'must be a rule.')
        unknown = set(item) - {'id', 'name', 'when', 'then', 'tweaks'}
        if unknown:
            raise _err(n, f'unknown field {sorted(unknown)[0]!r}.')
        name = str(item.get('name') or '').strip() or f'Rule {n}'
        if len(name) > MAX_NAME:
            raise _err(n, f'the name is over {MAX_NAME} characters.')
        rid = item.get('id')
        if not (isinstance(rid, str) and 1 <= len(rid) <= 16 and rid.isalnum() and rid not in taken):
            rid = new_rule_id(taken)
        taken.add(rid)
        rule = {'id': rid, 'name': name, 'when': _when(n, item.get('when'), form),
                'then': _then(n, item.get('then'), presets, form)}
        tweaks = _tweaks(n, item.get('tweaks'))
        if tweaks:
            rule['tweaks'] = tweaks
        out.append(rule)
    return out


def preset_ids(rules: list[dict]) -> set[int]:
    ids: set[int] = set()
    for rule in rules:
        then = rule.get('then') or {}
        if 'preset' in then:
            ids.add(then['preset'])
        for entry in then.get('weighted') or []:
            ids.add(entry['preset'])
    return ids


def matches(when: Mapping[str, Any], facts: MatchFacts) -> bool:
    if 'round' in when and facts.round not in when['round']:
        return False
    if 'game' in when:
        if facts.game is None:
            return False
        last = facts.best_of is not None and facts.game == facts.best_of
        if facts.game not in when['game'] and not (last and LAST_GAME in when['game']):
            return False
    if 'title' in when:
        title = (facts.title or '').casefold()
        if not any(t.casefold() in title for t in when['title']):
            return False
    if 'on_stage' in when and facts.on_stage != when['on_stage']:
        return False
    if 'from' in when or 'to' in when:
        if facts.scheduled_at is None:
            return False
        at = facts.scheduled_at.astimezone(timezone.utc)
        if 'from' in when and at < datetime.fromisoformat(when['from']):
            return False
        if 'to' in when and at >= datetime.fromisoformat(when['to']):
            return False
    for cond in when.get('answer') or ():
        if not _answer_holds(cond, facts.answers.get(cond['q'])):
            return False
    return True


def _picked(value: Any, wanted: Optional[list]) -> bool:
    if wanted is None:
        return True
    if isinstance(value, list):
        return any(v in wanted for v in value)
    return value in wanted


def _answer_holds(cond: Mapping[str, Any], answers: Optional[Answers]) -> bool:
    """An unanswered question never holds, so its rule skips and the next decides."""
    if answers is None or not answers.values:
        return False
    hits = [_picked(v, cond.get('is')) for v in answers.values]
    if cond.get('who') == 'any':
        return any(hits)
    return answers.expected > 0 and len(answers.values) >= answers.expected and all(hits)


def first_fitting(
    rules: list[dict], facts: MatchFacts, *, usable: Optional[Callable[[int], bool]] = None,
) -> Optional[tuple[int, dict, list[dict]]]:
    """The first rule whose conditions hold and that names a preset it can roll.

    Returns its index, the rule and the outcome's rollable entries (one entry
    of weight 1 for a single preset), without drawing.
    """
    ok = usable or (lambda _pid: True)
    for i, rule in enumerate(rules):
        if not matches(rule.get('when') or {}, facts):
            continue
        then = rule.get('then') or {}
        if 'preset' in then:
            if ok(then['preset']):
                return i, rule, [{'preset': then['preset'], 'weight': 1}]
            continue
        if 'from_answer' in then:
            answers = facts.answers.get(then['from_answer'])
            linked = set(answers.presets) if answers else set()
            if len(linked) == 1 and ok(next(iter(linked))):
                return i, rule, [{'preset': next(iter(linked)), 'weight': 1}]
            continue
        entries = [e for e in then.get('weighted') or [] if e['weight'] > 0 and ok(e['preset'])]
        if entries:
            return i, rule, entries
    return None


def resolve(
    rules: list[dict], facts: MatchFacts, *,
    rng: Optional[random.Random] = None,
    usable: Optional[Callable[[int], bool]] = None,
) -> Optional[RuleHit]:
    """The first rule whose conditions all hold, with its outcome drawn.

    ``usable`` filters presets that have since gone away: a rule whose outcome
    names none it can roll is skipped, so the next rule (or the tournament's
    own preset) decides rather than the roll failing.
    """
    found = first_fitting(rules, facts, usable=usable)
    if found is None:
        return None
    i, rule, entries = found
    draw = None
    if 'weighted' not in rule['then']:
        pid = entries[0]['preset']
    else:
        rng = rng or random.SystemRandom()
        pid = rng.choices([e['preset'] for e in entries], weights=[e['weight'] for e in entries])[0]
        draw = {'weights': {str(e['preset']): e['weight'] for e in entries}, 'picked': pid}
    return RuleHit(i, rule['id'], rule['name'], pid, dict(rule.get('tweaks') or {}), draw)


def describe(record: Optional[Mapping[str, Any]], preset_names: Mapping[int, str]) -> Optional[str]:
    """Why this seed, in a few words, from a roll's ``resolution`` record."""
    if not record:
        return None
    pid = record.get('preset_id')
    name = preset_names.get(pid) if isinstance(pid, int) else None
    source = record.get('source')
    if source == 'rule':
        line = f"Rule {record.get('rule_number')}: {record.get('rule_name') or 'Unnamed'}"
        if name:
            line += f' → {name}'
        if record.get('draw'):
            line += ' (weighted draw)'
        if record.get('tweaks'):
            line += ' + tweaks'
        return line
    if source == 'hard_mode':
        return f'Every player opted in → {name}' if name else 'Every player opted in'
    if source == 'override':
        return f'Set by staff → {name}' if name else 'Set by staff'
    if source == 'default':
        return f'No rule fitted → {name}' if name else 'No rule fitted'
    return None


def apply_tweaks(settings: Any, tweaks: Mapping[str, Any]) -> Any:
    """A copy of ``settings`` with each dotted path set; the input is untouched."""
    if not tweaks:
        return settings
    out = copy.deepcopy(settings) if isinstance(settings, dict) else {}
    for path, value in tweaks.items():
        node = out
        *parents, leaf = path.split('.')
        for part in parents:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[leaf] = copy.deepcopy(value)
    return out


def to_yaml(rules: list[dict], preset_names: Mapping[int, str]) -> str:
    """The text view: presets by name, rule ids dropped."""
    doc = []
    for rule in rules:
        item: dict[str, Any] = {'name': rule['name']}
        if rule.get('when'):
            item['when'] = dict(rule['when'])
        then = rule['then']
        if 'preset' in then:
            item['then'] = {'preset': preset_names.get(then['preset'], f"#{then['preset']}")}
        elif 'from_answer' in then:
            item['then'] = {'from_answer': then['from_answer']}
        else:
            item['then'] = {'weighted': {
                preset_names.get(e['preset'], f"#{e['preset']}"): e['weight'] for e in then['weighted']
            }}
        if rule.get('tweaks'):
            item['tweaks'] = dict(rule['tweaks'])
        doc.append(item)
    return yaml.safe_dump({'rules': doc}, sort_keys=False, allow_unicode=True, width=100)


def _name_to_id(n: int, name: Any, presets_by_name: Mapping[str, int]) -> int:
    if isinstance(name, str):
        key = name.strip()
        if key in presets_by_name:
            return presets_by_name[key]
        if key.startswith('#') and key[1:].isdigit() and int(key[1:]) in presets_by_name.values():
            return int(key[1:])
    raise _err(n, f"no preset named {name!r} in this community.")


def from_yaml(text: str, presets_by_name: Mapping[str, int]) -> list[dict]:
    """Parse the text view into stored rules (not yet cleaned), or raise ``ValueError``.

    A YAML syntax error names its line. Presets are looked up by name, exactly
    as :func:`to_yaml` wrote them.
    """
    if len(text) > 100_000:
        raise ValueError('That text is too long for a rule list.')
    try:
        doc = yaml.safe_load(text) if text.strip() else {'rules': []}
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f'Line {mark.line + 1} of the text' if mark is not None else 'The text'
        problem = getattr(exc, 'problem', None) or 'is not valid YAML'
        raise ValueError(f'{where}: {problem}.') from None
    if isinstance(doc, list):
        doc = {'rules': doc}
    if not isinstance(doc, dict) or not isinstance(doc.get('rules', []), list):
        raise ValueError('The text needs a "rules:" list.')
    out: list[dict] = []
    for i, item in enumerate(doc.get('rules') or []):
        n = i + 1
        if not isinstance(item, dict):
            raise _err(n, 'must be a rule.')
        rule = {k: v for k, v in item.items() if k != 'then'}
        then = item.get('then')
        if not isinstance(then, dict):
            raise _err(n, 'needs an outcome.')
        if 'preset' in then:
            rule['then'] = {'preset': _name_to_id(n, then['preset'], presets_by_name)}
        elif isinstance(then.get('weighted'), dict):
            rule['then'] = {'weighted': [
                {'preset': _name_to_id(n, name, presets_by_name), 'weight': weight}
                for name, weight in then['weighted'].items()
            ]}
        else:
            rule['then'] = then
        out.append(rule)
    return out
