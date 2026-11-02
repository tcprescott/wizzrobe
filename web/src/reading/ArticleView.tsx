/**
 * One help or event information article. Desktop is three columns, as on the
 * website: the section's articles, the article, and On this page. Below
 * 1280px the rail folds into an "On this page" disclosure above the body, and
 * on a phone the article list goes too, leaving a back link.
 */
import type { ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { Banner, Button, EmptyState, Icon, Spinner } from '../components'
import type { QueryState } from '../api/useQuery'
import { ApiError } from '../api/client'
import { contentIcon, Prose } from './Prose'
import { useReadingBase, type ArticleData, type ArticleSummary } from './data'

interface ArticleViewProps {
  article: ArticleData
  articles: ArticleSummary[]
  /** `help` or `event-info`: the section's path under the base. */
  section: string
  backLabel: string
  embeds?: Record<string, ReactNode>
  intro?: ReactNode
}

function Contents({ article }: { article: ArticleData }) {
  return (
    <ul className="article-toc">
      {article.headings.map((h) => (
        <li key={h.anchor}>
          <a href={`#${h.anchor}`}>{h.text}</a>
        </li>
      ))}
    </ul>
  )
}

export function ArticleView({ article, articles, section, backLabel, embeds, intro }: ArticleViewProps) {
  const { base } = useReadingBase()
  const home = `${base}/${section}`
  const staffStart = articles.findIndex((a) => a.for_staff)
  return (
    <div className="article-layout">
      <nav className="article-nav" aria-label={backLabel.replace(/^All /, '').replace(/^./, (c) => c.toUpperCase())}>
        <Link to={home} className="back-link">
          <Icon name="chevron_left" size={20} />
          {backLabel}
        </Link>
        {staffStart > 0 && <p className="article-nav__group">Everyone</p>}
        <ul>
          {articles.map((a, i) => (
            <li key={a.slug}>
              {i === staffStart && <p className="article-nav__group">For staff</p>}
              <NavLink to={`${home}/${a.slug}`} end>
                {a.title}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <article className="article-body">
        <Link to={home} className="back-link article-body__back">
          <Icon name="chevron_left" size={20} />
          {backLabel}
        </Link>
        {article.headings.length > 0 && (
          <details className="article-toc-fold">
            <summary>On this page</summary>
            <Contents article={article} />
          </details>
        )}
        <header className="article-head">
          <h1>
            <Icon name={contentIcon(article.icon)} size={26} />
            {article.title}
          </h1>
          {article.summary && <p className="article-head__lede">{article.summary}</p>}
          {intro}
        </header>
        <Prose blocks={article.blocks} embeds={embeds} />
      </article>
      {article.headings.length > 0 && (
        <nav className="article-rail" aria-label="On this page">
          <p className="article-nav__group">On this page</p>
          <Contents article={article} />
        </nav>
      )}
    </div>
  )
}

/** Loading, failure and not-found for a reading screen; `children` once the data is in. */
export function Loaded<T>({
  query,
  what,
  missing,
  children,
}: {
  query: QueryState<T>
  what: string
  missing?: ReactNode
  children: (data: T) => ReactNode
}) {
  const { data, error, loading, reload } = query
  if (data !== undefined) return <>{children(data)}</>
  if (error instanceof ApiError && error.status === 404 && missing) return <>{missing}</>
  if (error) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
        {error.message}
      </Banner>
    )
  }
  return loading ? <Spinner label={`Loading ${what}`} /> : null
}

export function NotHere({ title, back, to, children }: { title: string; back: string; to: string; children?: ReactNode }) {
  return (
    <EmptyState icon="info" title={title}>
      {children}
      <p>
        <Link to={to}>{back}</Link>
      </p>
    </EmptyState>
  )
}
