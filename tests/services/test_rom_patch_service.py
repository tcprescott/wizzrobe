"""RomPatchService: hosted ROM patches for the generic patcher, and reading patch files."""

import zlib

import pytest

from application.errors import NotFoundError
from application.services.rom_patch_service import RomPatchService
from application.services.user_service import UserService
from application.utils.rom_patch_format import normalize_crc, read_patch
from application.utils.rom_patch_store import RomPatchStore
from models import AuditLog, RomPatch, Tournament
from tests.factories import make_user


def _footer(body: bytes, source: bytes, target: bytes) -> bytes:
    body += zlib.crc32(source).to_bytes(4, 'little') + zlib.crc32(target).to_bytes(4, 'little')
    return body + zlib.crc32(body).to_bytes(4, 'little')


SOURCE = bytes(range(256)) * 4


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            return bytes(out + bytes([0x80 | x]))
        out.append(x)
        n -= 1


def make_bps(source: bytes = SOURCE, target: bytes = SOURCE) -> bytes:
    body = b'BPS1' + _varint(len(source)) + _varint(len(target)) + _varint(0)
    body += _varint((len(target) - 1) << 2 | 1) + target
    return _footer(body, source, target)


IPS = b'PATCH' + bytes([0, 0, 2, 0, 2, 9, 9]) + b'EOF'


@pytest.fixture
async def actor(db):
    return await UserService().get_system_user()


@pytest.fixture
def service(tmp_path):
    return RomPatchService(store=RomPatchStore(str(tmp_path)))


class TestReadPatch:
    def test_bps_names_its_rom(self):
        info = read_patch(make_bps())
        assert info.format == 'bps'
        assert info.source_crc == f'{zlib.crc32(SOURCE):08X}'

    def test_ips_names_none(self):
        assert read_patch(IPS).format == 'ips'
        assert read_patch(IPS).source_crc is None

    def test_damaged_bps_is_refused(self):
        data = bytearray(make_bps())
        data[10] ^= 1
        with pytest.raises(ValueError, match='damaged'):
            read_patch(bytes(data))

    def test_cut_short_ips_is_refused(self):
        with pytest.raises(ValueError, match='cut short'):
            read_patch(b'PATCH' + bytes([0, 0, 2, 0, 9, 1]))

    def test_other_files_are_refused(self):
        with pytest.raises(ValueError, match="isn't a BPS, IPS or UPS"):
            read_patch(b'\x89PNG\r\n')

    @pytest.mark.parametrize('raw,expected', [('d63ed5f8', 'D63ED5F8'), ('0xD63ED5F8', 'D63ED5F8'), ('  ', None)])
    def test_crc_is_normalized(self, raw, expected):
        assert normalize_crc(raw) == expected

    def test_bad_crc_is_refused(self):
        with pytest.raises(ValueError, match='8 hex digits'):
            normalize_crc('xyz')


class TestManagePatches:
    async def test_create_stores_the_file_and_audits(self, service, actor):
        tournament = await Tournament.create(name='Map Rando')
        patch = await service.create_patch(
            actor, title=' SGL practice hack ', game='Super Metroid (Japan, USA)',
            filename='C:\\hacks\\sm-practice.bps', data=make_bps(), tournament_id=tournament.id,
            source_crc='00000000',
        )
        assert patch.title == 'SGL practice hack'
        assert patch.filename == 'sm-practice.bps'
        assert patch.format == 'bps'
        # The file's own CRC wins over a typed one.
        assert patch.source_crc == f'{zlib.crc32(SOURCE):08X}'
        assert await service.store.get(patch.token, 'bps') == make_bps()
        assert await AuditLog.filter(action='rom_patch.created').count() == 1

    async def test_ips_takes_a_typed_crc(self, service, actor):
        patch = await service.create_patch(
            actor, title='QoL', game='Zelda', filename='qol.ips', data=IPS, source_crc='d445f698',
        )
        assert patch.source_crc == 'D445F698'

    async def test_title_and_game_are_required(self, service, actor):
        with pytest.raises(ValueError, match='Title is required'):
            await service.create_patch(actor, title=' ', game='x', filename='a.ips', data=IPS)
        with pytest.raises(ValueError, match='Game is required'):
            await service.create_patch(actor, title='x', game='', filename='a.ips', data=IPS)

    async def test_a_player_cannot_manage_patches(self, service, db):
        player = await make_user(4242)
        with pytest.raises(PermissionError):
            await service.create_patch(player, title='x', game='y', filename='a.ips', data=IPS)
        with pytest.raises(PermissionError):
            await service.list_patches(player)

    async def test_unknown_tournament_is_refused(self, service, actor):
        with pytest.raises(NotFoundError):
            await service.create_patch(actor, title='x', game='y', filename='a.ips', data=IPS, tournament_id=999)

    async def test_update_keeps_the_crc_of_a_bps(self, service, actor):
        patch = await service.create_patch(actor, title='x', game='y', filename='a.bps', data=make_bps())
        updated = await service.update_patch(
            actor, patch.id, title='New', game='Game', description=' notes ', source_crc='11111111',
        )
        assert (updated.title, updated.game, updated.description) == ('New', 'Game', 'notes')
        assert updated.source_crc == f'{zlib.crc32(SOURCE):08X}'

    async def test_replace_keeps_the_link_and_drops_the_old_file(self, service, actor):
        patch = await service.create_patch(actor, title='x', game='y', filename='a.ips', data=IPS)
        replaced = await service.replace_file(actor, patch.id, filename='b.bps', data=make_bps())
        assert replaced.token == patch.token
        assert replaced.format == 'bps'
        assert await service.store.get(patch.token, 'ips') is None
        assert await service.store.get(patch.token, 'bps') == make_bps()

    async def test_delete_removes_row_and_file(self, service, actor):
        patch = await service.create_patch(actor, title='x', game='y', filename='a.ips', data=IPS)
        await service.delete_patch(actor, patch.id)
        assert await RomPatch.filter(id=patch.id).count() == 0
        assert await service.store.get(patch.token, 'ips') is None


class TestReadingPatches:
    async def test_file_and_download_count(self, service, actor):
        patch = await service.create_patch(actor, title='x', game='y', filename='a.ips', data=IPS)
        found = await service.patch_file(patch.token)
        assert found[1] == IPS
        await RomPatchService.record_download(patch.token)
        await RomPatchService.record_download(patch.token)
        assert (await RomPatch.get(id=patch.id)).download_count == 2

    async def test_unknown_token(self, service, db):
        with pytest.raises(NotFoundError):
            await service.patch_file('nope-nope-nope')
        with pytest.raises(NotFoundError):
            await RomPatchService.get_by_token('nope-nope-nope')
        with pytest.raises(NotFoundError):
            await RomPatchService.record_download('nope-nope-nope')

    async def test_store_refuses_odd_names(self, tmp_path):
        store = RomPatchStore(str(tmp_path))
        with pytest.raises(ValueError):
            await store.get('../../etc/passwd', 'bps')
        with pytest.raises(ValueError):
            await store.get('abcdefgh', 'exe')
