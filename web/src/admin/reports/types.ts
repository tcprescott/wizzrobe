import type { DateRange, ReportsMenu } from './range'

/** What every report view gets from the screen. */
export interface ReportProps {
  menu: ReportsMenu
  range: DateRange
  /** The range as UTC instants, for the endpoints that take date-times. */
  bounds: { start: string; end: string }
  /** The clock the range's days follow; times show on it too. */
  zone: string
  adminBase: string
}
