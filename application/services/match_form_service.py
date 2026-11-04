"""A tournament's match form: questions players answer before a match, which seed rules read.

Staff build the questions (choices only: Yes/No, pick one, pick several, a
number). Each question says who in a match answers it, which matches ask it,
and when the other players see the answer. Answers lock once the seed rolls.

What a player may see is decided here and only here: their own answers, and
another player's answer only when the question's visibility allows it. A
``never`` question (an opt-in, such as hard mode) never shows anyone else's
answer, and its answers are audited but never published on the event bus.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Mapping, Optional, Set

from application.errors import require_found
from application.events import EventType
from application.repositories.match_form_repository import MatchFormRepository
from application.repositories.match_repository import MatchRepository
from application.repositories.preset_repository import PresetRepository
from application.repositories.tournament_repository import TournamentRepository
from application.services import seed_rules
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import (
    Match,
    MatchFormAskOn,
    MatchFormKind,
    MatchFormQuestion,
    MatchFormVisibility,
    MatchFormWho,
    Tournament,
    User,
)

MAX_QUESTIONS = 12
MAX_CHOICES = 16
YES_NO_VALUES = ('yes', 'no')
_KEY_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789'


def _new_key(taken: Set[str]) -> str:
    while True:
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        if key not in taken:
            return key


def _err(n: int, msg: str) -> ValueError:
    return ValueError(f'Question {n}: {msg}')


def _enum(n: int, cls: Any, raw: Any, default: Any, label: str) -> Any:
    if raw is None:
        return default
    try:
        return cls(raw)
    except ValueError:
        raise _err(n, f'{label} must be one of {", ".join(m.value for m in cls)}.') from None


def clean_questions(raw: Any, presets: Mapping[int, Any], randomizer: Optional[str]) -> List[dict]:
    """Validate a whole match form, or raise ``ValueError`` naming the bad question."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('The match form must be a list of questions.')
    if len(raw) > MAX_QUESTIONS:
        raise ValueError(f'A match form can have up to {MAX_QUESTIONS} questions.')
    taken: Set[str] = set()
    out: List[dict] = []
    for i, item in enumerate(raw):
        n = i + 1
        if not isinstance(item, Mapping):
            raise _err(n, 'must be a question.')
        unknown = set(item) - {'used_by', 'key', 'kind', 'prompt', 'help', 'choices', 'config', 'who', 'ask_on', 'visibility'}
        if unknown:
            raise _err(n, f'unknown field {sorted(unknown)[0]!r}.')
        key = item.get('key')
        if not (isinstance(key, str) and 1 <= len(key) <= 16 and key.isalnum() and key not in taken):
            key = _new_key(taken)
        taken.add(key)
        prompt = str(item.get('prompt') or '').strip()
        if not prompt or len(prompt) > 200:
            raise _err(n, 'needs a question of 1 to 200 characters.')
        help_text = str(item.get('help') or '').strip() or None
        if help_text and len(help_text) > 500:
            raise _err(n, 'the help text is over 500 characters.')
        kind = _enum(n, MatchFormKind, item.get('kind'), None, 'kind')
        if kind is None:
            raise _err(n, 'needs a kind: yes_no, choice_one, choice_many or number.')
        choices, config = _choices(n, kind, item.get('choices'), item.get('config'), presets, randomizer)
        out.append({
            'key': key, 'kind': kind.value, 'prompt': prompt, 'help': help_text,
            'choices': choices, 'config': config,
            'who': _enum(n, MatchFormWho, item.get('who'), MatchFormWho.EACH_PLAYER, 'who').value,
            'ask_on': _enum(n, MatchFormAskOn, item.get('ask_on'), MatchFormAskOn.EVERY_MATCH, 'ask_on').value,
            'visibility': _enum(
                n, MatchFormVisibility, item.get('visibility'), MatchFormVisibility.RIGHT_AWAY, 'visibility',
            ).value,
        })
    return out


