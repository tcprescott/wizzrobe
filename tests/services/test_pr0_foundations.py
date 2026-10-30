"""Tests for the online-tournament foundations (PR 0).

Covers the three shared primitives: the reserved system ``User`` accessor, the
hybrid-config validator, and the strategy registry. The AuthService role gates
live in ``test_auth_service.py``; ``TournamentService`` config wiring lives in
``test_tournament_service.py``.
"""

import pytest

from application.services.tournament_config import (
    TournamentConfig,
    validate_tournament_config,
)
from application.services.tournament_strategies import (
    available_strategies,
    get_strategy,
    register_strategy,
)
from application.services.user_service import UserService
from models import SYSTEM_USER_DISCORD_ID, User


class TestValidateTournamentConfig:
    def test_none_passes_through(self):
        assert validate_tournament_config(None) is None

    def test_empty_dict_round_trips(self):
        assert validate_tournament_config({}) == {}

    def test_valid_messaging_templates_round_trip(self):
        cfg = {'messaging_templates': {'welcome': 'hi {player}'}}
        assert validate_tournament_config(cfg) == cfg

    def test_unset_keys_dropped(self):
        # exclude_none: a config that only names an absent optional stays empty.
        assert validate_tournament_config({'messaging_templates': None}) == {}

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match='Invalid tournament config'):
            validate_tournament_config({'not_a_real_key': 1})

    def test_wrong_value_type_raises(self):
        with pytest.raises(ValueError, match='Invalid tournament config'):
            validate_tournament_config({'messaging_templates': 'should be a dict'})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match='must be an object'):
            validate_tournament_config(['not', 'a', 'dict'])

    def test_model_forbids_extra(self):
        # The extra='forbid' setting is what turns unknown keys into errors.
        assert TournamentConfig.model_config.get('extra') == 'forbid'


class TestStrategyRegistry:
    def test_register_and_lookup(self):
        @register_strategy('test_kind', 'alpha')
        class Alpha:
            pass

        assert get_strategy('test_kind', 'alpha') is Alpha
        assert 'alpha' in available_strategies('test_kind')

    def test_duplicate_registration_raises(self):
        @register_strategy('test_kind', 'beta')
        class Beta:
            pass

        with pytest.raises(ValueError, match='already registered'):
            register_strategy('test_kind', 'beta')(Beta)

    def test_unknown_lookup_raises(self):
        with pytest.raises(ValueError, match='Unknown strategy'):
            get_strategy('test_kind', 'does_not_exist')

    def test_available_is_scoped_by_kind_and_sorted(self):
        @register_strategy('sorted_kind', 'zed')
        class Zed:
            pass

        @register_strategy('sorted_kind', 'aardvark')
        class Aardvark:
            pass

        assert available_strategies('sorted_kind') == ['aardvark', 'zed']
        assert available_strategies('nonexistent_kind') == []


class TestSystemUser:
    async def test_creates_reserved_row(self, db):
        service = UserService()
        user = await service.get_system_user()

        assert user.discord_id == SYSTEM_USER_DISCORD_ID
        assert user.is_system is True
        assert user.username == 'System'
        # A real username to snapshot — the reason for a row over a bare -1.
        assert user.username

    async def test_idempotent_single_row(self, db):
        service = UserService()
        first = await service.get_system_user()
        second = await service.get_system_user()

        assert first.id == second.id
        assert await User.filter(discord_id=SYSTEM_USER_DISCORD_ID).count() == 1

    async def test_usable_as_audit_actor(self, db):
        from application.services.audit_service import AuditService

        service = UserService()
        system = await service.get_system_user()

        log = await AuditService().write_log(system, 'system.ping', {'k': 'v'})
        assert log.user_id == system.id
        # Actor identity is snapshotted into details for a real (non-sentinel) name.
        import json
        assert json.loads(log.details)['actor_username'] == 'System'
