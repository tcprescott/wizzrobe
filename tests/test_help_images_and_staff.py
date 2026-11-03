"""Screenshots in help articles, and the "For staff" group.

Two halves. The image block is the first block that points at a file, so it gets
the same treatment as links: only the committed screenshot tree is a valid
source, and anything else reads as the characters the author typed. The
screenshot manifest and the tree must agree, or ``scripts/help_screenshots.js``
silently stops regenerating a shot an article shows.

The staff half proves reader gating: an ``audience: staff`` article is offered
to anyone who can open Admin here (tournament admins included) and to nobody
else, a ``roles:`` article only to holders, and both land in one trailing group.
"""

import json
import re
from pathlib import Path

import pytest

from application.content import IMAGE_SRC_RE, ContentCatalog, parse_blocks
from application.event_info import articles_for_tenant, tenant_slugs
from application.help import all_articles
from application.services import HelpService
from application.tenant_context import tenant_scope
from models import Role, Tournament, UserRole
from tests.factories import make_user

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = ROOT / 'scripts' / 'help_screenshots'
SHOT_ROOT = ROOT / 'static' / 'help'


class TestImageBlock:
    def test_a_screenshot_becomes_an_image_block(self):
        [block] = parse_blocks('![The board on a phone](/static/help/player/board.webp)')
        assert block.kind == 'image'
        assert block.src == '/static/help/player/board.webp'
        assert block.alt == 'The board on a phone'
        assert block.spans == ()

    def test_a_caption_is_inline_text(self):
        [block] = parse_blocks('![Alt](/static/help/player/board.webp "Your **own** row")')
        assert [(s.kind, s.text) for s in block.spans] == [
            ('text', 'Your '), ('strong', 'own'), ('text', ' row'),
        ]

    @pytest.mark.parametrize('src', [
        'https://example.com/x.webp',
        '/static/icons/icon-192.png',
        '/static/help/../icons/icon-192.png',
        '/static/help/player/board.svg',
        '/static/help/player/sub/board.webp',
        'data:image/png;base64,AAAA',
        'javascript:alert(1)',
    ])
    def test_a_source_outside_the_screenshot_tree_degrades_to_text(self, src):
        line = f'![Alt]({src})'
        [block] = parse_blocks(line)
        assert block.kind == 'para'
        assert 'Alt' in ''.join(span.text for span in block.spans)
        assert not any(span.kind == 'link' and not span.href.startswith('/') for span in block.spans)

    def test_alt_text_is_required(self):
        [block] = parse_blocks('![](/static/help/player/board.webp)')
        assert block.kind == 'para'

    def test_an_image_ends_the_paragraph_above_it(self):
        blocks = parse_blocks('Some text\n![Alt](/static/help/player/board.webp)\nMore text')
        assert [b.kind for b in blocks] == ['para', 'image', 'para']

    def test_an_image_only_counts_on_a_line_of_its_own(self):
        [block] = parse_blocks('See ![Alt](/static/help/player/board.webp) here')
        assert block.kind == 'para'


def _manifest() -> list[dict]:
    """Every entry across the per-article files, with ``slug`` taken from the file name."""
    shots = []
    for path in sorted(MANIFEST_DIR.glob('*.json')):
        shots.extend({**entry, 'slug': path.stem} for entry in json.loads(path.read_text()))
    return shots


def _shipped_articles():
    yield from all_articles()
    for slug in tenant_slugs():
        yield from articles_for_tenant(slug)


def _referenced_images() -> dict[str, str]:
    """``src`` → the article that shows it, across help and every handbook."""
    found: dict[str, str] = {}
    for article in _shipped_articles():
        for block in article.blocks:
            if block.kind == 'image':
                found[block.src] = article.slug
    return found


