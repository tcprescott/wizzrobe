"""The Discord-avatar URL builder and the entry→avatar map the brackets use.

Two rules matter here: a missing id or hash yields nothing at all (so callers
fall back to their own placeholder rather than linking a 404), and an animated
hash is served as GIF.
"""

from types import SimpleNamespace

from application.utils.discord_avatar import avatar_url, user_avatar_url
from theme.brackets import entry_avatars


class TestAvatarUrl:
    def test_builds_a_cdn_png_url(self):
        url = avatar_url(123, 'abc', 64)
        assert url == 'https://cdn.discordapp.com/avatars/123/abc.png?size=64'

    def test_animated_hashes_are_served_as_gif(self):
        assert avatar_url(123, 'a_abc').endswith('.gif?size=64')

    def test_no_hash_means_no_url(self):
        assert avatar_url(123, None) is None
        assert avatar_url(123, '') is None

    def test_no_discord_id_means_no_url(self):
        assert avatar_url(None, 'abc') is None

    def test_user_helper_tolerates_a_missing_user(self):
        assert user_avatar_url(None) is None

    def test_user_helper_reads_the_row(self):
        user = SimpleNamespace(discord_id=5, discord_avatar='hash')
        assert user_avatar_url(user, 128) == (
            'https://cdn.discordapp.com/avatars/5/hash.png?size=128'
        )


def _entrant(entrant_id, user=None):
    return SimpleNamespace(id=entrant_id, user=user)


def _entry(entry_id, entrant_id):
    return SimpleNamespace(id=entry_id, entrant_id=entrant_id)


class TestEntryAvatars:
    def test_maps_entries_to_their_entrant_s_avatar(self):
        entrants = [_entrant(1, SimpleNamespace(discord_id=9, discord_avatar='h'))]
        assert entry_avatars(entrants, [_entry(50, 1)]) == {
            50: 'https://cdn.discordapp.com/avatars/9/h.png?size=64'
        }

    def test_placeholder_entrants_are_absent_rather_than_empty(self):
        entrants = [_entrant(1, None)]
        assert entry_avatars(entrants, [_entry(50, 1)]) == {}

    def test_an_unfetched_user_relation_is_skipped_not_crashed(self):
        entrants = [_entrant(1, object())]
        assert entry_avatars(entrants, [_entry(50, 1)]) == {}
