import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { EChartsCoreOption } from 'echarts/core'
import { Spinner } from '../../components'

/** Token colours resolved to rgb(), since ECharts can't read CSS variables. */
export interface Palette {
  fg: string
  muted: string
  grid: string
  surface: string
  accent: string
  accentSoft: string
  ok: string
  pending: string
  bad: string
  info: string
  live: string
  font: string
}

const TOKENS: Record<Exclude<keyof Palette, 'font'>, string> = {
  fg: '--fg',
  muted: '--fg-muted',
  grid: '--border-subtle',
  surface: '--surface',
  accent: '--accent',
  accentSoft: '--accent-soft',
  ok: '--status-ok',
  pending: '--status-pending',
  bad: '--status-cancelled',
  info: '--status-info',
  live: '--status-live',
}

function readPalette(host: HTMLElement): Palette {
  const probe = document.createElement('span')
  probe.style.display = 'none'
  host.appendChild(probe)
  const out = { font: getComputedStyle(host).fontFamily } as Palette
  for (const [key, token] of Object.entries(TOKENS)) {
    probe.style.color = `var(${token})`
    out[key as keyof typeof TOKENS] = getComputedStyle(probe).color
  }
  probe.remove()
  return out
}

/** The axes, grid and tooltip every report chart shares. */
export function baseOption(p: Palette): EChartsCoreOption {
  return {
    animation: false,
    textStyle: { fontFamily: p.font, color: p.fg },
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: p.surface,
      borderColor: p.grid,
      textStyle: { color: p.fg, fontFamily: p.font },
    },
    legend: { top: 0, left: 0, textStyle: { color: p.fg }, icon: 'roundRect', itemWidth: 12, itemHeight: 12 },
  }
}

export function axisStyle(p: Palette) {
  return {
    axisLine: { lineStyle: { color: p.grid } },
    axisTick: { show: false },
    axisLabel: { color: p.muted },
    splitLine: { lineStyle: { color: p.grid } },
  }
}

/**
 * One ECharts chart, loaded on first use. `summary` is what a screen reader
 * hears; every report also shows its numbers as text or a table below.
 */
export function Chart({
  option,
  summary,
  height = 260,
  children,
}: {
  option: (palette: Palette) => EChartsCoreOption
  summary: string
  height?: number
  children?: ReactNode
}) {
  const host = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  const optionRef = useRef(option)
  optionRef.current = option

  useEffect(() => {
    const el = host.current
    if (!el) return
    let chart: import('echarts/core').ECharts | undefined
    let dead = false
    const paint = () => chart?.setOption(optionRef.current(readPalette(el)), true)
    const resize = new ResizeObserver(() => chart?.resize())
    const theme = new MutationObserver(paint)
    const scheme = window.matchMedia('(prefers-color-scheme: dark)')
    void import('./echarts').then(({ echarts }) => {
      if (dead) return
      chart = echarts.init(el, undefined, { renderer: 'svg' })
      paint()
      setReady(true)
      resize.observe(el)
      theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style', 'class'] })
      scheme.addEventListener('change', paint)
    })
    return () => {
      dead = true
      resize.disconnect()
      theme.disconnect()
      scheme.removeEventListener('change', paint)
      chart?.dispose()
    }
  }, [])

  useEffect(() => {
    const el = host.current
    if (!el || !ready) return
    void import('./echarts').then(({ echarts }) => {
      echarts.getInstanceByDom(el)?.setOption(option(readPalette(el)), true)
    })
  }, [option, ready])

  return (
    <figure className="report-chart">
      <div className="report-chart__frame" style={{ height }}>
        <div ref={host} className="report-chart__canvas" role="img" aria-label={summary} />
        {!ready && (
          <div className="report-chart__wait">
            <Spinner label="Loading chart" />
          </div>
        )}
      </div>
      {children && <figcaption className="report-chart__caption">{children}</figcaption>}
    </figure>
  )
}
