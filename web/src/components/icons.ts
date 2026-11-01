// Generated list: add a name here (and to the import block) to use a new icon.
// Each SVG is bundled on its own, so only icons listed here ship.
import type { FC, SVGProps } from 'react'
import Home from '@material-symbols/svg-400/outlined/home.svg?react'
import CalendarMonth from '@material-symbols/svg-400/outlined/calendar_month.svg?react'
import Stadium from '@material-symbols/svg-400/outlined/stadium.svg?react'
import Badge from '@material-symbols/svg-400/outlined/badge.svg?react'
import Person from '@material-symbols/svg-400/outlined/person.svg?react'
import MoreVert from '@material-symbols/svg-400/outlined/more_vert.svg?react'
import Close from '@material-symbols/svg-400/outlined/close.svg?react'
import KeyboardArrowDown from '@material-symbols/svg-400/outlined/keyboard_arrow_down.svg?react'
import Notifications from '@material-symbols/svg-400/outlined/notifications.svg?react'
import Logout from '@material-symbols/svg-400/outlined/logout.svg?react'
import CheckCircle from '@material-symbols/svg-400/outlined/check_circle.svg?react'
import Check from '@material-symbols/svg-400/outlined/check.svg?react'
import Event from '@material-symbols/svg-400/outlined/event.svg?react'
import LightMode from '@material-symbols/svg-400/outlined/light_mode.svg?react'
import DarkMode from '@material-symbols/svg-400/outlined/dark_mode.svg?react'
import BrightnessAuto from '@material-symbols/svg-400/outlined/brightness_auto.svg?react'
import Login from '@material-symbols/svg-400/outlined/login.svg?react'
import Error from '@material-symbols/svg-400/outlined/error.svg?react'
import Info from '@material-symbols/svg-400/outlined/info.svg?react'
import Warning from '@material-symbols/svg-400/outlined/warning.svg?react'
import ChevronRight from '@material-symbols/svg-400/outlined/chevron_right.svg?react'
import ProgressActivity from '@material-symbols/svg-400/outlined/progress_activity.svg?react'
import Groups from '@material-symbols/svg-400/outlined/groups.svg?react'
import ArrowBack from '@material-symbols/svg-400/outlined/arrow_back.svg?react'
import HomeFill from '@material-symbols/svg-400/outlined/home-fill.svg?react'
import CalendarMonthFill from '@material-symbols/svg-400/outlined/calendar_month-fill.svg?react'
import StadiumFill from '@material-symbols/svg-400/outlined/stadium-fill.svg?react'
import BadgeFill from '@material-symbols/svg-400/outlined/badge-fill.svg?react'
import PersonFill from '@material-symbols/svg-400/outlined/person-fill.svg?react'

type Svg = FC<SVGProps<SVGSVGElement>>

export const ICONS = {
  home: Home,
  calendar_month: CalendarMonth,
  stadium: Stadium,
  badge: Badge,
  person: Person,
  more_vert: MoreVert,
  close: Close,
  keyboard_arrow_down: KeyboardArrowDown,
  notifications: Notifications,
  logout: Logout,
  check_circle: CheckCircle,
  check: Check,
  event: Event,
  light_mode: LightMode,
  dark_mode: DarkMode,
  brightness_auto: BrightnessAuto,
  login: Login,
  error: Error,
  info: Info,
  warning: Warning,
  chevron_right: ChevronRight,
  progress_activity: ProgressActivity,
  groups: Groups,
  arrow_back: ArrowBack,
} satisfies Record<string, Svg>

export const FILLED: Partial<Record<IconName, Svg>> = {
  home: HomeFill,
  calendar_month: CalendarMonthFill,
  stadium: StadiumFill,
  badge: BadgeFill,
  person: PersonFill,
}

export type IconName = keyof typeof ICONS