class TestScreenshotsAndManifestAgree:
    def test_the_manifest_is_one_list_per_article(self):
        """A file per article is what keeps parallel help PRs from colliding in one list."""
        for path in MANIFEST_DIR.iterdir():
            assert path.suffix == '.json', f'{path.name}: only <slug>.json files belong here'
            entries = json.loads(path.read_text())
            assert isinstance(entries, list) and entries, f'{path.name} must be a non-empty JSON array'
            for entry in entries:
                assert 'slug' not in entry, f'{path.name}: the file name is the slug; drop "slug" from {entry}'

    def test_every_manifest_entry_is_well_formed_and_unique(self):
        seen = set()
        for shot in _manifest():
            for key in ('slug', 'name', 'device' if 'device' in shot else 'path'):
                assert shot.get(key), f'manifest entry {shot} has no {key}'
            if 'device' in shot:
                assert 'path' not in shot, f'{shot}: a device shot is retaken by hand, so it has no path'
            assert IMAGE_SRC_RE.match(f"/static/help/{shot['slug']}/{shot['name']}.webp"), shot
            key = (shot['slug'], shot['name'])
            assert key not in seen, f'{key} is in the manifest twice'
            seen.add(key)

    def test_every_image_an_article_shows_exists(self):
        for src, slug in _referenced_images().items():
            assert (ROOT / src.lstrip('/')).is_file(), f'{slug} shows {src}, which is not committed'

    def test_a_help_article_shows_only_its_own_screenshots(self):
        """One directory per article keeps a renamed article's shots findable."""
        for article in all_articles():
            for block in article.blocks:
                if block.kind == 'image':
                    assert block.src.startswith(f'/static/help/{article.slug}/'), (article.slug, block.src)

    def test_every_manifest_entry_is_shown_somewhere(self):
        shown = set(_referenced_images())
        for shot in _manifest():
            src = f"/static/help/{shot['slug']}/{shot['name']}.webp"
            assert src in shown, f'{src} is captured but no article shows it'

    def test_every_committed_screenshot_can_be_regenerated(self):
        """A file nothing captures goes stale the first time the UI changes."""
        captured = {f"{shot['slug']}/{shot['name']}.webp" for shot in _manifest()}
        for path in SHOT_ROOT.rglob('*'):
            if path.is_file():
                rel = path.relative_to(SHOT_ROOT).as_posix()
                assert rel in captured, f'static/help/{rel} is not in scripts/help_screenshots/'

    def test_the_pipeline_has_proved_itself_end_to_end(self):
        assert _manifest(), 'the manifest is empty'


_STAFF = '''---
title: Running the board
slug: staff-board
order: 5
audience: staff
summary: For staff.
---

## Board

:::snippet staff-board-snippet
Staff words.
:::
'''

_ROLES = '''---
title: Coordinating volunteers
slug: coord
order: 6
roles: VOLUNTEER_COORDINATOR
---

## Shifts

Coordinator words.
'''

_TYPO = '''---
title: Misspelt audience
slug: typo
audience: staf
---

## Heading

Words.
'''

_PUBLIC = '''---
title: Everyone
slug: everyone
order: 50
---

## Heading

See [the board](/help/staff-board).
'''


@pytest.fixture
def staff_catalog(tmp_path, monkeypatch):
    for name, text in [('a.md', _STAFF), ('b.md', _ROLES), ('c.md', _TYPO), ('d.md', _PUBLIC)]:
        (tmp_path / name).write_text(text)
    catalog = ContentCatalog(tmp_path)
    import application.services.help_service as module

    monkeypatch.setattr(module, 'all_articles', catalog.articles)
    monkeypatch.setattr(module, 'get_article', catalog.get)
    monkeypatch.setattr(module, 'get_snippet', catalog.snippet)
    return catalog


async def _slugs(user=None) -> list[str]:
    return [a.slug for a in await HelpService.list_articles(user)]


async def _with_role(role: Role, discord_id: int):
    user = await make_user(discord_id=discord_id)
    await UserRole.create(user=user, role=role)
    return user


