"""The pages anyone can read: help, the event handbook, What's new, About and facts.

Public like the website's pages, which they mirror: help matters most to someone
who can't get in, and the handbook answers questions people have before they
have an account. The community comes from the header (``require_reader``); a
signed-in member also sees the articles their roles open. What's new, About and
facts describe the app rather than a community, so they need no header at all.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from api.dependencies import ServiceErrorRoute, reader_feature, require_reader
from api.schemas.reading import (
    AboutPage,
    Article,
    ArticleSummary,
    Changelog,
    ChangelogSectionOut,
    ChangelogWeek,
    EventInfoIndex,
    Facts,
    GuideInfo,
    HandbookTournamentOut,
    Heading,
    HelpIndex,
    NoticeGroupOut,
    NoticeOut,
    Notices,
    NoticeText,
    PrivacyPolicy,
    Snippet,
    blocks_out,
)
from application.about import ABOUT_INTRO, BUILT_WITH_CLAUDE, PRIVACY_UPDATED, all_notices, find_notice, privacy_blocks
from application.changelog import all_entries
from application.content import ContentArticle, parse_blocks
from application.errors import NotFoundError
from application.services import EventInfoService, HelpService, TimezoneService
from application.tenant_context import require_tenant_id
from application.utils.build_info import build_info
from application.utils.easter_eggs import current_theme
from models import FeatureFlag, User

__all__ = ['app_router', 'event_info_router', 'help_router']

help_router = APIRouter(prefix='/help', tags=['Help'], route_class=ServiceErrorRoute)
event_info_router = APIRouter(
    prefix='/event-info', tags=['Event information'], route_class=ServiceErrorRoute,
    dependencies=[Depends(reader_feature(FeatureFlag.EVENT_INFO))],
)
app_router = APIRouter(tags=['About the app'], route_class=ServiceErrorRoute)

_READER = (
    "**Who can call this:** Anyone, signed in or not. Name the community in the "
    "`X-Wizzrobe-Community` header (a personal token's own community is used instead). "
    "Signed in as a member, you also get the articles your roles open."
)


def _summary(article: ContentArticle, *, staff: bool = False) -> ArticleSummary:
    return ArticleSummary(slug=article.slug, title=article.title, summary=article.summary,
                          icon=article.icon, for_staff=staff)


def _article(article: ContentArticle, *, staff: bool = False) -> Article:
    return Article(
        **_summary(article, staff=staff).model_dump(),
        headings=[Heading(anchor=a, text=t) for a, t in article.headings],
        blocks=blocks_out(article.blocks),
    )


@help_router.get(
    '',
    response_model=HelpIndex,
    summary='List help articles',
    description=(
        "The help articles this community offers you, the \"For staff\" group last. An article "
        "about a feature the community has turned off isn't listed. `q` keeps only the articles "
        "containing every word of it.\n\n" + _READER
    ),
)
async def list_help(
    q: Optional[str] = Query(None, max_length=200, description='Words to search for.'),
    reader: Optional[User] = Depends(require_reader),
) -> HelpIndex:
    articles = await HelpService.search(q or '', reader)
    return HelpIndex(articles=[_summary(a, staff=HelpService.for_staff(a)) for a in articles])


@help_router.get(
    '/snippets/{name}',
    response_model=Snippet,
    summary='Get a help snippet',
    description=(
        "The few lines a help icon shows, cut from an article, with a link to the rest. "
        "`404` when the article isn't offered to you here.\n\n" + _READER
    ),
)
async def help_snippet(name: str, reader: Optional[User] = Depends(require_reader)) -> Snippet:
    snippet = await HelpService.get_snippet(name, reader)
    if snippet is None:
        raise NotFoundError('No such help snippet here.')
    return Snippet(name=snippet.name, title=snippet.article_title, heading=snippet.heading,
                   href=snippet.href, blocks=blocks_out(snippet.blocks))


@help_router.get(
    '/{slug}',
    response_model=Article,
    summary='Get a help article',
    description=(
        "One help article as blocks. Links to articles you couldn't open here are already "
        "taken out. `404` for an article that doesn't exist, is about a feature that's off, or "
        "isn't for you; the three look the same on purpose.\n\n" + _READER
    ),
)
async def help_article(slug: str, reader: Optional[User] = Depends(require_reader)) -> Article:
    article = await HelpService.get_article(slug, reader)
    if article is None:
        raise NotFoundError('No such help article here.')
    return _article(article, staff=HelpService.for_staff(article))


@event_info_router.get(
    '',
    response_model=EventInfoIndex,
    summary='List event information',
    description=(
        "The event handbook's articles for this community's event, and its PDF guide when it has "
        "one. Pages written for crew or staff are listed only for people holding those roles.\n\n"
        + _READER
    ),
)
async def list_event_info(reader: Optional[User] = Depends(require_reader)) -> EventInfoIndex:
    guide = await EventInfoService.get_guide()
    return EventInfoIndex(
        articles=[_summary(a) for a in await EventInfoService.list_articles(reader)],
        guide=GuideInfo(filename=guide.filename, size_bytes=guide.size_bytes, size_label=guide.size_label)
        if guide else None,
    )


@event_info_router.get(
    '/guide.pdf',
    response_class=FileResponse,
    responses={200: {'content': {'application/pdf': {}}, 'description': 'The guide.'}},
    summary='Download the event guide',
    description="The organisers' event guide, as a PDF.\n\n" + _READER,
)
async def event_guide(reader: Optional[User] = Depends(require_reader)) -> FileResponse:
    guide = await EventInfoService.get_guide()
    if guide is None:
        raise NotFoundError('This event has no guide.')
    return FileResponse(
        guide.path, media_type='application/pdf', filename=guide.filename,
        content_disposition_type='inline',
        headers={'Cache-Control': 'public, max-age=300', 'X-Content-Type-Options': 'nosniff'},
    )


@event_info_router.get(
    '/{slug}',
    response_model=Article,
    summary='Get an event information article',
    description=(
        "One handbook article as blocks. An article with a `tournaments` embed also carries the "
        "live tournament list it draws, and the venue's clock that list's times read on. `404` for "
        "an article that doesn't exist or isn't for you.\n\n" + _READER
    ),
)
async def event_info_article(slug: str, reader: Optional[User] = Depends(require_reader)) -> Article:
    article = await EventInfoService.get_article(slug, reader)
    if article is None:
        raise NotFoundError('No such event information page here.')
    out = _article(article)
    if any(b.kind == 'embed' and b.name == 'tournaments' for b in article.blocks):
        out.tournaments = [
            HandbookTournamentOut(
                id=t.id, name=t.name, format=t.format, rules_url=t.rules_url, bracket_url=t.bracket_url,
                signups=t.signups.value, signups_close_at=t.signups_close_at,
                prize_total=t.prize_total, prize_synced_at=t.prize_synced_at,
            )
            for t in await EventInfoService.list_tournaments()
        ]
        out.venue_timezone = await TimezoneService.tenant_timezone_name(require_tenant_id())
    return out


_OPEN_TO_ALL = "**Who can call this:** Anyone; no token or community needed."


@app_router.get(
    '/changelog',
    response_model=Changelog,
    summary="Get What's new",
    description=(
        "The bigger changes to Wizzrobe, a week per entry, newest first, each week grouped into "
        "New, Improved, Fixed and so on.\n\n" + _OPEN_TO_ALL
    ),
)
async def changelog() -> Changelog:
    return Changelog(weeks=[
        ChangelogWeek(
            start=e.start, end=e.end, title=e.title, period=e.period, anchor=e.anchor,
            sections=[ChangelogSectionOut(category=s.category, count=s.count, blocks=blocks_out(s.blocks))
                      for s in e.sections],
        )
        for e in all_entries()
    ])


@app_router.get(
    '/about',
    response_model=AboutPage,
    summary='Get the About page',
    description="What Wizzrobe is, its licence, which build is running, and how many notices it ships.\n\n" + _OPEN_TO_ALL,
)
async def about() -> AboutPage:
    info = build_info()
    return AboutPage(
        intro=blocks_out(parse_blocks(ABOUT_INTRO)), built_with_claude=BUILT_WITH_CLAUDE,
        version=info.version, commit_short=info.commit_short, source_url=info.source_url,
        notice_count=sum(len(g.notices) for g in all_notices()),
    )


@app_router.get(
    '/about/privacy',
    response_model=PrivacyPolicy,
    summary='Get the privacy policy',
    description="The privacy policy as blocks, and when it last changed.\n\n" + _OPEN_TO_ALL,
)
async def privacy() -> PrivacyPolicy:
    return PrivacyPolicy(updated=PRIVACY_UPDATED, blocks=blocks_out(privacy_blocks()))


def _notice(n) -> NoticeOut:
    return NoticeOut(key=n.key, name=n.name, version=n.version, license=n.license, url=n.url)


@app_router.get(
    '/about/notices',
    response_model=Notices,
    summary='List third-party notices',
    description="The open-source software Wizzrobe ships, grouped, with each one's licence name.\n\n" + _OPEN_TO_ALL,
)
async def notices() -> Notices:
    return Notices(groups=[
        NoticeGroupOut(key=g.key, heading=g.heading, blurb=g.blurb, notices=[_notice(n) for n in g.notices])
        for g in all_notices()
    ])


@app_router.get(
    '/about/notice',
    response_model=NoticeText,
    summary='Get a third-party notice',
    description="One library's licence and copyright notice in full.\n\n" + _OPEN_TO_ALL,
)
async def notice(
    key: str = Query(..., max_length=300, description="The notice's `key`, from the list, such as `web:react`."),
) -> NoticeText:
    found = find_notice(key)
    if found is None:
        raise NotFoundError('No such notice.')
    return NoticeText(**_notice(found).model_dump(), text=found.reading_text)


@app_router.get(
    '/facts',
    response_model=Facts,
    summary='Get the facts',
    description="Every fact the app hides in empty lists and spinners, for this season's topic.\n\n" + _OPEN_TO_ALL,
)
async def facts() -> Facts:
    theme = current_theme()
    return Facts(title=theme.title, noun=theme.noun, icon=theme.icon, another=theme.another,
                 total=len(theme.facts), sections={k: list(v) for k, v in theme.sections.items()})

