"""Tests for the DK64R backend of SeedGenerationService (unit — no network).

Split from ``test_seedgen_service.py`` when that module approached the 800-line
guideline. DK64R is the one randomizer that rolls through a **task queue**
rather than a single request, so it carries its own HTTP stub, its own
credential fixture and roughly half the file: submit, poll, the branch routing,
the remote preset catalogue, the simulated queue under ``MOCK_SEEDGEN``, and the
spoiler log. Everything else — the shared surface and the single-request
randomizers — stays in the sibling.
"""

import pytest

from application.services.seedgen_service import SeedGenerationService


@pytest.fixture
def service():
    return SeedGenerationService()



class _FakeResp:
    """A minimal aiohttp response context manager returning scripted data."""

    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def text(self):
        import json as _json
        return _json.dumps(self._payload) if not isinstance(self._payload, Exception) else 'error'


class _DK64RStub:
    """A fake ``aiohttp.ClientSession`` scripting DK64R's HTTP round-trips.

    Construct with an ordered list of ``(status, payload)`` responses consumed
    per request; ``default`` (if given) is reused once the list is exhausted.
    Records ``(method, url, kwargs)`` for each call and the session headers.
    """

    def __init__(self, responses, *, default=None):
        self._responses = list(responses)
        self._default = default
        self.calls = []
        self.headers = None

    def __call__(self, *args, **kwargs):  # aiohttp.ClientSession(headers=...)
        self.headers = kwargs.get('headers')
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        if self._responses:
            status, payload = self._responses.pop(0)
        elif self._default is not None:
            status, payload = self._default
        else:
            raise AssertionError('DK64RStub ran out of scripted responses')
        return _FakeResp(status, payload)

    def post(self, url, **kwargs):
        return self._next('POST', url, kwargs)

    def get(self, url, **kwargs):
        return self._next('GET', url, kwargs)


@pytest.fixture
async def dk64r_key(db):
    """The tenant's own DK64R credential — the successor to ``DK64R_API_KEY``.

    ``db`` binds the default test tenant, so the row is auto-stamped with it.
    """
    from models import RandomizerCredential

    await RandomizerCredential.create(randomizer='dk64r', key='api_key', value='k')


