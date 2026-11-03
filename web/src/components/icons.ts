// Every icon the app can draw. Each SVG is bundled on its own, so only icons
// listed here ship. To add one, put its import and its ICONS entry in
// alphabetical order (icons.test.ts checks), not at the end: appended lines
// from parallel PRs conflict, sorted ones rarely touch.
import type { FC, SVGProps } from 'react'
import Accessible from '@material-symbols/svg-400/outlined/accessible.svg?react'
import AccountTreeIcon from '@material-symbols/svg-400/outlined/account_tree.svg?react'
import AddIcon from '@material-symbols/svg-400/outlined/add.svg?react'
import AddLinkIcon from '@material-symbols/svg-400/outlined/add_link.svg?react'
import AdminPanelSettingsIcon from '@material-symbols/svg-400/outlined/admin_panel_settings.svg?react'
import AnalyticsIcon from '@material-symbols/svg-400/outlined/analytics.svg?react'
import AppsIcon from '@material-symbols/svg-400/outlined/apps.svg?react'
import ArrowBack from '@material-symbols/svg-400/outlined/arrow_back.svg?react'
import ArrowDownwardIcon from '@material-symbols/svg-400/outlined/arrow_downward.svg?react'
import ArrowUpwardIcon from '@material-symbols/svg-400/outlined/arrow_upward.svg?react'
import IAttractions from '@material-symbols/svg-400/outlined/attractions.svg?react'
import AutorenewIcon from '@material-symbols/svg-400/outlined/autorenew.svg?react'
import Badge from '@material-symbols/svg-400/outlined/badge.svg?react'
import BadgeFill from '@material-symbols/svg-400/outlined/badge-fill.svg?react'
import BlockIcon from '@material-symbols/svg-400/outlined/block.svg?react'
import BrightnessAuto from '@material-symbols/svg-400/outlined/brightness_auto.svg?react'
import BugReportIcon from '@material-symbols/svg-400/outlined/bug_report.svg?react'
import CalendarMonth from '@material-symbols/svg-400/outlined/calendar_month.svg?react'
import CalendarMonthFill from '@material-symbols/svg-400/outlined/calendar_month-fill.svg?react'
import CalendarViewWeekIcon from '@material-symbols/svg-400/outlined/calendar_view_week.svg?react'
import Campaign from '@material-symbols/svg-400/outlined/campaign.svg?react'
import Casino from '@material-symbols/svg-400/outlined/casino.svg?react'
import CelebrationIcon from '@material-symbols/svg-400/outlined/celebration.svg?react'
import ChairIcon from '@material-symbols/svg-400/outlined/chair.svg?react'
import ChangeHistoryIcon from '@material-symbols/svg-400/outlined/change_history.svg?react'
import ChatIcon from '@material-symbols/svg-400/outlined/chat.svg?react'
import ChatBubbleIcon from '@material-symbols/svg-400/outlined/chat_bubble.svg?react'
import Check from '@material-symbols/svg-400/outlined/check.svg?react'
import CheckCircle from '@material-symbols/svg-400/outlined/check_circle.svg?react'
import ChecklistIcon from '@material-symbols/svg-400/outlined/checklist.svg?react'
import ChevronLeft from '@material-symbols/svg-400/outlined/chevron_left.svg?react'
import ChevronRight from '@material-symbols/svg-400/outlined/chevron_right.svg?react'
import Close from '@material-symbols/svg-400/outlined/close.svg?react'
import ICode from '@material-symbols/svg-400/outlined/code.svg?react'
import ContactEmergency from '@material-symbols/svg-400/outlined/contact_emergency.svg?react'
import IContactPhone from '@material-symbols/svg-400/outlined/contact_phone.svg?react'
import IContactSupport from '@material-symbols/svg-400/outlined/contact_support.svg?react'
import ContentCopy from '@material-symbols/svg-400/outlined/content_copy.svg?react'
import DarkMode from '@material-symbols/svg-400/outlined/dark_mode.svg?react'
import DashboardIcon from '@material-symbols/svg-400/outlined/dashboard.svg?react'
import DateRangeIcon from '@material-symbols/svg-400/outlined/date_range.svg?react'
import DeleteIcon from '@material-symbols/svg-400/outlined/delete.svg?react'
import IDescription from '@material-symbols/svg-400/outlined/description.svg?react'
import Desk from '@material-symbols/svg-400/outlined/desk.svg?react'
import DirectionsRun from '@material-symbols/svg-400/outlined/directions_run.svg?react'
import IDownload from '@material-symbols/svg-400/outlined/download.svg?react'
import EditIcon from '@material-symbols/svg-400/outlined/edit.svg?react'
import EditCalendarIcon from '@material-symbols/svg-400/outlined/edit_calendar.svg?react'
import Error from '@material-symbols/svg-400/outlined/error.svg?react'
import Event from '@material-symbols/svg-400/outlined/event.svg?react'
import EventAvailable from '@material-symbols/svg-400/outlined/event_available.svg?react'
import EventBusy from '@material-symbols/svg-400/outlined/event_busy.svg?react'
import EventNoteIcon from '@material-symbols/svg-400/outlined/event_note.svg?react'
import ExtensionIcon from '@material-symbols/svg-400/outlined/extension.svg?react'
import FactCheckIcon from '@material-symbols/svg-400/outlined/fact_check.svg?react'
import FavoriteIcon from '@material-symbols/svg-400/outlined/favorite.svg?react'
import Feedback from '@material-symbols/svg-400/outlined/feedback.svg?react'
import FlagIcon from '@material-symbols/svg-400/outlined/flag.svg?react'
import FormatListNumberedIcon from '@material-symbols/svg-400/outlined/format_list_numbered.svg?react'
import ForumIcon from '@material-symbols/svg-400/outlined/forum.svg?react'
import GroupAddIcon from '@material-symbols/svg-400/outlined/group_add.svg?react'
import Groups from '@material-symbols/svg-400/outlined/groups.svg?react'
import Groups2Icon from '@material-symbols/svg-400/outlined/groups_2.svg?react'
import IHelp from '@material-symbols/svg-400/outlined/help.svg?react'
import HideImageIcon from '@material-symbols/svg-400/outlined/hide_image.svg?react'
import History from '@material-symbols/svg-400/outlined/history.svg?react'
import Home from '@material-symbols/svg-400/outlined/home.svg?react'
import HomeFill from '@material-symbols/svg-400/outlined/home-fill.svg?react'
import HowToReg from '@material-symbols/svg-400/outlined/how_to_reg.svg?react'
import ImageIcon from '@material-symbols/svg-400/outlined/image.svg?react'
import Inbox from '@material-symbols/svg-400/outlined/inbox.svg?react'
import Info from '@material-symbols/svg-400/outlined/info.svg?react'
import Inventory2 from '@material-symbols/svg-400/outlined/inventory_2.svg?react'
import KeepIcon from '@material-symbols/svg-400/outlined/keep.svg?react'
import KeyIcon from '@material-symbols/svg-400/outlined/key.svg?react'
import KeyOffIcon from '@material-symbols/svg-400/outlined/key_off.svg?react'
import KeyboardArrowDown from '@material-symbols/svg-400/outlined/keyboard_arrow_down.svg?react'
import LeaderboardIcon from '@material-symbols/svg-400/outlined/leaderboard.svg?react'
import LightMode from '@material-symbols/svg-400/outlined/light_mode.svg?react'
import ILightbulb from '@material-symbols/svg-400/outlined/lightbulb.svg?react'
import LinkIcon from '@material-symbols/svg-400/outlined/link.svg?react'
import LinkOff from '@material-symbols/svg-400/outlined/link_off.svg?react'
import IList from '@material-symbols/svg-400/outlined/list.svg?react'
import LiveTvIcon from '@material-symbols/svg-400/outlined/live_tv.svg?react'
import LocationOn from '@material-symbols/svg-400/outlined/location_on.svg?react'
import Lock from '@material-symbols/svg-400/outlined/lock.svg?react'
import LockOpenIcon from '@material-symbols/svg-400/outlined/lock_open.svg?react'
import Login from '@material-symbols/svg-400/outlined/login.svg?react'
import Logout from '@material-symbols/svg-400/outlined/logout.svg?react'
import MailIcon from '@material-symbols/svg-400/outlined/mail.svg?react'
import ManageAccountsIcon from '@material-symbols/svg-400/outlined/manage_accounts.svg?react'
import MeetingRoomIcon from '@material-symbols/svg-400/outlined/meeting_room.svg?react'
import IMenuBook from '@material-symbols/svg-400/outlined/menu_book.svg?react'
import Mic from '@material-symbols/svg-400/outlined/mic.svg?react'
import MilitaryTechIcon from '@material-symbols/svg-400/outlined/military_tech.svg?react'
import Mobile from '@material-symbols/svg-400/outlined/mobile.svg?react'
import MonitorHeartIcon from '@material-symbols/svg-400/outlined/monitor_heart.svg?react'
import MoreVert from '@material-symbols/svg-400/outlined/more_vert.svg?react'
import IMusicNote from '@material-symbols/svg-400/outlined/music_note.svg?react'
import Notifications from '@material-symbols/svg-400/outlined/notifications.svg?react'
import NotificationsActiveIcon from '@material-symbols/svg-400/outlined/notifications_active.svg?react'
import NotificationsOffIcon from '@material-symbols/svg-400/outlined/notifications_off.svg?react'
import OpenInNew from '@material-symbols/svg-400/outlined/open_in_new.svg?react'
import Palette from '@material-symbols/svg-400/outlined/palette.svg?react'
import PauseIcon from '@material-symbols/svg-400/outlined/pause.svg?react'
import PaymentsIcon from '@material-symbols/svg-400/outlined/payments.svg?react'
import IPendingActions from '@material-symbols/svg-400/outlined/pending_actions.svg?react'
import Person from '@material-symbols/svg-400/outlined/person.svg?react'
import PersonFill from '@material-symbols/svg-400/outlined/person-fill.svg?react'
import PersonAddIcon from '@material-symbols/svg-400/outlined/person_add.svg?react'
import PersonCheckIcon from '@material-symbols/svg-400/outlined/person_check.svg?react'
import PersonOffIcon from '@material-symbols/svg-400/outlined/person_off.svg?react'
import PersonRemoveIcon from '@material-symbols/svg-400/outlined/person_remove.svg?react'
import IPets from '@material-symbols/svg-400/outlined/pets.svg?react'
import IPictureAsPdf from '@material-symbols/svg-400/outlined/picture_as_pdf.svg?react'
import IPictureInPicture from '@material-symbols/svg-400/outlined/picture_in_picture.svg?react'
import PlayArrow from '@material-symbols/svg-400/outlined/play_arrow.svg?react'
import PlayCircleIcon from '@material-symbols/svg-400/outlined/play_circle.svg?react'
import PodcastsIcon from '@material-symbols/svg-400/outlined/podcasts.svg?react'
import PriorityHigh from '@material-symbols/svg-400/outlined/priority_high.svg?react'
import IPrivacyTip from '@material-symbols/svg-400/outlined/privacy_tip.svg?react'
import ProgressActivity from '@material-symbols/svg-400/outlined/progress_activity.svg?react'
import PublicIcon from '@material-symbols/svg-400/outlined/public.svg?react'
import RadioButtonUncheckedIcon from '@material-symbols/svg-400/outlined/radio_button_unchecked.svg?react'
import RateReviewIcon from '@material-symbols/svg-400/outlined/rate_review.svg?react'
import IRecordVoiceOver from '@material-symbols/svg-400/outlined/record_voice_over.svg?react'
import IRefresh from '@material-symbols/svg-400/outlined/refresh.svg?react'
import IReleaseAlert from '@material-symbols/svg-400/outlined/release_alert.svg?react'
import RestartAlt from '@material-symbols/svg-400/outlined/restart_alt.svg?react'
import Schedule from '@material-symbols/svg-400/outlined/schedule.svg?react'
import SchemaIcon from '@material-symbols/svg-400/outlined/schema.svg?react'
import Search from '@material-symbols/svg-400/outlined/search.svg?react'
import SendIcon from '@material-symbols/svg-400/outlined/send.svg?react'
import SettingsIcon from '@material-symbols/svg-400/outlined/settings.svg?react'
import ShuffleIcon from '@material-symbols/svg-400/outlined/shuffle.svg?react'
import SkipNextIcon from '@material-symbols/svg-400/outlined/skip_next.svg?react'
import ISmartToy from '@material-symbols/svg-400/outlined/smart_toy.svg?react'
import SportsEsports from '@material-symbols/svg-400/outlined/sports_esports.svg?react'
import Stadium from '@material-symbols/svg-400/outlined/stadium.svg?react'
import StadiumFill from '@material-symbols/svg-400/outlined/stadium-fill.svg?react'
import StarIcon from '@material-symbols/svg-400/outlined/star.svg?react'
import ISupportAgent from '@material-symbols/svg-400/outlined/support_agent.svg?react'
import SwapHoriz from '@material-symbols/svg-400/outlined/swap_horiz.svg?react'
import SwapVertIcon from '@material-symbols/svg-400/outlined/swap_vert.svg?react'
import SyncIcon from '@material-symbols/svg-400/outlined/sync.svg?react'
import SyncAltIcon from '@material-symbols/svg-400/outlined/sync_alt.svg?react'
import TaskAlt from '@material-symbols/svg-400/outlined/task_alt.svg?react'
import TextFieldsIcon from '@material-symbols/svg-400/outlined/text_fields.svg?react'
import TimerIcon from '@material-symbols/svg-400/outlined/timer.svg?react'
import ToggleOnIcon from '@material-symbols/svg-400/outlined/toggle_on.svg?react'
import TrophyIcon from '@material-symbols/svg-400/outlined/trophy.svg?react'
import TuneIcon from '@material-symbols/svg-400/outlined/tune.svg?react'
import Tv from '@material-symbols/svg-400/outlined/tv.svg?react'
import UnarchiveIcon from '@material-symbols/svg-400/outlined/unarchive.svg?react'
import UndoIcon from '@material-symbols/svg-400/outlined/undo.svg?react'
import UploadIcon from '@material-symbols/svg-400/outlined/upload.svg?react'
import IVerified from '@material-symbols/svg-400/outlined/verified.svg?react'
import Videocam from '@material-symbols/svg-400/outlined/videocam.svg?react'
import IVideogameAsset from '@material-symbols/svg-400/outlined/videogame_asset.svg?react'
import ViewColumnIcon from '@material-symbols/svg-400/outlined/view_column.svg?react'
import ViewTimelineIcon from '@material-symbols/svg-400/outlined/view_timeline.svg?react'
import VisibilityIcon from '@material-symbols/svg-400/outlined/visibility.svg?react'
import VisibilityOffIcon from '@material-symbols/svg-400/outlined/visibility_off.svg?react'
import VolunteerActivismIcon from '@material-symbols/svg-400/outlined/volunteer_activism.svg?react'
import Warning from '@material-symbols/svg-400/outlined/warning.svg?react'
import Webhook from '@material-symbols/svg-400/outlined/webhook.svg?react'

