"""Which event-handbook articles this community's readers should see.

Three rules, which is why this is a service rather than a bare loader:

* the whole section is behind ``EVENT_INFO``, so a community that has not been
  granted it reaches nothing here — including through a caller that forgot to
  gate itself;
* an individual article may name a **feature** of its own, exactly as a help
  article can. An article that says "sign up for a shift on the Volunteer tab"
  must not be offered where there is no Volunteer tab; and
* an article may name **roles**. The floor procedure a proctor works from is not
  written for the general public — it is the standard the people running the
  room are held to, and it reads as bewildering house rules to anyone else. An
  article with roles is invisible to a reader who holds none of them, signed out
  or signed in, which is why the page can stay public.

Read-only and stateless; no repository of its own beyond the tenant lookup,
because in v1 there is no stored handbook. When the content moves into the
database this interface is the seam it lands on — the surfaces above call
``list_articles``/``get_article`` and know nothing about where the words live.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from application.content import ContentArticle, ContentSnippet
from application.event_context import get_current_event_id
from application.event_info import (
    article_for_tenant,
    articles_for_tenant,
    guide_pdf_for_tenant,
    handbook_key,
    snippet_for_tenant,
)
from application.feature_flags import requires_feature
from application.repositories import TenantRepository
from application.services.auth_service import AuthService
from application.services.event_service import EventService
from application.services.feature_flag_service import FeatureFlagService
from application.tenant_context import require_tenant_id
from application.utils.safe_url import http_url_or_empty
from application.utils.tournament_signup import SignupWindow, signup_window_state
from models import FeatureFlag, Role, Tournament, User

__all__ = ['EventGuide', 'EventInfoService', 'HandbookTournament']


@dataclass(frozen=True)
class HandbookTournament:
    """One row of the handbook's tournament list, read from Admin → Tournaments."""

    id: int
    name: str
    format: str
    rules_url: str
    bracket_url: str
    signups: SignupWindow
    signups_close_at: Optional[datetime]
    prize_total: Optional[Decimal] = None
    prize_synced_at: Optional[datetime] = None


def prize_fields(t: Tournament) -> dict:
    """``prize_total`` and ``prize_synced_at`` for a public list, or ``{}`` with no pool."""
    total = (t.prize_pool or Decimal(0)) + (t.prize_bonus or Decimal(0))
    if total <= 0:
        return {}
    return {
        'prize_total': total,
        'prize_synced_at': t.prize_pool_synced_at if t.sglive_slug else None,
    }


@dataclass(frozen=True)
class EventGuide:
    """The organisers' own guide, offered as a download beside the handbook."""

    path: Path
    filename: str
    size_bytes: int

    @property
    def size_label(self) -> str:
        kb = self.size_bytes / 1024
        return f'{kb / 1024:.1f} MB' if kb >= 1024 else f'{max(1, round(kb))} KB'