class TestGenerateDk64r:
    async def test_a_temporary_upstream_error_is_retried(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (503, {'error': 'busy'}),
            (200, [{'name': 'Keeper', 'settings_string': 'y'}]),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        presets = await service.list_remote_presets('dk64r')
        assert [p.name for p in presets] == ['Keeper']
        assert len(stub.calls) == 2

    async def test_a_rejected_request_is_not_retried(self, service, monkeypatch, dk64r_key):
        from application.utils.seed_provider import SeedProviderInvalidRequest

        stub = _DK64RStub([(400, {'error': 'bad branch'})], default=(200, []))
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)

        with pytest.raises(SeedProviderInvalidRequest, match=r'list the presets.*bad branch'):
            await service.list_remote_presets('dk64r')
        assert len(stub.calls) == 1

    async def test_missing_credential_raises(self, service, db):
        preset = _preset({'settings_string': 's'})
        with pytest.raises(ValueError, match='DK64 Randomizer API key is not configured'):
            await service._generate_dk64r(preset)

    async def test_settings_string_happy_path(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (200, {'level_randomization': 'level_order'}),        # convert_settings
            (200, {'task_id': 'task-123', 'status': 'queued'}),   # submit-task
            (200, {'status': 'queued', 'position': 2}),           # poll
            (200, {'status': 'started'}),                          # poll
            (200, {'status': 'finished', 'result': {'seed_number': 90210, 'hash': 'DK-DK-DK'}}),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        preset = _preset({'settings_string': 'abc123'})
        url = (await service._generate_dk64r(preset)).url

        assert url == 'https://dk64randomizer.com/randomizer.html?seed_id=90210'
        # The API key travels as X-API-Key on the session.
        assert stub.headers == {'X-API-Key': 'k'}
        # Convert was called with the settings string, submit with the expanded dict.
        assert stub.calls[0][1].endswith('/convert_settings')
        assert stub.calls[0][2]['json'] == {'settings': 'abc123'}
        assert stub.calls[0][2]['params'] == {'branch': 'stable'}
        assert stub.calls[1][1].endswith('/submit-task')
        import json as _json
        assert _json.loads(stub.calls[1][2]['json']['settings_data']) == {
            'level_randomization': 'level_order', 'generate_spoilerlog': False,
        }
        assert '/task-status/task-123' in stub.calls[2][1]

    async def test_full_json_preset_skips_convert(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (200, {'task_id': 't1', 'status': 'queued'}),
            (200, {'status': 'finished', 'result': {'seed_number': 5}}),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        preset = _preset({'level_randomization': 'level_order', 'krool_phases': 5})
        url = (await service._generate_dk64r(preset)).url

        assert url.endswith('seed_id=5')
        # First (and only POST) call is submit — no convert step.
        assert stub.calls[0][1].endswith('/submit-task')
        import json as _json
        assert _json.loads(stub.calls[0][2]['json']['settings_data']) == {
            'level_randomization': 'level_order', 'krool_phases': 5,
            'generate_spoilerlog': False,
        }

    async def test_dev_branch_routes_to_dev_host_and_is_stripped(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (200, {'task_id': 't1', 'status': 'queued'}),
            (200, {'status': 'finished', 'result': {'seed_number': 77}}),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        preset = _preset({'_branch': 'dev', 'krool_phases': 3})
        url = (await service._generate_dk64r(preset)).url

        assert url == 'https://dev.dk64randomizer.com/randomizer.html?seed_id=77'
        # Every call carries branch=dev...
        assert stub.calls[0][2]['params'] == {'branch': 'dev'}
        # ...and _branch never leaks into the submitted settings.
        import json as _json
        assert '_branch' not in _json.loads(stub.calls[0][2]['json']['settings_data'])

    async def test_unknown_branch_raises(self, service, monkeypatch, dk64r_key):
        preset = _preset({'_branch': 'nightly', 'krool_phases': 3})
        with pytest.raises(ValueError, match='Unknown DK64R branch'):
            await service._generate_dk64r(preset)

    async def test_no_preset_falls_back_to_builtin_file(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (200, {'expanded': True}),                             # convert (builtin is a settings string)
            (200, {'task_id': 't1', 'status': 'queued'}),
            (200, {'status': 'finished', 'result': {'seed_number': 1}}),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        url = (await service._generate_dk64r(None)).url
        assert url.endswith('seed_id=1')
        # The committed builtin is a settings-string preset → convert runs first.
        assert stub.calls[0][1].endswith('/convert_settings')

    async def test_submit_rejection_raises(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (400, {'error': 'invalid settings_data'}),            # submit-task 400
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)

        preset = _preset({'krool_phases': 3})
        with pytest.raises(ValueError, match='invalid settings_data'):
            await service._generate_dk64r(preset)

    async def test_task_crash_http_500_raises(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (200, {'task_id': 't1', 'status': 'queued'}),
            (500, {'error': 'generator exploded'}),               # task-status 500
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        preset = _preset({'krool_phases': 3})
        with pytest.raises(ValueError, match='generate the seed'):
            await service._generate_dk64r(preset)

    async def test_failed_status_raises(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([
            (200, {'task_id': 't1', 'status': 'queued'}),
            (200, {'status': 'failed'}),                          # defensive failed inside 200
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        preset = _preset({'krool_phases': 3})
        with pytest.raises(ValueError, match='failed to generate'):
            await service._generate_dk64r(preset)

    async def test_poll_timeout_raises(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub(
            [(200, {'task_id': 't1', 'status': 'queued'})],
            default=(200, {'status': 'queued', 'position': 9}),   # never finishes
        )
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)
        # Advance the monotonic clock past the deadline. A never-exhausting
        # counter (vs. a fixed iterator) is safe even if teardown reads the clock.
        import itertools
        clock = itertools.count(0.0, 10_000.0)
        monkeypatch.setattr(
            'application.services._seedgen_dk64r.time.monotonic',
            lambda: next(clock),
        )

        preset = _preset({'krool_phases': 3})
        with pytest.raises(ValueError, match='timed out'):
            await service._generate_dk64r(preset)


async def _noop_sleep(_seconds):
    return None


def _preset(settings):
    from types import SimpleNamespace
    return SimpleNamespace(randomizer='dk64r', settings=settings)


class TestRemotePresetCatalogue:
    """``list_remote_presets`` — the randomizer's own published presets."""

    async def test_maps_entries_into_storable_presets(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([(200, [
            {'branch': 'stable', 'name': 'Season 5', 'description': 'Race settings',
             'settings_string': 'abc123'},
        ])])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)

        presets = await service.list_remote_presets('dk64r', branch='stable')

        assert stub.calls[0][1].endswith('/get_presets')
        assert stub.calls[0][2]['params'] == {'branch': 'stable'}
        assert [(p.name, p.description) for p in presets] == [('Season 5', 'Race settings')]
        # Stored as the roll path reads it: the branch it came from, and the
        # portable string expanded through /convert_settings at roll time.
        assert presets[0].settings == {'_branch': 'stable', 'settings_string': 'abc123'}

    async def test_skips_unusable_entries(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([(200, [
            {'name': 'No string'},
            {'settings_string': 'x'},
            'not an object',
            {'name': 'Keeper', 'settings_string': 'y'},
        ])])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)

        presets = await service.list_remote_presets('dk64r')
        # One malformed row must not cost the community the whole catalogue.
        assert [p.name for p in presets] == ['Keeper']

    async def test_non_list_payload_raises(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([(200, {'presets': []})])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)

        with pytest.raises(ValueError, match='unexpected preset catalogue'):
            await service.list_remote_presets('dk64r')

    async def test_upstream_error_surfaces(self, service, monkeypatch, dk64r_key):
        stub = _DK64RStub([(500, {'error': 'boom'})] * 3)
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        with pytest.raises(ValueError, match='list the presets'):
            await service.list_remote_presets('dk64r')

    async def test_missing_credential_raises(self, service, db):
        with pytest.raises(ValueError, match='DK64 Randomizer API key is not configured'):
            await service.list_remote_presets('dk64r')

    async def test_randomizer_without_a_catalogue_raises(self, service):
        with pytest.raises(ValueError, match='does not publish a preset catalogue'):
            await service.list_remote_presets('ootr')

    def test_branch_helpers(self):
        assert SeedGenerationService.offers_remote_presets('dk64r')
        assert not SeedGenerationService.offers_remote_presets('alttpr')
        assert SeedGenerationService.remote_preset_branches('dk64r') == ['stable', 'dev']
        assert SeedGenerationService.remote_preset_branches('alttpr') == []


class TestMockDK64:
    """The simulated task queue (``MOCK_SEEDGEN`` + ``application.utils.mocks.mock_dk64``).

    Note the absence of the ``db`` and ``dk64r_key`` fixtures throughout: the
    simulated queue authenticates nobody, so a dev sandbox with no credential
    row rolls DK64 exactly like every other mocked backend.
    """

    @pytest.fixture(autouse=True)
    def _mock_env(self, monkeypatch):
        monkeypatch.setenv('ENVIRONMENT', 'development')
        monkeypatch.setenv('MOCK_SEEDGEN', 'true')
        monkeypatch.setenv('MOCK_DK64_SECONDS', '0')

    async def test_returns_a_real_shaped_permalink(self, service):
        url = (await service._generate_dk64r(None)).url
        assert url.startswith('https://dk64randomizer.com/randomizer.html?seed_id=')

    async def test_does_not_return_the_generic_mock_url(self, service):
        # The whole point of opting out of the short-circuit: DK64 must travel
        # its own convert -> submit -> poll path even when mocked.
        url = await service.generate_seed('dk64r')
        assert 'mock.seedgen.local' not in url

    async def test_dev_branch_routes_to_dev_host(self, service):
        preset = _preset({'_branch': 'dev', 'settings_string': 'abc'})
        url = (await service._generate_dk64r(preset)).url
        assert url.startswith('https://dev.dk64randomizer.com/randomizer.html?seed_id=')

    async def test_seed_numbers_differ_between_rolls(self, service):
        urls = {(await service._generate_dk64r(None)).url for _ in range(5)}
        assert len(urls) > 1

    @pytest.mark.parametrize('outcome,message', [
        ('failed', 'failed to generate'),
        ('http_error', 'generate the seed'),
    ])
    async def test_failure_outcomes_raise(self, service, monkeypatch, outcome, message):
        monkeypatch.setenv('MOCK_DK64_OUTCOME', outcome)
        with pytest.raises(ValueError, match=message):
            await service._generate_dk64r(None)

    @pytest.mark.parametrize('stage,message', [
        ('convert', 'convert settings'),
        ('submit', 'submit the seed'),
    ])
    async def test_broken_stages_raise(self, service, monkeypatch, stage, message):
        monkeypatch.setenv('MOCK_DK64_BROKEN_STAGE', stage)
        with pytest.raises(ValueError, match=message):
            await service._generate_dk64r(None)

    async def test_walks_queued_then_started_then_finished(self, monkeypatch):
        # The progression the presentation layer renders a waiting state from,
        # driven off a controlled clock rather than real seconds.
        from application.utils.mocks import mock_dk64

        monkeypatch.setenv('MOCK_DK64_SECONDS', '100')
        now = [0.0]
        monkeypatch.setattr(mock_dk64.time, 'monotonic', lambda: now[0])

        session = mock_dk64.MockDK64Session()
        async with session.post(
            'https://api.dk64rando.com/api/submit-task',
            json={'settings_data': '{}'},
        ) as resp:
            task_id = (await resp.json())['task_id']

        async def status_at(elapsed):
            now[0] = elapsed
            async with session.get(f'https://api.dk64rando.com/api/task-status/{task_id}') as r:
                return (await r.json())['status']

        assert await status_at(1.0) == 'queued'
        assert await status_at(50.0) == 'started'
        assert await status_at(101.0) == 'finished'

    async def test_stuck_outcome_never_finishes(self, monkeypatch):
        from application.utils.mocks import mock_dk64

        monkeypatch.setenv('MOCK_DK64_SECONDS', '10')
        monkeypatch.setenv('MOCK_DK64_OUTCOME', 'stuck')
        now = [0.0]
        monkeypatch.setattr(mock_dk64.time, 'monotonic', lambda: now[0])

        session = mock_dk64.MockDK64Session()
        async with session.post(
            'https://api.dk64rando.com/api/submit-task', json={'settings_data': '{}'},
        ) as resp:
            task_id = (await resp.json())['task_id']

        now[0] = 100_000.0
        async with session.get(f'https://api.dk64rando.com/api/task-status/{task_id}') as r:
            assert (await r.json())['status'] == 'started'

    async def test_unknown_task_id_is_404(self):
        from application.utils.mocks.mock_dk64 import MockDK64Session

        session = MockDK64Session()
        async with session.get('https://api.dk64rando.com/api/task-status/nope') as resp:
            assert resp.status == 404

    async def test_presets_match_the_upstream_shape(self):
        from application.utils.mocks.mock_dk64 import mock_dk64_presets

        presets = mock_dk64_presets('stable')
        assert presets
        assert all(
            set(p) == {'branch', 'name', 'description', 'settings_string'}
            for p in presets
        )
        assert all(p['branch'] == 'stable' for p in presets)

    def test_refused_in_production(self, monkeypatch):
        from application.utils.mocks.mock_dk64 import is_mock_dk64

        monkeypatch.setenv('ENVIRONMENT', 'production')
        with pytest.raises(RuntimeError, match='must not be enabled in production'):
            is_mock_dk64()


class TestDk64rSpoilerLog:
    """Spoiler logs are forced off, whatever the preset says.

    Upstream serves the log to anyone holding the seed number
    (``GET /get_spoiler_log?hash=<seed_number>``), and the seed number is the
    permalink the players are DMed — so a preset with this on would hand every
    racer the item locations.
    """

    async def test_full_json_preset_asking_for_a_spoiler_is_overridden(
        self, service, monkeypatch, dk64r_key,
    ):
        stub = _DK64RStub([
            (200, {'task_id': 't1', 'status': 'queued'}),
            (200, {'status': 'finished', 'result': {'seed_number': 1}}),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        preset = _preset({'krool_phases': 3, 'generate_spoilerlog': True})
        rolled = await service._generate_dk64r(preset)

        import json as _json
        sent = _json.loads(stub.calls[0][2]['json']['settings_data'])
        assert sent['generate_spoilerlog'] is False
        # And the recorded snapshot reflects what actually went upstream.
        assert rolled.settings['generate_spoilerlog'] is False

    async def test_converted_settings_string_is_overridden(
        self, service, monkeypatch, dk64r_key,
    ):
        # The live API converts several of the site's own presets — including
        # "Season 5 Race Settings" — with the spoiler log switched on, so the
        # override has to happen after convert_settings, not before.
        stub = _DK64RStub([
            (200, {'krool_phases': 3, 'generate_spoilerlog': True}),   # convert
            (200, {'task_id': 't1', 'status': 'queued'}),
            (200, {'status': 'finished', 'result': {'seed_number': 2}}),
        ])
        import aiohttp
        monkeypatch.setattr(aiohttp, 'ClientSession', stub)
        monkeypatch.setattr('asyncio.sleep', _noop_sleep)

        await service._generate_dk64r(_preset({'settings_string': 'abc'}))

        import json as _json
        sent = _json.loads(stub.calls[1][2]['json']['settings_data'])
        assert sent['generate_spoilerlog'] is False