def _choices(
    n: int, kind: MatchFormKind, raw: Any, config: Any, presets: Mapping[int, Any], randomizer: Optional[str],
) -> tuple[list, dict]:
    if kind == MatchFormKind.NUMBER:
        cfg = config if isinstance(config, Mapping) else {}
        lo, hi = cfg.get('min', 0), cfg.get('max', 10)
        for v in (lo, hi):
            if isinstance(v, bool) or not isinstance(v, int) or not -10_000 <= v <= 10_000:
                raise _err(n, 'a number question needs whole-number bounds.')
        if lo >= hi:
            raise _err(n, 'the lowest number must be below the highest.')
        return [], {'min': lo, 'max': hi}
    items = raw if isinstance(raw, list) else []
    if kind == MatchFormKind.YES_NO:
        given = {c.get('value'): c for c in items if isinstance(c, Mapping)}
        items = [
            {'value': v, 'label': 'Yes' if v == 'yes' else 'No', 'preset': (given.get(v) or {}).get('preset')}
            for v in YES_NO_VALUES
        ]
    elif not 2 <= len(items) <= MAX_CHOICES:
        raise _err(n, f'needs 2 to {MAX_CHOICES} choices.')
    out: List[dict] = []
    seen_values: Set[str] = set()
    seen_labels: Set[str] = set()
    for c in items:
        if not isinstance(c, Mapping):
            raise _err(n, 'each choice needs a label.')
        label = str(c.get('label') or '').strip()
        if not label or len(label) > 80:
            raise _err(n, 'each choice needs a label of 1 to 80 characters.')
        if label.casefold() in seen_labels:
            raise _err(n, f'the choice {label!r} is listed twice.')
        seen_labels.add(label.casefold())
        value = c.get('value')
        if not (isinstance(value, str) and 1 <= len(value) <= 16 and value.isalnum()) or value in seen_values:
            value = _new_key(seen_values)
        seen_values.add(value)
        choice: Dict[str, Any] = {'value': value, 'label': label}
        pid = c.get('preset')
        if pid is not None:
            if isinstance(pid, bool) or not isinstance(pid, int) or pid not in presets:
                raise _err(n, f"the choice {label!r} links a preset this community doesn't have.")
            if randomizer and getattr(presets[pid], 'randomizer', randomizer) != randomizer:
                raise _err(n, f"the choice {label!r} links a preset for another randomizer.")
            choice['preset'] = pid
        out.append(choice)
    return out, {}


def engine_question(q: Mapping[str, Any]) -> seed_rules.FormQuestion:
    return seed_rules.FormQuestion(
        key=q['key'], kind=q['kind'],
        choices=tuple(c['value'] for c in q.get('choices') or []),
        choice_presets={c['value']: c['preset'] for c in q.get('choices') or [] if c.get('preset') is not None},
    )


def _as_dict(q: MatchFormQuestion) -> dict:
    return {
        'key': q.key, 'kind': q.kind.value if hasattr(q.kind, 'value') else q.kind,
        'prompt': q.prompt, 'help': q.help, 'choices': list(q.choices or []), 'config': dict(q.config or {}),
        'who': getattr(q.who, 'value', q.who), 'ask_on': getattr(q.ask_on, 'value', q.ask_on),
        'visibility': getattr(q.visibility, 'value', q.visibility),
    }


def rules_reading(rules: List[dict], key: str) -> List[int]:
    """The 1-based numbers of the rules that read question ``key``."""
    out = []
    for n, rule in enumerate(rules, start=1):
        reads = any(c.get('q') == key for c in (rule.get('when') or {}).get('answer') or [])
        if reads or (rule.get('then') or {}).get('from_answer') == key:
            out.append(n)
    return out


