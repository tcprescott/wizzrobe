"""Schemas for a community's own settings (``/api/settings``), one group per Settings pane."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ThemeColours(BaseModel):
    primary: str = Field(..., description="Hex like `#9c6b12`. Buttons, links and the selected menu item in light mode.")
    secondary: str
    accent: str = Field(..., description="Highlights and links in dark mode.")
    header: str = Field(..., description="The brand mark beside the community's name when there's no logo.")


class ThemePreset(BaseModel):
    name: str
    colours: ThemeColours


class AppearanceResponse(BaseModel):
    colours: ThemeColours
    defaults: ThemeColours
    presets: List[ThemePreset] = Field(..., description="Palettes already checked for AA contrast.")
    issues: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Per colour, a sentence for each place it falls below AA contrast. Warnings only: saving still works.",
    )
    logo_url: Optional[str] = Field(None, description="Path of the community's logo on this server, when one is uploaded.")


class ThemeColoursUpdate(BaseModel):
    primary: str = Field('', description="Blank puts the default back.")
    secondary: str = ''
    accent: str = ''
    header: str = ''


class ContrastRequest(BaseModel):
    colours: Dict[str, str]


class ContrastResponse(BaseModel):
    issues: Dict[str, List[str]]


class TimezoneSettingsResponse(BaseModel):
    mode: str = Field(..., description="`pinned` shows one zone to everybody; `user` follows each viewer, with `name` as the fallback.")
    name: str = Field(..., description="IANA zone, like `America/New_York`.")
    options: List[str] = Field(..., description="The usual zones to offer, with the stored one first when it isn't among them.")


class TimezoneSettingsUpdate(BaseModel):
    mode: str
    name: str


class JoinModeOption(BaseModel):
    value: str
    label: str
    help: str = Field(..., description="What picking it means, adjusted for whether a Discord server is linked.")


class JoiningSettingsResponse(BaseModel):
    join_mode: str
    modes: List[JoinModeOption] = Field(..., description="The modes staff may pick. Discord ones are left out until a server is linked, unless already chosen.")
    guild_linked: bool
    discord_invite_url: str = Field('', description="Shown on the join page. Blank when there isn't one.")


class JoiningSettingsUpdate(BaseModel):
    join_mode: str
    discord_invite_url: str = ''


class ProfileReminder(BaseModel):
    check: str
    label: str
    prompt: str = Field(..., description="What the banner says to a member who hasn't done it.")
    enabled: bool
    dismissible: bool = Field(..., description="Whether a member can hide the banner without doing it.")
    note: str = ''
    unavailable: Optional[str] = Field(None, description="Why members can't complete it here right now, so they never see it.")


class ProfileRemindersResponse(BaseModel):
    reminders: List[ProfileReminder]
    max_note_length: int


class ProfileReminderRule(BaseModel):
    check: str
    dismissible: bool = True
    note: str = ''


class ProfileRemindersUpdate(BaseModel):
    rules: List[ProfileReminderRule] = Field(..., description="The reminders to switch on. Any check left out is switched off.")


class WatchAudience(BaseModel):
    live_viewers: int
    static_readers: int
    arrivals_last_minute: int
    admissions_per_minute: int = Field(..., description="Roughly how many new live viewers the app lets in a minute.")
    peak: int
    peak_at: Optional[datetime] = None
    tripped: bool = Field(..., description="The page switched itself to static during a rush.")
    tripped_at: Optional[datetime] = None


class WatchSettings(BaseModel):
    mode: str = Field(..., description="`live` or `static`.")
    static_signed_out: bool = Field(..., description="Signed-out visitors always get the static page.")
    auto_static: bool = Field(..., description="Switch to static on its own during a rush.")
    threshold: int = Field(..., description="Arrivals a minute that count as a rush.")


class WatchSettingsResponse(WatchSettings):
    audience: WatchAudience


class VolunteerSettings(BaseModel):
    reminder_lead_minutes: Optional[int] = Field(None, description="How long before a shift its volunteers get a DM. Null uses 60.")


class MeetupSettings(BaseModel):
    require_approval: bool = Field(..., description="A meetup waits for staff before it's listed.")
    reminder_lead_minutes: Optional[int] = Field(None, description="How long before a meetup its subscribers get a DM. Null uses 30.")


class FeatureRow(BaseModel):
    flag: str
    label: str
    description: str
    category: str
    available: bool = Field(..., description="A platform admin made it available to this community's tier.")
    enabled: bool = Field(..., description="Staff have it switched on. It's live only when available too.")


class FeaturesResponse(BaseModel):
    tier: Optional[str] = None
    features: List[FeatureRow]


class FeatureUpdate(BaseModel):
    enabled: bool


class ConnectedApp(BaseModel):
    app_id: int
    name: str
    owner_name: str
    homepage_host: Optional[str] = None
    members: int = Field(..., description="How many members connected it. Who they are is never shown.")
    blocked: bool = Field(..., description="Blocked in this community.")
    disabled: bool = Field(..., description="Disabled for everyone by a platform admin.")


class BlockAppResponse(BaseModel):
    disconnected: int = Field(..., description="Members whose connection ended.")
