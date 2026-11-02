import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Icon, PageHeader, type IconName } from '../components'
import { ArticleView, Loaded, NotHere } from './ArticleView'
import { contentIcon } from './Prose'
import { TournamentList } from './TournamentList'
import {
  useEventInfo,
  useEventInfoArticle,
  useHelpArticle,
  useHelpIndex,
  useReadingBase,
  type ArticleSummary,
} from './data'
import './reading.css'

export function Tile({ to, icon, title, summary }: { to: string; icon: IconName; title: string; summary?: string }) {
  return (
    <Link to={to} className="read-tile">
      <Icon name={icon} />
      <span className="read-tile__text">
        <span className="read-tile__title">{title}</span>
        {summary && <span className="read-tile__sub">{summary}</span>}
      </span>
      <Icon name="chevron_right" className="read-tile__chev" />
    </Link>
  )
}

function Group({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="read-group" aria-label={title}>
      {title && <h2 className="read-group__title">{title}</h2>}
      <div className="read-tiles">{children}</div>
    </section>
  )
}

function useDebounced(value: string, ms = 250): string {
  const [out, setOut] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setOut(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return out
}

export function HelpIndexScreen() {
  const { base } = useReadingBase()
  const [text, setText] = useState('')
  const q = useDebounced(text.trim())
  const query = useHelpIndex(q)
  const tile = (a: ArticleSummary) => (
    <Tile key={a.slug} to={`${base}/help/${a.slug}`} icon={contentIcon(a.icon)} title={a.title} summary={a.summary} />
  )
  return (
    <div className="read-page">
      <PageHeader title="Help" lede="How the app works for players, crew and volunteers. Search, or pick a topic." />
      <label className="read-search">
        <Icon name="search" />
        <span className="visually-hidden">Search help</span>
        <input type="search" placeholder="Search help" value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <Loaded query={query} what="help">
        {({ articles }) => {
          const general = articles.filter((a) => !a.for_staff)
          const staff = articles.filter((a) => a.for_staff)
          if (!articles.length) {
            return <p className="muted" role="status">{q ? `Nothing matches "${q}". Try fewer words.` : 'No help articles are available.'}</p>
          }
          return (
            <>
              {general.length > 0 && <Group title="Topics">{general.map(tile)}</Group>}
              {staff.length > 0 && <Group title="For staff">{staff.map(tile)}</Group>}
            </>
          )
        }}
      </Loaded>
      <Group title="More">
        <Tile to={`${base}/changelog`} icon="release_alert" title="What's new" summary="The bigger changes, a week at a time." />
        <Tile to={`${base}/about`} icon="info" title="About Wizzrobe" summary="Privacy policy, third-party notices and the version." />
      </Group>
    </div>
  )
}

export function HelpArticleScreen() {
  const { slug = '' } = useParams()
  const { base } = useReadingBase()
  const article = useHelpArticle(slug)
  const index = useHelpIndex('')
  return (
    <Loaded
      query={article}
      what="the article"
      missing={<NotHere title="That help page isn't here" back="All help" to={`${base}/help`} />}
    >
      {(data) => (
        <ArticleView article={data} articles={index.data?.articles ?? []} section="help" backLabel="All help" />
      )}
    </Loaded>
  )
}

function GuideLink() {
  const { base } = useReadingBase()
  return (
    <Link to={`${base}/event-info/guide`} className="read-guide-link">
      <Icon name="picture_as_pdf" size={18} />
      Full details in the event guide
    </Link>
  )
}

export function EventInfoIndexScreen() {
  const { base } = useReadingBase()
  const query = useEventInfo()
  return (
    <div className="read-page">
      <Loaded
        query={query}
        what="event information"
        missing={
          <NotHere title="This community has no event information" back="How the app works" to={`${base}/help`} />
        }
      >
        {({ articles, guide }) => (
          <>
            <PageHeader
              title="Event information"
              lede={
                guide
                  ? 'The short version: hours, tournaments, getting set up and who to ask.'
                  : 'Everything about this event: what is on, attending on the day, and who to ask.'
              }
            />
            {guide && (
              <div className="read-guide">
                <Icon name="picture_as_pdf" />
                <div className="read-guide__text">
                  <span className="read-tile__title">The full event guide</span>
                  <span className="read-tile__sub">Directions, the hotel map and every detail. PDF, {guide.size_label}.</span>
                </div>
                <Link to={`${base}/event-info/guide`} className="btn btn--primary btn--md">
                  Open guide
                </Link>
              </div>
            )}
            {!articles.length && !guide ? (
              <p className="muted">Nothing has been published here yet.</p>
            ) : (
              <Group>
                {articles.map((a) => (
                  <Tile key={a.slug} to={`${base}/event-info/${a.slug}`} icon={contentIcon(a.icon)} title={a.title} summary={a.summary} />
                ))}
              </Group>
            )}
            <p className="muted read-foot">
              Looking for how the app works? <Link to={`${base}/help`}>Help</Link>
            </p>
          </>
        )}
      </Loaded>
    </div>
  )
}

export function EventInfoArticleScreen() {
  const { slug = '' } = useParams()
  const { base } = useReadingBase()
  const article = useEventInfoArticle(slug)
  const index = useEventInfo()
  return (
    <Loaded
      query={article}
      what="the page"
      missing={
        <NotHere title="That page isn't here" back="All event information" to={`${base}/event-info`}>
          Some pages are only for crew and staff. If you have a role in this community, sign in and try again.
        </NotHere>
      }
    >
      {(data) => (
        <ArticleView
          article={data}
          articles={index.data?.articles ?? []}
          section="event-info"
          backLabel="All event information"
          intro={index.data?.guide ? <GuideLink /> : undefined}
          embeds={
            data.tournaments
              ? { tournaments: <TournamentList rows={data.tournaments} zone={data.venue_timezone ?? 'UTC'} /> }
              : undefined
          }
        />
      )}
    </Loaded>
  )
}