class TestForStaff:
    async def test_signed_out_readers_see_only_everyones_articles(self, db, staff_catalog):
        assert await _slugs() == ['everyone']

    async def test_a_player_sees_only_everyones_articles(self, db, staff_catalog):
        assert await _slugs(await make_user(discord_id=11)) == ['everyone']

    async def test_staff_see_the_staff_group_last(self, db, staff_catalog):
        slugs = await _slugs(await _with_role(Role.STAFF, 12))
        assert slugs[0] == 'everyone'
        assert set(slugs[1:]) == {'staff-board', 'typo'}

    async def test_a_tournament_admin_reads_staff_articles(self, db, staff_catalog):
        """They run their tournaments on Admin → Schedule but hold no role."""
        user = await make_user(discord_id=13)
        tournament = await Tournament.create(name='A Cup')
        await tournament.admins.add(user)
        assert 'staff-board' in await _slugs(user)

    async def test_a_crew_coordinator_reads_staff_articles(self, db, staff_catalog):
        user = await make_user(discord_id=14)
        tournament = await Tournament.create(name='A Cup')
        await tournament.crew_coordinators.add(user)
        assert 'staff-board' in await _slugs(user)

    async def test_roles_admit_only_their_holders(self, db, staff_catalog):
        assert 'coord' not in await _slugs(await _with_role(Role.STAFF, 15))
        assert 'coord' in await _slugs(await _with_role(Role.VOLUNTEER_COORDINATOR, 16))

    async def test_single_tab_admin_roles_do_not_read_staff_articles(self, db, staff_catalog):
        """Held to their own Admin tabs: a volunteer coordinator keeps the
        article gated by their role, an equipment manager gets neither."""
        vc = await _slugs(await _with_role(Role.VOLUNTEER_COORDINATOR, 18))
        assert 'staff-board' not in vc and 'coord' in vc
        assert await _slugs(await _with_role(Role.EQUIPMENT_MANAGER, 19)) == ['everyone']

    async def test_a_super_admin_reads_everything(self, db, staff_catalog):
        user = await make_user(discord_id=17)
        await UserRole.create(user=user, role=Role.SUPER_ADMIN, tenant=None)
        assert set(await _slugs(user)) == {'everyone', 'staff-board', 'coord', 'typo'}

    async def test_a_misspelt_audience_fails_closed(self, db, staff_catalog):
        assert 'typo' not in await _slugs()

    async def test_by_url_a_staff_article_does_not_exist_for_a_player(self, db, staff_catalog):
        player = await make_user(discord_id=18)
        assert await HelpService.get_article('staff-board', player) is None
        assert await HelpService.get_article('staff-board', await _with_role(Role.STAFF, 19)) is not None

    async def test_a_staff_snippet_is_withheld_from_a_player(self, db, staff_catalog):
        assert await HelpService.get_snippet('staff-board-snippet') is None
        staff = await _with_role(Role.STAFF, 20)
        assert await HelpService.get_snippet('staff-board-snippet', staff) is not None

    async def test_search_respects_the_reader(self, db, staff_catalog):
        assert await HelpService.search('staff') == []
        staff = await _with_role(Role.STAFF, 21)
        assert 'staff-board' in [a.slug for a in await HelpService.search('staff', staff)]

    async def test_a_link_to_a_staff_article_is_pruned_for_a_player(self, db, staff_catalog):
        article = await HelpService.get_article('everyone')
        assert not [s for b in article.blocks for s in b.spans if s.kind == 'link']
        staff = await _with_role(Role.STAFF, 22)
        article = await HelpService.get_article('everyone', staff)
        assert [s.href for b in article.blocks for s in b.spans if s.kind == 'link'] == ['/help/staff-board']

    async def test_roles_are_judged_in_this_community_only(self, db, staff_catalog):
        from models import Tenant

        staff = await _with_role(Role.STAFF, 23)
        other = await Tenant.create(name='Other', slug='other')
        with tenant_scope(other.id):
            assert 'staff-board' not in await _slugs(staff)


def test_the_render_module_knows_the_image_kind():
    """A block kind the renderer has no branch for would silently vanish."""
    source = (ROOT / 'theme' / 'help' / 'render.py').read_text()
    assert re.search(r"block\.kind == 'image'", source)


def test_shipped_role_articles_do_not_also_demand_staff_audience():
    """``audience: staff`` and ``roles:`` must both pass, and ``audience: staff``
    is staff, TA and CC only, so pairing it with a non-staff role would lock that
    role out of its own article (a volunteer coordinator out of shift help)."""
    offenders = [
        a.slug for a in all_articles()
        if a.audience != 'everyone' and a.roles and set(a.roles) - {Role.STAFF}
    ]
    assert offenders == []
