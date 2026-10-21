"""Coverage for standalone utility modules.

These are all pure helpers — no DB, no Discord connection, no network. Env-var
helpers are exercised by monkeypatching ``os.environ`` (they read lazily on each
call), message builders by asserting the exact formatted strings, and the QR
helper by decoding the PNG/data-URI it produces.
"""

import base64

import pytest

from application.utils import discord_embeds as de
from application.utils import discord_messages as dm
from application.utils import discord_messages_crew as dm_crew
from application.utils import discord_messages_volunteer as dm_vol
from application.utils import easter_eggs, environment, qrcode_util
from application.utils.http_headers import header_safe

# ---------------------------------------------------------------------------
# environment.py
# ---------------------------------------------------------------------------

class TestGetEnvironment:
    def test_default_is_development(self, monkeypatch):
        monkeypatch.delenv('ENVIRONMENT', raising=False)
        assert environment.get_environment() == 'development'

    def test_normalizes_case_and_whitespace(self, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', '  Production  ')
        assert environment.get_environment() == 'production'

    def test_is_production_true(self, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', 'PRODUCTION')
        assert environment.is_production() is True

    def test_is_production_false_for_development(self, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', 'development')
        assert environment.is_production() is False


class TestGetBaseUrl:
    def test_default(self, monkeypatch):
        monkeypatch.delenv('BASE_URL', raising=False)
        assert environment.get_base_url() == 'http://localhost:8000'

    def test_strips_trailing_slashes(self, monkeypatch):
        monkeypatch.setenv('BASE_URL', 'https://example.com/')
        assert environment.get_base_url() == 'https://example.com'

    def test_custom_no_trailing_slash_unchanged(self, monkeypatch):
        monkeypatch.setenv('BASE_URL', 'https://sgl.example.org')
        assert environment.get_base_url() == 'https://sgl.example.org'


class TestTelemetryEnabled:
    def test_unset_defaults_to_enabled(self, monkeypatch):
        monkeypatch.delenv('TELEMETRY_ENABLED', raising=False)
        assert environment.telemetry_enabled() is True

    @pytest.mark.parametrize('value', ['1', 'true', 'YES', 'On', ' true '])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv('TELEMETRY_ENABLED', value)
        assert environment.telemetry_enabled() is True

    @pytest.mark.parametrize('value', ['0', 'false', 'no', 'off', '', 'nonsense'])
    def test_falsey_values(self, monkeypatch, value):
        monkeypatch.setenv('TELEMETRY_ENABLED', value)
        assert environment.telemetry_enabled() is False


class TestSessionStorage:
    def test_unset_means_file_backed(self, monkeypatch):
        monkeypatch.delenv('NICEGUI_REDIS_URL', raising=False)
        assert environment.session_storage_url() == ''
        assert environment.validate_session_storage() is None

    def test_blank_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv('NICEGUI_REDIS_URL', '   ')
        assert environment.session_storage_url() == ''
        assert environment.validate_session_storage() is None

    def test_reachable_redis_passes(self, monkeypatch):
        monkeypatch.setenv('NICEGUI_REDIS_URL', 'redis://localhost:6379/0')
        pinged = {}

        class FakeClient:
            def ping(self):
                pinged['called'] = True
                return True

            def close(self):
                pinged['closed'] = True

        monkeypatch.setattr('redis.Redis.from_url', lambda url, **kw: FakeClient())
        assert environment.validate_session_storage() is None
        assert pinged == {'called': True, 'closed': True}

    def test_unreachable_redis_aborts_startup(self, monkeypatch):
        """A bad URL must fail at boot, not at the first person's login."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        monkeypatch.setenv('NICEGUI_REDIS_URL', 'redis://nope:6379/0')

        def explode(url, **kw):
            raise RedisConnectionError('name does not resolve')

        monkeypatch.setattr('redis.Redis.from_url', explode)
        with pytest.raises(RuntimeError, match='Redis is not reachable'):
            environment.validate_session_storage()

    def test_client_is_closed_even_when_ping_fails(self, monkeypatch):
        from redis.exceptions import TimeoutError as RedisTimeoutError

        monkeypatch.setenv('NICEGUI_REDIS_URL', 'redis://localhost:6379/0')
        closed = {}

        class FakeClient:
            def ping(self):
                raise RedisTimeoutError('timed out')

            def close(self):
                closed['yes'] = True

        monkeypatch.setattr('redis.Redis.from_url', lambda url, **kw: FakeClient())
        with pytest.raises(RuntimeError, match='Redis is not reachable'):
            environment.validate_session_storage()
        assert closed == {'yes': True}


class TestValidateSecurityConfig:
    def test_missing_storage_secret_raises(self, monkeypatch):
        monkeypatch.delenv('STORAGE_SECRET', raising=False)
        monkeypatch.setenv('ENVIRONMENT', 'development')
        with pytest.raises(RuntimeError, match='STORAGE_SECRET is required'):
            environment.validate_security_config()

    def test_blank_storage_secret_raises(self, monkeypatch):
        monkeypatch.setenv('STORAGE_SECRET', '   ')
        monkeypatch.setenv('ENVIRONMENT', 'development')
        with pytest.raises(RuntimeError, match='STORAGE_SECRET is required'):
            environment.validate_security_config()

    def test_development_short_secret_ok(self, monkeypatch):
        """Outside production a short secret is accepted (no length/DB checks)."""
        monkeypatch.setenv('STORAGE_SECRET', 'short')
        monkeypatch.setenv('ENVIRONMENT', 'development')
        monkeypatch.delenv('DB_USERNAME', raising=False)
        monkeypatch.delenv('DB_PASSWORD', raising=False)
        assert environment.validate_security_config() is None

    def test_production_short_secret_raises(self, monkeypatch):
        monkeypatch.setenv('STORAGE_SECRET', 'a' * 31)
        monkeypatch.setenv('ENVIRONMENT', 'production')
        with pytest.raises(RuntimeError, match='at least 32 characters'):
            environment.validate_security_config()

    def test_production_missing_db_username_raises(self, monkeypatch):
        monkeypatch.setenv('STORAGE_SECRET', 'a' * 32)
        monkeypatch.setenv('ENVIRONMENT', 'production')
        monkeypatch.delenv('DB_USERNAME', raising=False)
        monkeypatch.setenv('DB_PASSWORD', 'pw')
        with pytest.raises(RuntimeError, match='DB_USERNAME must be set'):
            environment.validate_security_config()

    def test_production_blank_db_username_raises(self, monkeypatch):
        monkeypatch.setenv('STORAGE_SECRET', 'a' * 32)
        monkeypatch.setenv('ENVIRONMENT', 'production')
        monkeypatch.setenv('DB_USERNAME', '   ')
        monkeypatch.setenv('DB_PASSWORD', 'pw')
        with pytest.raises(RuntimeError, match='DB_USERNAME must be set'):
            environment.validate_security_config()

    def test_production_missing_db_password_raises(self, monkeypatch):
        monkeypatch.setenv('STORAGE_SECRET', 'a' * 32)
        monkeypatch.setenv('ENVIRONMENT', 'production')
        monkeypatch.setenv('DB_USERNAME', 'sgl')
        monkeypatch.delenv('DB_PASSWORD', raising=False)
        with pytest.raises(RuntimeError, match='DB_PASSWORD must be set'):
            environment.validate_security_config()

    def test_production_all_present_ok(self, monkeypatch):
        monkeypatch.setenv('STORAGE_SECRET', 'a' * 40)
        monkeypatch.setenv('ENVIRONMENT', 'production')
        monkeypatch.setenv('DB_USERNAME', 'sgl')
        monkeypatch.setenv('DB_PASSWORD', 'secret')
        assert environment.validate_security_config() is None


# ---------------------------------------------------------------------------
# discord_messages.py
# ---------------------------------------------------------------------------

class TestPlayersLabel:
    def test_empty(self):
        assert dm._players_label(None) == ''
        assert dm._players_label([]) == ''

    def test_two_players_uses_vs(self):
        assert dm._players_label(['Alice', 'Bob']) == 'Alice vs Bob'

    def test_three_players_comma_joined(self):
        assert dm._players_label(['A', 'B', 'C']) == 'A, B, C'

    def test_one_player(self):
        assert dm._players_label(['Solo']) == 'Solo'


class TestMatchInfoLines:
    def test_all_fields(self):
        lines = dm._match_info_lines(
            player_names=['A', 'B'],
            scheduled_at_display='2025-01-01 10:00 EST',
            stage_name='Main Stage',
        )
        assert lines == [
            'Players: A vs B',
            'Scheduled for: 2025-01-01 10:00 EST',
            'Stage: Main Stage',
        ]

    def test_empty_data_omits_lines(self):
        assert dm._match_info_lines() == []

    def test_custom_time_label(self):
        lines = dm._match_info_lines(scheduled_at_display='soon', time_label='New time')
        assert lines == ['New time: soon']

    def test_bracket_line_leads_the_block(self):
        """It is what tells a player what the match is *for* (U4/U5)."""
        lines = dm._match_info_lines(
            player_names=['A', 'B'],
            bracket_line='Semifinals · Game 2 of 3 · Series 1-0',
        )
        assert lines == [
            'Round: Semifinals · Game 2 of 3 · Series 1-0',
            'Players: A vs B',
        ]

    def test_no_bracket_line_for_an_ordinary_match(self):
        assert dm._match_info_lines(player_names=['A']) == ['Players: A']


class TestMatchupReadyDm:
    """The one new bracket notification (D7): "you have a matchup to schedule"."""

    def test_carries_opponent_seed_format_and_link(self):
        msg = dm.matchup_ready_dm(
            'Cool Cup', 'Semifinals', 'Bob',
            opponent_seed=3, best_of=3,
            schedule_url='https://wizz.gg/t/acme/brackets/7',
        )
        assert '**Semifinals**' in msg
        assert '**Cool Cup**' in msg
        assert 'Opponent: Bob (#3)' in msg
        assert 'Format: Best of 3' in msg
        assert '[Pick a time](https://wizz.gg/t/acme/brackets/7)' in msg

    def test_a_bo1_omits_the_format_line(self):
        msg = dm.matchup_ready_dm('Cool Cup', 'Final', 'Bob')
        assert 'Format:' not in msg

    def test_rebook_variant_says_the_game_was_called_off(self):
        """A released game (U3a) must not read as a duplicate invitation."""
        msg = dm.matchup_ready_dm(
            'Cool Cup', 'Semifinals', 'Bob', rebook=True,
            schedule_url='https://wizz.gg/brackets/7',
        )
        assert 'was called off' in msg
        assert 'open to reschedule' in msg
        assert '[Pick a new time]' in msg

    def test_without_a_link_it_still_says_what_to_do(self):
        """And names the surface an *entrant* can act on.

        The bracket page's Schedule button is staff-only, so the old fallback —
        "Pick a time on the bracket." — sent the two people this DM addresses
        somewhere they could not book anything.
        """
        msg = dm.matchup_ready_dm('Cool Cup', 'Final', 'Bob')
        assert 'Pick a time on the Player tab' in msg
        assert 'bracket' not in msg.rsplit('\n\n', 1)[-1]


class TestCancelledDmRelease:
    def test_a_released_game_tells_the_players_to_rebook(self):
        msg = dm.cancelled_dm('Cool Cup', released=True)
        assert 'released' in msg
        assert 'Nothing further is needed' not in msg

    def test_an_ordinary_cancellation_is_unchanged(self):
        msg = dm.cancelled_dm('Cool Cup')
        assert "You're all set. Nothing else to do here." in msg


class TestSchedulingDms:
    def test_scheduled_dm_contains_tournament_and_body(self):
        msg = dm.scheduled_dm(
            'Cool Cup', '2025-03-01 12:00 EST',
            player_names=['A', 'B'], stage_name='Stage 1',
        )
        assert '**Cool Cup**' in msg
        assert 'Players: A vs B' in msg
        assert 'Scheduled for: 2025-03-01 12:00 EST' in msg
        assert 'Stage: Stage 1' in msg

    def test_rescheduled_dm_uses_new_time_label(self):
        msg = dm.rescheduled_dm('Cool Cup', '2025-03-02 12:00 EST', player_names=['A', 'B'])
        assert 'has moved' in msg
        assert 'New time: 2025-03-02 12:00 EST' in msg

    # Nobody who receives these is playing: players get the acknowledgment DM.
    # Each audience's copy has to be true for that reader.
    @pytest.mark.parametrize('builder', ['scheduled_dm', 'rescheduled_dm'])
    @pytest.mark.parametrize('audience', [
        dm.AUDIENCE_SUBSCRIBER, dm.AUDIENCE_CREW, dm.AUDIENCE_WATCHER,
    ])
    def test_no_audience_is_told_it_is_playing(self, builder, audience):
        msg = getattr(dm, builder)('Cup', 'later', player_names=['A', 'B'], audience=audience)
        assert 'Your match' not in msg
        assert "You've got a match" not in msg
        assert 'Good luck' not in msg

    def test_a_subscriber_is_pointed_at_the_signup_buttons(self):
        msg = dm.scheduled_dm('Cup', 'later', audience=dm.AUDIENCE_SUBSCRIBER)
        assert msg.startswith('A match was just scheduled in **Cup**.')
        assert msg.endswith('Use the buttons below.')

    def test_crew_hear_it_is_their_slot_and_how_to_drop_it(self):
        msg = dm.rescheduled_dm('Cup', 'later', audience=dm.AUDIENCE_CREW)
        assert msg.startswith("A match you're on crew for in **Cup** has moved.")
        assert 'withdraw from My Schedule' in msg

    def test_a_watcher_is_asked_to_do_nothing(self):
        msg = dm.scheduled_dm('Cup', 'later', player_names=['A', 'B'], audience=dm.AUDIENCE_WATCHER)
        assert msg.startswith("A match you're watching in **Cup**")
        assert 'buttons' not in msg and 'calendar' not in msg


class TestTournamentSignupDm:
    def test_it_no_longer_promises_staff_will_schedule(self):
        """A bracket-run tournament has players book their own games."""
        msg = dm.tournament_signup_dm('Open', can_request=False)
        assert 'Staff can now schedule' not in msg
        assert 'My Schedule' in msg and 'pick a time' in msg
        assert 'request a match' not in msg

    def test_request_route_only_where_it_exists(self):
        assert 'request a match' in dm.tournament_signup_dm('Open', can_request=True)


class TestStageDmsPerAudience:
    @pytest.mark.parametrize('builder,args', [
        ('stage_assigned_dm', ('Cup', 'Kraid', 'later')),
        ('stage_cleared_dm', ('Cup', 'later')),
        ('stage_reminder_dm', ('Cup', 'Kraid', 'later')),
    ])
    def test_only_a_player_reads_your_match(self, builder, args):
        fn = getattr(dm, builder)
        assert fn(*args, audience=dm.AUDIENCE_PLAYER).startswith('Your match in **Cup**')
        crew = fn(*args, audience=dm.AUDIENCE_CREW)
        watcher = fn(*args, audience=dm.AUDIENCE_WATCHER)
        assert crew.startswith("A match you're on crew for in **Cup**")
        assert watcher.startswith("A match you're watching in **Cup**")
        assert 'tournament room as usual' not in watcher
        assert 'rather than the tournament room' not in crew + watcher


class TestAcknowledgmentRequestDm:
    def test_rescheduled_branch(self):
        msg = dm.acknowledgment_request_dm(
            'Cup', 'later', rescheduled=True,
            stage_name='Stage', player_names=['A', 'B'],
        )
        assert 'has been rescheduled' in msg
        assert 'New time: later' in msg
        assert 'Stage: Stage' in msg
        assert 'Acknowledge' in msg

    def test_not_rescheduled_branch(self):
        msg = dm.acknowledgment_request_dm('Cup', 'noon', rescheduled=False)
        assert 'A match has been scheduled for you' in msg
        assert 'Scheduled for: noon' in msg
        assert msg.endswith("Click **Acknowledge** below to confirm you've seen this.")


class TestCheckedInAndStateChangedDms:
    def test_checked_in_with_info(self):
        msg = dm.checked_in_dm('Cup', player_names=['A', 'B'], scheduled_at_display='noon')
        assert "You're checked in" in msg
        assert 'Players: A vs B' in msg
        assert msg.endswith('about to begin — good luck!')

    def test_checked_in_without_info_has_no_block(self):
        msg = dm.checked_in_dm('Cup')
        assert "You're checked in" in msg
        # No players/time/stage block when nothing supplied.
        assert 'Players:' not in msg

    def test_state_changed_with_info_appends_block(self):
        msg = dm.state_changed_dm('Cup', 'IN_PROGRESS', player_names=['A', 'B'])
        assert '**IN_PROGRESS**' in msg
        assert 'Players: A vs B' in msg

    def test_state_changed_without_info(self):
        msg = dm.state_changed_dm('Cup', 'DONE')
        assert msg == 'Your match in **Cup** is now: **DONE**.'


class TestStreamCandidateAndSeedDms:
    def test_stream_candidate_dm(self):
        msg = dm.stream_candidate_dm('Cup', 'noon', player_names=['A', 'B'])
        assert 'might be heading to stream' in msg
        assert 'Players: A vs B' in msg
        assert msg.endswith('Use the buttons below to sign up as crew.')

    def test_seed_dm_with_info(self):
        msg = dm.seed_dm(
            'Alice', 'Cup', 'https://seed.example/abc',
            player_names=['Alice', 'Bob'], scheduled_at_display='noon',
        )
        assert msg.startswith('Hello Alice,')
        assert 'https://seed.example/abc' in msg
        assert 'Players: Alice vs Bob' in msg
        assert msg.endswith('Good luck and have fun!')

    def test_seed_dm_without_info_omits_block(self):
        msg = dm.seed_dm('Alice', 'Cup', 'https://seed.example/abc')
        assert 'Players:' not in msg
        assert 'https://seed.example/abc' in msg


class TestCrewAssignmentDm:
    def test_all_fields(self):
        msg = dm_crew.crew_assignment_dm(
            'Commentator', 'Grand Final', 'noon', 'Stage 2', ['A', 'B'],
        )
        assert "You're confirmed as Commentator for this match!" in msg
        assert '**Match:** Grand Final' in msg
        assert '**Players:** A vs B' in msg
        assert '**Scheduled:** noon' in msg
        assert '**Stage:** Stage 2' in msg
        assert msg.endswith('Please click below to acknowledge your assignment.')

    def test_optional_fields_suppressed(self):
        msg = dm_crew.crew_assignment_dm('Tracker', None, '', None, None)
        assert "You're confirmed as Tracker for this match!" in msg
        assert '**Match:**' not in msg
        assert '**Players:**' not in msg
        assert '**Scheduled:**' not in msg
        assert '**Stage:**' not in msg


class TestCrewWithdrawnDm:
    """The DM to whoever owns the crew when an approved member drops out."""

    def test_names_the_person_and_the_match(self):
        msg = dm_crew.crew_withdrawn_dm(
            'commentator', 'Player Three', 'Grand Final', 'noon', 'Stage 2', ['A', 'B'],
        )
        assert '**Player Three** has withdrawn as commentator.' in msg
        assert '**Match:** Grand Final' in msg
        assert '**Players:** A vs B' in msg
        assert msg.endswith('The slot is open again — the match needs someone else on it.')

    def test_hours_notice_reads_as_urgency(self):
        def notice(hours):
            return dm_crew.crew_withdrawn_dm(
                'tracker', 'Ann', None, '', None, None, hours_notice=hours,
            ).splitlines()[0]

        assert notice(0.5).endswith('in under an hour.')
        assert notice(1.2).endswith('in about 1 hour.')
        assert notice(5.0).endswith('in about 5 hours.')
        # Far out enough that the countdown says nothing useful.
        assert notice(96.0).endswith('withdrawn as tracker.')
        # A match already past its scheduled time must not read as "in under an hour".
        assert notice(-2.0).endswith('already past its scheduled time.')

    def test_no_notice_when_the_match_is_unscheduled(self):
        msg = dm_crew.crew_withdrawn_dm('tracker', 'Ann', None, '', None, None)
        assert msg.splitlines()[0] == '**Ann** has withdrawn as tracker.'


class TestVolunteerDms:
    def test_shift_lines_with_label(self):
        lines = dm_vol._volunteer_shift_lines('Runner', 'Booth A', 'start', 'end')
        assert lines[0] == '**Position:** Runner — Booth A'
        assert '**Start:** start' in lines
        assert '**End:** end' in lines

    def test_shift_lines_without_label(self):
        lines = dm_vol._volunteer_shift_lines('Runner', None, '', '')
        assert lines == ['**Position:** Runner']

    def test_assignment_dm(self):
        msg = dm_vol.volunteer_assignment_dm('Runner', 'Booth A', 'start', 'end')
        assert msg.startswith("You've been scheduled for a volunteer shift")
        assert '**Position:** Runner — Booth A' in msg
        assert msg.endswith('Please click below to acknowledge your shift.')

    def test_reminder_dm(self):
        msg = dm_vol.volunteer_reminder_dm('Runner', None, 'start', 'end')
        assert msg.startswith('⏰ Reminder')
        assert '**Position:** Runner' in msg

    def test_ack_confirmation(self):
        assert dm_vol.volunteer_ack_confirmation('Runner') == \
            'Thanks! Your **Runner** shift is acknowledged.'


class TestDiscordEmbeds:
    def test_time_field_is_a_discord_timestamp(self):
        from datetime import datetime, timezone
        # Discord <t:unix:F> · <t:unix:R> — renders in the viewer's own tz.
        when = datetime(2026, 7, 19, 22, 41, tzinfo=timezone.utc)
        ts = int(when.timestamp())
        assert de.time_field(when) == f'<t:{ts}:F> · <t:{ts}:R>'
        assert de.time_field(None) == ''

    def test_players_value(self):
        assert de._players_value(['A', 'B']) == 'A vs B'
        assert de._players_value(['A', 'B', 'C']) == 'A, B, C'
        assert de._players_value(None) == ''

    def test_match_embed_fields_footer_color(self):
        from datetime import datetime, timezone
        emb = de.match_embed(
            title='🗓️ Match scheduled', color=de.COLOR_SCHEDULED,
            tournament='Wizzrobe Dev', community_name='Acme Community',
            player_names=['A', 'B'], when=datetime(2026, 7, 19, 22, 41, tzinfo=timezone.utc),
            stage_name='Stage 2', description='Good luck!',
        )
        assert emb.title == '🗓️ Match scheduled'
        assert emb.colour.value == de.COLOR_SCHEDULED
        assert emb.description == 'Good luck!'
        assert emb.footer.text == 'Acme Community'  # community identity
        names = {f.name: f.value for f in emb.fields}
        assert names['Tournament'] == 'Wizzrobe Dev'
        assert names['Players'] == 'A vs B'
        assert names['Stage'] == 'Stage 2'
        assert names['Time'].startswith('<t:') and ':R>' in names['Time']

    def test_match_embed_suppresses_empty_fields(self):
        emb = de.match_embed(title='t', color=0, tournament='Wizzrobe Dev')
        labels = {f.name for f in emb.fields}
        assert labels == {'Tournament'}  # no Players/Time/Stage
        # no community footer when unset (unset footer is falsy in discord.py)
        assert not emb.footer or not emb.footer.text

    def test_state_changed_embed_colors(self):
        assert de.state_changed_embed('T', 'Started').colour.value == de.COLOR_STARTED
        assert de.state_changed_embed('T', 'Finished').colour.value == de.COLOR_FINISHED
        assert de.state_changed_embed('T', 'Confirmed').colour.value == de.COLOR_CONFIRMED
        assert 'started' in de.state_changed_embed('T', 'Started').title.lower()

    def test_volunteer_embed(self):
        from datetime import datetime, timezone
        emb = de.volunteer_embed(
            title='🙌 Volunteer shift', position='Race Proctor — Shift 1',
            community_name='Acme', starts=datetime(2026, 7, 19, 12, tzinfo=timezone.utc),
            ends=datetime(2026, 7, 19, 16, tzinfo=timezone.utc),
        )
        names = {f.name: f.value for f in emb.fields}
        assert names['Position'] == 'Race Proctor — Shift 1'
        assert names['Start'].startswith('<t:')
        assert emb.colour.value == de.COLOR_VOLUNTEER
        assert emb.footer.text == 'Acme'


class TestEphemeralReplies:
    def test_match_ack_with_players(self):
        assert dm.match_ack_confirmation('A vs B') == \
            "You've acknowledged your match (A vs B). Thanks!"

    def test_match_ack_without_players(self):
        assert dm.match_ack_confirmation('') == "You've acknowledged your match. Thanks!"

    def test_crew_ack_with_players(self):
        msg = dm_crew.crew_ack_confirmation('Commentator', 'A vs B')
        assert msg == "You're confirmed for Commentator (A vs B). Thanks!"

    def test_crew_ack_without_players(self):
        msg = dm_crew.crew_ack_confirmation('Commentator', '')
        assert msg == "You're confirmed for Commentator. Thanks!"

    def test_crew_signup_with_players(self):
        msg = dm_crew.crew_signup_confirmation('Tracker', 'A vs B')
        assert msg == (
            "You're signed up as **Tracker** for the match (A vs B). "
            "Staff will confirm it shortly."
        )

    def test_crew_signup_without_players(self):
        msg = dm_crew.crew_signup_confirmation('Tracker', '')
        assert msg == "You're signed up as **Tracker**. Staff will confirm it shortly."

    def test_unwatch_was_watching(self):
        assert dm.unwatch_confirmation('A vs B', True) == \
            'You are no longer watching the match (A vs B).'

    def test_unwatch_was_not_watching(self):
        assert dm.unwatch_confirmation('A vs B', False) == \
            'You were not watching the match (A vs B).'

    def test_unwatch_without_players(self):
        assert dm.unwatch_confirmation('', True) == 'You are no longer watching the match.'


# ---------------------------------------------------------------------------
# easter_eggs.py
# ---------------------------------------------------------------------------

class TestEasterEggs:
    def _all_facts(self):
        return easter_eggs.CAT_FACTS

    def test_the_pool_is_all_cats(self):
        """The other topic lists were retired; random_fact is a cat fact now."""
        assert not [name for name in vars(easter_eggs) if name.endswith('_FACTS')
                    and name not in {'CAT_FACTS'}]

    def test_sections_flatten_into_the_pool(self):
        flattened = [f for facts in easter_eggs.CAT_FACT_SECTIONS.values() for f in facts]
        assert easter_eggs.CAT_FACTS == flattened
        assert all(facts for facts in easter_eggs.CAT_FACT_SECTIONS.values())

    def test_random_fact_returns_known_string(self):
        fact = easter_eggs.random_fact()
        assert isinstance(fact, str) and fact
        assert fact in self._all_facts()

    def test_random_cat_fact_from_cat_pool(self):
        fact = easter_eggs.random_cat_fact()
        assert fact in easter_eggs.CAT_FACTS

    def test_random_cat_fact_honours_exclude(self):
        """A re-roll that returns the same fact reads as a broken button."""
        for fact in easter_eggs.CAT_FACTS:
            for _ in range(20):
                assert easter_eggs.random_cat_fact(exclude=fact) != fact

    def test_random_cat_fact_exclude_unknown_still_returns_a_fact(self):
        assert easter_eggs.random_cat_fact(exclude='not a cat fact') in easter_eggs.CAT_FACTS

    def test_cat_facts_are_unique(self):
        """The /cat-facts page lists the pool verbatim, so a dupe is visible."""
        assert len(set(easter_eggs.CAT_FACTS)) == len(easter_eggs.CAT_FACTS)

    def test_random_fact_deterministic_with_seed(self):
        import random
        state = random.getstate()
        try:
            random.seed(1234)
            first = easter_eggs.random_fact()
            random.seed(1234)
            second = easter_eggs.random_fact()
            assert first == second
        finally:
            random.setstate(state)

    def test_every_fact_survives_header_encoding(self):
        """X-Fun-Fact is set on every response; an unencodable fact breaks them all."""
        for fact in self._all_facts():
            header_safe(fact).encode('latin-1')

    def test_header_safe_neutralizes_unencodable_and_newlines(self):
        assert header_safe('rides 🎢 fast').encode('latin-1')
        assert '\n' not in header_safe('two\nlines')
        assert '\r' not in header_safe('two\r\nlines')
        assert header_safe('an — em-dash') == 'an - em-dash'


# ---------------------------------------------------------------------------
# qrcode_util.py
# ---------------------------------------------------------------------------

class TestQrCodeUtil:
    def test_png_bytes_have_png_signature(self):
        # The util now takes the fully-built (tenant-qualified) URL directly.
        data = qrcode_util.asset_qr_png_bytes('https://sgl.example.org/t/acme/equipment/7')
        assert isinstance(data, bytes) and len(data) > 0
        assert data[:8] == b'\x89PNG\r\n\x1a\n'

    def test_data_uri_prefix_and_decodes_to_png(self):
        url = 'https://sgl.example.org/t/acme/equipment/7'
        uri = qrcode_util.asset_qr_data_uri(url)
        prefix = 'data:image/png;base64,'
        assert uri.startswith(prefix)
        decoded = base64.b64decode(uri[len(prefix):])
        assert decoded[:8] == b'\x89PNG\r\n\x1a\n'
        assert decoded == qrcode_util.asset_qr_png_bytes(url)

    def test_svg_without_logo_is_plain(self):
        svg = qrcode_util.asset_qr_svg('https://sgl.example.org/t/acme/equipment/7')
        assert svg.startswith('<svg') and '<image' not in svg

    def test_svg_with_logo_embeds_it_in_black_at_level_h(self):
        url = 'https://sgl.example.org/t/acme/equipment/7'
        logo = 'data:image/svg+xml;base64,PHN2Zy8+'
        plain = qrcode_util.asset_qr_svg(url)
        with_logo = qrcode_util.asset_qr_svg(url, logo)
        assert f'href="{logo}"' in with_logo and 'feColorMatrix' in with_logo
        # Level H needs more modules for the same URL.
        assert len(with_logo) > len(plain)
        uri = qrcode_util.asset_qr_svg_data_uri(url, logo)
        assert base64.b64decode(uri.split(',', 1)[1]).decode() == with_logo


class TestHardPresetOverrideCopy:
    def test_names_the_preset_staff_chose(self):
        from application.utils.discord_messages import hard_preset_override_dm

        text = hard_preset_override_dm('T', 'Hard Mode', forced=True)
        assert '**Hard Mode**' in text

    def test_a_tournament_with_no_preset_is_not_told_it_plays_nothing(self):
        """resolve_preset returns None when no standard preset is configured,
        and "set your match to ****" is worse than not naming it."""
        from application.utils.discord_messages import hard_preset_override_dm

        text = hard_preset_override_dm('T', '', forced=True)
        assert '****' not in text
        assert 'Staff chose the settings' in text
