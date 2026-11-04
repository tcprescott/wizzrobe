"""Dev seed for the match form: questions in every type and state, the rules that read them, and a saved answer."""

from models import Match, MatchFormAnswer, MatchFormQuestion, MatchPlayers, Preset, Tenant, Tournament


async def seed_match_form_answers(tenant: Tenant, tournament: Tournament, match: Match) -> None:
    """One player has said yes to hard mode and picked a hint count, so the player card shows saved answers."""
    first = await MatchPlayers.filter(match=match).order_by("id").first()
    if first is None:
        return
    for key, value in (("devhrd", "yes"), ("devhnt", 2)):
        question = await MatchFormQuestion.get_or_none(tournament=tournament, key=key)
        if question:
            await MatchFormAnswer.get_or_create(
                match=match, user_id=first.user_id, question=question, tenant=tenant,
                defaults={"value": {"v": value}},
            )


async def seed_match_form(tenant: Tenant, tournament: Tournament, preset: Preset) -> None:
    """The Match form tab: one question per answer type, between them every who / ask on / visibility,
    and the two rules that read them (hidden Yes/No for hard mode, a Game 3 pick from the previous loser)."""
    hard = await Preset.get_or_none(name="ALTTPR Open — Hard Mode", tenant=tenant)
    yes_no = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
    questions = [
        ("devhrd", "yes_no", "Play Hard mode?", "Only if every player says yes.", yes_no,
         "each_player", "every_match", "never"),
        ("devg3", "choice_one", "Game 3 settings", "You lost game 2, so this one's your call.",
         [{"value": "open", "label": "ALTTPR Open", "preset": preset.id},
          *([{"value": "hard", "label": "Hard Mode", "preset": hard.id}] if hard else []),
          {"value": "same", "label": "Same as game 2"}],
         "previous_loser", "when_used", "at_roll"),
        ("devgl", "choice_many", "Which goals are fine with you?", None,
         [{"value": "ganon", "label": "Defeat Ganon"}, {"value": "ped", "label": "Pedestal"},
          {"value": "tri", "label": "Triforce hunt"}],
         "higher_seed", "every_match", "right_away"),
        ("devhnt", "number", "How many hint tiles?", None, [], "each_player", "every_match", "right_away"),
    ]
    for position, (key, kind, prompt, help_text, choices, who, ask_on, visibility) in enumerate(questions):
        await MatchFormQuestion.get_or_create(
            tournament=tournament, key=key, tenant=tenant,
            defaults={
                "position": position, "kind": kind, "prompt": prompt, "help": help_text, "choices": choices,
                "config": {"min": 0, "max": 5} if kind == "number" else {},
                "who": who, "ask_on": ask_on, "visibility": visibility,
            },
        )
    rules = list(tournament.seed_rules or [])
    have = {r.get("id") for r in rules}
    extra = []
    if hard and "devhm" not in have:
        extra.append({"id": "devhm", "name": "Hard mode",
                      "when": {"answer": [{"q": "devhrd", "who": "every", "is": ["yes"]}]},
                      "then": {"preset": hard.id}})
    if "devg3r" not in have:
        extra.append({"id": "devg3r", "name": "Game 3 pick", "when": {"game": [3]},
                      "then": {"from_answer": "devg3"}})
    if extra:
        await Tournament.filter(id=tournament.id).update(seed_rules=extra + rules)
        tournament.seed_rules = extra + rules
