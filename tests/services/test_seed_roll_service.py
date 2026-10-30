"""The persisted seed roll: submit, poll, complete — and survive a restart.

The behaviour that justifies :class:`ProviderTask` existing at all is here: a
task submitted by one process is completed by another, because the poller reads
its work from the table rather than from a local variable.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.provider_task_service import TASK_MAX_AGE, ProviderTaskService
from application.services.seed_roll_service import SeedRollService
from application.services.seedgen_service import AsyncRollPoll, AsyncRollSubmission, RolledSeed
from models import (
    GeneratedSeeds,
    Match,
    MatchPlayers,
    ProviderTask,
    ProviderTaskStatus,
    Tournament,
    User,
)


def _submission(task_id='up-1'):
    return AsyncRollSubmission(
        provider_task_id=task_id,
        provider_params={'branch': 'stable'},
        settings={'branch': 'stable', 'generate_spoilerlog': False},
    )


def _finished(url='https://dk64randomizer.com/randomizer.html?seed_id=42'):
    return AsyncRollPoll(
        seed=RolledSeed(url=url, settings={'branch': 'stable'}),
        verification_hash=[6, 2, 8, 8, 4],
    )


@pytest.fixture
async def match_with_players(db):
    tournament = await Tournament.create(name='T', seed_generator='dk64r')
    match = await Match.create(tournament=tournament)
    await MatchPlayers.create(
        match=match,
        user=await User.create(discord_id=7001, username='p1', dm_notifications=False),
    )
    return match


@pytest.fixture
async def requester(db):
    return await User.create(discord_id=7002, username='staff')


@pytest.fixture
async def queued_task(match_with_players, requester):
    return await ProviderTaskService().queue(
        provider='dk64r', operation='generate_seed', actor=requester,
        match_id=match_with_players.id,
    )


class TestSubmit:
    async def test_records_the_upstream_handle(self, queued_task, monkeypatch):
        service = SeedRollService()
        monkeypatch.setattr(
            service.seedgen_service, 'submit_async_roll',
            _async_returning(_submission('up-99')),
        )

        task = await service.submit(queued_task)

        assert task.status == ProviderTaskStatus.RUNNING
        assert task.provider_task_id == 'up-99'
        assert task.provider_params == {'branch': 'stable'}
        assert task.settings_snapshot['generate_spoilerlog'] is False

    async def test_upstream_failure_is_recorded_not_raised(self, queued_task, monkeypatch):
        service = SeedRollService()
        monkeypatch.setattr(
            service.seedgen_service, 'submit_async_roll', _async_raising(RuntimeError('boom')),
        )

        task = await service.submit(queued_task)

        assert task.status == ProviderTaskStatus.FAILED
        assert 'boom' in task.error

    async def test_misconfiguration_raises_and_leaves_no_row(self, queued_task, monkeypatch):
        service = SeedRollService()
        monkeypatch.setattr(
            service.seedgen_service, 'submit_async_roll',
            _async_raising(ValueError('API key is not configured')),
        )

        with pytest.raises(ValueError, match='not configured'):
            await service.submit(queued_task)

        assert await ProviderTask.filter(id=queued_task.id).exists() is False


class TestPollAndComplete:
    async def test_still_running_changes_nothing(self, queued_task, monkeypatch):
        service = SeedRollService()
        await service.task_service.mark_submitted(
            queued_task, provider_task_id='up-1',
            provider_params={'branch': 'stable'}, settings={'branch': 'stable'},
        )
        monkeypatch.setattr(
            service.seedgen_service, 'poll_async_roll', _async_returning(AsyncRollPoll(seed=None)),
        )

        status = await service.poll(queued_task)

        assert status == ProviderTaskStatus.RUNNING
        assert await GeneratedSeeds.all().count() == 0

    async def test_a_finished_task_seeds_the_match(
        self, queued_task, match_with_players, monkeypatch,
    ):
        service = SeedRollService()
        await service.task_service.mark_submitted(
            queued_task, provider_task_id='up-1',
            provider_params={'branch': 'stable'}, settings={'branch': 'stable'},
        )
        monkeypatch.setattr(
            service.seedgen_service, 'poll_async_roll', _async_returning(_finished()),
        )

        status = await service.poll(queued_task)

        assert status == ProviderTaskStatus.SUCCEEDED
        refreshed = await Match.get(id=match_with_players.id)
        assert refreshed.generated_seed_id is not None
        seed = await GeneratedSeeds.get(id=refreshed.generated_seed_id)
        assert seed.seed_url.endswith('seed_id=42')
        assert seed.provider_meta['verification_hash'] == [6, 2, 8, 8, 4]

    async def test_a_second_tick_does_not_seed_twice(
        self, queued_task, match_with_players, monkeypatch,
    ):
        service = SeedRollService()
        await service.task_service.mark_submitted(
            queued_task, provider_task_id='up-1',
            provider_params={'branch': 'stable'}, settings={'branch': 'stable'},
        )
        monkeypatch.setattr(
            service.seedgen_service, 'poll_async_roll', _async_returning(_finished()),
        )

        await service.poll(queued_task)
        stale = await ProviderTask.get(id=queued_task.id)
        stale.status = ProviderTaskStatus.RUNNING
        await service.poll(stale)

        assert await GeneratedSeeds.all().count() == 1

    async def test_a_failed_task_records_the_error(self, queued_task, monkeypatch):
        service = SeedRollService()
        await service.task_service.mark_submitted(
            queued_task, provider_task_id='up-1',
            provider_params={'branch': 'stable'}, settings={'branch': 'stable'},
        )
        monkeypatch.setattr(
            service.seedgen_service, 'poll_async_roll',
            _async_raising(ValueError('DK64 Randomizer failed to generate the seed.')),
        )

        status = await service.poll(queued_task)

        assert status == ProviderTaskStatus.FAILED
        refreshed = await ProviderTask.get(id=queued_task.id)
        assert 'failed to generate' in refreshed.error
        assert await GeneratedSeeds.all().count() == 0

    async def test_a_submit_still_in_flight_is_left_alone(self, queued_task):
        """A tick that lands mid-submit must not kill a live roll.

        QUEUED with no upstream handle is both "the submit died" and "the submit
        has not answered yet", and nothing at this instant can tell them apart.
        Failing here would mean a 5-second tick occasionally destroying a roll
        whose provider simply took a moment to reply.
        """
        status = await SeedRollService().poll(queued_task)

        assert status == ProviderTaskStatus.QUEUED
        refreshed = await ProviderTask.get(id=queued_task.id)
        assert refreshed.status == ProviderTaskStatus.QUEUED
        assert refreshed.error is None

    async def test_an_unsubmitted_task_is_not_even_scanned(self, queued_task):
        due = await ProviderTaskService().due_for_poll()
        assert queued_task.id not in [t.id for t in due]

    async def test_a_submit_that_never_landed_is_swept_eventually(self, queued_task):
        await ProviderTask.filter(id=queued_task.id).update(
            created_at=datetime.now(timezone.utc) - TASK_MAX_AGE - timedelta(minutes=1),
        )

        assert await ProviderTaskService().sweep_stale() == 1

        refreshed = await ProviderTask.get(id=queued_task.id)
        assert refreshed.status == ProviderTaskStatus.ABANDONED


class TestSurvivesRestart:
    async def test_a_task_submitted_before_a_restart_is_completed_after_one(
        self, queued_task, match_with_players, monkeypatch,
    ):
        """The whole point of the table.

        The submitting process is gone; a *new* service instance picks the task
        up from the queue and finishes it. Nothing is carried over in memory —
        the second half reads only what the first half persisted.
        """
        submitting = SeedRollService()
        monkeypatch.setattr(
            submitting.seedgen_service, 'submit_async_roll',
            _async_returning(_submission('up-survives')),
        )
        await submitting.submit(queued_task)
        del submitting

        after_restart = SeedRollService()
        monkeypatch.setattr(
            after_restart.seedgen_service, 'poll_async_roll', _async_returning(_finished()),
        )
        due = await after_restart.task_service.due_for_poll()
        assert [t.provider_task_id for t in due] == ['up-survives']
        await after_restart.poll(due[0])

        refreshed = await Match.get(id=match_with_players.id)
        assert refreshed.generated_seed_id is not None


class TestSweepStale:
    async def test_abandons_a_task_that_outlived_its_budget(self, queued_task):
        await ProviderTask.filter(id=queued_task.id).update(
            created_at=datetime.now(timezone.utc) - TASK_MAX_AGE - timedelta(minutes=1),
        )

        assert await ProviderTaskService().sweep_stale() == 1

        refreshed = await ProviderTask.get(id=queued_task.id)
        assert refreshed.status == ProviderTaskStatus.ABANDONED
        assert refreshed.completed_at is not None

    async def test_leaves_a_young_task_alone(self, queued_task):
        assert await ProviderTaskService().sweep_stale() == 0
        refreshed = await ProviderTask.get(id=queued_task.id)
        assert refreshed.status == ProviderTaskStatus.QUEUED


def _async_returning(value):
    async def _call(*args, **kwargs):
        return value
    return _call


def _async_raising(exc):
    async def _call(*args, **kwargs):
        raise exc
    return _call
