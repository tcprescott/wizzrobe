"""What "Copy setup from" brings into a new event, and what it leaves behind.

Setup crosses over, people and history never do: no players, matches, shifts,
sign-ups, meetups, qualifiers, availability or private details, whatever is
ticked. Anything that points at last year's instance of something (a Challonge
or start.gg bracket, an sglive prize-pool page, a Matcherino venue) is cleared,
so the copy can't sync against last year's bracket.

A rule that points at something not copied (a Discord role rule for a tournament
that wasn't ticked) is skipped and reported, never copied pointing at last
year's row.
"""

from typing import Any, Dict, List

from application.repositories.event_repository import EventRepository
from application.repositories.event_setup_repository import EventSetupRepository
from models import DiscordRoleRuleKind, Event

COPY_GROUPS: Dict[str, str] = {
    'tournaments': 'Tournaments',
    'positions': 'Volunteer positions',
    'stages': 'Stages and stations',
    'check_in': 'Check-in tiers',
    'discord_roles': 'Discord roles from Wizzrobe',
}

TOURNAMENT_CLEARED = frozenset({
    'bracket_url',
    'challonge_tournament_id', 'challonge_community', 'challonge_tournament_url',
    'challonge_last_synced_at', 'challonge_state', 'challonge_group_stage', 'challonge_tournament_type',
    'startgg_event_id', 'startgg_event_slug', 'startgg_event_name', 'startgg_last_synced_at',
    'startgg_bookable_phase_id', 'startgg_report_results',
    'event_start_date', 'event_end_date', 'tournament_hours',
    'signups_open_at', 'signups_close_at',
    'prize_pool', 'prize_bonus', 'sglive_slug', 'prize_pool_synced_at',
})
TOURNAMENT_CONFIG_CLEARED = frozenset({'round_schedule'})

CHECK_IN_CLEARED = frozenset({
    'matcherino_venue_id', 'status', 'last_synced_at', 'last_sync_error', 'last_sync_count',
})

CONFIG_WITH_GROUP: Dict[str, tuple[str, ...]] = {
    'stages': ('station_format',),
    'positions': ('volunteer_comp_tiers',),
}


def _tournament_config(config: Any) -> Any:
    if not isinstance(config, dict):
        return config
    kept = {k: v for k, v in config.items() if k not in TOURNAMENT_CONFIG_CLEARED}
    return kept or None


async def copy_setup(source: Event, target: Event, groups: frozenset[str]) -> Dict[str, Any]:
    """Copy ``groups`` of ``source``'s setup into ``target``. Call inside a transaction.

    Returns counts per thing copied, and ``skipped``: one line per rule left out.
    """
    copied: Dict[str, Any] = {}
    skipped: List[str] = []
    tournament_ids: Dict[int, int] = {}

    if 'tournaments' in groups:
        rows = await EventSetupRepository.tournaments(source.id)
        tournament_ids = await EventSetupRepository.copy_tournaments(
            rows, target.id, skip=TOURNAMENT_CLEARED,
            config={t.id: _tournament_config(t.config) for t in rows},
        )
        copied['tournaments'] = len(tournament_ids)
        copied['tournament_role_mappings'] = await EventSetupRepository.copy_tournament_role_mappings(tournament_ids)

    if 'positions' in groups:
        position_ids = await EventSetupRepository.copy_positions(source.id, target.id)
        copied['positions'] = len(position_ids)
        copied['position_role_mappings'] = await EventSetupRepository.copy_position_role_mappings(position_ids, target.id)
        copied['qualifications'] = await EventSetupRepository.copy_qualifications(position_ids)

    if 'stages' in groups:
        copied['stages'] = len(await EventSetupRepository.copy_stages(source.id, target.id))
        copied['stations'] = len(await EventSetupRepository.copy_stations(source.id, target.id))

    if 'check_in' in groups:
        events, tiers = await EventSetupRepository.copy_check_in(source.id, target.id, skip=CHECK_IN_CLEARED)
        copied['check_in_events'] = events
        copied['check_in_tiers'] = tiers

    if 'discord_roles' in groups:
        rules = 0
        for rule in await EventSetupRepository.role_rules(source.id):
            if rule.kind == DiscordRoleRuleKind.TOURNAMENT:
                new_tournament = tournament_ids.get(rule.tournament_id or 0)
                if new_tournament is None:
                    skipped.append(
                        f'The "{rule.discord_role_name}" role rule: its tournament was not copied.'
                    )
                    continue
                await EventSetupRepository.copy_role_rule(
                    rule, target.id, key=f'tournament:{new_tournament}', tournament_id=new_tournament,
                )
            else:
                await EventSetupRepository.copy_role_rule(rule, target.id, key=rule.key, tournament_id=None)
            rules += 1
        copied['discord_role_rules'] = rules

    config_keys = [key for group in groups for key in CONFIG_WITH_GROUP.get(group, ())]
    carried = {key: source.config[key] for key in config_keys if key in (source.config or {})}
    if carried:
        await EventRepository.update(target, config={**(target.config or {}), **carried})

    if skipped:
        copied['skipped'] = skipped
    return copied
