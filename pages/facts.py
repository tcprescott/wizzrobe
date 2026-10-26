"""The facts page — where the easter egg finally lands.

Public, because there is nothing here but trivia. Deliberately absent from the
drawer nav: it's reached from the fact dialog (the Konami code, or five taps on
the wordmark) and from the facts scattered through empty states, waiting
spinners and the 404 page. Finding it should still feel like finding something.

The topic follows ``easter_eggs.current_theme``, so the one URL always lists
today's facts; the per-topic URLs people may have shared redirect here.
"""

from fastapi import Request
from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.services import get_user_from_session
from application.utils.easter_eggs import current_theme, random_fact
from middleware.auth import public_page
from theme.base import BaseLayout
from theme.page_header import page_header

__all__ = ['FACTS_PATH', 'LEGACY_PATHS', 'create']

FACTS_PATH = '/facts'
LEGACY_PATHS = ('/cat-facts', '/hadestown-facts', '/rollercoaster-facts')


def create() -> None:
    @public_page(FACTS_PATH, scope='community')
    async def facts() -> None:
        theme = current_theme()
        ui.page_title(theme.title)
        user = await get_user_from_session(app.storage.user)
        await BaseLayout(user=user).render()

        state = {'fact': random_fact()}

        @ui.refreshable
        def featured() -> None:
            ui.label(state['fact']).classes('wiz-fact-featured-fact text-center')

        def shuffle() -> None:
            state['fact'] = random_fact(exclude=state['fact'])
            featured.refresh()

        with ui.column().classes('page-container-narrow w-full'):
            page_header(theme.title)
            ui.label(
                f"You found them. All {len(theme.facts)} of the facts this app "
                "hides in its empty tables, its spinners, its 404 page and the "
                "X-Fun-Fact header on every response."
            ).classes('text-muted')

            with ui.card().classes('wiz-fact-featured w-full items-center'):
                ui.icon(theme.icon).props('size=lg').classes('text-primary')
                featured()
                ui.button(theme.another, icon='refresh', on_click=shuffle).props('flat')

            for section, section_facts in theme.sections.items():
                ui.label(f'{section} · {len(section_facts)}').classes('section-title')
                with ui.column().classes('wiz-fact-list w-full'):
                    for fact in section_facts:
                        with ui.row().classes('wiz-fact-list-row items-start no-wrap'):
                            ui.icon(theme.icon).props('size=xs').classes('wiz-fact-list-icon')
                            ui.label(fact)

    for legacy in LEGACY_PATHS:
        @public_page(legacy, scope='community')
        async def legacy_facts(request: Request) -> RedirectResponse:
            return RedirectResponse(f"{request.scope.get('root_path', '')}{FACTS_PATH}")
