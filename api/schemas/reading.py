"""Schemas for the pages anyone can read: help, the event handbook, What's new, About and facts.

Articles travel as the same closed set of blocks and spans the website renders
(``application/content``), never as HTML or Markdown, so the app draws each one
with its own element and article text can't become markup.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from application.content import Block, Span


class SpanOut(BaseModel):
    kind: str = Field(..., description='One of `text`, `strong`, `em`, `code`, `link`, `chip`, `state`.')
    text: str
    href: str = Field(..., description='Links only. Already checked safe: a site path (`/help/crew`), `#anchor`, `https://` or `mailto:`.')
    tone: str = Field(..., description='Chips only: `ok`, `pending`, `live`, `neutral`, `cancelled`, `candidate` or `new`.')

    @classmethod
    def of(cls, span: Span) -> 'SpanOut':
        return cls(kind=span.kind, text=span.text, href=span.href, tone=span.tone)


def _spans(spans) -> List[SpanOut]:
    return [SpanOut.of(s) for s in spans]


class BlockOut(BaseModel):
    kind: str = Field(..., description='One of `heading`, `para`, `bullets`, `numbers`, `table`, `note`, `image`, `embed`.')
    spans: List[SpanOut] = Field(..., description="Heading, paragraph or note text; an image's caption.")
    items: List[List[SpanOut]] = Field(..., description='List items.')
    headers: List[List[SpanOut]] = Field(..., description='Table header cells.')
    rows: List[List[List[SpanOut]]] = Field(..., description='Table rows of cells.')
    level: int = Field(..., description='Heading level, 2 to 4.')
    anchor: str = Field(..., description="A heading's fragment id, for On this page links.")
    src: str = Field(..., description="An image's path on the server, under `/static/help/`.")
    alt: str
    name: str = Field(..., description="An embed's name; `tournaments` draws the article's `tournaments` list in place.")

    @classmethod
    def of(cls, block: Block) -> 'BlockOut':
        return cls(
            kind=block.kind, spans=_spans(block.spans), items=[_spans(i) for i in block.items],
            headers=[_spans(c) for c in block.headers], rows=[[_spans(c) for c in row] for row in block.rows],
            level=block.level, anchor=block.anchor, src=block.src, alt=block.alt, name=block.name,
        )


def blocks_out(blocks) -> List[BlockOut]:
    return [BlockOut.of(b) for b in blocks]


class ArticleSummary(BaseModel):
    slug: str
    title: str
    summary: str
    icon: str = Field(..., description='A Material Symbols icon name.')
    for_staff: bool = Field(..., description='Listed under "For staff" (help only).')


class Heading(BaseModel):
    anchor: str
    text: str


class HandbookTournamentOut(BaseModel):
    id: int
    name: str
    format: str
    rules_url: str
    bracket_url: str = Field(..., description='A site path for a bracket Wizzrobe shows, else an outside link, else empty.')
    signups: str = Field(..., description='`upcoming`, `open` or `closed`.')
    signups_close_at: Optional[datetime]
    prize_total: Optional[Decimal] = None
    prize_synced_at: Optional[datetime] = None


class Article(ArticleSummary):
    headings: List[Heading] = Field(..., description='The On this page list: every top-level heading.')
    blocks: List[BlockOut]
    tournaments: Optional[List[HandbookTournamentOut]] = Field(
        None, description='Handbook articles with a `tournaments` embed: the live list it draws.')
    venue_timezone: Optional[str] = Field(
        None, description='The clock the article states times on (IANA name). Handbook articles only.')


class HelpIndex(BaseModel):
    articles: List[ArticleSummary] = Field(..., description='Every article you may open, "For staff" last.')


class Snippet(BaseModel):
    name: str
    title: str = Field(..., description='The article it comes from.')
    heading: str
    href: str = Field(..., description='The article, at the heading the snippet sits under.')
    blocks: List[BlockOut]


class GuideInfo(BaseModel):
    filename: str
    size_bytes: int
    size_label: str = Field(..., description='Such as `4.2 MB`.')


class EventInfoIndex(BaseModel):
    articles: List[ArticleSummary]
    guide: Optional[GuideInfo] = Field(None, description="The organisers' PDF guide, when there is one.")


class ChangelogSectionOut(BaseModel):
    category: str = Field(..., description='`New`, `Improved`, `Fixed`, `Security` or `Behind the scenes`.')
    count: int
    blocks: List[BlockOut]


class ChangelogWeek(BaseModel):
    start: date
    end: date
    title: str
    period: str = Field(..., description='The dates, written out: `26 Oct – 1 Nov`.')
    anchor: str
    sections: List[ChangelogSectionOut]


class Changelog(BaseModel):
    weeks: List[ChangelogWeek] = Field(..., description='Newest first.')


class AboutPage(BaseModel):
    intro: List[BlockOut]
    built_with_claude: str
    version: str
    commit_short: Optional[str]
    source_url: str
    notice_count: int


class PrivacyPolicy(BaseModel):
    updated: date
    blocks: List[BlockOut]


class NoticeOut(BaseModel):
    key: str
    name: str
    version: str
    license: str
    url: Optional[str]


class NoticeGroupOut(BaseModel):
    key: str
    heading: str
    blurb: str
    notices: List[NoticeOut]


class NoticeText(NoticeOut):
    text: str = Field(..., description='The licence and copyright notice, as plain text.')


class Notices(BaseModel):
    groups: List[NoticeGroupOut]


class Facts(BaseModel):
    title: str
    noun: str
    icon: str
    another: str = Field(..., description='The shuffle button\'s label.')
    total: int
    sections: Dict[str, List[str]]
