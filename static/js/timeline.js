/*
 * The hour-scale grids (theme/volunteer_grid.py: Full Schedule, Shift Board,
 * My Schedule's Timeline). A dashed line follows the mouse across every
 * column with its time tagged in the hour column; on a touch screen, pressing
 * and dragging down the hour column does the same. The now line moves on its
 * own, so a page left open all day doesn't show a stale "now".
 *
 * Delegated from the document, because the grids are built and rebuilt by
 * NiceGUI after this script runs.
 */
(function () {
  if (window.__wizTimeline) return;
  window.__wizTimeline = true;

  var HOUR_PX = 48;
  var SNAP_MIN = 5;
  var formats = {};

  // The grid's data-h12 carries the viewer's clock, so these tags match the
  // server-rendered times beside them ("21:30", or "9:30 PM").
  function format(ms, tz, h12) {
    var key = tz + (h12 ? '|12' : '|24');
    if (!formats[key]) {
      var opts = h12
        ? { hour: 'numeric', minute: '2-digit', hour12: true }
        : { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
      var locale = h12 ? 'en-US' : 'en-GB';
      try {
        formats[key] = new Intl.DateTimeFormat(locale, Object.assign({ timeZone: tz || undefined }, opts));
      } catch (e) {
        formats[key] = new Intl.DateTimeFormat(locale, opts);
      }
    }
    return formats[key].format(new Date(ms));
  }

  function scale(grid) {
    return grid.querySelector('.wiz-vgrid__times .wiz-vgrid__body');
  }

  function place(grid, kind, offset, label) {
    var tag = grid.querySelector('.wiz-vgrid__tag--' + kind);
    var lines = grid.querySelectorAll(kind === 'now' ? '.wiz-vgrid__now' : '.wiz-vgrid__cursor');
    var show = offset !== null;
    var shift = show ? 'translateY(' + offset.toFixed(1) + 'px)' : '';
    if (tag) {
      tag.hidden = !show;
      if (show) { tag.style.transform = shift; tag.textContent = label; }
    }
    lines.forEach(function (line) {
      line.hidden = !show;
      if (show) line.style.transform = shift;
    });
  }

  function showCursor(grid, clientY) {
    var body = scale(grid);
    var t0 = Number(grid.dataset.t0);
    if (!body || !t0) return;
    var rect = body.getBoundingClientRect();
    var y = clientY - rect.top;
    if (y < 0 || y >= rect.height) { place(grid, 'cursor', null); return; }
    var minutes = Math.round((y / HOUR_PX) * 60 / SNAP_MIN) * SNAP_MIN;
    place(grid, 'cursor', (minutes / 60) * HOUR_PX, format(t0 + minutes * 60000, grid.dataset.tz, grid.dataset.h12 === '1'));
  }

  var hovered = null;
  document.addEventListener('pointermove', function (e) {
    if (e.pointerType === 'touch') return;
    var grid = e.target.closest ? e.target.closest('.wiz-vgrid[data-t0]') : null;
    if (hovered && hovered !== grid) place(hovered, 'cursor', null);
    hovered = grid;
    if (grid) showCursor(grid, e.clientY);
  }, { passive: true });

  document.documentElement.addEventListener('pointerleave', function () {
    if (hovered) place(hovered, 'cursor', null);
    hovered = null;
  });

  var scrub = null;
  var fade = null;
  document.addEventListener('pointerdown', function (e) {
    if (e.pointerType !== 'touch' || !e.target.closest) return;
    var times = e.target.closest('.wiz-vgrid__times');
    var grid = times && times.closest('.wiz-vgrid[data-t0]');
    if (!grid) return;
    clearTimeout(fade);
    scrub = { grid: grid, id: e.pointerId };
    showCursor(grid, e.clientY);
  }, { passive: true });

  document.addEventListener('pointermove', function (e) {
    if (scrub && e.pointerId === scrub.id) showCursor(scrub.grid, e.clientY);
  }, { passive: true });

  function endScrub(e) {
    if (!scrub || e.pointerId !== scrub.id) return;
    var grid = scrub.grid;
    scrub = null;
    fade = setTimeout(function () { place(grid, 'cursor', null); }, 1500);
  }
  document.addEventListener('pointerup', endScrub);
  document.addEventListener('pointercancel', endScrub);

  function tickNow() {
    var now = Date.now();
    document.querySelectorAll('.wiz-vgrid[data-t0]').forEach(function (grid) {
      var body = scale(grid);
      if (!body) return;
      var offset = ((now - Number(grid.dataset.t0)) / 3600000) * HOUR_PX;
      var inside = offset >= 0 && offset < body.offsetHeight;
      place(grid, 'now', inside ? offset : null, format(now, grid.dataset.tz, grid.dataset.h12 === '1'));
    });
  }
  setInterval(tickNow, 30000);
  tickNow();
})();