type Svg = FC<SVGProps<SVGSVGElement>>

export const ICONS = {
  accessible: Accessible,
  account_tree: AccountTreeIcon,
  add: AddIcon,
  add_link: AddLinkIcon,
  admin_panel_settings: AdminPanelSettingsIcon,
  analytics: AnalyticsIcon,
  apps: AppsIcon,
  arrow_back: ArrowBack,
  arrow_downward: ArrowDownwardIcon,
  arrow_upward: ArrowUpwardIcon,
  attractions: IAttractions,
  autorenew: AutorenewIcon,
  badge: Badge,
  block: BlockIcon,
  brightness_auto: BrightnessAuto,
  bug_report: BugReportIcon,
  calendar_month: CalendarMonth,
  calendar_view_week: CalendarViewWeekIcon,
  campaign: Campaign,
  casino: Casino,
  celebration: CelebrationIcon,
  chair: ChairIcon,
  change_history: ChangeHistoryIcon,
  chat: ChatIcon,
  chat_bubble: ChatBubbleIcon,
  check: Check,
  check_circle: CheckCircle,
  checklist: ChecklistIcon,
  chevron_left: ChevronLeft,
  chevron_right: ChevronRight,
  close: Close,
  code: ICode,
  contact_emergency: ContactEmergency,
  contact_phone: IContactPhone,
  contact_support: IContactSupport,
  content_copy: ContentCopy,
  dark_mode: DarkMode,
  dashboard: DashboardIcon,
  date_range: DateRangeIcon,
  delete: DeleteIcon,
  description: IDescription,
  desk: Desk,
  directions_run: DirectionsRun,
  download: IDownload,
  edit: EditIcon,
  edit_calendar: EditCalendarIcon,
  error: Error,
  event: Event,
  event_available: EventAvailable,
  event_busy: EventBusy,
  event_note: EventNoteIcon,
  extension: ExtensionIcon,
  fact_check: FactCheckIcon,
  favorite: FavoriteIcon,
  feedback: Feedback,
  flag: FlagIcon,
  format_list_numbered: FormatListNumberedIcon,
  forum: ForumIcon,
  group_add: GroupAddIcon,
  groups: Groups,
  groups_2: Groups2Icon,
  help: IHelp,
  hide_image: HideImageIcon,
  history: History,
  home: Home,
  how_to_reg: HowToReg,
  image: ImageIcon,
  inbox: Inbox,
  info: Info,
  inventory_2: Inventory2,
  keep: KeepIcon,
  key: KeyIcon,
  key_off: KeyOffIcon,
  keyboard_arrow_down: KeyboardArrowDown,
  leaderboard: LeaderboardIcon,
  light_mode: LightMode,
  lightbulb: ILightbulb,
  link: LinkIcon,
  link_off: LinkOff,
  list: IList,
  live_tv: LiveTvIcon,
  location_on: LocationOn,
  lock: Lock,
  lock_open: LockOpenIcon,
  login: Login,
  logout: Logout,
  mail: MailIcon,
  manage_accounts: ManageAccountsIcon,
  meeting_room: MeetingRoomIcon,
  menu_book: IMenuBook,
  mic: Mic,
  military_tech: MilitaryTechIcon,
  mobile: Mobile,
  monitor_heart: MonitorHeartIcon,
  more_vert: MoreVert,
  music_note: IMusicNote,
  notifications: Notifications,
  notifications_active: NotificationsActiveIcon,
  notifications_off: NotificationsOffIcon,
  open_in_new: OpenInNew,
  palette: Palette,
  pause: PauseIcon,
  payments: PaymentsIcon,
  pending_actions: IPendingActions,
  person: Person,
  person_add: PersonAddIcon,
  person_check: PersonCheckIcon,
  person_off: PersonOffIcon,
  person_remove: PersonRemoveIcon,
  pets: IPets,
  picture_as_pdf: IPictureAsPdf,
  picture_in_picture: IPictureInPicture,
  play_arrow: PlayArrow,
  play_circle: PlayCircleIcon,
  podcasts: PodcastsIcon,
  priority_high: PriorityHigh,
  privacy_tip: IPrivacyTip,
  progress_activity: ProgressActivity,
  public: PublicIcon,
  radio_button_unchecked: RadioButtonUncheckedIcon,
  rate_review: RateReviewIcon,
  record_voice_over: IRecordVoiceOver,
  refresh: IRefresh,
  release_alert: IReleaseAlert,
  restart_alt: RestartAlt,
  schedule: Schedule,
  schema: SchemaIcon,
  search: Search,
  send: SendIcon,
  settings: SettingsIcon,
  shuffle: ShuffleIcon,
  skip_next: SkipNextIcon,
  smart_toy: ISmartToy,
  sports_esports: SportsEsports,
  stadium: Stadium,
  star: StarIcon,
  support_agent: ISupportAgent,
  swap_horiz: SwapHoriz,
  swap_vert: SwapVertIcon,
  sync: SyncIcon,
  sync_alt: SyncAltIcon,
  task_alt: TaskAlt,
  text_fields: TextFieldsIcon,
  timer: TimerIcon,
  toggle_on: ToggleOnIcon,
  trophy: TrophyIcon,
  tune: TuneIcon,
  tv: Tv,
  unarchive: UnarchiveIcon,
  undo: UndoIcon,
  upload: UploadIcon,
  verified: IVerified,
  videocam: Videocam,
  videogame_asset: IVideogameAsset,
  view_column: ViewColumnIcon,
  view_timeline: ViewTimelineIcon,
  visibility: VisibilityIcon,
  visibility_off: VisibilityOffIcon,
  volunteer_activism: VolunteerActivismIcon,
  warning: Warning,
  webhook: Webhook,
} satisfies Record<string, Svg>

export const FILLED: Partial<Record<IconName, Svg>> = {
  home: HomeFill,
  calendar_month: CalendarMonthFill,
  stadium: StadiumFill,
  badge: BadgeFill,
  person: PersonFill,
}

export type IconName = keyof typeof ICONS
