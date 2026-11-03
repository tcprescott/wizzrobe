"""Patreon: the community's campaign card, your own link, and patron status."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from application.services import CampaignOverview, MyPatreon, PatronStatus


class PatronStatusInfo(BaseModel):
    """Someone's standing in one community's Patreon campaign. Never a pledge amount."""

    is_patron: bool = Field(..., description="An active patron of this community's campaign.")
    tier_ids: List[str] = Field(default_factory=list, description="Patreon ids of the tiers they're entitled to.")
    badge_label: Optional[str] = Field(None, description="The badge they show here, when they're a patron.")
    badge_hidden: bool = Field(False, description="They chose not to show the badge to other people.")
    last_synced: Optional[datetime] = Field(None, description="When Patreon last told us about them (UTC).")

    @classmethod
    def build(cls, s: PatronStatus) -> 'PatronStatusInfo':
        return cls(
            is_patron=s.is_patron, tier_ids=list(s.tier_ids), badge_label=s.badge_label,
            badge_hidden=s.badge_hidden, last_synced=s.last_synced,
        )


class PatreonTierInfo(BaseModel):
    tier_id: str
    title: str = Field(..., description="The tier's name on Patreon.")
    badge_label: Optional[str] = Field(None, description="The badge its patrons get here; null means `Patron`.")


class PatreonCampaignResponse(BaseModel):
    configured: bool = Field(..., description="The server has a Patreon app set up, so Connect can work.")
    connected: bool
    campaign_name: Optional[str] = None
    connected_by: Optional[str] = None
    connected_at: Optional[datetime] = Field(None, description="UTC.")
    last_synced_at: Optional[datetime] = Field(None, description="The last full pass over the campaign (UTC).")
    last_sync_error: Optional[str] = Field(None, description="Why the last sync failed, ready to show.")
    sync_pending: bool = Field(False, description="A full sync is queued and runs within five minutes.")
    linked_members: int = Field(0, description="Members here who linked Patreon and are in the campaign.")
    patrons: int = Field(0, description="Of those, active patrons.")
    tiers: List[PatreonTierInfo] = Field(default_factory=list, description="In staff's order: the first tier someone holds picks their label.")

    @classmethod
    def build(cls, o: CampaignOverview) -> 'PatreonCampaignResponse':
        return cls(
            configured=o.configured, connected=o.connected, campaign_name=o.campaign_name,
            connected_by=o.connected_by, connected_at=o.connected_at, last_synced_at=o.last_synced_at,
            last_sync_error=o.last_sync_error, sync_pending=o.sync_pending,
            linked_members=o.linked_members, patrons=o.patrons,
            tiers=[PatreonTierInfo(tier_id=t.tier_id, title=t.title, badge_label=t.badge_label) for t in o.tiers],
        )


class PatreonTierLabel(BaseModel):
    tier_id: str
    badge_label: Optional[str] = Field(None, max_length=40, description="Empty or null for the default, `Patron`.")


class PatreonTierLabelsUpdate(BaseModel):
    tiers: List[PatreonTierLabel] = Field(..., description="Every tier, in the order to use. Unknown ids are ignored.")


class SignInUrlResponse(BaseModel):
    authorize_url: str = Field(..., description="Open this in the browser; Patreon sends the person back to the app.")


class SyncQueuedResponse(BaseModel):
    queued: bool = Field(..., description="A full sync is queued and runs within five minutes.")


class CommunityBadgeInfo(BaseModel):
    community: str
    is_patron: bool
    badge_label: Optional[str] = None


class MyPatreonResponse(BaseModel):
    configured: bool = Field(..., description="The server has a Patreon app set up, so Link can work.")
    linked: bool
    username: Optional[str] = Field(None, description="Your name on Patreon. Shown only to you.")
    linked_at: Optional[datetime] = Field(None, description="UTC.")
    badge_hidden: bool = Field(..., description="You chose not to show your badge to other people.")
    connected_here: bool = Field(..., description="This community has connected its Patreon campaign.")
    here: PatronStatusInfo = Field(..., description="Your standing in this community.")
    communities: List[CommunityBadgeInfo] = Field(default_factory=list, description="Every community whose campaign you're in.")

    @classmethod
    def build(cls, m: MyPatreon) -> 'MyPatreonResponse':
        return cls(
            configured=m.configured, linked=m.linked, username=m.username, linked_at=m.linked_at,
            badge_hidden=m.badge_hidden, connected_here=m.connected_here, here=PatronStatusInfo.build(m.here),
            communities=[CommunityBadgeInfo(**vars(c)) for c in m.communities],
        )


class MyPatreonUpdate(BaseModel):
    badge_hidden: bool = Field(..., description="True hides your badge from other people. Perks still work.")