class MatchFormService:
    def __init__(self) -> None:
        self.repository = MatchFormRepository()
        self.tournament_repository = TournamentRepository()
        self.match_repository = MatchRepository()
        self.preset_repository = PresetRepository()
        self.audit_service = AuditService()

    async def _tournament(self, tournament_id: int) -> Tournament:
        return require_found(await self.tournament_repository.get_by_id(tournament_id), 'Tournament')

    async def _ensure_can_edit(self, actor: Optional[User], tournament: Tournament) -> None:
        allowed = (
            await AuthService.can_edit_tournament(actor, tournament)
            or await AuthService.can_manage_presets(actor)
        )
        await AuthService.ensure(allowed, "You can't change this tournament's match form.")

    async def questions(self, tournament_id: int) -> List[dict]:
        """The form as stored, for the rule engine and the editor. No permission check."""
        return [_as_dict(q) for q in await self.repository.list_questions(tournament_id)]

    async def engine_questions(self, tournament_id: int) -> Dict[str, seed_rules.FormQuestion]:
        return {q['key']: engine_question(q) for q in await self.questions(tournament_id)}

    async def get_form(self, tournament_id: int, actor: Optional[User]) -> List[dict]:
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        rules = list(tournament.seed_rules or [])
        return [dict(q, used_by=rules_reading(rules, q['key'])) for q in await self.questions(tournament_id)]

    async def save_form(self, tournament_id: int, raw: Any, actor: User) -> List[dict]:
        """Replace the whole form. A question a seed rule still reads can't be removed."""
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        presets = {p.id: p for p in await self.preset_repository.list_all()}
        cleaned = clean_questions(raw, presets, tournament.seed_generator)
        rules = list(tournament.seed_rules or [])
        keep = {q['key'] for q in cleaned}
        existing = {q.key: q for q in await self.repository.list_questions(tournament.id)}
        for key, row in existing.items():
            if key not in keep and rules_reading(rules, key):
                numbers = ', '.join(str(n) for n in rules_reading(rules, key))
                raise ValueError(f'"{row.prompt}" is read by seed rule {numbers}. Change the rule first.')
        if rules:
            seed_rules.clean_rules(rules, presets, {q['key']: engine_question(q) for q in cleaned})
        for position, q in enumerate(cleaned):
            fields = {k: v for k, v in q.items() if k != 'key'}
            if q['key'] in existing:
                await self.repository.update_question(existing[q['key']], position=position, **fields)
            else:
                await self.repository.create_question(tournament.id, key=q['key'], position=position, **fields)
        await self.repository.delete_questions([row.id for key, row in existing.items() if key not in keep])
        await self.audit_service.write_and_publish(
            actor, AuditActions.TOURNAMENT_MATCH_FORM_UPDATED,
            {'tournament_id': tournament.id, 'questions': len(cleaned)},
            EventType.TOURNAMENT_MATCH_FORM_UPDATED,
        )
        return await self.get_form(tournament.id, actor)

    async def _asked(self, match: Match, q: MatchFormQuestion, players: List[int]) -> Set[int]:
        """Who in ``match`` answers ``q``. Empty when nobody can (a seat that isn't decided)."""
        who = getattr(q.who, 'value', q.who)
        if who == MatchFormWho.EACH_PLAYER.value:
            return set(players)
        if who == MatchFormWho.HIGHER_SEED.value:
            seeds = await self.repository.native_seeds(match.tournament_id, players)  # type: ignore[attr-defined]
            if len(seeds) < len(players) or not seeds:
                return set()
            return {min(seeds, key=lambda uid: seeds[uid])}
        return await self._previous_loser(match)

    async def _previous_loser(self, match: Match) -> Set[int]:
        from application.services.bracket_set_service import BracketSetService

        if (match.set_best_of or 0) < 2:
            return set()
        games = await self.match_repository.set_games(getattr(match, 'set_first_id', None) or match.id)
        if not games:
            return set()
        numbers = await BracketSetService().game_numbers_for(games)
        mine = numbers.get(match.id)
        if not mine or mine < 2:
            return set()
        previous = next((g for g in games if numbers.get(g.id) == mine - 1), None)
        if previous is None:
            return set()
        return {
            p.user_id for p in await self.repository.players(previous.id)  # type: ignore[attr-defined]
            if p.finish_rank and p.finish_rank > 1
        }

    async def _shown_on(self, match: Match, q: MatchFormQuestion, rules: List[dict]) -> bool:
        if getattr(q.ask_on, 'value', q.ask_on) == MatchFormAskOn.EVERY_MATCH.value:
            return True
        reading = rules_reading(rules, q.key)
        if not reading:
            return False
        from application.services.seed_rule_service import SeedRuleService

        facts = await SeedRuleService().facts_for(match, with_answers=False)
        for n in reading:
            when = {k: v for k, v in (rules[n - 1].get('when') or {}).items() if k != 'answer'}
            if seed_rules.matches(when, facts):
                return True
        return False

    async def answers_for(self, match: Match) -> Dict[str, seed_rules.Answers]:
        """Every question's answers on ``match`` for the rule engine, keyed by question key."""
        questions = await self.repository.list_questions(match.tournament_id)  # type: ignore[attr-defined]
        if not questions:
            return {}
        rows = await self.repository.answers_for_match(match.id)
        players = [p.user_id for p in await self.repository.players(match.id)]  # type: ignore[attr-defined]
        out: Dict[str, seed_rules.Answers] = {}
        for q in questions:
            asked = await self._asked(match, q, players)
            linked = {c['value']: c.get('preset') for c in q.choices or []}
            values, picked = [], []
            for row in rows:
                if row.question_id != q.id or row.user_id not in asked:  # type: ignore[attr-defined]
                    continue
                values.append(row.value)
                for v in row.value if isinstance(row.value, list) else [row.value]:
                    if linked.get(v) is not None:
                        picked.append(linked[v])
            out[q.key] = seed_rules.Answers(expected=len(asked), values=tuple(values), presets=tuple(picked))
        return out

    async def _match(self, match_id: int) -> Match:
        match = require_found(await self.match_repository.get_by_id(match_id), 'Match')
        await match.fetch_related('tournament')
        return match

    async def player_form(self, match_id: int, user: User) -> Dict[str, Any]:
        """The "Before your match" card for ``user``: the questions they're asked, their
        answers, and the other players' answers each question lets them see."""
        match = await self._match(match_id)
        players = {p.user_id: p.user for p in await self.repository.players(match.id)}  # type: ignore[attr-defined]
        if user.id not in players:
            raise PermissionError('Only the players in this match can see its form.')
        locked = match.generated_seed_id is not None  # type: ignore[attr-defined]
        rules = list(match.tournament.seed_rules or [])
        rows = await self.repository.answers_for_match(match.id)
        out = []
        for q in await self.repository.list_questions(match.tournament_id):  # type: ignore[attr-defined]
            asked = await self._asked(match, q, list(players))
            if user.id not in asked or not await self._shown_on(match, q, rules):
                continue
            visibility = getattr(q.visibility, 'value', q.visibility)
            can_see = visibility == MatchFormVisibility.RIGHT_AWAY.value or (
                visibility == MatchFormVisibility.AT_ROLL.value and locked
            )
            mine = next((r.value for r in rows if r.question_id == q.id and r.user_id == user.id), None)  # type: ignore[attr-defined]
            others = [
                {'name': players[r.user_id].preferred_name, 'value': r.value}  # type: ignore[attr-defined]
                for r in rows
                if can_see and r.question_id == q.id and r.user_id != user.id and r.user_id in players  # type: ignore[attr-defined]
            ]
            out.append({
                'key': q.key, 'kind': getattr(q.kind, 'value', q.kind), 'prompt': q.prompt, 'help': q.help,
                'choices': [{'value': c['value'], 'label': c['label']} for c in q.choices or []],
                'config': dict(q.config or {}), 'visibility': visibility,
                'answer': mine, 'others': others,
            })
        return {'match_id': match.id, 'locked': locked, 'questions': out}

    async def answer(self, match_id: int, user: User, key: str, value: Any) -> Dict[str, Any]:
        """Save, change or (with ``None``) clear ``user``'s answer. Refused once the seed rolls."""
        match = await self._match(match_id)
        players = [p.user_id for p in await self.repository.players(match.id)]  # type: ignore[attr-defined]
        if user.id not in players:
            raise PermissionError('Only the players in this match can answer its form.')
        if match.generated_seed_id is not None:  # type: ignore[attr-defined]
            raise ValueError("The seed for this match is rolled, so its answers are locked.")
        q = next((x for x in await self.repository.list_questions(match.tournament_id) if x.key == key), None)  # type: ignore[attr-defined]
        if q is None or user.id not in await self._asked(match, q, players):
            raise ValueError("That question isn't asked of you on this match.")
        if value is None:
            await self.repository.clear_answer(match.id, user.id, q.id)
        else:
            await self.repository.set_answer(match.id, user.id, q.id, _check_answer(q, value))
        await self.audit_service.write_log(
            user, AuditActions.MATCH_FORM_ANSWERED, {'match_id': match.id, 'question': q.key},
        )
        return await self.player_form(match.id, user)

    async def question_count_for(self, match: Match, user_id: int) -> int:
        """How many questions ``match`` asks ``user_id`` (``match.tournament`` loaded)."""
        questions = await self.repository.list_questions(match.tournament_id)  # type: ignore[attr-defined]
        if not questions:
            return 0
        rules = list(match.tournament.seed_rules or [])
        players = [p.user_id for p in await self.repository.players(match.id)]  # type: ignore[attr-defined]
        count = 0
        for q in questions:
            if user_id in await self._asked(match, q, players) and await self._shown_on(match, q, rules):
                count += 1
        return count


def _check_answer(q: MatchFormQuestion, value: Any) -> Any:
    kind = getattr(q.kind, 'value', q.kind)
    values = [c['value'] for c in q.choices or []]
    if kind == MatchFormKind.NUMBER.value:
        cfg = q.config or {}
        if isinstance(value, bool) or not isinstance(value, int) or not cfg.get('min', 0) <= value <= cfg.get('max', 10):
            raise ValueError(f"Pick a whole number from {cfg.get('min', 0)} to {cfg.get('max', 10)}.")
        return value
    if kind == MatchFormKind.CHOICE_MANY.value:
        if not isinstance(value, list) or not value or any(v not in values for v in value):
            raise ValueError('Pick one or more of the choices listed.')
        return [v for v in values if v in value]
    if value not in values:
        raise ValueError('Pick one of the choices listed.')
    return value
