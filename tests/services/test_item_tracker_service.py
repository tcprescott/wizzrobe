"""The ALTTP item tracker: crew mark a runner's items, the stream shows them late.

What matters: only approved trackers, staff and broadcast techs can mark a
board (never the runner, never other crew); it's for ALTTP matches only; marks
clamp to what the item or dungeon can hold, with keys only on seeds that shuffle
them; the dungeon-item mode comes from the seed unless overridden; undo takes
back only a change nothing has overwritten; the overlay runs behind by the
delay and answers to its token alone; and the flag gates every entry.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import FeatureDisabledError, NotFoundError
from application.services import ItemTrackerService
from application.services import item_tracker_service as its
from application.services.feature_flag_service import reset_flag_cache
from application.services.item_tracker_catalog import (
    DUNGEONS_BY_KEY,
    chest_total,
    dungeon_items,
    mode_from_settings,
)
from application.tenant_context import tenant_scope
from models import (
    FeatureFlag,
    GeneratedSeeds,
    ItemTrackerChange,
    Match,
    MatchPlayers,
    Role,
    Tenant,
    TenantFeatureFlag,
    Tournament,
    Tracker,
    UserRole,
)
from tests.factories import make_user


async def _match(*, randomizer='alttpr', settings=None, base=9100):
    t = await Tournament.create(name='ALTTPR Main', seed_generator=randomizer)
    seed = await GeneratedSeeds.create(
        seed_url='https://alttpr.com/h/abc', randomizer=randomizer, settings_snapshot=settings,
    )
    m = await Match.create(tournament=t, scheduled_at=datetime.now(timezone.utc), generated_seed=seed)
    runners = []
    for i in range(2):
        u = await make_user(discord_id=base + i, username=f'runner{base + i}')
        await MatchPlayers.create(match=m, user=u)
        runners.append(u)
    tracker = await make_user(discord_id=base + 50, username=f'tracker{base}')
    await Tracker.create(match=m, user=tracker, approved=True)
    return m, runners, tracker


async def _open(match, who):
    return await ItemTrackerService().open_match(match.id, who)


class TestWhoCanTrack:
    async def test_approved_tracker_opens_one_board_per_runner(self, db):
        m, runners, tracker = await _match()
        view = await _open(m, tracker)
        assert [r.runner.id for r in view.runners] == [u.id for u in runners]
        assert view.runners[0].board.overlay_token != view.runners[1].board.overlay_token

    async def test_opening_twice_reuses_the_boards(self, db):
        m, _, tracker = await _match()
        first = await _open(m, tracker)
        again = await _open(m, tracker)
        assert [r.board.id for r in first.runners] == [r.board.id for r in again.runners]

    async def test_the_runner_cannot_track_themselves(self, db):
        m, runners, _ = await _match()
        with pytest.raises(ValueError, match='approved trackers'):
            await _open(m, runners[0])

    async def test_an_unapproved_tracker_cannot(self, db):
        m, _, _ = await _match()
        waiting = await make_user(discord_id=9300, username='waiting')
        await Tracker.create(match=m, user=waiting, approved=False)
        with pytest.raises(ValueError, match='approved trackers'):
            await _open(m, waiting)

    async def test_broadcast_tech_and_staff_can(self, db):
        m, _, _ = await _match()
        tech = await make_user(discord_id=9301, username='tech')
        staff = await make_user(discord_id=9302, username='staff')
        await UserRole.create(user=tech, role=Role.STREAM_MANAGER)
        await UserRole.create(user=staff, role=Role.STAFF)
        assert (await _open(m, tech)).runners
        assert (await _open(m, staff)).runners

    async def test_not_for_other_games(self, db):
        m, _, tracker = await _match(randomizer='ootr')
        with pytest.raises(ValueError, match='A Link to the Past'):
            await _open(m, tracker)

    async def test_sheet_link_only_for_people_who_can_track(self, db):
        m, runners, tracker = await _match()
        service = ItemTrackerService()
        assert await service.sheet_link(m.id, tracker) == f'/tracker/{m.id}'
        assert await service.sheet_link(m.id, runners[0]) is None
        assert await service.sheet_link(m.id, None) is None


class TestMarking:
    async def test_a_mark_updates_state_and_logs_it(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'hookshot', 1, tracker)
        await service.set_level(board.id, 'sword', 2, tracker)
        fresh = await service.board(board.id, tracker)
        assert fresh.board.state == {'hookshot': 1, 'sword': 2}
        assert [e.text for e in fresh.log] == ['Sword: Master', 'Hookshot']

    async def test_levels_clamp_to_the_item(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'sword', 99, tracker)
        await service.set_level(board.id, 'bottles', -3, tracker)
        assert (await service.board(board.id, tracker)).board.state == {'sword': 4}

    async def test_unknown_keys_are_refused(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        with pytest.raises(ValueError, match="isn't something"):
            await ItemTrackerService().set_level(board.id, 'triforce', 1, tracker)

    async def test_keys_only_on_seeds_that_shuffle_them(self, db):
        m, _, tracker = await _match(settings={'dungeon_items': 'standard'})
        board = (await _open(m, tracker)).runners[0].board
        with pytest.raises(ValueError):
            await ItemTrackerService().set_level(board.id, 'pod.keys', 1, tracker)

    async def test_keysanity_seed_counts_keys_and_big_keys(self, db):
        m, _, tracker = await _match(settings={'dungeon_items': 'full'})
        view = await _open(m, tracker)
        assert view.items.mode == 'full' and view.items_source == 'seed'
        board = view.runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'pod.keys', 9, tracker)
        await service.set_level(board.id, 'pod.big_key', 1, tracker)
        await service.set_level(board.id, 'pod.chests', 99, tracker)
        assert (await service.board(board.id, tracker)).board.state == {
            'pod.keys': 6, 'pod.big_key': 1, 'pod.chests': 14,
        }

    async def test_staff_can_override_the_seed_mode(self, db):
        m, _, tracker = await _match(settings={'dungeon_items': 'standard'})
        await ItemTrackerService().set_dungeon_items(m.id, 'mcs', tracker)
        view = await _open(m, tracker)
        assert view.items.mode == 'mcs' and view.items_source == 'override'

    async def test_a_mark_on_another_match_board_needs_that_match(self, db):
        m, _, tracker = await _match()
        other, _, other_tracker = await _match(base=9500)
        board = (await _open(other, other_tracker)).runners[0].board
        with pytest.raises(ValueError, match='approved trackers'):
            await ItemTrackerService().set_level(board.id, 'hookshot', 1, tracker)
        assert m.id != other.id


class TestUndo:
    async def test_undo_restores_the_level_before(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'sword', 1, tracker)
        await service.set_level(board.id, 'sword', 2, tracker)
        log = (await service.board(board.id, tracker)).log
        await service.undo(board.id, log[0].id, tracker)
        fresh = await service.board(board.id, tracker)
        assert fresh.board.state['sword'] == 1
        assert fresh.log[0].text == 'Undid: Sword: Fighter'

    async def test_an_overwritten_change_cannot_be_undone_alone(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'sword', 1, tracker)
        await service.set_level(board.id, 'sword', 2, tracker)
        log = (await service.board(board.id, tracker)).log
        assert [e.can_undo for e in log] == [True, False]
        with pytest.raises(ValueError, match='changed since'):
            await service.undo(board.id, log[1].id, tracker)

    async def test_undo_last_skips_what_cannot_be_undone(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'hookshot', 1, tracker)
        await service.undo_last(board.id, tracker)
        await service.undo_last(board.id, tracker)
        assert (await service.board(board.id, tracker)).board.state == {'hookshot': 0}

    async def test_reset_clears_board_and_log(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'hookshot', 1, tracker)
        await service.reset(board.id, tracker)
        fresh = await service.board(board.id, tracker)
        assert fresh.board.state == {} and fresh.log == []


class TestStreamCopy:
    async def test_overlay_without_delay_shows_the_board(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        await service.set_level(board.id, 'hammer', 1, tracker)
        view = await service.overlay(board.overlay_token)
        assert view is not None and view.state == {'hammer': 1}
        assert view.runner_name == 'runner9100'

    async def test_overlay_runs_behind_by_the_delay(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        service = ItemTrackerService()
        assert await service.set_delay(m.id, 30, tracker) == 30
        await service.set_level(board.id, 'hammer', 1, tracker)
        old = await service.set_level(board.id, 'lamp', 1, tracker)
        await ItemTrackerChange.filter(board_id=old.id, key='lamp').update(
            created_at=datetime.now(timezone.utc) - timedelta(seconds=45),
        )
        view = await service.overlay(board.overlay_token)
        assert view.state == {'hammer': 0, 'lamp': 1}
        assert await service.preview(board.id, tracker) == {'hammer': 0, 'lamp': 1}

    async def test_delay_applies_to_every_runner_and_is_capped(self, db):
        m, _, tracker = await _match()
        view = await _open(m, tracker)
        assert await ItemTrackerService().set_delay(m.id, 5000, tracker) == its.MAX_DELAY_SECONDS
        again = await _open(m, tracker)
        assert {r.board.delay_seconds for r in again.runners} == {its.MAX_DELAY_SECONDS}
        assert view.delay_seconds == 0

    async def test_a_bad_token_shows_nothing(self, db):
        await _match()
        assert await ItemTrackerService().overlay('nope') is None
        assert await ItemTrackerService().overlay('') is None


class TestFlag:
    async def test_every_entry_refuses_when_off(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        await TenantFeatureFlag.filter(flag=FeatureFlag.ITEM_TRACKER.value).update(enabled=False)
        reset_flag_cache()
        service = ItemTrackerService()
        with pytest.raises(FeatureDisabledError):
            await service.open_match(m.id, tracker)
        with pytest.raises(FeatureDisabledError):
            await service.set_level(board.id, 'hookshot', 1, tracker)
        with pytest.raises(FeatureDisabledError):
            await service.overlay(board.overlay_token)
        assert await service.sheet_link(m.id, tracker) is None


class TestCatalog:
    def test_mode_from_alttpr_customizer_settings(self):
        assert mode_from_settings({'dungeon_items': 'standard', 'custom': {'region.wildKeys': False}}) == 'standard'
        assert mode_from_settings({'custom': {'region.wildKeys': True}}) == 'mcs'
        assert mode_from_settings({'custom': {'region.wildBigKeys': True, 'region.wildKeys': True}}) == 'full'
        assert mode_from_settings({'dungeon_items': 'mc'}) == 'mc'
        assert mode_from_settings(None) is None
        assert mode_from_settings({'goal': 'ganon'}) is None

    def test_chest_totals_match_the_standard_tracker_counts(self):
        standard = {k: chest_total(DUNGEONS_BY_KEY[k], dungeon_items('standard'))
                    for k in ('ep', 'dp', 'toh', 'pod', 'sp', 'sw', 'tt', 'ip', 'mm', 'tr', 'gt')}
        assert standard == {'ep': 3, 'dp': 2, 'toh': 2, 'pod': 5, 'sp': 6, 'sw': 2, 'tt': 4,
                            'ip': 3, 'mm': 2, 'tr': 5, 'gt': 20}
        full = {k: chest_total(DUNGEONS_BY_KEY[k], dungeon_items('full'))
                for k in ('ep', 'dp', 'toh', 'pod', 'sp', 'sw', 'tt', 'ip', 'mm', 'tr', 'gt')}
        assert full == {'ep': 6, 'dp': 6, 'toh': 6, 'pod': 14, 'sp': 10, 'sw': 8, 'tt': 8,
                        'ip': 8, 'mm': 8, 'tr': 12, 'gt': 27}

    def test_every_sprite_ships(self):
        from pathlib import Path

        from application.services.item_tracker_catalog import (
            DUNGEONS,
            ITEMS,
            MEDALLION_SPRITES,
            PRIZE_SPRITES,
        )
        root = Path(__file__).resolve().parents[2] / 'static' / 'item_tracker'
        stems = {s for i in ITEMS for s in i.sprites} | set(PRIZE_SPRITES) | set(MEDALLION_SPRITES)
        stems |= {s for d in DUNGEONS if d.boss for s in d.boss} | {'smallkey1', 'bigkey0', 'bigkey1'}
        assert not [s for s in sorted(stems) if not (root / f'{s}.png').exists()]


class TestCommunityWalls:
    async def test_another_community_cannot_reach_a_board(self, db):
        m, _, tracker = await _match()
        board = (await _open(m, tracker)).runners[0].board
        other = await Tenant.create(name='Other', slug='other')
        await TenantFeatureFlag.create(
            tenant=other, flag=FeatureFlag.ITEM_TRACKER.value, available=True, enabled=True,
        )
        their_staff = await make_user(discord_id=9900, username='their_staff')
        await UserRole.create(user=their_staff, role=Role.STAFF, tenant=other)
        service = ItemTrackerService()
        with tenant_scope(other.id):
            reset_flag_cache()
            assert await service.overlay(board.overlay_token) is None
            with pytest.raises(NotFoundError):
                await service.board(board.id, their_staff)
            with pytest.raises(NotFoundError):
                await service.set_level(board.id, 'bow', 1, their_staff)
            with pytest.raises(ValueError):
                await service.open_match(m.id, their_staff)
        reset_flag_cache()
