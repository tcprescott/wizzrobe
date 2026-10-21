"""Tests for the volunteer export dialog's archive assembly.

Only the pure ``VolunteerExportBundle.to_zip_bytes`` half is covered here — the dialog body
itself is NiceGUI rendering, exercised by ``/ui-validation``.
"""

import io
import itertools
import zipfile

from application.services.volunteer.volunteer_export_service import VolunteerExportService
from models import Role, UserRole, VolunteerPosition, VolunteerShift
from tests.factories import make_user, utc

_next_discord_id = itertools.count(940000)


async def _coordinator():
    user = await make_user(next(_next_discord_id), username='coord')
    await UserRole.create(user=user, role=Role.VOLUNTEER_COORDINATOR)
    return user


class TestBundleToZipBytes:
    async def test_archive_holds_a_readme_and_one_csv_per_sheet(self, db):
        actor = await _coordinator()
        position = await VolunteerPosition.create(name='Check-in')
        await VolunteerShift.create(
            position=position, starts_at=utc(2026, 10, 4, 12), ends_at=utc(2026, 10, 4, 16),
        )
        bundle = await VolunteerExportService().build(actor, utc(2026, 10, 4), utc(2026, 10, 5))

        with zipfile.ZipFile(io.BytesIO(bundle.to_zip_bytes())) as archive:
            names = archive.namelist()
            assert names[0] == 'README.txt'
            assert names[1:] == [f'{s.name}.csv' for s in bundle.sheets]
            assert 'Position' in archive.read('shifts.csv').decode('utf-8-sig')