class EventInfoService:
    """Feature- and role-aware access to the current community's event handbook."""

    @staticmethod
    async def _tenant_slug() -> str:
        """The handbook folder: the event's own, else the latest event's, else the community's."""
        tenant_id = require_tenant_id()
        tenant = await TenantRepository.get_by_id(tenant_id)
        events = await EventService.events_for_tenant(tenant_id)
        current = get_current_event_id()
        latest = sorted(
            (e for e in events if not e.is_draft and e.id != current),
            key=lambda e: (e.starts_on or date.min, e.id), reverse=True,
        )
        own = [e.slug for e in events if e.id == current]
        return handbook_key(tenant.slug if tenant else '', [*own, *(e.slug for e in latest)])

    @staticmethod
    async def _reader_roles(user: Optional[User]) -> set[Role]:
        """The roles to judge ``user`` by here. Signed out is simply no roles."""
        return await AuthService.get_roles(user) if user else set()

    @staticmethod
    def _readable(article: ContentArticle, live: set, roles: set[Role]) -> bool:
        if article.feature is not None and article.feature not in live:
            return False
        return not article.roles or bool(roles.intersection(article.roles))

    @staticmethod
    @requires_feature(FeatureFlag.EVENT_INFO)
    async def list_articles(user: Optional[User] = None) -> list[ContentArticle]:
        """This community's articles, minus any this reader may not see."""
        slug = await EventInfoService._tenant_slug()
        live = await FeatureFlagService().enabled_flags()
        roles = await EventInfoService._reader_roles(user)
        return [
            article for article in articles_for_tenant(slug)
            if EventInfoService._readable(article, live, roles)
        ]

    @staticmethod
    @requires_feature(FeatureFlag.EVENT_INFO)
    async def get_article(slug: str, user: Optional[User] = None) -> Optional[ContentArticle]:
        """One article by slug, or ``None`` when it is missing or not for this reader.

        Same answer for "no such article", "gated off" and "not your role", for
        the reason ``HelpService`` gives: a reader who followed a stale link
        should not learn which optional features their community has turned off,
        nor which articles exist for people with more access than them.
        """
        article = article_for_tenant(await EventInfoService._tenant_slug(), slug)
        if article is None:
            return None
        live = await FeatureFlagService().enabled_flags()
        roles = await EventInfoService._reader_roles(user)
        if not EventInfoService._readable(article, live, roles):
            return None
        return replace(article, blocks=await EventInfoService._prune(article.blocks, user))

    @staticmethod
    @requires_feature(FeatureFlag.EVENT_INFO)
    async def get_guide() -> Optional[EventGuide]:
        """This community's event guide PDF, or ``None`` when it ships none.

        Public like the articles: it is the document the organisers hand every
        attendee, so there is no role gate.
        """
        path = guide_pdf_for_tenant(await EventInfoService._tenant_slug())
        if path is None:
            return None
        return EventGuide(path=path, filename=path.name, size_bytes=path.stat().st_size)

    @staticmethod
    async def _prune(blocks, user: Optional[User]):
        """Drop links this reader could not follow — see ``HelpService.prune_links``."""
        from application.services.help_service import HelpService

        return await HelpService.prune_links(blocks, user)

    @staticmethod
    @requires_feature(FeatureFlag.EVENT_INFO)
    async def list_tournaments() -> list[HandbookTournament]:
        """The community's active tournaments, minus those staff marked as tests.

        What the ``:::tournaments`` embed draws, so the handbook's list is the
        one staff already keep up to date rather than a second copy in prose.
        A published Wizzrobe bracket wins over an external bracket link, the
        same order the Watch page uses.
        """
        from application.services.bracket_service import BracketService
        from application.services.tournament_service import TournamentService

        published: set[int] = set()
        if await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS):
            published = {b.tournament_id for b in await BracketService().list_all_visible_brackets(None)}
        startgg_live = await FeatureFlagService().is_enabled(FeatureFlag.STARTGG)
        payouts_live = await FeatureFlagService().is_enabled(FeatureFlag.PAYOUTS)
        rows = []
        for t in await TournamentService().get_all_tournaments(active_only=True):
            if t.is_test:
                continue
            if t.id in published:
                bracket = f'/live/tournament/{t.id}/brackets'
            elif startgg_live and t.startgg_event_slug:
                bracket = f'/tournament/{t.id}/startgg'
            else:
                bracket = http_url_or_empty(t.bracket_url) or http_url_or_empty(t.challonge_tournament_url)
            rows.append(HandbookTournament(
                id=t.id,
                name=t.name,
                format=t.tournament_format or '',
                rules_url=http_url_or_empty(t.rules_url),
                bracket_url=bracket,
                signups=signup_window_state(t.signups_open_at, t.signups_close_at),
                signups_close_at=t.signups_close_at,
                **(prize_fields(t) if payouts_live else {}),
            ))
        return sorted(rows, key=lambda r: r.name.lower())

    @staticmethod
    @requires_feature(FeatureFlag.EVENT_INFO)
    async def get_snippet(name: str, user: Optional[User] = None) -> Optional[ContentSnippet]:
        """One popup snippet, or ``None`` when its article is not for this reader.

        The gate is the owning article's, not the snippet's: a snippet is a
        region of an article, so it can never be more readable than the article
        it was cut from.
        """
        slug = await EventInfoService._tenant_slug()
        snippet = snippet_for_tenant(slug, name)
        if snippet is None:
            return None
        article = article_for_tenant(slug, snippet.article_slug)
        if article is None:
            return None
        live = await FeatureFlagService().enabled_flags()
        roles = await EventInfoService._reader_roles(user)
        if not EventInfoService._readable(article, live, roles):
            return None
        return replace(snippet, blocks=await EventInfoService._prune(snippet.blocks, user))
