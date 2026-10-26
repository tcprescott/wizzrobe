"""Saving the tournament edit dialog writes only the fields it changed.

The dialog holds the tournament it opened with, while its start.gg section saves
the bookable phase and result reporting the moment they change. A full-row save
on Save put both back to what they were when the dialog opened.
"""

import pytest

from application.services.tournament_service import TournamentService
from models import Tournament
from tests.factories import make_member

pytestmark = pytest.mark.usefixtures("bypass_auth")


async def test_save_keeps_start_gg_settings_changed_while_the_dialog_was_open(db):
    actor = await make_member(1, 'staff')
    opened = await Tournament.create(name='OoTR', startgg_event_id='1705914')
    await Tournament.filter(id=opened.id).update(
        startgg_bookable_phase_id='2392805', startgg_report_results=True,
    )

    await TournamentService().update_tournament(opened, name='Ocarina of Time Randomizer', actor=actor)

    saved = await Tournament.get(id=opened.id)
    assert saved.name == 'Ocarina of Time Randomizer'
    assert saved.startgg_bookable_phase_id == '2392805'
    assert saved.startgg_report_results is True
