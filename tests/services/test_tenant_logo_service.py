"""Tests for TenantLogoService — upload validation, storage and serving.

``normalise_logo`` is pure and covers the hostile-bitmap cases (polyglot,
image bomb, oversize); hostile SVGs are covered in tests/test_svg_sanitize.py. The persistence tests run against real ``Tenant`` rows
via ``two_tenants``; the route tests mount ``frontend._register_root_routes`` on
a bare app, as tests/test_public_bracket_access.py does for robots.txt.
"""

import hashlib
import struct
import zlib
from io import BytesIO

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from application.services import tenant_logo_service
from application.services.tenant_logo_service import (
    MAX_UPLOAD_BYTES,
    OUTPUT_SIDE,
    TOUCH_ICON_BACKGROUND,
    TenantLogoService,
    normalise_logo,
    render_icon,
)
from application.services.tenant_theme_service import TenantThemeService
from application.tenant_context import tenant_scope
from models import AuditLog, Role, TenantLogo, User, UserRole


def _png(size=(128, 128), mode='RGBA', color=(200, 30, 30, 255), **save) -> bytes:
    out = BytesIO()
    Image.new(mode, size, color).save(out, 'PNG', **save)
    return out.getvalue()


def _bomb_png(side: int) -> bytes:
    """A tiny PNG whose header claims ``side``×``side`` pixels."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    ihdr = struct.pack('>IIBBBBB', side, side, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(b'\0')) + chunk(b'IEND', b'')


class TestNormalise:
    def test_output_is_square_webp(self):
        out = normalise_logo(_png((300, 120)))
        with Image.open(BytesIO(out)) as img:
            assert img.format == 'WEBP'
            assert img.size == (OUTPUT_SIDE, OUTPUT_SIDE)
            assert img.mode == 'RGBA'

    def test_wide_image_is_fitted_not_cropped(self):
        out = normalise_logo(_png((400, 100), color=(0, 0, 255, 255)))
        with Image.open(BytesIO(out)) as img:
            assert img.getpixel((128, 5))[3] == 0
            assert img.getpixel((5, 128))[3] == 255

    @pytest.mark.parametrize('fmt', ['JPEG', 'WEBP'])
    def test_accepts_jpeg_and_webp(self, fmt):
        out = BytesIO()
        Image.new('RGB', (100, 100), (1, 2, 3)).save(out, fmt)
        assert normalise_logo(out.getvalue())

    def test_bitmap_path_never_takes_svg(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with pytest.raises(ValueError, match="isn't an SVG, PNG"):
            normalise_logo(svg)

    def test_rejects_gif(self):
        out = BytesIO()
        Image.new('RGB', (100, 100)).save(out, 'GIF')
        with pytest.raises(ValueError, match="isn't an SVG, PNG"):
            normalise_logo(out.getvalue())

    def test_rejects_garbage_and_empty(self):
        with pytest.raises(ValueError):
            normalise_logo(b'not an image at all')
        with pytest.raises(ValueError, match='empty'):
            normalise_logo(b'')

    def test_rejects_oversize_bytes(self):
        with pytest.raises(ValueError, match='1 MB'):
            normalise_logo(b'\x89PNG' + b'\0' * MAX_UPLOAD_BYTES)

    def test_rejects_huge_dimensions_before_decoding(self):
        with pytest.raises(ValueError, match='4096'):
            normalise_logo(_bomb_png(50_000))

    def test_rejects_tiny_images(self):
        with pytest.raises(ValueError, match='at least 64'):
            normalise_logo(_png((32, 32)))

    def test_trailing_payload_is_not_kept(self):
        polyglot = _png() + b'<script>alert(1)</script>'
        out = normalise_logo(polyglot)
        assert b'<script>' not in out

    def test_metadata_is_stripped(self):
        out = BytesIO()
        img = Image.new('RGB', (100, 100))
        exif = img.getexif()
        exif[0x010E] = 'secret description'
        img.save(out, 'JPEG', exif=exif)
        assert b'secret description' not in normalise_logo(out.getvalue())


@pytest.fixture
async def tenants_with_staff(two_tenants):
    a, b = two_tenants
    staff = await User.create(discord_id=820, username='staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant=a)
    await UserRole.create(user=staff, role=Role.STAFF, tenant=b)
    tenant_logo_service._cache.clear()
    tenant_logo_service._icon_cache.clear()
    return a, b, staff


async def test_set_logo_stores_and_exposes_url(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        url = await TenantLogoService.set_logo(staff, _png())
    row = await TenantLogo.get(tenant_id=a.id)
    assert row.sha256 == hashlib.sha256(bytes(row.content)).hexdigest()
    assert url == f'/brand-logo/{a.id}/{row.sha256}.webp'
    assert (await TenantThemeService.get_theme(a.id))['logo'] == url
    assert await AuditLog.filter(action='theme.logo_updated').count() == 1


async def test_replacing_keeps_one_row_and_changes_url(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        first = await TenantLogoService.set_logo(staff, _png(color=(1, 1, 1, 255)))
        second = await TenantLogoService.set_logo(staff, _png(color=(2, 200, 2, 255)))
    assert first != second
    assert await TenantLogo.filter(tenant_id=a.id).count() == 1


async def test_remove_logo(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TenantLogoService.set_logo(staff, _png())
        await TenantLogoService.remove_logo(staff)
    assert not await TenantLogo.exists(tenant_id=a.id)
    assert 'logo' not in await TenantThemeService.get_theme(a.id)
    assert await AuditLog.filter(action='theme.logo_removed').count() == 1


async def test_set_logo_is_staff_gated(two_tenants):
    a, _ = two_tenants
    rando = await User.create(discord_id=821, username='rando')
    with tenant_scope(a.id):
        with pytest.raises(PermissionError):
            await TenantLogoService.set_logo(rando, _png())
        with pytest.raises(PermissionError):
            await TenantLogoService.remove_logo(rando)


async def test_get_logo_bytes_rejects_malformed_hash(tenants_with_staff):
    a, _, _ = tenants_with_staff
    assert await TenantLogoService.get_logo_bytes(a.id, '../../etc/passwd') is None


@pytest.fixture
def logo_client(monkeypatch):
    monkeypatch.setenv('MOCK_DISCORD', 'true')
    import frontend

    app = FastAPI()
    frontend._register_root_routes(app)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


async def test_route_serves_with_cache_headers(tenants_with_staff, logo_client):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        url = await TenantLogoService.set_logo(staff, _png())
    async with logo_client as client:
        resp = await client.get(url)
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'image/webp'
        assert 'immutable' in resp.headers['cache-control']
        assert resp.headers['x-content-type-options'] == 'nosniff'
        assert 'sandbox' in resp.headers['content-security-policy']
        etag = resp.headers['etag']

        again = await client.get(url, headers={'If-None-Match': etag})
        assert again.status_code == 304
        assert again.content == b''

        missing = await client.get(f'/brand-logo/{a.id}/{"0" * 64}.webp')
        assert missing.status_code == 404


_SVG = (
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10">'
    b'<style>.a{fill:#ffce29}</style><rect class="a" width="20" height="10"/></svg>'
)


async def test_svg_logo_is_stored_sanitised_and_served_as_svg(tenants_with_staff, logo_client):
    a, _, staff = tenants_with_staff
    hostile = _SVG.replace(b'<rect', b'<script>alert(1)</script><rect onclick="x()"')
    with tenant_scope(a.id):
        url = await TenantLogoService.set_logo(staff, hostile)
    assert url.endswith('.svg')
    assert (await TenantThemeService.get_theme(a.id))['logo'] == url
    row = await TenantLogo.get(tenant_id=a.id)
    stored = bytes(row.content)
    assert stored.startswith(b'<svg') and b'script' not in stored and b'onclick' not in stored
    async with logo_client as client:
        resp = await client.get(url)
        assert resp.status_code == 200
        assert resp.headers['content-type'].startswith('image/svg+xml')
        assert 'sandbox' in resp.headers['content-security-policy']
        assert (await client.get(url.replace('.svg', '.webp'))).status_code == 404


async def test_replacing_svg_with_bitmap_drops_the_format(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TenantLogoService.set_logo(staff, _SVG)
        url = await TenantLogoService.set_logo(staff, _png())
    assert url.endswith('.webp')
    assert (await TenantThemeService.get_theme(a.id))['logo'] == url


async def test_qr_logo_is_the_current_communitys(tenants_with_staff):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        assert await TenantLogoService.qr_logo_data_uri() is None
        await TenantLogoService.set_logo(staff, _SVG)
        assert (await TenantLogoService.qr_logo_data_uri()).startswith('data:image/svg+xml;base64,')
        await TenantLogoService.set_logo(staff, _png())
        assert (await TenantLogoService.qr_logo_data_uri()).startswith('data:image/webp;base64,')
    with tenant_scope(b.id):
        assert await TenantLogoService.qr_logo_data_uri() is None


class TestRenderIcon:
    @pytest.mark.parametrize('stored', [
        _SVG[_SVG.index(b'<svg'):],
        normalise_logo(_png((300, 150), color=(255, 206, 41, 255))),
    ], ids=['svg', 'webp'])
    def test_tab_icon_is_a_transparent_square_png(self, stored):
        with Image.open(BytesIO(render_icon(stored, 32))) as img:
            assert (img.format, img.size) == ('PNG', (32, 32))
            assert img.getpixel((16, 1))[3] == 0
            # The stored WebP is lossy, so the colour comes back near, not exact.
            assert all(abs(got - want) <= 3 for got, want in zip(img.getpixel((16, 16)), (255, 206, 41, 255), strict=True))

    def test_touch_icon_sits_on_the_dark_tile(self):
        with Image.open(BytesIO(render_icon(_SVG[_SVG.index(b'<svg'):], 180))) as img:
            assert img.size == (180, 180)
            assert img.getpixel((2, 2)) == TOUCH_ICON_BACKGROUND
            assert img.getpixel((90, 90))[:3] == (255, 206, 41)


async def test_icon_route_serves_png_for_svg_logo(tenants_with_staff, logo_client):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TenantLogoService.set_logo(staff, _SVG)
    theme = await TenantThemeService.get_theme(a.id)
    async with logo_client as client:
        for key, side in (('logo_icon', 32), ('logo_touch', 180)):
            resp = await client.get(theme[key])
            assert resp.status_code == 200
            assert resp.headers['content-type'] == 'image/png'
            assert 'immutable' in resp.headers['cache-control']
            with Image.open(BytesIO(resp.content)) as img:
                assert img.size == (side, side)
        etag = (await client.get(theme['logo_icon'])).headers['etag']
        assert (await client.get(theme['logo_icon'], headers={'If-None-Match': etag})).status_code == 304
        assert (await client.get(theme['logo_icon'].replace('-32.png', '-64.png'))).status_code == 404
        assert (await client.get(theme['logo_icon'].replace(f'/{a.id}/', f'/{b.id}/'))).status_code == 404
    assert 'logo_icon' not in await TenantThemeService.get_theme(b.id)
