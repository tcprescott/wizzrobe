"""Tenant isolation for :class:`ProviderTask`.

A provider task carries an upstream handle, the settings that were sent, and the
match the resulting seed belongs to. A read that crossed tenants would let one
community's board claim another's in-flight roll — and, because completing a
task records a seed and DMs the players, deliver one community's seed into
another's match.

The worker's own scan is deliberately cross-tenant (it owns the whole queue),
so the test that matters is that it re-enters each task's tenant before acting,
and that every request-facing read stays scoped.
"""

import pytest

from application.repositories import ProviderTaskRepository
from application.tenant_context import tenant_scope
from models import Match, ProviderTask, ProviderTaskStatus, Tournament


@pytest.fixture
async def tasks_in_both(two_tenants):
    a, b = two_tenants
    matches, tasks = {}, {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            tournament = await Tournament.create(name=f'Event {tenant.slug}', tenant=tenant)
            matches[tenant.id] = await Match.create(tournament=tournament, tenant=tenant)
            tasks[tenant.id] = await ProviderTaskRepository.create_queued(
                provider='dk64r',
                operation='generate_seed',
                match_id=matches[tenant.id].id,
                # Needs an upstream handle: without one the worker scan skips it as a submit in flight.
                provider_task_id=f'up-{tenant.slug}',
            )
    return a, b, matches, tasks


async def test_get_by_id_does_not_cross_tenants(tasks_in_both):
    a, b, _, tasks = tasks_in_both
    with tenant_scope(a.id):
        assert await ProviderTaskRepository.get_with_relations(tasks[a.id].id) is not None
        assert await ProviderTaskRepository.get_with_relations(tasks[b.id].id) is None
    with tenant_scope(b.id):
        assert await ProviderTaskRepository.get_with_relations(tasks[b.id].id) is not None
        assert await ProviderTaskRepository.get_with_relations(tasks[a.id].id) is None


async def test_active_for_match_does_not_cross_tenants(tasks_in_both):
    a, b, matches, tasks = tasks_in_both
    with tenant_scope(a.id):
        found = await ProviderTaskRepository.active_for_match(matches[a.id].id)
        assert found is not None and found.id == tasks[a.id].id
        assert await ProviderTaskRepository.active_for_match(matches[b.id].id) is None


async def test_board_state_does_not_cross_tenants(tasks_in_both):
    a, b, matches, tasks = tasks_in_both
    with tenant_scope(a.id):
        rolling, failed = await ProviderTaskRepository.board_state_for_matches(
            [matches[a.id].id, matches[b.id].id],
        )
        assert set(rolling) == {matches[a.id].id}
        assert failed == {}


async def test_create_stamps_the_ambient_tenant(two_tenants):
    a, b = two_tenants
    with tenant_scope(b.id):
        task = await ProviderTaskRepository.create_queued(
            provider='dk64r', operation='generate_seed',
        )
    assert task.tenant_id == b.id


async def test_worker_scan_is_cross_tenant_by_design(tasks_in_both):
    # The poller must see the whole queue; scoping this query would be the bug.
    a, b, _, tasks = tasks_in_both
    with tenant_scope(a.id):
        due = await ProviderTaskRepository.due_for_poll()
    assert {t.id for t in due} == {tasks[a.id].id, tasks[b.id].id}


async def test_claim_transitions_only_once(tasks_in_both):
    a, _, _, tasks = tasks_in_both
    task = tasks[a.id]
    assert await ProviderTaskRepository.claim(task, ProviderTaskStatus.SUCCEEDED) is True
    stale = await ProviderTask.filter(id=task.id).first()
    stale.status = ProviderTaskStatus.QUEUED
    assert await ProviderTaskRepository.claim(stale, ProviderTaskStatus.SUCCEEDED) is False
