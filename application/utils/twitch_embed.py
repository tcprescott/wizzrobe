"""Turning a stage's stream link into a Twitch player embed.

Twitch refuses to play an embed unless ``parent`` names the hostname of the page
hosting it, so the player URL is built per request from the host the viewer
actually reached (a community's custom domain or the shared one).
"""

import re
from typing import Optional
from urllib.parse import urlencode, urlsplit

from application.utils.safe_url import http_url_or_empty

_TWITCH_HOSTS = {'twitch.tv', 'www.twitch.tv', 'm.twitch.tv', 'go.twitch.tv'}
_CHANNEL = re.compile(r'^[A-Za-z0-9_]{1,25}$')
# Paths under twitch.tv that are pages, not channels.
_RESERVED = {
    'directory', 'videos', 'settings', 'subscriptions', 'inventory', 'wallet',
    'downloads', 'jobs', 'p', 'search', 'login', 'signup', 'turbo', 'prime',
}
_PLAYER = 'https://player.twitch.tv/'


def twitch_channel(stream_url: Optional[str]) -> Optional[str]:
    """The channel a ``twitch.tv/<channel>`` link points at, else ``None``."""
    name = twitch_channel_name(stream_url)
    return name.lower() if name else None


def twitch_channel_name(stream_url: Optional[str]) -> Optional[str]:
    """The channel as the link spells it (``SpeedGaming``), for showing to people."""
    url = http_url_or_empty(stream_url)
    if not url:
        return None
    parts = urlsplit(url)
    if (parts.hostname or '').lower() not in _TWITCH_HOSTS:
        return None
    segments = [s for s in parts.path.split('/') if s]
    if len(segments) != 1:
        return None
    channel = segments[0]
    if channel.lower() in _RESERVED or not _CHANNEL.match(channel):
        return None
    return channel


def parent_hostname(host: Optional[str]) -> Optional[str]:
    """``host`` without its port: Twitch matches ``parent`` on the bare name."""
    if not host:
        return None
    name = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    return name or None


def player_url(channel: str, parent: str) -> str:
    """The ``player.twitch.tv`` iframe source for ``channel`` hosted on ``parent``.

    Starts muted: browsers block autoplay with sound, and a muted autoplay
    is the only kind every browser allows.
    """
    query = urlencode({'channel': channel, 'parent': parent, 'autoplay': 'true', 'muted': 'true'})
    return f'{_PLAYER}?{query}'


def preview_url(channel: str) -> str:
    """Twitch's still of ``channel``'s stream, refreshed by Twitch every few minutes.

    A plain image on Twitch's CDN, so it costs no player, no cookies and works on
    the static Watch page. An offline channel gets Twitch's grey placeholder.
    """
    return f'https://static-cdn.jtvnw.net/previews-ttv/live_user_{channel}-640x360.jpg'
