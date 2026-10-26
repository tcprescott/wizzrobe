"""The OBS browser-source overlay: ``/t/<slug>/overlay/<token>``.

Plain FastAPI routes, not a NiceGUI page: OBS keeps a source open for days, and
a page with no websocket, no session and no framework bundle is one that can't
drop its connection mid-show. The page polls ``feed`` every couple of seconds
and repaints the text; it reloads the layout only when its version changes.

The body is transparent. Art is drawn on a canvas and every ``hole`` box is
cut out of it, so OBS's video sources sit underneath with a pixel-exact edge.
All text goes in through ``textContent``; colours and fonts were checked by
:mod:`application.services.stream_layout_rules` on the way in.
"""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from nicegui import app

from application.errors import NotFoundError
from application.services.stream_layout_rules import FONTS
from application.tenant_context import get_current_tenant_id

_NO_STORE = {
    'Cache-Control': 'no-store',
    'X-Robots-Tag': 'noindex',
    'Referrer-Policy': 'no-referrer',
}
_POLL_MS = 2000

OVERLAY_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Wizzrobe overlay</title>
<style>
html,body{margin:0;padding:0;background:transparent;overflow:hidden}
#stage{position:absolute;left:0;top:0;transform-origin:0 0}
#art{position:absolute;left:0;top:0}
.box{position:absolute;display:flex;align-items:center;overflow:hidden;box-sizing:border-box;line-height:1.15}
.box span{display:block;width:100%}
.box.one span{white-space:nowrap}
</style></head><body><div id="stage"><canvas id="art"></canvas></div>
<script>
const FONTS = __FONTS__;
const POLL = __POLL__;
const stage = document.getElementById('stage');
const canvas = document.getElementById('art');
let version = null, layout = null;
const feedUrl = location.pathname.replace(/\/$/, '') + '/feed';

function scale() {
  if (!layout) return;
  const s = Math.min(innerWidth / layout.width, innerHeight / layout.height);
  stage.style.transform = 'scale(' + s + ')';
}
addEventListener('resize', scale);

function drawArt(feed) {
  canvas.width = feed.width; canvas.height = feed.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, feed.width, feed.height);
  if (!feed.art_url) return;
  const img = new Image();
  img.onload = () => {
    ctx.globalCompositeOperation = 'source-over';
    ctx.drawImage(img, 0, 0, feed.width, feed.height);
    ctx.globalCompositeOperation = 'destination-out';
    for (const b of feed.boxes) {
      if (b.kind !== 'hole') continue;
      ctx.beginPath();
      if (ctx.roundRect && b.radius) ctx.roundRect(b.x, b.y, b.w, b.h, b.radius);
      else ctx.rect(b.x, b.y, b.w, b.h);
      ctx.fill();
    }
  };
  img.src = feed.art_url;
}

function build(feed) {
  layout = feed;
  stage.style.width = feed.width + 'px';
  stage.style.height = feed.height + 'px';
  stage.querySelectorAll('.box').forEach(e => e.remove());
  drawArt(feed);
  for (const b of feed.boxes) {
    if (b.kind !== 'field') continue;
    const el = document.createElement('div');
    el.className = 'box' + (b.h < b.size * 2.2 ? ' one' : '');
    el.dataset.id = b.id;
    el.dataset.size = b.size;
    Object.assign(el.style, {
      left: b.x + 'px', top: b.y + 'px', width: b.w + 'px', height: b.h + 'px',
      color: b.color, fontFamily: FONTS[b.font] || FONTS.Inter, fontWeight: b.weight,
      fontSize: b.size + 'px', textAlign: b.align,
      textTransform: b.upper ? 'uppercase' : 'none',
      textShadow: b.shadow ? '0 2px 4px rgba(0,0,0,.85)' : 'none',
    });
    el.appendChild(document.createElement('span'));
    stage.appendChild(el);
  }
  scale();
}

function fit(el) {
  let size = Number(el.dataset.size);
  el.style.fontSize = size + 'px';
  const span = el.firstChild;
  while (size > 8 && (span.scrollWidth > el.clientWidth + 1 || span.offsetHeight > el.clientHeight + 1)) {
    size -= Math.max(1, Math.round(size / 20));
    el.style.fontSize = size + 'px';
  }
}

function paint(values) {
  for (const el of stage.querySelectorAll('.box')) {
    const text = values[el.dataset.id] || '';
    const span = el.firstChild;
    if (span.textContent !== text) { span.textContent = text; fit(el); }
  }
}

async function tick() {
  try {
    const r = await fetch(feedUrl, {cache: 'no-store'});
    if (r.ok) {
      const feed = await r.json();
      if (feed.version !== version) { version = feed.version; build(feed); }
      paint(feed.values);
    }
  } catch (e) {}
  setTimeout(tick, POLL);
}
tick();
</script></body></html>
"""


def _page() -> str:
    return OVERLAY_HTML.replace('__FONTS__', json.dumps(FONTS)).replace('__POLL__', str(_POLL_MS))


def create() -> None:
    """Register the overlay page, its data feed and its art.

    On NiceGUI's ``app``, not the outer FastAPI app: the tenant middleware sits
    on NiceGUI's app, so only routes there see ``/t/<slug>`` stripped and the
    community bound.
    """

    async def _resolve(token: str):
        from application.services import StreamLayoutService

        overlay = await StreamLayoutService.resolve_token(token)
        if overlay is None or overlay.tenant_id != get_current_tenant_id():
            return None
        return overlay

    @app.api_route('/overlay/{token}', methods=['GET', 'HEAD'], include_in_schema=False)
    async def _overlay_page(token: str) -> Response:
        if await _resolve(token) is None:
            return Response(status_code=404, headers=_NO_STORE)
        return HTMLResponse(_page(), headers=_NO_STORE)

    @app.api_route('/overlay/{token}/feed', methods=['GET', 'HEAD'], include_in_schema=False)
    async def _overlay_feed(token: str, request: Request) -> Response:
        from application.services import StreamLayoutService
        from application.services.stream_layout_service import art_url

        overlay = await _resolve(token)
        if overlay is None:
            return Response(status_code=404, headers=_NO_STORE)
        try:
            feed = await StreamLayoutService().overlay_feed(overlay)
        except NotFoundError:
            return Response(status_code=404, headers=_NO_STORE)
        root = request.scope.get('root_path', '')
        return JSONResponse({
            'version': feed.version,
            'width': feed.width,
            'height': feed.height,
            'art_url': art_url(root, feed.layout_id, feed.art_sha256) if feed.layout_id and feed.art_sha256 else None,
            'boxes': feed.boxes,
            'values': feed.values,
        }, headers=_NO_STORE)

    @app.api_route('/overlay-art/{layout_id}/{sha256}.png', methods=['GET', 'HEAD'], include_in_schema=False)
    async def _overlay_art(layout_id: int, sha256: str, request: Request) -> Response:
        from application.services import StreamLayoutService

        tenant_id = get_current_tenant_id()
        content = (
            await StreamLayoutService.art_bytes(tenant_id, layout_id, sha256)
            if tenant_id is not None else None
        )
        if content is None:
            return Response(status_code=404, headers={'Cache-Control': 'no-store'})
        etag = f'"{sha256}"'
        headers = {
            'Cache-Control': 'public, max-age=31536000, immutable',
            'ETag': etag,
            'X-Content-Type-Options': 'nosniff',
        }
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(content, media_type='image/png', headers=headers)
