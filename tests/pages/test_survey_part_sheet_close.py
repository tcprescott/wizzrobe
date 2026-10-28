"""A survey part keeps its answers however its sheet closes.

Only Done used to save, so Back to parts, the close button, Escape or a tap
outside threw away whatever had been typed, under a footer promising "answers
save as you go" (Thomas picked "save on any close", 2026-10-28).
"""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from nicegui import core, events
from nicegui.client import Client
from nicegui.page import page

from application.services import RespondentView
from application.services.survey import plan
from models import SurveyQuestion, SurveyQuestionKind
from theme.survey_form import PartSheet


def _question(qid: int, kind: SurveyQuestionKind, **extra: Any) -> SurveyQuestion:
    return SurveyQuestion(id=qid, prompt=f'Q{qid}', kind=kind, required=False, conditions=[],
                          options=None, other_option=False, none_option=None, min_picks=None,
                          max_picks=None, long_answer=False, **extra)


def _part() -> plan.Part:
    return plan.Part(section_id=1, title='The event', reason='Everyone', scope='', scope_label='',
                     icon='event', questions=[_question(1, SurveyQuestionKind.COMMENT),
                                              _question(2, SurveyQuestionKind.RATING)])


class _Form:
    def __init__(self, part: plan.Part, *, fail: bool = False) -> None:
        self.view = RespondentView(survey=SimpleNamespace(), recipient=None, parts=[part])
        self.editable = True
        self.saves: List[Dict[int, Any]] = []
        self.fail = fail
        self.parts_list = SimpleNamespace(refresh=lambda: None)

    async def save_part(self, key: str, answers: Dict[int, Any]) -> RespondentView:
        if self.fail:
            raise ValueError('Keep it under 1,000 characters.')
        self.saves.append(answers)
        for qid, value in answers.items():
            if value is not None:
                self.view.answers[(qid, '')] = value
        return self.view


@pytest.fixture
def slot():
    return Client(page('/'))


@pytest.fixture
async def run_now(monkeypatch):
    scheduled: list = []
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    # Like the app does, an async handler runs inside the element's slot.
    async def in_slot(coro, context):
        with context:
            await coro

    monkeypatch.setattr(events.background_tasks, 'create_or_defer',
                        lambda coro, *, context, **_kw: scheduled.append(in_slot(coro, context)))
    return scheduled


async def _close(sheet: PartSheet, scheduled: list) -> None:
    sheet.dialog.value = False
    while scheduled:
        await scheduled.pop(0)


async def test_closing_without_done_saves_what_was_typed(slot, run_now, monkeypatch):
    monkeypatch.setattr('theme.survey_form.notify_error', lambda e: None)
    part = _part()
    form = _Form(part)
    with slot:
        sheet = PartSheet(form)
        sheet.open(part)
        sheet._set(part.questions[0], 'The queue was long', quiet=True)
        await _close(sheet, run_now)
    assert form.saves == [{1: 'The queue was long', 2: None}]


async def test_closing_an_untouched_part_saves_nothing(slot, run_now):
    part = _part()
    form = _Form(part)
    with slot:
        sheet = PartSheet(form)
        sheet.open(part)
        await _close(sheet, run_now)
    assert form.saves == []


async def test_answers_a_close_could_not_save_come_back_on_reopen(slot, run_now, monkeypatch):
    errors: list = []
    monkeypatch.setattr('theme.survey_form.notify_error', errors.append)
    part = _part()
    form = _Form(part, fail=True)
    with slot:
        sheet = PartSheet(form)
        sheet.open(part)
        sheet._set(part.questions[0], 'x' * 2000, quiet=True)
        await _close(sheet, run_now)
        sheet.open(part)
    assert errors and sheet.draft[1] == 'x' * 2000
